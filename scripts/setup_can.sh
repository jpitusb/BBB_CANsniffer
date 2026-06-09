#!/usr/bin/env bash
set -euo pipefail

BITRATE=${1:-500000}
IFACE=can1

ip link set "$IFACE" down 2>/dev/null || true
ip link set "$IFACE" type can \
    bitrate "$BITRATE" \
    listen-only on \
    berr-reporting on \
    restart-ms 100
ip link set "$IFACE" up

# Set P9.26/P9.24 to CAN mode (DCAN1). Done after can1 up because the
# driver has no pinctrl-0 and never resets these pins itself.
echo can > /sys/devices/platform/ocp/ocp:P9_26_pinmux/state
echo can > /sys/devices/platform/ocp/ocp:P9_24_pinmux/state

ip -details link show "$IFACE"
echo "can1 up at ${BITRATE} bit/s"
