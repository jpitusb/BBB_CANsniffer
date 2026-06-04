	.section ".text._start", "ax"
	.global  _start
_start:
	ldi	r2, 0x1E00
	jmp	main
