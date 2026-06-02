#ifndef SHARED_MEM_H
#define SHARED_MEM_H

#include <stdint.h>

#define PRU_SHM_MAGIC        0xCAFE1234U
#define PRU_RING_DEPTH       256U           /* must be a power of 2 */

/*
 * PRUSS Shared RAM (12 KB).
 *
 * Two addresses for the same physical memory:
 *   PRU_SHM_PRU_ADDR  — PRU local data bus address.  PRU accesses
 *     Shared RAM via the internal local bus (no OCP master required).
 *     The OCP master cannot self-target back to PRUSS Shared RAM.
 *   PRU_SHM_ARM_ADDR  — ARM physical address for Python /dev/mem mmap.
 *     PRUSS Shared RAM is a non-cached I/O region; no DTS reservation needed.
 *
 * Our struct is ~4 KB; 12 KB shared RAM is sufficient.
 */
/*
 * Ring buffer in DDR at 0x9F000000 (reserved via DTS, no no-map).
 * SBBO uses ARM physical addresses — DDR is external to PRUSS so
 * there is no OCP self-targeting issue.  ARM accesses via /dev/mem
 * (MAP_SHARED writable, since no-map is absent).
 */
#define PRU_SHM_ARM_ADDR     0x9F000000U
#define PRU_SHM_SIZE         0x2000U        /* 8 KB */

/* PRU event types — also used in Python as PruEventType enum */
#define EVT_SOF              0x01
#define EVT_GLITCH           0x02
#define EVT_DOMINANT_RUNAWAY 0x03

/*
 * Bit-timing thresholds in IEP counts (5 ns/tick at 200 MHz PRU clock).
 * Defaults are for 500 kbit/s (2000 ns/bit).  Recompile pru firmware when
 * changing bus bitrate; Python reads these indirectly via the same header.
 */
#define GLITCH_THRESHOLD_COUNTS  200U   /* 1000 ns = 0.5 bit  */
#define SOF_MAX_COUNTS          4000U   /* 20000 ns = 10 bits */
#define IFS_COUNTS              1200U   /* 6000 ns = 3 bits (Intermission Frame Space) */

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
 * Shared memory header placed at PRU_SHM_PHYS_ADDR.
 * PRU increments write_idx after each entry; Python polls write_idx and reads
 * all entries between its cached read_idx and the new write_idx.
 * Both indices are logical (never wrap); mask with (PRU_RING_DEPTH - 1) for
 * the physical slot.
 */
typedef struct __attribute__((packed)) {
    uint32_t         magic;       /* PRU_SHM_MAGIC; set by firmware at startup */
    volatile uint32_t write_idx;  /* written by PRU, read by ARM                */
    uint32_t         _pad[2];     /* pad header to 16 bytes for alignment        */
    volatile pru_event_t ring[PRU_RING_DEPTH];
} pru_shm_t;

#endif /* SHARED_MEM_H */
