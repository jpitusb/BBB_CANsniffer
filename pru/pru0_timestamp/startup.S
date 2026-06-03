/*
 * PRU0 minimal startup for gcc-pru.
 *
 * After PRU reset all registers are 0, including R2 (stack pointer).
 * Without a valid sp, the first function prologue wraps sp to 0xFFFFFFxx
 * (an OCP master address in unmapped ARM space), which stalls the PRU.
 *
 * We set sp = 0x1E00.  Because PRUDMEM origin is 0x0, this is ARM
 * physical address 0x1E00 (boot ROM).  Stack saves hit boot ROM and are
 * silently dropped — which is acceptable because:
 *   1. main() never returns, so the corrupted saved return address
 *      is never used.
 *   2. Any C variables that matter (IEP state, ring buffer data) are
 *      stored in DDR (shm) at 0x9F000000, not on the stack.
 *
 * Do NOT set sp to a PRUSS address (e.g., 0x4A301E00) — OCP
 * self-targeting into the PRUSS subsystem is blocked on AM335x.
 */

	.section ".text._start", "ax"
	.global  _start

_start:
	ldi	r2, 0x1E00
	jmp	main
