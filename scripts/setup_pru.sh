#!/bin/bash
# setup_pru.sh — configure and start the PRU0 CAN timestamp firmware
#
# Must be run as root (or via sudo).  Called by pru-loader.service.
#
# Prerequisites (persistent across reboots via /boot/uEnv.txt):
#   uboot_overlay_addr0=/lib/firmware/BB-PRU0-CAN-TS-00A0.dtbo
#   disable_uboot_overlay_video=1
#   enable_uboot_cape_universal=0
#   cmdline=... memmap=8K$0x9F000000
#
# Steps performed here (must be done every boot):
#   1. Configure P8.45 as PRU input (pr1_pru0_pru_r31_0, mode 6)
#   2. Enable PRU OCP master port (PRUSS_CFG.SYSCFG STANDBY_INIT=0)
#   3. Start PRU0 firmware via remoteproc

set -e

REMOTEPROC=/sys/class/remoteproc/remoteproc1
PINMUX=/sys/devices/platform/ocp/ocp:P8_45_pinmux
PRUSS_CFG_SYSCFG=/sys/bus/platform/devices/4a326000.cfg/...  # not used; we use python

# 1. P8.45 → PRU input mode
if [ -f "${PINMUX}/state" ]; then
    echo pruin > "${PINMUX}/state"
    echo "P8.45 set to pruin"
else
    echo "WARNING: ${PINMUX}/state not found — PRUSS overlay may not be loaded" >&2
fi

# 2. Enable OCP master from ARM side.
# gcc-pru firmware's ocp_enable() attempts this via SBCO/C4 as well,
# but the C4 constant table mapping is unverified.  This ARM-side enable
# is the reliable path.
python3 - <<'EOF'
import mmap, struct, sys
try:
    with open('/dev/mem', 'r+b') as f:
        mm = mmap.mmap(f.fileno(), 0x100, offset=0x4A326000)
        v = struct.unpack_from('<I', mm, 4)[0]
        struct.pack_into('<I', mm, 4, v & ~0x10)
        v2 = struct.unpack_from('<I', mm, 4)[0]
        if v2 & 0x10:
            print("ERROR: STANDBY_INIT still set after write", file=sys.stderr)
            sys.exit(1)
        print(f"OCP enabled (SYSCFG=0x{v2:08X})")
except PermissionError:
    print("ERROR: /dev/mem not accessible — run as root", file=sys.stderr)
    sys.exit(1)
EOF

# 3. Start PRU0
if [ "$(cat ${REMOTEPROC}/state 2>/dev/null)" = "running" ]; then
    echo stop > "${REMOTEPROC}/state"
fi
echo start > "${REMOTEPROC}/state"

STATE=$(cat "${REMOTEPROC}/state")
echo "PRU0 state: ${STATE}"
[ "${STATE}" = "running" ] || { echo "ERROR: PRU0 failed to start" >&2; exit 1; }
