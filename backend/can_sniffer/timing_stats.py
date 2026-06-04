from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Optional


class _IdStats:
    __slots__ = ("count", "last_ts", "_intervals")

    def __init__(self) -> None:
        self.count: int = 0
        self.last_ts: Optional[float] = None
        self._intervals: deque[float] = deque(maxlen=100)

    def record(self, ts: float) -> None:
        if self.last_ts is not None:
            self._intervals.append((ts - self.last_ts) * 1000.0)
        self.last_ts = ts
        self.count += 1

    def to_dict(self) -> dict:
        if len(self._intervals) < 2:
            fps = round(1.0 / ((self.last_ts or 0) + 1e-9), 2) if self.count == 1 else 0.0
            return {"count": self.count, "interval_min_ms": None,
                    "interval_max_ms": None, "interval_mean_ms": None,
                    "interval_std_ms": None, "interval_p95_ms": None,
                    "interval_p99_ms": None, "jitter_rms_ms": None,
                    "frames_per_sec": fps}

        ivs = list(self._intervals)
        n = len(ivs)
        ivs_sorted = sorted(ivs)
        mean = sum(ivs) / n
        variance = sum((x - mean) ** 2 for x in ivs) / n
        std = math.sqrt(variance)
        p95 = ivs_sorted[min(int(n * 0.95), n - 1)]
        p99 = ivs_sorted[min(int(n * 0.99), n - 1)]
        fps = round(1000.0 / mean, 2) if mean > 0 else 0.0

        return {
            "count":            self.count,
            "interval_min_ms":  round(ivs_sorted[0],  3),
            "interval_max_ms":  round(ivs_sorted[-1], 3),
            "interval_mean_ms": round(mean,  3),
            "interval_std_ms":  round(std,   3),
            "interval_p95_ms":  round(p95,   3),
            "interval_p99_ms":  round(p99,   3),
            "jitter_rms_ms":    round(std,   3),
            "frames_per_sec":   fps,
        }


class TimingStatsCollector:
    def __init__(self) -> None:
        self._stats: dict[int, _IdStats] = defaultdict(_IdStats)

    def ingest(self, frame) -> None:
        self._stats[frame.arb_id].record(frame.kernel_ts)

    def snapshot(self) -> dict:
        return {
            f"0x{arb_id:X}": stats.to_dict()
            for arb_id, stats in sorted(self._stats.items())
        }

    def reset(self) -> None:
        self._stats.clear()
