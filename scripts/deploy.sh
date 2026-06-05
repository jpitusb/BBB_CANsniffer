#!/usr/bin/env bash
# deploy.sh — push local changes to a running BBB and reload the service.
#
# Usage:
#   BBB_HOST=10.183.184.161 BBB_USER=lauren ./scripts/deploy.sh
#   BBB_HOST=10.183.184.218 BBB_USER=lauren ./scripts/deploy.sh
set -euo pipefail

BBB_HOST=${BBB_HOST:-192.168.7.2}
BBB_USER=${BBB_USER:-lauren}
DEST=/opt/can_sniffer
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Deploying to ${BBB_USER}@${BBB_HOST}:${DEST} ..."

rsync -az --delete \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.db' \
    --exclude='.venv' \
    "${REPO_ROOT}/" \
    "${BBB_USER}@${BBB_HOST}:${DEST}/"

# Re-install the Python package so new modules are picked up, then restart.
# PYTHONPATH points to the user's local site-packages; adjust if different user.
ssh "${BBB_USER}@${BBB_HOST}" bash -s <<'REMOTE'
set -e
REAL_USER=$(whoami)
REAL_HOME=$(eval echo ~"$REAL_USER")
PIP="$REAL_HOME/.local/bin/pip3"

echo "Reinstalling Python package..."
"$PIP" install --break-system-packages --quiet "/opt/can_sniffer/backend"

echo "Fixing script permissions..."
find /opt/can_sniffer/scripts /opt/can_sniffer/tools -name "*.sh" -o -name "generator.py" \
    2>/dev/null | xargs chmod +x 2>/dev/null || true

echo "Reloading systemd and restarting services..."
echo "${SUDO_PASS:-}" | sudo -S systemctl daemon-reload 2>/dev/null || \
    sudo systemctl daemon-reload
echo "${SUDO_PASS:-}" | sudo -S systemctl restart pru-loader.service can-sniffer.service 2>/dev/null || \
    sudo systemctl restart pru-loader.service can-sniffer.service

# Wait briefly then check
sleep 3
sudo systemctl is-active pru-loader can-sniffer
echo "Deploy complete — dashboard at http://$(hostname -I | awk '{print $1}'):8000/"
REMOTE
