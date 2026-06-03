#!/usr/bin/env bash
# install_deps.sh — run once on the BeagleBone Black as root to set everything up.
# Idempotent: safe to re-run after updates.
set -euo pipefail

REPO=/opt/can_sniffer
LAUREN_PIP=/home/lauren/.local/bin/pip3

# ── System packages ──────────────────────────────────────────────────────────
apt-get update -q
apt-get install -y \
    can-utils \
    python3-pip \
    python3.11-venv \
    device-tree-compiler \
    gcc-pru binutils-pru ti-pru-software-v6.3

# ── Python dependencies ──────────────────────────────────────────────────────
# Install into the lauren user's local site-packages (service uses PYTHONPATH).
sudo -u lauren pip3 install --break-system-packages --upgrade pip setuptools
sudo -u lauren pip3 install --break-system-packages "$REPO/backend[dev]"

# ── Build PRU firmware ───────────────────────────────────────────────────────
make -C "$REPO/pru/pru0_timestamp" clean all
cp "$REPO/pru/pru0_timestamp/am335x-pru0-fw" /lib/firmware/

# ── Build + install DTS overlay ─────────────────────────────────────────────
make -C "$REPO/dts" BB-PRU0-CAN-TS-00A0.dtbo
cp "$REPO/dts/BB-PRU0-CAN-TS-00A0.dtbo" /lib/firmware/

# ── Install systemd services ─────────────────────────────────────────────────
cp "$REPO/systemd/pru-loader.service"  /etc/systemd/system/
cp "$REPO/systemd/can-sniffer.service" /etc/systemd/system/
chmod +x "$REPO/scripts/setup_pru.sh" "$REPO/scripts/setup_can.sh"

systemctl daemon-reload
systemctl enable pru-loader.service can-sniffer.service

# ── uEnv.txt checks ──────────────────────────────────────────────────────────
UENV=/boot/uEnv.txt
warn() { echo "WARNING: $*" >&2; }

grep -q "uboot_overlay_addr0=/lib/firmware/BB-PRU0-CAN-TS-00A0.dtbo" "$UENV" \
    || warn "Add 'uboot_overlay_addr0=/lib/firmware/BB-PRU0-CAN-TS-00A0.dtbo' to $UENV"
grep -q "disable_uboot_overlay_video=1" "$UENV" \
    || warn "Add 'disable_uboot_overlay_video=1' to $UENV"
grep -q "enable_uboot_cape_universal=0" "$UENV" \
    || warn "Add 'enable_uboot_cape_universal=0' to $UENV"
grep -q "memmap=8K\\\$0x9F000000" "$UENV" \
    || warn "Add 'memmap=8K\$0x9F000000' to cmdline in $UENV"

echo ""
echo "Done. Reboot to activate the systemd services."
echo "Dashboard will be at http://<BBB-IP>:8000/"
