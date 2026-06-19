from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .bus_load import BusLoadMonitor
from .correlator import Correlator
from .diag_logger import DiagLogger
from .diagnostics_aggregator import DiagnosticsAggregator
from .frame_store import FrameStore
from .latency_monitor import ExplicitPair, LatencyMonitor, PatternPair, load_pairs, save_pairs
from .partial_frame_detector import PartialFrameDetector
from .pru_shm import PruShm
from .sequence_export import export_svg_from_session
from .socketcan_reader import SocketCanReader
from .tec_rec_poller import TecRecPoller
from .system_health import SystemHealthMonitor
from .timing_stats import TimingStatsCollector
from .trigger_capture import TriggerCapture, TriggerCondition

FRONTEND_DIR      = Path(__file__).parent.parent.parent / "frontend"
UPDATE_INTERVAL_S = 0.2    # 5 Hz — ample for a human-watched diagnostic view
TIMEOUT_CHECK_S   = 0.1
# Max frames serialised into a single WS update. A busy 1 Mbit/s bus produces
# ~2360 frames/s (~470 per 5 Hz tick); a human-watched view can't use that many,
# and every frame is still persisted to the DB by DiagLogger. Capping the live
# stream bounds the dominant to_dict + json.dumps cost on the single-core ARM.
MAX_WS_FRAMES_PER_TICK = 100
DB_PATH           = Path(os.environ.get("CAN_SNIFFER_DB",
                         "/opt/can_sniffer/data/diagnostics.db"))
PAIRS_PATH        = Path("/opt/can_sniffer/data/latency_pairs.json")

# Module-level singletons — initialised in lifespan, used by WS handler and REST
_frame_store:    FrameStore
_bus_load:       BusLoadMonitor
_diag:           DiagnosticsAggregator
_logger:         DiagLogger
_timing:         TimingStatsCollector
_latency:        LatencyMonitor
_trigger:        TriggerCapture
_health:         SystemHealthMonitor


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _frame_store, _bus_load, _diag, _logger, _timing, _latency, _trigger, _health

    _frame_store = FrameStore(maxlen=1000)
    _bus_load    = BusLoadMonitor()
    tec_rec      = TecRecPoller()
    _diag        = DiagnosticsAggregator(tec_rec_poller=tec_rec)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _logger = DiagLogger(DB_PATH)
    _logger.open()

    explicit, patterns = load_pairs(PAIRS_PATH)
    _timing  = TimingStatsCollector()
    _latency = LatencyMonitor(explicit, patterns)
    _trigger = TriggerCapture()
    _health  = SystemHealthMonitor()

    pru        = PruShm()
    correlator = Correlator(epoch_offset_ns=pru.epoch_offset_ns)
    pfd        = PartialFrameDetector()
    reader     = SocketCanReader()

    pfd.add_callback(_diag.ingest_abort)

    # Drain the raw CAN socket directly from the event loop. add_reader fires
    # this in the main thread whenever the fd is readable — no executor thread,
    # so no GIL ping-pong on the single core. recv_batch drains all queued
    # frames; if more than its cap remain, the fd stays readable and we refire.
    loop = asyncio.get_event_loop()

    def _on_can_readable() -> None:
        for msg in reader.recv_batch():
            if msg.is_error_frame:
                err = _diag.ingest_error_frame(msg)
                if err is not None:
                    _logger.log_error(err)
            else:
                correlator.ingest_frame(msg)

    loop.add_reader(reader.fileno(), _on_can_readable)

    tasks = [
        asyncio.create_task(_ingest_loop(pru, correlator, pfd)),
        asyncio.create_task(tec_rec.run()),
        asyncio.create_task(_timeout_loop()),
        asyncio.create_task(_logger.run()),
        asyncio.create_task(_capture_logger_loop()),
    ]
    try:
        yield
    finally:
        loop.remove_reader(reader.fileno())
        for t in tasks:
            t.cancel()
        pru.close()
        reader.close()
        _logger.close()


app = FastAPI(lifespan=lifespan)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    last_total = 0
    try:
        while True:
            # Send only frames appended since the last tick. (The previous
            # index-based scheme re-sent and re-serialised all 1000 frames
            # every tick once the ring buffer filled — the dominant CPU cost.)
            new_objs, last_total = _frame_store.since(last_total)
            # Cap frames per tick (see MAX_WS_FRAMES_PER_TICK). On a burst we
            # send the most recent ones; the full record stays in the DB.
            if len(new_objs) > MAX_WS_FRAMES_PER_TICK:
                new_objs = new_objs[-MAX_WS_FRAMES_PER_TICK:]
            new_frames = [f.to_dict() for f in new_objs]
            await websocket.send_text(json.dumps({
                "type":     "update",
                "frames":   new_frames,
                "bus_load": _bus_load.current(),
                "diag":     _diag.snapshot(),
                "timing":   _timing.snapshot(),
                "latency":  _latency.snapshot(),
                "trigger":  _trigger.snapshot(),
                "health":   _health.snapshot(),
            }))
            await asyncio.sleep(UPDATE_INTERVAL_S)
    except WebSocketDisconnect:
        pass


async def _ingest_loop(pru: PruShm, correlator: Correlator,
                       pfd: PartialFrameDetector) -> None:
    """Coalesced PRU-drain + matched-frame-drain + abort sweep.

    Replaces three separate polling tasks (PRU reader, drain loop, pfd.run)
    with one loop, cutting event-loop wakeups ~10x (the dominant CPU cost on
    the single-core ARM).

    Safe at 20 ms because the PRU writes each SOF to DDR at frame *start*,
    before the kernel delivers the CAN frame to userspace — so the matching
    PRU event is already queued when a frame arrives, and _try_match (run on
    PRU ingest) fires before drain_matched's stale-flush in the same
    iteration. Correlation uses timestamps, not queue residence time, so the
    longer interval only delays dashboard display by <=20 ms.
    """
    while True:
        for event in pru.drain():
            correlator.ingest_pru(event)
            pfd.ingest_pru(event)
            _diag.ingest_pru_event(event)
            _logger.log_pru_event(event)
        for frame in correlator.drain_matched():
            if frame.pru_ts_ns is not None:
                pfd.mark_matched()
            _frame_store.append(frame)
            _bus_load.record(frame)
            _diag.ingest_frame(frame)
            _logger.log_frame(frame)
            _timing.ingest(frame)
            _latency.ingest(frame)
            _trigger.ingest(frame, _bus_load.current())
        pfd.sweep_timeouts()
        await asyncio.sleep(0.02)


async def _capture_logger_loop() -> None:
    while True:
        cap = _trigger.pop_completed()
        if cap:
            _logger.log_capture(cap.to_dict())
        await asyncio.sleep(0.1)


async def _timeout_loop() -> None:
    while True:
        _diag.check_periodic_timeouts()
        await asyncio.sleep(TIMEOUT_CHECK_S)


# ── Trigger endpoints ─────────────────────────────────────────────────────────

@app.post("/api/trigger/arm")
async def trigger_arm(body: dict = Body(...)) -> JSONResponse:
    cond = TriggerCondition(
        type               = body.get("type", "manual"),
        arb_id             = body.get("arb_id"),
        bus_load_threshold = body.get("bus_load_threshold"),
    )
    _trigger.arm(cond)
    return JSONResponse({"status": "armed", "condition": cond.to_dict()})


@app.post("/api/trigger/disarm")
async def trigger_disarm() -> JSONResponse:
    _trigger.disarm()
    return JSONResponse({"status": "idle"})


@app.post("/api/trigger/fire")
async def trigger_fire() -> JSONResponse:
    _trigger.fire()
    return JSONResponse({"status": _trigger.state})


@app.get("/api/captures")
async def list_captures() -> JSONResponse:
    return JSONResponse([c.summary() for c in reversed(_trigger.captures)])


@app.get("/api/captures/{capture_id}")
async def get_capture(capture_id: str) -> JSONResponse:
    cap = _trigger.get_capture(capture_id)
    if cap is None:
        raise HTTPException(status_code=404, detail="Capture not found")
    return JSONResponse(cap.to_dict())


@app.get("/api/captures/{capture_id}/svg")
async def get_capture_svg(capture_id: str) -> Response:
    cap = _trigger.get_capture(capture_id)
    if cap is None:
        raise HTTPException(status_code=404, detail="Capture not found")
    explicit, patterns = load_pairs(PAIRS_PATH)
    pairs_raw = [{"request_id": p.request_id, "response_id": p.response_id,
                  "label": p.label} for p in explicit] + \
                [{"request_base": p.request_base, "response_base": p.response_base,
                  "label_template": p.label_template} for p in patterns]
    svg = export_svg_from_session(cap.to_dict(), pairs_raw)
    return Response(content=svg, media_type="image/svg+xml")


# ── Latency pair endpoints ────────────────────────────────────────────────────

@app.get("/api/latency/pairs")
async def get_latency_pairs() -> JSONResponse:
    if PAIRS_PATH.exists():
        return JSONResponse(json.loads(PAIRS_PATH.read_text()))
    return JSONResponse([])


@app.put("/api/latency/pairs")
async def set_latency_pairs(body: list = Body(...)) -> JSONResponse:
    save_pairs(PAIRS_PATH, body)
    explicit, patterns = load_pairs(PAIRS_PATH)
    _latency.set_pairs(explicit, patterns)
    return JSONResponse({"status": "ok", "count": len(body)})


# ── Annotation endpoint ───────────────────────────────────────────────────────

@app.post("/api/frames/annotate")
async def annotate_frame(body: dict = Body(...)) -> JSONResponse:
    kernel_ts = float(body["kernel_ts"])
    arb_id    = int(body["arb_id"])
    note      = str(body["note"])[:500]
    _logger.log_annotation(kernel_ts, arb_id, note)
    return JSONResponse({"status": "ok"})


# ── Timing reset ──────────────────────────────────────────────────────────────

@app.post("/api/timing/reset")
async def reset_timing() -> JSONResponse:
    _timing.reset()
    return JSONResponse({"status": "ok"})


def main() -> None:
    uvicorn.run("can_sniffer.server:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
