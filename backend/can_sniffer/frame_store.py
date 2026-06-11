from __future__ import annotations

from collections import deque

from .models import EnrichedFrame


class FrameStore:
    def __init__(self, maxlen: int = 1000) -> None:
        # collections.deque single-operation appends and reads are GIL-atomic
        # in CPython, so no explicit lock is needed for the common case.
        self._frames: deque[EnrichedFrame] = deque(maxlen=maxlen)
        self._total = 0  # monotonic count of frames ever appended

    def append(self, frame: EnrichedFrame) -> None:
        self._frames.append(frame)
        self._total += 1

    @property
    def total_appended(self) -> int:
        return self._total

    def since(self, last_total: int) -> tuple[list[EnrichedFrame], int]:
        """Return (frames appended after last_total, new total).

        Lets the WebSocket send only genuinely new frames instead of copying
        and re-serialising the whole buffer each tick. A fresh client (last
        total 0) gets the current buffer once, then only deltas. Survives the
        deque dropping old items because the counter is monotonic and we clamp
        to what's still retained.
        """
        cur = self._total
        n_new = cur - last_total
        if n_new <= 0:
            return [], cur
        n_new = min(n_new, len(self._frames))
        base = len(self._frames) - n_new
        # Index from the right end (cheap for the small per-tick delta);
        # avoids list(deque) copying the full 1000-element buffer.
        return [self._frames[base + i] for i in range(n_new)], cur

    def snapshot(self) -> list[EnrichedFrame]:
        return list(self._frames)

    def __len__(self) -> int:
        return len(self._frames)
