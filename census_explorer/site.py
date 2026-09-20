"""Build a static site from an already-built release.

GitHub Pages has no Python runtime, so nothing can be computed while a reader
is looking at the page. This module runs the existing statistical code once,
here, and writes what it produced as plain files the browser fetches over
relative URLs.

Two rules govern what it emits.

*Nothing is recomputed in the browser.* Every estimate, margin of error,
coefficient of variation, reliability judgement, denominator and reference
value in the output was computed by `measures.py`, `benchmark.py` and
`server.py` during this build. The page reshapes and counts; it never does
survey arithmetic.

*Nothing private is emitted.* Only the official published aggregates, the
Census Bureau's own boundary files, and the wording this project writes about
them. No raw cache, no saved project, no credential, no local path, no
absolute filename.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import benchmark as benchmark_mod
from . import provenance, questions, server

#: Per-area keys, grouped by how they are encoded. A key that appears in a
#: value record and is not listed here fails the build rather than being
#: dropped: a silently missing field would read as "no data" in the browser.
NUMERIC_FIELDS = ("e", "m", "n", "d", "cv")
TEXT_FIELDS = ("es", "ms", "er", "mr", "rel")
FLAG_FIELDS = ("ctl",)
LIST_FIELDS = ("flags",)

#: The sentences whose text depends on how many areas are selected. The page
#: fills in the counts; the wording is written once, here, and a round-trip
#: test compares the page's output against `server.quality_report`.
UNCERTAINTY_TEMPLATES = {
    "published": "{usable} of {total} selected areas have a published estimate",
    "published_all": ".",
    "published_missing": "; {missing} do not and are shown as 'no data'.",
    "no_moe": ("{n} have no usable margin of error. Missing uncertainty is "
               "unavailable, not zero."),
    "high_cv": ("{n} area(s) have a high relative error (coefficient of "
                "variation of 30% or more). Treat those single values as "
                "indicative rather than precise."),
    "widest": ("The widest margin of error among the selected areas is "
               "±{widest} percentage points at 90% confidence."),
    "none_usable": ("Every selected area's estimate is unavailable. The reason "
                    "for each is in the table and the exported data."),
}

#: What the static build cannot do, and why. Shown in the interface rather
#: than left for a reader to discover by clicking something that does nothing.
UNSUPPORTED = {
    "brief": ("The printable brief is rendered by the local Python service, "
              "which this published site does not run. Download the data here, "
              "or run the app locally to produce a brief."),
    "export": ("Exporting a bundle writes files next to the app, which a "
               "published site cannot do. The data for exactly this selection "
               "downloads as CSV instead."),
    "projects": ("Saved views are stored and re-verified by the local Python "
                 "service. This published site cannot save one, and cannot "
                 "check that a saved view's inputs are unchanged, so it does "
                 "not offer to."),
    "selected_benchmark": ("A reference built from the places you selected has "
                           "to be recomputed from the underlying counts for "
                           "each combination, which needs the local service. "
                           "New York City is available here because it is one "
                           "fixed set of five boroughs, computed during the "
                           "build."),
}


@dataclass
class BuildReport:
    out_dir: Path
    files: list[Path]
    bytes_written: int
    release_id: str
    measures: int
    areas: int

    def summary(self) -> str:
        return (f"{len(self.files)} files, {self.bytes_written / 1e6:.1f} MB, "
                f"{self.measures} measures over {self.areas} areas, "
                f"release {self.release_id}")


def strip_local_paths(value: Any) -> Any:
    """Remove every reference to a file on the build machine.

    The built dataset records where each input was cached so a local run can
    trace a number back to the file it came from. None of that is published:
    the raw cache is not part of the site, and a path into it tells a reader
    about the machine that ran the build rather than about the data.
    """
    if isinstance(value, dict):
        return {k: strip_local_paths(v) for k, v in value.items()
                if not k.endswith(("_path", "_paths", "_dir",
                                   "_file", "_files"))}
    if isinstance(value, list):
        return [strip_local_paths(v) for v in value]
    return value


def _write(path: Path, payload: Any, files: list[Path]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (str, bytes)):
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
    else:
        data = json.dumps(payload, separators=(",", ":"),
                          sort_keys=False).encode("utf-8")
    path.write_bytes(data)
    files.append(path)
    return len(data)


def _encode_values(values: dict[str, dict], geoids: list[str]) -> dict:
    """Columnar encoding of one measure's values.

    The shape is chosen for size, not for cleverness: repeated status words
    become indices into one table, and the area order is shared with every
    other measure so it is stored once for the whole site.
    """
    notes: list[str] = []
    note_index: dict[str, int] = {}

    def note(value: str | None) -> int | None:
        if value is None:
            return None
        if value not in note_index:
            note_index[value] = len(notes)
            notes.append(value)
        return note_index[value]

    numeric: dict[str, list] = {k: [] for k in NUMERIC_FIELDS}
    text: dict[str, list] = {k: [] for k in TEXT_FIELDS}
    controlled: list[int] = []
    flags: list[list[str]] = []

    for geoid in geoids:
        record = values.get(geoid) or {}
        unknown = set(record) - set(NUMERIC_FIELDS) - set(TEXT_FIELDS) \
            - set(FLAG_FIELDS) - set(LIST_FIELDS)
        if unknown:
            raise ValueError(
                f"{geoid} carries value fields this static build does not "
                f"encode: {sorted(unknown)}. Add them to site.py rather than "
                "publishing a page that silently drops them.")
        for key in NUMERIC_FIELDS:
            numeric[key].append(record.get(key))
        for key in TEXT_FIELDS:
            text[key].append(note(record.get(key)))
        controlled.append(1 if record.get("ctl") is True else 0)
        flags.append(list(record.get("flags") or []))

    # Columns that are empty for this measure are dropped; the decoder treats
    # an absent column as "this field is absent from every record", which is
    # what it was.
    # A key whose value is null and a key that is absent mean the same thing —
    # no value — and the authoritative signal is the status field beside it.
    # The encoding keeps only the second form, so the decoded record can differ
    # from the service's by the presence of null-valued keys and by nothing
    # else; the round-trip test asserts exactly that.
    numeric = {k: v for k, v in numeric.items() if any(x is not None for x in v)}
    text = {k: v for k, v in text.items() if any(x is not None for x in v)}
    out: dict[str, Any] = {"notes": notes, "numeric": numeric, "text": text}
    if any(controlled):
        out["ctl"] = controlled
    if any(flags):
        out["flags"] = flags
    return out


def _quality_cases(state: server.ServiceState, release_id: str, level: str,
                   areas: list[str], measure_id: str) -> dict:
    """The parts of the quality report that do not depend on the selection.

    `quality_report` is called for each shape of selection the interface can
    produce, and the sentences it returns are stored verbatim. The page picks
    the matching case and fills in the counts; it never writes these sentences
    itself.
    """
    # The comparison and period panels do not depend on the measure, so any
    # measure the build carries at this level produces the same sentences.
    def report(selected: list[str], benchmark: dict | None) -> dict:
        sel = server.build_selection(state, {
            "release_id": release_id, "measure_id": measure_id,
            "level": level, "areas": selected})
        values = state.values(release_id, measure_id)
        return server.quality_report(state, sel, values, benchmark=benchmark)

    one = areas[:1]
    many = areas[:2] if len(areas) > 1 else areas[:1]
    nyc = benchmark_mod.options(level, [], state.config.county_geoids, {})
    nyc_label = next((o["label"] for o in nyc if o["benchmark_id"] == benchmark_mod.NYC),
                     "New York City (all five boroughs)")

    return {
        "freshness": report(areas, None)["freshness"],
        "comparison": {
            "many": report(many, None)["comparison"],
            "one_no_reference": report(one, None)["comparison"],
            "one_with_reference": report(
                one, {"available": True, "label": nyc_label})["comparison"],
            "one_reference_unavailable": report(
                one, {"available": False, "label": nyc_label})["comparison"],
        },
        "uncertainty_templates": UNCERTAINTY_TEMPLATES,
    }


def build(repo_root: Path, out_dir: Path, release_id: str | None = None,
          data_dir: str = "data/processed", base_path: str = "",
          log=print) -> BuildReport:
    """Write the whole static site into `out_dir`."""
    repo_root = Path(repo_root).resolve()
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    files: list[Path] = []
    written = 0

    state = server.ServiceState(repo_root, data_dir)
    release_id = release_id or state.config.raw["explorer"]["default_release"]
    dataset = state.dataset(release_id)
    release = dataset["release"]
    data_mode = dataset.get("data_mode", "live")
    log(f"release {release_id} ({release['period_label']}), data mode {data_mode}")

    areas = dataset["areas"]
    geoids = [a["geoid"] for a in areas]
    levels = sorted({a["level"] for a in areas})

    data = out_dir / "data"

    # -- the app itself, copied verbatim -----------------------------------
    # The interface ships with the package, like the service's own static
    # handler reads it: a build run against a data directory somewhere else
    # must still publish this app, not look for one beside the data.
    web = server.WEB_DIR
    for name in ("app.css", "app.js", "core.js", "static.js"):
        source = web / name
        written += _write(out_dir / name, source.read_bytes(), files)
    index = (web / "index.html").read_text(encoding="utf-8")
    injected = (
        '<script>window.CENSUS_EXPLORER_STATIC = '
        + json.dumps({"base": "data/", "base_path": base_path,
                      "unsupported": UNSUPPORTED},
                     separators=(",", ":"))
        + ';</script>\n<script src="static.js"></script>\n'
    )
    if '<script src="core.js"></script>' not in index:
        raise ValueError("web/index.html no longer loads core.js; "
                         "the static build does not know where to inject")
    index = index.replace('<script src="core.js"></script>',
                          '<script src="core.js"></script>\n' + injected.rstrip())
    written += _write(out_dir / "index.html", index, files)
    # GitHub Pages runs Jekyll unless told not to, and Jekyll drops files and
    # directories whose names begin with an underscore.
    written += _write(out_dir / ".nojekyll", "", files)

    # -- shared area order --------------------------------------------------
    written += _write(data / "areas.json",
                      {"release_id": release_id, "geoids": geoids}, files)

    # -- the three payloads the page starts from ---------------------------
    published = {r: state.dataset(r)["release"] for r in state.available_releases()}
    written += _write(data / "status.json", {
        "data_mode": data_mode,
        "releases": [published[release_id]],
        "default_release": release_id,
        "comparison_release": None,
        "code_revision": dataset.get("code_revision"),
        "offline": True,
        "annotation_reference": server._annotation_provenance(),
        "static": True,
    }, files)

    dataset_payload = strip_local_paths(dataset)
    written += _write(data / "dataset.json", dataset_payload, files)
    written += _write(data / "catalog.json",
                      server.measure_catalog(state, release_id), files)

    # -- geometry, one file per level --------------------------------------
    for level in levels:
        try:
            geo = state.geography(release_id, level)
        except (FileNotFoundError, KeyError):
            log(f"  no boundary layer for {level}; skipping")
            continue
        written += _write(data / "geography" / f"{level}.json", geo, files)

    # -- per-level constants ------------------------------------------------
    for level in levels:
        level_areas = [a["geoid"] for a in areas if a["level"] == level]
        level_catalog = questions.catalog(dataset, level)
        if not level_catalog:
            log(f"  no measure available at {level}; skipping")
            continue
        written += _write(
            data / "quality" / f"{level}.json",
            _quality_cases(state, release_id, level, level_areas,
                           level_catalog[0].measure_id), files)
        # A representative selection, so every option the interface can offer
        # is listed. One that this build cannot compute is marked unavailable
        # with a reason rather than quietly left out.
        options = benchmark_mod.options(
            level, level_areas[:2], state.config.county_geoids,
            {a["geoid"]: a["name"] for a in areas})
        for option in options:
            if option["benchmark_id"] == benchmark_mod.SELECTED:
                option["available_in_static_build"] = False
                option["unavailable_reason"] = UNSUPPORTED["selected_benchmark"]
            else:
                option["available_in_static_build"] = True
        written += _write(data / "benchmarks" / f"{level}.json",
                          {"benchmarks": options}, files)

    # -- per-measure values, references and question resolution ------------
    catalog_ids = sorted({o.measure_id
                          for level in levels
                          for o in questions.catalog(dataset, level)})
    for measure_id in catalog_ids:
        values = state.values(release_id, measure_id)
        written += _write(data / "values" / f"{measure_id}.json",
                          {"measure_id": measure_id,
                           "release_id": release_id,
                           "period_label": release["period_label"],
                           **_encode_values(values, geoids)}, files)

        references: dict[str, Any] = {}
        questions_for: dict[str, Any] = {}
        for level in levels:
            level_areas = [a["geoid"] for a in areas if a["level"] == level]
            if measure_id not in {o.measure_id
                                  for o in questions.catalog(dataset, level)}:
                continue
            sel = server.build_selection(state, {
                "release_id": release_id, "measure_id": measure_id,
                "level": level, "areas": level_areas})
            bench = server.build_benchmark(state, sel, benchmark_mod.NYC)
            references[level] = bench.to_json()
            questions_for[level] = {
                "one": questions.question_for(measure_id, level, 1),
                "many": questions.question_for(measure_id, level, 2),
            }
        written += _write(data / "reference" / f"{measure_id}.json",
                          {"measure_id": measure_id,
                           "nyc": references,
                           "questions": questions_for}, files)

    # -- the manifest a reader can check the site against ------------------
    manifest = {
        "site": "Census Explorer — static build",
        "generated_at": provenance.utc_now(),
        "code_revision": dataset.get("code_revision"),
        "data_mode": data_mode,
        "base_path": base_path,
        "release": release,
        "source": {
            "provider": release["provider"],
            "citation": release["citation"],
            "product": release["product_label"],
            "period_label": release["period_label"],
            "dataset_key": release["dataset_key"],
            "geography_vintage": release["geography_vintage"],
            "tables": sorted({t for m in dataset["measures"] for t in m["tables"]}),
        },
        "build_manifest_id": dataset.get("manifest_id"),
        "build_manifest_ids": dataset.get("manifest_ids"),
        "counts": {
            "measures": len(catalog_ids),
            "areas": len(geoids),
            "areas_by_level": {lv: sum(1 for a in areas if a["level"] == lv)
                               for lv in levels},
        },
        "join_reports": dataset.get("join_reports", []),
        "unsupported_here": UNSUPPORTED,
        "contents": ("Official published American Community Survey aggregates "
                     "for New York City, the Census Bureau's own cartographic "
                     "boundary files for the matching vintage, and this "
                     "project's wording about them. No raw retrieval cache, no "
                     "saved view, no credential and no local path is included."),
    }
    if data_mode != "live":
        manifest["warning"] = (
            "FIXTURE DATA — every value in this build is synthetic test data "
            "and must not be read as a census finding.")
    written += _write(data / "manifest.json", manifest, files)

    # -- digests, so a published file can be checked against this build ----
    digests = {}
    for path in sorted(files):
        if path.suffix in (".json", ".js", ".css", ".html"):
            digests[path.relative_to(out_dir).as_posix()] = \
                provenance.sha256_bytes(path.read_bytes())
    written += _write(data / "digests.json", {
        "algorithm": "sha256",
        "note": ("Content digests of every file this build wrote, so a "
                 "published copy can be compared against it."),
        "files": digests,
    }, files)

    return BuildReport(out_dir=out_dir, files=files, bytes_written=written,
                       release_id=release_id, measures=len(catalog_ids),
                       areas=len(geoids))
