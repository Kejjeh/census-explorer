"""Turn cached raw responses into a validated analysis dataset.

Reading is strictly offline: this module opens files under ``data/raw`` and
never touches the network.  It classifies every source value, computes the
configured measures, accounts for the geographic join, and records diagnostics
(such as the difference between two tables' nominally similar totals) rather
than reconciling them behind the scenes.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import geography, measures as measures_mod, provenance
from .config import ProjectConfig, Release
from .metadata import ReleaseMetadata
from .redact import redact
from .sentinels import Cell, classify

SUMMARY_LEVEL_NAME = {"05000": "county", "14000": "tract"}


class DatasetError(ValueError):
    pass


# --------------------------------------------------------------------------
# Raw readers
# --------------------------------------------------------------------------

_SF_COL_RE = re.compile(r"^([A-Z0-9]+)_([EM])(\d{3})$")


def read_summary_file(path: Path) -> dict[str, dict[str, tuple[str | None, str | None]]]:
    """Read a cached Summary File subset into ``{GEO_ID: {cell: (est, moe)}}``."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        header = fh.readline().rstrip("\n").rstrip("\r").split("|")
        if not header or header[0] != "GEO_ID":
            raise DatasetError(f"{path}: first column is not GEO_ID")
        columns: list[tuple[str, str] | None] = [None]
        for name in header[1:]:
            m = _SF_COL_RE.match(name)
            columns.append((f"{m.group(1)}_{m.group(3)}", m.group(2)) if m else None)

        out: dict[str, dict[str, tuple[str | None, str | None]]] = {}
        for lineno, line in enumerate(fh, start=2):
            line = line.rstrip("\n").rstrip("\r")
            if not line:
                continue
            parts = line.split("|")
            if len(parts) != len(header):
                raise DatasetError(
                    f"{path}:{lineno}: {len(parts)} fields, header has {len(header)}"
                )
            geo_id = parts[0]
            if geo_id in out:
                raise DatasetError(f"{path}: duplicate GEO_ID {geo_id}")
            cells: dict[str, list[str | None]] = {}
            for value, column in zip(parts[1:], columns[1:]):
                if column is None:
                    continue
                cell, which = column
                slot = cells.setdefault(cell, [None, None])
                slot[0 if which == "E" else 1] = value
            out[geo_id] = {c: (v[0], v[1]) for c, v in cells.items()}
    return out


def read_api_response(path: Path) -> dict[str, dict[str, tuple[str | None, str | None]]]:
    """Read a cached Census API JSON array into ``{GEO_ID: {cell: (est, moe)}}``."""
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, list) or len(doc) < 1:
        raise DatasetError(f"{path}: not a Census API response array")
    header = doc[0]
    try:
        geo_index = header.index("GEO_ID")
    except ValueError:
        raise DatasetError(f"{path}: response has no GEO_ID column") from None

    columns: dict[int, tuple[str, str]] = {}
    for i, name in enumerate(header):
        m = re.match(r"^([A-Z0-9]+_\d{3})([EM])$", str(name))
        if m:
            columns[i] = (m.group(1), m.group(2))

    out: dict[str, dict[str, tuple[str | None, str | None]]] = {}
    for row in doc[1:]:
        geo_id = row[geo_index]
        cells: dict[str, list[str | None]] = {}
        for i, (cell, which) in columns.items():
            slot = cells.setdefault(cell, [None, None])
            slot[0 if which == "E" else 1] = row[i]
        merged = {c: (v[0], v[1]) for c, v in cells.items()}
        if geo_id in out:
            out[geo_id].update(merged)
        else:
            out[geo_id] = merged
    return out


# --------------------------------------------------------------------------
# Dataset assembly
# --------------------------------------------------------------------------

@dataclass
class Area:
    geoid: str
    name: str
    level: str

    def to_json(self) -> dict:
        return {"geoid": self.geoid, "name": self.name, "level": self.level}


@dataclass
class BuildResult:
    release: Release
    areas: dict[str, Area]
    values: dict[str, dict[str, dict]]           # measure_id -> geoid -> value json
    join_reports: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    availability: dict[str, dict[str, bool]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _area_name(config: ProjectConfig, geoid: str, level: str,
               geo_names: dict[str, str]) -> str:
    if level == "county":
        return config.county_name(geoid)
    name = geo_names.get(geoid)
    if name:
        borough = config.county_name(geoid[:5])
        return f"{name}, {borough}"
    return geoid


def build(repo_root: Path, config: ProjectConfig, release: Release,
          meta: ReleaseMetadata, levels: list[str],
          transport: str = "summary-file") -> BuildResult:
    """Assemble the analysis dataset for one release from the raw cache."""
    tables = config.all_tables()
    raw: dict[str, dict[str, tuple[str | None, str | None]]] = {}

    for table in tables:
        paths: list[Path] = []
        if transport == "summary-file":
            p = repo_root / f"data/raw/acs/{release.release_id}/summary_file/{table}.psv"
            if p.exists():
                paths = [p]
        else:
            api_dir = repo_root / f"data/raw/acs/{release.release_id}/api"
            paths = sorted(api_dir.glob(f"{table}_*.json")) if api_dir.exists() else []
        if not paths:
            raise DatasetError(
                f"no cached {transport} response for table {table} "
                f"({release.release_id}). Run the fetch command first."
            )
        for p in paths:
            chunk = read_summary_file(p) if transport == "summary-file" else read_api_response(p)
            for geo_id, cells in chunk.items():
                raw.setdefault(geo_id, {}).update(cells)

    # Classify, restrict to the requested levels and the project's counties.
    wanted_counties = set(config.county_geoids)
    areas: dict[str, Area] = {}
    classified: dict[str, dict[str, Cell]] = {}
    classified_moe: dict[str, dict[str, Cell]] = {}

    for geo_id, cells in raw.items():
        try:
            summary_level, geoid = geography.split_geo_id(geo_id)
        except geography.GeographyError:
            continue
        level = SUMMARY_LEVEL_NAME.get(summary_level[:5])
        if level is None or level not in levels:
            continue
        if level == "county" and geoid not in wanted_counties:
            continue
        if level == "tract" and geoid[:5] not in wanted_counties:
            continue
        geography.validate_geoid(geoid, level)
        areas[geoid] = Area(geoid=geoid, name=geoid, level=level)
        est: dict[str, Cell] = {}
        moe: dict[str, Cell] = {}
        for cell, (e, m) in cells.items():
            est[cell] = classify(e)
            moe[cell] = classify(m)
        classified[geoid] = est
        classified_moe[geoid] = moe

    if not areas:
        raise DatasetError(
            "no observations matched the configured geography; check the cached "
            "responses and the county list in config/project.json"
        )

    # Compute measures.
    values: dict[str, dict[str, dict]] = {}
    availability: dict[str, dict[str, bool]] = {}
    warnings: list[str] = []
    for m in config.measures_for_release():
        per_geo: dict[str, dict] = {}
        level_ok = {lvl: False for lvl in levels}
        for geoid, area in areas.items():
            mv = measures_mod.compute(m, geoid, classified[geoid], classified_moe[geoid])
            per_geo[geoid] = mv.to_json()
            if mv.estimate_status == measures_mod.OK:
                level_ok[area.level] = True
        values[m.measure_id] = per_geo
        availability[m.measure_id] = level_ok
        for lvl, ok in level_ok.items():
            if not ok:
                warnings.append(
                    f"{m.measure_id}: no usable estimate at {lvl} level in "
                    f"{release.period_label}; the measure is marked unavailable there"
                )

    return BuildResult(
        release=release, areas=areas, values=values,
        availability=availability, warnings=warnings,
    )


def diagnostics_between_tables(result: BuildResult,
                               classified: dict[str, dict[str, Cell]] | None = None
                               ) -> list[dict]:
    """Cross-table checks that must be reported, not silently reconciled."""
    return result.diagnostics


def add_cross_table_diagnostics(repo_root: Path, release: Release,
                                result: BuildResult, transport: str) -> None:
    """Compare nominally similar totals published in different tables.

    B05002 counts the foreign-born population; B05006 counts the foreign-born
    population *excluding people born at sea*.  Their totals are expected to be
    close but not identical.  The same applies to B01003 and B05002 totals.
    """
    raw: dict[str, dict[str, tuple[str | None, str | None]]] = {}
    for table in ("B01003", "B05002", "B05006"):
        if transport == "summary-file":
            p = repo_root / f"data/raw/acs/{release.release_id}/summary_file/{table}.psv"
            chunks = [read_summary_file(p)] if p.exists() else []
        else:
            api_dir = repo_root / f"data/raw/acs/{release.release_id}/api"
            chunks = [read_api_response(p) for p in sorted(api_dir.glob(f"{table}_*.json"))]
        for chunk in chunks:
            for geo_id, cells in chunk.items():
                raw.setdefault(geo_id, {}).update(cells)

    checks = [
        ("total_population_b01003_vs_b05002", "B01003_001", "B05002_001",
         "Total population as published in B01003 and in B05002."),
        ("foreign_born_b05002_vs_b05006", "B05002_013", "B05006_001",
         "Foreign-born population (B05002) against the B05006 universe, which "
         "excludes people born at sea. A small difference is expected."),
    ]
    for check_id, left, right, note in checks:
        rows = []
        for geoid, area in sorted(result.areas.items()):
            if area.level != "county":
                continue
            geo_id = f"0500000US{geoid}"
            cells = raw.get(geo_id, {})
            a = classify(cells.get(left, (None, None))[0])
            b = classify(cells.get(right, (None, None))[0])
            if not (a.is_number and b.is_number):
                continue
            rows.append({
                "geoid": geoid,
                "name": area.name,
                left: a.value,
                right: b.value,
                "difference": a.value - b.value,
            })
        result.diagnostics.append({
            "check_id": check_id,
            "note": note,
            "level": "county",
            "rows": rows,
            "max_absolute_difference": max((abs(r["difference"]) for r in rows), default=None),
        })


def attach_geography(repo_root: Path, config: ProjectConfig, release: Release,
                     result: BuildResult, levels: list[str]) -> dict[str, Path]:
    """Build the GeoJSON layers, name the areas, and account for every join.

    Runs after the observations are assembled so that the join report compares
    the actual retained observations against the actual boundary features.
    """
    from .retrieve import boundaries as boundaries_mod

    written: dict[str, Path] = {}
    out_dir = repo_root / "data/processed" / release.release_id
    out_dir.mkdir(parents=True, exist_ok=True)

    for level in levels:
        zip_rel = boundaries_mod.cache_rel_path(release, level, config.state_fips)
        zip_path = repo_root / zip_rel
        if not zip_path.exists():
            result.warnings.append(
                f"{level} boundaries for {release.boundary_release} are not cached "
                f"({zip_rel}); the map cannot draw this level. Run: fetch geography"
            )
            continue
        observed = sorted(g for g, a in result.areas.items() if a.level == level)
        collection, feature_geoids = geography.build_geojson(
            zip_path, level, set(observed), release
        )
        report = geography.join(feature_geoids, observed, level, release.boundary_release)
        report_json = report.to_json()
        # Report the population that falls outside the join, not just the count
        # of identifiers, so an unmatched area can be judged rather than guessed.
        pop = result.values.get("total_population", {})
        unmatched_pop = 0.0
        unmatched_pop_known = True
        for g in report.unmatched_observations:
            v = pop.get(g)
            if not v or v.get("estimate") is None:
                unmatched_pop_known = False
                continue
            unmatched_pop += float(v["estimate"])
        report_json["unmatched_observation_population"] = (
            unmatched_pop if unmatched_pop_known else None
        )
        report_json["unmatched_observation_population_note"] = (
            "Total population of observations with no boundary feature at this "
            "vintage." if unmatched_pop_known else
            "Population of some unmatched observations is unavailable."
        )
        result.join_reports.append(report_json)
        if not report.is_complete:
            result.warnings.append(redact(report.summary()))

        names = {f["properties"]["GEOID"]: f["properties"]["name"]
                 for f in collection["features"]}
        for geoid in observed:
            result.areas[geoid].name = _area_name(config, geoid, level, names)
        for f in collection["features"]:
            f["properties"]["name"] = result.areas[f["properties"]["GEOID"]].name

        path = out_dir / f"geography_{level}.geojson"
        geography.write_geojson(path, collection)
        written[level] = path
    return written


def compact_value(value: dict) -> dict:
    """Trim a measure value for storage without losing meaning.

    Null fields are omitted rather than stored, and numbers are rounded to the
    precision the source supports.  ``estimate_status`` and ``moe_status`` are
    always present, so a consumer can never mistake an omitted estimate for a
    zero.
    """
    def num(x, places):
        return None if x is None else round(float(x), places)

    out: dict[str, Any] = {
        "e": num(value["estimate"], 0 if value["kind"] == "count" else 4),
        "es": value["estimate_status"],
        "m": num(value["moe"], 0 if value["kind"] == "count" else 4),
        "ms": value["moe_status"],
    }
    if value.get("estimate_reason"):
        out["er"] = value["estimate_reason"]
    if value.get("moe_reason"):
        out["mr"] = value["moe_reason"]
    if value.get("cv_percent") is not None:
        out["cv"] = round(float(value["cv_percent"]), 2)
        out["rel"] = value["reliability"]
    if value.get("numerator") is not None and value["kind"] == "share":
        out["n"] = num(value["numerator"], 0)
        out["d"] = num(value["denominator"], 0)
    if value.get("controlled"):
        # Explicit provenance. Consumers must read this rather than looking for
        # the word "controlled" in a source flag: flags are pooled across the
        # numerator and the denominator, so one cell's flag says nothing about
        # the result.
        out["ctl"] = True
    if value.get("source_flags"):
        out["flags"] = value["source_flags"]
    return out


def write_processed(repo_root: Path, config: ProjectConfig, release: Release,
                    meta: ReleaseMetadata, result: BuildResult,
                    manifest_id: str, data_mode: str,
                    transport: str, manifest_ids: list[str] | None = None) -> Path:
    """Write the dataset the local service reads.  Nothing here fetches."""
    out_dir = repo_root / "data/processed" / release.release_id
    out_dir.mkdir(parents=True, exist_ok=True)

    measures_json = []
    for m in config.measures_for_release():
        entry = m.to_json()
        cells = []
        for code in m.numerator_cells + m.denominator_cells:
            cm = meta.cell(code)
            cells.append(cm.to_json())
        entry["cells"] = cells
        entry["universe_published"] = sorted({meta.table_universe[t] for t in m.tables})
        entry["availability"] = result.availability.get(m.measure_id, {})
        measures_json.append(entry)

    doc = {
        "schema_version": 1,
        "manifest_id": manifest_id,
        "manifest_ids": manifest_ids or [manifest_id],
        "data_mode": data_mode,
        "transport": transport,
        "built_at": provenance.utc_now(),
        "code_revision": provenance.code_revision(repo_root),
        "release": release.to_json(),
        "tables": {
            t: {
                "universe": meta.table_universe[t],
                "concept": meta.table_concept[t],
                "metadata_cache_path": meta.source_files[t],
            } for t in sorted(meta.tables)
        },
        "areas": [a.to_json() for a in sorted(result.areas.values(), key=lambda x: x.geoid)],
        "measures": measures_json,
        "value_files": {mid: f"values/{mid}.json" for mid in sorted(result.values)},
        "join_reports": result.join_reports,
        "diagnostics": result.diagnostics,
        "warnings": [redact(w) for w in result.warnings],
    }
    path = out_dir / "dataset.json"
    path.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")

    # Measure values are written one file per measure so the local service can
    # serve only what a view asks for instead of loading everything.
    values_dir = out_dir / "values"
    values_dir.mkdir(parents=True, exist_ok=True)
    for existing in values_dir.glob("*.json"):
        existing.unlink()
    for measure_id, per_geo in result.values.items():
        (values_dir / f"{measure_id}.json").write_text(
            json.dumps({"measure_id": measure_id,
                        "release_id": release.release_id,
                        "period_label": release.period_label,
                        "values": {g: compact_value(v) for g, v in per_geo.items()}},
                       separators=(",", ":")),
            encoding="utf-8",
        )

    # A small long-form file for inspection. Scoped exports are produced by the
    # export command against a saved project, not written wholesale at build time.
    csv_path = out_dir / "observations_county.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([
            "release_id", "period_label", "product", "geography_vintage", "level",
            "geoid", "area_name", "measure_id", "measure_label", "unit", "kind",
            "estimate", "estimate_status", "estimate_note",
            "moe_90pct", "moe_status", "moe_note", "cv_percent", "reliability",
            "universe", "numerator_cells", "denominator_cells",
        ])
        by_id = {m.measure_id: m for m in config.measures_for_release()}
        for measure_id in sorted(result.values):
            m = by_id[measure_id]
            for geoid in sorted(result.values[measure_id]):
                area = result.areas[geoid]
                if area.level != "county":
                    continue
                v = result.values[measure_id][geoid]
                w.writerow([
                    release.release_id, release.period_label, release.product_label,
                    release.geography_vintage, area.level, geoid, area.name,
                    measure_id, m.label, m.unit, m.kind,
                    "" if v["estimate"] is None else v["estimate"],
                    v["estimate_status"], v["estimate_reason"] or "",
                    "" if v["moe"] is None else v["moe"],
                    v["moe_status"], v["moe_reason"] or "",
                    "" if v["cv_percent"] is None else round(v["cv_percent"], 2),
                    v["reliability"], m.universe_note,
                    ";".join(m.numerator_cells), ";".join(m.denominator_cells),
                ])
    return path
