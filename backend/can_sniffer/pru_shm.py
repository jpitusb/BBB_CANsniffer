from __future__ import annotations

import mmap
import struct
import time
from typing import Iterator

from .models import PruEvent, PruEventType

PRU_SHM_PHYS_ADDR = 0x9F000000    # DDR reserved via memmap=8K$0x9F000000 in kernel cmdline
PRU_SHM_SIZE      = 0x2000        # 8 KB
PRU_SHM_MAGIC     = 0xCAFE1234
PRU_RING_DEPTH    = 256

# pru_shm_t header layout (must match shared_mem.h pru_shm_t):
#   magic(I=4) + write_idx(I=4) + _pad(I=4) + _pru_prev_iep(I=4)
#   + _pru_rollover_ns(Q=8) = 24 bytes total
# Python only needs magic and write_idx; the rest are PRU-private.
_HDR_MAGIC_WIDX = struct.Struct("<II")   # reads first 8 bytes
_EVT_OFFSET     = 24                     # ring array starts at byte 24
# pru_event_t: type(B) flags(B) seq(H) t_fall_ns(Q) pulse_ns(I) = 16 bytes
_EVT  = struct.Struct("<BBHQI")


class PruShm:
    """
    Live MAP_SHARED view of the PRU DDR ring buffer at 0x9F000000.

    Memory is reserved via memmap=8K$0x9F000000 in the kernel cmdline and
    accessed via /dev/mem opened for writing (MAP_SHARED).  ACCESS_READ
    would create a MAP_PRIVATE snapshot and miss PRU writes.
    """

    def __init__(self, phys_addr: int = PRU_SHM_PHYS_ADDR) -> None:
        self._fd = open("/dev/mem", "r+b", buffering=0)
        self._mm = mmap.mmap(self._fd.fileno(), PRU_SHM_SIZE, offset=phys_addr)
        magic, _ = _HDR_MAGIC_WIDX.unpack_from(self._mm, 0)
        if magic != PRU_SHM_MAGIC:
            raise RuntimeError(
                f"PRU SHM magic mismatch: 0x{magic:08X} (expected 0x{PRU_SHM_MAGIC:08X}); "
                "firmware may not be running or OCP not pre-enabled"
            )
        # Start reading from the current write position so we don't replay all
        # historical events (which would exhaust RAM on a long-running PRU).
        self._read_idx: int = self._write_idx()
        self.epoch_offset_ns: int = self._calibrate()

    # ------------------------------------------------------------------

    def _write_idx(self) -> int:
        _, write_idx = _HDR_MAGIC_WIDX.unpack_from(self._mm, 0)
        return write_idx

    def _latest_pru_ns(self) -> int:
        w = self._write_idx()
        if w == 0:
            return 0
        idx    = (w - 1) & (PRU_RING_DEPTH - 1)
        offset = _EVT_OFFSET + idx * _EVT.size
        _, _, _, t_fall_ns, _ = _EVT.unpack_from(self._mm, offset)
        return t_fall_ns

    def _calibrate(self) -> int:
        """Return epoch_offset_ns = Unix_now - latest_pru_ns, or Unix_now if no events."""
        latest = self._latest_pru_ns()
        return time.time_ns() - latest  # latest==0 gives Unix_now (still usable)

    # ------------------------------------------------------------------

    def drain(self) -> Iterator[PruEvent]:
        """Yield all new events since the last drain() call."""
        write_idx = self._write_idx()

        # Detect PRU restart: firmware resets write_idx to 0 and IEP to 0.
        # If write_idx fell behind our read cursor by more than the ring depth,
        # the PRU restarted — reset and recalibrate.
        read_mod = self._read_idx & 0xFFFFFFFF
        if write_idx != read_mod and (read_mod - write_idx) & 0xFFFFFFFF < PRU_RING_DEPTH:
            # write_idx is slightly behind due to normal ring wrap — fine.
            pass
        elif write_idx == 0 and self._read_idx > PRU_RING_DEPTH:
            # write_idx reset to 0 while we've read many events → PRU restart.
            self._read_idx = 0
            self.epoch_offset_ns = self._calibrate()

        # Recalibrate epoch every poll to compensate for drift and handle
        # cases where the server started before the PRU had any events.
        if write_idx != self._read_idx:
            latest = self._latest_pru_ns()
            if latest > 0:
                self.epoch_offset_ns = time.time_ns() - latest

        while self._read_idx != write_idx:
            idx    = self._read_idx & (PRU_RING_DEPTH - 1)
            offset = _EVT_OFFSET + idx * _EVT.size
            etype, flags, seq, t_fall_ns, pulse_ns = _EVT.unpack_from(self._mm, offset)
            yield PruEvent(
                type      = PruEventType(etype),
                flags     = flags,
                seq       = seq,
                t_fall_ns = t_fall_ns + self.epoch_offset_ns,
                pulse_ns  = pulse_ns,
            )
            self._read_idx += 1

    def close(self) -> None:
        self._mm.close()
        self._fd.close()

    def __enter__(self) -> "PruShm":
        return self

    def __exit__(self, *_) -> None:
        self.close()
