"""Orchestration for the retrieval and build commands.

Each function corresponds to one explicit user action.  Retrieval writes
immutable raw bytes plus a manifest; the build step reads only what retrieval
cached.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import dataset, metadata, provenance
from .config import ProjectConfig, Release
from .retrieve import acs_api, acs_metadata, acs_summary_file, boundaries, reference

MANIFEST_DIR = "data/manifests"


def manifest_path(repo_root: Path, manifest_id: str) -> Path:
    return repo_root / MANIFEST_DIR / f"{manifest_id}.json"


def save_manifest(repo_root: Path, manifest: provenance.Manifest) -> Path:
    path = manifest_path(repo_root, manifest.manifest_id)
    manifest.save(path)
    return path


def new_manifest(release: Release | None, action: str, data_mode: str,
                 repo_root: Path) -> provenance.Manifest:
    stamp = provenance.utc_now().replace(":", "").replace("-", "")
    suffix = release.release_id if release else "project"
    m = provenance.Manifest.new(
        manifest_id=f"{action}-{suffix}-{stamp}",
        description=f"{action} for {suffix}",
        data_mode=data_mode,
        repo_root=repo_root,
    )
    if release:
        m.inputs["release"] = release.to_json()
    return m


def fetch_metadata(repo_root: Path, config: ProjectConfig, release: Release,
                   log=print) -> provenance.Manifest:
    manifest = new_manifest(release, "metadata", "live", repo_root)
    for table in config.all_tables():
        acs_metadata.fetch_group(repo_root, release, table, manifest)
        log(f"  metadata cached: {table} ({release.period_label})")
    save_manifest(repo_root, manifest)
    return manifest


def fetch_reference(repo_root: Path, log=print) -> provenance.Manifest:
    manifest = new_manifest(None, "reference", "live", repo_root)
    path = reference.refresh(repo_root, manifest)
    log(f"  annotation reference rebuilt: {path}")
    path = reference.refresh_definitions(repo_root, manifest)
    log(f"  quoted definitions rebuilt: {path}")
    save_manifest(repo_root, manifest)
    return manifest


def fetch_geography(repo_root: Path, config: ProjectConfig, release: Release,
                    levels: list[str], log=print) -> provenance.Manifest:
    manifest = new_manifest(release, "geography", "live", repo_root)
    for level in levels:
        boundaries.fetch_boundaries(repo_root, release, level, config.state_fips, manifest)
        log(f"  boundaries cached: {level} @ {release.boundary_release}")
    save_manifest(repo_root, manifest)
    return manifest


def fetch_roster(repo_root: Path, config: ProjectConfig, release: Release,
                 log=print) -> provenance.Manifest:
    """Retrieve the release's own list of the geographies it publishes."""
    manifest = new_manifest(release, "roster", "live", repo_root)
    fips = str(config.study_area["state_fips"])
    log(f"  streaming the {release.period_label} geography file for state {fips} ...")
    acs_summary_file.fetch_roster(repo_root, release, fips, manifest,
                                  progress=lambda s: log("   " + s))
    save_manifest(repo_root, manifest)
    return manifest


def fetch_observations(repo_root: Path, config: ProjectConfig, release: Release,
                       levels: list[str], transport: str,
                       log=print) -> provenance.Manifest:
    manifest = new_manifest(release, f"observations-{transport}", "live", repo_root)
    tables = config.all_tables()

    if transport == "summary-file":
        if config.statewide:
            # Every row of each level whose GEOID begins with the state FIPS,
            # plus the state's own published row. The state row is what the
            # county totals are reconciled against, and the published figure a
            # state reference is read from; it is not a map level.
            fips = str(config.study_area["state_fips"])
            selections = [("state", [fips])] + [(lvl, [fips]) for lvl in levels]
            log(f"  statewide coverage: state {fips}, levels "
                f"{', '.join(['state', *levels])}")
        else:
            selections = [(lvl, config.county_geoids) for lvl in levels]
        for table in tables:
            log(f"  streaming Summary File for {table} ...")
            acs_summary_file.fetch_table(
                repo_root, release, table, selections, manifest,
                progress=lambda s: log("   " + s),
                # A different geographic selection goes to a different file,
                # so an earlier, narrower retrieval and its manifest survive.
                name_suffix=config.cache_suffix,
            )
    elif transport == "api":
        meta = metadata.load_release_metadata(repo_root, release, tables)
        for table in tables:
            variables: list[str] = []
            for cell in sorted(meta.tables[table]):
                cm = meta.tables[table][cell]
                variables.append(cm.estimate_var)
                if cm.moe_var:
                    variables.append(cm.moe_var)
            if "county" in levels:
                acs_api.fetch_variables(
                    repo_root, release, table, "county", variables,
                    acs_api.geography_params("county", config.state_fips), manifest,
                )
                log(f"  API: {table} counties")
            if "tract" in levels:
                for county in sorted(config.counties):
                    acs_api.fetch_variables(
                        repo_root, release, table, "tract", variables,
                        acs_api.geography_params("tract", config.state_fips, county),
                        manifest, label=county,
                    )
                log(f"  API: {table} tracts ({len(config.counties)} counties)")
    else:
        raise ValueError(f"unknown transport {transport!r}")

    save_manifest(repo_root, manifest)
    return manifest


def build_dataset(repo_root: Path, config: ProjectConfig, release: Release,
                  levels: list[str], transport: str, manifest_id: str,
                  data_mode: str = "live", log=print,
                  manifest_ids: list[str] | None = None) -> Path:
    meta = metadata.load_release_metadata(repo_root, release, config.all_tables())
    problems = metadata.verify_measures(meta, config.measures_for_release())
    if problems:
        raise dataset.DatasetError(
            "measure definitions do not match the published release:\n  - "
            + "\n  - ".join(problems)
        )
    result = dataset.build(repo_root, config, release, meta, levels, transport)
    dataset.add_cross_table_diagnostics(repo_root, release, result, transport,
                                        config=config)
    dataset.attach_geography(repo_root, config, release, result, levels)
    path = dataset.write_processed(
        repo_root, config, release, meta, result, manifest_id, data_mode, transport,
        manifest_ids=manifest_ids or manifests_for_release(repo_root, release.release_id),
    )
    for report in result.join_reports:
        log("  join: " + json.dumps(
            {k: report[k] for k in ("level", "matched", "unmatched_feature_count",
                                    "unmatched_observation_count")}))
    for warning in result.warnings[:20]:
        log("  warning: " + warning)
    log(f"  dataset written: {path}")
    return path


def manifests_for_release(repo_root: Path, release_id: str) -> list[str]:
    """Every manifest that recorded an input for this release, oldest first."""
    d = repo_root / MANIFEST_DIR
    if not d.exists():
        return []
    out: list[tuple[float, str]] = []
    for path in d.glob("*.json"):
        try:
            doc = provenance.read_json(path)
        except (ValueError, OSError):
            continue
        if (doc.get("inputs") or {}).get("release", {}).get("release_id") == release_id:
            out.append((path.stat().st_mtime, doc["manifest_id"]))
    return [mid for _mtime, mid in sorted(out)]


def observations_manifest_id(repo_root: Path, release_id: str) -> str | None:
    """The most recent manifest that retrieved observations for this release."""
    candidates = [m for m in manifests_for_release(repo_root, release_id)
                  if m.startswith("observations-")]
    return candidates[-1] if candidates else None


def latest_manifest_id(repo_root: Path) -> str | None:
    d = repo_root / MANIFEST_DIR
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0].stem if files else None


def verify_manifests(repo_root: Path, log=print) -> list[str]:
    d = repo_root / MANIFEST_DIR
    problems: list[str] = []
    if not d.exists():
        return ["no manifests found; nothing has been retrieved yet"]
    for path in sorted(d.glob("*.json")):
        m = provenance.Manifest.load(path)
        found = m.verify(repo_root)
        log(f"  {path.name}: {len(m.records)} artifacts, "
            f"{'OK' if not found else str(len(found)) + ' problem(s)'}")
        problems.extend(f"{path.name}: {p}" for p in found)
    return problems
