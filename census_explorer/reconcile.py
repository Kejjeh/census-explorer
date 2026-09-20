"""Reconcile the assembled dataset against an independently published row.

New York City as a place (GEOID 3651000) is exactly the five boroughs, and the
Census Bureau publishes it as its own row in the same table.  Summing our five
county rows and comparing them to that published place row is a real check on
the whole path: the right cells, the right geographies, the right parsing, and
no silent row loss or duplication.

A mismatch is reported with its size.  It is never absorbed.
"""

from __future__ import annotations

from pathlib import Path

from . import provenance
from .config import ProjectConfig, Release
from .dataset import read_summary_file
from .retrieve import acs_summary_file
from .sentinels import classify

#: New York City, place FIPS 3651000 in state 36.
NYC_PLACE_GEOID = "3651000"
NYC_PLACE_GEO_ID = f"1600000US{NYC_PLACE_GEOID}"

#: Cells whose borough sum must equal the published city row.
RECONCILED_CELLS = [
    ("B01003_001", "Total population"),
    ("B05002_001", "Total population (place of birth table)"),
    ("B05002_013", "Foreign-born population"),
    ("B05002_003", "Born in state of residence"),
    ("B06004B_001", "Black or African American alone population"),
    ("B06009_001", "Population 25 years and over"),
]


PLACE_SUFFIX = "place_nyc"


def cache_rel_path(release: Release, table: str) -> str:
    return acs_summary_file.cache_rel_path(release, table, PLACE_SUFFIX)


def fetch_place_rows(repo_root: Path, release: Release, tables: list[str],
                     manifest: provenance.Manifest, log=print) -> None:
    """Retrieve the published New York City place row for each table."""
    for table in tables:
        log(f"  streaming Summary File for {table} (New York City place row) ...")
        acs_summary_file.fetch_table(
            repo_root, release, table, [("place", [NYC_PLACE_GEOID])], manifest,
            name_suffix=PLACE_SUFFIX)


def run(repo_root: Path, config: ProjectConfig, release: Release,
        tolerance: int = 0) -> dict:
    """Compare the borough sum with the published city row for each checked cell."""
    results = []
    for cell, label in RECONCILED_CELLS:
        table = cell.split("_")[0]
        borough_path = repo_root / f"data/raw/acs/{release.release_id}/summary_file/{table}.psv"
        place_path = repo_root / cache_rel_path(release, table)
        if not borough_path.exists() or not place_path.exists():
            results.append({
                "cell": cell, "label": label, "status": "not checked",
                "note": "cached rows are missing; run: fetch observations, then "
                        "reconcile --fetch",
            })
            continue
        boroughs = read_summary_file(borough_path)
        place = read_summary_file(place_path)

        total = 0.0
        problems = []
        for geoid in config.county_geoids:
            value = classify(boroughs.get(f"0500000US{geoid}", {}).get(cell, (None, None))[0])
            if not value.is_number:
                problems.append(f"{geoid}: {value.meaning or value.status}")
                continue
            total += value.value
        published = classify(place.get(NYC_PLACE_GEO_ID, {}).get(cell, (None, None))[0])

        if problems or not published.is_number:
            results.append({
                "cell": cell, "label": label, "status": "not comparable",
                "borough_sum": total if not problems else None,
                "published_city_value": published.value if published.is_number else None,
                "note": "; ".join(problems) or (published.meaning or "no published value"),
            })
            continue

        difference = total - published.value
        results.append({
            "cell": cell,
            "label": label,
            "status": "match" if abs(difference) <= tolerance else "MISMATCH",
            "borough_sum": total,
            "published_city_value": published.value,
            "difference": difference,
        })

    checked = [r for r in results if r["status"] in ("match", "MISMATCH")]
    return {
        "release": release.to_json(),
        "source": (
            "Borough (county) rows summed and compared with the separately published "
            "New York City place row (GEOID 3651000) from the same table and release. "
            "New York City consists of exactly these five counties."
        ),
        "tolerance": tolerance,
        "checked": len(checked),
        "matched": len([r for r in checked if r["status"] == "match"]),
        "results": results,
        "passed": bool(checked) and all(r["status"] == "match" for r in checked),
    }
