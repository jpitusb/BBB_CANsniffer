/*
 * AM335x PRU0 linker script for gcc-pru (ELF output for remoteproc).
 *
 * PRU0 local memories:
 *   IRAM  8 KB  instruction RAM
 *   DMEM  8 KB  data RAM (PRU0 local)
 */

ENTRY(_start)

/*
 * gcc-pru memory model: SBBO/LBBO always resolves as an ARM physical
 * address.  PRUDMEM origin 0x0 means BSS/stack SBBOs hit ARM boot ROM
 * (0x0000-0x1BFFF) and are silently dropped.  This is intentional:
 *   - Stack saves/restores are lost but main() never returns, so
 *     caller-saved register corruption does not matter.
 *   - IEP rollover state and ring buffer data are kept in DDR (shm)
 *     where SBBO to 0x9F000000 works correctly with OCP enabled.
 * Do NOT move PRUDMEM to 0x4A300000 (PRU DRAM ARM physical): OCP
 * self-targeting into the PRUSS subsystem is blocked on AM335x.
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
