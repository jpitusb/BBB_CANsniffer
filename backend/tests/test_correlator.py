import can

from can_sniffer.correlator import Correlator


def frame(arb_id: int = 0x123, ts: float = 0.0, data: bytes = b"\xDE\xAD",
          extended: bool = False) -> can.Message:
    return can.Message(arbitration_id=arb_id, data=data, timestamp=ts,
                       is_extended_id=extended)


class TestCorrelator:
    def test_enriches_frame(self):
        c = Correlator()
        c.ingest_frame(frame(arb_id=0x123, ts=1.5))
        out = c.drain_matched()
        assert len(out) == 1
        assert out[0].arb_id == 0x123
        assert out[0].kernel_ts == 1.5
        assert out[0].data == b"\xDE\xAD"

    def test_fifo_order_preserved(self):
        c = Correlator()
        for i in range(5):
            c.ingest_frame(frame(arb_id=0x100 + i, ts=i * 0.001))
        out = c.drain_matched()
        assert [f.arb_id for f in out] == [0x100 + i for i in range(5)]

    def test_drain_clears_queue(self):
        c = Correlator()
        c.ingest_frame(frame())
        assert len(c.drain_matched()) == 1
        assert c.drain_matched() == []

    def test_no_silent_drop_on_burst(self):
        # The old deque(maxlen=256) silently evicted the oldest unprocessed
        # frame on a burst; the unbounded queue must keep every frame.
        c = Correlator()
        for i in range(1000):
            c.ingest_frame(frame(arb_id=i & 0x7FF, ts=i * 1e-4))
        assert len(c.drain_matched()) == 1000

    def test_extended_id_flag(self):
        c = Correlator()
        c.ingest_frame(frame(arb_id=0x18FF50E5, ts=0.0, extended=True))
        out = c.drain_matched()
        assert out[0].is_extended is True
