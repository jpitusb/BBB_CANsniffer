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
let _activePanel = "panel-frames";

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    const leaving = _activePanel;
    document.querySelectorAll(".tab, .panel").forEach(el => el.classList.remove("active"));
    tab.classList.add("active");
    const entering = tab.dataset.panel;
    document.getElementById(entering).classList.add("active");
    _activePanel = entering;

    // Notify graphs module of visibility changes
    if (leaving === "panel-graphs" && typeof graphsTabDeactivated !== "undefined") {
      graphsTabDeactivated();
    }
    if (entering === "panel-graphs" && typeof graphsTabActivated !== "undefined") {
      graphsTabActivated();
    }
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
    if (msg.timing)                 updateTiming(msg.timing);
    if (msg.latency)                updateLatency(msg.latency);
    if (msg.trigger)                updateTrigger(msg.trigger);
    if (msg.health)                 updateHealth(msg.health);

    // Feed graphs module regardless of active tab (data must accumulate)
    if (typeof graphsIngest !== "undefined") graphsIngest(msg);
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
      `<td>${f.pru_ts_ns ?? "—"}</td>` +
      `<td>${f.kernel_ts != null ? f.kernel_ts.toFixed(6) : "—"}</td>` +
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
  const pct = fraction * 100;
  // One decimal so low-traffic buses (a handful of frames/sec at 500 kbit/s
  // is well under 1%) still show a non-zero value instead of rounding to 0%.
  busLoadLabel.textContent = `Bus: ${pct.toFixed(1)}%`;
  busLoadFill.style.width  = `${Math.min(pct, 100)}%`;
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

// ── Timing ───────────────────────────────────────────────────────────────────
function updateTiming(timing) {
  const tbody = document.getElementById("timing-tbody");
  if (!tbody || !timing) return;
  const rows = Object.entries(timing)
    .sort((a, b) => b[1].count - a[1].count);
  tbody.replaceChildren();
  const frag = document.createDocumentFragment();
  for (const [id, s] of rows) {
    const jitterWarn = s.interval_mean_ms && s.jitter_rms_ms > s.interval_mean_ms * 0.1;
    const tr = document.createElement("tr");
    if (jitterWarn) tr.className = "timing-jitter-warn";
    const fmt = v => v === null || v === undefined ? "—" : v.toFixed(3);
    tr.innerHTML =
      `<td>${id}</td>` +
      `<td>${s.count}</td>` +
      `<td>${s.frames_per_sec ?? "—"}</td>` +
      `<td>${fmt(s.interval_min_ms)}</td>` +
      `<td>${fmt(s.interval_max_ms)}</td>` +
      `<td>${fmt(s.interval_mean_ms)}</td>` +
      `<td>${fmt(s.interval_std_ms)}</td>` +
      `<td>${fmt(s.interval_p95_ms)}</td>` +
      `<td>${fmt(s.jitter_rms_ms)}</td>`;
    frag.appendChild(tr);
  }
  tbody.appendChild(frag);
}

// ── Latency ───────────────────────────────────────────────────────────────────
function updateLatency(latency) {
  const tbody = document.getElementById("latency-tbody");
  if (!tbody || !latency) return;
  tbody.replaceChildren();
  const frag = document.createDocumentFragment();
  for (const [label, s] of Object.entries(latency)) {
    const tr = document.createElement("tr");
    const fmt = v => v === null || v === undefined ? "—" : v.toFixed(1);
    tr.innerHTML =
      `<td>${label}</td>` +
      `<td>${s.count}</td>` +
      `<td>${fmt(s.min_us)}</td>` +
      `<td>${fmt(s.max_us)}</td>` +
      `<td>${fmt(s.mean_us)}</td>` +
      `<td>${fmt(s.std_us)}</td>` +
      `<td>${fmt(s.last_us)}</td>`;
    frag.appendChild(tr);
  }
  tbody.appendChild(frag);
}

// ── Trigger ───────────────────────────────────────────────────────────────────
const triggerStateBadge = document.getElementById("trigger-state-badge");
const triggerHdrBadge   = document.getElementById("trigger-hdr-badge");
const capturesList      = document.getElementById("captures-list");
const triggerTypeSelect = document.getElementById("trigger-type");
const triggerArbIdInput = document.getElementById("trigger-arb-id");
const triggerLoadInput  = document.getElementById("trigger-load-val");
const triggerTabBadge   = document.getElementById("trigger-tab-badge");

triggerTypeSelect?.addEventListener("change", () => {
  if (triggerArbIdInput) triggerArbIdInput.style.display =
    triggerTypeSelect.value === "arb_id" ? "" : "none";
  if (triggerLoadInput) triggerLoadInput.style.display =
    triggerTypeSelect.value === "bus_load" ? "" : "none";
});

document.getElementById("btn-arm")?.addEventListener("click", async () => {
  const body = { type: triggerTypeSelect?.value || "manual" };
  if (body.type === "arb_id" && triggerArbIdInput?.value)
    body.arb_id = parseInt(triggerArbIdInput.value.replace(/^0x/i, ""), 16);
  if (body.type === "bus_load" && triggerLoadInput?.value)
    body.bus_load_threshold = parseFloat(triggerLoadInput.value);
  await fetch("/api/trigger/arm", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
});
document.getElementById("btn-disarm")?.addEventListener("click", () =>
  fetch("/api/trigger/disarm", { method: "POST" }));
document.getElementById("btn-fire")?.addEventListener("click", () =>
  fetch("/api/trigger/fire", { method: "POST" }));

function updateTrigger(trigger) {
  if (!trigger) return;
  const STATE_CLASSES = {
    idle:       ["IDLE",      "badge-unknown"],
    armed:      ["ARMED",     "badge-warning"],
    collecting: ["CAPTURING", "badge-busoff"],
  };
  const [label, cls] = STATE_CLASSES[trigger.state] || ["?", "badge-unknown"];
  if (triggerStateBadge) {
    triggerStateBadge.textContent = label;
    triggerStateBadge.className   = `badge ${cls}`;
  }
  if (triggerHdrBadge) {
    triggerHdrBadge.textContent = label;
    triggerHdrBadge.className   = `badge ${cls}` + (trigger.state === "idle" ? " hidden" : "");
  }
  if (triggerTabBadge) {
    triggerTabBadge.textContent = trigger.state === "idle" ? "" : "●";
    triggerTabBadge.classList.toggle("hidden", trigger.state === "idle");
  }
  if (!capturesList) return;
  capturesList.replaceChildren();
  const frag = document.createDocumentFragment();
  for (const cap of (trigger.captures || [])) {
    const d = new Date(cap.trigger_ts * 1000);
    const row = document.createElement("div");
    row.className = "capture-row";
    row.innerHTML =
      `<span class="capture-id">${cap.id}</span>` +
      `<span>${d.toLocaleTimeString()}</span>` +
      `<span class="badge badge-unknown">${cap.condition_type}</span>` +
      `<span>${cap.frame_count} frames</span>` +
      `<a href="/api/captures/${cap.id}" target="_blank">JSON</a>` +
      `<a href="/api/captures/${cap.id}/svg" target="_blank">SVG</a>`;
    frag.appendChild(row);
  }
  capturesList.appendChild(frag);
}

// ── Latency pairs editor ──────────────────────────────────────────────────────
document.getElementById("btn-edit-pairs")?.addEventListener("click", async () => {
  const resp = await fetch("/api/latency/pairs");
  const data = await resp.json();
  const ta = document.getElementById("pairs-json");
  if (ta) ta.value = JSON.stringify(data, null, 2);
  document.getElementById("pairs-dialog")?.showModal();
});
document.getElementById("btn-save-pairs")?.addEventListener("click", async () => {
  try {
    const val = document.getElementById("pairs-json")?.value || "[]";
    const pairs = JSON.parse(val);
    await fetch("/api/latency/pairs", { method: "PUT",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(pairs) });
    document.getElementById("pairs-dialog")?.close();
  } catch (e) { alert("Invalid JSON: " + e.message); }
});
document.getElementById("btn-cancel-pairs")?.addEventListener("click", () =>
  document.getElementById("pairs-dialog")?.close());
document.getElementById("btn-reset-timing")?.addEventListener("click", () =>
  fetch("/api/timing/reset", { method: "POST" }));

// ── Annotate frame (double-click row) ─────────────────────────────────────────
tbody.addEventListener("dblclick", async (e) => {
  const tr = e.target.closest("tr");
  if (!tr) return;
  const cells = tr.querySelectorAll("td");
  if (cells.length < 2) return;
  // cells[0] = PRU ts / kernel_ts, cells[1] = arb_id
  const tsRaw   = cells[0].textContent.trim();
  const idRaw   = cells[1].textContent.trim();
  const note    = prompt(`Annotate frame ${idRaw}:`);
  if (!note) return;
  const arb_id  = parseInt(idRaw.replace(/^0x/i, ""), 16);
  // kernel_ts is embedded in the frame object — approximate from display
  // Use Date.now() as fallback (server stores against kernel_ts of nearest frame)
  const kernel_ts = parseFloat(tsRaw) || Date.now() / 1000;
  await fetch("/api/frames/annotate", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kernel_ts, arb_id, note }) });
});

// ── BBB Health ───────────────────────────────────────────────────────────────
function updateHealth(h) {
  if (!h) return;

  function setBar(barId, pct) {
    const el = document.getElementById(barId);
    if (!el) return;
    const p = pct == null ? 0 : Math.min(100, pct);
    el.style.width = p + "%";
    el.style.background = p > 85 ? "var(--critical)" : p > 60 ? "var(--warn)" : "var(--ok)";
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function colorBig(id, pct) {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.color = pct > 85 ? "var(--critical)" : pct > 60 ? "var(--warn)" : "var(--text)";
  }

  // CPU
  if (h.cpu_pct != null) {
    setText("hth-cpu-val", h.cpu_pct + "%");
    setBar("hth-cpu-bar", h.cpu_pct);
    colorBig("hth-cpu-val", h.cpu_pct);
  }
  if (h.load) {
    setText("hth-load", "Load: " + h.load.map(v => v.toFixed(2)).join("  "));
  }

  // Memory
  if (h.mem) {
    setText("hth-mem-val", h.mem.pct + "%");
    setText("hth-mem-detail", h.mem.used_mb + " MB / " + h.mem.total_mb + " MB");
    setBar("hth-mem-bar", h.mem.pct);
    colorBig("hth-mem-val", h.mem.pct);
  }

  // Temperature
  if (h.temp_c != null) {
    const el = document.getElementById("hth-temp-val");
    if (el) {
      el.textContent = h.temp_c + " °C";
      el.style.color = h.temp_c > 80 ? "var(--critical)" : h.temp_c > 65 ? "var(--warn)" : "var(--text)";
    }
  }

  // Uptime
  if (h.uptime_s != null) {
    const s = Math.floor(h.uptime_s);
    const d = Math.floor(s / 86400);
    const hh = Math.floor((s % 86400) / 3600);
    const mm = Math.floor((s % 3600) / 60);
    const parts = [];
    if (d) parts.push(d + "d");
    if (hh || d) parts.push(hh + "h");
    parts.push(mm + "m");
    setText("hth-uptime", "Uptime: " + parts.join(" "));
  }

  // Disk
  if (h.disk) {
    setText("hth-disk-val", h.disk.pct + "%");
    setText("hth-disk-detail", h.disk.used_gb + " GB / " + h.disk.total_gb + " GB");
    setBar("hth-disk-bar", h.disk.pct);
    colorBig("hth-disk-val", h.disk.pct);
  }
}

// ── Init ─────────────────────────────────────────────────────────────────────
connect();
