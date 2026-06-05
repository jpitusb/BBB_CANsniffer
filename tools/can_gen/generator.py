#!/usr/bin/env python3
"""
CAN traffic generator for BBB #2 — generates good and bad CAN messages
and controls the PRU fault injector for physical-layer noise.

Usage:
    sudo python3 generator.py [--scenario SCENARIO ...] [--loop] [--channel can0]

Scenarios (can combine multiple with --scenario):
  normal         Periodic good traffic (3 messages, correct rates/DLC/signals)
  babble         0x100 at 10× its normal 10 ms cycle (triggers BABBLING_TX)
  missing        0x100 disappears for 3 s then resumes (triggers MISSING_MSG)
  unknown_id     Sprinkle frames with IDs not in the DBC (UNEXPECTED_ID)
  dlc_mismatch   0x100 sent with DLC=4 instead of 8 (DLC_MISMATCH)
  range_viol     EngineSpeed set to 9000 rpm (> 8000 max; RANGE_VIOLATION)
  bus_flood      High-rate frames saturating the bus
  glitch         PRU: one 400 ns dominant glitch
  glitch_burst   PRU: ~5 glitches/s for GLITCH_BURST alert on sniffer
  dominant       PRU: bus stuck dominant for 80 ms (DOMINANT_RUNAWAY)
  intermittent   PRU: random glitch ~once/s

Hardware (BBB #2):
  Transceiver on P9.19(RX) / P9.20(TX)  →  can0  (same pins as BBB #1)
  P8.45 (PRU0 R30[0] output) wired to transceiver TXD alongside P9.20
  for physical-layer fault injection (see tools/can_gen/setup_bbb2.sh).
"""

import argparse
import asyncio
import mmap
import struct
import sys
import time
import random
from dataclasses import dataclass, field
from typing import Optional

try:
    import can
except ImportError:
    sys.exit("python-can not installed. Run: pip3 install python-can")

# ── Shared memory for PRU fault injector ────────────────────────────────────
FAULT_SHM_ADDR  = 0x9F000000
FAULT_SHM_SIZE  = 0x1000
FAULT_SHM_MAGIC = 0xFA017123

FAULT_IDLE         = 0
FAULT_GLITCH       = 1
FAULT_GLITCH_BURST = 2
FAULT_DOMINANT     = 3
FAULT_INTERMITTENT = 4

# pru_fault_shm_t: magic(I) fault_mode(I) faults_done(I) pad(I)
_FAULT_HDR = struct.Struct("<IIII")


class PruFault:
    """Interface to the PRU fault injector via /dev/mem."""

    def __init__(self) -> None:
        try:
            self._fd = open("/dev/mem", "r+b", buffering=0)
            self._mm = mmap.mmap(self._fd.fileno(), FAULT_SHM_SIZE,
                                 offset=FAULT_SHM_ADDR)
            magic, _, _, _ = _FAULT_HDR.unpack_from(self._mm, 0)
            if magic != FAULT_SHM_MAGIC:
                print(f"[PRU] Warning: magic 0x{magic:08X} ≠ 0x{FAULT_SHM_MAGIC:08X}; "
                      "fault PRU may not be running — physical faults disabled")
                self._ok = False
            else:
                self._ok = True
                print("[PRU] Fault injector connected.")
        except PermissionError:
            print("[PRU] /dev/mem not accessible — run as root for PRU faults")
            self._ok = False
        except Exception as e:
            print(f"[PRU] Fault injector unavailable: {e}")
            self._ok = False

    def set_mode(self, mode: int) -> None:
        if self._ok:
            _FAULT_HDR.pack_into(self._mm, 0,
                                 FAULT_SHM_MAGIC, mode,
                                 _FAULT_HDR.unpack_from(self._mm, 0)[2], 0)

    def faults_done(self) -> int:
        if self._ok:
            return _FAULT_HDR.unpack_from(self._mm, 0)[2]
        return 0

    def close(self) -> None:
        if self._ok:
            self.set_mode(FAULT_IDLE)
        if hasattr(self, "_mm"):
            self._mm.close()
        if hasattr(self, "_fd"):
            self._fd.close()


# ── Message definitions (match the test DBC) ────────────────────────────────
#   0x100  EngineStatus   8 B  10 ms   EngineSpeed [0–8000 rpm], Throttle [0–100 %]
#   0x200  TransmStatus   4 B  50 ms   GearPos [0–8], VehicleSpeed [0–300 km/h]
#   0x300  BodyControl    3 B 100 ms   Lights [0/1], Doors [bitmask]

def make_engine(speed_rpm: int = 1500, throttle_pct: int = 25) -> bytes:
    speed_raw    = min(max(int(speed_rpm / 0.25), 0), 65535)
    throttle_raw = min(max(int(throttle_pct / 0.4), 0), 255)
    return struct.pack("<HB5x", speed_raw, throttle_raw)

def make_trans(gear: int = 3, speed_kmh: float = 60.0) -> bytes:
    speed_raw = min(max(int(speed_kmh), 0), 300)
    return struct.pack("<BB2x", gear, speed_raw)

def make_body(lights: int = 1, doors: int = 0) -> bytes:
    return struct.pack("<BBB", lights, doors, 0)


# ── Scenario tasks ──────────────────────────────────────────────────────────

async def task_normal(bus: can.Bus) -> None:
    """Three periodic messages at their correct rates."""
    t_engine = t_trans = t_body = time.monotonic()
    while True:
        now = time.monotonic()
        if now >= t_engine:
            bus.send(can.Message(arbitration_id=0x100, data=make_engine(
                speed_rpm=random.uniform(800, 3000),
                throttle_pct=random.uniform(5, 40)),
                is_extended_id=False))
            t_engine = now + 0.010
        if now >= t_trans:
            bus.send(can.Message(arbitration_id=0x200, data=make_trans(
                gear=3, speed_kmh=random.uniform(50, 80)),
                is_extended_id=False))
            t_trans = now + 0.050
        if now >= t_body:
            bus.send(can.Message(arbitration_id=0x300, data=make_body(),
                is_extended_id=False))
            t_body = now + 0.100
        await asyncio.sleep(0.001)


async def task_babble(bus: can.Bus) -> None:
    """0x100 at 10× its expected rate — triggers BABBLING_TX."""
    print("[fault] babble: 0x100 at 1 ms (10× normal)")
    while True:
        bus.send(can.Message(arbitration_id=0x100,
                             data=make_engine(speed_rpm=2000),
                             is_extended_id=False))
        await asyncio.sleep(0.001)


async def task_missing(bus: can.Bus) -> None:
    """0x100 stops for 3 s then resumes — triggers MISSING_MSG."""
    while True:
        print("[fault] missing: sending 0x100 for 5 s...")
        t_end = time.monotonic() + 5.0
        while time.monotonic() < t_end:
            bus.send(can.Message(arbitration_id=0x100,
                                 data=make_engine(), is_extended_id=False))
            await asyncio.sleep(0.010)
        print("[fault] missing: 0x100 suspended for 3 s")
        await asyncio.sleep(3.0)


async def task_unknown_id(bus: can.Bus) -> None:
    """Random frames with IDs outside the known set — UNEXPECTED_ID."""
    known = {0x100, 0x200, 0x300}
    print("[fault] unknown_id: injecting random IDs every 200 ms")
    while True:
        uid = random.choice([0x050, 0x4A0, 0x6FF, 0x7E0, 0x420])
        while uid in known:
            uid = random.randint(1, 0x7FE)
        data = bytes([random.randint(0, 255) for _ in range(random.randint(1, 8))])
        bus.send(can.Message(arbitration_id=uid, data=data,
                             is_extended_id=False))
        await asyncio.sleep(0.2)


async def task_dlc_mismatch(bus: can.Bus) -> None:
    """0x100 with DLC=4 instead of 8 — DLC_MISMATCH."""
    print("[fault] dlc_mismatch: 0x100 with 4-byte payload every 10 ms")
    while True:
        bus.send(can.Message(arbitration_id=0x100,
                             data=b"\xDE\xAD\xBE\xEF",
                             is_extended_id=False))
        await asyncio.sleep(0.010)


async def task_range_viol(bus: can.Bus) -> None:
    """EngineSpeed > 8000 rpm — RANGE_VIOLATION."""
    print("[fault] range_viol: EngineSpeed=9500 rpm every 10 ms")
    while True:
        bus.send(can.Message(arbitration_id=0x100,
                             data=make_engine(speed_rpm=9500, throttle_pct=95),
                             is_extended_id=False))
        await asyncio.sleep(0.010)


async def task_bus_flood(bus: can.Bus) -> None:
    """High-rate mixed frames — saturates bus load."""
    print("[fault] bus_flood: 500 frames/s")
    ids = [0x001, 0x002, 0x003, 0x004, 0x005]
    while True:
        for aid in ids:
            bus.send(can.Message(arbitration_id=aid,
                                 data=bytes(8), is_extended_id=False))
        await asyncio.sleep(0.010)


async def task_pru_glitch(pru: PruFault) -> None:
    """One-shot physical glitch via PRU then idle."""
    print("[PRU] glitch: single 400 ns dominant pulse")
    pru.set_mode(FAULT_GLITCH)
    await asyncio.sleep(0.1)
    pru.set_mode(FAULT_IDLE)


async def task_pru_glitch_burst(pru: PruFault) -> None:
    """PRU ~5 glitches/s — triggers GLITCH_BURST on sniffer."""
    print("[PRU] glitch_burst: ~5 glitches/s")
    pru.set_mode(FAULT_GLITCH_BURST)
    while True:
        await asyncio.sleep(1.0)
        print(f"[PRU] glitches injected so far: {pru.faults_done()}")


async def task_pru_dominant(pru: PruFault) -> None:
    """PRU holds bus dominant for 80 ms — DOMINANT_RUNAWAY on sniffer."""
    print("[PRU] dominant: bus stuck dominant for 80 ms, then idle")
    pru.set_mode(FAULT_DOMINANT)
    await asyncio.sleep(0.080)
    pru.set_mode(FAULT_IDLE)
    print("[PRU] dominant: released")


async def task_pru_intermittent(pru: PruFault) -> None:
    """PRU random glitch ~1/s."""
    print("[PRU] intermittent: random glitch ~once/s")
    pru.set_mode(FAULT_INTERMITTENT)
    while True:
        await asyncio.sleep(1.0)


# ── Main ─────────────────────────────────────────────────────────────────────

SCENARIO_MAP = {
    "normal":       (task_normal,            False),
    "babble":       (task_babble,            False),
    "missing":      (task_missing,           False),
    "unknown_id":   (task_unknown_id,        False),
    "dlc_mismatch": (task_dlc_mismatch,      False),
    "range_viol":   (task_range_viol,        False),
    "bus_flood":    (task_bus_flood,         False),
    "glitch":       (task_pru_glitch,        True),   # True = needs PRU
    "glitch_burst": (task_pru_glitch_burst,  True),
    "dominant":     (task_pru_dominant,      True),
    "intermittent": (task_pru_intermittent,  True),
}


async def run(scenarios: list[str], channel: str, loop: bool) -> None:
    pru = PruFault()
    bus = can.interface.Bus(channel=channel, interface="socketcan")
    print(f"[CAN] connected to {channel}")

    try:
        while True:
            tasks = []
            for name in scenarios:
                fn, needs_pru = SCENARIO_MAP[name]
                arg = pru if needs_pru else bus
                tasks.append(asyncio.create_task(fn(arg)))

            if loop:
                print(f"[gen] running {scenarios} — Ctrl-C to stop")
                await asyncio.gather(*tasks)
            else:
                # Run for 10 s then exit
                try:
                    await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)
                except asyncio.TimeoutError:
                    for t in tasks:
                        t.cancel()
                break

    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks if 'tasks' in dir() else []:
            t.cancel()
        pru.set_mode(FAULT_IDLE)
        pru.close()
        bus.shutdown()
        print("[gen] shutdown complete")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", "-s", action="append", default=[],
                        choices=list(SCENARIO_MAP), metavar="SCENARIO",
                        help="scenario(s) to run (default: normal)")
    parser.add_argument("--loop", "-l", action="store_true",
                        help="run continuously until Ctrl-C")
    parser.add_argument("--channel", "-c", default="can0",
                        help="SocketCAN interface (default: can0 — P9.19 RX / P9.20 TX)")
    parser.add_argument("--list", action="store_true",
                        help="list available scenarios and exit")
    args = parser.parse_args()

    if args.list:
        print("Available scenarios:")
        for name, (_, pru) in SCENARIO_MAP.items():
            print(f"  {name:<16} {'[PRU]' if pru else ''}")
        return

    scenarios = args.scenario or ["normal"]
    try:
        asyncio.run(run(scenarios, args.channel, args.loop))
    except KeyboardInterrupt:
        print("\n[gen] interrupted")


if __name__ == "__main__":
    main()
