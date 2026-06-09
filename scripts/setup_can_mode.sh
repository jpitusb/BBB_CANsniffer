#!/bin/bash
# setup_can_mode.sh — switch can1 between listen-only (normal sniffer) and
# normal mode (needed when a second node must ACK frames during testing).
#
# Usage:
#   sudo ./setup_can_mode.sh listen   # default sniffer mode (no ACK)
#   sudo ./setup_can_mode.sh normal   # test mode (ACKs frames from generator)

set -e

MODE=${1:-listen}
BITRATE=500000

ip link set can1 down 2>/dev/null || true

case "$MODE" in
    normal)
        ip link set can1 type can bitrate $BITRATE listen-only off berr-reporting on
        ip link set can1 up
        echo can > /sys/devices/platform/ocp/ocp:P9_26_pinmux/state
        echo can > /sys/devices/platform/ocp/ocp:P9_24_pinmux/state
        echo "can1 → normal mode (will ACK frames)"
        ;;
    listen|listen-only)
        ip link set can1 type can bitrate $BITRATE listen-only on berr-reporting on
        ip link set can1 up
        echo can > /sys/devices/platform/ocp/ocp:P9_26_pinmux/state
        echo can > /sys/devices/platform/ocp/ocp:P9_24_pinmux/state
        echo "can1 → listen-only mode (passive sniffer)"
        # Restart the sniffer service if it was stopped
        systemctl start can-sniffer.service 2>/dev/null && echo "can-sniffer service started" || true
        ;;
    *)
        echo "Usage: $0 [listen|normal]"
        exit 1
        ;;
esac

ip -details link show can1 | grep -E "state|listen|bitrate"
