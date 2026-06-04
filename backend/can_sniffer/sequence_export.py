from __future__ import annotations

"""Generate SVG sequence diagrams from CaptureSession frame data."""

from typing import Optional

# ── Layout constants ──────────────────────────────────────────────────────────
_MARGIN_LEFT  = 90    # px — timestamp column
_MARGIN_TOP   = 50    # px — header row
_COL_WIDTH    = 100   # px — per-arb_id swimlane
_ROW_HEIGHT   = 18    # px — per-frame row
_CIRCLE_R     = 4     # px
_FONT         = "11px monospace"
_COLORS = {
    "bg_odd":    "#1a1a1e",
    "bg_even":   "#222228",
    "header_bg": "#26262c",
    "header_border": "#3a3a42",
    "text":      "#e0e0e8",
    "dim":       "#888890",
    "arrow":     "#5b9bd5",
    "trigger":   "#e05555",
}


def _id_color(arb_id: int) -> str:
    hue = (arb_id * 137) % 360
    return f"hsl({hue},45%,58%)"


def _ts_label(t: float, t0: float) -> str:
    ms = (t - t0) * 1000
    return f"{ms:.1f}"


def export_svg(
    frames:        list[dict],
    latency_pairs: Optional[list[dict]] = None,
    trigger_index: Optional[int]        = None,   # index in frames where post starts
) -> str:
    if not frames:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='400' height='60'>" \
               "<text x='10' y='30' fill='#888'>No frames</text></svg>"

    # Collect unique arb_ids in order of first appearance
    seen: list[int] = []
    for f in frames:
        aid = f["arb_id"]
        arb_id = int(aid, 16) if isinstance(aid, str) else int(aid)
        if arb_id not in seen:
            seen.append(arb_id)
    seen.sort()

    col_x = {aid: _MARGIN_LEFT + i * _COL_WIDTH + _COL_WIDTH // 2
              for i, aid in enumerate(seen)}

    n_rows  = len(frames)
    width   = _MARGIN_LEFT + len(seen) * _COL_WIDTH + 20
    height  = _MARGIN_TOP + n_rows * _ROW_HEIGHT + 20

    lines: list[str] = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" '
        f'style="font-family:monospace;font-size:11px;">'
    )

    # ── Defs: arrowhead ───────────────────────────────────────────────────────
    lines.append(
        '<defs><marker id="arrow" markerWidth="8" markerHeight="6" '
        'refX="8" refY="3" orient="auto">'
        f'<polygon points="0 0, 8 3, 0 6" fill="{_COLORS["arrow"]}"/>'
        '</marker></defs>'
    )

    # ── Background rows ───────────────────────────────────────────────────────
    for i in range(n_rows):
        y   = _MARGIN_TOP + i * _ROW_HEIGHT
        bg  = _COLORS["bg_odd"] if i % 2 else _COLORS["bg_even"]
        lines.append(
            f'<rect x="0" y="{y}" width="{width}" height="{_ROW_HEIGHT}" fill="{bg}"/>'
        )

    # ── Trigger line ──────────────────────────────────────────────────────────
    if trigger_index is not None and 0 < trigger_index <= n_rows:
        ty = _MARGIN_TOP + trigger_index * _ROW_HEIGHT
        lines.append(
            f'<line x1="0" y1="{ty}" x2="{width}" y2="{ty}" '
            f'stroke="{_COLORS["trigger"]}" stroke-width="1.5" stroke-dasharray="6 3"/>'
            f'<text x="4" y="{ty - 3}" fill="{_COLORS["trigger"]}" font-size="9px">▲ trigger</text>'
        )

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(
        f'<rect x="0" y="0" width="{width}" height="{_MARGIN_TOP}" '
        f'fill="{_COLORS["header_bg"]}"/>'
        f'<line x1="0" y1="{_MARGIN_TOP}" x2="{width}" y2="{_MARGIN_TOP}" '
        f'stroke="{_COLORS["header_border"]}"/>'
    )
    lines.append(
        f'<text x="4" y="30" fill="{_COLORS["dim"]}" font-size="10px">Time (ms)</text>'
    )
    for aid in seen:
        cx = col_x[aid]
        color = _id_color(aid)
        lines.append(
            f'<text x="{cx}" y="30" fill="{color}" text-anchor="middle" '
            f'font-size="10px">0x{aid:X}</text>'
        )

    # ── Vertical swimlane guides ──────────────────────────────────────────────
    for aid in seen:
        cx = col_x[aid]
        lines.append(
            f'<line x1="{cx}" y1="{_MARGIN_TOP}" x2="{cx}" y2="{height}" '
            f'stroke="{_COLORS["header_border"]}" stroke-width="0.5" stroke-dasharray="2 4"/>'
        )

    # ── Build latency pair lookup: for each frame index, find its partner ─────
    pair_arrows: list[tuple[int, int, str]] = []  # (req_idx, resp_idx, label)
    if latency_pairs:
        for pp in latency_pairs:
            req_base  = int(pp.get("request_base",  0))
            resp_base = int(pp.get("response_base", 0))
            req_id    = pp.get("request_id")
            resp_id   = pp.get("response_id")
            label     = pp.get("label", "")
            tmpl      = pp.get("label_template", "")

            for i, f in enumerate(frames):
                fid = int(f["arb_id"], 16) if isinstance(f["arb_id"], str) else int(f["arb_id"])
                # Explicit
                if req_id is not None and fid == int(req_id):
                    for j in range(i + 1, min(i + 50, n_rows)):
                        gid = int(frames[j]["arb_id"], 16) if isinstance(frames[j]["arb_id"], str) \
                              else int(frames[j]["arb_id"])
                        if gid == int(resp_id):
                            pair_arrows.append((i, j, label))
                            break
                # Pattern
                elif req_base and (fid & ~0xFF) == req_base:
                    node = fid & 0xFF
                    for j in range(i + 1, min(i + 50, n_rows)):
                        gid = int(frames[j]["arb_id"], 16) if isinstance(frames[j]["arb_id"], str) \
                              else int(frames[j]["arb_id"])
                        if (gid & ~0xFF) == resp_base and (gid & 0xFF) == node:
                            lbl = tmpl.format(node_id=node) if tmpl else f"0x{node:02X}"
                            pair_arrows.append((i, j, lbl))
                            break

    # ── Frame dots ────────────────────────────────────────────────────────────
    t0 = float(frames[0].get("kernel_ts", 0))
    for i, f in enumerate(frames):
        aid = int(f["arb_id"], 16) if isinstance(f["arb_id"], str) else int(f["arb_id"])
        cx  = col_x.get(aid, _MARGIN_LEFT)
        cy  = _MARGIN_TOP + i * _ROW_HEIGHT + _ROW_HEIGHT // 2
        ts  = float(f.get("kernel_ts", t0))
        color = _id_color(aid)

        # timestamp label
        lines.append(
            f'<text x="4" y="{cy + 4}" fill="{_COLORS["dim"]}" font-size="9px">'
            f'{_ts_label(ts, t0)}</text>'
        )
        # dot
        lines.append(
            f'<circle cx="{cx}" cy="{cy}" r="{_CIRCLE_R}" fill="{color}"/>'
        )

    # ── Latency arrows ────────────────────────────────────────────────────────
    for req_i, resp_i, lbl in pair_arrows:
        r_aid = int(frames[req_i]["arb_id"],  16) if isinstance(frames[req_i]["arb_id"],  str) \
                else int(frames[req_i]["arb_id"])
        s_aid = int(frames[resp_i]["arb_id"], 16) if isinstance(frames[resp_i]["arb_id"], str) \
                else int(frames[resp_i]["arb_id"])
        x1 = col_x.get(r_aid, _MARGIN_LEFT)
        y1 = _MARGIN_TOP + req_i  * _ROW_HEIGHT + _ROW_HEIGHT // 2
        x2 = col_x.get(s_aid, _MARGIN_LEFT)
        y2 = _MARGIN_TOP + resp_i * _ROW_HEIGHT + _ROW_HEIGHT // 2
        req_ts  = float(frames[req_i].get("kernel_ts",  t0))
        resp_ts = float(frames[resp_i].get("kernel_ts", t0))
        lat_us  = (resp_ts - req_ts) * 1_000_000
        mid_x   = (x1 + x2) // 2
        mid_y   = (y1 + y2) // 2

        lines.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{_COLORS["arrow"]}" stroke-width="1.5" '
            f'marker-end="url(#arrow)" opacity="0.8"/>'
            f'<text x="{mid_x + 4}" y="{mid_y - 2}" fill="{_COLORS["arrow"]}" '
            f'font-size="9px">{lbl} {lat_us:.0f}µs</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def export_svg_from_session(session: dict,
                            latency_pairs: Optional[list[dict]] = None) -> str:
    pre  = session.get("pre_frames",  [])
    post = session.get("post_frames", [])
    return export_svg(pre + post, latency_pairs=latency_pairs,
                      trigger_index=len(pre) if pre else None)
