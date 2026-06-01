from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import cantools

from .models import (
    ALERT_SEVERITY_MAP,
    Alert,
    AlertCategory,
    EnrichedFrame,
)

_OVERATE_RATIO   = 1.5   # babble if actual rate > 150% expected
_OVERDUE_WARN    = 2.0   # MISSING_MSG if overdue > 200% cycle time
_OVERDUE_INFO    = 1.5   # MISSING_MSG_TRANSIENT if overdue > 150%


@dataclass
class MessageState:
    can_id:        int
    name:          str
    expected_dlc:  int
    cycle_time_ms: Optional[float]         # None → event-driven, not periodic
    last_rx_time:  float = field(default_factory=time.monotonic)
    rx_count:      int   = 0
    babble_window: deque = field(default_factory=lambda: deque(maxlen=20))
    last_signals:  dict  = field(default_factory=dict)
    first_seen:    float = field(default_factory=time.monotonic)
    is_known:      bool  = True


class BehavioralMonitor:
    def __init__(self, db: cantools.database.Database) -> None:
        self._db              = db
        self._states:         dict[int, MessageState] = {}
        self._pending_alerts: list[Alert] = []
        self._init_known_ids()

    def _init_known_ids(self) -> None:
        for msg in self._db.messages:
            self._states[msg.frame_id] = MessageState(
                can_id        = msg.frame_id,
                name          = msg.name,
                expected_dlc  = msg.length,
                cycle_time_ms = msg.cycle_time,
            )

    # ------------------------------------------------------------------

    def ingest(self, frame: EnrichedFrame) -> None:
        now   = time.monotonic()
        state = self._states.get(frame.arb_id)

        if state is None:
            state = MessageState(
                can_id        = frame.arb_id,
                name          = f"0x{frame.arb_id:X}",
                expected_dlc  = frame.dlc,
                cycle_time_ms = None,
                is_known      = False,
            )
            self._states[frame.arb_id] = state
            self._emit(AlertCategory.UNEXPECTED_ID,
                       f"Unknown CAN ID 0x{frame.arb_id:X} first seen",
                       can_id=frame.arb_id)
        else:
            if frame.dlc != state.expected_dlc:
                self._emit(AlertCategory.DLC_MISMATCH,
                           f"{state.name}: expected DLC {state.expected_dlc}, got {frame.dlc}",
                           can_id=frame.arb_id)

        state.rx_count += 1
        state.babble_window.append(now)
        state.last_rx_time = now

        self._check_babble(state)
        if state.is_known:
            self._check_signals(frame, state)

    def check_timeouts(self) -> None:
        now = time.monotonic()
        for state in self._states.values():
            if state.cycle_time_ms is None or not state.is_known:
                continue
            overdue_s = now - state.last_rx_time
            cycle_s   = state.cycle_time_ms / 1000.0
            ratio     = overdue_s / cycle_s
            if ratio > _OVERDUE_WARN:
                self._emit(AlertCategory.MISSING_MSG,
                           f"{state.name} overdue {overdue_s * 1000:.0f} ms "
                           f"(cycle {state.cycle_time_ms:.0f} ms)",
                           can_id=state.can_id)
            elif ratio > _OVERDUE_INFO:
                self._emit(AlertCategory.MISSING_MSG_TRANSIENT,
                           f"{state.name} missed one cycle", can_id=state.can_id)

    # ------------------------------------------------------------------

    def drain_alerts(self) -> list[Alert]:
        alerts = self._pending_alerts[:]
        self._pending_alerts.clear()
        return alerts

    def periodic_message_status(self) -> list[dict]:
        now = time.monotonic()
        out = []
        for s in self._states.values():
            if s.cycle_time_ms is None or not s.is_known:
                continue
            overdue_ms = max(0.0, (now - s.last_rx_time) * 1000 - s.cycle_time_ms)
            out.append({
                "id":          f"0x{s.can_id:X}",
                "name":        s.name,
                "cycle_ms":    s.cycle_time_ms,
                "overdue_ms":  round(overdue_ms, 1),
            })
        return out

    def unexpected_ids(self) -> list[str]:
        return [f"0x{s.can_id:X}" for s in self._states.values() if not s.is_known]

    # ------------------------------------------------------------------

    def _check_babble(self, state: MessageState) -> None:
        if state.cycle_time_ms is None or len(state.babble_window) < 10:
            return
        span_s = state.babble_window[-1] - state.babble_window[0]
        if span_s <= 0:
            return
        actual_hz   = (len(state.babble_window) - 1) / span_s
        expected_hz = 1000.0 / state.cycle_time_ms
        if actual_hz > expected_hz * _OVERATE_RATIO:
            self._emit(AlertCategory.BABBLING_TX,
                       f"{state.name}: {actual_hz:.1f} Hz (expected {expected_hz:.1f} Hz)",
                       can_id=state.can_id)

    def _check_signals(self, frame: EnrichedFrame, state: MessageState) -> None:
        try:
            msg_def = self._db.get_message_by_frame_id(frame.arb_id)
            decoded = self._db.decode_message(frame.arb_id, frame.data)
        except Exception:
            return

        for sig_name, value in decoded.items():
            try:
                sig = msg_def.get_signal_by_name(sig_name)
            except KeyError:
                continue
            if sig.minimum is not None and value < sig.minimum:
                self._emit(AlertCategory.RANGE_VIOLATION,
                           f"{state.name}.{sig_name} = {value} < min {sig.minimum}",
                           can_id=frame.arb_id, signal_name=sig_name)
            elif sig.maximum is not None and value > sig.maximum:
                self._emit(AlertCategory.RANGE_VIOLATION,
                           f"{state.name}.{sig_name} = {value} > max {sig.maximum}",
                           can_id=frame.arb_id, signal_name=sig_name)

        state.last_signals = decoded

    def _emit(self, category: AlertCategory, msg: str, **kwargs) -> None:
        self._pending_alerts.append(Alert(
            alert_id = Alert.make_id(category, kwargs.get("can_id"),
                                     kwargs.get("signal_name")),
            severity = ALERT_SEVERITY_MAP[category],
            category = category,
            msg      = msg,
            ts       = time.time(),
            **kwargs,
        ))
