"""Standalone SVG figures generated on the service side.

Generating figures here rather than only in the browser means an exported
figure can be produced and tested without a browser, and that it always
carries its period label, universe, legend and source line.  A figure with a
missing value shows a distinct 'no data' treatment; it never shows a gap as a
zero.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

# A restrained sequential ramp (light to dark), plus a distinct hatch colour for
# areas with no usable value.  Chosen for adequate contrast on white.
SEQUENTIAL = ["#e8eef4", "#bcd0e2", "#89aecb", "#5386ad", "#27618e"]
WARN = "#7a3b12"
NO_DATA_FILL = "#f4f1ec"
NO_DATA_STROKE = "#b9b2a6"
INK = "#1d1f21"
MUTED = "#5d6166"
RULE = "#d7d9dc"
ACCENT = "#27618e"
ACCENT_ALT = "#9a6b3f"


def esc(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def quantile_cuts(values: Iterable[float], classes: int = 5) -> list[float]:
    data = sorted(v for v in values if v is not None)
    if not data:
        return []
    cuts: list[float] = []
    for i in range(1, classes):
        pos = i * (len(data) - 1) / classes
        lo = int(pos)
        hi = min(lo + 1, len(data) - 1)
        cuts.append(data[lo] + (data[hi] - data[lo]) * (pos - lo))
    out: list[float] = []
    for c in cuts:
        if not out or c > out[-1]:
            out.append(c)
    return out


def class_of(value: float | None, cuts: list[float]) -> int | None:
    if value is None:
        return None
    for i, c in enumerate(cuts):
        if value < c:
            return i
    return len(cuts)


def fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "no data"
    if unit == "percent":
        return f"{value:,.1f}%"
    return f"{value:,.0f}"


def _uncertainty_phrase(v: dict, unit: str) -> str:
    """What to say about a value's uncertainty, in a tooltip.

    A controlled estimate has no sampling error, and "plus or minus zero"
    reads as a measurement of remarkable precision rather than as the absence
    of one.
    """
    if v.get("controlled"):
        return " (controlled total, no sampling error)"
    if v.get("m") is None:
        return ""
    return " " + fmt_moe(v["m"], unit)


def fmt_moe(value: float | None, unit: str) -> str:
    """A margin of error on a percentage is a span of percentage points.

    Writing it as a percentage invites reading it as a percentage *of the
    estimate*, which is a different and much smaller number.
    """
    if value is None:
        return "not available"
    if unit == "percent":
        return f"±{value:,.1f} percentage points"
    return f"±{value:,.0f}"


# ---------------------------------------------------------------------------
# Choropleth
# ---------------------------------------------------------------------------

def _restrict(features: list[dict], areas: list[str] | None) -> list[dict]:
    if areas is None:
        return features
    keep = set(areas)
    return [f for f in features if f.get("properties", {}).get("GEOID") in keep]


def _bounds(features: list[dict]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(node):
        if isinstance(node[0], (int, float)):
            xs.append(node[0])
            ys.append(node[1])
        else:
            for child in node:
                walk(child)

    for f in features:
        if f.get("geometry"):
            walk(f["geometry"]["coordinates"])
    if not xs:
        return (-74.3, 40.4, -73.6, 40.95)
    return (min(xs), min(ys), max(xs), max(ys))


def _projector(bounds, width, height, pad=12):
    minx, miny, maxx, maxy = bounds
    mid_lat = math.radians((miny + maxy) / 2)
    # Equirectangular with a cosine correction at the centre latitude: adequate
    # for a single city and free of any projection dependency.
    sx = math.cos(mid_lat)
    w = (maxx - minx) * sx
    h = (maxy - miny)
    if w <= 0 or h <= 0:
        w = h = 1.0
    scale = min((width - 2 * pad) / w, (height - 2 * pad) / h)
    ox = pad + ((width - 2 * pad) - w * scale) / 2
    oy = pad + ((height - 2 * pad) - h * scale) / 2

    def project(x, y):
        return (ox + (x - minx) * sx * scale, oy + (maxy - y) * scale)

    return project


def _path_for(geometry: dict, project) -> str:
    parts: list[str] = []

    def ring(coords):
        pts = [project(x, y) for x, y in coords]
        if not pts:
            return
        parts.append("M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in pts) + "Z")

    if geometry["type"] == "Polygon":
        for r in geometry["coordinates"]:
            ring(r)
    else:
        for poly in geometry["coordinates"]:
            for r in poly:
                ring(r)
    return " ".join(parts)


def choropleth_svg(*, features: list[dict], values: dict[str, dict],
                   title: str, subtitle: str, unit: str, cuts: list[float],
                   source_lines: list[str], width: int = 760, height: int = 620,
                   data_mode: str = "live", panel_label: str | None = None,
                   areas: list[str] | None = None,
                   disclosures: list[str] | None = None) -> str:
    """One choropleth panel.

    ``areas`` restricts the drawing to exactly the selected areas, so a figure
    cannot show ground the accompanying data does not cover.
    """
    features = _restrict(features, areas)
    disclosures = disclosures or []
    project = _projector(_bounds(features), width, height - 150, pad=16)
    body: list[str] = []
    n_missing = 0
    for f in features:
        geoid = f["properties"]["GEOID"]
        v = values.get(geoid) or {}
        est = v.get("e") if v.get("es") == "ok" else None
        cls = class_of(est, cuts)
        if cls is None:
            n_missing += 1
            fill, stroke = NO_DATA_FILL, NO_DATA_STROKE
            extra = ' stroke-dasharray="2 2"'
        else:
            fill, stroke, extra = SEQUENTIAL[min(cls, len(SEQUENTIAL) - 1)], "#ffffff", ""
        label = f"{f['properties'].get('name', geoid)}: {fmt(est, unit)}"
        body.append(
            f'<path d="{_path_for(f["geometry"], project)}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="0.6"{extra}>'
            f"<title>{esc(label)}</title></path>"
        )

    legend_y = height - 118
    legend: list[str] = []
    swatch_w = 44
    for i in range(len(cuts) + 1):
        x = 16 + i * (swatch_w + 4)
        legend.append(
            f'<rect x="{x}" y="{legend_y}" width="{swatch_w}" height="12" '
            f'fill="{SEQUENTIAL[min(i, len(SEQUENTIAL) - 1)]}" stroke="#ffffff"/>'
        )
        if i < len(cuts):
            legend.append(
                f'<text x="{x + swatch_w + 2}" y="{legend_y + 26}" '
                f'text-anchor="middle" font-size="10" fill="{MUTED}">'
                f"{esc(fmt(cuts[i], unit))}</text>"
            )
    nd_x = 16 + (len(cuts) + 1) * (swatch_w + 4) + 16
    legend.append(
        f'<rect x="{nd_x}" y="{legend_y}" width="{swatch_w}" height="12" '
        f'fill="{NO_DATA_FILL}" stroke="{NO_DATA_STROKE}" stroke-dasharray="2 2"/>'
        f'<text x="{nd_x + swatch_w + 6}" y="{legend_y + 10}" font-size="10" '
        f'fill="{MUTED}">no usable estimate ({n_missing})</text>'
    )

    footer = []
    y = height - 66 - 13 * len(disclosures)
    for line in disclosures:
        footer.append(
            f'<text x="16" y="{y}" font-size="10" fill="{WARN}">{esc("! " + line)}</text>')
        y += 13
    for line in source_lines:
        footer.append(f'<text x="16" y="{y}" font-size="10" fill="{MUTED}">{esc(line)}</text>')
        y += 13

    banner = ""
    if data_mode == "fixture":
        banner = (
            f'<rect x="0" y="0" width="{width}" height="22" fill="#7a3b12"/>'
            f'<text x="10" y="15" font-size="11" fill="#ffffff" font-weight="600">'
            f"FIXTURE MODE - synthetic test values, not census estimates</text>"
        )
    top = 22 if banner else 0
    panel = (f'<text x="{width - 16}" y="{top + 24}" text-anchor="end" font-size="11" '
             f'fill="{MUTED}">{esc(panel_label)}</text>' if panel_label else "")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" \
viewBox="0 0 {width} {height}" font-family="Iowan Old Style, Palatino, Georgia, serif" \
role="img" aria-label="{esc(title)}. {esc(subtitle)}">
<rect width="{width}" height="{height}" fill="#ffffff"/>{banner}
<text x="16" y="{top + 26}" font-size="17" fill="{INK}">{esc(title)}</text>{panel}
<text x="16" y="{top + 45}" font-size="12" fill="{MUTED}">{esc(subtitle)}</text>
<g transform="translate(0,{top + 56})">{''.join(body)}</g>
<line x1="16" y1="{legend_y - 14}" x2="{width - 16}" y2="{legend_y - 14}" stroke="{RULE}"/>
{''.join(legend)}
{''.join(footer)}
</svg>"""


def choropleth_comparison_svg(*, panels: list[dict], title: str, subtitle: str,
                              unit: str, cuts: list[float],
                              source_lines: list[str], areas: list[str] | None = None,
                              disclosures: list[str] | None = None,
                              width: int = 1120, height: int = 600,
                              data_mode: str = "live") -> str:
    """Both periods of a comparison, side by side on one set of class breaks.

    A comparison export that showed only one panel would not be the comparison
    the interface displayed, so this draws both or the caller labels the figure
    as a single period.
    """
    disclosures = disclosures or []
    panel_width = (width - 48) // 2
    map_height = height - 190

    # One projection for both panels, so the two maps are directly comparable.
    all_features = []
    for panel in panels:
        all_features.extend(_restrict(panel["features"], areas))
    project = _projector(_bounds(all_features), panel_width, map_height, pad=14)

    groups = []
    n_missing = 0
    for index, panel in enumerate(panels):
        body = []
        for f in _restrict(panel["features"], areas):
            geoid = f["properties"]["GEOID"]
            v = panel["values"].get(geoid) or {}
            est = v.get("e") if v.get("es") == "ok" else None
            cls = class_of(est, cuts)
            if cls is None:
                n_missing += 1
                fill, stroke, extra = NO_DATA_FILL, NO_DATA_STROKE, ' stroke-dasharray="2 2"'
            else:
                fill, stroke, extra = SEQUENTIAL[min(cls, len(SEQUENTIAL) - 1)], "#ffffff", ""
            body.append(
                f'<path d="{_path_for(f["geometry"], project)}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="0.6"{extra}>'
                f'<title>{esc(f["properties"].get("name", geoid))}: '
                f'{esc(fmt(est, unit))}</title></path>')
        x = 16 + index * (panel_width + 16)
        groups.append(
            f'<g transform="translate({x},0)">'
            f'<text x="0" y="-8" font-size="12" fill="{INK}">{esc(panel["label"])}</text>'
            f'{"".join(body)}</g>')

    legend_y = height - 128
    legend = []
    swatch_w = 44
    for i in range(len(cuts) + 1):
        x = 16 + i * (swatch_w + 4)
        legend.append(
            f'<rect x="{x}" y="{legend_y}" width="{swatch_w}" height="12" '
            f'fill="{SEQUENTIAL[min(i, len(SEQUENTIAL) - 1)]}" stroke="#ffffff"/>')
        if i < len(cuts):
            legend.append(
                f'<text x="{x + swatch_w + 2}" y="{legend_y + 26}" text-anchor="middle" '
                f'font-size="10" fill="{MUTED}">{esc(fmt(cuts[i], unit))}</text>')
    nd_x = 16 + (len(cuts) + 1) * (swatch_w + 4) + 16
    legend.append(
        f'<rect x="{nd_x}" y="{legend_y}" width="{swatch_w}" height="12" '
        f'fill="{NO_DATA_FILL}" stroke="{NO_DATA_STROKE}" stroke-dasharray="2 2"/>'
        f'<text x="{nd_x + swatch_w + 6}" y="{legend_y + 10}" font-size="10" '
        f'fill="{MUTED}">no usable estimate ({n_missing})</text>'
        f'<text x="{nd_x + swatch_w + 160}" y="{legend_y + 10}" font-size="10" '
        f'fill="{MUTED}">both panels share these breaks</text>')

    y = height - 76
    footer = []
    for line in disclosures:
        footer.append(
            f'<text x="16" y="{y}" font-size="10" fill="{WARN}">{esc("! " + line)}</text>')
        y += 13
    for line in source_lines:
        footer.append(f'<text x="16" y="{y}" font-size="10" fill="{MUTED}">{esc(line)}</text>')
        y += 13

    banner = ""
    if data_mode == "fixture":
        banner = (f'<rect x="0" y="0" width="{width}" height="22" fill="{WARN}"/>'
                  f'<text x="10" y="15" font-size="11" fill="#ffffff" font-weight="600">'
                  f"FIXTURE MODE - synthetic test values, not census estimates</text>")
    top = 22 if banner else 0

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" \
viewBox="0 0 {width} {height}" font-family="Iowan Old Style, Palatino, Georgia, serif" \
role="img" aria-label="{esc(title)}. {esc(subtitle)}">
<rect width="{width}" height="{height}" fill="#ffffff"/>{banner}
<text x="16" y="{top + 26}" font-size="17" fill="{INK}">{esc(title)}</text>
<text x="16" y="{top + 45}" font-size="12" fill="{MUTED}">{esc(subtitle)}</text>
<g transform="translate(0,{top + 70})">{''.join(groups)}</g>
<line x1="16" y1="{legend_y - 14}" x2="{width - 16}" y2="{legend_y - 14}" stroke="{RULE}"/>
{''.join(legend)}
{''.join(footer)}
</svg>"""


# ---------------------------------------------------------------------------
# Shared-scale group chart
# ---------------------------------------------------------------------------

def group_chart_svg(*, rows: list[dict], title: str, subtitle: str, unit: str,
                    source_lines: list[str], series_labels: list[str],
                    width: int = 760, data_mode: str = "live",
                    disclosures: list[str] | None = None,
                    scope_note: str = "") -> str:
    """One row per group on a single shared scale.

    ``rows`` is ``[{"label": str, "values": [{"e": float|None, "m": float|None,
    "note": str}, ...]}]`` with one entry per series, in ``series_labels`` order.
    Missing values are drawn as an explicit gap marker, never as a zero bar.
    """
    disclosures = list(disclosures or [])
    if scope_note:
        disclosures = [scope_note] + disclosures
    label_w = 230
    row_h = 26 if len(series_labels) == 1 else 34
    # The tick labels sit 16 px above the plot. At 68 their tops overlapped the
    # subtitle's descenders (visible in the printed brief); 82 clears them.
    top = (22 if data_mode == "fixture" else 0) + 82
    chart_h = max(1, len(rows)) * row_h
    footer_h = 40 + 13 * (len(source_lines) + len(disclosures))
    height = top + chart_h + 56 + footer_h
    plot_w = width - label_w - 90

    maxv = max(
        [v["e"] + (v.get("m") or 0) for r in rows for v in r["values"] if v.get("e") is not None]
        or [1.0]
    )
    maxv = maxv * 1.08 or 1.0

    def x_of(v: float) -> float:
        return label_w + (v / maxv) * plot_w

    ticks: list[str] = []
    for i in range(5):
        v = maxv * i / 4
        x = x_of(v)
        ticks.append(
            f'<line x1="{x:.1f}" y1="{top - 10}" x2="{x:.1f}" y2="{top + chart_h}" '
            f'stroke="{RULE}" stroke-width="0.8"/>'
            f'<text x="{x:.1f}" y="{top - 16}" text-anchor="middle" font-size="10" '
            f'fill="{MUTED}">{esc(fmt(v, unit))}</text>'
        )

    colors = [ACCENT, ACCENT_ALT]
    body: list[str] = []
    for i, r in enumerate(rows):
        y0 = top + i * row_h
        body.append(
            f'<text x="{label_w - 10}" y="{y0 + row_h / 2 + 4:.0f}" text-anchor="end" '
            f'font-size="12" fill="{INK}">{esc(r["label"])}</text>'
        )
        bar_h = (row_h - 10) / len(series_labels)
        for s, v in enumerate(r["values"]):
            by = y0 + 5 + s * bar_h
            if v.get("e") is None:
                body.append(
                    f'<line x1="{label_w}" y1="{by + bar_h / 2:.1f}" '
                    f'x2="{label_w + 26}" y2="{by + bar_h / 2:.1f}" '
                    f'stroke="{NO_DATA_STROKE}" stroke-width="1" stroke-dasharray="3 3"/>'
                    f'<text x="{label_w + 32}" y="{by + bar_h / 2 + 4:.1f}" font-size="10" '
                    f'fill="{MUTED}">{esc(v.get("note") or "no usable estimate")}</text>'
                )
                continue
            x1 = x_of(v["e"])
            body.append(
                f'<rect x="{label_w}" y="{by:.1f}" width="{max(x1 - label_w, 1):.1f}" '
                f'height="{bar_h - 2:.1f}" fill="{colors[s % len(colors)]}" '
                f'opacity="{1 if s == 0 else 0.85}"><title>'
                f'{esc(r["label"])} - {esc(series_labels[s])}: {esc(fmt(v["e"], unit))}'
                f'{esc(_uncertainty_phrase(v, unit))}'
                f"</title></rect>"
            )
            has_bar = v.get("m") is not None and not v.get("controlled")
            if has_bar:
                lo, hi = x_of(max(v["e"] - v["m"], 0)), x_of(v["e"] + v["m"])
                cy = by + (bar_h - 2) / 2
                body.append(
                    f'<line x1="{lo:.1f}" y1="{cy:.1f}" x2="{hi:.1f}" y2="{cy:.1f}" '
                    f'stroke="{INK}" stroke-width="1"/>'
                    f'<line x1="{lo:.1f}" y1="{cy - 3:.1f}" x2="{lo:.1f}" y2="{cy + 3:.1f}" '
                    f'stroke="{INK}" stroke-width="1"/>'
                    f'<line x1="{hi:.1f}" y1="{cy - 3:.1f}" x2="{hi:.1f}" y2="{cy + 3:.1f}" '
                    f'stroke="{INK}" stroke-width="1"/>'
                )
            body.append(
                f'<text x="{hi + 6 if has_bar else x1 + 6:.1f}" '
                f'y="{by + bar_h / 2 + 3:.1f}" font-size="10" fill="{MUTED}">'
                f"{esc(fmt(v['e'], unit))}</text>"
            )

    key = []
    if len(series_labels) > 1:
        for s, lab in enumerate(series_labels):
            x = label_w + s * 170
            key.append(
                f'<rect x="{x}" y="{top + chart_h + 16}" width="11" height="11" '
                f'fill="{colors[s % len(colors)]}"/>'
                f'<text x="{x + 16}" y="{top + chart_h + 26}" font-size="11" '
                f'fill="{INK}">{esc(lab)}</text>'
            )

    y = top + chart_h + 52
    footer = []
    for line in disclosures:
        footer.append(
            f'<text x="16" y="{y}" font-size="10" fill="{WARN}">{esc("! " + line)}</text>'
        )
        y += 13
    for line in source_lines:
        footer.append(f'<text x="16" y="{y}" font-size="10" fill="{MUTED}">{esc(line)}</text>')
        y += 13

    banner = ""
    if data_mode == "fixture":
        banner = (
            f'<rect x="0" y="0" width="{width}" height="22" fill="{WARN}"/>'
            f'<text x="10" y="15" font-size="11" fill="#ffffff" font-weight="600">'
            f"FIXTURE MODE - synthetic test values, not census estimates</text>"
        )
    ttop = 22 if banner else 0

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height:.0f}" \
viewBox="0 0 {width} {height:.0f}" font-family="Iowan Old Style, Palatino, Georgia, serif" \
role="img" aria-label="{esc(title)}. {esc(subtitle)}">
<rect width="{width}" height="{height:.0f}" fill="#ffffff"/>{banner}
<text x="16" y="{ttop + 26}" font-size="17" fill="{INK}">{esc(title)}</text>
<text x="16" y="{ttop + 45}" font-size="12" fill="{MUTED}">{esc(subtitle)}</text>
{''.join(ticks)}
{''.join(body)}
{''.join(key)}
{''.join(footer)}
</svg>"""
