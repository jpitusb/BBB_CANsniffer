import time
import pytest

from can_sniffer.bus_load import BusLoadMonitor
from can_sniffer.models import EnrichedFrame


def make_frame(dlc: int = 8, ts: float = 0.0) -> EnrichedFrame:
    return EnrichedFrame(
        arb_id=0x100, dlc=dlc, data=b"\x00" * dlc,
        is_extended=False, kernel_ts=ts,
    )


class TestBusLoadMonitor:
    def test_zero_when_empty(self):
        m = BusLoadMonitor(bitrate=500_000)
        assert m.current() == 0.0

    def test_single_frame_nonzero(self):
        m = BusLoadMonitor(bitrate=500_000)
        m.record(make_frame(dlc=8, ts=time.time()))
        assert 0.0 < m.current() < 1.0

    def test_saturated_bus(self):
        m = BusLoadMonitor(bitrate=500_000, window_s=1.0)
        # Insert enough bits to saturate the window
        now = time.time()
        for _ in range(4000):   # 4000 * ~130 bits >> 500_000
            m.record(make_frame(dlc=8, ts=now))
        assert m.current() == 1.0

    def test_old_frames_expire(self):
        m = BusLoadMonitor(bitrate=500_000, window_s=1.0)
        old_ts = time.time() - 2.0
        m.record(make_frame(dlc=8, ts=old_ts))
        # Force a new record to trigger eviction
        m.record(make_frame(dlc=0, ts=time.time()))
        # Only the fresh 0-DLC frame counts
        assert m.current() < 0.001
