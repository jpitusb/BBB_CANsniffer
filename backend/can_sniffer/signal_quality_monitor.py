from __future__ import annotations

import time
from collections import deque

from .models import AbortedFrameEvent, Alert, AlertCategory, AlertSeverity, PruEvent, PruEventType

_GLITCH_BURST_THRESHOLD  = 3   # glitches per second → GLITCH_BURST WARN
_ABORT_BURST_THRESHOLD   = 5   # aborted frames per second → REPEATED_ABORTS CRITICAL
_WINDOW_S                = 1.0


class SignalQualityMonitor:
    def __init__(self) -> None:
        self._glitch_window: deque[float]  = deque()
        self._abort_window:  deque[float]  = deque()
        self._pending_alerts: list[Alert]  = []
        self._runaway_active: bool         = False

    # ------------------------------------------------------------------

    def ingest_pru_event(self, event: PruEvent) -> None:
        now = time.time()
        if event.type is PruEventType.GLITCH:
            self._glitch_window.append(now)
            self._trim(self._glitch_window, now)
            if len(self._glitch_window) >= _GLITCH_BURST_THRESHOLD:
                self._emit(AlertCategory.GLITCH_BURST,
                           f"{len(self._glitch_window)} glitches in last {_WINDOW_S:.0f} s")
            else:
                self._emit(AlertCategory.SINGLE_GLITCH,
                           f"Glitch: {event.pulse_ns} ns dominant pulse")

        elif event.type is PruEventType.DOMINANT_RUNAWAY:
            self._runaway_active = True
            self._emit(AlertCategory.DOMINANT_RUNAWAY,
                       f"Bus stuck dominant for {event.pulse_ns / 1_000_000:.1f} ms")

    def ingest_abort(self, evt: AbortedFrameEvent) -> None:
        now = time.time()
        self._abort_window.append(now)
        self._trim(self._abort_window, now)
        if len(self._abort_window) >= _ABORT_BURST_THRESHOLD:
            self._emit(AlertCategory.REPEATED_ABORTS,
                       f"{len(self._abort_window)} aborted frames in last {_WINDOW_S:.0f} s")
        else:
            self._emit(AlertCategory.ABORTED_FRAME, "SOF detected but no frame delivered")

    # ------------------------------------------------------------------

    def drain_alerts(self) -> list[Alert]:
        alerts = self._pending_alerts[:]
        self._pending_alerts.clear()
        return alerts

    def snapshot(self) -> dict:
        now = time.time()
        self._trim(self._glitch_window, now)
        self._trim(self._abort_window,  now)
        return {
            "glitches_1s":    len(self._glitch_window),
            "aborts_1s":      len(self._abort_window),
            "dominant_runaway": self._runaway_active,
        }

    # ------------------------------------------------------------------

    def _emit(self, category: AlertCategory, msg: str) -> None:
        from .models import ALERT_SEVERITY_MAP
        self._pending_alerts.append(Alert(
            alert_id = Alert.make_id(category, None, None),
            severity = ALERT_SEVERITY_MAP[category],
            category = category,
            msg      = msg,
            ts       = time.time(),
        ))

    @staticmethod
    def _trim(window: deque[float], now: float) -> None:
        cutoff = now - _WINDOW_S
        while window and window[0] < cutoff:
            window.popleft()
