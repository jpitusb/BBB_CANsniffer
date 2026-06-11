/*
 * AM335x PRU0 linker script for gcc-pru (ELF output for remoteproc).
 *
 * PRU0 local memories:
 *   IRAM  8 KB  instruction RAM  (ARM phys 0x4A334000)
 *   DMEM  8 KB  data RAM         (ARM phys 0x4A300000)
 *
 * ELF PhysAddr (LMA) mapping used by 4.19-ti pru-rproc:
 *   LMA 0x00000..0x01FFF  → PRU0 IRAM  (R E segments)
 *   LMA 0x00000..0x01FFF  → PRU0 DRAM  (RW segments)
 *   pru-rproc distinguishes IRAM/DRAM by PT_LOAD flags: R|E = IRAM, R|W = DRAM.
 *
 * .resource_table must be a PT_LOAD (RW) segment at LMA 0x100 (DRAM offset 0x100)
 * so that 4.19-ti pru-rproc can find it.  Without it Boot fails: -22 (EINVAL).
 *
 * PRUDMEM is placed at virtual 0x10000 (avoids PRUIMEM address overlap in the
 * linker).  AT(0x100) forces LMA 0x100 for .resource_table so remoteproc
 * loads it to PRU DRAM + 0x100.
 *
 * gcc-pru SBBO/LBBO uses ARM physical addresses.  SBBO to virtual-DRAM addresses
 * (0x10000+) resolves to ARM phys 0x10000 (ROM) and is silently dropped.
 * This is intentional: stack/BSS writes are harmless drops.
 * Real data is in DDR at 0x9F000000 via explicit SBBO with OCP enabled.
 */

ENTRY(_start)

MEMORY
{
    PRUIMEM  (X)  : ORIGIN = 0x00000000, LENGTH = 0x00002000  /* PRU0 IRAM */
    PRUDMEM  (RW) : ORIGIN = 0x00010000, LENGTH = 0x00002000  /* PRU0 DRAM (virtual) */
}

SECTIONS
{
    .text : {
        *(.text._start)   /* startup must be first */
        *(.text*)
    } > PRUIMEM

    /*
     * Resource table: must be a LOAD segment for 4.19-ti pru-rproc.
     * AT(0x100) forces LMA = 0x100 so remoteproc places it in PRU DRAM+0x100.
     * VMA is in PRUDMEM (virtual) — PRU code never reads this at runtime.
     */
    /* LMA 0x400: past end of .text (0x268), within PRU DRAM range (8KB).
     * 4.19-ti pru-rproc treats RW segments as DRAM offsets. */
    .resource_table : AT(0x400) { *(.resource_table) } > PRUDMEM

    /* All other data sections: NOLOAD avoids LMA conflicts with .text at 0x0 */
    .rodata (NOLOAD): { *(.rodata*) } > PRUDMEM
    .data   (NOLOAD): { *(.data*) }  > PRUDMEM
    .bss    (NOLOAD): { *(.bss*) *(COMMON) } > PRUDMEM
    .stack  (NOLOAD): { *(.stack) } > PRUDMEM
}
