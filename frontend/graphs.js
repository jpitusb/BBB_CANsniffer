"use strict";

// ── Graphs module ─────────────────────────────────────────────────────────────
// Exposes: graphsIngest(msg), graphsTabActivated(), graphsTabDeactivated()

(function () {

  // ── Constants ──────────────────────────────────────────────────────────────
  const WINDOW_S          = 60;
  const MAX_BUCKETS       = WINDOW_S;
  const RENDER_HZ         = 2;
  const HEATMAP_IDS       = 32;
  const HIST_BINS         = 40;
  const MAX_LATENCY_PAIRS = 6;

  // Dark-theme palette matching style.css CSS variables
  const C = {
    bg:       "#1a1a1e",
    surface:  "#26262c",
    border:   "#3a3a42",
    text:     "#e0e0e8",
    dim:      "#888890",
    accent:   "#5b9bd5",
    ok:       "#4caf7d",
    warn:     "#e8a838",
    critical: "#e05555",
  };

  const SERIES_PALETTE = [
    "#5b9bd5", "#4caf7d", "#e8a838", "#e05555",
    "#a855f7", "#22d3ee", "#f97316",
  ];

  // ── Rolling-window time-series ring buffer ─────────────────────────────────
  function makeRing() {
    return { data: new Array(MAX_BUCKETS).fill(null), head: 0, count: 0 };
  }

  function ringPush(ring, value) {
    ring.data[ring.head] = value;
    ring.head = (ring.head + 1) % MAX_BUCKETS;
    if (ring.count < MAX_BUCKETS) ring.count++;
  }

  // Returns [timestamps[], values[]] as parallel arrays for uPlot
  function ringToUplot(ring, nowTs) {
    const n = ring.count;
    if (n === 0) return [[nowTs], [null]];
    const ts = new Array(n);
    const vs = new Array(n);
    const oldest = nowTs - (n - 1);
    for (let i = 0; i < n; i++) {
      const idx = (ring.head - n + i + MAX_BUCKETS) % MAX_BUCKETS;
      ts[i] = oldest + i;
      vs[i] = ring.data[idx];
    }
    return [ts, vs];
  }

  // ── Per-second accumulator state ───────────────────────────────────────────
  let _lastBucketSec = 0;

  const acc = {
    busLoadSum:   0,
    busLoadCount: 0,
    tec:          null,
    rec:          null,
    errorFrames:  0,
    latency:      {},  // label -> { sum, count }
  };

  // Time-series rings (1-second buckets)
  const rings = {
    busLoad: makeRing(),
    tec:     makeRing(),
    rec:     makeRing(),
    errRate: makeRing(),
  };

  // Latency per-pair rings: label -> ring
  const latencyRings = {};
  let latencyPairLabels = [];

  // Heatmap state
  let heatmapCurrentBucket = {};  // arb_id -> count this second
  const heatmapCols = [];         // rolling 60 columns
  let heatmapIds    = [];         // sorted arb_ids seen (capped at HEATMAP_IDS)

  // Per-ID delta histogram samples: arb_id -> Float32Array-like array
  const idDeltaHistory = {};
  let histSelectedId = "";

  // PRU vs kernel timestamp delta histogram (µs)
  const pruKernelDeltas = [];

  // ── Chart handles ──────────────────────────────────────────────────────────
  const charts = {};
  let heatmapCanvas   = null;
  let heatmapCtx      = null;
  let renderIntervalId = null;
  let isActive        = false;
  let resizeObserver  = null;

  // ── uPlot helpers ──────────────────────────────────────────────────────────
  function containerWidth(el) {
    return Math.max(200, el.getBoundingClientRect().width || 400);
  }

  function destroyChart(name) {
    if (charts[name]) {
      try { charts[name].destroy(); } catch (_) {}
      charts[name] = null;
    }
  }

  function sharedAxes() {
    return [
      {
        stroke: C.dim,
        grid:   { stroke: C.border, width: 1 },
        ticks:  { stroke: C.border },
        font:   "11px Fira Mono, monospace",
      },
      {
        stroke: C.dim,
        grid:   { stroke: C.border, width: 1 },
        ticks:  { stroke: C.border },
        font:   "11px Fira Mono, monospace",
      },
    ];
  }

  function makeBarsPlugin(seriesIdx) {
    return {
      opts(u, opts) {
        if (uPlot.paths && uPlot.paths.bars) {
          const s = opts.series[seriesIdx];
          if (s) s.paths = uPlot.paths.bars({ size: [0.75, 100] });
        }
        return opts;
      },
    };
  }

  // ── Bus Load chart ─────────────────────────────────────────────────────────
  function buildBusLoadChart() {
    const el = document.getElementById("graph-busload-wrap");
    if (!el) return;
    destroyChart("busLoad");
    const w = containerWidth(el);
    const nowTs = Math.floor(Date.now() / 1000);
    const [ts, vs] = ringToUplot(rings.busLoad, nowTs);
    charts.busLoad = new uPlot(
      {
        title:   "Bus Load %",
        width:   w,
        height:  160,
        padding: [8, 12, 0, 0],
        legend:  { show: true },
        cursor:  { show: false },
        scales:  { x: { time: true }, y: { range: [0, 100] } },
        axes:    sharedAxes(),
        series: [
          {},
          {
            label:  "Load %",
            stroke: C.accent,
            fill:   "rgba(91,155,213,0.18)",
            width:  1.5,
          },
        ],
      },
      [ts, vs.map(v => (v === null ? null : v * 100))],
      el,
    );
  }

  // ── TEC / REC chart ────────────────────────────────────────────────────────
  function buildTecRecChart() {
    const el = document.getElementById("graph-tecrec-wrap");
    if (!el) return;
    destroyChart("tecRec");
    const w = containerWidth(el);
    const nowTs = Math.floor(Date.now() / 1000);
    const [ts, tecVs] = ringToUplot(rings.tec, nowTs);
    const [, recVs]   = ringToUplot(rings.rec, nowTs);
    charts.tecRec = new uPlot(
      {
        title:   "TEC / REC",
        width:   w,
        height:  160,
        padding: [8, 12, 0, 0],
        legend:  { show: true },
        cursor:  { show: false },
        scales:  { x: { time: true } },
        axes:    sharedAxes(),
        series: [
          {},
          { label: "TEC", stroke: C.warn,   width: 1.5 },
          { label: "REC", stroke: C.accent, width: 1.5 },
        ],
      },
      [ts, tecVs, recVs],
      el,
    );
  }

  // ── Error Rate bar chart ───────────────────────────────────────────────────
  function buildErrRateChart() {
    const el = document.getElementById("graph-errrate-wrap");
    if (!el) return;
    destroyChart("errRate");
    const w = containerWidth(el);
    const nowTs = Math.floor(Date.now() / 1000);
    const [ts, vs] = ringToUplot(rings.errRate, nowTs);
    charts.errRate = new uPlot(
      {
        title:   "Error Frames / sec",
        width:   w,
        height:  160,
        padding: [8, 12, 0, 0],
        legend:  { show: true },
        cursor:  { show: false },
        scales:  { x: { time: true } },
        axes:    sharedAxes(),
        plugins: [makeBarsPlugin(1)],
        series: [
          {},
          {
            label:  "err/s",
            stroke: C.critical,
            fill:   "rgba(224,85,85,0.55)",
            width:  1,
          },
        ],
      },
      [ts, vs],
      el,
    );
  }

  // ── Latency trend multi-line chart ────────────────────────────────────────
  function buildLatencyChart() {
    const el = document.getElementById("graph-latency-wrap");
    if (!el) return;
    destroyChart("latency");
    if (latencyPairLabels.length === 0) return;

    const w = containerWidth(el);
    const nowTs = Math.floor(Date.now() / 1000);
    const seriesDefs = [{}];
    const uData      = [null];
    let tsFinal      = null;

    latencyPairLabels.slice(0, MAX_LATENCY_PAIRS).forEach((lbl, i) => {
      const ring = latencyRings[lbl];
      const [ts, vs] = ring ? ringToUplot(ring, nowTs) : [[nowTs], [null]];
      if (!tsFinal) tsFinal = ts;
      uData.push(vs);
      seriesDefs.push({
        label:  lbl,
        stroke: SERIES_PALETTE[i % SERIES_PALETTE.length],
        width:  1.5,
      });
    });

    uData[0] = tsFinal || [Math.floor(Date.now() / 1000)];
    charts.latency = new uPlot(
      {
        title:   "Latency mean (µs)",
        width:   w,
        height:  180,
        padding: [8, 12, 0, 0],
        legend:  { show: true },
        cursor:  { show: false },
        scales:  { x: { time: true } },
        axes:    sharedAxes(),
        series:  seriesDefs,
      },
      uData,
      el,
    );
  }

  // ── Histogram utilities ────────────────────────────────────────────────────
  function buildHistogram(samples, bins) {
    if (!samples.length) return { xs: [], ys: [] };
    let min = Infinity, max = -Infinity;
    for (const v of samples) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
    if (min === max) { max = min + 1; }
    const step   = (max - min) / bins;
    const counts = new Array(bins).fill(0);
    for (const v of samples) {
      const b = Math.min(bins - 1, Math.floor((v - min) / step));
      counts[b]++;
    }
    const xs = [];
    const ys = [];
    for (let i = 0; i < bins; i++) {
      xs.push(+(min + (i + 0.5) * step).toFixed(2));
      ys.push(counts[i]);
    }
    return { xs, ys };
  }

  // ── PRU vs kernel delta histogram ─────────────────────────────────────────
  function buildPruHistChart() {
    const el = document.getElementById("graph-pruhist-wrap");
    if (!el) return;
    destroyChart("pruHist");
    const { xs, ys } = buildHistogram(pruKernelDeltas, HIST_BINS);
    if (!xs.length) return;
    const w = containerWidth(el);
    charts.pruHist = new uPlot(
      {
        title:   "PRU vs Kernel Δ (µs)",
        width:   w,
        height:  160,
        padding: [8, 12, 0, 0],
        legend:  { show: true },
        cursor:  { show: false },
        scales:  { x: { time: false }, y: {} },
        axes:    sharedAxes(),
        plugins: [makeBarsPlugin(1)],
        series: [
          {},
          {
            label:  "count",
            stroke: C.ok,
            fill:   "rgba(76,175,125,0.45)",
            width:  1,
          },
        ],
      },
      [xs, ys],
      el,
    );
  }

  // ── Per-ID interval histogram ──────────────────────────────────────────────
  function buildIdHistChart() {
    const el = document.getElementById("graph-idhist-wrap");
    if (!el) return;
    destroyChart("idHist");
    const samples = histSelectedId ? (idDeltaHistory[histSelectedId] || []) : [];
    const { xs, ys } = buildHistogram(samples, HIST_BINS);
    if (!xs.length) return;
    const w = containerWidth(el);
    charts.idHist = new uPlot(
      {
        title:   `ID ${histSelectedId} Interval (µs)`,
        width:   w,
        height:  160,
        padding: [8, 12, 0, 0],
        legend:  { show: true },
        cursor:  { show: false },
        scales:  { x: { time: false }, y: {} },
        axes:    sharedAxes(),
        plugins: [makeBarsPlugin(1)],
        series: [
          {},
          {
            label:  "count",
            stroke: C.accent,
            fill:   "rgba(91,155,213,0.45)",
            width:  1,
          },
        ],
      },
      [xs, ys],
      el,
    );
  }

  // ── Heatmap canvas ─────────────────────────────────────────────────────────
  function initHeatmapCanvas() {
    const wrap = document.getElementById("graph-heatmap-wrap");
    if (!wrap) return;
    heatmapCanvas = document.getElementById("graph-heatmap-canvas");
    if (!heatmapCanvas) {
      heatmapCanvas = document.createElement("canvas");
      heatmapCanvas.id = "graph-heatmap-canvas";
      heatmapCanvas.style.cssText = "width:100%;height:180px;display:block;";
      wrap.appendChild(heatmapCanvas);
    }
    heatmapCtx = heatmapCanvas.getContext("2d");
  }

  function heatmapColor(norm) {
    if (norm <= 0)   return C.bg;
    if (norm < 0.15) return "#1a3a5c";
    if (norm < 0.35) return "#1e6fa8";
    if (norm < 0.55) return C.ok;
    if (norm < 0.75) return C.warn;
    return C.critical;
  }

  function renderHeatmap() {
    if (!heatmapCanvas || !heatmapCtx) return;
    const wrap = document.getElementById("graph-heatmap-wrap");
    if (!wrap) return;
    const dpr = window.devicePixelRatio || 1;
    const cw  = Math.max(200, wrap.getBoundingClientRect().width || 400);
    const ch  = 180;
    heatmapCanvas.width  = Math.round(cw * dpr);
    heatmapCanvas.height = Math.round(ch * dpr);
    const ctx = heatmapCtx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = C.bg;
    ctx.fillRect(0, 0, cw, ch);

    const yIds = heatmapIds.slice(0, HEATMAP_IDS);

    if (!heatmapCols.length || !yIds.length) {
      ctx.fillStyle = C.dim;
      ctx.font = "12px Fira Mono, monospace";
      ctx.textAlign = "center";
      ctx.fillText("No heatmap data yet", cw / 2, ch / 2);
      return;
    }

    const LABEL_W = 62;
    const AXIS_H  = 18;
    const plotW   = cw - LABEL_W - 4;
    const plotH   = ch - AXIS_H;
    const nCols   = heatmapCols.length;
    const nRows   = yIds.length;
    const colW    = plotW / nCols;
    const rowH    = plotH / nRows;

    // Find max count for color scaling
    let maxCount = 1;
    for (const col of heatmapCols) {
      for (const v of col.counts.values()) {
        if (v > maxCount) maxCount = v;
      }
    }

    // Draw cells
    for (let ci = 0; ci < nCols; ci++) {
      const col = heatmapCols[ci];
      const x = LABEL_W + ci * colW;
      for (let ri = 0; ri < nRows; ri++) {
        const id    = yIds[ri];
        const count = col.counts.get(id) || 0;
        ctx.fillStyle = heatmapColor(count / maxCount);
        ctx.fillRect(Math.floor(x), Math.floor(ri * rowH), Math.ceil(colW) + 1, Math.ceil(rowH) + 1);
      }
    }

    // Y-axis labels
    ctx.fillStyle  = C.dim;
    ctx.font       = "10px Fira Mono, monospace";
    ctx.textAlign  = "right";
    const maxLabels = Math.floor(plotH / 14);
    const labelStep = Math.max(1, Math.ceil(nRows / maxLabels));
    for (let ri = 0; ri < nRows; ri += labelStep) {
      ctx.fillText(yIds[ri], LABEL_W - 4, ri * rowH + rowH / 2 + 4);
    }

    // X-axis time ticks
    ctx.fillStyle  = C.dim;
    ctx.font       = "10px Fira Mono, monospace";
    ctx.textAlign  = "center";
    const tickCount = Math.min(6, nCols);
    const tickStep  = Math.max(1, Math.floor(nCols / tickCount));
    for (let ci = 0; ci < nCols; ci += tickStep) {
      const col = heatmapCols[ci];
      const x   = LABEL_W + (ci + 0.5) * colW;
      const d   = new Date(col.ts_s * 1000);
      const lbl = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      ctx.fillText(lbl, x, ch - 3);
    }

    // Chart title
    ctx.fillStyle  = C.dim;
    ctx.font       = "11px Fira Mono, monospace";
    ctx.textAlign  = "left";
    ctx.fillText("Frame Activity (ID vs Time)", LABEL_W, plotH + 12);
  }

  // ── Per-second bucket flush ────────────────────────────────────────────────
  function flushBucket(bucketSec) {
    ringPush(rings.busLoad, acc.busLoadCount > 0 ? acc.busLoadSum / acc.busLoadCount : null);
    ringPush(rings.tec,     acc.tec);
    ringPush(rings.rec,     acc.rec);
    ringPush(rings.errRate, acc.errorFrames);

    for (const lbl of latencyPairLabels) {
      if (!latencyRings[lbl]) latencyRings[lbl] = makeRing();
      const s = acc.latency[lbl];
      ringPush(latencyRings[lbl], s && s.count > 0 ? s.sum / s.count : null);
    }

    const col = { ts_s: bucketSec, counts: new Map(Object.entries(heatmapCurrentBucket)) };
    heatmapCols.push(col);
    if (heatmapCols.length > WINDOW_S) heatmapCols.shift();

    // Reset
    acc.busLoadSum   = 0;
    acc.busLoadCount = 0;
    acc.tec          = null;
    acc.rec          = null;
    acc.errorFrames  = 0;
    acc.latency      = {};
    heatmapCurrentBucket = {};
  }

  // ── Data ingestion ─────────────────────────────────────────────────────────
  function graphsIngest(msg) {
    const nowSec = Math.floor(Date.now() / 1000);

    if (_lastBucketSec === 0) _lastBucketSec = nowSec;
    // Flush any completed second(s)
    while (_lastBucketSec < nowSec) {
      flushBucket(_lastBucketSec);
      _lastBucketSec++;
    }

    // Bus load
    if (msg.bus_load != null) {
      acc.busLoadSum   += msg.bus_load;
      acc.busLoadCount += 1;
    }

    // Diag: TEC, REC, error rate — handle flat schema { tec, rec, ... }
    // and nested schema { bus_health: { tec, rec, ... } }
    const diag = msg.diag;
    if (diag) {
      const bh = diag.bus_health || diag;
      if (bh.tec != null) acc.tec = bh.tec;
      if (bh.rec != null) acc.rec = bh.rec;
      const errFs = bh.error_frames_per_sec ?? bh.error_frames_1s ?? null;
      if (errFs != null) acc.errorFrames = Math.max(acc.errorFrames, errFs);
    }

    // Latency — handles both object map and pairs array
    const latency = msg.latency;
    if (latency) {
      let pairs;
      if (Array.isArray(latency.pairs)) {
        pairs = latency.pairs;
      } else if (Array.isArray(latency)) {
        pairs = latency;
      } else {
        pairs = Object.entries(latency).map(([label, s]) => ({ label, ...s }));
      }
      let labelsChanged = false;
      for (const p of pairs) {
        const lbl = p.label;
        if (!lbl) continue;
        if (!latencyPairLabels.includes(lbl) && latencyPairLabels.length < MAX_LATENCY_PAIRS) {
          latencyPairLabels.push(lbl);
          labelsChanged = true;
        }
        const v = p.mean_us ?? p.last_us ?? null;
        if (v != null) {
          if (!acc.latency[lbl]) acc.latency[lbl] = { sum: 0, count: 0 };
          acc.latency[lbl].sum   += v;
          acc.latency[lbl].count += 1;
        }
      }
      if (labelsChanged && isActive) {
        buildLatencyChart();
      }
    }

    // Frames: heatmap, per-ID delta histogram, PRU-kernel delta histogram
    if (msg.frames && msg.frames.length) {
      let idsChanged = false;
      for (const f of msg.frames) {
        const id = f.arb_id;
        if (!id) continue;

        // Heatmap bucket
        heatmapCurrentBucket[id] = (heatmapCurrentBucket[id] || 0) + 1;
        if (!heatmapIds.includes(id)) {
          heatmapIds.push(id);
          heatmapIds.sort((a, b) => parseInt(a, 16) - parseInt(b, 16));
          if (heatmapIds.length > HEATMAP_IDS) heatmapIds.length = HEATMAP_IDS;
          idsChanged = true;
        }

        // Per-ID interval delta
        if (f.delta_us != null) {
          if (!idDeltaHistory[id]) idDeltaHistory[id] = [];
          idDeltaHistory[id].push(f.delta_us);
          if (idDeltaHistory[id].length > 2000) idDeltaHistory[id].splice(0, 500);
        }

        // PRU vs kernel delta (µs)
        if (f.pru_ts_ns != null && f.kernel_ts != null) {
          const delta_us = (f.pru_ts_ns - f.kernel_ts * 1e9) / 1000;
          pruKernelDeltas.push(delta_us);
          if (pruKernelDeltas.length > 2000) pruKernelDeltas.splice(0, 500);
        }
      }
      if (idsChanged && isActive) {
        populateIdDropdown();
      }
    }
  }

  // ── Render tick (called at RENDER_HZ while active) ────────────────────────
  function updateCharts() {
    if (!isActive) return;
    const nowTs = Math.floor(Date.now() / 1000);

    if (charts.busLoad) {
      const [ts, vs] = ringToUplot(rings.busLoad, nowTs);
      try { charts.busLoad.setData([ts, vs.map(v => (v === null ? null : v * 100))]); } catch (_) {}
    }

    if (charts.tecRec) {
      const [ts, tecVs] = ringToUplot(rings.tec, nowTs);
      const [, recVs]   = ringToUplot(rings.rec, nowTs);
      try { charts.tecRec.setData([ts, tecVs, recVs]); } catch (_) {}
    }

    if (charts.errRate) {
      const [ts, vs] = ringToUplot(rings.errRate, nowTs);
      try { charts.errRate.setData([ts, vs]); } catch (_) {}
    }

    if (charts.latency && latencyPairLabels.length) {
      let tsFinal = null;
      const uData = [null];
      latencyPairLabels.slice(0, MAX_LATENCY_PAIRS).forEach(lbl => {
        const ring     = latencyRings[lbl];
        const [ts, vs] = ring ? ringToUplot(ring, nowTs) : [[nowTs], [null]];
        if (!tsFinal) tsFinal = ts;
        uData.push(vs);
      });
      if (tsFinal) {
        uData[0] = tsFinal;
        try { charts.latency.setData(uData); } catch (_) {}
      }
    }

    // Histograms: rebuild each tick (cheap for small data)
    buildPruHistChart();
    if (histSelectedId) buildIdHistChart();

    renderHeatmap();
  }

  // ── ID dropdown ────────────────────────────────────────────────────────────
  function populateIdDropdown() {
    const sel = document.getElementById("graph-id-select");
    if (!sel) return;
    const allIds = Object.keys(idDeltaHistory).sort((a, b) => parseInt(a, 16) - parseInt(b, 16));
    const prev = sel.value;
    sel.replaceChildren();
    const ph = document.createElement("option");
    ph.value = ""; ph.textContent = "Select Arb ID…";
    sel.appendChild(ph);
    for (const id of allIds) {
      const opt = document.createElement("option");
      opt.value = id; opt.textContent = id;
      sel.appendChild(opt);
    }
    if (prev && allIds.includes(prev)) {
      sel.value      = prev;
      histSelectedId = prev;
    } else {
      histSelectedId = "";
    }
  }

  // ── ResizeObserver ─────────────────────────────────────────────────────────
  function attachResizeObserver() {
    if (resizeObserver) return;
    const panel = document.getElementById("panel-graphs");
    if (!panel) return;
    resizeObserver = new ResizeObserver(() => {
      if (!isActive) return;
      rebuildAllCharts();
    });
    resizeObserver.observe(panel);
  }

  function rebuildAllCharts() {
    buildBusLoadChart();
    buildTecRecChart();
    buildErrRateChart();
    buildLatencyChart();
    buildPruHistChart();
    buildIdHistChart();
    // Heatmap auto-sizes inside renderHeatmap()
  }

  // ── Tab lifecycle ──────────────────────────────────────────────────────────
  function graphsTabActivated() {
    isActive = true;
    populateIdDropdown();
    if (!renderIntervalId) {
      renderIntervalId = setInterval(updateCharts, Math.round(1000 / RENDER_HZ));
    }
    attachResizeObserver();
    // Defer chart build by two animation frames so the panel finishes
    // transitioning from display:none → display:flex before we measure widths.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      initHeatmapCanvas();
      rebuildAllCharts();
    }));
  }

  function graphsTabDeactivated() {
    isActive = false;
    if (renderIntervalId) {
      clearInterval(renderIntervalId);
      renderIntervalId = null;
    }
  }

  // ── Wire dropdown change ───────────────────────────────────────────────────
  function wireDropdown() {
    const sel = document.getElementById("graph-id-select");
    if (!sel) return;
    sel.addEventListener("change", () => {
      histSelectedId = sel.value;
      if (isActive) buildIdHistChart();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wireDropdown);
  } else {
    wireDropdown();
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.graphsIngest         = graphsIngest;
  window.graphsTabActivated   = graphsTabActivated;
  window.graphsTabDeactivated = graphsTabDeactivated;

})();
