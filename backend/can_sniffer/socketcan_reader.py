from __future__ import annotations

import socket
import struct
import time
from typing import List

# ── SocketCAN constants ──────────────────────────────────────────────────────
CAN_EFF_FLAG = 0x80000000   # extended (29-bit) frame
CAN_RTR_FLAG = 0x40000000   # remote-transmission request
CAN_ERR_FLAG = 0x20000000   # error frame
CAN_EFF_MASK = 0x1FFFFFFF
CAN_SFF_MASK = 0x000007FF
CAN_ERR_MASK = 0x1FFFFFFF

# struct can_frame: __u32 can_id; __u8 len; 3 pad; __u8 data[8]  → 16 bytes,
# can_id in host byte order ("=" = native order, standard sizes, no padding).
_CAN_FRAME = struct.Struct("=IB3x8s")
_CAN_FRAME_SIZE = 16

SO_TIMESTAMP = getattr(socket, "SO_TIMESTAMP", 29)
_ANC_BUF = 64   # room for the SCM_TIMESTAMP (struct timeval) cmsg


class RawCanMessage:
    """Minimal drop-in for the subset of can.Message the pipeline reads.

    Replaces python-can's per-frame Message construction (whose IntFlag
    bitwise ops in capture_message were ~a third of the CPU on the
    single-core ARM at 1 Mbit/s) with plain integer parsing.
    """
    __slots__ = ("timestamp", "arbitration_id", "dlc", "data",
                 "is_extended_id", "is_error_frame")

    def __init__(self, timestamp: float, arbitration_id: int, dlc: int,
                 data: bytes, is_extended_id: bool, is_error_frame: bool) -> None:
        self.timestamp      = timestamp
        self.arbitration_id = arbitration_id
        self.dlc            = dlc
        self.data           = data
        self.is_extended_id = is_extended_id
        self.is_error_frame = is_error_frame


class SocketCanReader:
    """Raw AF_CAN reader, drained directly from the asyncio event loop.

    The previous implementation went through python-can in a ThreadPoolExecutor
    thread — on a single core that added GIL ping-pong with no parallelism. This
    reads the bound socket non-blockingly so the server can register it with
    loop.add_reader() and drain in the main thread.
    """

    def __init__(self, channel: str = "can1") -> None:
        self._sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self._sock.bind((channel,))
        # Receive all error-frame classes so the diagnostics error path keeps
        # working (kernel emits these with CAN_ERR_FLAG set on bus errors).
        self._sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_ERR_FILTER,
                              CAN_ERR_MASK)
        # Ask the kernel to attach the RX timestamp as ancillary data, giving
        # microsecond-resolution frame timestamps independent of when userspace
        # gets scheduled.
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMP, 1)
            self._timestamping = True
        except OSError:
            self._timestamping = False
        self._sock.setblocking(False)

    def fileno(self) -> int:
        return self._sock.fileno()

    def recv_batch(self, max_batch: int = 256) -> List[RawCanMessage]:
        """Non-blockingly drain up to max_batch queued frames.

        Returns [] when the socket is empty. Called from the event loop's
        add_reader callback; if more than max_batch are queued the fd stays
        readable and the loop re-invokes us, so nothing is starved.
        """
        out: List[RawCanMessage] = []
        recvmsg = self._sock.recvmsg
        for _ in range(max_batch):
            try:
                data, ancdata, _flags, _addr = recvmsg(_CAN_FRAME_SIZE, _ANC_BUF)
            except (BlockingIOError, InterruptedError):
                break
            if len(data) < _CAN_FRAME_SIZE:
                continue
            out.append(self._parse(data, ancdata))
        return out

    @staticmethod
    def _parse(data: bytes, ancdata) -> RawCanMessage:
        can_id, dlc, payload = _CAN_FRAME.unpack(data)
        is_err = bool(can_id & CAN_ERR_FLAG)
        is_ext = bool(can_id & CAN_EFF_FLAG)
        if is_err:
            arb = can_id & CAN_ERR_MASK
        elif is_ext:
            arb = can_id & CAN_EFF_MASK
        else:
            arb = can_id & CAN_SFF_MASK
        n = dlc if dlc <= 8 else 8

        ts = 0.0
        for lvl, typ, cdata in ancdata:
            if lvl == socket.SOL_SOCKET and typ == SO_TIMESTAMP:
                # struct timeval: two longs (sec, usec). 8 bytes on 32-bit, 16
                # on 64-bit time_t — derive width from the cmsg length.
                if len(cdata) >= 16:
                    sec, usec = struct.unpack("=qq", cdata[:16])
                else:
                    sec, usec = struct.unpack("=ll", cdata[:8])
                ts = sec + usec / 1_000_000
                break
        if ts == 0.0:
            ts = time.time()

        return RawCanMessage(ts, arb, n, payload[:n], is_ext, is_err)

    def close(self) -> None:
        self._sock.close()
