"""Fixture mode: a runnable, obviously synthetic dataset.

Fixture mode exists so the application can be built, run and tested with no
credentials and no network at all.  Everything it produces is labelled:

* the dataset is marked ``data_mode: "fixture"``;
* area names begin with ``FIXTURE``;
* the geometry is a grid of rectangles whose metadata says ``synthetic: true``
  and which is drawn only in this mode - it is never presented as a boundary;
* figures and exports carry a fixture banner and note.

Fixture values are generated deterministically from the measure and area
identifiers.  They are not census estimates and must never be quoted as
findings.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import provenance
from .config import ProjectConfig

FIXTURE_RELEASE_ID = "fixture_5yr"
FIXTURE_PERIOD_LABEL = "FIXTURE 2019-2023 (synthetic)"
FIXTURE_AREAS = [
    ("99001", "FIXTURE Borough One"),
    ("99003", "FIXTURE Borough Two"),
    ("99005", "FIXTURE Borough Three"),
    ("99007", "FIXTURE Borough Four"),
    ("99009", "FIXTURE Borough Five"),
]
#: Areas that exercise the unavailable paths the interface must handle.
FIXTURE_SPECIAL = {
    "99007": "annotated",   # source value is an annotation, not a number
    "99009": "zero_denom",  # denominator is zero: a share is undefined
}


def _deterministic(*parts: str) -> float:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def synthetic_geometry(index: int) -> dict[str, Any]:
    """A plain rectangle. Conspicuously not a boundary."""
    x0 = -1.0 + index * 0.45
    y0 = -0.4
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x0 + 0.4, y0], [x0 + 0.4, y0 + 0.8],
                         [x0, y0 + 0.8], [x0, y0]]],
    }


def build_fixture_dataset(repo_root: Path, config: ProjectConfig,
                          out_rel: Path | str = "data/fixture-processed") -> Path:
    out_dir = repo_root / Path(out_rel) / FIXTURE_RELEASE_ID
    (out_dir / "values").mkdir(parents=True, exist_ok=True)
    for stale in (out_dir / "values").glob("*.json"):
        stale.unlink()

    release_json = {
        "release_id": FIXTURE_RELEASE_ID,
        "provider": "SYNTHETIC FIXTURE - not a data provider",
        "dataset": "fixture/5yr",
        "vintage": 0,
        "period_start": 2019,
        "period_end": 2023,
        "period_label": FIXTURE_PERIOD_LABEL,
        "product_label": "Synthetic fixture product (not a survey)",
        "period_years": 5,
        "boundary_release": "FIXTURE-GEOMETRY",
        "geography_vintage": "synthetic rectangles; not boundaries of any place",
        "dataset_key": "fixture/5yr|2019-2023|geo:FIXTURE-GEOMETRY",
        "citation": "No citation: these values are synthetic test data.",
    }

    areas = [{"geoid": g, "name": n, "level": "county"} for g, n in FIXTURE_AREAS]

    measures_json = []
    values: dict[str, dict[str, dict]] = {}
    for m in config.measures_for_release():
        entry = m.to_json()
        entry["cells"] = [
            {"cell": c, "table": c.split("_")[0], "estimate_var": f"{c}E",
             "moe_var": f"{c}M", "label": "FIXTURE cell", "label_path": ["FIXTURE"],
             "concept": "FIXTURE", "universe": "FIXTURE universe",
             "estimate_annotation_var": f"{c}EA", "moe_annotation_var": f"{c}MA"}
            for c in m.numerator_cells + m.denominator_cells
        ]
        entry["universe_published"] = ["FIXTURE universe (synthetic)"]
        entry["availability"] = {"county": True}
        measures_json.append(entry)

        per_geo: dict[str, dict] = {}
        for geoid, _name in FIXTURE_AREAS:
            special = FIXTURE_SPECIAL.get(geoid)
            if special == "annotated":
                per_geo[geoid] = {
                    "e": None, "es": "unavailable",
                    "er": f"{m.numerator_cells[0]}: The estimate or margin of error "
                          "cannot be displayed because there were an insufficient "
                          "number of sample cases in the selected geographic area. "
                          "[SYNTHETIC FIXTURE]",
                    "m": None, "ms": "unavailable",
                    "flags": [f"{m.numerator_cells[0]} N"],
                }
                continue
            if special == "zero_denom" and m.kind == "share":
                per_geo[geoid] = {
                    "e": None, "es": "unavailable",
                    "er": "denominator is zero in this area; a share is undefined, "
                          "not zero [SYNTHETIC FIXTURE]",
                    "m": None, "ms": "unavailable",
                }
                continue
            r = _deterministic(m.measure_id, geoid)
            if m.kind == "percent" or m.unit == "percent":
                est = round(4 + r * 55, 4)
                moe = round(0.4 + r * 2.5, 4)
                per_geo[geoid] = {"e": est, "es": "ok", "m": moe, "ms": "ok",
                                  "mr": "proportion formula [SYNTHETIC FIXTURE]",
                                  "n": round(est * 2000), "d": 200000}
            else:
                est = round(1000 + r * 900000)
                moe = round(50 + r * 6000)
                cv = (moe / 1.645) / est * 100
                per_geo[geoid] = {
                    "e": est, "es": "ok", "m": moe, "ms": "ok",
                    "cv": round(cv, 2),
                    "rel": ("lower relative error" if cv < 15 else
                            "moderate relative error" if cv < 30 else
                            "high relative error"),
                }
        values[m.measure_id] = per_geo

    geo = {
        "type": "FeatureCollection",
        "metadata": {
            "level": "county",
            "boundary_release": "FIXTURE-GEOMETRY",
            "geography_vintage": "synthetic",
            "source": "generated by census_explorer.fixtures",
            "synthetic": True,
            "note": "SYNTHETIC GEOMETRY. These rectangles are not the boundaries of "
                    "any real place and must never be presented as such.",
        },
        "features": [
            {"type": "Feature", "id": g, "properties":
                {"GEOID": g, "name": n, "level": "county", "synthetic": True,
                 "land_area_sq_m": None},
             "geometry": synthetic_geometry(i)}
            for i, (g, n) in enumerate(FIXTURE_AREAS)
        ],
    }
    (out_dir / "geography_county.geojson").write_text(
        json.dumps(geo, separators=(",", ":")), encoding="utf-8")

    doc = {
        "schema_version": 1,
        "manifest_id": "fixture",
        "data_mode": "fixture",
        "transport": "fixture",
        "built_at": provenance.utc_now(),
        "code_revision": provenance.code_revision(repo_root),
        "release": release_json,
        "tables": {t: {"universe": "FIXTURE universe (synthetic)",
                       "concept": "FIXTURE", "metadata_cache_path": "n/a"}
                   for t in config.all_tables()},
        "areas": areas,
        "measures": measures_json,
        "value_files": {mid: f"values/{mid}.json" for mid in sorted(values)},
        "join_reports": [{
            "level": "county", "boundary_release": "FIXTURE-GEOMETRY",
            "features_total": len(FIXTURE_AREAS),
            "observations_total": len(FIXTURE_AREAS),
            "matched": len(FIXTURE_AREAS),
            "unmatched_feature_count": 0, "unmatched_observation_count": 0,
            "unmatched_features": [], "unmatched_observations": [],
            "complete": True,
            "unmatched_observation_population": 0,
            "unmatched_observation_population_note": "fixture",
        }],
        "diagnostics": [],
        "warnings": [
            "FIXTURE MODE: every value in this dataset is synthetic. It is here so "
            "the application can be run and tested without credentials or network "
            "access. Do not quote any of it as a census finding.",
        ],
    }
    (out_dir / "dataset.json").write_text(json.dumps(doc, separators=(",", ":")),
                                          encoding="utf-8")
    for mid, per_geo in values.items():
        (out_dir / "values" / f"{mid}.json").write_text(
            json.dumps({"measure_id": mid, "release_id": FIXTURE_RELEASE_ID,
                        "period_label": FIXTURE_PERIOD_LABEL, "values": per_geo},
                       separators=(",", ":")),
            encoding="utf-8")
    return out_dir
