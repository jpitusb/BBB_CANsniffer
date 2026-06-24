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
    device-tree-compiler

# ── Python dependencies ───────────────────────────────────────────────────────
# --break-system-packages was added in pip 22+ (PEP 668); older pip (Buster) ignores it
PIP_MAJOR=$(pip3 --version 2>/dev/null | grep -oP '(?<=pip )\d+' | head -1)
PIP_FLAGS=""
[ "${PIP_MAJOR:-0}" -ge 22 ] && PIP_FLAGS="--break-system-packages"
sudo -u "$REAL_USER" pip3 install $PIP_FLAGS --upgrade pip setuptools
sudo -u "$REAL_USER" pip3 install $PIP_FLAGS "$REPO/backend"

# ── Build + install DTS overlay ──────────────────────────────────────────────
echo "Building DTS overlay..."
make -C "$REPO/dts" BB-DCAN1-00A0.dtbo
cp "$REPO/dts/BB-DCAN1-00A0.dtbo" /lib/firmware/

# ── Install systemd service with correct PYTHONPATH ──────────────────────────
echo "Installing systemd service..."
chmod +x "$REPO/scripts/setup_can.sh"

# Patch the service file with the real user's site-packages path and Python version
sed -e "s|/home/lauren/.local/lib/python3.11/site-packages|${SITE_PACKAGES}|g" \
    -e "s|python3\.11|python${PYVER}|g" \
    "$REPO/systemd/can-sniffer.service" > /etc/systemd/system/can-sniffer.service

systemctl daemon-reload
systemctl enable can-sniffer.service

# ── Data directory + default configs ─────────────────────────────────────────
DATA_DIR="$REPO/data"
mkdir -p "$DATA_DIR"
chown "$REAL_USER:$REAL_USER" "$DATA_DIR"

# Create a sample latency_pairs.json if none exists
PAIRS="$DATA_DIR/latency_pairs.json"
if [ ! -f "$PAIRS" ]; then
    cp "$REPO/data/latency_pairs.example.json" "$PAIRS"
    chown "$REAL_USER:$REAL_USER" "$PAIRS"
    echo "Created default latency_pairs.json — edit at $PAIRS"
fi

# ── Verify uEnv.txt ──────────────────────────────────────────────────────────
UENV=/boot/uEnv.txt
ok=true
check() {
    grep -q "$1" "$UENV" || { echo "  MISSING: $1"; ok=false; }
}
echo "Checking /boot/uEnv.txt..."
check "disable_uboot_overlay_video=1"
check "enable_uboot_cape_universal=0"
if ! $ok; then
    echo "  Run bootstrap.sh first to patch uEnv.txt, then reboot."
    exit 1
fi
echo "  uEnv.txt OK"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo "Install complete."
echo "Start service now: sudo systemctl start can-sniffer"
echo "Or reboot for a clean start."
echo ""
echo "Dashboard: http://$(hostname -I | awk '{print $1}'):8000/"
echo ""
echo "Configure latency address pairs at:"
echo "  $DATA_DIR/latency_pairs.json"
echo "  (or via the Latency tab in the dashboard)"
echo "========================================================"
