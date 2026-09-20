"""Deriving measures from published cells, with uncertainty carried through.

Rules enforced here:

* An annotated or missing cell never becomes a number.  Estimate availability
  and margin-of-error availability are tracked separately, because ACS
  publishes real estimates with annotated margins of error (a controlled county
  total, for example).
* Counts are summed, then shares are computed from summed numerators and
  summed denominators.  Percentages are never summed or averaged.
* Aggregation and proportion margins of error use the approximation formulas
  in the Census Bureau's published ACS margin-of-error guidance.  Missing
  uncertainty is reported as unavailable, never as zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .config import MeasureDef
from .sentinels import Cell, is_controlled

#: ACS margins of error are published at the 90% confidence level.
CONFIDENCE_LEVEL = 0.90
Z_90 = 1.645

OK = "ok"
UNAVAILABLE = "unavailable"


@dataclass
class MeasureValue:
    measure_id: str
    geoid: str
    unit: str
    kind: str
    estimate: float | None = None
    estimate_status: str = OK
    estimate_reason: str | None = None
    moe: float | None = None
    moe_status: str = OK
    moe_reason: str | None = None
    # Inputs kept for the detail drawer.
    numerator: float | None = None
    denominator: float | None = None
    source_flags: list[str] = field(default_factory=list)

    @property
    def cv_percent(self) -> float | None:
        """Coefficient of variation, only where it is defined.

        Uses SE = MOE / 1.645, which applies to a valid numeric 90% margin of
        error.  Undefined for a zero estimate, an annotated margin of error, or
        a percentage (where the ratio to the estimate is not a CV of a count).
        """
        if self.kind != "count":
            return None
        if self.estimate_status != OK or self.moe_status != OK:
            return None
        if not self.estimate or self.moe is None:
            return None
        return (self.moe / Z_90) / abs(self.estimate) * 100.0

    @property
    def reliability(self) -> str:
        cv = self.cv_percent
        if cv is None:
            return "not available"
        if cv < 15:
            return "lower relative error"
        if cv < 30:
            return "moderate relative error"
        return "high relative error"

    def to_json(self) -> dict:
        return {
            "measure_id": self.measure_id,
            "geoid": self.geoid,
            "unit": self.unit,
            "kind": self.kind,
            "estimate": self.estimate,
            "estimate_status": self.estimate_status,
            "estimate_reason": self.estimate_reason,
            "moe": self.moe,
            "moe_status": self.moe_status,
            "moe_reason": self.moe_reason,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "cv_percent": self.cv_percent,
            "reliability": self.reliability,
            "source_flags": self.source_flags,
        }


@dataclass
class _Agg:
    total: float | None
    total_reason: str | None
    moe: float | None
    moe_reason: str | None
    flags: list[str]


def aggregate_cells(cells: Iterable[tuple[str, Cell, Cell]]) -> _Agg:
    """Sum estimate cells and combine their margins of error.

    ``cells`` yields ``(cell_code, estimate_cell, moe_cell)``.
    """
    total = 0.0
    variance = 0.0
    est_problem: str | None = None
    moe_problem: str | None = None
    flags: list[str] = []

    any_cell = False
    for code, est, moe in cells:
        any_cell = True
        if est.status != "ok" or est.value is None:
            reason = f"{code}: {est.meaning or est.status}"
            est_problem = reason if est_problem is None else est_problem
            if est.symbol:
                flags.append(f"{code} {est.symbol}")
            continue
        total += float(est.value)
        if is_controlled(moe):
            # Published guidance for this annotation: the estimate is controlled
            # to an independent population estimate, has no sampling error, and
            # its margin of error may be treated as zero. The contribution is
            # zero rather than unknown, and the fact is always flagged.
            flags.append(f"{code} controlled estimate (MOE treated as zero)")
            continue
        if moe is None or moe.status != "ok" or moe.value is None:
            detail = (moe.meaning if moe is not None else "margin of error not published")
            reason = f"{code}: {detail}"
            moe_problem = reason if moe_problem is None else moe_problem
            if moe is not None and moe.symbol:
                flags.append(f"{code} MOE {moe.symbol}")
            continue
        variance += float(moe.value) ** 2

    if not any_cell:
        return _Agg(None, "no cells supplied", None, "no cells supplied", flags)
    if est_problem is not None:
        return _Agg(None, est_problem, None, est_problem, flags)
    if moe_problem is not None:
        return _Agg(total, None, None, moe_problem, flags)
    return _Agg(total, None, math.sqrt(variance), None, flags)


def proportion_moe(numerator: float, denominator: float,
                   moe_num: float, moe_den: float) -> tuple[float, str]:
    """Margin of error for numerator/denominator, using the ACS formulas.

    Returns the margin of error on the proportion (0-1 scale) and the name of
    the formula used, so the detail drawer can say which one applied.
    """
    if denominator == 0:
        raise ZeroDivisionError("denominator is zero")
    p = numerator / denominator
    radicand = moe_num ** 2 - (p ** 2) * (moe_den ** 2)
    if radicand < 0:
        # The published guidance says to fall back to the ratio formula when
        # the proportion formula's radicand is negative.
        return (math.sqrt(moe_num ** 2 + (p ** 2) * (moe_den ** 2)) / denominator,
                "ratio formula (proportion radicand was negative)")
    return math.sqrt(radicand) / denominator, "proportion formula"


def compute(measure: MeasureDef, geoid: str,
            estimates: Mapping[str, Cell], moes: Mapping[str, Cell]) -> MeasureValue:
    """Compute one measure for one geography from classified source cells."""
    value = MeasureValue(measure_id=measure.measure_id, geoid=geoid,
                         unit=measure.unit, kind=measure.kind)

    def pairs(codes):
        return [(c, estimates.get(c), moes.get(c)) for c in codes]

    missing = [c for c in measure.numerator_cells + measure.denominator_cells
               if c not in estimates]
    if missing:
        value.estimate_status = UNAVAILABLE
        value.estimate_reason = f"source cells not retrieved: {missing}"
        value.moe_status = UNAVAILABLE
        value.moe_reason = value.estimate_reason
        return value

    num = aggregate_cells(pairs(measure.numerator_cells))
    value.source_flags.extend(num.flags)

    if measure.kind == "count":
        value.numerator = num.total
        value.estimate = num.total
        if num.total is None:
            value.estimate_status = UNAVAILABLE
            value.estimate_reason = num.total_reason
        value.moe = num.moe
        if num.moe is None:
            value.moe_status = UNAVAILABLE
            value.moe_reason = num.moe_reason
        return value

    den = aggregate_cells(pairs(measure.denominator_cells))
    value.source_flags.extend(den.flags)
    value.numerator = num.total
    value.denominator = den.total

    if num.total is None or den.total is None:
        value.estimate_status = UNAVAILABLE
        value.estimate_reason = num.total_reason or den.total_reason
        value.moe_status = UNAVAILABLE
        value.moe_reason = value.estimate_reason
        return value
    if den.total == 0:
        value.estimate_status = UNAVAILABLE
        value.estimate_reason = (
            "denominator is zero in this area; a share is undefined, not zero"
        )
        value.moe_status = UNAVAILABLE
        value.moe_reason = value.estimate_reason
        return value

    value.estimate = num.total / den.total * 100.0

    if num.moe is None or den.moe is None:
        value.moe_status = UNAVAILABLE
        value.moe_reason = num.moe_reason or den.moe_reason
        return value
    try:
        moe_p, formula = proportion_moe(num.total, den.total, num.moe, den.moe)
    except ZeroDivisionError:
        value.moe_status = UNAVAILABLE
        value.moe_reason = "denominator is zero"
        return value
    value.moe = moe_p * 100.0
    value.moe_reason = formula
    return value
