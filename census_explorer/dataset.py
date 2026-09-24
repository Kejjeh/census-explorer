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

SUMMARY_LEVEL_NAME = {"04000": "state", "05000": "county", "14000": "tract"}

#: Why an area the release lists has no value in these tables. Said plainly,
#: because "source cells not retrieved" would read as a failed download.
ROSTER_ONLY_REASON = (
    "the release's own geography file lists this area, but none of the "
    "detailed tables used here publishes a row for it in this release")


def summary_file_path(repo_root: Path, release: Release, table: str,
                      config: ProjectConfig) -> Path:
    """The cached rows for one table, for this project's coverage.

    A statewide pull is cached under its own name so it never overwrites a
    narrower earlier one, whose manifest would then stop verifying.
    """
    suffix = config.cache_suffix
    stem = f"{table}{('_' + suffix) if suffix else ''}"
    return repo_root / f"data/raw/acs/{release.release_id}/summary_file/{stem}.psv"


def roster_path(repo_root: Path, release: Release, config: ProjectConfig) -> Path:
    from .retrieve.acs_summary_file import roster_cache_rel_path
    return repo_root / roster_cache_rel_path(
        release, str(config.study_area["state_fips"]))


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
    #: For statewide coverage: the release roster compared with the tables.
    coverage: dict[str, Any] | None = None


def county_display_name(config: ProjectConfig, geoid: str,
                        county_names: dict[str, str]) -> str:
    """A county's name as the interface shows it.

    The five New York City counties keep their borough names, because that is
    how a reader looks for them. Every other county is called by its official
    name from the boundary file, "Erie County", never by a city inside it.
    """
    alias = config.borough_alias(geoid)
    if alias:
        return alias
    return county_names.get(geoid) or config.county_name(geoid)


def _area_name(config: ProjectConfig, geoid: str, level: str,
               geo_names: dict[str, str],
               county_names: dict[str, str] | None = None) -> str:
    county_names = county_names or {}
    if level == "state":
        return geo_names.get(geoid) or geoid
    if level == "county":
        return county_display_name(config, geoid, {**county_names, **geo_names})
    name = geo_names.get(geoid)
    if name:
        return f"{name}, {county_display_name(config, geoid[:5], county_names)}"
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
            p = summary_file_path(repo_root, release, table, config)
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

    # Classify, restrict to the requested levels and the project's coverage.
    # A statewide project also keeps the state's own row: it is what county
    # totals are reconciled against and what a state reference reads.
    if config.statewide and "state" not in levels:
        levels = ["state", *levels]
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
        if not config.in_study_area(geoid, level):
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

    # Areas the release lists but these tables have no row for. They are
    # carried with an explicit reason rather than dropped: a tract missing
    # from the data would otherwise be a silent hole in the map and a silent
    # gap in every count of coverage.
    roster_only: set[str] = set()
    coverage: dict[str, Any] | None = None
    if config.statewide:
        coverage, roster_only = _check_roster(repo_root, release, config,
                                              areas, levels)
        for geoid in sorted(roster_only):
            level = geography.level_for_geoid(geoid)
            areas[geoid] = Area(geoid=geoid, name=geoid, level=level)
            classified[geoid] = {}
            classified_moe[geoid] = {}
        tract_names = coverage.pop("_tract_names", {})
        for geoid, tract_part in tract_names.items():
            if geoid in areas:
                # The county part is filled in once county names are known.
                areas[geoid].name = tract_part

    # Compute measures.
    values: dict[str, dict[str, dict]] = {}
    availability: dict[str, dict[str, bool]] = {}
    warnings: list[str] = []
    for m in config.measures_for_release():
        per_geo: dict[str, dict] = {}
        level_ok = {lvl: False for lvl in levels}
        for geoid, area in areas.items():
            if geoid in roster_only:
                mv = measures_mod.MeasureValue(
                    measure_id=m.measure_id, geoid=geoid, unit=m.unit,
                    kind=m.kind, estimate_status=measures_mod.UNAVAILABLE,
                    estimate_reason=ROSTER_ONLY_REASON,
                    moe_status=measures_mod.UNAVAILABLE,
                    moe_reason=ROSTER_ONLY_REASON)
            else:
                mv = measures_mod.compute(m, geoid, classified[geoid],
                                          classified_moe[geoid])
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

    result = BuildResult(
        release=release, areas=areas, values=values,
        availability=availability, warnings=warnings,
    )
    if coverage is not None:
        result.coverage = coverage
    return result


def _check_roster(repo_root: Path, release: Release, config: ProjectConfig,
                  areas: dict[str, "Area"], levels: list[str]
                  ) -> tuple[dict[str, Any], set[str]]:
    """Compare the table rows against the release's own list of geographies.

    A table download that succeeds says only that some rows arrived. The
    release's geography file says which geographies it publishes at all, so
    this is where "every county and every tract" is proved rather than
    assumed. A geography in the tables that the roster does not list is an
    error; one the roster lists that the tables lack is carried and reported.
    """
    from .retrieve.acs_summary_file import read_roster

    path = roster_path(repo_root, release, config)
    if not path.exists():
        raise DatasetError(
            f"statewide coverage needs the release's geography roster "
            f"({path.relative_to(repo_root).as_posix()}), which is not cached. "
            "Run: python -m census_explorer.cli fetch roster")
    roster = read_roster(path)
    report: dict[str, Any] = {
        "source": ("the release's own geography file (Geos"
                   f"{release.vintage}{release.period_years}YR.txt), cached at "
                   f"{path.relative_to(repo_root).as_posix()}"),
        "state_fips": str(config.study_area["state_fips"]),
        "levels": {},
    }
    roster_only: set[str] = set()
    for level in levels:
        listed = {g[9:] for g, v in roster.items() if v["level"] == level}
        in_tables = {g for g, a in areas.items() if a.level == level}
        extra = sorted(in_tables - listed)
        if extra:
            raise DatasetError(
                f"{len(extra)} {level} row(s) in the tables are not in the "
                f"release's geography file ({', '.join(extra[:5])}); the "
                "cached tables and roster disagree and must be reviewed")
        missing = sorted(listed - in_tables)
        roster_only.update(missing)
        report["levels"][level] = {
            "listed_by_release": len(listed),
            "with_table_rows": len(in_tables),
            "listed_without_table_rows": missing,
        }
    # Name every area the roster knows, so one with no boundary polygon (a
    # water-only tract, typically) is still called by its published name
    # rather than a bare GEOID. Areas with a polygon are renamed from the
    # boundary file later; the two agree on the tract part.
    for geo_id, entry in roster.items():
        geoid = geo_id[9:]
        if entry["level"] != "tract" or geoid not in areas and geoid not in roster_only:
            continue
        tract_part = entry["name"].split(",")[0].split(";")[0].strip()
        if tract_part:
            report.setdefault("_tract_names", {})[geoid] = tract_part
    fips = str(config.study_area["state_fips"])
    state_geo = roster.get(f"0400000US{fips}")
    if fips in areas and state_geo:
        # Named "New York State", not "New York", so the state is never read
        # as the city of the same name.
        areas[fips].name = f"{state_geo['name']} State"
    return report, roster_only


def diagnostics_between_tables(result: BuildResult,
                               classified: dict[str, dict[str, Cell]] | None = None
                               ) -> list[dict]:
    """Cross-table checks that must be reported, not silently reconciled."""
    return result.diagnostics


def add_cross_table_diagnostics(repo_root: Path, release: Release,
                                result: BuildResult, transport: str,
                                config: ProjectConfig | None = None) -> None:
    """Compare nominally similar totals published in different tables.

    B05002 counts the foreign-born population; B05006 counts the foreign-born
    population *excluding people born at sea*.  Their totals are expected to be
    close but not identical.  The same applies to B01003 and B05002 totals.
    """
    raw: dict[str, dict[str, tuple[str | None, str | None]]] = {}
    for table in ("B01003", "B05002", "B05006"):
        if transport == "summary-file":
            p = (summary_file_path(repo_root, release, table, config) if config
                 else repo_root / f"data/raw/acs/{release.release_id}/summary_file/{table}.psv")
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
    # Counties first, so a tract can be named with its county.
    county_names: dict[str, str] = {}
    levels = sorted(levels, key=lambda lv: {"county": 0, "tract": 1}.get(lv, 2))

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
        if level == "county":
            county_names.update(names)
        for geoid in observed:
            area = result.areas[geoid]
            fallback = {} if area.name == geoid else {geoid: area.name}
            area.name = _area_name(config, geoid, level, {**fallback, **names},
                                   county_names)
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
        "coverage": result.coverage,
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
