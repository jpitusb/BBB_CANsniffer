from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Optional

from .models import Alert, EnrichedFrame, ErrorEvent

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA page_size=4096;
PRAGMA wal_autocheckpoint=1000;

CREATE TABLE IF NOT EXISTS can_frames (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    can_id      INTEGER NOT NULL,
    is_extended INTEGER NOT NULL DEFAULT 0,
    dlc         INTEGER NOT NULL,
    data        BLOB
);
CREATE INDEX IF NOT EXISTS idx_frames_ts    ON can_frames(ts);
CREATE INDEX IF NOT EXISTS idx_frames_id    ON can_frames(can_id);

CREATE TABLE IF NOT EXISTS error_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    error_class INTEGER NOT NULL,
    error_data  BLOB,
    tec         INTEGER,
    rec         INTEGER,
    bus_state   TEXT
);
CREATE INDEX IF NOT EXISTS idx_errors_ts ON error_events(ts);

CREATE TABLE IF NOT EXISTS behavioral_alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    severity     TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    can_id       INTEGER,
    signal_name  TEXT,
    detail       TEXT    NOT NULL,
    resolved     INTEGER NOT NULL DEFAULT 0,
    resolved_ts  REAL
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts       ON behavioral_alerts(ts);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON behavioral_alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_can_id   ON behavioral_alerts(can_id);

CREATE TABLE IF NOT EXISTS bus_state_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL    NOT NULL,
    tec     INTEGER NOT NULL,
    rec     INTEGER NOT NULL,
    state   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_busstate_ts ON bus_state_log(ts);

CREATE TABLE IF NOT EXISTS captures (
    id             TEXT    PRIMARY KEY,
    trigger_ts     REAL    NOT NULL,
    condition_type TEXT    NOT NULL,
    frame_count    INTEGER NOT NULL,
    data           TEXT    NOT NULL   -- JSON {pre:[...], post:[...]}
);
CREATE INDEX IF NOT EXISTS idx_captures_ts ON captures(trigger_ts);

CREATE TABLE IF NOT EXISTS frame_annotations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kernel_ts  REAL    NOT NULL,
    arb_id     INTEGER NOT NULL,
    note       TEXT    NOT NULL,
    created_ts REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ann_ts ON frame_annotations(kernel_ts);
"""

# Retention: frames 7 days, errors 7 days, alerts 90 days
_RETENTION_SQL = """
DELETE FROM can_frames        WHERE ts < (strftime('%s','now') - 86400 * 7);
DELETE FROM error_events      WHERE ts < (strftime('%s','now') - 86400 * 7);
DELETE FROM behavioral_alerts WHERE ts < (strftime('%s','now') - 86400 * 90);
DELETE FROM bus_state_log     WHERE ts < (strftime('%s','now') - 86400 * 30);
DELETE FROM captures          WHERE trigger_ts < (strftime('%s','now') - 86400 * 30);
DELETE FROM frame_annotations WHERE created_ts < (strftime('%s','now') - 86400 * 90);
"""

_FLUSH_INTERVAL_S  = 0.05   # 20 Hz batch writes
_RETAIN_INTERVAL_S = 86400  # daily retention purge


class DiagLogger:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn:   Optional[sqlite3.Connection] = None
        # Buffers are appended from the event-loop thread and flushed from a
        # worker thread (asyncio.to_thread). _lock guards every append and the
        # buffer swap in _flush so no record is lost in the gap between the DB
        # write and the buffer reset.
        self._lock = Lock()
        self._frame_buf:  list = []
        self._error_buf:  list = []
        self._alert_buf:  list = []
        self._busst_buf:  list = []
        self._cap_buf:    list = []
        self._ann_buf:    list = []

    def open(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._flush()
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------

    def log_frame(self, frame: EnrichedFrame) -> None:
        with self._lock:
            self._frame_buf.append((
                frame.kernel_ts,
                frame.arb_id,
                int(frame.is_extended),
                frame.dlc,
                frame.data,
            ))

    def log_error(self, err: ErrorEvent, bus_state: Optional[str] = None) -> None:
        with self._lock:
            self._error_buf.append((
                err.ts,
                err.error_class,
                err.raw_data,
                err.tec,
                err.rec,
                bus_state,
            ))

    def log_alert(self, alert: Alert) -> None:
        with self._lock:
            self._alert_buf.append((
                alert.ts,
                alert.severity.value,
                alert.category.value,
                alert.can_id,
                alert.signal_name,
                alert.msg,
                int(alert.resolved),
                alert.resolved_ts,
            ))

    def log_bus_state(self, tec: int, rec: int, state: str) -> None:
        with self._lock:
            self._busst_buf.append((time.time(), tec, rec, state))

    def log_capture(self, session: dict) -> None:
        import json
        with self._lock:
            self._cap_buf.append((
                session["id"],
                session["trigger_ts"],
                session["condition_type"],
                session["frame_count"],
                json.dumps({"pre": session["pre_frames"], "post": session["post_frames"]}),
            ))

    def log_annotation(self, kernel_ts: float, arb_id: int, note: str) -> None:
        with self._lock:
            self._ann_buf.append((kernel_ts, arb_id, note, time.time()))

    # ------------------------------------------------------------------

    async def run(self) -> None:
        last_retain = time.time()
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL_S)
            await asyncio.to_thread(self._flush)
            if time.time() - last_retain > _RETAIN_INTERVAL_S:
                await asyncio.to_thread(self._purge)
                last_retain = time.time()

    def _flush(self) -> None:
        if not self._conn:
            return
        # Detach the current buffers under the lock and replace them with fresh
        # lists, so concurrent appends from the event-loop thread land in the
        # new buffers and are never dropped between the write and the reset.
        with self._lock:
            frame_buf, self._frame_buf = self._frame_buf, []
            error_buf, self._error_buf = self._error_buf, []
            alert_buf, self._alert_buf = self._alert_buf, []
            busst_buf, self._busst_buf = self._busst_buf, []
            cap_buf,   self._cap_buf   = self._cap_buf,   []
            ann_buf,   self._ann_buf   = self._ann_buf,   []
        with self._conn:
            if frame_buf:
                self._conn.executemany(
                    "INSERT INTO can_frames(ts,can_id,is_extended,dlc,data)"
                    " VALUES(?,?,?,?,?)", frame_buf)
            if error_buf:
                self._conn.executemany(
                    "INSERT INTO error_events(ts,error_class,error_data,tec,rec,bus_state)"
                    " VALUES(?,?,?,?,?,?)", error_buf)
            if alert_buf:
                self._conn.executemany(
                    "INSERT INTO behavioral_alerts"
                    "(ts,severity,category,can_id,signal_name,detail,resolved,resolved_ts)"
                    " VALUES(?,?,?,?,?,?,?,?)", alert_buf)
            if busst_buf:
                self._conn.executemany(
                    "INSERT INTO bus_state_log(ts,tec,rec,state) VALUES(?,?,?,?)",
                    busst_buf)
            if cap_buf:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO captures"
                    "(id,trigger_ts,condition_type,frame_count,data) VALUES(?,?,?,?,?)",
                    cap_buf)
            if ann_buf:
                self._conn.executemany(
                    "INSERT INTO frame_annotations(kernel_ts,arb_id,note,created_ts)"
                    " VALUES(?,?,?,?)",
                    ann_buf)

    def _purge(self) -> None:
        if self._conn:
            self._conn.executescript(_RETENTION_SQL)
            self._conn.commit()
