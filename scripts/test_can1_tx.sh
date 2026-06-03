#!/bin/bash
# test_can1_tx.sh — bring up DCAN1 as a transmitter and send test frames
# into the CAN bus so can0 (in listen-only mode) and the PRU can capture them.
#
# Hardware required:
#   SN65HVD230 #2 wired to P9.20 (TX) / P9.22 (RX)
#   Both transceivers connected to the same CANH/CANL bus
#   120Ω termination at each end of the bus
#
# Usage: sudo ./test_can1_tx.sh [--loop]
#   --loop  keep sending frames every 100 ms until Ctrl-C

set -e

BITRATE=500000
LOOP=false
[ "${1:-}" = "--loop" ] && LOOP=true

# Bring up can1 in normal mode (not listen-only — it needs to transmit)
if [ "$(ip link show can1 | grep -c 'state UP')" -eq 0 ]; then
    echo "Bringing up can1 at ${BITRATE} bit/s..."
    ip link set can1 type can bitrate "${BITRATE}"
    ip link set can1 up
    echo "can1 up."
else
    echo "can1 already up."
fi

cleanup() {
    echo ""
    echo "Bringing down can1..."
    ip link set can1 down
    echo "Done."
    exit 0
}
trap cleanup INT TERM

send_burst() {
    # A mix of IDs and data patterns to exercise the dashboard
    cansend can1 001#0102030405060708   # short ID, 8 bytes
    cansend can1 100#DEADBEEF           # 4-byte payload
    cansend can1 200#0000000000000000   # all zeros
    cansend can1 7FF#FF                 # max 11-bit ID, 1 byte
    cansend can1 18FF5500#CAFEBABE      # extended 29-bit ID
}

if $LOOP; then
    echo "Sending frames every 100 ms — Ctrl-C to stop."
    while true; do
        send_burst
        sleep 0.1
    done
else
    echo "Sending one burst of 5 test frames..."
    send_burst
    echo "Done. Check candump on can0 or open the dashboard."
    ip link set can1 down
fi
