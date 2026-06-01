OVERLAYS = BB-DCAN0-00A0.dtbo BB-PRU0-CAN-TS-00A0.dtbo
DTC      ?= dtc
DTCFLAGS  = -@ -I dts -O dtb -W no-unit_address_vs_reg

all: $(OVERLAYS)

%.dtbo: %.dts
	$(DTC) $(DTCFLAGS) -o $@ $<

install: all
	cp $(OVERLAYS) /lib/firmware/

clean:
	rm -f $(OVERLAYS)

.PHONY: all install clean
