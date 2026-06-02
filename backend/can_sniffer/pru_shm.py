from __future__ import annotations

import mmap
import struct
import time
from typing import Iterator

from .models import PruEvent, PruEventType

PRU_SHM_PHYS_ADDR = 0x9F000000    # DDR reserved (no no-map) — PRU writes via OCP, ARM via /dev/mem
PRU_SHM_SIZE      = 0x2000        # 8 KB
PRU_SHM_MAGIC     = 0xCAFE1234
PRU_RING_DEPTH    = 256

# pru_shm_t header: magic(I) + write_idx(I) + pad(II) = 16 bytes
_HDR  = struct.Struct("<IIII")
# pru_event_t: type(B) flags(B) seq(H) t_fall_ns(Q) pulse_ns(I) = 16 bytes
# Must match shared_mem.h pru_event_t with __attribute__((packed))
_EVT  = struct.Struct("<BBHQI")
_EVT_OFFSET = _HDR.size   # ring array starts immediately after header


class PruShm:
    """
    Read-only mmap view of the PRU DDR ring buffer.

    The physical memory region is marked no-map in the device tree, so the
    ARM cache will not hold stale copies.  Python accesses it as uncached DRAM
    through /dev/mem.
    """

    def __init__(self, phys_addr: int = PRU_SHM_PHYS_ADDR) -> None:
        self._fd = open("/dev/mem", "rb", buffering=0)
        # mmap with ACCESS_READ leaves the mapping write-protected from Python,
        # while the PRU still writes to the underlying physical memory.
        self._mm = mmap.mmap(
            self._fd.fileno(),
            PRU_SHM_SIZE,
            access=mmap.ACCESS_READ,
            offset=phys_addr,
        )
        magic, _, _, _ = _HDR.unpack_from(self._mm, 0)
        if magic != PRU_SHM_MAGIC:
            raise RuntimeError(
                f"PRU SHM magic mismatch: 0x{magic:08X} (expected 0x{PRU_SHM_MAGIC:08X}); "
                "firmware may not be running"
            )
        self._read_idx: int = 0
        # Calibrate: convert PRU IEP nanoseconds to Unix time_ns once at startup.
        # Drift is ~50 ppm on AM335x — acceptable for per-frame correlation.
        self.epoch_offset_ns: int = time.time_ns() - self._latest_pru_ns()

    # ------------------------------------------------------------------

    def _write_idx(self) -> int:
        _, write_idx, _, _ = _HDR.unpack_from(self._mm, 0)
        return write_idx

    def _latest_pru_ns(self) -> int:
        w = self._write_idx()
        if w == 0:
            return 0
        idx    = (w - 1) & (PRU_RING_DEPTH - 1)
        offset = _EVT_OFFSET + idx * _EVT.size
        _, _, _, t_fall_ns, _ = _EVT.unpack_from(self._mm, offset)
        return t_fall_ns

    # ------------------------------------------------------------------

    def drain(self) -> Iterator[PruEvent]:
        """Yield all new events since the last drain() call."""
        write_idx = self._write_idx()
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
