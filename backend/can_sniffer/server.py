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
from .timing_stats import TimingStatsCollector
from .trigger_capture import TriggerCapture, TriggerCondition

FRONTEND_DIR      = Path(__file__).parent.parent.parent / "frontend"
UPDATE_INTERVAL_S = 0.05   # 20 Hz
TIMEOUT_CHECK_S   = 0.05
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _frame_store, _bus_load, _diag, _logger, _timing, _latency, _trigger

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

    pru        = PruShm()
    correlator = Correlator(epoch_offset_ns=pru.epoch_offset_ns)
    pfd        = PartialFrameDetector()
    reader     = SocketCanReader()

    pfd.add_callback(_diag.ingest_abort)

    tasks = [
        asyncio.create_task(_pru_reader_loop(pru, correlator, pfd)),
        asyncio.create_task(_can_reader_loop(reader, correlator)),
        asyncio.create_task(_drain_loop(correlator, pfd)),
        asyncio.create_task(pfd.run()),
        asyncio.create_task(tec_rec.run()),
        asyncio.create_task(_timeout_loop()),
        asyncio.create_task(_logger.run()),
        asyncio.create_task(_capture_logger_loop()),
    ]
    try:
        yield
    finally:
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
    last_sent = 0
    try:
        while True:
            frames = _frame_store.snapshot()
            n      = len(frames)
            # last_sent tracks position in a monotonically-growing list;
            # once the circular buffer (maxlen=1000) is full it stays at 1000.
            # When last_sent >= n the buffer has wrapped — send the whole
            # snapshot so the client gets a full refresh.
            if last_sent >= n:
                last_sent = 0
            new_frames = [f.to_dict() for f in frames[last_sent:]]
            last_sent  = n
            await websocket.send_text(json.dumps({
                "type":     "update",
                "frames":   new_frames,
                "bus_load": _bus_load.current(),
                "diag":     _diag.snapshot(),
                "timing":   _timing.snapshot(),
                "latency":  _latency.snapshot(),
                "trigger":  _trigger.snapshot(),
            }))
            await asyncio.sleep(UPDATE_INTERVAL_S)
    except WebSocketDisconnect:
        pass


async def _pru_reader_loop(pru: PruShm, correlator: Correlator,
                           pfd: PartialFrameDetector) -> None:
    while True:
        for event in pru.drain():
            correlator.ingest_pru(event)
            pfd.ingest_pru(event)
            _diag.ingest_pru_event(event)
            _logger.log_pru_event(event)
        await asyncio.sleep(0.001)


async def _can_reader_loop(reader: SocketCanReader, correlator: Correlator) -> None:
    while True:
        msg = await asyncio.to_thread(reader.recv_one)
        if msg is not None:
            if msg.is_error_frame:
                err = _diag.ingest_error_frame(msg)
                if err is not None:
                    _logger.log_error(err)
            else:
                correlator.ingest_frame(msg)


async def _drain_loop(correlator: Correlator, pfd: PartialFrameDetector) -> None:
    while True:
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
        await asyncio.sleep(0.005)


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
