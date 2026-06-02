/*
 * AM335x PRU0 linker script for gcc-pru (ELF output for remoteproc).
 *
 * PRU0 local memories:
 *   IRAM  8 KB  instruction RAM
 *   DMEM  8 KB  data RAM (PRU0 local)
 */

ENTRY(main)

MEMORY
{
    PRUIMEM (X)  : ORIGIN = 0x00000000, LENGTH = 0x00002000
    PRUDMEM (RW) : ORIGIN = 0x00000000, LENGTH = 0x00002000
}

SECTIONS
{
    .text           : { *(.text*) }  > PRUIMEM

    .resource_table : { *(.resource_table) } > PRUDMEM
    .rodata         : { *(.rodata*) } > PRUDMEM
    .data           : { *(.data*) }  > PRUDMEM
    .bss  (NOLOAD)  : { *(.bss*) *(COMMON) } > PRUDMEM
}
