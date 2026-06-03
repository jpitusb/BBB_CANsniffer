# gcc-pru on BeagleBone Black — Lessons Learned

This document captures everything non-obvious discovered while getting
PRU0 firmware working with `gcc-pru` on a BBB running Debian 12 /
5.10-ti kernel.  Read this before starting a new PRU project to avoid
weeks of debugging.

---

## 1. The gcc-pru memory model is not what you expect

### SBBO/LBBO always uses ARM physical addresses

`gcc-pru` compiles every C pointer dereference into `SBBO`/`LBBO`
instructions.  These instructions send the address **directly to the
L3 interconnect as an ARM physical address**.  There is no
PRUSS-internal routing.

Consequences:

| Address used in C | Where SBBO/LBBO actually writes |
|---|---|
| `0x0000`–`0x1FFF` (PRUDMEM origin 0) | ARM boot ROM — **silently dropped** |
| `0x4A300000`–`0x4A33FFFF` (PRUSS ARM range) | OCP self-targeting — **blocked/stalled** |
| `0x9F000000` (DDR) | DDR — **works** with OCP enabled |
| `0x40300000` (OCMC0) | Works, but **kernel may use OCMC0** for suspend code — dangerous |

**Do not** try to route SBBO to `0x4A300000` (PRU DRAM) or
`0x4A310000` (Shared RAM) — the PRUSS blocks OCP self-targeting on
AM335x.

### SBCO/LBCO uses the constant table (always works)

`SBCO`/`LBCO` instructions address via the PRU's constant table
entries and are routed **internally through the PRUSS** without using
the OCP master.  They always work regardless of `STANDBY_INIT`.

Use SBCO/LBCO for:
- **IEP timer registers** — constant table C26
- **PRUSS_CFG registers** — constant table C4 (best-effort; see §3)
- Any PRUSS peripheral

gcc-pru does not generate SBCO/LBCO from C code.  You must use inline
assembly.

### Constant table indices for AM335x

| Index | Peripheral | Offset for common registers |
|---|---|---|
| C4 | PRUSS_CFG | +0x04 = SYSCFG |
| C26 | PRUSS IEP | +0x00 = TMR_GLB_CFG, +0x0C = TMR_CNT |

Verify C4 maps to PRUSS_CFG on your board before relying on it — in
practice, pre-enable OCP from the ARM side as the primary path.

---

## 2. The OCP master must be enabled before any SBBO to DDR

By default `PRUSS_CFG.SYSCFG` bit 4 (`STANDBY_INIT`) = 1, meaning the
PRU OCP master is in standby and all `SBBO` to external addresses stall.

**Pre-enable from the ARM side** before starting the PRU firmware:

```python
import mmap, struct
with open('/dev/mem', 'r+b') as f:
    mm = mmap.mmap(f.fileno(), 0x100, offset=0x4A326000)
    v = struct.unpack_from('<I', mm, 4)[0]
    struct.pack_into('<I', mm, 4, v & ~0x10)   # clear STANDBY_INIT
```

- PRUSS_CFG base: `0x4A326000`
- SYSCFG at offset `0x04`
- Bit 4 = `STANDBY_INIT`; clear it to enable the OCP master

This setting survives `echo stop; echo start` for remoteproc — the
pru_rproc driver does not reset PRUSS_CFG.

The firmware also calls `ocp_enable()` via SBCO (inline asm, constant
table C4) as a backup, but treat the ARM-side pre-enable as the
primary.

---

## 3. Ring buffer must be in DDR, not PRUSS memory

PRUSS self-targeting (PRU OCP master → L3 → PRUSS OCP slave) is
**blocked** on AM335x.  The ring buffer cannot live in PRUSS Shared RAM
at `0x4A310000` or PRU DRAM at `0x4A300000`.

**Use DDR**, reserved from the kernel before it initialises its
allocator.

### Reserving DDR with memmap

Add to the kernel cmdline in `/boot/uEnv.txt`:

```
cmdline=... memmap=8K$0x9F000000
```

This tells the kernel to exclude 8 KB at `0x9F000000` from its page
allocator.  The memory is still accessible via `/dev/mem`.

A `reserved-memory` DTS node **does not work** for this — overlays are
applied after early memory initialisation, so the reservation is
ignored.  A DTS `no-map` node additionally blocks `/dev/mem` write
access.

### Accessing from Python

Open `/dev/mem` as `r+b` (writable) and use the default mmap (no
`access=` parameter).  `mmap.ACCESS_READ` creates a `MAP_PRIVATE`
snapshot — PRU writes after the mmap call will be invisible.

```python
with open('/dev/mem', 'r+b') as f:
    mm = mmap.mmap(f.fileno(), 0x2000, offset=0x9F000000)
    # reads now see live PRU writes
```

---

## 4. C static variables in the PRU firmware are broken

With `PRUDMEM` origin `0x00000000`, all `static` C variables (BSS,
`.data`) are placed at addresses `0x0000`–`0x1FFF`.  `SBBO` to those
addresses hits ARM boot ROM (read-only, writes silently dropped).

Workarounds:

1. **Store mutable state in the DDR ring buffer header** — add private
   fields to `pru_shm_t`.  SBBO to `0x9F000000+offset` works.

2. **Use PRU registers** — designate specific registers (`r24`–`r29`)
   for private variables using `register ... __asm__("rN")`.

3. **Use SBCO** — store in PRUSS Shared RAM or PRUSS DRAM via constant
   table, but this requires inline asm for every access.

Do **not** set `PRUDMEM` origin to `0x4A300000` (PRU DRAM ARM
physical) — SBBO to those addresses uses OCP self-targeting, which is
blocked.

### Stack corruption

The function prologue `sbbo` saves are also silently dropped.  This is
only safe because:
- `main()` is an infinite loop and never returns
- Function calls use R30 (link register), not the stack, for the return
  address
- Callee-saved register corruption on return is acceptable if the
  callee is a leaf function or if the caller doesn't use those registers
  after the call

If you need nested calls with preserved registers, use option 1 or 2
above.

---

## 5. IEP timer access requires constant table inline assembly

The IEP at ARM physical `0x4A32E000` is inside the PRUSS subsystem.
SBBO to that address uses OCP self-targeting — blocked.

Access IEP **only via SBCO/LBCO** with constant table C26:

```c
static inline uint32_t iep_cnt_read(void)
{
    uint32_t cnt;
    __asm__ volatile (
        "lbco r0, 26, 0x0C, 4 \n"
        "mov  %0, r0           \n"
        : "=r" (cnt) :: "r0"
    );
    return cnt;
}

static inline void iep_enable_and_reset(void)
{
    __asm__ volatile (
        "lbco r0, 26, 0x00, 4 \n"  /* read TMR_GLB_CFG */
        "set  r0, r0, 0        \n"  /* set CNT_ENABLE */
        "sbco r0, 26, 0x00, 4 \n"  /* write TMR_GLB_CFG */
        "ldi  r0, 0            \n"
        "sbco r0, 26, 0x0C, 4 \n"  /* reset TMR_CNT */
        ::: "r0"
    );
}
```

IEP register offsets from its base (C26): `TMR_GLB_CFG=0x00`,
`TMR_CNT=0x0C`.

---

## 6. Toolchain notes (Debian 12 / Bookworm)

```
sudo apt install gcc-pru binutils-pru ti-pru-software-v6.3
```

- `pru-software-support-package` has been renamed — install
  `ti-pru-software-v6.3` instead.
- Headers in `/usr/lib/ti/pru-software-support-package-v6.3/include/am335x/`
  use `cregister` pragmas that are `clpru`-only.  They cannot be used
  with `gcc-pru` for peripheral access — use inline SBCO/LBCO instead.
- The GNU PRU assembler takes numeric constant table indices (`4`, `26`),
  not symbolic names (`c4`, `C4`).

### Linker script

- `PRUDMEM` origin must be `0x00000000` (not `0x4A300000`).
- Include a `startup.S` that sets `sp = 0x1E00` before `jmp main`; a
  zero `sp` causes the first function prologue to wrap to `0xFFFFFFxx`
  which stalls the PRU.
- Constant table is a PRU hardware register; `ENTRY(main)` works; the
  PRU starts at the ELF entry point address.

---

## 7. Boot configuration checklist

In `/boot/uEnv.txt`:

```ini
# Load PRU overlay before cape-universal claims P8.45
uboot_overlay_addr0=/lib/firmware/BB-PRU0-CAN-TS-00A0.dtbo

# Free LCD data pins (P8.27-P8.46) — HDMI uses them by default
disable_uboot_overlay_video=1

# cape-universal claims all P8/P9 pins; disable so PRUSS can probe
enable_uboot_cape_universal=0

# Reserve 8 KB DDR for ring buffer (must be in cmdline, not DTS overlay)
cmdline=coherent_pool=1M ... memmap=8K$0x9F000000
```

The PRU DTS overlay (`BB-PRU0-CAN-TS-00A0.dtbo`) must:
1. Delete `pinctrl-0` and `pinctrl-names` from the `&pruss` node — the
   base BBB DTB assigns a pinctrl that conflicts with `ocp:P8_45_pinmux`.
2. NOT include a `reserved-memory` fragment — it is ignored at overlay
   load time.

---

## 8. Every-boot startup sequence

```bash
# 1. P8.45 → PRU input mode (pr1_pru0_pru_r31_0)
echo pruin > /sys/devices/platform/ocp/ocp:P8_45_pinmux/state

# 2. Pre-enable OCP master
python3 -c "
import mmap, struct
with open('/dev/mem', 'r+b') as f:
    mm = mmap.mmap(f.fileno(), 0x100, offset=0x4A326000)
    v = struct.unpack_from('<I', mm, 4)[0]
    struct.pack_into('<I', mm, 4, v & ~0x10)
"

# 3. Start PRU0
echo start > /sys/class/remoteproc/remoteproc1/state
```

See `scripts/setup_pru.sh` for the production version.

---

## 9. Verification

After starting, check:

```python
import mmap, struct
with open('/dev/mem', 'r+b') as f:
    mm = mmap.mmap(f.fileno(), 0x2000, offset=0x9F000000)
    magic, write_idx = struct.unpack_from('<II', mm, 0)
    assert magic == 0xCAFE1234, f"bad magic: {magic:#x}"
    print(f"magic OK, write_idx={write_idx}")

# IEP running?
with open('/dev/mem', 'r+b') as f:
    mm = mmap.mmap(f.fileno(), 0x100, offset=0x4A32E000)
    cfg = struct.unpack_from('<I', mm, 0)[0]
    assert cfg & 1, "IEP CNT_ENABLE not set"
    print(f"IEP running, TMR_GLB_CFG={cfg:#x}")
```
