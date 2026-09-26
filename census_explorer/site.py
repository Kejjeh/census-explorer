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
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import benchmark as benchmark_mod
from . import scope as scope_mod
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
                           "New York City, New York State and each county are "
                           "available here because each is one fixed set of "
                           "areas, computed during the build."),
}


class UnsafeOutputDirectory(ValueError):
    """The build refused to write to the directory it was given."""


#: Written into every manifest, and required of any directory this build is
#: willing to replace. A file named `manifest.json` proves nothing on its own.
SITE_MARKER = "Census Explorer — static build"

#: Files a build writes that carry no digest of their own, and so are not in
#: the digest map. `data/digests.json` cannot contain its own digest, and
#: `.nojekyll` is empty. Used only to read an inventory written before this
#: build recorded one explicitly.
UNDIGESTED_FILES = ("data/digests.json", ".nojekyll")


def _contents(target: Path) -> list[str]:
    """Every file and link inside `target`, as posix paths relative to it.

    `os.walk` with `followlinks=False`, because a symbolic link to a
    directory would otherwise be walked into and files outside the target
    counted as if they were in it.
    """
    found: list[str] = []
    for root, dirs, names in os.walk(target, followlinks=False):
        here = Path(root)
        # A symbolic link to a directory is an entry in its own right. It is
        # counted, and not descended into: what it points at is somewhere
        # else and is not this build's to account for.
        linked = [d for d in dirs if (here / d).is_symlink()]
        dirs[:] = [d for d in dirs if d not in linked]
        for name in linked + names:
            found.append((here / name).relative_to(target).as_posix())
    return found


def previous_build_inventory(target: Path) -> set[str] | None:
    """What a previous build of *this* site wrote into `target`, or None.

    Identity is not "there is an index.html". It is: this build's own marker
    in the manifest it wrote, and a digest file that lists what it wrote. A
    directory that cannot produce both is not ours to delete.
    """
    manifest_path = target / "data" / "manifest.json"
    digests_path = target / "data" / "digests.json"
    if not manifest_path.is_file() or not digests_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
        digests = json.loads(digests_path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("site") != SITE_MARKER:
        return None
    if not isinstance(digests, dict):
        return None
    inventory = digests.get("inventory")
    if isinstance(inventory, list) and inventory:
        return {str(name) for name in inventory}
    # Builds before the inventory was recorded listed only digested files.
    files = digests.get("files")
    if isinstance(files, dict) and files:
        return set(files) | set(UNDIGESTED_FILES)
    return None


def check_out_dir(out_dir: Path, repo_root: Path) -> Path:
    """Refuse an output directory whose contents are not ours to replace.

    The build writes a whole tree and therefore has to remove what was there
    before. That makes `--out` the one argument that can destroy work, so it
    is checked before anything is deleted: a typo names a directory this
    refuses, and refusing leaves every file in it untouched.

    A non-empty directory is accepted only when it is a previous build of
    this site *and* holds nothing that build did not write. A file someone
    else put there is reported, not removed.
    """
    target = Path(out_dir).expanduser()
    target = (repo_root / target) if not target.is_absolute() else target
    target = target.resolve()
    repo_root = Path(repo_root).resolve()

    def refuse(reason: str) -> None:
        raise UnsafeOutputDirectory(
            f"refusing to build into {out_dir}: {reason}. Nothing was "
            "deleted. Choose an empty directory, a new one, or the --out "
            "directory of a previous build.")

    if target.parent == target:
        refuse("that is the root of the filesystem")
    if target == Path.home().resolve():
        refuse("that is the home directory")
    if target == repo_root:
        refuse("that is the repository itself")
    if target in repo_root.parents:
        refuse("that directory contains the repository")
    if (target / ".git").exists():
        refuse("that directory is a git repository")
    if not target.exists():
        return target
    if not target.is_dir():
        refuse("that path is a file, not a directory")
    if not any(target.iterdir()):
        return target
    owned = previous_build_inventory(target)
    if owned is None:
        refuse("that directory is not empty and is not a previous build of "
               "this site (no manifest of ours, or no record of what it "
               "wrote)")
    unowned = sorted(set(_contents(target)) - owned)
    if unowned:
        shown = ", ".join(unowned[:4])
        more = f" and {len(unowned) - 4} more" if len(unowned) > 4 else ""
        refuse(f"that directory holds {len(unowned)} file(s) this build did "
               f"not write ({shown}{more})")
    return target


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
          log=print, hospitals_dir: Path | None = None) -> BuildReport:
    """Write the whole static site into `out_dir`.

    ``hospitals_dir`` (a built hospital registry) is optional and off by
    default, so a census-only build is unchanged by the hospital layer.
    """
    repo_root = Path(repo_root).resolve()
    target = check_out_dir(out_dir, repo_root)
    # Written beside the target, in a directory this build creates and
    # therefore owns. A fixed name would let one build delete another's work
    # in progress, or a directory that merely happened to have that name.
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.building-",
                                    dir=target.parent))
    try:
        report = _build_into(staging, repo_root, release_id, data_dir,
                            base_path, log, hospitals_dir)
        _replace_directory(staging, target)
    except BaseException:
        # Only the directory this build made is removed.
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return BuildReport(
        out_dir=target,
        files=[target / f.relative_to(staging) for f in report.files],
        bytes_written=report.bytes_written, release_id=report.release_id,
        measures=report.measures, areas=report.areas)


HOSPITALS_PREFIX = "data/hospitals/"


def _write_hospitals(src: Path, data: Path, files: list[Path], log,
                     census_mode: str) -> dict:
    """Publish a built hospital registry, compactly, with its provenance.

    The pair is verified against its index first (bytes, schema, retrieval,
    rules, mode). Hospital data whose mode differs from the census build's is
    refused: live census figures are never published beside synthetic
    hospital records, nor the other way round.
    """
    from . import hospitals as hospitals_mod
    index, reg_bytes, cert_bytes = hospitals_mod.read_outputs(src)
    registry = json.loads(reg_bytes)
    cert = json.loads(cert_bytes)
    if registry["data_mode"] != census_mode:
        raise ValueError(
            f"the hospital registry in {src} is {registry['data_mode']!r} data but the "
            f"census build is {census_mode!r}; the two are not published together")
    total = _write(data / "hospitals" / "registry.json", strip_local_paths(registry), files)
    total += _write(data / "hospitals" / "certification.json", strip_local_paths(cert), files)
    log(f"hospital registry {registry['retrieval_manifest_id']} ({registry['data_mode']}): "
        f"{len(registry['sites'])} sites, {len(registry['cms_entities'])} CMS entities")
    return {
        "_bytes": total,
        "retrieval_manifest_id": registry["retrieval_manifest_id"],
        "data_mode": registry["data_mode"],
        "built_at": registry.get("built_at"),
        "code_revision": registry.get("code_revision"),
        "retrieval_config_sha256": registry["retrieval_config_sha256"],
        "rules_version": registry["rules_version"],
        "rules_sha256": registry["rules_sha256"],
        "built_files_sha256": index["files"],
        "sites": len(registry["sites"]),
        "cms_entities": len(registry["cms_entities"]),
        "sources": {k: {f: v.get(f) for f in ("title", "publisher", "dataset_id",
                                              "released", "modified", "rows_updated_at",
                                              "rows", "sha256")}
                    for k, v in registry["sources"].items()},
        "note": ("Hospital registry files are digested with the rest of the site but "
                 "are not part of the census snapshot. built_files_sha256 are the "
                 "build's own files; the published copies are re-serialized compactly "
                 "and carry their own digests in data/digests.json."),
    }


def _replace_directory(staging: Path, target: Path) -> None:
    """Put `staging` at `target`, keeping the old contents until it is there.

    This is two renames, not one operation, and is not an atomic swap. What
    it does guarantee is that the previous build is never deleted before the
    new one is in place, and that a failure at the last step puts the
    previous one back where it was.
    """
    if not target.exists():
        staging.rename(target)
        return
    # The previous build is moved inside a directory this build creates, so
    # there is no name to race for and nothing else can be holding it.
    holding = Path(tempfile.mkdtemp(prefix=f".{target.name}.previous-",
                                    dir=target.parent))
    kept = holding / target.name
    target.rename(kept)
    try:
        staging.rename(target)
    except BaseException:
        if not target.exists():
            try:
                kept.rename(target)
            except OSError as restore_failed:
                # Recovery uses the operation that just failed, so it can
                # fail too. What must never happen is losing the previous
                # build quietly: it stays where it is and the message says
                # where that is.
                raise RuntimeError(
                    f"the new build could not be moved into {target}, and "
                    f"the previous one could not be put back. It is intact "
                    f"at {kept} — move it back by hand."
                ) from restore_failed
        shutil.rmtree(holding, ignore_errors=True)
        raise
    shutil.rmtree(holding, ignore_errors=True)


def _build_into(out_dir: Path, repo_root: Path, release_id: str | None,
                data_dir: str, base_path: str, log,
                hospitals_dir: Path | None = None) -> BuildReport:
    """Write the site into an empty directory this build owns."""
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
    # The map levels only. A statewide build also carries the state's own
    # row, which is read as a reference value and never drawn.
    levels = [lv for lv in server.LEVELS if any(a["level"] == lv for a in areas)]
    statewide = state.statewide(release_id)
    boroughs = list(state.config.borough_geoids)
    names = {a["geoid"]: a["name"] for a in areas}

    data = out_dir / "data"

    # -- the app itself, copied verbatim -----------------------------------
    # The interface ships with the package, like the service's own static
    # handler reads it: a build run against a data directory somewhere else
    # must still publish this app, not look for one beside the data.
    web = server.WEB_DIR
    for name in ("app.css", "app.js", "core.js", "static.js", "publish.js",
                 "hospitals.js"):
        source = web / name
        written += _write(out_dir / name, source.read_bytes(), files)
    index = (web / "index.html").read_text(encoding="utf-8")
    injected = (
        '<script>window.CENSUS_EXPLORER_STATIC = '
        + json.dumps({"base": "data/", "base_path": base_path,
                      "unsupported": UNSUPPORTED, "snapshot": "__CENSUS_SNAPSHOT__", "dataDigests": "__CENSUS_DIGESTS__"},
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
        # The sentences are the same for every scope; they are drawn from New
        # York City's places, which every build has.
        city_areas = scope_mod.resolve(scope_mod.Scope(scope_mod.NYC), level,
                                       areas, boroughs, statewide)
        written += _write(
            data / "quality" / f"{level}.json",
            _quality_cases(state, release_id, level, city_areas,
                           level_catalog[0].measure_id), files)
        # A representative selection, so every option the interface can offer
        # is listed. One that this build cannot compute is marked unavailable
        # with a reason rather than quietly left out.
        # The containing county is listed once per county, with the label the
        # service would give it, and the page offers only the one whose
        # county is in view. Before this, one "containing borough" option was
        # listed for every tract view and then could not be served.
        options = benchmark_mod.options(
            level, city_areas[:2], state.config.county_geoids, names,
            statewide=statewide, boroughs=boroughs)
        options = [o for o in options
                   if o["benchmark_id"] != benchmark_mod.CONTAINING_COUNTY]
        for option in options:
            if option["benchmark_id"] == benchmark_mod.SELECTED:
                option["available_in_static_build"] = False
                option["unavailable_reason"] = UNSUPPORTED["selected_benchmark"]
            else:
                option["available_in_static_build"] = True
        containing = {}
        if level == "tract":
            for county in sorted({g[:5] for g in level_areas}):
                opt = next(o for o in benchmark_mod.options(
                    level, [], state.config.county_geoids, names,
                    scope=f"county:{county}", boroughs=boroughs)
                    if o["benchmark_id"] == benchmark_mod.CONTAINING_COUNTY)
                opt["available_in_static_build"] = True
                containing[county] = opt
        written += _write(data / "benchmarks" / f"{level}.json",
                          {"benchmarks": options,
                           "containing_county": containing}, files)

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
        state_refs: dict[str, Any] = {}
        county_refs: dict[str, Any] = {}
        questions_for: dict[str, Any] = {}
        for level in levels:
            if measure_id not in {o.measure_id
                                  for o in questions.catalog(dataset, level)}:
                continue
            # Every reference is computed by the service's own code, from a
            # selection the service itself resolved; the page only looks
            # them up.
            sel = server.build_selection(state, {
                "release_id": release_id, "measure_id": measure_id,
                "level": level})
            references[level] = server.build_benchmark(
                state, sel, benchmark_mod.NYC).to_json()
            if statewide:
                state_refs[level] = server.build_benchmark(
                    state, sel, benchmark_mod.NYS).to_json()
            if level == "tract":
                for county in sorted({a["geoid"] for a in areas
                                      if a["level"] == "county"}):
                    csel = server.build_selection(state, {
                        "release_id": release_id, "measure_id": measure_id,
                        "level": level, "scope": f"county:{county}"})
                    county_refs[county] = server.build_benchmark(
                        state, csel, benchmark_mod.CONTAINING_COUNTY).to_json()
            questions_for[level] = {
                "one": questions.question_for(measure_id, level, 1),
                "many": questions.question_for(measure_id, level, 2),
            }
        written += _write(data / "reference" / f"{measure_id}.json",
                          {"measure_id": measure_id,
                           "nyc": references,
                           "nys": state_refs,
                           "containing_county": county_refs,
                           "questions": questions_for}, files)

    # -- the optional hospital layer ----------------------------------------
    hospitals_section = None
    if hospitals_dir is not None:
        hospitals_section = _write_hospitals(Path(hospitals_dir), data, files, log,
                                             data_mode)
        written += hospitals_section.pop("_bytes")

    # -- the manifest a reader can check the site against ------------------
    manifest = {
        "site": SITE_MARKER,
        "generated_at": provenance.utc_now(),
        # The census data's build revision (also in status.json, and so part
        # of the snapshot), and separately the revision of the code that
        # wrote this site. They differ whenever an unchanged data release is
        # published with newer page code.
        "code_revision": dataset.get("code_revision"),
        "site_code_revision": provenance.code_revision(repo_root),
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
                     + ("for every county and census tract in New York State, "
                        "with New York City as its documented five-borough "
                        "subset, " if statewide else "for New York City, ")
                     + "the Census Bureau's own cartographic "
                     "boundary files for the matching vintage, and this "
                     "project's wording about them. No raw retrieval cache, no "
                     "saved view, no credential and no local path is included."),
    }
    if hospitals_section is not None:
        manifest["hospitals"] = hospitals_section
    if data_mode != "live":
        manifest["warning"] = (
            "FIXTURE DATA — every value in this build is synthetic test data "
            "and must not be read as a census finding.")
    written += _write(data / "manifest.json", manifest, files)

    # Pin public data and interpretation metadata, independently of UI assets.
    snapshot_inputs = {path.relative_to(out_dir).as_posix():
                       provenance.sha256_bytes(path.read_bytes())
                       for path in sorted(files) if data in path.parents}
    # The hospital files are digested and checked like every other file,
    # but they are not part of the census snapshot: refreshing the hospital
    # registry must not invalidate census share links, and a census-only
    # build keeps the same snapshot it always had.
    snapshot = provenance.sha256_bytes(json.dumps(
        {key: value for key, value in snapshot_inputs.items()
         if key != "data/manifest.json"
         and not key.startswith(HOSPITALS_PREFIX)}, sort_keys=True).encode())
    page = out_dir / "index.html"
    page.write_text(page.read_text("utf-8").replace("__CENSUS_SNAPSHOT__", snapshot)
                    .replace('"__CENSUS_DIGESTS__"', json.dumps(snapshot_inputs, separators=(",", ":"))), encoding="utf-8")

    # -- digests, so a published file can be checked against this build ----
    digests = {}
    for path in sorted(files):
        if path.suffix in (".json", ".js", ".css", ".html"):
            digests[path.relative_to(out_dir).as_posix()] = \
                provenance.sha256_bytes(path.read_bytes())
    # The inventory is what makes a later build willing to replace this one:
    # it names every file written, including the ones that carry no digest.
    inventory = sorted({path.relative_to(out_dir).as_posix() for path in files}
                       | {"data/digests.json"})
    written += _write(data / "digests.json", {
        "algorithm": "sha256",
        "note": ("Content digests of every file this build wrote, so a "
                 "published copy can be compared against it."),
        "inventory_note": ("Every file this build wrote, digested or not. A "
                           "later build refuses to replace this directory if "
                           "it holds anything not listed here."),
        "site": SITE_MARKER,
        "inventory": inventory,
        "files": digests,
    }, files)

    return BuildReport(out_dir=out_dir, files=files, bytes_written=written,
                       release_id=release_id, measures=len(catalog_ids),
                       areas=len(geoids))
