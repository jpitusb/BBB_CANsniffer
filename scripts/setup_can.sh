#!/usr/bin/env bash
set -euo pipefail

BITRATE=${1:-500000}
IFACE=can0

ip link set "$IFACE" down 2>/dev/null || true
ip link set "$IFACE" type can \
    bitrate "$BITRATE" \
    listen-only on \
    berr-reporting on \
    restart-ms 100
ip link set "$IFACE" up
ip -details link show "$IFACE"
echo "can0 up at ${BITRATE} bit/s"
