import time
import pytest

from can_sniffer.alert_manager import AlertManager
from can_sniffer.models import Alert, AlertCategory, AlertSeverity


def make_alert(cat: AlertCategory = AlertCategory.BABBLING_TX,
               can_id: int = None) -> Alert:
    return Alert(
        alert_id = Alert.make_id(cat, can_id, None),
        severity = AlertSeverity.WARN,
        category = cat,
        msg      = "test",
        ts       = time.time(),
        can_id   = can_id,
    )


class TestAlertManager:
    def test_new_alert_accepted(self):
        mgr = AlertManager()
        a   = make_alert()
        out = mgr.submit(a)
        assert out is not None
        assert len(mgr.active_alerts()) == 1

    def test_duplicate_suppressed_within_cooldown(self):
        mgr = AlertManager()
        mgr.submit(make_alert())
        out = mgr.submit(make_alert())
        assert out is None
        assert len(mgr.active_alerts()) == 1

    def test_critical_no_cooldown(self):
        mgr = AlertManager()
        a = Alert(
            alert_id = Alert.make_id(AlertCategory.BUS_OFF, None, None),
            severity = AlertSeverity.CRITICAL,
            category = AlertCategory.BUS_OFF,
            msg      = "bus off",
            ts       = time.time(),
        )
        mgr.submit(a)
        a2 = Alert(**{**a.__dict__, "ts": time.time()})
        out = mgr.submit(a2)
        assert out is not None  # CRITICAL has 0 s cooldown

    def test_resolve_clears_alert(self):
        mgr = AlertManager()
        mgr.submit(make_alert(AlertCategory.BABBLING_TX, can_id=None))
        mgr.resolve(AlertCategory.BABBLING_TX)
        assert len(mgr.active_alerts()) == 0

    def test_different_can_ids_independent(self):
        mgr = AlertManager()
        mgr.submit(make_alert(AlertCategory.MISSING_MSG, can_id=0x100))
        out = mgr.submit(make_alert(AlertCategory.MISSING_MSG, can_id=0x200))
        assert out is not None  # different key → not suppressed
        assert len(mgr.active_alerts()) == 2
