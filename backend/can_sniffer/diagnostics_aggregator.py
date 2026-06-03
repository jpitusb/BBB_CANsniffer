from __future__ import annotations

import time
from typing import Optional

import can

from .alert_manager import AlertManager
from .behavioral_monitor import BehavioralMonitor
from .error_decoder import ErrorDecoder
from .models import (
    Alert,
    AlertCategory,
    AlertSeverity,
    BusState,
    EnrichedFrame,
    ErrorEvent,
    PruEvent,
    PruEventType,
)
from .signal_quality_monitor import SignalQualityMonitor
from .tec_rec_poller import TecRecPoller

_ERR_BURST_THRESHOLD = 3   # SocketCAN error frames per second → REPEATED_ERROR_FRAMES


class DiagnosticsAggregator:
    """
    Central fan-out point for all diagnostic data.

    Each 20 Hz WebSocket tick calls snapshot() which returns a JSON-serialisable
    dict merging signal quality, protocol errors, bus state, and behavioral data.
    """

    def __init__(
        self,
        behavioral_monitor: Optional[BehavioralMonitor] = None,
        tec_rec_poller:     Optional[TecRecPoller]      = None,
    ) -> None:
        self._sqm      = SignalQualityMonitor()
        self._decoder  = ErrorDecoder()
        self._alerts   = AlertManager()
        self._behav    = behavioral_monitor
        self._tec_rec  = tec_rec_poller

        # Rolling 1-second error frame counter
        self._err_ts:   list[float] = []

        # Session protocol error counters
        self._proto_counts = {
            "bit_errors":   0,
            "stuff_errors": 0,
            "crc_errors":   0,
            "form_errors":  0,
            "ack_errors":   0,
        }

    # ------------------------------------------------------------------

    def ingest_pru_event(self, event: PruEvent) -> None:
        self._sqm.ingest_pru_event(event)
        self._flush_alerts()

    def ingest_abort(self, evt) -> None:
        self._sqm.ingest_abort(evt)
        self._flush_alerts()

    def ingest_error_frame(self, msg: can.Message) -> Optional[ErrorEvent]:
        err = self._decoder.decode(msg)
        if err is None:
            return None
        self._update_proto_counts(err)
        self._update_bus_state_from_error(err)
        now = time.time()
        self._err_ts.append(now)
        self._err_ts = [t for t in self._err_ts if now - t < 1.0]
        if len(self._err_ts) >= _ERR_BURST_THRESHOLD:
            self._submit(Alert(
                alert_id = Alert.make_id(AlertCategory.REPEATED_ERROR_FRAMES, None, None),
                severity = AlertSeverity.WARN,
                category = AlertCategory.REPEATED_ERROR_FRAMES,
                msg      = f"{len(self._err_ts)} error frames in last 1 s",
                ts       = now,
            ))
        if err.bus_off:
            self._submit(Alert(
                alert_id = Alert.make_id(AlertCategory.BUS_OFF, None, None),
                severity = AlertSeverity.CRITICAL,
                category = AlertCategory.BUS_OFF,
                msg      = "CAN controller entered bus-off state",
                ts       = now,
            ))
        return err

    def ingest_frame(self, frame: EnrichedFrame) -> None:
        if self._behav:
            self._behav.ingest(frame)
            self._flush_behavioural_alerts()

    # ------------------------------------------------------------------

    def check_periodic_timeouts(self) -> None:
        if self._behav:
            self._behav.check_timeouts()
            self._flush_behavioural_alerts()

    def snapshot(self) -> dict:
        tec_rec = self._tec_rec.snapshot() if self._tec_rec else {"tec": 0, "rec": 0, "state": "unknown"}
        sqm     = self._sqm.snapshot()
        behav: dict = {}
        if self._behav:
            behav = {
                "missing_msgs":          self._behav.periodic_message_status(),
                "unexpected_ids":        self._behav.unexpected_ids(),
                "dlc_mismatches":        [],  # populated from alerts
                "range_violations":      [],  # populated from alerts
                "babbling_transmitters": [],  # populated from alerts
            }
        return {
            "bus_health":    {**tec_rec, "error_frames_1s": len(self._err_ts)},
            "protocol_errors": self._proto_counts,
            "signal_quality":  sqm,
            "behavioral":      behav,
            "alerts":         [a.to_dict() for a in self._alerts.active_alerts()[-20:]],
        }

    # ------------------------------------------------------------------

    def _submit(self, alert: Alert) -> None:
        self._alerts.submit(alert)

    def _flush_alerts(self) -> None:
        for a in self._sqm.drain_alerts():
            self._alerts.submit(a)

    def _flush_behavioural_alerts(self) -> None:
        if self._behav:
            for a in self._behav.drain_alerts():
                self._alerts.submit(a)

    def _update_proto_counts(self, err: ErrorEvent) -> None:
        if err.bit_error:   self._proto_counts["bit_errors"]   += 1
        if err.stuff_error: self._proto_counts["stuff_errors"] += 1
        if err.crc_error:   self._proto_counts["crc_errors"]   += 1
        if err.form_error:  self._proto_counts["form_errors"]  += 1
        if err.ack_error:   self._proto_counts["ack_errors"]   += 1

    def _update_bus_state_from_error(self, err: ErrorEvent) -> None:
        if self._tec_rec and err.tec is not None:
            self._tec_rec.tec = err.tec
        if self._tec_rec and err.rec is not None:
            self._tec_rec.rec = err.rec
