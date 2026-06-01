from __future__ import annotations

from collections import deque

from .models import EnrichedFrame


class FrameStore:
    def __init__(self, maxlen: int = 1000) -> None:
        # collections.deque single-operation appends and reads are GIL-atomic
        # in CPython, so no explicit lock is needed for the common case.
        self._frames: deque[EnrichedFrame] = deque(maxlen=maxlen)

    def append(self, frame: EnrichedFrame) -> None:
        self._frames.append(frame)

    def snapshot(self) -> list[EnrichedFrame]:
        return list(self._frames)

    def __len__(self) -> int:
        return len(self._frames)
