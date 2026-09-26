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
STATE_GEOID_LEN = 2
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

#: Metres per degree, adequate over a single city.
_METRES_PER_DEGREE_LAT = 110574.0
_METRES_PER_DEGREE_LON_EQUATOR = 111320.0

#: Evidence kinds, strongest first. Only the first three can establish that two
#: releases describe the same area.
SAME_VINTAGE = "same_vintage"
PROVIDER_CORRESPONDENCE = "provider_correspondence"
REVIEWED_RECORD = "reviewed_record"
FOOTPRINT_COMPARISON = "footprint_comparison"
NO_EVIDENCE = "none"


def equivalence_rule() -> dict[str, Any]:
    with open(EQUIVALENCE_CONFIG, "r", encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class GeographyEvidence:
    """Why two releases' areas may (or may not) be compared one to one.

    ``established`` is the only field that decides anything, and a computed
    measurement never sets it.  Measuring that two published polygons look
    alike is not the same as knowing the Census Bureau defines them as the
    same area, so the measurement is reported and the decision waits for the
    provider's own correspondence or a scoped human review.
    """

    kind: str
    established: bool
    level: str
    boundary_release_a: str
    boundary_release_b: str
    detail: str
    areas_compared: int = 0
    measurements: dict[str, Any] = field(default_factory=dict)
    failing_areas: list[str] = field(default_factory=list)
    #: The areas equivalence was established for. ``None`` means "every shared
    #: area", which only same-vintage evidence may claim.
    established_areas: list[str] | None = None
    source: str = ""

    @classmethod
    def same_vintage(cls, level: str, boundary_release: str) -> "GeographyEvidence":
        return cls(
            kind=SAME_VINTAGE, established=True, level=level,
            boundary_release_a=boundary_release, boundary_release_b=boundary_release,
            detail=(f"both releases use boundary vintage {boundary_release}, so the "
                    "areas are the same by construction"),
            source="config/geography_equivalence.json")

    @classmethod
    def none(cls, level: str, a: str, b: str, detail: str) -> "GeographyEvidence":
        return cls(kind=NO_EVIDENCE, established=False, level=level,
                   boundary_release_a=a, boundary_release_b=b, detail=detail,
                   source="config/geography_equivalence.json")

    def applies_to(self, level: str, a: str, b: str) -> bool:
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
            "measurements": self.measurements,
            "failing_area_count": len(self.failing_areas),
            "failing_areas": sorted(self.failing_areas)[:50],
            "established_area_count": (None if self.established_areas is None
                                       else len(self.established_areas)),
            "source": self.source,
        }


# -- geometry helpers -------------------------------------------------------

def _scales(lat0: float) -> tuple[float, float]:
    return (_METRES_PER_DEGREE_LON_EQUATOR * math.cos(math.radians(lat0)),
            _METRES_PER_DEGREE_LAT)


def rings_of(geometry: dict[str, Any] | None) -> list[list]:
    if not geometry:
        return []
    if geometry["type"] == "Polygon":
        return [r for r in geometry["coordinates"] if len(r) >= 4]
    if geometry["type"] == "MultiPolygon":
        return [r for poly in geometry["coordinates"] for r in poly if len(r) >= 4]
    return []


def geometry_metrics(geometry: dict[str, Any] | None,
                     lat0: float | None = None
                     ) -> tuple[float, tuple[float, float]] | None:
    """Absolute area in square metres and the centroid, in metres.

    ``lat0`` fixes the projection origin. Two geometries being compared must
    use the same one, or the comparison measures the projection rather than
    the shapes.
    """
    rings = rings_of(geometry)
    if not rings:
        return None
    if lat0 is None:
        lat0 = rings[0][0][1]
    mx, my = _scales(lat0)

    total = 0.0
    wx = 0.0
    wy = 0.0
    for ring in rings:
        area2 = cx = cy = 0.0
        for i in range(len(ring) - 1):
            x1, y1 = ring[i][0] * mx, ring[i][1] * my
            x2, y2 = ring[i + 1][0] * mx, ring[i + 1][1] * my
            cross = x1 * y2 - x2 * y1
            area2 += cross
            cx += (x1 + x2) * cross
            cy += (y1 + y2) * cross
        if area2 == 0:
            continue
        area = area2 / 2.0
        total += area
        wx += (cx / (3.0 * area2)) * area
        wy += (cy / (3.0 * area2)) * area
    if total == 0:
        return None
    return abs(total), (wx / total, wy / total)


def _point_segment_distance_sq(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    qx, qy = ax + t * dx, ay + t * dy
    return (px - qx) ** 2 + (py - qy) ** 2


def _directed_boundary_distance(vertices, segments, cutoff_sq: float) -> float:
    """Greatest distance from any vertex of one boundary to the other boundary.

    Stops early per vertex once a segment is within the cutoff, which is the
    common case for two renderings of the same boundary, and stops entirely
    once the running maximum has already exceeded the cutoff.
    """
    worst = 0.0
    for px, py in vertices:
        best = float("inf")
        for ax, ay, bx, by, minx, maxx, miny, maxy in segments:
            if best < cutoff_sq:
                break
            # Cheap rejection before the projection arithmetic.
            if px < minx - best ** 0.5 or px > maxx + best ** 0.5:
                if py < miny - best ** 0.5 or py > maxy + best ** 0.5:
                    continue
            d = _point_segment_distance_sq(px, py, ax, ay, bx, by)
            if d < best:
                best = d
        if best > worst:
            worst = best
            if worst > cutoff_sq * 4:
                # Already far outside any tolerance; the exact value adds
                # nothing to the decision.
                return math.sqrt(worst)
    return math.sqrt(worst)


def _projected(rings, mx, my):
    verts = []
    segs = []
    for ring in rings:
        pts = [(x * mx, y * my) for x, y in ring]
        verts.extend(pts)
        for i in range(len(pts) - 1):
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            segs.append((ax, ay, bx, by, min(ax, bx), max(ax, bx),
                         min(ay, by), max(ay, by)))
    return verts, segs


def footprint_disagreement(geom_a: dict | None, geom_b: dict | None,
                           cutoff_metres: float) -> dict[str, Any] | None:
    """Measure how far two published footprints actually differ.

    Area and centroid are reported, but they cannot decide anything on their
    own: a square and an equal-area diamond about the same point agree on both
    while describing different ground. The figure that reflects the footprint
    is ``boundary_distance_metres``, the greatest distance from either
    boundary's vertices to the other boundary.
    """
    rings_a, rings_b = rings_of(geom_a), rings_of(geom_b)
    if not rings_a or not rings_b:
        return None
    lat0 = rings_a[0][0][1]
    mx, my = _scales(lat0)

    ma = geometry_metrics(geom_a, lat0)
    mb = geometry_metrics(geom_b, lat0)
    if ma is None or mb is None:
        return None
    area_a, (cax, cay) = ma
    area_b, (cbx, cby) = mb

    verts_a, segs_a = _projected(rings_a, mx, my)
    verts_b, segs_b = _projected(rings_b, mx, my)
    cutoff_sq = cutoff_metres ** 2
    forward = _directed_boundary_distance(verts_a, segs_b, cutoff_sq)
    backward = _directed_boundary_distance(verts_b, segs_a, cutoff_sq)

    denom = max(area_a, area_b)
    return {
        "relative_area_difference": abs(area_a - area_b) / denom if denom else 0.0,
        "centroid_shift_metres": math.hypot(cax - cbx, cay - cby),
        "boundary_distance_metres": max(forward, backward),
        "vertices_a": len(verts_a),
        "vertices_b": len(verts_b),
    }


def footprint_comparison(features_a: dict[str, dict], features_b: dict[str, dict],
                         level: str, boundary_release_a: str,
                         boundary_release_b: str,
                         tolerances: dict[str, float] | None = None
                         ) -> GeographyEvidence:
    """Compare the published footprints of the areas two vintages share.

    This never returns established evidence. It is a measurement: it can show
    that two renderings disagree, which is decisive, but agreeing within a
    tolerance does not by itself establish that the provider defines them as
    the same area. That still needs the provider's own correspondence or a
    scoped human review.
    """
    rule = equivalence_rule()
    tol = dict(rule["footprint_comparison"]["tolerances"])
    if tolerances:
        tol.update(tolerances)
    max_area = float(tol["max_relative_area_difference"])
    max_boundary = float(tol["max_boundary_distance_metres"])

    shared = sorted(set(features_a) & set(features_b))
    if not shared:
        return GeographyEvidence.none(
            level, boundary_release_a, boundary_release_b,
            "the two releases share no areas at this level, so there is nothing "
            "to compare")

    worst = {"relative_area_difference": 0.0, "centroid_shift_metres": 0.0,
             "boundary_distance_metres": 0.0}
    failing: list[str] = []
    agreeing: list[str] = []
    uncomparable: list[str] = []

    for geoid in shared:
        m = footprint_disagreement(features_a[geoid].get("geometry"),
                                   features_b[geoid].get("geometry"), max_boundary)
        if m is None:
            uncomparable.append(geoid)
            continue
        for key in worst:
            worst[key] = max(worst[key], m[key])
        if (m["relative_area_difference"] > max_area
                or m["boundary_distance_metres"] > max_boundary):
            failing.append(geoid)
        else:
            agreeing.append(geoid)

    detail = (
        f"Footprint comparison of {len(agreeing) + len(failing)} shared {level} "
        f"area(s) between {boundary_release_a} and {boundary_release_b}: "
        f"{len(agreeing)} agree within the measurement tolerance "
        f"(relative area {max_area}, boundary distance {max_boundary} m) and "
        f"{len(failing)} do not. Worst observed: relative area "
        f"{worst['relative_area_difference']:.6f}, boundary distance "
        f"{worst['boundary_distance_metres']:.1f} m, centroid shift "
        f"{worst['centroid_shift_metres']:.1f} m."
        + (f" {len(uncomparable)} area(s) had no usable geometry."
           if uncomparable else "")
        + " This is a measurement, not an establishment of equivalence: two "
          "renderings agreeing within a tolerance does not show that the "
          "provider defines them as the same area. Record documented provider "
          "correspondence or a scoped review in "
          "config/geography_equivalence.json to allow the comparison."
    )

    return GeographyEvidence(
        kind=FOOTPRINT_COMPARISON,
        established=False,
        level=level,
        boundary_release_a=boundary_release_a,
        boundary_release_b=boundary_release_b,
        detail=detail,
        areas_compared=len(agreeing) + len(failing),
        measurements={
            "worst": worst,
            "tolerances": {"max_relative_area_difference": max_area,
                           "max_boundary_distance_metres": max_boundary},
            "agreeing_area_count": len(agreeing),
            "disagreeing_area_count": len(failing),
            "uncomparable_area_count": len(uncomparable),
            "method": ("greatest distance from either boundary's vertices to the "
                       "other boundary, computed on the published cartographic "
                       "geometry with a shared projection origin"),
            "limitation": ("vertex-to-boundary distance is a lower bound on the "
                           "true Hausdorff distance between the boundaries; it is "
                           "close for densely sampled published polygons"),
        },
        failing_areas=failing,
        established_areas=[],
        source="computed from the cached cartographic boundary files",
    )


def recorded_equivalence(level: str, boundary_release_a: str,
                         boundary_release_b: str) -> GeographyEvidence | None:
    """Documented provider correspondence, or a scoped human review.

    These are the only computed-free routes to establishing equivalence, and
    the repository ships none of them: a record has to be added deliberately,
    citing what it rests on.
    """
    rule = equivalence_rule()
    for key, kind in (("provider_correspondence", PROVIDER_CORRESPONDENCE),
                      ("reviewed_equivalences", REVIEWED_RECORD)):
        for record in rule.get(key, []):
            pair = {record.get("boundary_release_a"), record.get("boundary_release_b")}
            if record.get("level") != level or pair != {boundary_release_a,
                                                        boundary_release_b}:
                continue
            areas = record.get("established_areas")
            return GeographyEvidence(
                kind=kind, established=bool(record.get("established")),
                level=level, boundary_release_a=boundary_release_a,
                boundary_release_b=boundary_release_b,
                detail=(f"{record.get('summary', 'no summary recorded')} "
                        f"[source: {record.get('source', 'none recorded')}; "
                        f"recorded by {record.get('recorded_by', 'unnamed')} on "
                        f"{record.get('recorded_on', 'an unrecorded date')}]"),
                established_areas=(None if areas in (None, "all") else list(areas)),
                source="config/geography_equivalence.json")
    return None


# ---------------------------------------------------------------------------
# Identifiers and joins
# ---------------------------------------------------------------------------

def split_geo_id(geo_id: str) -> tuple[str, str]:
    """Split a full ``GEO_ID`` into (summary level, GEOID string)."""
    m = _GEO_ID_RE.match(geo_id.strip())
    if not m:
        raise GeographyError(f"unrecognised GEO_ID {geo_id!r}")
    return m.group(1), m.group(2)


def level_for_geoid(geoid: str) -> str:
    if len(geoid) == STATE_GEOID_LEN:
        return "state"
    if len(geoid) == COUNTY_GEOID_LEN:
        return "county"
    if len(geoid) == TRACT_GEOID_LEN:
        return "tract"
    raise GeographyError(
        f"GEOID {geoid!r} is not a state, county or tract identifier")


def validate_geoid(geoid: Any, level: str | None = None) -> str:
    if not isinstance(geoid, str):
        raise GeographyError(
            f"GEOID must be a string, got {type(geoid).__name__} ({geoid!r}). "
            "Numeric GEOIDs lose leading zeroes."
        )
    if not GEOID_RE.match(geoid):
        raise GeographyError(f"GEOID {geoid!r} is not a digit string")
    expected = {"state": STATE_GEOID_LEN, "county": COUNTY_GEOID_LEN,
                "tract": TRACT_GEOID_LEN}
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
    membership_checked = 0
    disagreements: list[str] = []
    for feat in features:
        geoid = _feature_geoid(feat.properties, level)
        if geoid is None or geoid not in keep_geoids:
            continue
        validate_geoid(geoid, level)
        # A tract's county is read from its GEOID everywhere else in this
        # project. The boundary file states it independently, in STATEFP and
        # COUNTYFP, so the two are compared rather than one being assumed.
        if level == "tract":
            state_fp = feat.properties.get("STATEFP")
            county_fp = feat.properties.get("COUNTYFP")
            if state_fp and county_fp:
                membership_checked += 1
                if geoid[:5] != f"{state_fp}{county_fp}":
                    disagreements.append(geoid)
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
    if disagreements:
        raise GeographyError(
            f"{len(disagreements)} tract(s) whose GEOID does not begin with the "
            f"STATEFP and COUNTYFP the boundary file gives them: "
            f"{', '.join(disagreements[:5])}. County membership cannot be "
            "read from the GEOID for this file.")
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
            "county_membership_checked": membership_checked,
            "county_membership_rule": (
                "a tract belongs to the county named by the first five digits "
                "of its GEOID; checked against the boundary file's own STATEFP "
                "and COUNTYFP for every tract that carries them"),
        },
        "features": out_features,
    }
    return collection, geoids


def write_geojson(path: Path, collection: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")
