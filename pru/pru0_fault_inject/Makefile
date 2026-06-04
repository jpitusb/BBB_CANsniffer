TARGET     = am335x-pru0-fault-inject
SRCS       = startup.S main.c
PRUSS_SDK  ?= /usr/lib/ti/pru-software-support-package-v6.3
PRU_GCC    ?= pru-gcc
LINKER_CMD  = AM335x_PRU0.ld

CFLAGS  = -Os -mmcu=am335x.pru0 \
          -I$(PRUSS_SDK)/include \
          -I$(PRUSS_SDK)/include/am335x \
          -DCORE_0 -Wall -Wno-incompatible-pointer-types

LFLAGS  = -mmcu=am335x.pru0 -nostartfiles -T$(LINKER_CMD)

all: $(TARGET)

$(TARGET): $(SRCS) $(LINKER_CMD)
	$(PRU_GCC) $(CFLAGS) $(LFLAGS) -o $@ $(SRCS)

deploy: $(TARGET)
	sudo cp $(TARGET) /lib/firmware/
	echo stop | sudo tee /sys/class/remoteproc/remoteproc1/state || true
	echo start | sudo tee /sys/class/remoteproc/remoteproc1/state

clean:
	rm -f $(TARGET)

.PHONY: all deploy clean
