from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .bus_load import BusLoadMonitor
from .correlator import Correlator
from .diag_logger import DiagLogger
from .diagnostics_aggregator import DiagnosticsAggregator
from .frame_store import FrameStore
from .partial_frame_detector import PartialFrameDetector
from .pru_shm import PruShm
from .socketcan_reader import SocketCanReader
from .tec_rec_poller import TecRecPoller

FRONTEND_DIR      = Path(__file__).parent.parent.parent / "frontend"
UPDATE_INTERVAL_S = 0.05   # 20 Hz
TIMEOUT_CHECK_S   = 0.05   # how often to run behavioral timeout scan
DB_PATH           = Path(os.environ.get("CAN_SNIFFER_DB",
                         "/opt/can_sniffer/data/diagnostics.db"))

# Module-level singletons — initialised in lifespan, used by WS handler
_frame_store:    FrameStore
_bus_load:       BusLoadMonitor
_diag:           DiagnosticsAggregator
_logger:         DiagLogger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _frame_store, _bus_load, _diag, _logger

    _frame_store = FrameStore(maxlen=1000)
    _bus_load    = BusLoadMonitor()
    tec_rec      = TecRecPoller()
    _diag        = DiagnosticsAggregator(tec_rec_poller=tec_rec)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _logger = DiagLogger(DB_PATH)
    _logger.open()

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
                pfd.mark_matched()   # prevent false ABORTED_FRAME for this SOF
            _frame_store.append(frame)
            _bus_load.record(frame)
            _diag.ingest_frame(frame)
            _logger.log_frame(frame)
        await asyncio.sleep(0.005)


async def _timeout_loop() -> None:
    while True:
        _diag.check_periodic_timeouts()
        await asyncio.sleep(TIMEOUT_CHECK_S)


def main() -> None:
    uvicorn.run("can_sniffer.server:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
