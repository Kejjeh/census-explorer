"""CSV and provenance exports.

An export is a bundle, not a bare table.  Every row carries its period label,
product, geography vintage, universe, status and margin of error, and the
bundle carries the manifests, checksums and measure definitions that produced
it.  A value that is unavailable is exported as an empty cell plus a stated
reason — never as zero.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from . import provenance
from .config import MeasureDef, ProjectConfig, Release
from .projects import SavedProject
from .redact import redact_structure

CSV_COLUMNS = [
    "release_id", "period_label", "product", "geography_vintage", "boundary_release",
    "level", "geoid", "area_name", "measure_id", "measure_label", "unit", "kind",
    "estimate", "estimate_status", "estimate_note",
    "moe_90pct", "moe_status", "moe_note",
    "cv_percent", "reliability", "numerator", "denominator",
    "universe", "numerator_cells", "denominator_cells", "source_flags",
]


def _row(release: Release, area: dict, measure: MeasureDef, v: dict) -> list:
    return [
        release.release_id, release.period_label, release.product_label,
        release.geography_vintage, release.boundary_release,
        area["level"], area["geoid"], area["name"],
        measure.measure_id, measure.label, measure.unit, measure.kind,
        "" if v.get("e") is None else v["e"], v.get("es", ""), v.get("er", ""),
        "" if v.get("m") is None else v["m"], v.get("ms", ""), v.get("mr", ""),
        "" if v.get("cv") is None else v["cv"], v.get("rel", ""),
        "" if v.get("n") is None else v["n"],
        "" if v.get("d") is None else v["d"],
        measure.universe_note,
        ";".join(measure.numerator_cells), ";".join(measure.denominator_cells),
        ";".join(v.get("flags", [])),
    ]


def build_csv(releases: list[Release], areas: dict[str, dict],
              measures: list[MeasureDef],
              values: dict[tuple[str, str], dict[str, dict]]) -> str:
    """``values`` is keyed by (release_id, measure_id) -> {geoid: compact value}."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for release in releases:
        for measure in measures:
            per_geo = values.get((release.release_id, measure.measure_id), {})
            for geoid in sorted(per_geo):
                area = areas.get(geoid)
                if area is None:
                    continue
                w.writerow(_row(release, area, measure, per_geo[geoid]))
    return buf.getvalue()


def build_provenance(repo_root: Path, config: ProjectConfig,
                     releases: list[Release], measures: list[MeasureDef],
                     project: SavedProject | None,
                     datasets: list[dict], compatibility: dict | None = None,
                     data_mode: str = "live") -> dict[str, Any]:
    manifests = []
    seen: set[str] = set()
    for d in datasets:
        mid = d.get("manifest_id")
        if mid and mid not in seen:
            seen.add(mid)
            p = repo_root / "data/manifests" / f"{mid}.json"
            if p.exists():
                manifests.append(provenance.read_json(p))
    # Include every manifest that produced the cached inputs, not only the one
    # recorded on the dataset, so the bundle can be re-verified end to end.
    mdir = repo_root / "data/manifests"
    if mdir.exists():
        for p in sorted(mdir.glob("*.json")):
            doc = provenance.read_json(p)
            if doc.get("manifest_id") not in seen:
                seen.add(doc.get("manifest_id"))
                manifests.append(doc)

    doc = {
        "schema_version": 1,
        "generated_at": provenance.utc_now(),
        "generator": f"census-explorer {provenance.code_revision(repo_root)}",
        "data_mode": data_mode,
        "data_mode_note": (
            "FIXTURE MODE: values in this bundle are synthetic test data and are "
            "not census estimates." if data_mode == "fixture"
            else "Values are official published census estimates as retrieved."
        ),
        "releases": [r.to_json() for r in releases],
        "measures": [m.to_json() for m in measures],
        "datasets": [
            {k: d.get(k) for k in ("release", "built_at", "code_revision", "transport",
                                   "manifest_id", "data_mode", "tables", "join_reports",
                                   "diagnostics", "warnings")}
            for d in datasets
        ],
        "manifests": manifests,
        "project": project.to_json() if project else None,
        "comparison": compatibility,
        "citation": [r.citation for r in releases],
        "terms": (
            "Census Bureau data are in the public domain; cite the source and the "
            "reference period. Cartographic boundary files are generalized for "
            "display and are not legal boundary descriptions."
        ),
        "reading_the_columns": {
            "estimate": "Empty means unavailable; read estimate_status and estimate_note. "
                        "An empty estimate is never a zero.",
            "moe_90pct": "Margin of error at the 90% confidence level, in the same unit "
                         "as the estimate. Empty means unavailable.",
            "cv_percent": "Coefficient of variation, computed only for counts with a "
                          "valid numeric margin of error and a non-zero estimate, "
                          "using SE = MOE / 1.645.",
            "denominator": "Shares are computed from summed numerators over summed "
                           "denominators. Percentages are never summed or averaged.",
        },
    }
    return redact_structure(doc)


def write_bundle(out_dir: Path, csv_text: str, provenance_doc: dict,
                 figure_svg: str | None = None, readme: str | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    p = out_dir / "data.csv"
    p.write_text(csv_text, encoding="utf-8")
    written.append(p)
    p = out_dir / "provenance.json"
    p.write_text(json.dumps(provenance_doc, indent=2, sort_keys=True), encoding="utf-8")
    written.append(p)
    if figure_svg:
        p = out_dir / "figure.svg"
        p.write_text(figure_svg, encoding="utf-8")
        written.append(p)
    p = out_dir / "README.txt"
    p.write_text(readme or default_readme(provenance_doc), encoding="utf-8")
    written.append(p)
    return written


def default_readme(provenance_doc: dict) -> str:
    releases = ", ".join(r["period_label"] for r in provenance_doc.get("releases", []))
    lines = [
        "Census Explorer export",
        "======================",
        "",
        f"Generated: {provenance_doc.get('generated_at')}",
        f"Periods:   {releases}",
        f"Data mode: {provenance_doc.get('data_mode')}",
        "",
        provenance_doc.get("data_mode_note", ""),
        "",
        "Files",
        "-----",
        "data.csv         One row per area, measure and period, with status and",
        "                 margin of error columns. An empty estimate means the value",
        "                 is unavailable; the reason is in estimate_note.",
        "provenance.json  Releases, measure definitions, universes, retrieval records,",
        "                 checksums, geographic join accounting and any comparison rules",
        "                 that applied.",
        "figure.svg       The exported figure, if one was requested.",
        "",
        "Citation",
        "--------",
    ]
    lines.extend(f"  {c}" for c in provenance_doc.get("citation", []))
    lines += ["", provenance_doc.get("terms", ""), ""]
    return "\n".join(lines)
