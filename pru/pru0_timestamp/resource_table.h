#ifndef RESOURCE_TABLE_H
#define RESOURCE_TABLE_H

/*
 * TI remoteproc resource table for PRU0 on AM335x.
 * Requires pru-software-support-package headers (rsc_types.h).
 * Single CARVEOUT entry reserves the DDR region used as pru_shm_t.
 */

#include <stddef.h>
#include <rsc_types.h>

#include "shared_mem.h"

struct pru0_resource_table {
    struct resource_table base;
    uint32_t              offsets[1];
    struct fw_rsc_carveout carveout;
};

__attribute__((section(".resource_table"), used))
const struct pru0_resource_table pru_remoteproc_ResourceTable = {
    .base = {
        .ver      = 1,
        .num      = 1,
        .reserved = {0, 0},
    },
    .offsets = {
        offsetof(struct pru0_resource_table, carveout),
    },
    .carveout = {
        .type     = TYPE_CARVEOUT,
        .da       = 0,                  /* not mapped into PRU local address space */
        .pa       = PRU_SHM_PHYS_ADDR,
        .len      = PRU_SHM_SIZE,
        .flags    = 0,
        .reserved = 0,
        .name     = "pru_shm",
    },
};

#endif /* RESOURCE_TABLE_H */
