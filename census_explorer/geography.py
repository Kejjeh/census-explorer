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
import math
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


# ---------------------------------------------------------------------------
# Boundary-vintage equivalence
# ---------------------------------------------------------------------------

EQUIVALENCE_CONFIG = (Path(__file__).resolve().parent.parent
                      / "config" / "geography_equivalence.json")

#: Metres per degree, adequate for comparing two renderings of the same area.
_METRES_PER_DEGREE_LAT = 110574.0
_METRES_PER_DEGREE_LON_EQUATOR = 111320.0


def equivalence_rule() -> dict[str, Any]:
    with open(EQUIVALENCE_CONFIG, "r", encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class GeographyEvidence:
    """Why two releases' areas may (or may not) be compared one to one.

    ``established`` is the only field that decides anything.  Everything else
    exists so the reason can be shown rather than asserted.
    """

    kind: str                  # same_vintage | computed_geometry | reviewed_record | none
    established: bool
    level: str
    boundary_release_a: str
    boundary_release_b: str
    detail: str
    areas_compared: int = 0
    max_relative_area_difference: float | None = None
    max_centroid_shift_metres: float | None = None
    failing_areas: list[str] = field(default_factory=list)
    #: The areas equivalence was actually established for. ``None`` means
    #: "every shared area", which only same-vintage evidence may claim.
    established_areas: list[str] | None = None
    source: str = ""

    @classmethod
    def same_vintage(cls, level: str, boundary_release: str) -> "GeographyEvidence":
        return cls(
            kind="same_vintage", established=True, level=level,
            boundary_release_a=boundary_release, boundary_release_b=boundary_release,
            detail=(f"both releases use boundary vintage {boundary_release}, so areas "
                    "are the same by construction"),
            source="config/geography_equivalence.json",
        )

    @classmethod
    def none(cls, level: str, a: str, b: str, detail: str) -> "GeographyEvidence":
        return cls(kind="none", established=False, level=level,
                   boundary_release_a=a, boundary_release_b=b, detail=detail,
                   source="config/geography_equivalence.json")

    def applies_to(self, level: str, a: str, b: str) -> bool:
        """Evidence is specific to a level and a pair of vintages."""
        if self.level != level:
            return False
        return {self.boundary_release_a, self.boundary_release_b} == {a, b}

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "established": self.established,
            "level": self.level,
            "boundary_release_a": self.boundary_release_a,
            "boundary_release_b": self.boundary_release_b,
            "detail": self.detail,
            "areas_compared": self.areas_compared,
            "max_relative_area_difference": self.max_relative_area_difference,
            "max_centroid_shift_metres": self.max_centroid_shift_metres,
            "failing_area_count": len(self.failing_areas),
            "failing_areas": sorted(self.failing_areas)[:50],
            "established_area_count": (None if self.established_areas is None
                                       else len(self.established_areas)),
            "source": self.source,
        }


def _ring_metrics(ring: list, lat0: float) -> tuple[float, float, float]:
    """Signed planar area and area-weighted centroid of one ring, in metres."""
    mx = _METRES_PER_DEGREE_LON_EQUATOR * math.cos(math.radians(lat0))
    my = _METRES_PER_DEGREE_LAT
    area2 = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0] * mx, ring[i][1] * my
        x2, y2 = ring[i + 1][0] * mx, ring[i + 1][1] * my
        cross = x1 * y2 - x2 * y1
        area2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    area = area2 / 2.0
    if area == 0:
        return 0.0, 0.0, 0.0
    return area, cx / (3.0 * area2), cy / (3.0 * area2)


def geometry_metrics(geometry: dict[str, Any] | None) -> tuple[float, tuple[float, float]] | None:
    """Absolute area in square metres and the centroid, in metres."""
    if not geometry:
        return None
    rings: list[list] = []
    if geometry["type"] == "Polygon":
        rings = list(geometry["coordinates"])
    elif geometry["type"] == "MultiPolygon":
        for poly in geometry["coordinates"]:
            rings.extend(poly)
    else:
        return None
    if not rings or not rings[0]:
        return None
    lat0 = rings[0][0][1]

    total = 0.0
    wx = 0.0
    wy = 0.0
    for ring in rings:
        if len(ring) < 4:
            continue
        area, cx, cy = _ring_metrics(ring, lat0)
        total += area
        wx += cx * area
        wy += cy * area
    if total == 0:
        return None
    return abs(total), (wx / total, wy / total)


def geometry_equivalence(features_a: dict[str, dict], features_b: dict[str, dict],
                         level: str, boundary_release_a: str,
                         boundary_release_b: str,
                         tolerances: dict[str, float] | None = None
                         ) -> GeographyEvidence:
    """Compare two vintages' polygons for the areas they share.

    Only the shared identifiers are compared: areas present in one release
    alone are excluded from the comparison elsewhere and are not evidence of
    anything here.  The documented rule and its tolerances live in
    ``config/geography_equivalence.json``.
    """
    rule = equivalence_rule()
    tol = dict(rule["tolerances"])
    if tolerances:
        tol.update(tolerances)
    max_area = float(tol["max_relative_area_difference"])
    max_shift = float(tol["max_centroid_shift_metres"])

    shared = sorted(set(features_a) & set(features_b))
    if not shared:
        return GeographyEvidence.none(
            level, boundary_release_a, boundary_release_b,
            "the two releases share no areas at this level, so equivalence "
            "cannot be established")

    worst_area = 0.0
    worst_shift = 0.0
    failing: list[str] = []
    uncomparable: list[str] = []

    for geoid in shared:
        ma = geometry_metrics(features_a[geoid].get("geometry"))
        mb = geometry_metrics(features_b[geoid].get("geometry"))
        if ma is None or mb is None:
            uncomparable.append(geoid)
            continue
        area_a, (cax, cay) = ma
        area_b, (cbx, cby) = mb
        denom = max(area_a, area_b)
        rel = abs(area_a - area_b) / denom if denom else 0.0
        shift = math.hypot(cax - cbx, cay - cby)
        worst_area = max(worst_area, rel)
        worst_shift = max(worst_shift, shift)
        if rel > max_area or shift > max_shift:
            failing.append(geoid)

    if uncomparable:
        return GeographyEvidence(
            kind="computed_geometry", established=False, level=level,
            boundary_release_a=boundary_release_a,
            boundary_release_b=boundary_release_b,
            areas_compared=len(shared) - len(uncomparable),
            detail=(f"{len(uncomparable)} shared area(s) have no usable geometry in "
                    "one of the two releases, so equivalence cannot be computed"),
            failing_areas=uncomparable,
            source="computed from the cached cartographic boundary files",
        )

    established = not failing
    if established:
        detail = (
            f"all {len(shared)} shared {level} areas match between "
            f"{boundary_release_a} and {boundary_release_b} within the documented "
            f"tolerance (worst relative area difference {worst_area:.6f} of "
            f"{max_area}, worst centroid shift {worst_shift:.1f} m of {max_shift} m). "
            "Differences inside the tolerance are treated as cartographic "
            "generalisation, not as demographic change."
        )
    else:
        detail = (
            f"{len(failing)} of {len(shared)} shared {level} areas differ beyond the "
            f"documented tolerance between {boundary_release_a} and "
            f"{boundary_release_b} (worst relative area difference {worst_area:.6f}, "
            f"worst centroid shift {worst_shift:.1f} m). A shared identifier is not "
            "evidence that these are the same area; a reviewed equivalence record or "
            "a validated harmonisation is required."
        )

    return GeographyEvidence(
        kind="computed_geometry", established=established, level=level,
        boundary_release_a=boundary_release_a, boundary_release_b=boundary_release_b,
        detail=detail, areas_compared=len(shared),
        max_relative_area_difference=worst_area,
        max_centroid_shift_metres=worst_shift,
        failing_areas=failing,
        # Only the areas whose polygons were actually compared and matched. An
        # observation with no boundary in either vintage was not verified and
        # must not inherit the verdict.
        established_areas=sorted(set(shared) - set(failing)),
        source="computed from the cached cartographic boundary files",
    )


def reviewed_equivalence(level: str, boundary_release_a: str,
                         boundary_release_b: str) -> GeographyEvidence | None:
    """Look for a human-reviewed equivalence record covering this pair."""
    for record in equivalence_rule().get("reviewed_equivalences", []):
        pair = {record.get("boundary_release_a"), record.get("boundary_release_b")}
        if record.get("level") == level and pair == {boundary_release_a,
                                                     boundary_release_b}:
            return GeographyEvidence(
                kind="reviewed_record", established=bool(record.get("established")),
                level=level, boundary_release_a=boundary_release_a,
                boundary_release_b=boundary_release_b,
                detail=(f"reviewed by {record.get('reviewer', 'unnamed reviewer')} on "
                        f"{record.get('reviewed_on', 'an unrecorded date')}: "
                        f"{record.get('evidence', 'no evidence recorded')}"),
                source="config/geography_equivalence.json",
            )
    return None


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
