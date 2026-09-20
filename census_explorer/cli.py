"""Command line entry point.

Every command that touches the network says so in its name (``fetch``,
``reference refresh``, ``smoke``).  ``build``, ``serve``, ``export`` and
``verify`` are offline operations on the cache.

Windows PowerShell and POSIX shells run these identically::

    python -m census_explorer.cli fetch all --release acs5_2023
    python -m census_explorer.cli build --release acs5_2023
    python -m census_explorer.cli serve
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (compare as compare_mod, config as config_mod, dataset as dataset_mod,
               fixtures as fixtures_mod, http_client, metadata as metadata_mod,
               pipeline, projects as projects_mod, provenance, reconcile as reconcile_mod,
               server as server_mod, snapshot as snapshot_mod)
from .redact import redact
from .retrieve import acs_api

REPO_ROOT = Path(__file__).resolve().parent.parent
LEVELS = ("county", "tract")


def _levels(text: str) -> list[str]:
    out = [x.strip() for x in text.split(",") if x.strip()]
    bad = [x for x in out if x not in LEVELS]
    if bad:
        raise argparse.ArgumentTypeError(f"unsupported level(s) {bad}; choose from {LEVELS}")
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m census_explorer.cli",
        description="Census Explorer: retrieve, validate and explore official census data locally.",
    )
    p.add_argument("--repo-root", default=str(REPO_ROOT),
                   help="project root (default: the installed source tree)")
    sub = p.add_subparsers(dest="command", required=True)

    # fetch ---------------------------------------------------------------
    fetch = sub.add_parser("fetch", help="explicit network retrieval")
    fsub = fetch.add_subparsers(dest="what", required=True)
    for what, helptext in [
        ("metadata", "official table metadata (no credential required)"),
        ("geography", "cartographic boundary files of the matching vintage"),
        ("observations", "ACS estimates and margins of error"),
        ("all", "metadata, geography and observations"),
    ]:
        sp = fsub.add_parser(what, help=helptext)
        sp.add_argument("--release", default=None,
                        help="release id (default: the configured default release)")
        sp.add_argument("--levels", type=_levels, default=list(LEVELS),
                        help="comma-separated geography levels (default: county,tract)")
        if what in ("observations", "all"):
            sp.add_argument("--transport", choices=("summary-file", "api"),
                            default="summary-file",
                            help="summary-file needs no key; api needs CENSUS_API_KEY")

    # reference -----------------------------------------------------------
    ref = sub.add_parser("reference", help="official reference documentation")
    ref.add_argument("action", choices=("refresh", "show"))

    # build / verify ------------------------------------------------------
    b = sub.add_parser("build", help="build the analysis dataset from the cache (offline)")
    b.add_argument("--release", default=None)
    b.add_argument("--levels", type=_levels, default=list(LEVELS))
    b.add_argument("--transport", choices=("summary-file", "api"), default="summary-file")
    b.add_argument("--all-releases", action="store_true")

    v = sub.add_parser("verify", help="offline integrity checks")
    v.add_argument("what", choices=("manifests", "catalog", "dataset"))
    v.add_argument("--release", default=None)

    # catalog -------------------------------------------------------------
    c = sub.add_parser("catalog", help="inspect the measure catalog")
    c.add_argument("action", choices=("list", "show", "verify"))
    c.add_argument("--release", default=None)
    c.add_argument("--measure", default=None)
    c.add_argument("--search", default=None)

    # compare -------------------------------------------------------------
    cmp_ = sub.add_parser("compare", help="check whether two releases may be compared")
    cmp_.add_argument("--a", required=True)
    cmp_.add_argument("--b", required=True)
    cmp_.add_argument("--level", choices=LEVELS, default="county")
    cmp_.add_argument("--measure", required=True,
                      help="required: semantic compatibility cannot be established "
                           "without naming a measure")

    # fixtures ------------------------------------------------------------
    fx = sub.add_parser("fixtures", help="build the offline fixture dataset (synthetic)")
    fx.add_argument("action", choices=("build",))
    fx.add_argument("--out", default="data/fixture-processed")

    # projects ------------------------------------------------------------
    pr = sub.add_parser("project", help="saved project definitions")
    pr.add_argument("action", choices=("list", "show", "verify", "delete"))
    pr.add_argument("--id", default=None)

    # export --------------------------------------------------------------
    ex = sub.add_parser("export", help="write a CSV + provenance + figure bundle")
    ex.add_argument("--project", default=None, help="saved project id")
    ex.add_argument("--release", default=None)
    ex.add_argument("--measure", default=None)
    ex.add_argument("--level", choices=LEVELS, default="county")
    ex.add_argument("--compare-with", default=None)
    ex.add_argument("--areas", default=None,
                    help="comma-separated GEOIDs; the CSV and the figure both use "
                         "exactly this selection")
    ex.add_argument("--figure", choices=("map", "chart", "none"), default="map")
    ex.add_argument("--data-dir", default="data/processed")

    # reconcile -----------------------------------------------------------
    rc = sub.add_parser(
        "reconcile",
        help="compare the borough sum with the published New York City row")
    rc.add_argument("--release", default=None)
    rc.add_argument("--fetch", action="store_true",
                    help="retrieve the published city rows first (network)")
    rc.add_argument("--tolerance", type=int, default=0)

    # serve ---------------------------------------------------------------
    s = sub.add_parser("serve", help="run the local browser application")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--data-dir", default="data/processed",
                   help="use data/fixture-processed to run in fixture mode")

    # smoke ---------------------------------------------------------------
    sm = sub.add_parser("smoke", help="live network smoke test (explicit, separate action)")
    sm.add_argument("--release", default=None)
    sm.add_argument("--transport", choices=("summary-file", "api"), default="api")

    return p


def _release(cfg: config_mod.ProjectConfig, release_id: str | None):
    return cfg.release(release_id or cfg.raw["explorer"]["default_release"])


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.repo_root)
    cfg = config_mod.load()
    log = print

    try:
        return _dispatch(args, root, cfg, log)
    except snapshot_mod.SnapshotMismatch as exc:
        log(redact(str(exc)))
        return 4
    except acs_api.MissingCredential as exc:
        log("\nNo Census API key is configured.")
        log(redact(str(exc)))
        log("\nThe keyless Summary File transport retrieves the same published "
            "estimates:\n  python -m census_explorer.cli fetch all --transport summary-file")
        return 2
    except Exception as exc:
        log(f"error: {redact(f'{type(exc).__name__}: {exc}')}")
        return 1


def _dispatch(args, root: Path, cfg: config_mod.ProjectConfig, log) -> int:
    if args.command == "fetch":
        release = _release(cfg, args.release)
        log(f"Retrieving {args.what} for {release.period_label} ({release.release_id})")
        if args.what in ("metadata", "all"):
            pipeline.fetch_metadata(root, cfg, release, log=log)
        if args.what in ("geography", "all"):
            pipeline.fetch_geography(root, cfg, release, args.levels, log=log)
        if args.what in ("observations", "all"):
            pipeline.fetch_observations(root, cfg, release, args.levels,
                                        args.transport, log=log)
        log("done. Next: python -m census_explorer.cli build "
            f"--release {release.release_id}")
        return 0

    if args.command == "reference":
        if args.action == "refresh":
            pipeline.fetch_reference(root, log=log)
            return 0
        from .sentinels import annotation_table, reference_provenance
        log(json.dumps({"provenance": reference_provenance(),
                        "values": annotation_table()}, indent=2))
        return 0

    if args.command == "build":
        ids = ([r for r in cfg.releases] if args.all_releases
               else [(args.release or cfg.raw["explorer"]["default_release"])])
        for rid in ids:
            release = cfg.release(rid)
            manifest_id = (pipeline.observations_manifest_id(root, rid)
                           or pipeline.latest_manifest_id(root) or "unrecorded")
            log(f"Building {release.period_label} ({rid}) from the cache")
            pipeline.build_dataset(root, cfg, release, args.levels, args.transport,
                                   manifest_id, data_mode="live", log=log)
        log("done. Next: python -m census_explorer.cli serve")
        return 0

    if args.command == "verify":
        if args.what == "manifests":
            problems = pipeline.verify_manifests(root, log=log)
            for p in problems:
                log("  PROBLEM: " + p)
            log("manifest verification: " + ("OK" if not problems else "FAILED"))
            return 0 if not problems else 1
        if args.what == "catalog":
            release = _release(cfg, args.release)
            meta = metadata_mod.load_release_metadata(root, release, cfg.all_tables())
            problems = metadata_mod.verify_measures(meta, cfg.measures_for_release())
            for p in problems:
                log("  PROBLEM: " + p)
            log(f"{len(cfg.measures)} measures checked against {release.period_label}: "
                + ("OK" if not problems else "FAILED"))
            return 0 if not problems else 1
        release = _release(cfg, args.release)
        st = server_mod.ServiceState(root)
        ds = st.dataset(release.release_id)
        log(json.dumps({"release": ds["release"], "areas": len(ds["areas"]),
                        "measures": len(ds["measures"]),
                        "join_reports": ds["join_reports"],
                        "diagnostics": [
                            {"check_id": d["check_id"],
                             "max_absolute_difference": d["max_absolute_difference"]}
                            for d in ds["diagnostics"]],
                        "warnings": ds["warnings"][:10]}, indent=2))
        return 0

    if args.command == "catalog":
        release = _release(cfg, args.release)
        if args.action == "verify":
            return _dispatch(argparse.Namespace(command="verify", what="catalog",
                                                release=args.release,
                                                repo_root=str(root)), root, cfg, log)
        meta = metadata_mod.load_release_metadata(root, release, cfg.all_tables())
        if args.action == "list":
            needle = (args.search or "").lower()
            for m in cfg.measures_for_release():
                haystack = " ".join([m.measure_id, m.label, m.concept,
                                     " ".join(m.topics), " ".join(m.tables)]).lower()
                if needle and needle not in haystack:
                    continue
                log(f"{m.measure_id:<44} {m.unit:<8} {m.label}")
            return 0
        if not args.measure:
            log("--measure is required for 'catalog show'")
            return 2
        m = cfg.measures[args.measure]
        doc = m.to_json()
        doc["cells"] = [meta.cell(c).to_json()
                        for c in m.numerator_cells + m.denominator_cells]
        doc["release"] = release.to_json()
        log(json.dumps(doc, indent=2))
        return 0

    if args.command == "compare":
        st = server_mod.ServiceState(root)
        report = server_mod.compatibility(st, args.a, args.b, args.level, args.measure)
        log(json.dumps(report, indent=2))
        if not report["allowed"]:
            log("\nThis comparison is blocked. Every reason above must be resolved "
                "before the two releases may be shown together.")
        return 0 if report["allowed"] else 1

    if args.command == "fixtures":
        out = fixtures_mod.build_fixture_dataset(root, cfg, Path(args.out))
        log(f"fixture dataset written to {out}")
        log("Run it with: python -m census_explorer.cli serve --data-dir " + args.out)
        return 0

    if args.command == "project":
        if args.action == "list":
            for p in projects_mod.listing(root):
                log(f"{p['project_id']:<28} {p['release_id']:<12} {p['level']:<7} "
                    f"{p['measure_id']}")
            return 0
        if not args.id:
            log("--id is required")
            return 2
        if args.action == "show":
            log(json.dumps(projects_mod.load(root, args.id).to_json(), indent=2))
            return 0
        if args.action == "verify":
            project = projects_mod.load(root, args.id)
            pin = project.pin()
            if pin is None:
                log(f"project '{args.id}' has no pinned inputs and cannot be "
                    "replayed reproducibly; re-save it")
                return 1
            problems = snapshot_mod.verify(root, pin)
            for problem in problems:
                log("  PROBLEM: " + problem)
            log(f"{len(pin.inputs)} pinned input(s): "
                + ("OK" if not problems else f"{len(problems)} problem(s)"))
            return 0 if not problems else 1
        log("deleted" if projects_mod.delete(root, args.id) else "no such project")
        return 0

    if args.command == "export":
        st = server_mod.ServiceState(root, args.data_dir)
        if args.project:
            # The project's own pinned definitions and inputs decide what is
            # exported; the current catalog does not get a say.
            payload = {
                "project_id": args.project,
                "figure_kind": "map" if args.figure == "none" else args.figure,
                "include_figure": args.figure != "none",
            }
        else:
            if not args.measure:
                log("--measure (or --project) is required")
                return 2
            payload = {
                "release_id": args.release or cfg.raw["explorer"]["default_release"],
                "measure_id": args.measure, "level": args.level,
                "areas": [a for a in (args.areas or "").split(",") if a],
                "comparison_release_id": args.compare_with,
                "figure_kind": "map" if args.figure == "none" else args.figure,
                "include_figure": args.figure != "none",
            }
        result = server_mod.run_export(st, payload)
        log(json.dumps(result, indent=2))
        return 0

    if args.command == "reconcile":
        release = _release(cfg, args.release)
        if args.fetch:
            manifest = pipeline.new_manifest(release, "reconcile", "live", root)
            tables = sorted({c.split("_")[0] for c, _ in reconcile_mod.RECONCILED_CELLS})
            reconcile_mod.fetch_place_rows(root, release, tables, manifest, log=log)
            pipeline.save_manifest(root, manifest)
        report = reconcile_mod.run(root, cfg, release, args.tolerance)
        log(json.dumps(report, indent=2))
        out = root / "data/processed" / release.release_id / "reconciliation.json"
        if out.parent.exists():
            provenance.write_json(out, report)
            log(f"written to {out.relative_to(root)}")
        return 0 if report["passed"] else 1

    if args.command == "serve":
        server_mod.serve(root, args.host, args.port, args.data_dir, log=log)
        return 0

    if args.command == "smoke":
        return _smoke(root, cfg, args, log)

    log(f"unknown command {args.command}")
    return 2


def _smoke(root: Path, cfg: config_mod.ProjectConfig, args, log) -> int:
    """A deliberately small live request, run only when asked for explicitly."""
    release = _release(cfg, args.release)
    log(f"LIVE network smoke test against {release.period_label} "
        f"({args.transport}). This is the only command that contacts a provider "
        "outside 'fetch'.")
    manifest = pipeline.new_manifest(release, "smoke", "live", root)
    if args.transport == "api":
        if not acs_api.has_api_key():
            log("CENSUS_API_KEY is not set: the keyed API smoke test cannot run.")
            log("Set it locally and re-run, or use --transport summary-file.")
            return 3
        from .retrieve import acs_api as api
        paths = api.fetch_variables(
            root, release, "B01003", "county", ["B01003_001E", "B01003_001M"],
            api.geography_params("county", cfg.state_fips), manifest, label="smoke",
        )
        rows = dataset_mod.read_api_response(paths[0])
    else:
        from .retrieve import acs_summary_file as sf
        path = sf.fetch_table(root, release, "B01003",
                              [("county", cfg.county_geoids)], manifest)
        rows = dataset_mod.read_summary_file(path)
    pipeline.save_manifest(root, manifest)

    from .sentinels import classify
    ok = 0
    for geoid in cfg.county_geoids:
        cells = rows.get(f"0500000US{geoid}")
        if not cells:
            log(f"  MISSING: {geoid}")
            continue
        est = classify(cells["B01003_001"][0])
        moe = classify(cells["B01003_001"][1])
        log(f"  {geoid} {cfg.county_name(geoid):<28} "
            f"population={est.value if est.is_number else est.status} "
            f"moe={moe.value if moe.is_number else (moe.symbol or moe.status)}")
        ok += 1 if est.is_number else 0
    log(f"live smoke test: {ok}/{len(cfg.county_geoids)} boroughs returned a usable estimate")
    return 0 if ok == len(cfg.county_geoids) else 1


if __name__ == "__main__":
    sys.exit(main())
