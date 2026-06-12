/*
 * PRU0 CAN RX timestamp firmware for AM335x BeagleBone Black.
 *
 * Uses the PRUSS INTC (Interrupt Controller) to detect CAN dominant edges.
 * P8.08 (GPIO2_3) is configured as a GPIO with falling-edge interrupt.
 * GPIO2 bank-A interrupt → PRUSS system event 24 → channel 0 →
 * host interrupt 0 → R31[16].
 *
 * R31[29:16] are PRUSS INTC host-interrupt bits.  They are hardwired to
 * the INTC output and are completely independent of GPCFG0 (GPI mux).
 * This bypasses the GPCFG0 bit-25 lock that permanently disables R31[15:0]
 * on this BBB/kernel combination.
 *
 * When CAN RX goes dominant (falling edge on P8.08 via 1 kΩ to P9.24),
 * R31[16] goes high.  The PRU reads IEP immediately (~25 ns jitter vs the
 * physical edge), writes an EVT_SOF event to the ring buffer, clears the
 * GPIO interrupt, waits a 100 µs blind period to skip within-frame bits,
 * then re-arms for the next SOF.
 *
 * IEP timer: 5 ns/tick at 200 MHz, rolls over every ~21.47 s.
 * Rollover is tracked in DDR (shm->_pru_*) since gcc-pru SBBO to
 * PRUDMEM (origin 0x0) hits boot ROM and is silently dropped.
 */

#include <stdint.h>
#include "shared_mem.h"
#include "resource_table.h"

/* R31 = PRU input register: bits [15:0] = GPI (locked by GPCFG0 on this board),
 * bits [29:16] = PRUSS INTC host interrupt status (NOT affected by GPCFG0). */
register uint32_t __R31 __asm__("r31");

/* GPIO2 registers (ARM physical 0x481AC000) — OMAP4-compatible offsets ---- */
#define GPIO2_BASE              0x481AC000u
#define GPIO_IRQSTATUS_0        0x02Cu   /* write 1 to clear bank-A status */
/* GPIO2_3 = P8.08 (moved from P8.46/GPIO2_7, a SYSBOOT pin that blocked boot).
 * Same GPIO2 bank, so the PRUSS INTC routing (sysevt 24) is unchanged. */
#define GPIO2_PIN_MASK          (1u << 3)

/* PRUSS INTC system event for GPIO2 bank-A --------------------------------- */
#define INTC_GPIO2A_EVENT       24u

/*
 * IEP tick rate: DEFAULT_INC=5, PRU clock=200 MHz → 5 ticks/cycle ÷ 5 ns/cycle
 * = 1 tick per 1 ns (1 GHz effective).  iep_to_ns multiplies by 1.
 * Rollover: 2^32 ticks × 1 ns = 4 294 967 296 ns ≈ 4.29 s.
 *
 * Blind period after each detected SOF: 100 µs = 100 000 IEP ticks.
 * Skips within-frame data-bit edges before re-arming for the next SOF.
 */
#define BLIND_COUNTS            10000000u /* 10 ms → ~100 events/sec max.
                                           * Caps PRU-driven Python fan-out on
                                           * the single-core ARM. On an idle bus
                                           * P8.46 re-asserts every blind period,
                                           * so this directly sets the phantom-
                                           * event ceiling. Lower it (toward
                                           * 1000000u = 1 ms) only if you need
                                           * higher SOF coverage on a busy bus
                                           * and have CPU headroom. */

/* ── OCP / IEP helpers (same as before) ─────────────────────────────────── */

static inline void ocp_enable(void)
{
    __asm__ volatile (
        "lbco r0, 4, 4, 4 \n"
        "clr  r0, r0, 4   \n"
        "sbco r0, 4, 4, 4 \n"
        ::: "r0"
    );
}

static inline void iep_enable_and_reset(void)
{
    __asm__ volatile (
        "lbco r0, 26, 0x00, 4 \n"
        "set  r0, r0, 0        \n"
        "sbco r0, 26, 0x00, 4 \n"
        "ldi  r0, 0            \n"
        "sbco r0, 26, 0x0C, 4 \n"
        ::: "r0"
    );
}

static inline uint32_t iep_cnt_read(void)
{
    uint32_t cnt;
    __asm__ volatile (
        "lbco r0, 26, 0x0C, 4 \n"
        "mov  %0, r0           \n"
        : "=r"(cnt) : : "r0"
    );
    return cnt;
}

/* ── PRUSS INTC configuration (C0 = PRUSS INTC internal bus) ─────────────
 *
 * Route GPIO2 bank-A interrupt (system event 24) to R31[16]:
 *
 *   System event 24  →  channel 0  →  host interrupt 0  →  R31[16]
 *
 * PRUSS INTC register offsets (from PRUSS+0x20000, accessed via C0):
 *   0x04  CR      - control (enable bit 0)
 *   0x10  GER     - global enable (bit 0)
 *   0x24  SICR    - Status Index Clear (write event number)
 *   0x28  EISR    - Enable Index Set  (write event number)
 *   0x34  HIEISR  - Host Interrupt Enable Index Set (write host int number)
 *   0x40C CMR3    - Channel Map 3, events 24-31 (4 bits each)
 *   0x800 HMR0    - Host Interrupt Map 0, channels 0-7 (4 bits each)
 *   0xD00 SIPR0   - Polarity 0, events 0-31 (1 bit: 0=active-low, 1=active-hi)
 *   0xD80 SITR0   - Type 0, events 0-31 (1 bit: 0=pulse, 1=level)
 */
/*
 * PRUSS INTC initialisation — PRU side.
 *
 * LBCO/SBCO (constant-table access, internal PRUSS bus) has an 8-bit
 * byte-offset limit (0–255).  SIPR0 (0xD00), SITR0 (0xD80), CMR3 (0x40C)
 * and HMR0 (0x800) exceed this limit, so they are configured from the ARM
 * side in setup_pru.sh before the PRU starts.
 *
 * Power-on reset defaults (SPRUHH7B Table 4-x):
 *   CMR*  = 0  → all events map to channel 0           ✓ already correct
 *   HMR*  = 0  → channel 0 maps to host interrupt 0    ✓ already correct
 *   SIPR0 = 0  → active LOW  (setup_pru.sh sets bit 24 = active HIGH)
 *   SITR0 = 0  → pulse mode  (setup_pru.sh sets bit 24 = level mode)
 *
 * Here we only need the registers within the 0-255 offset window:
 *   GER   (0x10) — global enable
 *   EISR  (0x28) — enable event 24
 *   HIEISR(0x34) — enable host interrupt 0
 */
static inline void intc_init(void)
{
    /* All large-offset INTC registers (SIPR0, SITR0, CMR3, HMR0, HIER)
     * are configured from the ARM in setup_pru.sh before PRU start.
     * Here we only touch the registers within LBCO's 8-bit offset window: */

    /* Enable system event 24 (EISR at offset 0x28) */
    __asm__ volatile("ldi r0, 24\n sbco r0, 0, 0x28, 4\n" ::: "r0");

    /* Enable host interrupt 0 (HIEISR at offset 0x34) */
    __asm__ volatile("ldi r0,  0\n sbco r0, 0, 0x34, 4\n" ::: "r0");

    /* Enable global interrupt (GER at offset 0x10) */
    __asm__ volatile("ldi r0,  1\n sbco r0, 0, 0x10, 4\n" ::: "r0");

    /* Enable INTC module via CR register (offset 0x04, bit 0) */
    __asm__ volatile("ldi r0,  1\n sbco r0, 0, 0x04, 4\n" ::: "r0");
}

/* ── Ring buffer ─────────────────────────────────────────────────────────── */

#define shm  ((volatile pru_shm_t *)PRU_SHM_ARM_ADDR)

#define IEP_PERIOD_NS  4294967296ULL   /* 2^32 ticks × 1 ns/tick */

static uint64_t iep_to_ns(uint32_t count)
{
    if (count < shm->_pru_prev_iep)
        shm->_pru_rollover_ns += IEP_PERIOD_NS;
    shm->_pru_prev_iep = count;
    return shm->_pru_rollover_ns + (uint64_t)count * 1ULL;  /* 1 ns/tick */
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
    __asm__ volatile ("" ::: "memory");
    shm->write_idx++;
}

/* ── GPIO2 interrupt clear ───────────────────────────────────────────────── */

static inline void gpio2_irq_clear(void)
{
    /* Clear the GPIO2_3 interrupt status via SBBO to ARM physical 0x481AC03C.
     * Must be done before clearing PRUSS INTC event so the GPIO line
     * de-asserts and the level-triggered event can fire again next time. */
    volatile uint32_t *clr =
        (volatile uint32_t *)(GPIO2_BASE + GPIO_IRQSTATUS_0);
    *clr = GPIO2_PIN_MASK;
}

static inline void intc_event_clear(void)
{
    /* Clear PRUSS INTC system event 24 (SICR at C0+0x24) */
    __asm__ volatile("ldi r0, 24\n sbco r0, 0, 0x24, 4\n" ::: "r0");
}

/* ── Entry point ─────────────────────────────────────────────────────────── */

void main(void)
{
    uint32_t iep_snap;
    uint64_t t_fall_ns;
    uint16_t seq = 0;

    ocp_enable();
    iep_enable_and_reset();
    intc_init();

    /* Clear any GPIO interrupt that accumulated before we started */
    gpio2_irq_clear();
    intc_event_clear();

    shm->magic            = PRU_SHM_MAGIC;
    shm->write_idx        = 0;
    shm->_pru_prev_iep    = 0;
    shm->_pru_rollover_ns = 0;


    while (1) {
        /*
         * WAIT: spin until R31[16] goes high.
         * R31[16] = PRUSS INTC host interrupt 0 = GPIO2 bank-A interrupt
         * = falling edge on P8.08 = CAN dominant (SOF start).
         * This bit is independent of GPCFG0 (GPI mux lock).
         */
        /* Wait for host interrupt 0 on R31[30] = GPIO2 bank-A interrupt */
        while (!(__R31 & (1u << 30)))
            ;

        /* Snapshot IEP the instant the interrupt fires (~25 ns jitter) */
        iep_snap  = iep_cnt_read();
        t_fall_ns = iep_to_ns(iep_snap);

        /*
         * Clear in correct order:
         *  1. GPIO2_7 IRQSTATUS first (de-asserts the system event level)
         *  2. Then clear PRUSS INTC event (so it can re-arm for next edge)
         */
        gpio2_irq_clear();
        intc_event_clear();

        /* Write SOF timestamp to ring buffer */
        write_event(EVT_SOF, seq++, t_fall_ns, 0u);

        /*
         * Blind period: wait BLIND_COUNTS IEP ticks (100 µs) so we skip
         * within-frame data-bit edges.  The next detection will be the
         * SOF of the following CAN frame.
         */
        while ((iep_cnt_read() - iep_snap) < BLIND_COUNTS)
            ;

        /* Drain any edges that accumulated during the blind period */
        gpio2_irq_clear();
        intc_event_clear();
    }
}
