OVERLAYS   = BB-DCAN1-00A0.dtbo
DTC        ?= dtc
DTCFLAGS    = -@ -I dts -O dtb -W no-unit_address_vs_reg
DT_INCLUDE ?= /opt/source/dtb-5.10-ti/include
CPP         = cpp

all: $(OVERLAYS)

%.dtbo: %.dts
	$(CPP) -nostdinc -I$(DT_INCLUDE) -undef -x assembler-with-cpp $< | \
	$(DTC) $(DTCFLAGS) -o $@ -

install: all
	cp $(OVERLAYS) /lib/firmware/

clean:
	rm -f $(OVERLAYS)

.PHONY: all install clean
