# Session Handoff — PRU removal + critical fixes

> Portable snapshot of the work + Claude session memory so it can be picked up
> on another computer. **Safe to delete before merging** — this file is not part
> of the actual change. On the other machine, you (or Claude) can re-save the
> memory sections below into that machine's Claude memory if you want auto-recall.

## Where things stand

- Branch **`simplify/remove-pru-and-critical-fixes`** is pushed to `origin`
  (commit `b303487`), based on `master` @ `936b7c1`.
- Open a PR: https://github.com/jpitusb/BBB_CANsniffer/pull/new/simplify/remove-pru-and-critical-fixes
- Tests pass: 23/23 (`cd backend && python -m pytest -q`).

### ⚠️ Open question — your "other changes"
You said you uploaded other changes to GitHub, but as of this session `origin`
only has `master` at `936b7c1` (the same commit this branch is based on) and no
other branches. Your other work may have gone to a fork or a different remote,
or the push didn't land. Verify where it is; if it should be in `master`, this
branch may need a rebase onto the updated `master` afterward (watch for
conflicts in `server.py`, `diag_logger.py`, `diagnostics_aggregator.py`,
`models.py`, `README.md`).

## What was done on this branch

**PRU fully removed** (project is now a plain SocketCAN sniffer). Deleted `pru/`,
`dts/BB-PRU0-CAN-TS-00A0.dts`, `pru_shm.py`, `partial_frame_detector.py`,
`signal_quality_monitor.py`, `setup_pru.sh`, `pru-loader.service`, PRU docs, the
`pru_events` table + `pru_ts_ns`/`is_aborted` columns, the frontend PRU
column / Δ histogram / Signal Quality card, and PRU fault injection from the
BBB#2 generator. `correlator.py` is now a pass-through frame-intake buffer (also
removes the old `maxlen=256` silent-drop bug). Timestamps now come from kernel
`SO_TIMESTAMP` (microsecond). Build/boot/deploy wiring simplified. README
rewritten. Net ~2100 lines removed.

**Three pre-existing CRITICAL bugs fixed** (entangled in the same files, hence one
commit):
1. `BehavioralMonitor` was never instantiated → now loaded from `CAN_SNIFFER_DBC`
   env var and wired into the aggregator (gracefully disabled + logged if no DBC).
2. Alerts and bus state were never persisted → all alerts routed through an
   `on_alert` callback to SQLite + a 1 Hz bus-state logger.
3. `DiagLogger._flush` dropped records via an append/clear race → buffers now
   swap under a lock before writing.

## Not done yet (HIGH-severity review findings, out of scope so far)

- WebSocket handler is single-client and lets non-disconnect exceptions kill the
  loop silently; no `try/finally` cleanup.
- REST input validation missing on `/api/frames/annotate` and `/api/trigger/arm`
  (unhandled 500s; string `arb_id` silently never matches).
- Frontend XSS: frame data / annotations / alert text rendered via `innerHTML`
  unescaped (`frontend/app.js`).
- `uPlot.iife.min.js` / `uPlot.min.css` are git-ignored and absent from the repo
  → broken Graphs tab on a fresh clone; no CDN fallback.
- TEC/REC double-write: error-frame path and `TecRecPoller` both set the same
  fields, causing flapping.
- Test coverage gaps: `server.py`, `socketcan_reader.py`, `diag_logger.py`,
  `latency_monitor.py`, `timing_stats.py`, `trigger_capture.py` untested.

## Claude session memory (to re-save on the other machine if wanted)

### pru-removed (type: project)
The PRU was deemed not useful and removed on `simplify/remove-pru-and-critical-fixes`.
Why: firmware only emitted SOF events (~100/s), never emitted advertised
GLITCH/DOMINANT_RUNAWAY, unvalidated accuracy claim — poor cost-to-value vs. two
PRU toolchains, `/dev/mem`, DDR memmap, kernel lock-in. Lost: nanosecond
timestamps, glitch/runaway/aborted-frame detection. Retained: all
protocol/timing diagnostics. Committed `b303487`, pushed 2026-06-23.

### behavioral-needs-dbc (type: reference)
Behavioral monitoring loads from the `CAN_SNIFFER_DBC` env var. If unset/missing/
malformed, `_load_behavioral_monitor()` returns None and behavioral checks are
disabled (warning logged); everything else still runs. No `.dbc` is committed, so
behavioral monitoring is off by default until a DBC path is provided. This was a
CRITICAL bug fixed during PRU removal (monitor previously never instantiated).
