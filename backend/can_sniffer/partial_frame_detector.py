from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from .models import AbortedFrameEvent, PruEvent, PruEventType

_ABORT_TIMEOUT_S = 0.005  # 5 ms — see correlator.py for derivation


@dataclass
class _Pending:
    event:     PruEvent
    wall_time: float = field(default_factory=time.monotonic)


class PartialFrameDetector:
    """
    Tracks PRU SOF events that are never matched by a SocketCAN frame.

    The Correlator consumes SOF events as it matches them; this class holds the
    same queue and fires AbortedFrameEvents for any SOF older than _ABORT_TIMEOUT_S.
    In practice both classes share the same PRU event stream: the Correlator
    gets first call and removes matched SOFs; this class times out the rest.
    """

    def __init__(self, timeout_s: float = _ABORT_TIMEOUT_S) -> None:
        self._timeout_s  = timeout_s
        self._pending:   deque[_Pending] = deque()
        self._callbacks: list = []          # callables receiving AbortedFrameEvent

    def add_callback(self, cb) -> None:
        self._callbacks.append(cb)

    def ingest_pru(self, event: PruEvent) -> None:
        if event.type is PruEventType.SOF:
            self._pending.append(_Pending(event=event))

    def mark_matched(self) -> None:
        """Remove the oldest pending SOF (called by Correlator on a successful match)."""
        if self._pending:
            self._pending.popleft()

    async def run(self) -> None:
        while True:
            now = time.monotonic()
            while self._pending:
                oldest = self._pending[0]
                if now - oldest.wall_time > self._timeout_s:
                    self._pending.popleft()
                    evt = AbortedFrameEvent(
                        pru_ts_ns = oldest.event.t_fall_ns,
                        wall_time = oldest.wall_time,
                    )
                    for cb in self._callbacks:
                        cb(evt)
                else:
                    break
            await asyncio.sleep(0.01)   # 10 ms: abort detection has a 5 ms
                                        # timeout anyway, so 100 Hz scanning is
                                        # ample and cuts event-loop wakeups 10x.
