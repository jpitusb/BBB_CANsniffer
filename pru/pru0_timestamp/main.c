/*
 * PRU0 CAN RX timestamp firmware for AM335x BeagleBone Black.
 *
 * Monitors the CAN RX shadow GPIO (P8.45 → R31 bit 0), classifies each
 * dominant phase as SOF, GLITCH, or DOMINANT_RUNAWAY, and writes a 16-byte
 * event into the DDR ring buffer at PRU_SHM_PHYS_ADDR.
 *
 * IEP timer provides 5 ns resolution (200 MHz PRU clock).  Rollover every
 * 2^32 × 5 ns = ~21.47 s is tracked in firmware; t_fall_ns in each event is
 * monotonically increasing nanoseconds since PRU start.
 *
 * Python reads the ring buffer via mmap(/dev/mem) and adds epoch_offset_ns
 * to convert to Unix nanoseconds.
 */

#include <stdint.h>

#include "shared_mem.h"
#include "resource_table.h"

/* PRU0 R31 bit 0 = P8.45 (pr1_pru0_pru_r31_0) */
#define CAN_RX_BIT  (1u << 0)

/*
 * gcc-pru named register variables for the PRU I/O ports.
 * R30 = output, R31 = input (GPI).
 */
register uint32_t __R30 __asm__("r30");
register uint32_t __R31 __asm__("r31");

/*
 * Enable the PRU OCP master port by clearing STANDBY_INIT (bit 4) in
 * PRUSS_CFG.SYSCFG (Constant Table C4, offset 0x04).
 *
 * A regular C pointer to 0x00026004 cannot work here: that address is
 * accessed through the OCP master, which is exactly what we're trying to
 * enable.  Instead we must use LBCO/SBCO which address via the constant
 * table entry directly, bypassing the OCP master entirely.
 */
static inline void ocp_enable(void)
{
    /* Constant table entry index 4 (C4) = PRUSS_CFG base; SYSCFG at offset 4 */
    __asm__ volatile (
        "lbco r0, 4, 4, 4 \n"   /* read SYSCFG */
        "clr  r0, r0, 4   \n"   /* clear STANDBY_INIT (bit 4) */
        "sbco r0, 4, 4, 4 \n"   /* write back */
        ::: "r0"
    );
}

/*
 * IEP registers via ARM physical address (gcc-pru uses LBBO/SBBO which
 * address the ARM physical bus, not the PRU constant table space).
 * AM335x TRM: PRUSS base 0x4A300000, IEP offset 0x2E000.
 * Note: 0x0002E000 is the constant-table (C26) offset used by clpru's
 * LBCO/SBCO — do NOT use that address here with gcc-pru + LBBO/SBBO.
 */
#define IEP_TMR_GLB_CFG  (*(volatile uint32_t *)0x4a32e000u)
#define IEP_TMR_CNT      (*(volatile uint32_t *)0x4a32e00cu)

/*
 * The DDR carveout physical address is accessed directly from PRU via the
 * L3/EMIF slow-path.  On AM335x this is safe but adds ~100 ns per write;
 * acceptable because we write at most once per CAN frame (~25 µs at 500 kbit/s).
 */
static volatile pru_shm_t *const shm = (pru_shm_t *)PRU_SHM_PRU_ADDR;

/* IEP rollover tracking — updated in iep_to_ns() below */
static uint32_t _prev_iep = 0;
static uint64_t _rollover_ns = 0;

/* IEP period: 2^32 ticks × 5 ns = 21,474,836,480 ns */
#define IEP_PERIOD_NS  21474836480ULL

static inline uint32_t iep_read(void)
{
    return IEP_TMR_CNT;
}

/*
 * Convert a raw IEP 32-bit count to monotonic nanoseconds.
 * Must be called on every sample so rollovers are not missed.
 */
static uint64_t iep_to_ns(uint32_t count)
{
    if (count < _prev_iep)
        _rollover_ns += IEP_PERIOD_NS;
    _prev_iep = count;
    return _rollover_ns + (uint64_t)count * 5ULL;
}

static void write_event(uint8_t type, uint16_t seq,
                        uint64_t t_fall_ns, uint32_t pulse_ns)
{
    uint32_t idx = shm->write_idx & (PRU_RING_DEPTH - 1u);
    volatile pru_event_t *e = &shm->ring[idx];

    e->type      = type;
    e->flags     = 0;
    e->seq       = seq;
    e->t_fall_ns = t_fall_ns;
    e->pulse_ns  = pulse_ns;

    /* Ensure all fields are visible before the index bump that signals Python */
    __asm__ volatile ("" ::: "memory");
    shm->write_idx++;
}

void main(void)
{
    uint32_t iep_fall, iep_now;
    uint64_t t_fall_ns;
    uint32_t pulse_counts;
    uint16_t seq = 0;
    uint32_t stability;

    /* Debug sentinels in PRU local DRAM (no OCP needed, ARM reads at 0x4A300100+) */
    ((volatile uint32_t *)0x100u)[0] = 0x11111111u;  /* reached main */

    ocp_enable();
    ((volatile uint32_t *)0x100u)[1] = 0x22222222u;  /* OCP enabled */

    /* Enable IEP global counter (bit 0 of TMR_GLB_CFG) */
    IEP_TMR_GLB_CFG |= 1u;
    IEP_TMR_CNT = 0;
    ((volatile uint32_t *)0x100u)[2] = 0x33333333u;  /* IEP configured */

    shm->magic     = PRU_SHM_MAGIC;
    shm->write_idx = 0;
    ((volatile uint32_t *)0x100u)[3] = 0x44444444u;  /* shm written */

    while (1) {
        /* IDLE: spin until CAN RX goes dominant (low) */
        while (__R31 & CAN_RX_BIT)
            ;

        iep_fall  = iep_read();
        t_fall_ns = iep_to_ns(iep_fall);

        /* MEASURE_PULSE: spin while dominant, classify on recessive transition */
        while (!(__R31 & CAN_RX_BIT)) {
            iep_now      = iep_read();
            pulse_counts = iep_now - iep_fall;   /* unsigned wrap is intentional */
            if (pulse_counts >= SOF_MAX_COUNTS) {
                iep_to_ns(iep_now);   /* keep rollover counter current */
                write_event(EVT_DOMINANT_RUNAWAY, seq++,
                            t_fall_ns, pulse_counts * 5u);
                goto wait_idle;
            }
        }

        pulse_counts = iep_read() - iep_fall;
        if (pulse_counts < GLITCH_THRESHOLD_COUNTS)
            write_event(EVT_GLITCH, seq++, t_fall_ns, pulse_counts * 5u);
        else
            write_event(EVT_SOF, seq++, t_fall_ns, 0u);

wait_idle:
        /* WAIT_BUS_IDLE: require IFS_COUNTS consecutive recessive ticks */
        stability = 0;
        while (stability < IFS_COUNTS) {
            if (__R31 & CAN_RX_BIT)
                stability++;
            else
                stability = 0;
        }
    }
}
