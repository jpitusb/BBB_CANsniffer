from __future__ import annotations

import time
from typing import Optional

from .models import ALERT_COOLDOWN_S, Alert, AlertCategory, AlertSeverity


class AlertManager:
    """
    Deduplicates and rate-limits alerts.

    An alert with the same (category, can_id, signal_name) key is suppressed
    while it is still active and within its per-severity cooldown window.
    Once resolved, the same key can fire again immediately.
    """

    def __init__(self) -> None:
        self._active:  dict[str, Alert] = {}   # dedup key → active Alert
        self._history: list[Alert]      = []

    # ------------------------------------------------------------------

    def submit(self, alert: Alert) -> Optional[Alert]:
        """
        Accept an alert.  Returns the Alert if it should be broadcast
        (new or re-fired after cooldown), None if suppressed.
        """
        key      = _make_key(alert.category, alert.can_id, alert.signal_name)
        now      = time.time()
        existing = self._active.get(key)

        if existing and not existing.resolved:
            cooldown = ALERT_COOLDOWN_S[existing.severity]
            if now - existing.ts < cooldown:
                existing.count += 1
                return None          # suppress — within cooldown window
            existing.ts    = now
            existing.count += 1
            return existing

        self._active[key] = alert
        self._history.append(alert)
        return alert

    def resolve(self, category: AlertCategory,
                can_id: Optional[int] = None,
                signal_name: Optional[str] = None) -> None:
        key    = _make_key(category, can_id, signal_name)
        alert  = self._active.get(key)
        if alert and not alert.resolved:
            alert.resolved    = True
            alert.resolved_ts = time.time()

    def active_alerts(self) -> list[Alert]:
        return [a for a in self._active.values() if not a.resolved]

    def recent_history(self, n: int = 100) -> list[Alert]:
        return self._history[-n:]


def _make_key(category: AlertCategory, can_id: Optional[int],
              signal_name: Optional[str]) -> str:
    return f"{category.value}:{can_id}:{signal_name}"
