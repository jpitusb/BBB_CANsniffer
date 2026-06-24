#!/usr/bin/env python3
"""
CAN traffic generator for BBB #2 — generates good and bad CAN messages
to exercise the sniffer's protocol/behavioral diagnostics.

Usage:
    sudo python3 generator.py [--scenario SCENARIO ...] [--loop] [--channel can1]

Scenarios (can combine multiple with --scenario):
  normal         Periodic good traffic (3 messages, correct rates/DLC/signals)
  cmd_resp       Master polls 6 nodes ~10x/sec (0x10N->0x00N); populates latency
  babble         0x100 at 10× its normal 10 ms cycle (triggers BABBLING_TX)
  missing        0x100 disappears for 3 s then resumes (triggers MISSING_MSG)
  unknown_id     Sprinkle frames with IDs not in the DBC (UNEXPECTED_ID)
  dlc_mismatch   0x100 sent with DLC=4 instead of 8 (DLC_MISMATCH)
  range_viol     EngineSpeed set to 9000 rpm (> 8000 max; RANGE_VIOLATION)
  bus_flood      High-rate frames saturating the bus

Hardware (BBB #2):
  Transceiver on P9.24(RX) / P9.26(TX)  →  can1  (DCAN1, same pins as BBB #1)
"""

import argparse
import asyncio
import struct
import sys
import time
import random

try:
    import can
except ImportError:
    sys.exit("python-can not installed. Run: pip3 install python-can")


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

import subprocess as _subprocess


def _cansend(channel: str, arb_id: int, data: bytes) -> bool:
    """Send one CAN frame via cansend. Returns False on error."""
    hex_data = data.hex().upper()
    frame_str = f"{arb_id:03X}#{hex_data}"
    result = _subprocess.run(
        ["cansend", channel, frame_str],
        capture_output=True, timeout=0.5
    )
    # cansend exits 0 even on ENOBUFS; treat any stderr as failure
    if result.stderr:
        return False
    return result.returncode == 0


async def task_normal(bus: can.Bus) -> None:
    """Three messages at ~7 frames/sec total (one every ~140 ms)."""
    channel = bus.channel
    sent = 0
    print(f"[normal] starting on {channel} (~7 fps)", flush=True)
    while True:
        # 0x100 EngineStatus — every 300 ms
        data = make_engine(speed_rpm=random.uniform(800, 3000),
                           throttle_pct=random.uniform(5, 40))
        ok = await asyncio.to_thread(_cansend, channel, 0x100, data)
        if ok:
            sent += 1
            print(f"[normal] sent #{sent}  0x100 engine", flush=True)
        else:
            print("[normal] 0x100 FAILED", flush=True)
        await asyncio.sleep(0.300)

        # 0x200 TransmStatus — every 400 ms
        data = make_trans(gear=3, speed_kmh=random.uniform(50, 80))
        ok = await asyncio.to_thread(_cansend, channel, 0x200, data)
        if ok:
            sent += 1
            print(f"[normal] sent #{sent}  0x200 trans", flush=True)
        else:
            print("[normal] 0x200 FAILED", flush=True)
        await asyncio.sleep(0.400)

        # 0x300 BodyControl — every 500 ms
        data = make_body()
        ok = await asyncio.to_thread(_cansend, channel, 0x300, data)
        if ok:
            sent += 1
            print(f"[normal] sent #{sent}  0x300 body", flush=True)
        else:
            print("[normal] 0x300 FAILED", flush=True)
        await asyncio.sleep(0.500)


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


async def task_cmd_resp(bus: can.Bus) -> None:
    """Emulate a master polling 6 nodes via command/response.

    Master sends request 0x10N to node N; the node replies 0x00N after a short
    processing delay (~1-5 ms). This matches the pattern latency pair
    (request_base 0x100 -> response_base 0x000, lower byte = node id), so the
    dashboard Latency tab and the Graphs latency-trend chart show one series
    per node (Device 0x01 .. 0x06).

    Each node is polled ~10x/sec: 6 nodes x ~10 = ~60 req + ~60 resp ~= 120
    frames/sec. The measured latency per node is the reply delay (~1-5 ms).
    """
    NODES    = [1, 2, 3, 4, 5, 6]
    PERIOD_S = 0.1                      # poll each node ~10x/sec

    def _send(arb_id: int, data: bytes) -> None:
        try:
            bus.send(can.Message(arbitration_id=arb_id, data=data,
                                 is_extended_id=False))
        except can.CanError:
            pass

    async def poll(node: int, phase: float) -> None:
        # Stagger node start phases so the 6 pollers spread evenly across the
        # 100 ms period instead of bursting together.
        await asyncio.sleep(phase)
        req_id, resp_id = 0x100 | node, 0x000 | node
        counter = 0
        while True:
            counter = (counter + 1) & 0xFF
            _send(req_id, bytes([0x40, node, counter]))               # request
            await asyncio.sleep(random.uniform(0.001, 0.005))         # node processing
            _send(resp_id, bytes([0x43, node, counter, 0, 0, 0, 0, 0]))  # response
            await asyncio.sleep(PERIOD_S)

    print(f"[cmd_resp] master polling nodes {NODES} ~10x/sec each "
          f"(0x10N->0x00N, ~120 fps)", flush=True)
    await asyncio.gather(
        *(poll(n, i * PERIOD_S / len(NODES)) for i, n in enumerate(NODES)))


# ── Main ─────────────────────────────────────────────────────────────────────

SCENARIO_MAP = {
    "normal":       task_normal,
    "cmd_resp":     task_cmd_resp,
    "babble":       task_babble,
    "missing":      task_missing,
    "unknown_id":   task_unknown_id,
    "dlc_mismatch": task_dlc_mismatch,
    "range_viol":   task_range_viol,
    "bus_flood":    task_bus_flood,
}


async def run(scenarios: list[str], channel: str, loop: bool) -> None:
    bus = can.interface.Bus(channel=channel, interface="socketcan")
    print(f"[CAN] connected to {channel}")
    # Brief pause after socket open — let the CAN controller settle before
    # sending; simultaneous burst from asyncio.gather can overflow TX queue.
    await asyncio.sleep(0.2)

    tasks: list = []
    try:
        while True:
            tasks = [asyncio.create_task(SCENARIO_MAP[name](bus))
                     for name in scenarios]

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
        for t in tasks:
            t.cancel()
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
    parser.add_argument("--channel", "-c", default="can1",
                        help="SocketCAN interface (default: can1 — P9.24 RX / P9.26 TX)")
    parser.add_argument("--list", action="store_true",
                        help="list available scenarios and exit")
    args = parser.parse_args()

    if args.list:
        print("Available scenarios:")
        for name in SCENARIO_MAP:
            print(f"  {name}")
        return

    scenarios = args.scenario or ["normal"]
    try:
        asyncio.run(run(scenarios, args.channel, args.loop))
    except KeyboardInterrupt:
        print("\n[gen] interrupted")


if __name__ == "__main__":
    main()
