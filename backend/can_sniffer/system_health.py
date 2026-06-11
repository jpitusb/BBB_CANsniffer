from __future__ import annotations

import os
import time
from typing import Optional


class SystemHealthMonitor:
    """Reads CPU, memory, temperature, load, uptime, disk from /proc and /sys.
    Results are cached for 1 second so rapid snapshot() calls are cheap."""

    _CACHE_S = 1.0

    def __init__(self) -> None:
        self._prev_idle:  int = 0
        self._prev_total: int = 0
        self._cache_ts:   float = 0.0
        self._cache:      Optional[dict] = None

    def snapshot(self) -> dict:
        now = time.monotonic()
        if self._cache is None or (now - self._cache_ts) >= self._CACHE_S:
            self._cache = {
                "cpu_pct":  self._cpu_pct(),
                "mem":      self._mem(),
                "temp_c":   self._temp(),
                "load":     self._load(),
                "uptime_s": self._uptime(),
                "disk":     self._disk(),
            }
            self._cache_ts = now
        return self._cache

    # ── readers ────────────────────────────────────────────────────────────

    def _cpu_pct(self) -> Optional[float]:
        try:
            with open("/proc/stat") as f:
                parts = f.readline().split()
            idle  = int(parts[4])
            total = sum(int(x) for x in parts[1:])
            d_idle  = idle  - self._prev_idle
            d_total = total - self._prev_total
            self._prev_idle  = idle
            self._prev_total = total
            if d_total == 0:
                return 0.0
            return round(100.0 * (1.0 - d_idle / d_total), 1)
        except Exception:
            return None

    def _mem(self) -> Optional[dict]:
        try:
            info: dict = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, *rest = line.split()
                    if k in ("MemTotal:", "MemAvailable:"):
                        info[k[:-1]] = int(rest[0])
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", 0)
            used  = total - avail
            pct   = round(100.0 * used / total, 1) if total else 0.0
            return {"total_mb": total // 1024, "used_mb": used // 1024, "pct": pct}
        except Exception:
            return None

    def _temp(self) -> Optional[float]:
        for path in (
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/devices/platform/omap_temp_sensor.0/temp1_input",
        ):
            try:
                with open(path) as f:
                    return round(int(f.read().strip()) / 1000.0, 1)
            except Exception:
                continue
        return None

    def _load(self) -> Optional[list]:
        try:
            with open("/proc/loadavg") as f:
                p = f.read().split()
            return [float(p[0]), float(p[1]), float(p[2])]
        except Exception:
            return None

    def _uptime(self) -> Optional[float]:
        try:
            with open("/proc/uptime") as f:
                return float(f.read().split()[0])
        except Exception:
            return None

    def _disk(self) -> Optional[dict]:
        try:
            st   = os.statvfs("/opt/can_sniffer")
            tot  = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = tot - free
            pct  = round(100.0 * used / tot, 1) if tot else 0.0
            return {
                "total_gb": round(tot  / 1e9, 1),
                "used_gb":  round(used / 1e9, 1),
                "pct":      pct,
            }
        except Exception:
            return None
