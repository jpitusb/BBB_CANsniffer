/*
 * PRU0 minimal startup for gcc-pru.
 *
 * After PRU reset all registers are 0, including R2 (stack pointer).
 * The first function prologue does "sub sp, sp, N" which wraps sp to
 * 0xFFFFFFxx — an unmapped ARM address.  The subsequent "sbbo" to that
 * address stalls the OCP master before main ever gets to enable it.
 *
 * Set sp to the top of PRU0 local DRAM (0x00001E00) so function prologues
 * write to valid local memory that is reachable without the OCP master.
 * PRU0 DRAM is 8 KB (0x00000000–0x00001FFF); leave 0x200 bytes for stack.
 */

	.section ".text._start", "ax"
	.global  _start

_start:
	/*
	 * Set stack pointer to ARM physical 0x4A301E00 (top 512 bytes of PRU0
	 * data RAM, leaving room below for BSS).  SBBO uses ARM physical
	 * addresses, so sp must be in PRU DRAM's ARM range (0x4A300000-0x4A301FFF).
	 * LDI only handles 16-bit immediates; load the two halves separately.
	 */
	ldi	r2, 0x1E00	/* sp = 0x00001E00 (SBBO writes to boot ROM, silently dropped) */
	jmp	main
