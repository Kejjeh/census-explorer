"""Measure arithmetic: MOE pairing, propagation, denominators and CVs."""

from __future__ import annotations

import math
import unittest

from census_explorer import measures
from census_explorer.config import MeasureDef
from census_explorer.sentinels import classify


def cells(pairs):
    est = {c: classify(e) for c, (e, _m) in pairs.items()}
    moe = {c: classify(m) for c, (_e, m) in pairs.items()}
    return est, moe


COUNT = MeasureDef(
    measure_id="count_two_cells", label="Two cells summed", concept="test",
    unit="persons", kind="count",
    numerator_cells=["B06009_005", "B06009_006"], denominator_cells=[],
    universe_note="test universe", definition_note="test",
)
SHARE = MeasureDef(
    measure_id="share", label="A share", concept="test", unit="percent", kind="share",
    numerator_cells=["B05002_013"], denominator_cells=["B05002_001"],
    universe_note="test universe", definition_note="test",
)


class AggregationTests(unittest.TestCase):
    def test_counts_sum_and_moes_combine_in_quadrature(self):
        est, moe = cells({"B06009_005": ("100", "30"), "B06009_006": ("50", "40")})
        v = measures.compute(COUNT, "36005", est, moe)
        self.assertEqual(v.estimate, 150)
        self.assertAlmostEqual(v.moe, math.sqrt(30 ** 2 + 40 ** 2))
        self.assertEqual(v.moe_status, measures.OK)

    def test_an_annotated_input_makes_the_estimate_unavailable(self):
        est, moe = cells({"B06009_005": ("-999999999", "-999999999"),
                          "B06009_006": ("50", "40")})
        v = measures.compute(COUNT, "36005", est, moe)
        self.assertEqual(v.estimate_status, measures.UNAVAILABLE)
        self.assertIsNone(v.estimate)
        self.assertIn("insufficient number of sample cases", v.estimate_reason)

    def test_an_annotated_moe_leaves_the_estimate_but_removes_the_moe(self):
        est, moe = cells({"B06009_005": ("100", "-222222222"),
                          "B06009_006": ("50", "40")})
        v = measures.compute(COUNT, "36005", est, moe)
        self.assertEqual(v.estimate, 150)
        self.assertIsNone(v.moe)
        self.assertEqual(v.moe_status, measures.UNAVAILABLE)
        self.assertIsNone(v.cv_percent, "a CV needs a valid margin of error")

    def test_controlled_moe_contributes_zero_and_is_flagged(self):
        est, moe = cells({"B06009_005": ("100", "-555555555"),
                          "B06009_006": ("50", "40")})
        v = measures.compute(COUNT, "36005", est, moe)
        self.assertEqual(v.estimate, 150)
        self.assertAlmostEqual(v.moe, 40.0)
        self.assertTrue(any("controlled" in f for f in v.source_flags))

    def test_missing_source_cell_is_reported_not_treated_as_zero(self):
        est, moe = cells({"B06009_005": ("100", "30")})
        v = measures.compute(COUNT, "36005", est, moe)
        self.assertEqual(v.estimate_status, measures.UNAVAILABLE)
        self.assertIn("not retrieved", v.estimate_reason)


class ShareTests(unittest.TestCase):
    def test_share_uses_the_declared_denominator(self):
        est, moe = cells({"B05002_013": ("300", "25"), "B05002_001": ("1000", "0")})
        v = measures.compute(SHARE, "36005", est, moe)
        self.assertAlmostEqual(v.estimate, 30.0)
        self.assertEqual(v.numerator, 300)
        self.assertEqual(v.denominator, 1000)
        self.assertEqual(v.unit, "percent")

    def test_zero_denominator_is_undefined_not_zero(self):
        est, moe = cells({"B05002_013": ("0", "12"), "B05002_001": ("0", "12")})
        v = measures.compute(SHARE, "36061", est, moe)
        self.assertEqual(v.estimate_status, measures.UNAVAILABLE)
        self.assertIsNone(v.estimate)
        self.assertIn("undefined, not zero", v.estimate_reason)

    def test_proportion_formula_matches_the_published_approximation(self):
        moe, formula = measures.proportion_moe(300, 1000, 25, 40)
        expected = math.sqrt(25 ** 2 - (0.3 ** 2) * (40 ** 2)) / 1000
        self.assertAlmostEqual(moe, expected)
        self.assertEqual(formula, "proportion formula")

    def test_negative_radicand_falls_back_to_the_ratio_formula(self):
        moe, formula = measures.proportion_moe(900, 1000, 5, 400)
        expected = math.sqrt(5 ** 2 + (0.9 ** 2) * (400 ** 2)) / 1000
        self.assertAlmostEqual(moe, expected)
        self.assertIn("ratio formula", formula)

    def test_share_is_unavailable_when_the_numerator_is_annotated(self):
        est, moe = cells({"B05002_013": ("-666666666", "-222222222"),
                          "B05002_001": ("1000", "0")})
        v = measures.compute(SHARE, "36005", est, moe)
        self.assertEqual(v.estimate_status, measures.UNAVAILABLE)
        self.assertIsNone(v.estimate)


class ReliabilityTests(unittest.TestCase):
    def test_cv_uses_the_documented_90_percent_relationship(self):
        est, moe = cells({"B06009_005": ("1000", "100"), "B06009_006": ("0", "0")})
        v = measures.compute(COUNT, "36005", est, moe)
        self.assertAlmostEqual(v.cv_percent, (100 / 1.645) / 1000 * 100)
        self.assertEqual(v.reliability, "lower relative error")

    def test_no_cv_for_a_percentage(self):
        est, moe = cells({"B05002_013": ("300", "25"), "B05002_001": ("1000", "0")})
        v = measures.compute(SHARE, "36005", est, moe)
        self.assertIsNone(v.cv_percent)
        self.assertEqual(v.reliability, "not available")

    def test_no_cv_for_a_zero_estimate(self):
        est, moe = cells({"B06009_005": ("0", "12"), "B06009_006": ("0", "12")})
        v = measures.compute(COUNT, "36005", est, moe)
        self.assertEqual(v.estimate, 0)
        self.assertIsNone(v.cv_percent)


if __name__ == "__main__":
    unittest.main()
