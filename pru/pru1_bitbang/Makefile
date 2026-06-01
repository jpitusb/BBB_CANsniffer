TARGET     = am335x-pru1-fw
SRCS       = main.c
PRUSS_SDK  ?= /usr/lib/ti/pru-software-support-package
CLPRU      ?= clpru
LNKPRU     ?= lnkpru
LINKER_CMD  = $(PRUSS_SDK)/examples/am335x/PRU_RPMsg_Echo_Interrupt1/AM335x_PRU.cmd

CFLAGS  = -v3 -O2 --endian=little --hardware_mac=on \
          -I$(PRUSS_SDK)/include \
          -I$(PRUSS_SDK)/include/am335x \
          -DCORE_1

LFLAGS  = -i$(PRUSS_SDK)/lib \
          -llibc.a

all: $(TARGET)

$(TARGET): $(SRCS:.c=.obj)
	$(LNKPRU) $(LFLAGS) -o $@ $^ $(LINKER_CMD)

%.obj: %.c
	$(CLPRU) $(CFLAGS) -c $< -o $@

clean:
	rm -f *.obj *.map $(TARGET)

.PHONY: all clean
