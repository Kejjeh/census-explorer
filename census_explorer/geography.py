"""Geographic identifiers, polygons and join accounting.

Two rules drive this module:

1. A GEOID is a string.  ``36005`` is not the integer 36005, and a leading zero
   that is lost is a county that silently moves.
2. A join must be accounted for.  Unmatched features and unmatched observations
   are reported by count and by identifier; they are never dropped quietly and
   a duplicate identifier is a hard error, not a row multiplier.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import shapefile
from .config import Release

GEOID_RE = re.compile(r"^\d+$")
COUNTY_GEOID_LEN = 5
TRACT_GEOID_LEN = 11

#: Summary File / API ``GEO_ID`` looks like ``0500000US36005``.
_GEO_ID_RE = re.compile(r"^(\d{7})US(\d+)$")


class GeographyError(ValueError):
    pass


def split_geo_id(geo_id: str) -> tuple[str, str]:
    """Split a full ``GEO_ID`` into (summary level, GEOID string)."""
    m = _GEO_ID_RE.match(geo_id.strip())
    if not m:
        raise GeographyError(f"unrecognised GEO_ID {geo_id!r}")
    return m.group(1), m.group(2)


def level_for_geoid(geoid: str) -> str:
    if len(geoid) == COUNTY_GEOID_LEN:
        return "county"
    if len(geoid) == TRACT_GEOID_LEN:
        return "tract"
    raise GeographyError(f"GEOID {geoid!r} is neither a county nor a tract identifier")


def validate_geoid(geoid: Any, level: str | None = None) -> str:
    if not isinstance(geoid, str):
        raise GeographyError(
            f"GEOID must be a string, got {type(geoid).__name__} ({geoid!r}). "
            "Numeric GEOIDs lose leading zeroes."
        )
    if not GEOID_RE.match(geoid):
        raise GeographyError(f"GEOID {geoid!r} is not a digit string")
    expected = {"county": COUNTY_GEOID_LEN, "tract": TRACT_GEOID_LEN}
    if level:
        if level not in expected:
            raise GeographyError(f"unsupported level {level!r}")
        if len(geoid) != expected[level]:
            raise GeographyError(
                f"GEOID {geoid!r} has {len(geoid)} digits; a {level} GEOID has "
                f"{expected[level]}"
            )
    return geoid


def check_unique(geoids: Iterable[str], what: str) -> None:
    seen: dict[str, int] = {}
    for g in geoids:
        seen[g] = seen.get(g, 0) + 1
    dupes = sorted(g for g, n in seen.items() if n > 1)
    if dupes:
        raise GeographyError(
            f"duplicate {what} identifiers would multiply rows on join: {dupes[:10]}"
            + (f" (+{len(dupes) - 10} more)" if len(dupes) > 10 else "")
        )


@dataclass
class JoinReport:
    """Exact accounting for one geography/observation join."""

    level: str
    boundary_release: str
    features_total: int = 0
    observations_total: int = 0
    matched: int = 0
    unmatched_features: list[str] = field(default_factory=list)
    unmatched_observations: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.unmatched_features and not self.unmatched_observations

    def to_json(self) -> dict:
        return {
            "level": self.level,
            "boundary_release": self.boundary_release,
            "features_total": self.features_total,
            "observations_total": self.observations_total,
            "matched": self.matched,
            "unmatched_feature_count": len(self.unmatched_features),
            "unmatched_observation_count": len(self.unmatched_observations),
            "unmatched_features": sorted(self.unmatched_features)[:50],
            "unmatched_observations": sorted(self.unmatched_observations)[:50],
            "complete": self.is_complete,
        }

    def summary(self) -> str:
        return (
            f"{self.level} @ {self.boundary_release}: {self.matched} matched, "
            f"{len(self.unmatched_features)} boundary features without observations, "
            f"{len(self.unmatched_observations)} observations without a boundary"
        )


def join(feature_geoids: Iterable[str], observation_geoids: Iterable[str],
         level: str, boundary_release: str) -> JoinReport:
    features = list(feature_geoids)
    observations = list(observation_geoids)
    check_unique(features, f"{level} boundary")
    check_unique(observations, f"{level} observation")
    fs, os_ = set(features), set(observations)
    return JoinReport(
        level=level,
        boundary_release=boundary_release,
        features_total=len(features),
        observations_total=len(observations),
        matched=len(fs & os_),
        unmatched_features=sorted(fs - os_),
        unmatched_observations=sorted(os_ - fs),
    )


def _feature_geoid(props: dict[str, str], level: str) -> str | None:
    """Pull the GEOID out of a cartographic boundary attribute row."""
    for key in ("GEOID", "GEOID20", "GEOID10"):
        if props.get(key):
            return props[key]
    # Older/newer vintages carry the fully-qualified form instead.
    for key in ("GEOIDFQ", "AFFGEOID"):
        if props.get(key):
            try:
                return split_geo_id(props[key])[1]
            except GeographyError:
                continue
    return None


def build_geojson(zip_path: Path, level: str, keep_geoids: set[str],
                  release: Release) -> tuple[dict[str, Any], list[str]]:
    """Convert a cached boundary zip into GeoJSON restricted to ``keep_geoids``.

    Returns the FeatureCollection and the list of retained GEOIDs.
    """
    features = shapefile.read_zip(zip_path)
    out_features = []
    geoids: list[str] = []
    for feat in features:
        geoid = _feature_geoid(feat.properties, level)
        if geoid is None or geoid not in keep_geoids:
            continue
        validate_geoid(geoid, level)
        geometry = shapefile.round_geometry(feat.geometry)
        if geometry is None:
            continue
        name = (feat.properties.get("NAMELSAD") or feat.properties.get("NAME")
                or feat.properties.get("BASENAME") or geoid)
        out_features.append({
            "type": "Feature",
            "id": geoid,
            "properties": {
                "GEOID": geoid,
                "name": name,
                "level": level,
                "land_area_sq_m": feat.properties.get("ALAND"),
            },
            "geometry": geometry,
        })
        geoids.append(geoid)
    check_unique(geoids, f"{level} boundary")
    collection = {
        "type": "FeatureCollection",
        "metadata": {
            "level": level,
            "boundary_release": release.boundary_release,
            "geography_vintage": release.geography_vintage,
            "source": "US Census Bureau cartographic boundary files (1:500,000)",
            "coordinate_reference_system": "EPSG:4269 (NAD83), as published",
            "synthetic": False,
            "note": "Published boundary geometry. Generalized for cartography; "
                    "not a legal boundary description.",
        },
        "features": out_features,
    }
    return collection, geoids


def write_geojson(path: Path, collection: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")
