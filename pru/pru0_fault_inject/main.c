/*
 * PRU0 CAN bus fault injector for BeagleBone Black #2.
 *
 * P8.45 (pr1_pru0_pru_r30_0, mux mode 5) is wired to the TXD line of a
 * SN65HVD230 transceiver that is on the same CAN bus as BBB #1.
 *
 * Fault injection:
 *   PRU R30[0] = 1  →  TXD HIGH  →  bus recessive (passive, normal)
 *   PRU R30[0] = 0  →  TXD LOW   →  bus dominant  (injects fault)
 *
 * The PRU reads fault_mode from the DDR shared memory ring set by Python,
 * then drives the output accordingly.  Python writes the mode; the PRU
 * just acts on it — no constant table or OCP tricks needed for mode reads
 * because the fault shm is in DDR and OCP is pre-enabled from the ARM.
 */

#include <stdint.h>
#include "shared_mem.h"

#define FAULT_BIT  (1u << 0)   /* R30 bit 0 = P8.45 */

register uint32_t __R30 __asm__("r30");

static volatile fault_shm_t *const shm = (fault_shm_t *)FAULT_SHM_PHYS_ADDR;

/* Enable OCP master so PRU can read DDR */
static inline void ocp_enable(void)
{
    __asm__ volatile (
        "lbco r0, 4, 4, 4 \n"
        "clr  r0, r0, 4   \n"
        "sbco r0, 4, 4, 4 \n"
        ::: "r0"
    );
}

/* Busy-wait for `ticks` IEP counts using constant table C26 */
static inline void wait_ticks(uint32_t ticks)
{
    uint32_t start, now;
    __asm__ volatile ("lbco %0, 26, 0x0C, 4 \n" : "=r" (start));
    do {
        __asm__ volatile ("lbco %0, 26, 0x0C, 4 \n" : "=r" (now));
    } while ((now - start) < ticks);
}

static inline void inject_glitch(void)
{
    __R30 &= ~FAULT_BIT;          /* dominant */
    wait_ticks(GLITCH_TICKS);
    __R30 |=  FAULT_BIT;          /* recessive */
    shm->faults_done++;
}

static inline void inject_dominant(void)
{
    __R30 &= ~FAULT_BIT;          /* hold dominant — caller decides duration */
}

static inline void idle(void)
{
    __R30 |= FAULT_BIT;           /* recessive */
}

void main(void)
{
    ocp_enable();

    /* Enable IEP counter via C26 */
    __asm__ volatile (
        "lbco r0, 26, 0x00, 4 \n"
        "set  r0, r0, 0        \n"
        "sbco r0, 26, 0x00, 4 \n"
        "ldi  r0, 0            \n"
        "sbco r0, 26, 0x0C, 4 \n"
        ::: "r0"
    );

    shm->magic      = FAULT_SHM_MAGIC;
    shm->fault_mode = FAULT_IDLE;
    shm->faults_done = 0;

    idle();

    while (1) {
        uint32_t mode = shm->fault_mode;

        switch (mode) {
        case FAULT_IDLE:
            idle();
            wait_ticks(40000);   /* ~200 µs polling interval */
            break;

        case FAULT_GLITCH:
            inject_glitch();
            shm->fault_mode = FAULT_IDLE;   /* one-shot */
            wait_ticks(40000);
            break;

        case FAULT_GLITCH_BURST:
            inject_glitch();
            idle();
            wait_ticks(BURST_INTERVAL_TICKS);
            break;

        case FAULT_DOMINANT:
            inject_dominant();
            /* Hold dominant until mode changes */
            while (shm->fault_mode == FAULT_DOMINANT)
                ;
            idle();
            shm->faults_done++;
            break;

        case FAULT_INTERMITTENT:
            inject_glitch();
            idle();
            wait_ticks(INTERMITTENT_TICKS);
            break;

        default:
            idle();
            wait_ticks(40000);
            break;
        }
    }
}
