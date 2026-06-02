/*
 * AM335x PRU0 linker script for gcc-pru (ELF output for remoteproc).
 *
 * PRU0 local memories:
 *   IRAM  8 KB  instruction RAM
 *   DMEM  8 KB  data RAM (PRU0 local)
 */

ENTRY(_start)

/*
 * PRUDMEM origin is the ARM physical address of PRU0 data RAM (0x4A300000).
 * gcc-pru compiles SBBO/LBBO as OCP master accesses that use ARM physical
 * addresses — NOT the PRU local data bus address space (0x0000-0x1FFF).
 * Setting the origin here ensures the compiler generates correct ARM
 * physical addresses for all data accesses (stack, BSS variables, etc.)
 * so they land in PRU0 DRAM rather than ARM boot ROM.
 */
MEMORY
{
    PRUIMEM (X)  : ORIGIN = 0x00000000, LENGTH = 0x00002000
    PRUDMEM (RW) : ORIGIN = 0x00000000, LENGTH = 0x00002000
}

SECTIONS
{
    .text : {
        *(.text._start)   /* startup must be first: sets sp before main */
        *(.text*)
    } > PRUIMEM

    .resource_table : { *(.resource_table) } > PRUDMEM
    .rodata         : { *(.rodata*) } > PRUDMEM
    .data           : { *(.data*) }  > PRUDMEM
    .bss   (NOLOAD) : { *(.bss*) *(COMMON) } > PRUDMEM
    .stack (NOLOAD) : { *(.stack) } > PRUDMEM
}
