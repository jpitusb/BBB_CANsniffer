#ifndef FAULT_SHM_H
#define FAULT_SHM_H

#include <stdint.h>

#define FAULT_SHM_MAGIC      0xFA017123U
#define FAULT_SHM_PHYS_ADDR  0x9F000000U
#define FAULT_SHM_SIZE       0x1000U     /* 4 KB */

/* Fault modes written by Python, read by PRU */
#define FAULT_IDLE           0  /* passive — P8.45 held high (recessive) */
#define FAULT_GLITCH         1  /* one short dominant pulse, then idle */
#define FAULT_GLITCH_BURST   2  /* continuous rapid glitches (~5/s) */
#define FAULT_DOMINANT       3  /* bus stuck dominant until mode changed */
#define FAULT_INTERMITTENT   4  /* random glitches at ~1/s */

/*
 * IEP counts at 200 MHz (5 ns/tick):
 *   1 bit at 500 kbit/s = 2000 ns = 400 ticks
 *   "glitch" = below 0.5 bit = < 200 ticks  →  we use 80 ticks (400 ns)
 *   "dominant runaway" trigger on sniffer = > 4000 ticks (20 µs)
 */
#define GLITCH_TICKS         80U     /* 400 ns — invisible to error counters */
#define BURST_INTERVAL_TICKS 8000000U /* 40 ms between burst glitches */
#define INTERMITTENT_TICKS   40000000U/* 200 ms between random glitches */

typedef struct __attribute__((packed)) {
    uint32_t magic;
    volatile uint32_t fault_mode;    /* written by Python, read by PRU */
    volatile uint32_t faults_done;   /* incremented by PRU on each injection */
    uint32_t _pad;
} fault_shm_t;

#endif /* FAULT_SHM_H */
