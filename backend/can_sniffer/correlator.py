from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Optional

import can

from .models import EnrichedFrame, PruEvent, PruEventType

# Maximum time between a PRU SOF capture and the matching SocketCAN frame delivery.
# At 1 Mbit/s, a max-length frame (extended + 8 B data) takes ~130 µs.
# Linux socket delivery latency worst-case ~500 µs.  5 ms gives a 10x safety margin
# while still being short enough to detect genuinely aborted frames quickly.
_DEFAULT_MAX_DELTA_NS = 5_000_000  # 5 ms


class Correlator:
    def __init__(
        self,
        epoch_offset_ns: int = 0,
        max_delta_ns: int = _DEFAULT_MAX_DELTA_NS,
    ) -> None:
        self._epoch_offset_ns = epoch_offset_ns
        self._max_delta_ns    = max_delta_ns
        self._pru_q:     deque[PruEvent]   = deque(maxlen=256)
        self._can_q:     deque[can.Message] = deque(maxlen=256)
        self._matched:   deque[EnrichedFrame] = deque()
        self._lock = Lock()

    # ------------------------------------------------------------------

    def ingest_pru(self, event: PruEvent) -> None:
        if event.type is not PruEventType.SOF:
            return
        with self._lock:
            self._pru_q.append(event)
            self._try_match()

    def ingest_frame(self, msg: can.Message) -> None:
        with self._lock:
            self._can_q.append(msg)
            self._try_match()

    def drain_matched(self) -> list[EnrichedFrame]:
        with self._lock:
            # Flush CAN frames that are older than max_delta_ns and have no
            # matching PRU SOF left in the queue.  This prevents frames from
            # accumulating when the PRU is not running or a glitch discards
            # the only pending SOF.
            now_ns = time.time_ns()
            while self._can_q:
                msg = self._can_q[0]
                age_ns = now_ns - int(msg.timestamp * 1_000_000_000)
                if age_ns > self._max_delta_ns:
                    self._can_q.popleft()
                    self._matched.append(self._enrich(msg, None))
                else:
                    break
            result = list(self._matched)
            self._matched.clear()
            return result

    # ------------------------------------------------------------------

    def _try_match(self) -> None:
        while self._pru_q and self._can_q:
            pru = self._pru_q[0]
            msg = self._can_q[0]
            msg_ns   = int(msg.timestamp * 1_000_000_000)
            delta_ns = msg_ns - pru.t_fall_ns

            if delta_ns < 0:
                # SocketCAN frame is older than PRU timestamp — clock skew or
                # frame arrived before PRU was started.  Emit without PRU ts.
                self._can_q.popleft()
                self._matched.append(self._enrich(msg, None))
                continue

            if delta_ns > self._max_delta_ns:
                # PRU timestamp has no matching frame — it was aborted or
                # errored before DCAN0 could deliver it.  Discard the SOF.
                self._pru_q.popleft()
                continue

            self._pru_q.popleft()
            self._can_q.popleft()
            self._matched.append(self._enrich(msg, pru.t_fall_ns))

    @staticmethod
    def _enrich(msg: can.Message, pru_ts_ns: Optional[int]) -> EnrichedFrame:
        return EnrichedFrame(
            arb_id      = msg.arbitration_id,
            dlc         = msg.dlc,
            data        = bytes(msg.data) if msg.data else b"",
            is_extended = bool(msg.is_extended_id),
            pru_ts_ns   = pru_ts_ns,
            kernel_ts   = msg.timestamp,
        )
