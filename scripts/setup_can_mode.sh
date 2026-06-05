#!/bin/bash
# setup_can_mode.sh — switch can0 between listen-only (normal sniffer) and
# normal mode (needed when a second node must ACK frames during testing).
#
# Usage:
#   sudo ./setup_can_mode.sh listen   # default sniffer mode (no ACK)
#   sudo ./setup_can_mode.sh normal   # test mode (ACKs frames from generator)

set -e

MODE=${1:-listen}
BITRATE=500000

ip link set can0 down 2>/dev/null || true

case "$MODE" in
    normal)
        ip link set can0 type can bitrate $BITRATE listen-only off berr-reporting on
        ip link set can0 up
        echo "can0 → normal mode (will ACK frames)"
        ;;
    listen|listen-only)
        ip link set can0 type can bitrate $BITRATE listen-only on berr-reporting on
        ip link set can0 up
        echo "can0 → listen-only mode (passive sniffer)"
        ;;
    *)
        echo "Usage: $0 [listen|normal]"
        exit 1
        ;;
esac

ip -details link show can0 | grep -E "state|listen|bitrate"
