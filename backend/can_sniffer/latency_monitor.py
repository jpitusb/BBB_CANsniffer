from __future__ import annotations

import json
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

NODE_MASK = 0xFF   # lower byte always identifies the node/channel


@dataclass
class ExplicitPair:
    request_id:  int
    response_id: int
    label:       str


@dataclass
class PatternPair:
    request_base:  int   # e.g. 0x100
    response_base: int   # e.g. 0x000
    label_template: str  # e.g. "Device 0x{node_id:02X}"

    def label_for(self, node_id: int) -> str:
        return self.label_template.format(node_id=node_id)


def load_pairs(path: Path) -> tuple[list[ExplicitPair], list[PatternPair]]:
    """Load latency pair config from JSON.  Returns empty lists if file is missing."""
    if not path.exists():
        return [], []
    raw = json.loads(path.read_text())
    explicit, patterns = [], []
    for entry in raw:
        if entry.get("type") == "pattern":
            patterns.append(PatternPair(
                request_base   = int(entry["request_base"],  16)
                                 if isinstance(entry["request_base"],  str)
                                 else int(entry["request_base"]),
                response_base  = int(entry["response_base"], 16)
                                 if isinstance(entry["response_base"], str)
                                 else int(entry["response_base"]),
                label_template = entry.get("label_template", "0x{node_id:02X}"),
            ))
        else:
            request_id  = int(entry["request_id"],  16) \
                          if isinstance(entry["request_id"],  str) \
                          else int(entry["request_id"])
            response_id = int(entry["response_id"], 16) \
                          if isinstance(entry["response_id"], str) \
                          else int(entry["response_id"])
            explicit.append(ExplicitPair(
                request_id  = request_id,
                response_id = response_id,
                label       = entry.get("label", f"0x{request_id:X}→0x{response_id:X}"),
            ))
    return explicit, patterns


def save_pairs(path: Path, raw_list: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw_list, indent=2))


class LatencyMonitor:
    def __init__(self, explicit: list[ExplicitPair] = None,
                 patterns: list[PatternPair] = None) -> None:
        self._explicit: list[ExplicitPair] = explicit or []
        self._patterns: list[PatternPair]  = patterns or []
        # pending[label] = request kernel_ts
        self._pending:  dict[str, float]           = {}
        # measurements[label] = deque of latency_us floats
        self._meas:     dict[str, deque[float]]    = defaultdict(lambda: deque(maxlen=100))

    def set_pairs(self, explicit: list[ExplicitPair],
                  patterns: list[PatternPair]) -> None:
        self._explicit = explicit
        self._patterns = patterns
        self._pending.clear()

    def ingest(self, frame) -> None:
        arb_id = frame.arb_id
        ts     = frame.kernel_ts
        node   = arb_id & NODE_MASK
        base   = arb_id & ~NODE_MASK

        # ── Explicit pairs ────────────────────────────────────────────
        for ep in self._explicit:
            if arb_id == ep.request_id:
                self._pending[ep.label] = ts
            elif arb_id == ep.response_id and ep.label in self._pending:
                self._meas[ep.label].append(
                    (ts - self._pending.pop(ep.label)) * 1_000_000)

        # ── Pattern pairs ─────────────────────────────────────────────
        for pp in self._patterns:
            if base == pp.request_base:
                label = pp.label_for(node)
                self._pending[label] = ts
            elif base == pp.response_base:
                label = pp.label_for(node)
                if label in self._pending:
                    self._meas[label].append(
                        (ts - self._pending.pop(label)) * 1_000_000)

        # purge stale pending (> 1 s old)
        cutoff = ts - 1.0
        self._pending = {k: v for k, v in self._pending.items() if v >= cutoff}

    def snapshot(self) -> dict:
        out = {}
        for label, lats in self._meas.items():
            if not lats:
                continue
            lst = list(lats)
            n   = len(lst)
            mean = sum(lst) / n
            std  = math.sqrt(sum((x - mean) ** 2 for x in lst) / n)
            out[label] = {
                "count":   n,
                "min_us":  round(min(lst), 1),
                "max_us":  round(max(lst), 1),
                "mean_us": round(mean,     1),
                "std_us":  round(std,      1),
                "last_us": round(lst[-1],  1),
            }
        return out
