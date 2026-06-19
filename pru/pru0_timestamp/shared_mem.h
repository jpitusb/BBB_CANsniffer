#ifndef SHARED_MEM_H
#define SHARED_MEM_H

#include <stdint.h>

#define PRU_SHM_MAGIC        0xCAFE1234U
#define PRU_RING_DEPTH       256U           /* must be a power of 2 */

/*
 * Ring buffer in DDR at 0x9F000000.
 *
 * gcc-pru SBBO/LBBO always resolves as an ARM physical address on the L3
 * bus — there is no PRUSS-internal routing for SBBO.  PRUSS self-targeting
 * (OCP master → L3 → PRUSS OCP slave) is blocked on AM335x.  DDR is
 * external to PRUSS and writable via OCP with no self-targeting issue.
 *
 * Memory is reserved from the kernel via memmap=8K$0x9F000000 in the
 * kernel cmdline (/boot/uEnv.txt).  This makes it accessible via
 * /dev/mem MAP_SHARED (read+write) from Python.
 *
 * OCP must be enabled (PRUSS_CFG.SYSCFG STANDBY_INIT=0) before any SBBO
 * to DDR.  This is done from the ARM side before starting the PRU — see
 * scripts/setup_pru.sh.  The firmware also calls ocp_enable() as a
 * best-effort second attempt.
 */
#define PRU_SHM_ARM_ADDR     0x9F000000U
#define PRU_SHM_SIZE         0x2000U        /* 8 KB */

/* PRU event types — also used in Python as PruEventType enum */
#define EVT_SOF              0x01
#define EVT_GLITCH           0x02
#define EVT_DOMINANT_RUNAWAY 0x03

/*
 * Bit-timing thresholds from the original bit-classification design.
 * NOTE: these are NOT referenced by the current firmware — main.c only uses
 * the fixed BLIND_COUNTS event-rate limit, which is bitrate-independent.  They
 * are kept for documentation only; changing the bus bitrate (now 1 Mbit/s)
 * does NOT require recompiling the PRU firmware.  (Original values assumed
 * 500 kbit/s / 2000 ns per bit.)
 */
#define GLITCH_THRESHOLD_COUNTS  200U   /* unused */
#define SOF_MAX_COUNTS          4000U   /* unused */
#define IFS_COUNTS              1200U   /* unused */

/*
 * One ring buffer slot.  Layout must match Python struct "<BBHQI" (16 bytes):
 *   B  type       uint8
 *   B  flags      uint8
 *   H  seq        uint16
 *   Q  t_fall_ns  uint64  (IEP absolute nanoseconds; Python adds epoch_offset)
 *   I  pulse_ns   uint32  (0 for SOF — frame is still in progress at capture time)
 */
typedef struct __attribute__((packed)) {
    uint8_t  type;
    uint8_t  flags;       /* bit 0: IEP rollover occurred since previous entry */
    uint16_t seq;
    uint64_t t_fall_ns;
    uint32_t pulse_ns;
} pru_event_t;

/*
 * Shared memory layout at PRU_SHM_ARM_ADDR (DDR 0x9F000000).
 *
 * PRU increments write_idx after each event; Python polls write_idx
 * and drains all new entries between its read_idx and write_idx.
 * Both indices are logical (never wrap); mask with (PRU_RING_DEPTH-1).
 *
 * _pru_prev_iep / _pru_rollover_ns: gcc-pru SBBO always uses ARM
 * physical addresses, so C static variables in PRUDMEM (origin 0x0)
 * would hit boot ROM and be silently dropped.  These IEP rollover-
 * tracking variables are stored here in DDR instead, where SBBO works.
 * Python must not interpret them; they are private to the firmware.
 */
typedef struct __attribute__((packed)) {
    uint32_t          magic;            /* PRU_SHM_MAGIC at startup     */
    volatile uint32_t write_idx;        /* written by PRU, read by ARM  */
    uint32_t          _pad;             /* reserved                     */
    uint32_t          _pru_prev_iep;    /* PRU private: last IEP sample */
    uint64_t          _pru_rollover_ns; /* PRU private: rollover accum  */
    volatile pru_event_t ring[PRU_RING_DEPTH];
} pru_shm_t;

#endif /* SHARED_MEM_H */
