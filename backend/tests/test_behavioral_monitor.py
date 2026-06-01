"""
Tests for BehavioralMonitor.

Uses a minimal synthetic DBC (built with cantools) so no .dbc file is needed.
"""

import time
import cantools
import pytest

from can_sniffer.behavioral_monitor import BehavioralMonitor
from can_sniffer.models import AlertCategory, EnrichedFrame


def _make_db() -> cantools.database.Database:
    """Return a minimal in-memory DBC with one periodic 8-byte message."""
    dbc_text = """
VERSION ""

NS_ :

BS_:

BU_:

BO_ 0x100 EngineStatus: 8 Vector__XXX
 SG_ EngineSpeed : 0|16@1+ (0.25,0) [0|8000] "rpm" Vector__XXX
 SG_ Throttle    : 16|8@1+ (0.4,0) [0|100] "%" Vector__XXX

"""
    db = cantools.database.Database()
    db.add_dbc_string(dbc_text)
    msg = db.get_message_by_frame_id(0x100)
    object.__setattr__(msg, 'cycle_time', 10.0)  # 10 ms cycle time
    return db


def make_frame(arb_id: int = 0x100, dlc: int = 8, data: bytes = b"\x00" * 8) -> EnrichedFrame:
    return EnrichedFrame(
        arb_id=arb_id, dlc=dlc, data=data,
        is_extended=False, pru_ts_ns=None, kernel_ts=time.time(),
    )


class TestBehavioralMonitor:
    def setup_method(self):
        self.db = _make_db()
        self.mon = BehavioralMonitor(self.db)

    def test_unknown_id_raises_alert(self):
        self.mon.ingest(make_frame(arb_id=0x7FF))
        alerts = self.mon.drain_alerts()
        cats = [a.category for a in alerts]
        assert AlertCategory.UNEXPECTED_ID in cats

    def test_known_id_no_unexpected_alert(self):
        self.mon.ingest(make_frame(arb_id=0x100))
        alerts = self.mon.drain_alerts()
        cats = [a.category for a in alerts]
        assert AlertCategory.UNEXPECTED_ID not in cats

    def test_dlc_mismatch_detected(self):
        self.mon.ingest(make_frame(arb_id=0x100, dlc=4))  # expected 8
        alerts = self.mon.drain_alerts()
        cats = [a.category for a in alerts]
        assert AlertCategory.DLC_MISMATCH in cats

    def test_missing_msg_detected(self):
        self.mon.ingest(make_frame(arb_id=0x100))
        self.mon.drain_alerts()
        # Fast-forward last_rx_time so message appears very overdue
        state = self.mon._states[0x100]
        state.last_rx_time = time.monotonic() - 10.0  # 10 s overdue (1000x cycle)
        self.mon.check_timeouts()
        alerts = self.mon.drain_alerts()
        cats = [a.category for a in alerts]
        assert AlertCategory.MISSING_MSG in cats
