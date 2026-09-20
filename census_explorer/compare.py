"""Rules for comparing two releases.

A comparison is only meaningful when the survey product, the measure's
definition and universe, and the geography all line up.  This module decides
that explicitly and refuses rather than guessing.  Where a comparison is
allowed but needs a caveat — most importantly, that adjacent five-year ACS
periods share years and are therefore not independent observations — the
caveat is returned as a disclosure that the interface must display.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Release
from .geography import GeographyEvidence
from .metadata import ReleaseMetadata

BLOCK = "blocking"
DISCLOSE = "disclosure"

LABEL_SEMANTICS_CONFIG = (Path(__file__).resolve().parent.parent
                          / "config" / "label_semantics.json")


def label_semantics() -> dict[str, Any]:
    with open(LABEL_SEMANTICS_CONFIG, "r", encoding="utf-8") as fh:
        return json.load(fh)


def normalise_label(label: str) -> str:
    """Apply only the restylings a human has reviewed and recorded as harmless.

    Anything this does not fold away is a difference the comparison must not
    decide about on its own.
    """
    text = unicodedata.normalize("NFKD", label or "")
    text = (text.replace("\u2019", "'").replace("\u2018", "'")
                .replace("\u201c", '"').replace("\u201d", '"')
                .replace("\u2013", "-").replace("\u2014", "-"))
    text = text.lower()
    text = text.replace(".", "")            # abbreviation_dots
    text = text.replace("-", " ")           # hyphen_space
    text = re.sub(r"\s+", " ", text)        # whitespace
    return text.strip().strip(":").strip()  # trailing_colon


def reviewed_label_change(cell: str, old: str, new: str) -> str | None:
    """Return a reviewer's note if this exact change has been signed off."""
    for record in label_semantics().get("reviewed_equivalences", []):
        if record.get("cell") not in (cell, "*"):
            continue
        if record.get("from") == old and record.get("to") == new:
            return (f"reviewed by {record.get('reviewer', 'unnamed reviewer')} on "
                    f"{record.get('reviewed_on', 'an unrecorded date')}: "
                    f"{record.get('note', 'no note recorded')}")
    return None


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
    #: The areas a comparison may actually use: present in both releases and
    #: covered by established geographic equivalence. Everything else is
    #: excluded, which is what the disclosure promises.
    comparable_geoids: list[str] = field(default_factory=list)
    geography_evidence: dict[str, Any] | None = None
    semantic_verified: bool = False

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
            "comparable_geoids": self.comparable_geoids,
            "comparable_geoid_count": len(self.comparable_geoids),
            "geography_evidence": self.geography_evidence,
            "semantic_verified": self.semantic_verified,
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
          measure=None,
          geography_evidence: GeographyEvidence | None = None
          ) -> CompatibilityReport:
    """Decide whether two releases may be compared for one measure and level.

    The default is refusal. A comparison is allowed only when every applicable
    check has actually been performed and passed: a check that could not run
    blocks rather than being skipped.
    """
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

    # 4. The measure must mean the same thing in both releases.
    #
    # This check cannot be skipped. If the inputs needed to perform it are not
    # available, the comparison is refused rather than allowed on the strength
    # of the checks that did run.
    if measure is None:
        record("semantic_compatibility", False,
               "semantic compatibility has not been verified: no measure was named, "
               "so there is nothing to compare definitions for. Name a measure to "
               "compare these releases.")
    elif meta_a is None or meta_b is None:
        uncached = [r.release_id for r, m in ((a, meta_a), (b, meta_b)) if m is None]
        record("semantic_compatibility", False,
               f"semantic compatibility has not been verified: release metadata is "
               f"not cached for {', '.join(uncached)}. Run: fetch metadata")
    else:
        cells = list(measure.numerator_cells) + list(measure.denominator_cells)
        missing_a = [c for c in cells if not meta_a.has_cell(c)]
        missing_b = [c for c in cells if not meta_b.has_cell(c)]
        cells_ok = not (missing_a or missing_b)
        record("cells_present_in_both", cells_ok,
               f"measure cells missing from a release: {a.release_id}={missing_a}, "
               f"{b.release_id}={missing_b}",
               ok_detail=f"all {len(cells)} cell(s) exist in both releases")

        if not cells_ok:
            record("semantic_compatibility", False,
                   "semantic compatibility has not been verified: a cell this "
                   "measure depends on is absent from one of the releases")
        else:
            # Universes are compared cell by cell. Comparing them as sets hides
            # a swap: two cells exchanging universes leaves the set unchanged.
            universe_changes = {
                c: (meta_a.cell(c).universe, meta_b.cell(c).universe)
                for c in cells
                if meta_a.cell(c).universe != meta_b.cell(c).universe
            }
            record("same_universe_per_cell", not universe_changes,
                   "the published universe changed for: "
                   + "; ".join(f"{c}: {old!r} -> {new!r}"
                               for c, (old, new) in universe_changes.items()),
                   ok_detail=f"every cell keeps its published universe "
                             f"({meta_a.cell(cells[0]).universe})")

            # Label changes are classified, not merely reported. Only the
            # restylings a human has recorded as harmless are allowed through.
            harmless: list[str] = []
            substantive: list[str] = []
            for c in cells:
                old, new = meta_a.cell(c).label, meta_b.cell(c).label
                if old == new:
                    continue
                if normalise_label(old) == normalise_label(new):
                    harmless.append(
                        f"{c}: {old!r} -> {new!r} (reviewed restyling: punctuation, "
                        "spacing or case only; the meaning is unchanged)")
                    continue
                note = reviewed_label_change(c, old, new)
                if note:
                    harmless.append(f"{c}: {old!r} -> {new!r} ({note})")
                    continue
                substantive.append(f"{c}: {old!r} -> {new!r}")

            record("cell_labels_reviewed", not substantive,
                   "the published label changed in a way that has not been reviewed: "
                   + "; ".join(substantive)
                   + ". A label change may or may not be a change of meaning, and "
                     "nothing in the data says which. Record it in "
                     "config/label_semantics.json once a human has checked it.",
                   ok_detail="no unreviewed label change")
            for note in harmless:
                r.disclosures.append(f"published label restyled between releases: {note}")
                r.checks.append({"check": "cell_label_restyled", "passed": True,
                                 "detail": note, "severity": "info"})

            r.semantic_verified = (not universe_changes) and (not substantive)
            r.checks.append({
                "check": "semantic_compatibility",
                "passed": r.semantic_verified,
                "detail": ("the measure's cells, universes and labels were compared "
                           "in both releases"),
                "severity": "info" if r.semantic_verified else BLOCK,
            })

    # 5. Geography: shared identifiers are a precondition, not evidence.
    shared = geoids_a & geoids_b
    r.shared_geoids = len(shared)
    r.only_in_a = sorted(geoids_a - geoids_b)
    r.only_in_b = sorted(geoids_b - geoids_a)

    record("any_shared_geography", bool(shared),
           "the two releases share no geographic identifiers at this level",
           ok_detail=f"{r.shared_geoids} shared {level} identifier(s)")
    record("geography_ids_match", not (r.only_in_a or r.only_in_b),
           f"{len(r.only_in_a)} area(s) appear only in {a.release_id} and "
           f"{len(r.only_in_b)} only in {b.release_id}. Areas present in one release "
           "only are excluded from the comparison and reported here.",
           severity=DISCLOSE,
           ok_detail=f"identical {level} identifier sets ({len(geoids_a)} areas)")

    if a.boundary_release == b.boundary_release:
        evidence = GeographyEvidence.same_vintage(level, a.boundary_release)
    elif geography_evidence is None:
        evidence = GeographyEvidence.none(
            level, a.boundary_release, b.boundary_release,
            "no equivalence evidence was supplied")
    elif not geography_evidence.applies_to(level, a.boundary_release,
                                           b.boundary_release):
        evidence = GeographyEvidence.none(
            level, a.boundary_release, b.boundary_release,
            f"the evidence supplied covers {geography_evidence.level} at "
            f"{geography_evidence.boundary_release_a}/"
            f"{geography_evidence.boundary_release_b}, not {level} at "
            f"{a.boundary_release}/{b.boundary_release}")
    else:
        evidence = geography_evidence

    r.geography_evidence = evidence.to_json()
    record("boundary_vintage_equivalence", evidence.established,
           f"geographic comparability across boundary vintages "
           f"{a.boundary_release} and {b.boundary_release} at {level} level has not "
           f"been established: {evidence.detail}. A shared GEOID is not evidence "
           "that two releases describe the same area.",
           ok_detail=evidence.detail)
    if evidence.established and evidence.kind == "computed_geometry":
        r.disclosures.append(
            f"Areas were matched across boundary vintages by comparing the two "
            f"vintages' polygons: {evidence.detail}")

    if not evidence.established:
        r.comparable_geoids = []
    elif evidence.established_areas is None:
        r.comparable_geoids = sorted(shared)
    else:
        # An area with no verified geometry is not comparable, even if both
        # releases publish an observation for it.
        verified = shared & set(evidence.established_areas)
        r.comparable_geoids = sorted(verified)
        missing = shared - verified
        if missing:
            r.disclosures.append(
                f"{len(missing)} area(s) present in both releases have no verified "
                "geometry in one of the two boundary vintages and are excluded from "
                f"the comparison: {', '.join(sorted(missing)[:10])}"
                + (" and others" if len(missing) > 10 else ""))

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
