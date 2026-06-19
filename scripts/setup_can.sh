#!/usr/bin/env bash
set -euo pipefail

BITRATE=${1:-1000000}
# berr-reporting: "on" surfaces bus-error frames for diagnostics, but on a busy
# bus at the WRONG bitrate the controller emits an error frame per misread frame
# (thousands/s) and floods the CPU. Pass "off" when the rate is unconfirmed or
# the bus is heavily loaded.
BERR=${2:-on}
IFACE=can1

ip link set "$IFACE" down 2>/dev/null || true
ip link set "$IFACE" type can \
    bitrate "$BITRATE" \
    listen-only on \
    berr-reporting "$BERR" \
    restart-ms 100
ip link set "$IFACE" up

# Set P9.26/P9.24 to CAN mode (DCAN1). Done after can1 up because the
# driver has no pinctrl-0 and never resets these pins itself.
echo can > /sys/devices/platform/ocp/ocp:P9_26_pinmux/state
echo can > /sys/devices/platform/ocp/ocp:P9_24_pinmux/state

ip -details link show "$IFACE"
echo "can1 up at ${BITRATE} bit/s (berr-reporting ${BERR})"
