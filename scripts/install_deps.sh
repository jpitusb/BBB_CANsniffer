#!/usr/bin/env bash
# install_deps.sh — build and install everything on the BBB.
# Run as root AFTER bootstrap.sh has rebooted into the new uEnv.txt settings.
# Idempotent: safe to re-run after repo updates.
#
# Usage: sudo /opt/can_sniffer/scripts/install_deps.sh
set -euo pipefail

REPO=/opt/can_sniffer

# ── Detect the non-root user who invoked sudo ─────────────────────────────────
REAL_USER="${SUDO_USER:-${USER}}"
if [[ "$REAL_USER" == "root" ]]; then
    REAL_USER=$(ls /home | head -1)
fi
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
PYVER=$(python3 --version 2>&1 | grep -oP '3\.\d+')
SITE_PACKAGES="$REAL_HOME/.local/lib/python${PYVER}/site-packages"
echo "Installing for user: $REAL_USER  (home: $REAL_HOME, python: $PYVER)"

# ── System packages ──────────────────────────────────────────────────────────
apt-get update -q
apt-get install -y \
    can-utils \
    python3-pip \
    device-tree-compiler \
    gcc-pru binutils-pru ti-pru-software-v6.3

# ── Python dependencies ───────────────────────────────────────────────────────
sudo -u "$REAL_USER" pip3 install --break-system-packages --upgrade pip setuptools
sudo -u "$REAL_USER" pip3 install --break-system-packages "$REPO/backend"

# ── Build PRU timestamp firmware ─────────────────────────────────────────────
echo "Building PRU timestamp firmware..."
make -C "$REPO/pru/pru0_timestamp" clean all
cp "$REPO/pru/pru0_timestamp/am335x-pru0-fw" /lib/firmware/

# ── Build PRU fault-inject firmware (for BBB #2; harmless to build on BBB #1)
echo "Building PRU fault-inject firmware..."
make -C "$REPO/pru/pru0_fault_inject" clean all 2>/dev/null || \
    echo "(fault-inject firmware optional — skipping if it fails)"

# ── Build + install DTS overlay ──────────────────────────────────────────────
echo "Building DTS overlay..."
make -C "$REPO/dts" BB-PRU0-CAN-TS-00A0.dtbo
cp "$REPO/dts/BB-PRU0-CAN-TS-00A0.dtbo" /lib/firmware/

# ── Install systemd services with correct PYTHONPATH ─────────────────────────
echo "Installing systemd services..."
chmod +x "$REPO/scripts/setup_pru.sh" "$REPO/scripts/setup_can.sh"

# Patch the service file with the real user's site-packages path
sed "s|/home/lauren/.local/lib/python3.11/site-packages|${SITE_PACKAGES}|g" \
    "$REPO/systemd/can-sniffer.service" > /etc/systemd/system/can-sniffer.service

cp "$REPO/systemd/pru-loader.service" /etc/systemd/system/

systemctl daemon-reload
systemctl enable pru-loader.service can-sniffer.service

# ── Verify uEnv.txt ──────────────────────────────────────────────────────────
UENV=/boot/uEnv.txt
ok=true
check() {
    grep -q "$1" "$UENV" || { echo "  MISSING: $1"; ok=false; }
}
echo "Checking /boot/uEnv.txt..."
check "uboot_overlay_addr0=/lib/firmware/BB-PRU0-CAN-TS-00A0.dtbo"
check "disable_uboot_overlay_video=1"
check "enable_uboot_cape_universal=0"
check "memmap=8K"
if ! $ok; then
    echo "  Run bootstrap.sh first to patch uEnv.txt, then reboot."
    exit 1
fi
echo "  uEnv.txt OK"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo "Install complete."
echo "Start services now: sudo systemctl start pru-loader can-sniffer"
echo "Or reboot for a clean start."
echo "Dashboard: http://$(hostname -I | awk '{print $1}'):8000/"
echo "========================================================"
