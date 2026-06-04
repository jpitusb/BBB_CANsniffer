ENTRY(_start)
MEMORY
{
    PRUIMEM (X)  : ORIGIN = 0x00000000, LENGTH = 0x00002000
    PRUDMEM (RW) : ORIGIN = 0x00000000, LENGTH = 0x00002000
}
SECTIONS
{
    .text : { *(.text._start) *(.text*) } > PRUIMEM
    .resource_table : { *(.resource_table) } > PRUDMEM
    .bss  (NOLOAD) : { *(.bss*) *(COMMON) } > PRUDMEM
    .stack (NOLOAD) : { *(.stack) } > PRUDMEM
}
