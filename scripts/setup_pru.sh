#!/bin/bash
# setup_pru.sh — configure and start the PRU0 CAN timestamp firmware
#
# Must be run as root.  Called by pru-loader.service.
#
# Approach: GPIO2_7 (P8.46) falling-edge interrupt → PRUSS INTC system event 24
# → channel 0 → host interrupt 0 → PRU R31[30].  R31[30] is independent of
# GPCFG0 (which locks R31[15:0] to MII_RT on this BBB/kernel).
#
# Wire: P8.46 → 1 kΩ → P9.24 (CAN RX from SN65HVD230).
#
# Steps:
#   1. Configure P8.46 (GPIO2_7) via sysfs for falling-edge interrupt.
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

# ── 1. Configure P8.46 / GPIO2_7 for falling-edge interrupt ─────────────
#
# Use sysfs to correctly set GPIO IRQENABLE (direct /dev/mem writes to
# IRQENABLE0 at 0x11C don't persist on OMAP4-compatible GPIO).
# The kernel registers its own IRQ handler, but we're using level-triggered
# PRUSS INTC (TYPE=1), so even if ARM clears IRQSTATUS first, the PRU still
# processes the event before clearing it.

P8_46_PINMUX=/sys/devices/platform/ocp/ocp:P8_46_pinmux
if [ -f "$P8_46_PINMUX/state" ]; then
    # gpio_pd state: GPIO input mode with pull-down (allows sysfs export)
    echo gpio_pd > "$P8_46_PINMUX/state" 2>/dev/null || \
    echo default  > "$P8_46_PINMUX/state" 2>/dev/null || true
fi

echo "P8.46 GPIO configured for falling-edge interrupt → PRUSS sysevt 24 (no ARM handler)"

# Configure GPIO2_7 for falling-edge interrupt via /dev/mem (no ARM handler).
# Sysfs "echo falling > edge" would register an ARM interrupt handler that fires
# at the CAN edge rate (~10 000/s), overwhelming the system.  Writing directly
# to the GPIO hardware registers enables the falling-edge detection without
# registering any ARM IRQ handler.
#
# Registers (OMAP4-compatible GPIO2, base 0x481AC000):
#   IRQENABLE_SET at 0x034: write bit 7 to enable interrupt output for GPIO2_7
#   FALLINGDETECT  at 0x14C: bit 7 = falling-edge detection
#   IRQSTATUS_CLR  at 0x02C: write bit 7 to clear any pending status

python3 - <<'PYEOF'
import mmap, struct, sys
GPIO2_BASE = 0x481AC000
BIT7 = (1 << 7)
try:
    with open('/dev/mem', 'r+b') as f:
        mm = mmap.mmap(f.fileno(), 0x200, offset=GPIO2_BASE)
        # Enable interrupt output for GPIO2_7 (writes to 0x034 = IRQENABLE_SET)
        struct.pack_into('<I', mm, 0x034, BIT7)
        # Enable falling-edge detection
        v = struct.unpack_from('<I', mm, 0x14C)[0]
        struct.pack_into('<I', mm, 0x14C, v | BIT7)
        # Clear any pending interrupt
        struct.pack_into('<I', mm, 0x02C, BIT7)
    print("GPIO2_7 (P8.46) falling-edge interrupt enabled (no ARM handler)")
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
        mm.flush()

# EISR: enable event 24 (write index, not bitmask)
with open('/dev/mem', 'r+b') as f:
    mm = mmap.mmap(f.fileno(), 0x100, offset=BASE)
    struct.pack_into('<I', mm, 0x28, 24)   # EISR = 24
    struct.pack_into('<I', mm, 0x34, 0)    # HIEISR = 0 (host int 0)
    mm.flush()

print("PRUSS INTC armed: event24→ch0→hostint0→R31[30]")
PYEOF
