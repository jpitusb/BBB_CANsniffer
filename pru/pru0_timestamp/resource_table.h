#ifndef RESOURCE_TABLE_H
#define RESOURCE_TABLE_H

/*
 * Minimal remoteproc resource table for PRU0.
 * No carveout needed: shared memory uses PRUSS Shared RAM (0x00010000/0x4A310000)
 * which is always available as PRUSS I/O — no kernel reservation required.
 */

#include <rsc_types.h>

struct pru0_resource_table {
    struct resource_table base;
};

__attribute__((section(".resource_table"), used))
const struct pru0_resource_table pru_remoteproc_ResourceTable = {
    .base = {
        .ver      = 1,
        .num      = 0,
        .reserved = {0, 0},
    },
};

#endif /* RESOURCE_TABLE_H */
