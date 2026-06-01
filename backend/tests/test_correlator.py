import time
import can
import pytest

from can_sniffer.correlator import Correlator
from can_sniffer.models import PruEvent, PruEventType


def sof(t_ns: int, seq: int = 0) -> PruEvent:
    return PruEvent(type=PruEventType.SOF, flags=0, seq=seq, t_fall_ns=t_ns, pulse_ns=0)


def frame(arb_id: int = 0x123, ts: float = 0.0) -> can.Message:
    return can.Message(arbitration_id=arb_id, data=b"\xDE\xAD", timestamp=ts)


class TestCorrelator:
    def test_single_match(self):
        c = Correlator(max_delta_ns=5_000_000)
        t = int(time.time_ns())
        c.ingest_pru(sof(t))
        c.ingest_frame(frame(ts=t / 1e9 + 0.0001))
        matched = c.drain_matched()
        assert len(matched) == 1
        assert matched[0].arb_id == 0x123
        assert matched[0].pru_ts_ns == t

    def test_fifo_order_preserved(self):
        c = Correlator(max_delta_ns=5_000_000)
        t = int(time.time_ns())
        for i in range(5):
            c.ingest_pru(sof(t + i * 1_000_000, seq=i))
        for i in range(5):
            c.ingest_frame(frame(arb_id=0x100 + i, ts=(t + i * 1_000_000) / 1e9 + 0.0002))
        matched = c.drain_matched()
        assert len(matched) == 5
        for i, f in enumerate(matched):
            assert f.arb_id == 0x100 + i

    def test_stale_pru_event_discarded(self):
        c = Correlator(max_delta_ns=300_000)
        t = int(time.time_ns())
        c.ingest_pru(sof(t))
        # Frame arrives 10 ms after PRU timestamp — beyond 300 µs max
        c.ingest_frame(frame(ts=t / 1e9 + 0.010))
        matched = c.drain_matched()
        # Stale SOF discarded; frame emitted without PRU ts
        assert len(matched) == 1
        assert matched[0].pru_ts_ns is None

    def test_non_sof_events_ignored(self):
        c = Correlator()
        t = int(time.time_ns())
        glitch = PruEvent(type=PruEventType.GLITCH, flags=0, seq=0,
                          t_fall_ns=t, pulse_ns=500)
        c.ingest_pru(glitch)
        c.ingest_frame(frame(ts=t / 1e9 + 0.001))
        matched = c.drain_matched()
        assert len(matched) == 1
        assert matched[0].pru_ts_ns is None  # glitch not matched
