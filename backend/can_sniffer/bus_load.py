from __future__ import annotations

import time
from collections import deque

from .models import EnrichedFrame

_WINDOW_S = 1.0


def _frame_bit_length(dlc: int) -> int:
    """Approximate worst-case CAN frame size in bits (includes bit stuffing overhead)."""
    payload_bits = dlc * 8
    overhead     = 47          # SOF + arb(11) + ctrl(6) + CRC(15) + EOF(7) + IFS(3)
    total        = payload_bits + overhead
    # Worst-case stuff bits: 1 extra bit per 4 non-stuff bits
    return total + (total - 1) // 4


class BusLoadMonitor:
    def __init__(self, bitrate: int = 1_000_000, window_s: float = _WINDOW_S) -> None:
        self._bitrate  = bitrate
        self._window_s = window_s
        # Each entry: (timestamp_ns, bit_length)
        self._window: deque[tuple[float, int]] = deque()

    def record(self, frame: EnrichedFrame) -> None:
        ts  = frame.kernel_ts
        bits = _frame_bit_length(frame.dlc)
        self._window.append((ts, bits))
        cutoff = ts - self._window_s
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def current(self) -> float:
        if not self._window:
            return 0.0
        total_bits = sum(b for _, b in self._window)
        return min(total_bits / (self._bitrate * self._window_s), 1.0)
