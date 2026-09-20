"""A print-ready brief, built from the same validated selection as everything else.

The output is one self-contained HTML file: no scripts, no fonts to fetch, no
rendering dependency. The browser's own print dialog turns it into a PDF.

Three rules shape it:

* It answers the question the user actually chose, in that question's words,
  and states what it is counting and out of what before it shows a number.
* Uncertainty, comparison eligibility and freshness are three separate
  statements, because they are three different things and collapsing them into
  one score would hide which one is the problem.
* Anything a person wrote is visibly marked as such, so a reader can tell a
  computed figure from an opinion about it.
"""

from __future__ import annotations

import html
from typing import Any

from .figures import esc as _svg_esc  # noqa: F401  (kept for symmetry)

PRINT_CSS = """
:root{--ink:#1d1f21;--muted:#5d6166;--rule:#d9dbde;--soft:#f7f6f4;
--accent:#27618e;--warn:#7a3b12;--warnbg:#fbf0e6;
--serif:"Iowan Old Style",Palatino,"Palatino Linotype",Georgia,serif;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
--mono:ui-monospace,"SF Mono",Consolas,"Liberation Mono",monospace}
*{box-sizing:border-box}
body{margin:0;background:#eceae6;color:var(--ink);font-family:var(--sans);
font-size:13.5px;line-height:1.5}
.sheet{max-width:830px;margin:22px auto;background:#fff;padding:40px 46px 52px;
box-shadow:0 1px 4px rgba(0,0,0,.12)}
h1{font-family:var(--serif);font-size:25px;line-height:1.2;margin:0 0 6px}
h2{font-family:var(--serif);font-size:16px;margin:26px 0 8px;
padding-bottom:4px;border-bottom:1px solid var(--rule)}
h3{font-size:13px;margin:14px 0 4px}
p{margin:0 0 9px}
.lede{color:var(--muted);font-size:14px;margin:0 0 18px}
.meta{display:grid;grid-template-columns:150px 1fr;gap:3px 14px;
font-size:12.5px;margin:0 0 4px}
.meta dt{color:var(--muted)}
.meta dd{margin:0}
.figure{margin:16px 0 8px;text-align:center}
.figure svg{max-width:100%;height:auto}
figcaption{font-size:11.5px;color:var(--muted);margin-top:6px;text-align:left}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin:6px 0 4px;
table-layout:fixed}
td,th{overflow-wrap:anywhere}
th{text-align:left;border-bottom:1px solid var(--rule);padding:5px 8px;
font-weight:600;background:var(--soft)}
td{padding:4px 8px;border-bottom:1px solid #eceef0}
td.num{text-align:right;font-variant-numeric:tabular-nums}
td.missing{color:var(--warn);font-style:italic}
tr.benchmark td{background:var(--soft);font-weight:600}
.quality{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:8px 0}
.quality section{border:1px solid var(--rule);padding:10px 12px;background:#fff}
.quality h3{margin:0 0 4px;font-size:12px;color:var(--muted);
text-transform:uppercase;letter-spacing:.04em}
.quality p{font-size:12.5px;margin:0 0 5px}
.state{font-weight:600}
.state.ok{color:#1d5c38}
.state.caution{color:var(--warn)}
.state.blocked{color:#8c2f1d}
.limits li{margin-bottom:5px}
.note{border-left:3px solid var(--accent);background:#f2f6fa;padding:10px 12px;
margin:10px 0}
.note .who{font-size:11px;color:var(--muted);text-transform:uppercase;
letter-spacing:.04em;margin:0 0 4px}
.fixture{background:var(--warn);color:#fff;padding:8px 12px;font-weight:600;
font-size:12px;margin:-40px -46px 24px}
.repro{font-family:var(--mono);font-size:10.5px;color:var(--muted);
word-break:break-all}
.repro-table{font-family:var(--mono);font-size:10.5px}
.repro-table col.role{width:16%}
.repro-table col.file{width:56%}
.repro-table col.hash{width:28%}
.footer{margin-top:26px;padding-top:10px;border-top:1px solid var(--rule);
font-size:11px;color:var(--muted)}
@media print{
 body{background:#fff}
 .sheet{box-shadow:none;margin:0;max-width:none;padding:0 0 12mm}
 .fixture{margin:0 0 10mm}
 h2{break-after:avoid}
 table{break-inside:auto}
 tr{break-inside:avoid}
 .quality{break-inside:avoid}
 .figure{break-inside:avoid}
 @page{margin:16mm}
}
@media (max-width:720px){
 .sheet{padding:24px 16px 32px;margin:0}
 table{font-size:12px}
 td,th{padding:4px 5px}
 .repro-table{font-size:9.5px}
 .quality{grid-template-columns:1fr}
 .meta{grid-template-columns:1fr;gap:1px 0}
 .meta dt{margin-top:6px}
 .fixture{margin:-24px -18px 18px}
}
"""


def e(text: Any) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "no data"
    if unit == "percent":
        return f"{value:,.1f}%"
    return f"{value:,.0f}"


def _quality_panels(context: dict[str, Any]) -> str:
    """Three separate statements. No combined score."""
    unc = context["uncertainty"]
    comp = context["comparison"]
    fresh = context["freshness"]

    def panel(title, state_word, state_class, body_lines):
        body = "".join(f"<p>{e(line)}</p>" for line in body_lines)
        return (f'<section><h3>{e(title)}</h3>'
                f'<p class="state {state_class}">{e(state_word)}</p>{body}</section>')

    return ('<div class="quality">'
            + panel("Uncertainty", unc["state"], unc["class"], unc["lines"])
            + panel("Comparison", comp["state"], comp["class"], comp["lines"])
            + panel("Period", fresh["state"], fresh["class"], fresh["lines"])
            + '</div>')


def build(*, question: dict, summary: dict, rows: list[dict], measure: dict,
          release: dict, figure_svg: str, quality: dict,
          benchmark: dict | None, limitations: list[str],
          sources: list[str], reproducibility: dict,
          analyst_note: str = "", data_mode: str = "live",
          generated_at: str = "") -> str:
    """Render the brief. Every value passed in came from the same selection."""
    unit = measure["unit"]
    banner = ""
    if data_mode == "fixture":
        banner = ('<div class="fixture">FIXTURE MODE — every value below is '
                  'synthetic test data, and the shapes are generated rectangles, '
                  'not boundaries. Nothing here is a census finding.</div>')

    body_rows = []
    for row in rows:
        est = row.get("estimate")
        moe = row.get("moe")
        cells = [f'<td>{e(row["name"])}</td>']
        if est is None:
            cells.append(f'<td class="num missing">no data</td>')
        else:
            cells.append(f'<td class="num">{e(fmt(est, unit))}</td>')
        cells.append(f'<td class="num">{e("±" + fmt(moe, unit)) if moe is not None else "—"}</td>')
        cells.append(f'<td>{e(row.get("quality", ""))}</td>')
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    if benchmark and benchmark.get("available"):
        b_est = benchmark.get("estimate")
        b_moe = benchmark.get("moe") if benchmark.get("moe_status") == "ok" else None
        body_rows.append(
            '<tr class="benchmark">'
            f'<td>{e(benchmark["label"])}</td>'
            + (f'<td class="num">{e(fmt(b_est, unit))}</td>' if b_est is not None
               else '<td class="num missing">no data</td>')
            + f'<td class="num">{e("±" + fmt(b_moe, unit)) if b_moe is not None else "—"}</td>'
            + f'<td>{e("reference value" if b_moe is not None else "reference value; margin of error unavailable")}</td>'
            '</tr>')

    meta_rows = "".join(
        f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in [
            ("Question", summary["question"]),
            ("Places", summary["places"]),
            ("Reference period", summary["period"]),
            ("Measure", summary["measure"]),
            ("What is counted", summary["counted"]),
            ("Out of", summary["out_of"]),
            ("Units", summary["unit"]),
        ])

    note_block = ""
    if analyst_note.strip():
        paragraphs = "".join(f"<p>{e(p)}</p>"
                             for p in analyst_note.strip().split("\n") if p.strip())
        note_block = ('<div class="note"><p class="who">Analyst note — written by a '
                      'person, not computed from the data</p>' + paragraphs + "</div>")

    limit_items = "".join(f"<li>{e(x)}</li>" for x in limitations)
    source_items = "".join(f"<p>{e(x)}</p>" for x in sources)

    repro_rows = "".join(
        f"<tr><td>{e(i['role'])}</td><td>{e(i['path'])}</td>"
        f"<td>{e(i['sha256'][:16])}…</td></tr>"
        for i in reproducibility.get("inputs", [])[:40])

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(question['title'])} — {e(summary['places'])} — {e(release['period_label'])}</title>
<style>{PRINT_CSS}</style></head>
<body><div class="sheet">{banner}
<h1>{e(question['title'])}</h1>
<p class="lede">{e(summary['places'])} · {e(release['period_label'])}</p>

<h2>What this brief shows</h2>
<dl class="meta">{meta_rows}</dl>
<p>{e(question['answers'])}</p>
{note_block}

<h2>{e(measure['label'])}</h2>
<div class="figure">{figure_svg}</div>
<figcaption>{e(measure['definition_note'])} Universe: {e(measure['universe_published'][0]
    if measure.get('universe_published') else measure['universe_note'])}.</figcaption>

<table>
<thead><tr><th scope="col">Area</th><th scope="col">Estimate</th>
<th scope="col">Margin of error (90%)</th><th scope="col">Reliability</th></tr></thead>
<tbody>{''.join(body_rows)}</tbody>
</table>
<p style="font-size:11.5px;color:#5d6166">An empty estimate means the value is
unavailable, with the reason recorded in the exported data. It is never a zero.</p>

<h2>How far to trust this</h2>
{_quality_panels(quality)}

<h2>What this brief does not say</h2>
<ul class="limits">{limit_items}</ul>

<h2>Source</h2>
{source_items}

<h2>Reproducibility record</h2>
<p style="font-size:12px">Built from manifest
<span class="repro">{e(reproducibility.get('manifest_id', 'unrecorded'))}</span>
at code revision <span class="repro">{e(reproducibility.get('code_revision', 'unknown'))}</span>,
data mode <strong>{e(data_mode)}</strong>, generated {e(generated_at)}.</p>
<p style="font-size:12px">The saved brief records the digest of every file below.
Reopening it checks them and refuses if any has changed, naming the file. That is a
tamper check, not an archive: it does not keep a copy of the data or restore an
earlier version.</p>
<table class="repro-table">
<colgroup><col class="role"><col class="file"><col class="hash"></colgroup>
<thead><tr><th scope="col">Role</th><th scope="col">File</th>
<th scope="col">SHA-256</th></tr></thead><tbody>{repro_rows}</tbody></table>

<div class="footer">Generated locally by Census Explorer. No part of this brief was
sent to or retrieved from a network service while it was produced.</div>
</div></body></html>"""
