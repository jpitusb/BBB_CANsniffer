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
 * IEP access via constant table C26 (PRUSS IEP, internal routing).
 * SBBO/LBBO to 0x4A32E000 would use OCP self-targeting (blocked).
 * LBCO/SBCO via C26 uses the PRUSS internal bus — always accessible.
 *
 * IEP register offsets from its base: TMR_GLB_CFG=0x00, TMR_CNT=0x0C
 */
static inline void iep_enable_and_reset(void)
{
    __asm__ volatile (
        "lbco r0, 26, 0x00, 4 \n"  /* read TMR_GLB_CFG */
        "set  r0, r0, 0        \n"  /* set CNT_ENABLE (bit 0) */
        "sbco r0, 26, 0x00, 4 \n"  /* write TMR_GLB_CFG */
        "ldi  r0, 0            \n"
        "sbco r0, 26, 0x0C, 4 \n"  /* reset TMR_CNT = 0 */
        ::: "r0"
    );
}

static inline uint32_t iep_cnt_read(void)
{
    uint32_t cnt;
    __asm__ volatile (
        "lbco r0, 26, 0x0C, 4 \n"
        "mov  %0, r0           \n"
        : "=r" (cnt)
        :
        : "r0"
    );
    return cnt;
}

static volatile pru_shm_t *const shm = (pru_shm_t *)PRU_SHM_ARM_ADDR;

/* IEP period: 2^32 ticks × 5 ns = 21,474,836,480 ns */
#define IEP_PERIOD_NS  21474836480ULL

/*
 * Convert a raw IEP 32-bit count to monotonic nanoseconds.
 * Must be called on every sample so rollovers are not missed.
 *
 * Rollover state is stored in the DDR ring buffer header (shm->_pru_*)
 * rather than in C static variables — gcc-pru SBBO uses ARM physical
 * addresses, so statics at PRUDMEM origin 0x0 would land in boot ROM
 * and be silently dropped.
 */
static uint64_t iep_to_ns(uint32_t count)
{
    if (count < shm->_pru_prev_iep)
        shm->_pru_rollover_ns += IEP_PERIOD_NS;
    shm->_pru_prev_iep = count;
    return shm->_pru_rollover_ns + (uint64_t)count * 5ULL;
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

    ocp_enable();                /* best-effort; ARM pre-enables OCP before start */
    iep_enable_and_reset();     /* uses SBCO/C26, always works regardless of OCP */

    shm->magic            = PRU_SHM_MAGIC;
    shm->write_idx        = 0;
    shm->_pru_prev_iep    = 0;
    shm->_pru_rollover_ns = 0;

    while (1) {
        /* IDLE: spin until CAN RX goes dominant (low) */
        while (__R31 & CAN_RX_BIT)
            ;

        iep_fall  = iep_cnt_read();
        t_fall_ns = iep_to_ns(iep_fall);

        /* MEASURE_PULSE: spin while dominant, classify on recessive transition */
        while (!(__R31 & CAN_RX_BIT)) {
            iep_now      = iep_cnt_read();
            pulse_counts = iep_now - iep_fall;   /* unsigned wrap is intentional */
            if (pulse_counts >= SOF_MAX_COUNTS) {
                iep_to_ns(iep_now);   /* keep rollover counter current */
                write_event(EVT_DOMINANT_RUNAWAY, seq++,
                            t_fall_ns, pulse_counts * 5u);
                goto wait_idle;
            }
        }

        pulse_counts = iep_cnt_read() - iep_fall;
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
