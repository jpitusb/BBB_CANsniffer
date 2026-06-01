#!/usr/bin/env bash
# Run on the BeagleBone Black as root.
set -euo pipefail

apt-get update
apt-get install -y \
    can-utils \
    python3-pip \
    python3-venv \
    build-essential \
    device-tree-compiler

# TI PRU CGT (clpru) — install manually from TI website if not available via apt.
# https://www.ti.com/tool/PRU-CGT
# Uncomment if a Debian package is available:
# apt-get install -y ti-pru-cgt

DEST=/opt/can_sniffer
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --upgrade pip
"$DEST/.venv/bin/pip" install -e "$DEST/backend[dev]"

systemctl daemon-reload
echo "Dependencies installed."
