#!/bin/bash
# setup_bbb2.sh — configure BBB #2 as a CAN traffic + fault generator.
# Run as root after cloning the repo to /opt/can_sniffer.
#
# Both BBB #1 (sniffer) and BBB #2 (generator) use the same CAN pins:
#   P9.19 (RX) / P9.20 (TX)  →  can0
#
# Wiring for BBB #2:
#   SN65HVD230 TXD  ← P9.20 (CAN TX)
#                   ← P8.45 (PRU0 R30[0] output, mux mode 5) via diode/AND
#   SN65HVD230 RXD  → P9.19 (CAN RX)
#   Bus CANH/CANL connected to BBB #1's bus

set -e
REPO=/opt/can_sniffer

echo "=== BBB #2 CAN Generator Setup ==="

# 1. memmap reservation (same as BBB #1 — needed for PRU fault injector DDR)
UENV=/boot/uEnv.txt
if ! grep -q "memmap=8K" "$UENV"; then
    sed -i 's/rng_core.default_quality=100 quiet$/rng_core.default_quality=100 quiet memmap=8K$0x9F000000/' "$UENV"
    echo "Added memmap to $UENV — reboot required after this script"
fi

# 2. Build PRU fault injection firmware
echo "Building PRU fault injector..."
make -C "$REPO/pru/pru0_fault_inject" clean all
cp "$REPO/pru/pru0_fault_inject/am335x-pru0-fault-inject" /lib/firmware/

# 3. Configure P8.45 as PRU OUTPUT (mode 5 = pr1_pru0_pru_r30_0)
echo "Setting P8.45 to PRU output mode..."
echo pruout > /sys/devices/platform/ocp/ocp:P8_45_pinmux/state

# 4. Enable OCP from ARM
python3 - <<'EOF'
import mmap, struct
with open('/dev/mem', 'r+b') as f:
    mm = mmap.mmap(f.fileno(), 0x100, offset=0x4A326000)
    v = struct.unpack_from('<I', mm, 4)[0]
    struct.pack_into('<I', mm, 4, v & ~0x10)
    print(f"OCP enabled (SYSCFG=0x{struct.unpack_from('<I',mm,4)[0]:08X})")
EOF

# 5. Load PRU firmware
RPROC=/sys/class/remoteproc/remoteproc1
echo stop > "$RPROC/state" 2>/dev/null || true
echo am335x-pru0-fault-inject > "$RPROC/firmware"
echo start > "$RPROC/state"
sleep 1
echo "PRU state: $(cat $RPROC/state)"

# 6. Set CAN pin mux and bring up can0 in normal mode (transmitter)
echo "Setting CAN pin mux (P9.20=TX, P9.19=RX)..."
echo can > /sys/devices/platform/ocp/ocp:P9_20_pinmux/state
echo can > /sys/devices/platform/ocp/ocp:P9_19_pinmux/state

ip link set can0 down 2>/dev/null || true
ip link set can0 type can bitrate 500000 berr-reporting on
ip link set can0 up
echo "can0: $(ip link show can0 | grep state)"

echo ""
echo "=== Setup complete ==="
echo "Run the traffic generator (uses can0 by default):"
echo "  sudo python3 $REPO/tools/can_gen/generator.py --list"
echo "  sudo python3 $REPO/tools/can_gen/generator.py -s normal --loop"
echo "  sudo python3 $REPO/tools/can_gen/generator.py -s normal -s babble -s glitch_burst --loop"
