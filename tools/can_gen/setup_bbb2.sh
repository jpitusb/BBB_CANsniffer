#!/bin/bash
# setup_bbb2.sh — configure BBB #2 as a CAN traffic generator.
# Run as root after cloning the repo to /opt/can_sniffer.
#
# Both BBB #1 (sniffer) and BBB #2 (generator) use the same CAN pins:
#   P9.24 (RX) / P9.26 (TX)  →  can1  (DCAN1)
#
# Wiring for BBB #2:
#   SN65HVD230 RXD → P9.24 (CAN RX)
#   SN65HVD230 TXD → P9.26 (CAN TX)
#   Bus CANH/CANL connected to BBB #1's bus (120Ω at each end)

set -e
REPO=/opt/can_sniffer

echo "=== BBB #2 CAN Generator Setup ==="

# Install Python dependency system-wide so it's accessible under sudo
echo "Installing python-can system-wide..."
pip3 install --break-system-packages --quiet python-can

# Bring up can1 in normal mode (transmitter), then set P9.24/P9.26 to CAN mode.
# DCAN1 overlay has no pinctrl-0, so the driver never touches these pins.
ip link set can1 down 2>/dev/null || true
ip link set can1 type can bitrate 1000000 listen-only off berr-reporting on restart-ms 100
ip link set can1 up
echo can > /sys/devices/platform/ocp/ocp:P9_26_pinmux/state
echo can > /sys/devices/platform/ocp/ocp:P9_24_pinmux/state
echo "can1: $(ip link show can1 | grep state)"

echo ""
echo "=== Setup complete ==="
echo "Run the traffic generator (uses can1 by default):"
echo "  sudo python3 $REPO/tools/can_gen/generator.py --list"
echo "  sudo python3 $REPO/tools/can_gen/generator.py -s normal --loop"
echo "  sudo python3 $REPO/tools/can_gen/generator.py -s normal -s babble -s bus_flood --loop"
