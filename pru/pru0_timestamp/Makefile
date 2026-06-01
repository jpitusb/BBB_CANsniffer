TARGET     = am335x-pru0-fw
SRCS       = main.c
PRUSS_SDK  ?= /usr/lib/ti/pru-software-support-package
CLPRU      ?= clpru
LNKPRU     ?= lnkpru
LINKER_CMD  = $(PRUSS_SDK)/examples/am335x/PRU_RPMsg_Echo_Interrupt0/AM335x_PRU.cmd

CFLAGS  = -v3 -O2 --endian=little --hardware_mac=on \
          -I$(PRUSS_SDK)/include \
          -I$(PRUSS_SDK)/include/am335x \
          -DCORE_0

LFLAGS  = -i$(PRUSS_SDK)/lib \
          -llibc.a

all: $(TARGET)

$(TARGET): $(SRCS:.c=.obj)
	$(LNKPRU) $(LFLAGS) -o $@ $^ $(LINKER_CMD)

%.obj: %.c
	$(CLPRU) $(CFLAGS) -c $< -o $@

deploy: $(TARGET)
	scp $(TARGET) $(BBB_USER)@$(BBB_HOST):/lib/firmware/$(TARGET)
	ssh $(BBB_USER)@$(BBB_HOST) \
	    "echo stop > /sys/class/remoteproc/remoteproc1/state; \
	     echo start > /sys/class/remoteproc/remoteproc1/state"

clean:
	rm -f *.obj *.map $(TARGET)

.PHONY: all deploy clean
