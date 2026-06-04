#ifndef RESOURCE_TABLE_H
#define RESOURCE_TABLE_H
#include <rsc_types.h>

struct pru0_resource_table {
    struct resource_table base;
};

__attribute__((section(".resource_table"), used))
const struct pru0_resource_table pru_remoteproc_ResourceTable = {
    .base = { .ver = 1, .num = 0, .reserved = {0, 0} },
};

#endif
