"""A compatible reference value to read a place against.

The only honest way to build one is from the underlying counts: sum the
numerators, sum the denominators, then divide. Averaging the places'
percentages would weight a tract of 900 people the same as a borough of
2.6 million, and averaging medians is worse still.

Everything here stays inside one release, so no boundary-vintage question
arises. Where a correct answer would need data this build does not have, the
benchmark is disabled with the reason rather than approximated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .config import MeasureDef
from .measures import Z_90

#: The published aggregation approximation degrades as the number of
#: components grows. Beyond this many the sum itself is still arithmetically
#: exact, but the margin of error is reported as unavailable rather than
#: overstated. Arithmetic exactness is not accuracy: every component is a
#: sample-based estimate and carries its own sampling error.
MAX_COMPONENTS_FOR_MOE = 12

NYC = "nyc"
#: The state's own published row. Offered only when the build covers the state.
NYS = "nys"
#: The published row of the one county a set of tracts sits in. Called a
#: borough in words only when the county is one of New York City's five.
CONTAINING_COUNTY = "containing_county"
#: The identifier this reference had before the build covered the state. Still
#: accepted, so a link or saved project made then keeps working.
CONTAINING_BOROUGH = "containing_borough"
SELECTED = "selected"
NONE = "none"


@dataclass
class Benchmark:
    benchmark_id: str
    label: str
    basis: str                      # how it was built, in words
    unit: str
    estimate: float | None = None
    estimate_status: str = "ok"
    estimate_reason: str | None = None
    moe: float | None = None
    moe_status: str = "ok"
    moe_reason: str | None = None
    numerator: float | None = None
    denominator: float | None = None
    component_count: int = 0
    components: list[str] = field(default_factory=list)
    #: Every component was controlled to an independent population estimate, so
    #: the combined figure has no sampling error. "± 0" would read as a
    #: suspiciously precise measurement rather than as what it is.
    controlled: bool = False
    available: bool = True
    unavailable_reason: str = ""

    def to_json(self) -> dict:
        return {
            "benchmark_id": self.benchmark_id, "label": self.label,
            "basis": self.basis, "unit": self.unit,
            "estimate": self.estimate, "estimate_status": self.estimate_status,
            "estimate_reason": self.estimate_reason,
            "moe": self.moe, "moe_status": self.moe_status,
            "moe_reason": self.moe_reason,
            "numerator": self.numerator, "denominator": self.denominator,
            "component_count": self.component_count,
            "controlled": self.controlled,
            "components": self.components[:20],
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
        }


def unavailable(benchmark_id: str, label: str, reason: str, unit: str) -> Benchmark:
    return Benchmark(benchmark_id=benchmark_id, label=label, basis="", unit=unit,
                     available=False, unavailable_reason=reason,
                     estimate_status="unavailable", estimate_reason=reason,
                     moe_status="unavailable", moe_reason=reason)


def _sum_component(values: dict[str, dict], geoids: list[str], key: str
                   ) -> tuple[float | None, str | None]:
    """Sum one stored count field across components, or explain why not."""
    total = 0.0
    for geoid in geoids:
        v = values.get(geoid)
        if not v or v.get("es") != "ok" or v.get(key) is None:
            return None, (f"{geoid} has no usable value for this measure, so the "
                          "components cannot be added")
        total += float(v[key])
    return total, None


def aggregate(measure: MeasureDef, values: dict[str, dict], geoids: list[str],
              benchmark_id: str, label: str, basis: str,
              required_members: list[str] | None = None) -> Benchmark:
    """Build a benchmark by adding the underlying counts.

    Percentages are never added or averaged. For a share, the stored numerator
    and denominator counts are summed and the share is recomputed from the
    sums; for a count, the counts are summed.

    ``required_members`` is the documented membership of a named group. A
    reference that carries a place's name must be that whole place: if any
    member is missing, or an area outside the membership is included, the
    reference is refused rather than quietly rebased on whatever happens to be
    available. "New York City" computed from one borough is not a smaller New
    York City; it is wrong.
    """
    bench = Benchmark(benchmark_id=benchmark_id, label=label, basis=basis,
                      unit=measure.unit, component_count=len(geoids),
                      components=list(geoids))
    if not geoids:
        return unavailable(benchmark_id, label,
                           "no areas were selected to build this reference from",
                           measure.unit)

    if required_members is not None:
        required = set(required_members)
        present = set(geoids)
        missing = sorted(required - present)
        extra = sorted(present - required)
        if missing or extra:
            parts = []
            if missing:
                parts.append(
                    f"{len(missing)} of its {len(required)} documented member "
                    f"areas are not available: {', '.join(missing)}")
            if extra:
                parts.append(
                    f"{len(extra)} area(s) outside its documented membership were "
                    f"included: {', '.join(extra)}")
            return unavailable(
                benchmark_id, label,
                f"{label} cannot be built from what is available. "
                + "; ".join(parts)
                + ". A reference that carries a place's name must cover the whole "
                  "place, so no partial figure is shown.",
                measure.unit)

    if measure.kind == "count":
        total, problem = _sum_component(values, geoids, "e")
        if total is None:
            bench.estimate_status = "unavailable"
            bench.estimate_reason = problem
            bench.moe_status = "unavailable"
            bench.moe_reason = problem
            bench.available = False
            bench.unavailable_reason = problem
            return bench
        bench.estimate = total
        bench.numerator = total
    else:
        num, problem = _sum_component(values, geoids, "n")
        den, problem2 = _sum_component(values, geoids, "d")
        problem = problem or problem2
        if num is None or den is None:
            bench.estimate_status = "unavailable"
            bench.estimate_reason = (
                problem or "the underlying counts are not stored for this measure, "
                "so a reference cannot be built by adding them. Averaging the "
                "places' percentages would not be the same quantity.")
            bench.available = False
            bench.unavailable_reason = bench.estimate_reason
            bench.moe_status = "unavailable"
            bench.moe_reason = bench.estimate_reason
            return bench
        if den == 0:
            bench.estimate_status = "unavailable"
            bench.estimate_reason = ("the combined denominator is zero, so a share "
                                     "is undefined, not zero")
            bench.moe_status = "unavailable"
            bench.moe_reason = bench.estimate_reason
            bench.available = False
            bench.unavailable_reason = bench.estimate_reason
            return bench
        bench.numerator = num
        bench.denominator = den
        bench.estimate = num / den * 100.0

    if len(geoids) > MAX_COMPONENTS_FOR_MOE:
        bench.moe_status = "unavailable"
        bench.moe_reason = (
            f"this reference adds {len(geoids)} areas. The published approximation "
            f"for combining margins of error degrades as components are added, so "
            f"beyond {MAX_COMPONENTS_FOR_MOE} this build reports the combined "
            "estimate without a margin of error rather than an overstated one.")
        return bench

    variance = 0.0
    controlled_components = 0
    measured_components = 0
    for geoid in geoids:
        v = values.get(geoid) or {}
        if v.get("ms") != "ok" or v.get("m") is None:
            bench.moe_status = "unavailable"
            bench.moe_reason = (
                f"{geoid} has no usable margin of error, so the combined margin of "
                "error cannot be computed. Missing uncertainty is unavailable, not "
                "zero.")
            return bench
        # For a share the stored margin of error is on the percentage, which
        # cannot be combined. Only counts combine.
        if measure.kind != "count":
            bench.moe_status = "unavailable"
            bench.moe_reason = (
                "combining margins of error for a share across areas needs the "
                "margins of error of the underlying counts, which this build does "
                "not store separately. The sum itself is arithmetically exact, "
                "but it is built from sample-based estimates and carries their "
                "sampling error; that error is simply not quantified here.")
            return bench
        variance += float(v["m"]) ** 2
        # Explicit provenance only. A flag mentioning control may have come
        # from one cell of one component and says nothing about the total.
        if v.get("ctl") is True:
            controlled_components += 1
        else:
            measured_components += 1
    bench.moe = math.sqrt(variance)
    if (controlled_components == len(geoids) and measured_components == 0
            and bench.moe == 0):
        bench.controlled = True
        bench.moe_reason = (
            "every component is controlled to an independent population estimate, "
            "so the combined figure carries no sampling error. This is not a "
            "measured margin of error of zero.")
    return bench


def published(measure: MeasureDef, values: dict[str, dict], geoid: str,
              benchmark_id: str, label: str, basis: str) -> Benchmark:
    """One area's own figure, read as it is, used as a reference.

    Nothing is added up. The estimate and margin of error are the ones this
    build already shows for that area in the table: a published count and its
    published margin of error, or a share computed from its published counts
    by the same reviewed rules as every other share. When the area's margin of
    error is unavailable, so is the reference's; it is never taken as zero.
    """
    v = values.get(geoid) or {}
    if v.get("es") != "ok" or v.get("e") is None:
        reason = (v.get("er") or
                  f"{geoid} has no published value for this measure in this build")
        return unavailable(benchmark_id, label, reason, measure.unit)
    bench = Benchmark(benchmark_id=benchmark_id, label=label, basis=basis,
                      unit=measure.unit, estimate=float(v["e"]),
                      numerator=v.get("n"), denominator=v.get("d"),
                      component_count=1, components=[geoid])
    if measure.kind == "count":
        bench.numerator = float(v["e"])
    if v.get("ms") != "ok" or v.get("m") is None:
        bench.moe_status = "unavailable"
        bench.moe_reason = (
            (v.get("mr") or f"{geoid} has no usable margin of error for this measure")
            + ". Missing uncertainty is unavailable, not zero.")
        return bench
    bench.moe = float(v["m"])
    if v.get("ctl") is True and bench.moe == 0:
        bench.controlled = True
        bench.moe_reason = (
            "this figure is controlled to an independent population estimate, so "
            "it carries no sampling error. This is not a measured margin of error "
            "of zero.")
    return bench


def cv_percent(bench: Benchmark) -> float | None:
    if bench.unit != "persons" or bench.moe_status != "ok" or not bench.estimate:
        return None
    return (bench.moe / Z_90) / abs(bench.estimate) * 100.0


def options(level: str, selected_areas: list[str], all_counties: list[str],
            county_names: dict[str, str], *, scope: str = "nyc",
            statewide: bool = False, boroughs: list[str] | None = None
            ) -> list[dict]:
    """The references that make sense for this selection, in words.

    Only references this selection can actually be read against are listed:
    the containing county is offered when every place in view sits in one
    county, not as a promise that the build then refuses.
    """
    boroughs = list(boroughs if boroughs is not None else all_counties)
    out = [{"benchmark_id": NONE, "label": "No reference",
            "description": "Show the selected places on their own."}]
    out.append({
        "benchmark_id": NYC,
        "label": "New York City (all five boroughs)",
        "description": ("Built by adding the five boroughs' underlying counts and "
                        "recomputing the measure from the totals. New York City is "
                        "exactly these five counties, whatever else is in view."),
    })
    if statewide:
        out.append({
            "benchmark_id": NYS,
            "label": "New York State (published state figure)",
            "description": ("The state's own row in the same release, read as "
                            "published. It is not built by adding or averaging "
                            "counties."),
        })
    if level == "tract":
        county = None
        if scope.startswith("county:"):
            county = scope.split(":", 1)[1]
        else:
            counties = sorted({g[:5] for g in selected_areas})
            if len(counties) == 1:
                county = counties[0]
        if county:
            name = county_names.get(county, county)
            word = "borough" if county in boroughs else "county"
            out.append({
                "benchmark_id": CONTAINING_COUNTY,
                "county": county,
                "label": f"The containing {word} ({name})",
                "description": (f"Each tract is read against the published figure "
                                f"for the {word} it sits in."),
            })
    if len(selected_areas) > 1:
        out.append({
            "benchmark_id": SELECTED,
            "label": "The selected places combined",
            "description": ("Built by adding the selected places' underlying counts "
                            "and recomputing the measure from the totals."),
        })
    return out
