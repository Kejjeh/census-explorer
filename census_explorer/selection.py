"""One validated selection, used by every output.

The CSV, the map, the chart, the saved project and the provenance document
must describe the same thing. They do here because they are all built from a
single :class:`Selection`, which is resolved and validated once: the releases,
the geography level, the measures, the exact areas, and the class breaks.

Resolution is where the promises are kept. If a comparison is requested, the
area set is narrowed to the areas the comparison is actually allowed to use,
so "areas present in one release only are excluded" is true of the data, the
figure and the export rather than only of the sentence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import MeasureDef, Release

#: A chart with one row per area stops being readable long before a city's
#: worth of tracts. When the selection exceeds this, the chart shows the
#: highest-ranked rows and says so, on screen and in the exported figure.
DEFAULT_MAX_CHART_ROWS = 25


class SelectionError(ValueError):
    pass


@dataclass
class Selection:
    """What to show, resolved and checked once."""

    releases: list[Release]
    measures: list[MeasureDef]
    level: str
    areas: list[str]                       # exactly the areas to be used
    area_names: dict[str, str] = field(default_factory=dict)
    classes: int = 5
    cut_points: list[float] | None = None
    requested_areas: list[str] = field(default_factory=list)
    excluded_areas: list[str] = field(default_factory=list)
    exclusion_reason: str = ""
    max_chart_rows: int = DEFAULT_MAX_CHART_ROWS
    compatibility: dict[str, Any] | None = None
    #: What the map, table, CSV, brief and share link all cover, as a code
    #: ("nyc", "nys", "county:36029") and in words with its size.
    scope: str = ""
    scope_label: str = ""

    @property
    def primary_release(self) -> Release:
        return self.releases[0]

    @property
    def comparison_release(self) -> Release | None:
        return self.releases[1] if len(self.releases) > 1 else None

    @property
    def primary_measure(self) -> MeasureDef:
        return self.measures[0]

    @property
    def is_comparison(self) -> bool:
        return len(self.releases) > 1

    def chart_rows(self, values: dict[str, dict]) -> tuple[list[str], str]:
        """The areas a chart will draw, and a note if it had to limit them."""
        if len(self.areas) <= self.max_chart_rows:
            return list(self.areas), ""
        ranked = sorted(
            (g for g in self.areas if (values.get(g) or {}).get("es") == "ok"),
            key=lambda g: values[g]["e"], reverse=True,
        )
        kept = ranked[: self.max_chart_rows]
        note = (f"Showing the {len(kept)} highest of {len(self.areas)} selected "
                f"{self.level} areas, ranked by this measure. The exported CSV "
                "contains every selected area.")
        return kept, note

    def to_json(self) -> dict:
        return {
            "release_ids": [r.release_id for r in self.releases],
            "period_labels": [r.period_label for r in self.releases],
            "measure_ids": [m.measure_id for m in self.measures],
            "level": self.level,
            "areas": list(self.areas),
            "area_count": len(self.areas),
            "requested_areas": list(self.requested_areas),
            "excluded_areas": list(self.excluded_areas),
            "exclusion_reason": self.exclusion_reason,
            "classes": self.classes,
            "cut_points": self.cut_points,
            "max_chart_rows": self.max_chart_rows,
            "scope": self.scope,
            "scope_label": self.scope_label,
        }


def resolve(*, releases: list[Release], measures: list[MeasureDef], level: str,
            areas_by_release: dict[str, set[str]],
            area_names: dict[str, str],
            requested_areas: list[str] | None = None,
            classes: int = 5,
            cut_points: list[float] | None = None,
            comparable_geoids: list[str] | None = None,
            compatibility: dict[str, Any] | None = None,
            max_chart_rows: int = DEFAULT_MAX_CHART_ROWS) -> Selection:
    """Validate a request and narrow it to what may actually be shown."""
    if not releases:
        raise SelectionError("a selection needs at least one release")
    if not measures:
        raise SelectionError("a selection needs at least one measure")
    if level not in ("county", "tract"):
        raise SelectionError(f"unsupported geography level {level!r}")

    available = set(areas_by_release[releases[0].release_id])
    for r in releases[1:]:
        available &= set(areas_by_release[r.release_id])

    reason = ""
    if len(releases) > 1:
        if comparable_geoids is None:
            raise SelectionError(
                "a comparison selection must be given the areas the comparison "
                "is allowed to use")
        allowed = available & set(comparable_geoids)
        if allowed != available:
            reason = ("areas without established equivalence across the two "
                      "boundary vintages were excluded")
        available = allowed
        if not reason:
            reason = ("areas present in only one of the two releases were excluded "
                      "from the comparison")

    requested = [str(a) for a in (requested_areas or [])]
    if requested:
        chosen = [a for a in requested if a in available]
        missing = [a for a in requested if a not in available]
        if not chosen:
            raise SelectionError(
                f"none of the requested areas is available at {level} level for "
                f"{', '.join(r.release_id for r in releases)}: {missing}")
        excluded = missing
        if missing and not reason:
            reason = "requested areas that are not in this selection were excluded"
    else:
        chosen = sorted(available)
        excluded = sorted(set().union(*(areas_by_release[r.release_id]
                                        for r in releases)) - available)

    return Selection(
        releases=list(releases),
        measures=list(measures),
        level=level,
        areas=sorted(chosen),
        area_names={g: area_names.get(g, g) for g in chosen},
        classes=classes,
        cut_points=cut_points,
        requested_areas=requested,
        excluded_areas=sorted(excluded),
        exclusion_reason=reason,
        max_chart_rows=max_chart_rows,
        compatibility=compatibility,
    )
