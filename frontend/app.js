"use strict";

// ── Config ──────────────────────────────────────────────────────────────────
const WS_URL        = `ws://${location.host}/ws`;
const MAX_DOM_ROWS  = 200;
const RING_SIZE     = 10_000;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS  = 30_000;

// ── State ───────────────────────────────────────────────────────────────────
let ringBuffer   = [];    // circular array of enriched frame objects
let ringHead     = 0;     // index of oldest entry
let ringCount    = 0;
let paused       = false;
let filterText   = "";
let lastIdTs     = new Map();   // arb_id -> last pru_ts_ns for delta calculation
let seenIds      = new Set();
let totalFrames  = 0;
let totalErrors  = 0;
let totalAborts  = 0;
let reconnectMs  = RECONNECT_BASE_MS;

// ── DOM refs ─────────────────────────────────────────────────────────────────
const connDot       = document.getElementById("conn-dot");
const busStateBadge = document.getElementById("bus-state-badge");
const tecRec        = document.getElementById("tec-rec");
const errRate       = document.getElementById("err-rate");
const alertBadge    = document.getElementById("alert-badge");
const busLoadLabel  = document.getElementById("bus-load-label");
const busLoadFill   = document.getElementById("bus-load-fill");
const filterInput   = document.getElementById("filter-id");
const tbody         = document.getElementById("frame-tbody");
const btnPause      = document.getElementById("btn-pause");
const btnClear      = document.getElementById("btn-clear");

// ── Tabs ─────────────────────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab, .panel").forEach(el => el.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(tab.dataset.panel).classList.add("active");
  });
});

// ── Controls ─────────────────────────────────────────────────────────────────
btnPause.addEventListener("click", () => {
  paused = !paused;
  btnPause.classList.toggle("paused", paused);
  btnPause.textContent = paused ? "Resume" : "Pause";
  if (!paused) renderTable();
});

btnClear.addEventListener("click", () => {
  ringBuffer = []; ringHead = 0; ringCount = 0;
  lastIdTs.clear();
  tbody.replaceChildren();
});

filterInput.addEventListener("input", () => {
  filterText = filterInput.value.trim().toLowerCase();
  renderTable();
});

// ── WebSocket ─────────────────────────────────────────────────────────────────
function connect() {
  const ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    connDot.className = "dot connected";
    reconnectMs = RECONNECT_BASE_MS;
  };

  ws.onclose = () => {
    connDot.className = "dot disconnected";
    setTimeout(connect, reconnectMs);
    reconnectMs = Math.min(reconnectMs * 2, RECONNECT_MAX_MS);
  };

  ws.onmessage = ({ data }) => {
    let msg;
    try { msg = JSON.parse(data); } catch { return; }
    if (msg.type !== "update") return;

    if (msg.frames?.length) {
      for (const f of msg.frames) ingestFrame(f);
      if (!paused) renderTable();
    }

    if (msg.bus_load !== undefined) updateBusLoad(msg.bus_load);
    if (msg.diag)                   updateDiag(msg.diag);
  };
}

// ── Frame ingestion ───────────────────────────────────────────────────────────
function ingestFrame(f) {
  const idKey = f.arb_id;
  const prev  = lastIdTs.get(idKey);
  f.delta_us  = (prev !== undefined && f.pru_ts_ns !== null)
    ? Math.round((f.pru_ts_ns - prev) / 1000)
    : null;
  if (f.pru_ts_ns !== null) lastIdTs.set(idKey, f.pru_ts_ns);

  // Assign stable hue per ID for row colouring
  const idNum = parseInt(f.arb_id, 16);
  f._hue = idNum % 360;

  // Circular ring buffer push
  if (ringCount < RING_SIZE) {
    ringBuffer.push(f);
    ringCount++;
  } else {
    ringBuffer[ringHead] = f;
    ringHead = (ringHead + 1) % RING_SIZE;
  }

  seenIds.add(f.arb_id);
  totalFrames++;
  document.getElementById("stat-total").textContent = totalFrames;
  document.getElementById("stat-ids").textContent   = seenIds.size;
}

// ── Table render ──────────────────────────────────────────────────────────────
function* visibleFrames() {
  const n = ringCount;
  for (let i = 0; i < n; i++) {
    const frame = ringBuffer[(ringHead + i) % RING_SIZE];
    if (!filterText || frame.arb_id.toLowerCase().startsWith(filterText)) yield frame;
  }
}

function renderTable() {
  const frames = [...visibleFrames()].slice(-MAX_DOM_ROWS);
  tbody.replaceChildren();
  const frag = document.createDocumentFragment();
  for (const f of frames) {
    const tr = document.createElement("tr");
    tr.style.background = `hsla(${f._hue},30%,18%,0.5)`;
    tr.innerHTML =
      `<td>${f.pru_ts_ns ?? f.kernel_ts.toFixed(6)}</td>` +
      `<td>${f.arb_id}</td>` +
      `<td>${f.dlc}</td>` +
      `<td>${f.data}</td>` +
      `<td>${f.delta_us !== null ? f.delta_us : "—"}</td>`;
    frag.appendChild(tr);
  }
  tbody.appendChild(frag);
  // Scroll to bottom unless paused
  const wrap = tbody.closest(".table-wrap");
  if (wrap) wrap.scrollTop = wrap.scrollHeight;
}

// ── Bus load ─────────────────────────────────────────────────────────────────
function updateBusLoad(fraction) {
  const pct = Math.round(fraction * 100);
  busLoadLabel.textContent = `Bus: ${pct}%`;
  busLoadFill.style.width  = `${pct}%`;
  busLoadFill.style.background =
    pct > 80 ? "var(--critical)" : pct > 50 ? "var(--warn)" : "var(--ok)";
}

// ── Diagnostics ───────────────────────────────────────────────────────────────
function updateDiag(diag) {
  if (!diag) return;

  // Bus health header
  const bh = diag.bus_health || {};
  updateBusStateBadge(bh.state);
  tecRec.textContent    = `TEC=${bh.tec ?? "—"} REC=${bh.rec ?? "—"}`;
  errRate.textContent   = `${bh.error_frames_1s ?? 0} err/s`;

  // Protocol error counts
  const pe = diag.protocol_errors || {};
  document.getElementById("cnt-bit").textContent   = pe.bit_errors   ?? 0;
  document.getElementById("cnt-stuff").textContent = pe.stuff_errors ?? 0;
  document.getElementById("cnt-crc").textContent   = pe.crc_errors   ?? 0;
  document.getElementById("cnt-form").textContent  = pe.form_errors  ?? 0;
  document.getElementById("cnt-ack").textContent   = pe.ack_errors   ?? 0;

  // Signal quality
  const sq = diag.signal_quality || {};
  document.getElementById("cnt-glitch").textContent = sq.glitches_1s ?? 0;
  document.getElementById("cnt-abort").textContent  = sq.aborts_1s   ?? 0;
  const rb = document.getElementById("runaway-badge");
  rb.textContent  = sq.dominant_runaway ? "ACTIVE" : "OK";
  rb.className    = "badge " + (sq.dominant_runaway ? "badge-busoff" : "badge-ok");

  // Missing messages
  const missing = (diag.behavioral || {}).missing_msgs || [];
  const missingDiv = document.getElementById("missing-msgs");
  if (missing.length === 0) {
    missingDiv.innerHTML = "<em>None</em>";
  } else {
    missingDiv.replaceChildren();
    for (const m of missing) {
      const row = document.createElement("div");
      row.className = "missing-row";
      row.innerHTML =
        `<span class="missing-name">${m.name} (${m.id})</span>` +
        `<span class="missing-overdue">${m.overdue_ms.toFixed(0)} ms late</span>`;
      missingDiv.appendChild(row);
    }
  }

  // Alerts
  const alerts = diag.alerts || [];
  const unresolved = alerts.filter(a => !a.resolved);
  alertBadge.textContent = unresolved.length;
  alertBadge.classList.toggle("hidden", unresolved.length === 0);

  const feed = document.getElementById("alert-feed");
  feed.replaceChildren();
  for (const a of unresolved.slice(-20)) {
    const row = document.createElement("div");
    row.className = "alert-row";
    const age = Math.round(Date.now() / 1000 - a.ts);
    row.innerHTML =
      `<span class="sev-badge sev-${a.severity}">${a.severity}</span>` +
      `<span class="alert-msg">${a.msg}</span>` +
      `<span class="alert-ts">${age}s ago</span>`;
    feed.appendChild(row);
  }

  // Session stats
  totalErrors = Object.values(pe).reduce((s, v) => s + v, 0);
  totalAborts = (sq.aborts_1s ?? 0);  // approximation; logger has the real count
  document.getElementById("stat-errs").textContent   = totalErrors;
  document.getElementById("stat-aborts").textContent = totalAborts;
}

function updateBusStateBadge(state) {
  const cls = {
    "error-active":   ["ACTIVE",        "badge-active"],
    "error-warning":  ["WARN",          "badge-warning"],
    "error-passive":  ["ERROR PASSIVE", "badge-passive"],
    "bus-off":        ["BUS OFF",       "badge-busoff"],
  }[state] || ["UNKNOWN", "badge-unknown"];
  busStateBadge.textContent = cls[0];
  busStateBadge.className   = "badge " + cls[1];
}

// ── Init ─────────────────────────────────────────────────────────────────────
connect();
