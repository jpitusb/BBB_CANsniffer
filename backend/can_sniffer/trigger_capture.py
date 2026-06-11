from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
try:
    from typing import Literal, Optional
except ImportError:  # Python < 3.8
    from typing_extensions import Literal  # type: ignore
    from typing import Optional


@dataclass
class TriggerCondition:
    type: Literal["arb_id", "error_frame", "bus_load", "manual"]
    arb_id:               Optional[int]   = None
    bus_load_threshold:   Optional[float] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "type":               self.type,
            "arb_id":             self.arb_id,
            "bus_load_threshold": self.bus_load_threshold,
        }.items() if v is not None}


@dataclass
class CaptureSession:
    id:             str
    trigger_ts:     float
    condition_type: str
    pre_frames:     list[dict] = field(default_factory=list)
    post_frames:    list[dict] = field(default_factory=list)

    @property
    def frame_count(self) -> int:
        return len(self.pre_frames) + len(self.post_frames)

    def summary(self) -> dict:
        return {
            "id":             self.id,
            "trigger_ts":     self.trigger_ts,
            "condition_type": self.condition_type,
            "frame_count":    self.frame_count,
        }

    def to_dict(self) -> dict:
        return {**self.summary(),
                "pre_frames":  self.pre_frames,
                "post_frames": self.post_frames}


class TriggerCapture:
    def __init__(self, pre_size: int = 200, post_size: int = 200) -> None:
        self._pre_size  = pre_size
        self._post_size = post_size
        self._pre_buf:  deque[dict]          = deque(maxlen=pre_size)
        self._post_buf: list[dict]           = []
        self._state:    str                  = "idle"
        self._cond:     Optional[TriggerCondition] = None
        self._captures: deque[CaptureSession]      = deque(maxlen=20)
        self._pending:  Optional[CaptureSession]   = None
        self._lock = threading.Lock()

    # ── Public control API ────────────────────────────────────────────

    def arm(self, condition: TriggerCondition) -> None:
        with self._lock:
            self._cond     = condition
            self._post_buf = []
            self._state    = "armed"

    def disarm(self) -> None:
        with self._lock:
            self._state    = "idle"
            self._cond     = None
            self._post_buf = []

    def fire(self) -> None:
        with self._lock:
            if self._state == "armed":
                self._trigger()

    # ── Frame ingestion ───────────────────────────────────────────────

    def ingest(self, frame, bus_load: float) -> None:
        with self._lock:
            if self._state == "idle":
                return

            fd = frame.to_dict()

            if self._state == "armed":
                self._pre_buf.append(fd)
                if self._condition_met(frame, bus_load):
                    self._trigger()

            elif self._state == "collecting":
                self._post_buf.append(fd)
                if len(self._post_buf) >= self._post_size:
                    self._save_session()

    def _condition_met(self, frame, bus_load: float) -> bool:
        cond = self._cond
        if cond.type == "manual":
            return False
        if cond.type == "arb_id":
            return frame.arb_id == cond.arb_id
        if cond.type == "error_frame":
            return getattr(frame, "is_error_frame", False)
        if cond.type == "bus_load":
            return (cond.bus_load_threshold is not None
                    and bus_load >= cond.bus_load_threshold)
        return False

    def _trigger(self) -> None:
        self._state    = "collecting"
        self._post_buf = []

    def _save_session(self) -> None:
        session = CaptureSession(
            id             = uuid.uuid4().hex[:8],
            trigger_ts     = time.time(),
            condition_type = self._cond.type if self._cond else "manual",
            pre_frames     = list(self._pre_buf),
            post_frames    = list(self._post_buf),
        )
        self._captures.append(session)
        self._pending  = session
        self._post_buf = []
        self._state    = "armed"   # re-arm automatically

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def captures(self) -> list[CaptureSession]:
        return list(self._captures)

    def pop_completed(self) -> Optional[CaptureSession]:
        with self._lock:
            s = self._pending
            self._pending = None
            return s

    def get_capture(self, capture_id: str) -> Optional[CaptureSession]:
        for cap in self._captures:
            if cap.id == capture_id:
                return cap
        return None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state":     self._state,
                "condition": self._cond.to_dict() if self._cond else None,
                "captures":  [c.summary() for c in reversed(list(self._captures))][:5],
            }
