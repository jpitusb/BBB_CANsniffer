from __future__ import annotations

import asyncio
import json
from typing import Optional

from .models import BusState


class TecRecPoller:
    """Polls `ip -j -d link show <iface>` at 1 Hz for TEC, REC, and bus state."""

    def __init__(self, interface: str = "can1", interval_s: float = 1.0) -> None:
        self._iface    = interface
        self._interval = interval_s
        self.tec:   int = 0
        self.rec:   int = 0
        self.state: BusState = BusState.UNKNOWN

    async def run(self) -> None:
        while True:
            await self._poll()
            await asyncio.sleep(self._interval)

    async def _poll(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip", "-j", "-d", "link", "show", self._iface,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            data = json.loads(stdout)[0]
        except Exception:
            return

        info_data = data.get("linkinfo", {}).get("info_data", {})
        berr = info_data.get("berr_cnt", {})
        self.tec = berr.get("tx", self.tec)
        self.rec = berr.get("rx", self.rec)

        state_str: Optional[str] = info_data.get("state")
        if state_str:
            try:
                self.state = BusState(state_str.lower())
            except ValueError:
                pass

    def snapshot(self) -> dict:
        return {
            "tec":   self.tec,
            "rec":   self.rec,
            "state": self.state.value,
        }
