"""The local service.

Bound to the loopback interface, single user, no authentication because
nothing outside the machine can reach it. Four guarantees matter:

* The process switches network access off for itself at start-up, so a bug in
  a request handler cannot turn a page render into a download.
* No credential is read, held or served. The browser never sees a key because
  the service never has one.
* A saved project either reproduces exactly what it was saved from, or refuses
  to open. Nothing is substituted.
* Every output of one request — the table, the map, the chart, the CSV and the
  provenance document — is built from a single validated selection, so they
  cannot disagree about which areas and periods they describe.
"""

from __future__ import annotations

import json
import mimetypes
import posixpath
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import (benchmark as benchmark_mod, brief as brief_mod,
               compare as compare_mod, config as config_mod, exports, figures,
               geography as geography_mod, http_client, metadata as metadata_mod,
               projects as projects_mod, provenance, questions as questions_mod,
               selection as selection_mod, snapshot as snapshot_mod)
from .redact import redact, redact_structure

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 1 << 20
LEVELS = ("county", "tract")

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def safe_identifier(value: str, what: str) -> str:
    """Anything that becomes part of a file path is checked, not trusted."""
    if not isinstance(value, str) or not _SAFE_ID_RE.match(value):
        raise ValueError(f"invalid {what}: {value!r}")
    return value


# ---------------------------------------------------------------------------
# Request-level guards
# ---------------------------------------------------------------------------

def content_type_is_json(header: str | None) -> bool:
    """State-changing routes accept only ``application/json``.

    A cross-origin form or script cannot send this content type without a CORS
    preflight, which this service never answers, so requiring it closes the
    simple-POST route into localhost.
    """
    if not header:
        return False
    return header.split(";")[0].strip().lower() == "application/json"


def origin_is_local(origin: str | None, port: int) -> bool:
    """Accept only this service's own origin.

    A missing ``Origin`` is accepted: same-origin requests may omit it, and a
    cross-origin request that could omit it cannot set a JSON content type.
    """
    if origin is None or origin == "":
        return True
    try:
        parts = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme != "http" or parts.path or parts.query:
        return False
    host = (parts.hostname or "").strip("[]").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return False
    return (parts.port or 80) == port


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class ServiceState:
    """Loads processed datasets from disk. Never fetches."""

    def __init__(self, repo_root: Path, data_dir: str = "data/processed"):
        self.repo_root = Path(repo_root)
        self.data_dir = data_dir
        self.config = config_mod.load()
        self._lock = threading.RLock()   # re-entrant: values() loads the dataset it guards
        self._datasets: dict[str, dict] = {}
        self._values: dict[tuple[str, str], dict] = {}
        self._geography: dict[tuple[str, str], dict] = {}
        self._evidence: dict[tuple[str, str, str], geography_mod.GeographyEvidence] = {}

    # -- paths -----------------------------------------------------------
    def dataset_rel(self, release_id: str) -> str:
        return f"{self.data_dir}/{safe_identifier(release_id, 'release id')}/dataset.json"

    def values_rel(self, release_id: str, measure_id: str) -> str:
        return (f"{self.data_dir}/{safe_identifier(release_id, 'release id')}/values/"
                f"{safe_identifier(measure_id, 'measure id')}.json")

    def geography_rel(self, release_id: str, level: str) -> str:
        if level not in LEVELS:
            raise ValueError(f"unsupported geography level {level!r}")
        return (f"{self.data_dir}/{safe_identifier(release_id, 'release id')}/"
                f"geography_{level}.geojson")

    def dataset_path(self, release_id: str) -> Path:
        return self.repo_root / self.dataset_rel(release_id)

    # -- loading ---------------------------------------------------------
    def available_releases(self) -> list[str]:
        root = self.repo_root / self.data_dir
        if not root.exists():
            return []
        return sorted(p.name for p in root.iterdir() if (p / "dataset.json").exists())

    def dataset(self, release_id: str) -> dict:
        with self._lock:
            if release_id not in self._datasets:
                path = self.dataset_path(release_id)
                if not path.exists():
                    raise FileNotFoundError(
                        f"no built dataset for '{release_id}'. Build it first: "
                        f"python -m census_explorer.cli build --release {release_id}")
                with open(path, "r", encoding="utf-8") as fh:
                    self._datasets[release_id] = json.load(fh)
            return self._datasets[release_id]

    def values(self, release_id: str, measure_id: str) -> dict[str, dict]:
        key = (release_id, measure_id)
        with self._lock:
            if key not in self._values:
                ds = self.dataset(release_id)
                if measure_id not in (ds.get("value_files") or {}):
                    raise KeyError(f"measure '{measure_id}' is not in this dataset")
                path = self.repo_root / self.values_rel(release_id, measure_id)
                with open(path, "r", encoding="utf-8") as fh:
                    self._values[key] = json.load(fh)["values"]
            return self._values[key]

    def geography(self, release_id: str, level: str) -> dict:
        key = (release_id, level)
        with self._lock:
            if key not in self._geography:
                path = self.repo_root / self.geography_rel(release_id, level)
                if not path.exists():
                    raise FileNotFoundError(
                        f"no {level} boundaries built for '{release_id}'. "
                        "Retrieve them with: fetch geography")
                with open(path, "r", encoding="utf-8") as fh:
                    self._geography[key] = json.load(fh)
            return self._geography[key]

    def geography_features(self, release_id: str, level: str) -> dict[str, dict]:
        return {f["properties"]["GEOID"]: f
                for f in self.geography(release_id, level)["features"]}

    def release(self, release_id: str) -> config_mod.Release:
        """Resolve a release from the built dataset, falling back to config.

        Fixture datasets describe a release that is deliberately absent from
        the project configuration, so the dataset is the authority here.
        """
        try:
            return self.config.release(release_id)
        except config_mod.ConfigError:
            return release_from_json(self.dataset(release_id)["release"])

    def data_mode(self) -> str:
        modes = {self.dataset(r).get("data_mode", "live") for r in self.available_releases()}
        if not modes:
            return "none"
        return "fixture" if "fixture" in modes else "live"

    def areas(self, release_id: str) -> dict[str, dict]:
        return {a["geoid"]: a for a in self.dataset(release_id)["areas"]}

    def areas_at(self, release_id: str, level: str) -> set[str]:
        return {g for g, a in self.areas(release_id).items() if a["level"] == level}

    def measure_json(self, release_id: str, measure_id: str) -> dict:
        for m in self.dataset(release_id)["measures"]:
            if m["measure_id"] == measure_id:
                return m
        raise KeyError(f"unknown measure '{measure_id}'")

    def measure_def(self, release_id: str, measure_id: str) -> config_mod.MeasureDef:
        """The measure as the built dataset recorded it, not as config has it now."""
        return measure_from_json(self.measure_json(release_id, measure_id))

    # -- geographic equivalence -------------------------------------------
    def geography_evidence(self, a: str, b: str, level: str
                           ) -> geography_mod.GeographyEvidence:
        """Evidence that two releases' areas are the same ground.

        Same vintage needs none. Otherwise only a recorded provider
        correspondence or a scoped review counts; the service never computes
        its way to an answer here.
        """
        ra, rb = self.release(a), self.release(b)
        if ra.boundary_release == rb.boundary_release:
            return geography_mod.GeographyEvidence.same_vintage(level, ra.boundary_release)
        key = (a, b, level)
        with self._lock:
            if key not in self._evidence:
                recorded = geography_mod.recorded_equivalence(
                    level, ra.boundary_release, rb.boundary_release)
                self._evidence[key] = recorded or geography_mod.GeographyEvidence.none(
                    level, ra.boundary_release, rb.boundary_release,
                    "no documented provider correspondence and no scoped review "
                    "cover this pair of boundary vintages at this level. Comparing "
                    "the published polygons can inform such a review "
                    "(`cli geography footprint`) but cannot take its place.")
            return self._evidence[key]


def release_from_json(r: dict) -> config_mod.Release:
    return config_mod.Release(
        release_id=r["release_id"], provider=r["provider"], dataset=r["dataset"],
        vintage=r["vintage"], period_start=r["period_start"],
        period_end=r["period_end"], period_label=r["period_label"],
        product_label=r["product_label"], boundary_release=r["boundary_release"],
        geography_vintage=r["geography_vintage"], api_base="", summary_file_base="",
        citation=r["citation"])


def measure_from_json(m: dict) -> config_mod.MeasureDef:
    return config_mod.MeasureDef(
        measure_id=m["measure_id"], label=m["label"], concept=m["concept"],
        unit=m["unit"], kind=m["kind"], numerator_cells=list(m["numerator_cells"]),
        denominator_cells=list(m["denominator_cells"]),
        universe_note=m["universe_note"], definition_note=m["definition_note"],
        caveats=list(m.get("caveats") or []), topics=list(m.get("topics") or []))


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------

def compatibility(state: ServiceState, a: str | None, b: str | None, level: str,
                  measure_id: str | None) -> dict:
    if not a or not b:
        raise ValueError("a and b release identifiers are required")
    release_a, release_b = state.release(a), state.release(b)
    geo_a = state.areas_at(a, level)
    geo_b = state.areas_at(b, level)

    measure = None
    meta_a = meta_b = None
    if measure_id:
        try:
            measure = state.measure_def(a, measure_id)
        except KeyError:
            measure = state.config.measures.get(measure_id)
        if measure is not None and a in state.config.releases and b in state.config.releases:
            try:
                meta_a = metadata_mod.load_release_metadata(
                    state.repo_root, release_a, measure.tables)
                meta_b = metadata_mod.load_release_metadata(
                    state.repo_root, release_b, measure.tables)
            except metadata_mod.MetadataError:
                meta_a = meta_b = None

    report = compare_mod.check(
        release_a, release_b, level, geo_a, geo_b, meta_a, meta_b, measure,
        geography_evidence=state.geography_evidence(a, b, level))
    out = report.to_json()

    if report.allowed and measure is not None:
        pooled = []
        for rid in (a, b):
            for g in report.comparable_geoids:
                v = state.values(rid, measure.measure_id).get(g)
                if v and v.get("es") == "ok":
                    pooled.append(v["e"])
        out["shared_cut_points"] = compare_mod.shared_cut_points(pooled, 5)
        out["shared_cut_points_note"] = (
            "Both panels use these breaks. Independent legends on each side can "
            "manufacture the appearance of change.")
    return out


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def build_selection(state: ServiceState, payload: dict,
                    pinned: snapshot_mod.Snapshot | None = None
                    ) -> selection_mod.Selection:
    """Resolve and validate one request into a selection every output shares."""
    if pinned is not None:
        spec = pinned.definitions
        release_ids = list(spec["release_ids"])
        measure_ids = list(spec["measure_ids"])
        level = spec["level"]
        requested_areas = list(spec.get("areas") or [])
        releases = [release_from_json(r) for r in spec["releases"]]
        measures = [measure_from_json(m) for m in spec["measures"]]
        classes = int(spec.get("classes") or 5)
        cut_points = spec.get("cut_points")
    else:
        release_ids = [payload.get("release_id")] + (
            [payload["comparison_release_id"]] if payload.get("comparison_release_id") else [])
        release_ids = [r for r in release_ids if r]
        if not release_ids:
            raise ValueError("release_id is required")
        measure_ids = payload.get("measure_ids") or (
            [payload["measure_id"]] if payload.get("measure_id") else [])
        if not measure_ids:
            raise ValueError("measure_id or measure_ids is required")
        level = payload.get("level", "county")
        requested_areas = list(payload.get("areas") or [])
        releases = [state.release(r) for r in release_ids]
        measures = [state.measure_def(release_ids[0], m) for m in measure_ids]
        classes = int(payload.get("classes") or 5)
        cut_points = payload.get("cut_points")

    if level not in LEVELS:
        raise ValueError(f"unsupported geography level {level!r}")

    compat = None
    comparable = None
    if len(release_ids) > 1:
        # Every requested measure is validated, not only the first: a second
        # measure that cannot be compared must not ride along with one that can.
        for measure_id in measure_ids:
            report = compatibility(state, release_ids[0], release_ids[1], level, measure_id)
            if not report["allowed"]:
                raise ValueError(
                    f"this comparison is blocked for measure '{measure_id}': "
                    + "; ".join(report["blocking"]))
            if compat is None:
                compat = report
                comparable = report["comparable_geoids"]
        if cut_points is None and compat and compat.get("shared_cut_points"):
            cut_points = compat["shared_cut_points"]

    area_names = {}
    for rid in release_ids:
        for geoid, area in state.areas(rid).items():
            area_names.setdefault(geoid, area["name"])

    return selection_mod.resolve(
        releases=releases, measures=measures, level=level,
        areas_by_release={rid: state.areas_at(rid, level) for rid in release_ids},
        area_names=area_names, requested_areas=requested_areas,
        classes=classes, cut_points=cut_points,
        comparable_geoids=comparable, compatibility=compat)


def selection_inputs(state: ServiceState, sel: selection_mod.Selection) -> list[dict]:
    """Digest exactly the files this selection was computed from."""
    entries: list[snapshot_mod.InputDigest] = []
    seen: set[str] = set()

    def add(rel: str, role: str, note: str) -> None:
        if rel in seen:
            return
        seen.add(rel)
        entries.append(snapshot_mod.digest_file(state.repo_root, rel, role, note))

    for release in sel.releases:
        rid = release.release_id
        add(state.dataset_rel(rid), "dataset",
            "measure definitions, universes, areas and join reports")
        try:
            add(state.geography_rel(rid, sel.level), "geometry",
                f"{sel.level} boundaries at {release.boundary_release}")
        except FileNotFoundError:
            pass
        for measure in sel.measures:
            add(state.values_rel(rid, measure.measure_id), "values",
                f"{measure.measure_id} for {release.period_label}")
        for mid in state.dataset(rid).get("manifest_ids") or []:
            path = f"data/manifests/{mid}.json"
            if (state.repo_root / path).exists():
                add(path, "manifest", "retrieval provenance for this release")

    return [e.to_json() for e in entries]


def make_snapshot(state: ServiceState, sel: selection_mod.Selection
                  ) -> snapshot_mod.Snapshot:
    return snapshot_mod.Snapshot(
        created_at=provenance.utc_now(),
        code_revision=provenance.code_revision(state.repo_root),
        inputs=selection_inputs(state, sel),
        definitions={
            "release_ids": [r.release_id for r in sel.releases],
            "releases": [r.to_json() for r in sel.releases],
            "measure_ids": [m.measure_id for m in sel.measures],
            "measures": [m.to_json() for m in sel.measures],
            "level": sel.level,
            "areas": list(sel.requested_areas),
            "classes": sel.classes,
            "cut_points": sel.cut_points,
        },
    )


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def source_lines(state: ServiceState, sel: selection_mod.Selection) -> list[str]:
    release = sel.primary_release
    measure = sel.primary_measure
    universe = state.measure_json(release.release_id,
                                  measure.measure_id)["universe_published"][0]
    lines = [
        f"Source: {release.citation}",
        f"Tables: {', '.join(measure.tables)}. Universe: {universe}",
        f"Geography: {release.geography_vintage}. Measure: {measure.definition_note}",
        f"Areas shown: {len(sel.areas)} {sel.level}"
        + (f"; {len(sel.excluded_areas)} excluded ({sel.exclusion_reason})"
           if sel.excluded_areas else ""),
    ]
    return lines


def figure_cuts(state: ServiceState, sel: selection_mod.Selection) -> list[float]:
    if sel.cut_points:
        return list(sel.cut_points)
    values = state.values(sel.primary_release.release_id, sel.primary_measure.measure_id)
    usable = [values[g]["e"] for g in sel.areas
              if (values.get(g) or {}).get("es") == "ok"]
    return figures.quantile_cuts(usable, sel.classes)


def build_figure_for(state: ServiceState, sel: selection_mod.Selection,
                     kind: str) -> str:
    measure = sel.primary_measure
    release = sel.primary_release
    data_mode = state.dataset(release.release_id).get("data_mode", "live")
    cuts = figure_cuts(state, sel)
    lines = source_lines(state, sel)
    disclosures = list((sel.compatibility or {}).get("disclosures") or [])

    if kind == "map":
        if sel.is_comparison:
            panels = [
                {"label": r.period_label,
                 "features": state.geography(r.release_id, sel.level)["features"],
                 "values": state.values(r.release_id, measure.measure_id)}
                for r in sel.releases
            ]
            return figures.choropleth_comparison_svg(
                panels=panels, title=measure.label,
                subtitle=" vs ".join(r.period_label for r in sel.releases)
                         + f" · {sel.level} · unit: "
                           f"{'percent' if measure.unit == 'percent' else 'persons'}",
                unit=measure.unit, cuts=cuts, source_lines=lines, areas=sel.areas,
                disclosures=disclosures, data_mode=data_mode)
        return figures.choropleth_svg(
            features=state.geography(release.release_id, sel.level)["features"],
            values=state.values(release.release_id, measure.measure_id),
            title=measure.label,
            subtitle=f"{release.period_label} · {sel.level} · unit: "
                     f"{'percent' if measure.unit == 'percent' else 'persons'}",
            unit=measure.unit, cuts=cuts, source_lines=lines,
            data_mode=data_mode, panel_label=release.period_label,
            areas=sel.areas, disclosures=disclosures)

    if kind == "chart":
        primary_values = state.values(release.release_id, measure.measure_id)
        drawn, scope_note = sel.chart_rows(primary_values)
        rows = []
        for geoid in sorted(drawn, key=lambda g: sel.area_names.get(g, g)):
            row_values = []
            for r in sel.releases:
                v = state.values(r.release_id, measure.measure_id).get(geoid, {})
                row_values.append({
                    "e": v.get("e") if v.get("es") == "ok" else None,
                    "m": v.get("m") if v.get("ms") == "ok" else None,
                    "controlled": is_controlled_value(v) and not v.get("m"),
                    "note": plain_reason(v) if v.get("er") else "no usable estimate",
                })
            rows.append({"label": sel.area_names.get(geoid, geoid), "values": row_values})
        return figures.group_chart_svg(
            rows=rows, title=measure.label,
            subtitle=" vs ".join(r.period_label for r in sel.releases)
                     + f" · {sel.level} · bars show the estimate, whiskers the "
                       "90% margin of error",
            unit=measure.unit, source_lines=lines,
            series_labels=[r.period_label for r in sel.releases],
            data_mode=data_mode, disclosures=disclosures, scope_note=scope_note)

    raise ValueError(f"unknown figure kind {kind!r}")


# ---------------------------------------------------------------------------
# Guided brief
# ---------------------------------------------------------------------------

def is_controlled_value(v: dict) -> bool:
    """The stored value came from an estimate controlled to an independent total."""
    return any("controlled" in flag for flag in (v.get("flags") or []))


def plain_reason(v: dict, which: str = "e") -> str:
    """A reason a reader can act on, without the table cell code.

    The stored reason names the cell it came from, which belongs in the source
    details rather than in the body of a brief. The code is not lost: it stays
    in the exported CSV, in the provenance document and in the source panel.
    """
    raw = (v.get("er") if which == "e" else v.get("mr")) or ""
    if not raw:
        return "unavailable"
    body = raw.split(": ", 1)[1] if re.match(r"^[A-Z0-9]+_\d{3}: ", raw) else raw
    body = body.strip()
    lowered = body[:1].lower() + body[1:] if body else body
    if "insufficient number of sample cases" in lowered:
        return ("too few sample cases here for the Census Bureau to publish a "
                "value")
    if "insufficient number of sample observations" in lowered:
        return "too few sample observations here to compute a value"
    if "undefined, not zero" in lowered:
        return "the denominator is zero here, so a share is undefined, not zero"
    if "not applicable" in lowered:
        return "not applicable to this area"
    if "controlled" in lowered:
        return ("controlled to an independent population estimate, so it carries "
                "no sampling error")
    return lowered


def _reliability_words(v: dict, unit: str) -> str:
    """Say something the margin-of-error column does not already say.

    For a count that is the published coefficient of variation. For a share
    there is no CV, so the useful comparison is how large the margin of error
    is relative to the estimate itself — stated as that, not as a CV.
    """
    if v.get("es") != "ok":
        return plain_reason(v, "e")
    if is_controlled_value(v) and not v.get("m"):
        return ("controlled to an independent population estimate, so it carries "
                "no sampling error")
    if v.get("ms") != "ok":
        return f"margin of error unavailable: {plain_reason(v, 'm')}"
    if v.get("rel"):
        return f"{v['rel']} (CV {v['cv']:.0f}%)" if v.get("cv") is not None else v["rel"]
    if unit == "percent" and v.get("m") is not None and v.get("e"):
        ratio = abs(v["m"]) / abs(v["e"]) * 100.0
        if ratio < 10:
            band = "narrow"
        elif ratio < 30:
            band = "moderate"
        else:
            band = "wide — read as indicative"
        return f"{band}: the margin of error is {ratio:.0f}% of the estimate"
    return "estimate published"


def quality_report(state: ServiceState, sel: selection_mod.Selection,
                   values: dict[str, dict]) -> dict:
    """Three separate judgements, in words a reader can act on.

    Uncertainty, comparison eligibility and period are different concerns and
    are kept apart. There is no combined score: one number would hide which of
    the three is the problem, and none of them is really a number.
    """
    unit = sel.primary_measure.unit
    usable = [g for g in sel.areas if (values.get(g) or {}).get("es") == "ok"]
    missing = [g for g in sel.areas if g not in usable]
    with_moe = [g for g in usable if (values.get(g) or {}).get("ms") == "ok"]
    high = [g for g in with_moe
            if (values[g].get("cv") is not None and values[g]["cv"] >= 30)]

    lines = []
    if not sel.areas:
        state_word, cls = "No areas selected", "blocked"
    elif not usable:
        state_word, cls = "No usable estimates", "blocked"
        lines.append("Every selected area's estimate is unavailable. The reason for "
                     "each is in the table and the exported data.")
    else:
        share_high = len(high) / len(usable) if usable else 0
        if missing or share_high > 0.25:
            state_word, cls = "Read with care", "caution"
        else:
            state_word, cls = "Usable as published", "ok"
        lines.append(
            f"{len(usable)} of {len(sel.areas)} selected areas have a published "
            f"estimate" + (f"; {len(missing)} do not and are shown as 'no data'."
                           if missing else "."))
        if len(with_moe) < len(usable):
            lines.append(
                f"{len(usable) - len(with_moe)} have no usable margin of error. "
                "Missing uncertainty is unavailable, not zero.")
        if unit == "persons" and high:
            lines.append(
                f"{len(high)} area(s) have a high relative error (coefficient of "
                "variation of 30% or more). Treat those single values as "
                "indicative rather than precise.")
        elif unit == "percent":
            widest = max((values[g]["m"] for g in with_moe
                          if values[g].get("m") is not None), default=None)
            if widest is not None:
                lines.append(
                    f"The widest margin of error among the selected areas is "
                    f"±{widest:.1f} percentage points at 90% confidence.")
    uncertainty = {"state": state_word, "class": cls, "lines": lines}

    # Comparison eligibility.
    if sel.is_comparison and sel.compatibility:
        if sel.compatibility["allowed"]:
            comparison = {
                "state": "Periods may be compared", "class": "ok",
                "lines": list(sel.compatibility["disclosures"])[:3] or
                         ["The two releases passed every compatibility check."],
            }
        else:
            comparison = {
                "state": "Comparison blocked", "class": "blocked",
                "lines": list(sel.compatibility["blocking"])[:3],
            }
    else:
        release = sel.primary_release
        other = [r for r in state.available_releases() if r != release.release_id]
        lines = ["Places are compared with each other inside one reference period, "
                 "which needs no boundary equivalence."]
        if other:
            evidence = state.geography_evidence(
                release.release_id, other[0], sel.level)
            if not evidence.established:
                lines.append(
                    "Comparing this period against another is not available: these "
                    "boundaries have not been verified as equivalent between the "
                    "two releases. Next step: record documented provider "
                    "correspondence or a scoped review in "
                    "config/geography_equivalence.json, or run "
                    "`cli geography footprint` to measure how far they differ.")
        comparison = {"state": "Within one period", "class": "ok", "lines": lines}

    # Period and freshness.
    release = sel.primary_release
    freshness = {
        "state": release.period_label,
        "class": "ok",
        "lines": [
            f"A {release.period_years}-year period estimate covering "
            f"{release.period_start} to {release.period_end}. It describes the "
            "whole window, not any single year in it, and must not be relabelled "
            "with one year.",
            f"Geography vintage: {release.geography_vintage}.",
            "This build does not check whether the provider has published a newer "
            "release; it reports what was retrieved.",
        ],
    }
    return {"uncertainty": uncertainty, "comparison": comparison,
            "freshness": freshness}


def brief_context(state: ServiceState, sel: selection_mod.Selection,
                  question_id: str, benchmark_id: str = benchmark_mod.NONE,
                  analyst_note: str = "", figure_kind: str = "",
                  inputs: list[dict] | None = None) -> dict:
    """Everything the brief renders, derived from one selection."""
    question = questions_mod.QUESTIONS[question_id]
    measure = sel.primary_measure
    release = sel.primary_release
    values = state.values(release.release_id, measure.measure_id)
    measure_json = state.measure_json(release.release_id, measure.measure_id)
    dataset = state.dataset(release.release_id)

    option = next((o for o in questions_mod.QUESTION_MEASURES[question_id]
                   if o.measure_id == measure.measure_id), None)
    if option is None:
        raise ValueError(
            f"measure '{measure.measure_id}' is not one of the options for the "
            f"question '{question_id}'")

    if len(sel.areas) == 1:
        places = sel.area_names.get(sel.areas[0], sel.areas[0])
    elif sel.level == "tract":
        places = (f"{len(sel.areas):,} census tracts in New York City "
                  "(statistical areas, not neighbourhoods)")
    else:
        places = ", ".join(sel.area_names.get(g, g) for g in sel.areas)
    summary = questions_mod.describe(question_id, option, release.period_label, places)

    ranked = sorted(
        sel.areas,
        key=lambda g: ((values.get(g) or {}).get("e")
                       if (values.get(g) or {}).get("es") == "ok" else float("-inf")),
        reverse=True)
    shown = ranked if len(ranked) <= 25 else ranked[:25]
    rows = []
    for geoid in shown:
        v = values.get(geoid) or {}
        rows.append({
            "geoid": geoid,
            "name": sel.area_names.get(geoid, geoid),
            "estimate": v.get("e") if v.get("es") == "ok" else None,
            "moe": v.get("m") if v.get("ms") == "ok" else None,
            "controlled": is_controlled_value(v) and not v.get("m"),
            "quality": _reliability_words(v, measure.unit),
        })

    bench = None
    if benchmark_id and benchmark_id != benchmark_mod.NONE:
        # Kept even when unavailable: a reader who asked for a reference must be
        # told it could not be built, and why, rather than finding it missing.
        bench = build_benchmark(state, sel, benchmark_id).to_json()

    limitations = list(question.not_answered)
    limitations.extend(measure.caveats)
    if len(ranked) > len(shown):
        limitations.append(
            f"The table lists the {len(shown)} highest of {len(ranked)} selected "
            "areas. The exported data contains every one of them.")
    for report in dataset.get("join_reports", []):
        if report["level"] == sel.level and report["unmatched_observation_count"]:
            limitations.append(
                f"{report['unmatched_observation_count']} area(s) at this level have "
                "no published boundary at this geography vintage and cannot be drawn "
                f"on the map (total population "
                f"{report.get('unmatched_observation_population')}).")

    sources = [
        release.citation,
        f"Tables used: {', '.join(measure.tables)}. Published universe: "
        f"{measure_json['universe_published'][0]}.",
        f"Boundaries: {release.geography_vintage}, U.S. Census Bureau cartographic "
        "boundary files. Generalized for display; not legal boundary descriptions.",
        "Margins of error are the published 90% figures. Derived margins of error "
        "use the Census Bureau's documented approximation formulas.",
    ]

    return {
        "question": question.to_json(),
        "summary": summary,
        "rows": rows,
        "measure": {**measure.to_json(),
                    "universe_published": measure_json["universe_published"]},
        "release": release.to_json(),
        "quality": quality_report(state, sel, values),
        "benchmark": bench,
        "limitations": limitations,
        "sources": sources,
        "reproducibility": {
            "manifest_id": dataset.get("manifest_id"),
            "code_revision": dataset.get("code_revision"),
            "inputs": inputs if inputs is not None else selection_inputs(state, sel),
        },
        "analyst_note": analyst_note,
        "data_mode": dataset.get("data_mode", "live"),
        "figure_kind": figure_kind or ("map" if sel.level == "tract"
                                       or len(sel.areas) > 8 else "chart"),
    }


def build_benchmark(state: ServiceState, sel: selection_mod.Selection,
                    benchmark_id: str) -> benchmark_mod.Benchmark:
    """Resolve a benchmark inside the selection's own release."""
    measure = sel.primary_measure
    release = sel.primary_release
    values = state.values(release.release_id, measure.measure_id)
    county_values = values
    counties = state.config.county_geoids

    if benchmark_id == benchmark_mod.NYC:
        composite = state.config.composite("nyc")
        label = (composite or {}).get("label", "New York City (all five boroughs)")
        if not composite:
            return benchmark_mod.unavailable(
                benchmark_id, label,
                "no documented membership for this composite is recorded in "
                "config/project.json, so it cannot be built", measure.unit)

        members = [str(g) for g in composite["member_geoids"]]
        # The configured project must still describe the place this reference is
        # named after. A project narrowed to two boroughs may be perfectly
        # valid work, but its totals are not New York City's.
        if set(members) != set(counties):
            return benchmark_mod.unavailable(
                benchmark_id, label,
                "the configured county set does not match the documented "
                f"membership of {label}. Documented: {', '.join(sorted(members))}. "
                f"Configured: {', '.join(sorted(counties)) or 'none'}. A reference "
                "carrying this name is only built from the whole documented place.",
                measure.unit)

        built = state.areas_at(release.release_id, "county")
        present = [g for g in members if g in built]
        return benchmark_mod.aggregate(
            measure, county_values, present, benchmark_id, label,
            "the five boroughs' underlying counts added together, with the measure "
            "recomputed from the totals",
            required_members=members)

    if benchmark_id == benchmark_mod.CONTAINING_BOROUGH:
        boroughs = sorted({g[:5] for g in sel.areas})
        if sel.level != "tract" or len(boroughs) != 1:
            return benchmark_mod.unavailable(
                benchmark_id, "The containing borough",
                "this reference applies to tracts inside a single borough; the "
                "selection spans " + (f"{len(boroughs)} boroughs" if boroughs
                                      else "no borough"), measure.unit)
        borough = boroughs[0]
        if borough not in state.areas_at(release.release_id, "county"):
            return benchmark_mod.unavailable(
                benchmark_id, "The containing borough",
                "the borough's published value is not built for this release",
                measure.unit)
        name = state.areas(release.release_id)[borough]["name"]
        return benchmark_mod.aggregate(
            measure, county_values, [borough], benchmark_id, name,
            "the borough's own published figure, not an aggregate of its tracts")

    if benchmark_id == benchmark_mod.SELECTED:
        return benchmark_mod.aggregate(
            measure, values, list(sel.areas), benchmark_id,
            "The selected places combined",
            "the selected places' underlying counts added together, with the "
            "measure recomputed from the totals")

    return benchmark_mod.unavailable(benchmark_id, benchmark_id,
                                     f"unknown reference '{benchmark_id}'",
                                     measure.unit)


def render_brief(state: ServiceState, sel: selection_mod.Selection,
                 question_id: str, benchmark_id: str = benchmark_mod.NONE,
                 analyst_note: str = "", figure_kind: str = "",
                 inputs: list[dict] | None = None) -> str:
    context = brief_context(state, sel, question_id, benchmark_id, analyst_note,
                            figure_kind, inputs)
    svg = build_figure_for(state, sel, context["figure_kind"])
    return brief_mod.build(
        question=context["question"], summary=context["summary"],
        rows=context["rows"], measure=context["measure"],
        release=context["release"], figure_svg=svg, quality=context["quality"],
        benchmark=context["benchmark"], limitations=context["limitations"],
        sources=context["sources"], reproducibility=context["reproducibility"],
        analyst_note=context["analyst_note"], data_mode=context["data_mode"],
        generated_at=provenance.utc_now())


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def save_project(state: ServiceState, payload: dict) -> dict:
    project_id = projects_mod.validate_id(payload.get("project_id", ""))
    sel = build_selection(state, payload)
    snap = make_snapshot(state, sel)
    project = projects_mod.SavedProject(
        project_id=project_id,
        title=payload.get("title") or project_id,
        release_id=sel.primary_release.release_id,
        level=sel.level,
        measure_id=sel.primary_measure.measure_id,
        areas=list(sel.requested_areas),
        comparison_release_id=(sel.comparison_release.release_id
                               if sel.comparison_release else None),
        classes=sel.classes,
        cut_points=sel.cut_points,
        notes=payload.get("notes", ""),
        manifest_ids=[i["path"].rsplit("/", 1)[-1][:-5]
                      for i in snap.inputs if i["role"] == "manifest"],
        snapshot=snap.to_json(),
        brief={
            "question_id": payload.get("question_id") or "",
            "benchmark_id": payload.get("benchmark_id") or benchmark_mod.NONE,
            "analyst_note": payload.get("analyst_note", ""),
            "figure_kind": payload.get("figure_kind", ""),
        } if payload.get("question_id") else None,
    )
    path = projects_mod.save(state.repo_root, project)
    return {
        "saved": project.project_id,
        "path": str(path.relative_to(state.repo_root)).replace("\\", "/"),
        "pinned_inputs": len(snap.inputs),
    }


def replay_project(state: ServiceState, project_id: str) -> dict:
    """Reopen a saved project, or refuse because its inputs moved."""
    project = projects_mod.load(state.repo_root, project_id)
    snap = project.pin()
    snapshot_mod.require(state.repo_root, snap, project_id)
    sel = build_selection(state, {}, pinned=snap)
    values = {
        r.release_id: {m.measure_id: {g: state.values(r.release_id, m.measure_id)[g]
                                      for g in sel.areas
                                      if g in state.values(r.release_id, m.measure_id)}
                       for m in sel.measures}
        for r in sel.releases
    }
    return {
        "project": project.to_json(),
        "selection": {**sel.to_json(),
                      "measures": [m.to_json() for m in sel.measures],
                      "releases": [r.to_json() for r in sel.releases]},
        "values": values,
        "cut_points": figure_cuts(state, sel),
        "compatibility": sel.compatibility,
        "brief": project.brief,
        "pin": {
            "verified": True,
            "input_count": len(snap.inputs),
            "created_at": snap.created_at,
            "note": ("Every pinned input was present and unchanged, so this is the "
                     "same result the project was saved from."),
        },
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def run_export(state: ServiceState, payload: dict) -> dict:
    project = None
    if payload.get("project_id"):
        project = projects_mod.load(state.repo_root, payload["project_id"])
        snap = project.pin()
        snapshot_mod.require(state.repo_root, snap, project.project_id)
        sel = build_selection(state, {}, pinned=snap)
    else:
        sel = build_selection(state, payload)

    areas = {}
    for rid in [r.release_id for r in sel.releases]:
        for geoid, area in state.areas(rid).items():
            areas.setdefault(geoid, area)

    values: dict[tuple[str, str], dict[str, dict]] = {}
    for release in sel.releases:
        for measure in sel.measures:
            values[(release.release_id, measure.measure_id)] = state.values(
                release.release_id, measure.measure_id)

    datasets = [state.dataset(r.release_id) for r in sel.releases]
    data_mode = "fixture" if any(d.get("data_mode") == "fixture"
                                 for d in datasets) else "live"
    csv_text = exports.build_csv_for(sel, areas, values)
    inputs = selection_inputs(state, sel)
    prov = exports.build_provenance(
        state.repo_root, state.config, sel.releases, sel.measures, project,
        datasets, sel.compatibility, data_mode, sel=sel, inputs=inputs)

    svg = None
    figure_kind = payload.get("figure_kind", "map")
    if payload.get("include_figure", True):
        svg = build_figure_for(state, sel, figure_kind)

    brief_spec = dict((project.brief if project and project.brief else {}) or {})
    for key in ("question_id", "benchmark_id", "analyst_note"):
        if payload.get(key):
            brief_spec[key] = payload[key]
    brief_html = None
    if brief_spec.get("question_id"):
        brief_html = render_brief(
            state, sel, brief_spec["question_id"],
            brief_spec.get("benchmark_id") or benchmark_mod.NONE,
            brief_spec.get("analyst_note", ""),
            brief_spec.get("figure_kind", ""), inputs=inputs)

    stamp = provenance.utc_now().replace(":", "").replace("-", "")
    name = payload.get("name") or (
        f"{sel.primary_measure.measure_id}-{sel.level}-"
        f"{sel.primary_release.release_id}")
    artifacts_root = state.repo_root / "artifacts"
    out_dir = exports.export_dir(artifacts_root, name, stamp)
    written = exports.write_bundle(out_dir, csv_text, prov, svg,
                                   artifacts_root=artifacts_root,
                                   brief_html=brief_html)
    return {
        "export_dir": str(out_dir.relative_to(state.repo_root)).replace("\\", "/"),
        "files": [str(p.relative_to(state.repo_root)).replace("\\", "/") for p in written],
        "rows": csv_text.count("\n") - 1,
        "data_mode": data_mode,
        "selection": sel.to_json(),
        "figure_kind": figure_kind if svg else None,
        "brief": bool(brief_html),
        "comparison": sel.compatibility,
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "CensusExplorer/0.1"
    state: ServiceState

    def log_message(self, fmt: str, *args) -> None:   # redact anything logged
        print(redact("  %s - %s" % (self.address_string(), fmt % args)))

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(redact_structure(obj), separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": redact(message)}, status)

    def _guard_host(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]")
        if host in ("127.0.0.1", "localhost", "::1", ""):
            return True
        self._error(403, "this service only answers requests addressed to localhost")
        return False

    def _guard_write(self) -> bool:
        """State-changing requests must be same-origin JSON.

        A loopback Host header alone does not stop a page on another origin
        from posting to localhost, so the content type and the Origin are both
        checked before anything is written.
        """
        if not content_type_is_json(self.headers.get("Content-Type")):
            self._error(415, "state-changing requests must use "
                             "Content-Type: application/json")
            return False
        port = self.server.server_address[1]
        if not origin_is_local(self.headers.get("Origin"), port):
            self._error(403, "cross-origin requests are refused by this local service")
            return False
        return True

    def do_GET(self) -> None:
        if not self._guard_host():
            return
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path.startswith("/api/"):
                return self._api_get(parsed.path, query)
            return self._static(parsed.path)
        except snapshot_mod.SnapshotMismatch as exc:
            self._json({"error": redact(str(exc)), "kind": "pin_mismatch"}, 409)
        except FileNotFoundError as exc:
            self._error(404, str(exc))
        except KeyError as exc:
            self._error(404, str(exc))
        except (ValueError, projects_mod.ProjectError,
                selection_mod.SelectionError) as exc:
            self._error(400, str(exc))
        except Exception as exc:                        # pragma: no cover
            self._error(500, f"{type(exc).__name__}: {exc}")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        if not self._guard_host() or not self._guard_write():
            return
        parsed = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._error(413, "request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._error(400, "request body is not JSON")
        if not isinstance(payload, dict):
            return self._error(400, "request body must be a JSON object")
        try:
            if parsed.path == "/api/projects":
                return self._json(save_project(self.state, payload))
            if parsed.path == "/api/projects/delete":
                ok = projects_mod.delete(self.state.repo_root,
                                         payload.get("project_id", ""))
                return self._json({"deleted": ok})
            if parsed.path == "/api/export":
                return self._json(run_export(self.state, payload))
            self._error(404, f"no such endpoint: {parsed.path}")
        except snapshot_mod.SnapshotMismatch as exc:
            self._json({"error": redact(str(exc)), "kind": "pin_mismatch"}, 409)
        except exports.UnsafeExportName as exc:
            self._error(400, str(exc))
        except (ValueError, KeyError, FileNotFoundError, projects_mod.ProjectError,
                selection_mod.SelectionError) as exc:
            self._error(400, str(exc))
        except Exception as exc:                        # pragma: no cover
            self._error(500, f"{type(exc).__name__}: {exc}")

    # -- static ----------------------------------------------------------
    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else posixpath.normpath(path.lstrip("/"))
        if rel.startswith("..") or Path(rel).is_absolute() or "\\" in rel:
            return self._error(403, "forbidden path")
        root = WEB_DIR.resolve()
        target = (root / rel).resolve()
        # is_relative_to, not a string prefix: a sibling directory whose name
        # merely starts with the same characters is not inside the root.
        if not target.is_relative_to(root) or not target.is_file():
            return self._error(404, f"not found: {path}")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # -- API -------------------------------------------------------------
    def _api_get(self, path: str, query: dict[str, list[str]]) -> None:
        st = self.state
        one = lambda k, default=None: (query.get(k) or [default])[0]

        if path == "/api/status":
            releases = st.available_releases()
            return self._json({
                "data_mode": st.data_mode(),
                "releases": [st.dataset(r)["release"] for r in releases],
                "default_release": st.config.raw["explorer"]["default_release"],
                "comparison_release": st.config.raw["explorer"]["comparison_release"],
                "code_revision": provenance.code_revision(st.repo_root),
                "offline": http_client.is_offline(),
                "annotation_reference": _annotation_provenance(),
            })

        if path == "/api/dataset":
            release = one("release") or st.config.raw["explorer"]["default_release"]
            ds = dict(st.dataset(release))
            ds.pop("value_files", None)
            return self._json(ds)

        if path == "/api/values":
            release, measure = one("release"), one("measure")
            if not release or not measure:
                raise ValueError("release and measure are required")
            return self._json({
                "release_id": release, "measure_id": measure,
                "period_label": st.dataset(release)["release"]["period_label"],
                "values": st.values(release, measure),
            })

        if path == "/api/geography":
            release = one("release")
            if not release:
                raise ValueError("release is required")
            return self._json(st.geography(release, one("level", "county")))

        if path == "/api/compare":
            return self._json(compatibility(st, one("a"), one("b"),
                                            one("level", "county"), one("measure")))

        if path == "/api/questions":
            release = one("release") or st.config.raw["explorer"]["default_release"]
            ds = st.dataset(release)
            areas = [a for a in ds["areas"]]
            return self._json({
                "release_id": release,
                "period_label": ds["release"]["period_label"],
                "questions": questions_mod.to_json(ds),
                "places": {
                    "county": [a for a in areas if a["level"] == "county"],
                    "tract_count": sum(1 for a in areas if a["level"] == "tract"),
                },
            })

        if path == "/api/benchmarks":
            release = one("release") or st.config.raw["explorer"]["default_release"]
            level = one("level", "county")
            selected = [a for a in (one("areas", "") or "").split(",") if a]
            names = {g: a["name"] for g, a in st.areas(release).items()}
            return self._json({"benchmarks": benchmark_mod.options(
                level, selected, st.config.county_geoids, names)})

        if path == "/api/benchmark":
            sel = build_selection(st, _selection_payload(query))
            bench = build_benchmark(st, sel, one("benchmark", benchmark_mod.NONE))
            return self._json(bench.to_json())

        if path == "/api/quality":
            sel = build_selection(st, _selection_payload(query))
            values = st.values(sel.primary_release.release_id,
                               sel.primary_measure.measure_id)
            return self._json({"quality": quality_report(st, sel, values),
                               "selection": sel.to_json()})

        if path == "/api/brief":
            sel = build_selection(st, _selection_payload(query))
            html_text = render_brief(
                st, sel, one("question", questions_mod.WHO_LIVES_HERE),
                one("benchmark", benchmark_mod.NONE),
                one("note", "") or "", one("figure", "") or "")
            return self._send(200, html_text.encode("utf-8"),
                              "text/html; charset=utf-8")

        if path == "/api/projects":
            return self._json({"projects": projects_mod.listing(st.repo_root)})

        if path == "/api/project":
            pid = one("id")
            if not pid:
                raise ValueError("id is required")
            return self._json(replay_project(st, pid))

        if path == "/api/figure":
            release = one("release") or st.config.raw["explorer"]["default_release"]
            measure = one("measure")
            if not measure:
                raise ValueError("measure is required")
            payload = {
                "release_id": release, "measure_id": measure,
                "level": one("level", "county"),
                "comparison_release_id": one("compare") or None,
            }
            areas = one("areas")
            if areas:
                payload["areas"] = [a for a in areas.split(",") if a]
            sel = build_selection(st, payload)
            svg = build_figure_for(st, sel, one("kind", "map"))
            return self._send(200, svg.encode("utf-8"), "image/svg+xml; charset=utf-8")

        raise KeyError(f"no such endpoint: {path}")


def _selection_payload(query: dict[str, list[str]]) -> dict:
    """Build a selection payload from query parameters, validating as usual."""
    one = lambda k, default=None: (query.get(k) or [default])[0]
    payload = {
        "release_id": one("release"),
        "measure_id": one("measure"),
        "level": one("level", "county"),
        "comparison_release_id": one("compare") or None,
    }
    areas = one("areas")
    if areas:
        payload["areas"] = [a for a in areas.split(",") if a]
    return payload


def _annotation_provenance() -> dict:
    from .sentinels import reference_provenance
    return reference_provenance()


def serve(repo_root: Path, host: str = "127.0.0.1", port: int = 8765,
          data_dir: str = "data/processed", log=print) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(
            f"refusing to bind to {host!r}: this service is local-only by design")
    http_client.set_offline(True)
    state = ServiceState(repo_root, data_dir)
    releases = state.available_releases()
    if not releases:
        raise FileNotFoundError(
            "no built dataset found. Run: python -m census_explorer.cli build")
    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer((host, port), handler)
    log(f"Census Explorer service on http://{host}:{port}/")
    log(f"  data mode: {state.data_mode()}   releases: {', '.join(releases)}")
    log("  network access is disabled in this process; press Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("stopped")
    finally:
        httpd.server_close()
