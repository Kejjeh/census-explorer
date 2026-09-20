"""Rules for comparing two releases.

A comparison is only meaningful when the survey product, the measure's
definition and universe, and the geography all line up.  This module decides
that explicitly and refuses rather than guessing.  Where a comparison is
allowed but needs a caveat — most importantly, that adjacent five-year ACS
periods share years and are therefore not independent observations — the
caveat is returned as a disclosure that the interface must display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Release
from .metadata import ReleaseMetadata

BLOCK = "blocking"
DISCLOSE = "disclosure"


@dataclass
class CompatibilityReport:
    release_a: str
    release_b: str
    level: str
    measure_id: str | None = None
    blocking: list[str] = field(default_factory=list)
    disclosures: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    shared_geoids: int = 0
    only_in_a: list[str] = field(default_factory=list)
    only_in_b: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return not self.blocking

    @property
    def independent_observations(self) -> bool:
        return not any("not independent" in d for d in self.disclosures)

    def to_json(self) -> dict[str, Any]:
        return {
            "release_a": self.release_a,
            "release_b": self.release_b,
            "level": self.level,
            "measure_id": self.measure_id,
            "allowed": self.allowed,
            "independent_observations": self.independent_observations,
            "blocking": self.blocking,
            "disclosures": self.disclosures,
            "checks": self.checks,
            "shared_geoids": self.shared_geoids,
            "only_in_a_count": len(self.only_in_a),
            "only_in_b_count": len(self.only_in_b),
            "only_in_a": sorted(self.only_in_a)[:50],
            "only_in_b": sorted(self.only_in_b)[:50],
        }


def periods_overlap(a: Release, b: Release) -> bool:
    return a.period_start <= b.period_end and b.period_start <= a.period_end


def overlap_years(a: Release, b: Release) -> list[int]:
    return sorted(set(range(a.period_start, a.period_end + 1))
                  & set(range(b.period_start, b.period_end + 1)))


def check(a: Release, b: Release, level: str,
          geoids_a: set[str], geoids_b: set[str],
          meta_a: ReleaseMetadata | None = None,
          meta_b: ReleaseMetadata | None = None,
          measure=None) -> CompatibilityReport:
    """Decide whether two releases may be compared for one measure and level."""
    r = CompatibilityReport(a.release_id, b.release_id, level,
                            measure.measure_id if measure else None)

    def record(name: str, ok: bool, detail: str, severity: str = BLOCK,
               ok_detail: str = "check passed") -> None:
        r.checks.append({"check": name, "passed": ok,
                         "detail": ok_detail if ok else detail,
                         "severity": "info" if ok else severity})
        if not ok:
            (r.blocking if severity == BLOCK else r.disclosures).append(detail)

    # 1. Same survey product.
    record("same_dataset", a.dataset == b.dataset,
           f"different survey products: {a.dataset} and {b.dataset}",
           ok_detail=f"both sides are {a.dataset}")
    record("same_period_length", a.period_years == b.period_years,
           f"{a.period_label} covers {a.period_years} year(s) and {b.period_label} "
           f"covers {b.period_years}. ACS one-year and multi-year estimates are "
           "different products and must not be spliced into one series.",
           ok_detail=f"both sides are {a.period_years}-year period estimates")

    # 2. Identical release is not a comparison.
    record("distinct_releases", a.release_id != b.release_id,
           "the two sides of a comparison must be different releases",
           ok_detail=f"{a.release_id} vs {b.release_id}")

    # 3. Period overlap is a disclosure, not a block.
    shared = overlap_years(a, b)
    if shared and a.release_id != b.release_id:
        record(
            "independent_periods", False,
            f"{a.period_label} and {b.period_label} share the year(s) "
            f"{', '.join(str(y) for y in shared)}. Overlapping multi-year estimates "
            "are not independent observations: a difference between them must not be "
            "presented as a statistically tested change, and the shared years must be "
            "stated wherever the two are shown together.",
            severity=DISCLOSE,
        )
    else:
        r.checks.append({"check": "independent_periods", "passed": True,
                         "detail": "periods do not overlap", "severity": "info"})

    # 4. Measure definition and universe must match in both releases.
    if measure is not None and meta_a is not None and meta_b is not None:
        cells = list(measure.numerator_cells) + list(measure.denominator_cells)
        missing_a = [c for c in cells if not meta_a.has_cell(c)]
        missing_b = [c for c in cells if not meta_b.has_cell(c)]
        record("cells_present_in_both", not (missing_a or missing_b),
               f"measure cells missing from a release: {a.release_id}={missing_a}, "
               f"{b.release_id}={missing_b}",
               ok_detail=f"all {len(cells)} cell(s) exist in both releases")
        if not (missing_a or missing_b):
            ua = {meta_a.cell(c).universe for c in cells}
            ub = {meta_b.cell(c).universe for c in cells}
            record("same_universe", ua == ub,
                   f"universe changed between releases: {sorted(ua)} vs {sorted(ub)}",
                   ok_detail=f"same published universe: {sorted(ua)[0] if ua else 'n/a'}")
            la = {c: meta_a.cell(c).label for c in cells}
            lb = {c: meta_b.cell(c).label for c in cells}
            changed = {c: (la[c], lb[c]) for c in cells if la[c] != lb[c]}
            record("same_cell_labels", not changed,
                   "published cell labels changed between releases: "
                   + "; ".join(f"{c}: {old!r} -> {new!r}" for c, (old, new) in changed.items()),
                   severity=DISCLOSE,
                   ok_detail="published cell labels are identical in both releases")

    # 5. Geography.
    r.shared_geoids = len(geoids_a & geoids_b)
    r.only_in_a = sorted(geoids_a - geoids_b)
    r.only_in_b = sorted(geoids_b - geoids_a)
    if a.boundary_release != b.boundary_release:
        r.checks.append({
            "check": "boundary_vintage", "passed": True,
            "detail": (f"geography vintages differ ({a.boundary_release} vs "
                       f"{b.boundary_release}); the comparison is restricted to areas "
                       "whose identifiers appear in both releases"),
            "severity": "info",
        })
    record("geography_ids_match", not (r.only_in_a or r.only_in_b),
           f"{len(r.only_in_a)} area(s) appear only in {a.release_id} and "
           f"{len(r.only_in_b)} only in {b.release_id}. Areas present in one release "
           "only are excluded from the comparison and reported here.",
           severity=DISCLOSE)
    record("any_shared_geography", r.shared_geoids > 0,
           "the two releases share no geographic identifiers at this level",
           ok_detail=f"{r.shared_geoids} shared {level} identifier(s)")

    return r


def shared_cut_points(values: list[float], classes: int = 5) -> list[float]:
    """Quantile cut points computed once over the pooled values of a comparison.

    Both panels of a comparison must use the same breaks: independent legends
    on each side can manufacture the appearance of change.
    """
    data = sorted(v for v in values if v is not None)
    if not data:
        return []
    if len(set(data)) == 1:
        return [data[0]]
    cuts = []
    for i in range(1, classes):
        pos = i * (len(data) - 1) / classes
        lo = int(pos)
        hi = min(lo + 1, len(data) - 1)
        frac = pos - lo
        cuts.append(data[lo] + (data[hi] - data[lo]) * frac)
    # Deduplicate while preserving order; repeated cuts mean fewer usable classes.
    out: list[float] = []
    for c in cuts:
        if not out or c > out[-1]:
            out.append(c)
    return out
