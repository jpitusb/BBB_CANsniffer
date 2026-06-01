#!/usr/bin/env bash
# Sync repo to the BBB and reload the service.
# Usage: BBB_HOST=192.168.7.2 BBB_USER=debian ./deploy.sh
set -euo pipefail

BBB_HOST=${BBB_HOST:-192.168.7.2}
BBB_USER=${BBB_USER:-debian}
DEST=/opt/can_sniffer
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

rsync -av --delete \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.venv' \
    --exclude='*.db' \
    "${REPO_ROOT}/" \
    "${BBB_USER}@${BBB_HOST}:${DEST}/"

ssh "${BBB_USER}@${BBB_HOST}" "
    cd ${DEST}/backend &&
    ${DEST}/.venv/bin/pip install -e '.[dev]' -q &&
    systemctl daemon-reload &&
    systemctl restart can-sniffer.service &&
    echo 'Deploy complete — service restarted'
"
