from __future__ import annotations

from typing import Optional
import can


class SocketCanReader:
    def __init__(self, channel: str = "can1", bitrate: int = 500_000) -> None:
        self._bus = can.interface.Bus(
            channel=channel,
            interface="socketcan",
            bitrate=bitrate,
            receive_own_messages=False,
        )

    def recv_one(self, timeout: float = 0.1) -> Optional[can.Message]:
        return self._bus.recv(timeout=timeout)

    def close(self) -> None:
        self._bus.shutdown()
