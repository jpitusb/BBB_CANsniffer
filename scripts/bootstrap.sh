#!/usr/bin/env bash
# bootstrap.sh — FIRST-TIME setup for a fresh BeagleBone Black.
#
# Run once immediately after flashing Debian Bookworm and SSH-ing in.
# Sets hostname, clones the repo, patches /boot/uEnv.txt, then reboots.
# After the reboot, run install_deps.sh to build and install everything.
#
# Usage (as root or with sudo):
#   curl -sL https://raw.githubusercontent.com/jpitusb/BBB_CANsniffer/master/scripts/bootstrap.sh | sudo bash
# or:
#   sudo bash /path/to/bootstrap.sh [--hostname NAME] [--repo URL]

set -euo pipefail

HOSTNAME_DEFAULT="can-sniffer"
REPO_URL="https://github.com/jpitusb/BBB_CANsniffer.git"
REPO_DIR="/opt/can_sniffer"
UENV="/boot/uEnv.txt"

# ── Argument parsing ─────────────────────────────────────────────────────────
NEW_HOSTNAME="$HOSTNAME_DEFAULT"
while [[ $# -gt 0 ]]; do
    case $1 in
        --hostname) NEW_HOSTNAME="$2"; shift 2 ;;
        --repo)     REPO_URL="$2";    shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Detect the non-root user who invoked sudo ─────────────────────────────────
REAL_USER="${SUDO_USER:-${USER}}"
if [[ "$REAL_USER" == "root" ]]; then
    # Fallback: first non-root user in /home
    REAL_USER=$(ls /home | head -1)
fi
echo "Real user: $REAL_USER"

# ── Hostname ─────────────────────────────────────────────────────────────────
if [[ "$(hostname)" != "$NEW_HOSTNAME" ]]; then
    echo "Setting hostname to $NEW_HOSTNAME..."
    hostnamectl set-hostname "$NEW_HOSTNAME"
    # Ensure it resolves locally
    grep -q "127.0.1.1.*$NEW_HOSTNAME" /etc/hosts \
        || echo "127.0.1.1 $NEW_HOSTNAME" >> /etc/hosts
fi

# ── Install git if missing ────────────────────────────────────────────────────
which git >/dev/null 2>&1 || apt-get install -y git

# ── Clone repo ───────────────────────────────────────────────────────────────
if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "Cloning $REPO_URL → $REPO_DIR ..."
    git clone "$REPO_URL" "$REPO_DIR"
    chown -R "$REAL_USER:$REAL_USER" "$REPO_DIR"
else
    echo "Repo already at $REPO_DIR, pulling latest..."
    sudo -u "$REAL_USER" git -C "$REPO_DIR" pull
fi

# ── uEnv.txt ─────────────────────────────────────────────────────────────────
echo "Patching $UENV ..."
cp "$UENV" "${UENV}.bak"

patch_uenv() {
    local key="$1" value="$2"
    if grep -q "^#*${key}=" "$UENV"; then
        # Uncomment and set if it exists (commented or not)
        sed -i "s|^#*${key}=.*|${key}=${value}|" "$UENV"
    else
        # Append if not present at all
        echo "${key}=${value}" >> "$UENV"
    fi
}

patch_uenv "disable_uboot_overlay_video" "1"
patch_uenv "enable_uboot_cape_universal" "0"

echo "uEnv.txt changes:"
diff "${UENV}.bak" "$UENV" || true

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo "Bootstrap complete. Rebooting in 5 seconds..."
echo "After reboot, SSH back in and run:"
echo "  sudo $REPO_DIR/scripts/install_deps.sh"
echo "========================================================"
sleep 5
reboot
