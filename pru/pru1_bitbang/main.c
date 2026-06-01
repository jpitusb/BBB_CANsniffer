/*
 * PRU1 bit-bang CAN receiver — Phase 4 placeholder.
 *
 * Intended to implement a software CAN receiver on P8.46 (pr1_pru1_pru_r31_1)
 * at up to 250 kbit/s, writing decoded frames to a second DDR ring buffer at
 * PRU_SHM_PHYS_ADDR + 0x1000.
 *
 * Not implemented yet.  See project plan Phase 4 for design details.
 */

void main(void)
{
    /* Phase 4: implement bit-bang CAN state machine here */
    while (1)
        ;
}
