#!/bin/bash
# setup_pru.sh — configure and start the PRU0 CAN timestamp firmware
#
# Must be run as root.  Called by pru-loader.service.
#
# Approach: GPIO2_3 (P8.08) falling-edge interrupt → PRUSS INTC system event 24
# → channel 0 → host interrupt 0 → PRU R31[30].  R31[30] is independent of
# GPCFG0 (which locks R31[15:0] to MII_RT on this BBB/kernel).
#
# Wire: P8.08 → 1 kΩ → P9.24 (CAN RX from SN65HVD230).  (Moved from P8.46,
# which is a SYSBOOT pin that blocked boot with the jumper attached.)
#
# Steps:
#   1. Configure P8.08 (GPIO2_3) pad mux + falling-edge interrupt via /dev/mem.
#   2. Enable PRU OCP master port.
#   3. Start PRU0 via remoteproc.
#   4. Arm PRUSS INTC from ARM side (belt-and-suspenders alongside PRU SBCO).

set -e

REMOTEPROC=/sys/class/remoteproc/remoteproc1

# ── Wait for remoteproc1 ──────────────────────────────────────────────────
TIMEOUT=60
until [ -f "${REMOTEPROC}/state" ] || [ $TIMEOUT -le 0 ]; do
    sleep 1; TIMEOUT=$((TIMEOUT-1))
done
[ -f "${REMOTEPROC}/state" ] || { echo "ERROR: remoteproc1 not found" >&2; exit 1; }

# ── 1. Configure P8.08 / GPIO2_3 for falling-edge interrupt ─────────────
#
# P8.08 (GPMC_OEN_REN ball, GPIO2_3) replaces P8.46 (LCD_DATA1, GPIO2_7).
# P8.46 is an AM335x SYSBOOT pin: with the edge-tap jumper attached, the
# transceiver corrupts the sampled boot mode at power-on and the board won't
# boot.  P8.08 is a GPMC pin (boots high-Z, not a SYSBOOT pin), so it is
# boot-safe.  It stays in the GPIO2 bank, so the PRUSS INTC routing (GPIO2
# module interrupt → system event 24) is completely unchanged.
#
# The P8.08 pinmux helper only exposes its GPMC "default" state (not a gpio
# state), so set mode-7 (gpio2_3) input directly via the control-module pad
# register instead of sysfs.

python3 - <<'PYEOF'
import mmap, struct, sys
# Control module pad config: conf_gpmc_oen_ren (P8.08) at 0x44E10000 + 0x894.
# Value 0x27 = mux mode 7 (gpio2_3) | RXACTIVE bit5 (input enable) | pulldown.
CTRL_BASE    = 0x44E10000
CONF_OEN_REN = 0x894
try:
    with open('/dev/mem', 'r+b') as f:
        mm = mmap.mmap(f.fileno(), 0x1000, offset=CTRL_BASE)
        struct.pack_into('<I', mm, CONF_OEN_REN, 0x27)
    print("P8.08 (GPIO2_3) padmux set to mode-7 GPIO input (pulldown)")
except Exception as e:
    print("WARNING: P8.08 padmux config failed:", e, file=sys.stderr)
PYEOF

echo "P8.08 GPIO configured for falling-edge interrupt → PRUSS sysevt 24 (no ARM handler)"

# Configure GPIO2_3 for falling-edge interrupt via /dev/mem (no ARM handler).
# Sysfs "echo falling > edge" would register an ARM interrupt handler that fires
# at the CAN edge rate, overwhelming the system.  Writing directly to the GPIO
# hardware registers enables falling-edge detection with no ARM IRQ handler.
#
# Registers (OMAP4-compatible GPIO2, base 0x481AC000):
#   IRQENABLE_SET at 0x034: write bit 3 to enable interrupt output for GPIO2_3
#   FALLINGDETECT  at 0x14C: bit 3 = falling-edge detection
#   IRQSTATUS_CLR  at 0x02C: write bit 3 to clear any pending status

python3 - <<'PYEOF'
import mmap, struct, sys
GPIO2_BASE = 0x481AC000
BIT3 = (1 << 3)
try:
    with open('/dev/mem', 'r+b') as f:
        mm = mmap.mmap(f.fileno(), 0x200, offset=GPIO2_BASE)
        # Enable interrupt output for GPIO2_3 (writes to 0x034 = IRQENABLE_SET)
        struct.pack_into('<I', mm, 0x034, BIT3)
        # Enable falling-edge detection
        v = struct.unpack_from('<I', mm, 0x14C)[0]
        struct.pack_into('<I', mm, 0x14C, v | BIT3)
        # Clear any pending interrupt
        struct.pack_into('<I', mm, 0x02C, BIT3)
    print("GPIO2_3 (P8.08) falling-edge interrupt enabled (no ARM handler)")
except Exception as e:
    print("WARNING: GPIO /dev/mem config failed:", e, file=sys.stderr)
PYEOF

# ── 2. Enable PRU OCP master port ─────────────────────────────────────────
python3 - <<'PYEOF'
import mmap, struct, sys
try:
    with open('/dev/mem', 'r+b') as f:
        mm = mmap.mmap(f.fileno(), 0x100, offset=0x4A326000)
        v = struct.unpack_from('<I', mm, 4)[0]
        struct.pack_into('<I', mm, 4, v & ~0x10)
        v2 = struct.unpack_from('<I', mm, 4)[0]
        if v2 & 0x10:
            print("ERROR: STANDBY_INIT still set", file=sys.stderr)
            sys.exit(1)
        print(f"OCP enabled (SYSCFG=0x{v2:08X})")
except PermissionError:
    print("ERROR: /dev/mem not accessible — run as root", file=sys.stderr)
    sys.exit(1)
PYEOF

# ── 3. Start PRU0 ────────────────────────────────────────────────────────
if [ "$(cat ${REMOTEPROC}/state 2>/dev/null)" = "running" ]; then
    echo stop > "${REMOTEPROC}/state"
fi
echo start > "${REMOTEPROC}/state"

STATE=$(cat "${REMOTEPROC}/state")
echo "PRU0 state: ${STATE}"
[ "${STATE}" = "running" ] || { echo "ERROR: PRU0 failed to start" >&2; exit 1; }

# ── 4. Arm PRUSS INTC from ARM side ──────────────────────────────────────
#
# The PRU firmware also sets GER/EISR/HIEISR via SBCO (C0 = PRUSS INTC).
# This ARM-side step is belt-and-suspenders, and also sets the large-offset
# registers (SIPR0, SITR0, HIER) that SBCO can't reach (8-bit offset limit).
#
# PRUSS INTC base = 0x4A320000
#   SIPR0 (0xD00): event polarity (1 = active HIGH)
#   SITR0 (0xD80): event type (1 = level-triggered)
#   CMR3  (0x40C): event 24 → channel 0 (reset default = 0 ✓)
#   HMR0  (0x800): channel 0 → host interrupt 0 (reset default = 0 ✓)
#   GER   (0x010): global enable
#   HIER  (0x1500): enable host interrupt 0 → PRU R31[30]
#   EISR  (0x028): enable system event 24

python3 - <<'PYEOF' || echo "WARNING: INTC ARM config failed (non-fatal)"
import mmap, struct

BASE = 0x4A320000
REGS = [
    (0xD00, 1 << 24, 'SIPR0 bit24=1 (active-HIGH)'),
    (0xD80, 1 << 24, 'SITR0 bit24=1 (level)'),
    (0x010, 1,       'GER bit0=1 (global enable)'),
    (0x1500,1,       'HIER bit0=1 (host int 0 enable)'),
]
for off, mask, label in REGS:
    phys = BASE + off
    pg = phys & ~0xFFF
    pg_off = phys & 0xFFF
    pg_sz = max(0x2000, (pg_off + 8 + 0xFFF) & ~0xFFF)
    with open('/dev/mem', 'r+b') as f:
        mm = mmap.mmap(f.fileno(), pg_sz, offset=pg)
        v = struct.unpack_from('<I', mm, pg_off)[0]
        struct.pack_into('<I', mm, pg_off, v | mask)
        # No mm.flush(): msync() returns EINVAL on /dev/mem device mappings.
        # pack_into writes directly to physical hardware — no writeback needed.

# EISR: enable event 24 (write index, not bitmask)
with open('/dev/mem', 'r+b') as f:
    mm = mmap.mmap(f.fileno(), 0x100, offset=BASE)
    struct.pack_into('<I', mm, 0x28, 24)   # EISR = 24
    struct.pack_into('<I', mm, 0x34, 0)    # HIEISR = 0 (host int 0)

print("PRUSS INTC armed: event24→ch0→hostint0→R31[30]")
PYEOF
