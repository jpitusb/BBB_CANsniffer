from __future__ import annotations

from collections import deque
from threading import Lock

import can

from .models import EnrichedFrame


class Correlator:
    """Frame intake buffer.

    Originally correlated PRU hardware timestamps with SocketCAN frames; with
    the PRU removed it simply enriches each raw CAN message and queues it for
    the ingest loop to drain. The queue decouples the (hot) socket-readable
    callback from the heavier per-frame processing done every 20 ms.

    The queue is unbounded — it is drained every tick, so it stays small under
    normal load. (The previous deque(maxlen=256) silently evicted the oldest
    unprocessed frame on a burst, losing it from the DB, timing, and triggers.)
    """

    def __init__(self) -> None:
        self._can_q:   deque[EnrichedFrame] = deque()
        self._lock = Lock()

    # ------------------------------------------------------------------

    def ingest_frame(self, msg: can.Message) -> None:
        frame = self._enrich(msg)
        with self._lock:
            self._can_q.append(frame)

    def drain_matched(self) -> list[EnrichedFrame]:
        with self._lock:
            result = list(self._can_q)
            self._can_q.clear()
            return result

    # ------------------------------------------------------------------

    @staticmethod
    def _enrich(msg: can.Message) -> EnrichedFrame:
        return EnrichedFrame(
            arb_id      = msg.arbitration_id,
            dlc         = msg.dlc,
            data        = bytes(msg.data) if msg.data else b"",
            is_extended = bool(msg.is_extended_id),
            kernel_ts   = msg.timestamp,
        )
