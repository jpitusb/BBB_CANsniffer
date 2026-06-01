import can
import pytest

from can_sniffer.error_decoder import ErrorDecoder

_ERR_FLAG = 0x20000000


def make_err(arb: int, data: bytes = b"\x00" * 8) -> can.Message:
    return can.Message(
        arbitration_id=arb,
        data=data,
        is_error_frame=True,
        timestamp=1000.0,
    )


class TestErrorDecoder:
    def setup_method(self):
        self.dec = ErrorDecoder()

    def test_returns_none_for_data_frame(self):
        msg = can.Message(arbitration_id=0x123, data=b"\x01", is_error_frame=False)
        assert self.dec.decode(msg) is None

    def test_ack_error(self):
        err = self.dec.decode(make_err(0x020))
        assert err is not None
        assert err.ack_error is True
        assert err.bus_off is False

    def test_bus_off(self):
        err = self.dec.decode(make_err(0x040))
        assert err is not None
        assert err.bus_off is True

    def test_stuff_error(self):
        # CAN_ERR_PROT (0x008) + data[2] bit 0x04 = stuff error
        data = bytearray(8)
        data[2] = 0x04
        err = self.dec.decode(make_err(0x008, bytes(data)))
        assert err is not None
        assert err.stuff_error is True
        assert err.bit_error is False

    def test_tec_rec_extracted(self):
        data = bytearray(8)
        data[6] = 112  # TEC
        data[7] = 32   # REC
        err = self.dec.decode(make_err(0x004, bytes(data)))  # CAN_ERR_CRTL
        assert err is not None
        assert err.tec == 112
        assert err.rec == 32
