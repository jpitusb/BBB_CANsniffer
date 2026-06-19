#!/bin/bash
# test_can1_tx.sh — loopback test: DCAN1 transmits, DCAN0 receives + ACKs.
#
# CAN requires at least one other node to ACK every frame.  In normal
# operation can0 is listen-only (no ACK) to avoid disturbing external buses.
# For this test we temporarily bring can0 up in normal mode so it ACKs can1.
#
# Hardware: second SN65HVD230 on P9.20 (TX) / P9.22 (RX), both transceivers
# wired to the same CANH/CANL bus with 120Ω at each end.
#
# Usage: sudo ./test_can1_tx.sh [--loop]

set -e

BITRATE=1000000
LOOP=false
[ "${1:-}" = "--loop" ] && LOOP=true

# Stop the sniffer service so we can reconfigure can0
echo "Stopping can-sniffer service..."
systemctl stop can-sniffer.service 2>/dev/null || true

cleanup() {
    echo ""
    echo "Tearing down test interfaces..."
    ip link set can1 down 2>/dev/null || true
    ip link set can0 down 2>/dev/null || true
    echo "Restarting can-sniffer service (restores listen-only mode)..."
    systemctl start can-sniffer.service
    echo "Done. Dashboard back at http://$(hostname -I | awk '{print $1}'):8000/"
    exit 0
}
trap cleanup INT TERM EXIT

# Set DCAN1 pin mux: TX=P9.20 (can), RX=P9.19 (can)
echo "Setting DCAN1 pin mux (P9.20=TX, P9.19=RX)..."
echo can > /sys/devices/platform/ocp/ocp:P9_20_pinmux/state
echo can > /sys/devices/platform/ocp/ocp:P9_19_pinmux/state

# can0 in NORMAL mode so it can ACK can1's frames
echo "Bringing can0 up in normal mode (ACK enabled)..."
ip link set can0 down 2>/dev/null || true
ip link set can0 type can bitrate "${BITRATE}" berr-reporting on
ip link set can0 up

# can1 in normal mode as transmitter
echo "Bringing can1 up at ${BITRATE} bit/s..."
ip link set can1 down 2>/dev/null || true
ip link set can1 type can bitrate "${BITRATE}"
ip link set can1 up

echo ""
echo "Both interfaces up.  Listening on can0..."
candump can0 &
CDPID=$!

sleep 0.3

send_burst() {
    cansend can1 001#0102030405060708
    cansend can1 100#DEADBEEF
    cansend can1 200#0000000000000000
    cansend can1 7FF#FF
    cansend can1 18FF5500#CAFEBABE
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
    sleep 0.5
fi

kill $CDPID 2>/dev/null || true
