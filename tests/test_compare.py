"""Comparison compatibility rules and shared class breaks."""

from __future__ import annotations

import unittest

from census_explorer import compare
from census_explorer.config import ConfigError, MeasureDef, Release, _validate_release


def release(rid, start, end, label, dataset="acs/acs5", boundary="GENZ2023"):
    return Release(
        release_id=rid, provider="US Census Bureau", dataset=dataset, vintage=end,
        period_start=start, period_end=end, period_label=label,
        product_label="test", boundary_release=boundary,
        geography_vintage=f"{boundary} test", api_base="", summary_file_base="",
        citation="test",
    )


A = release("acs5_2023", 2019, 2023, "2019-2023 ACS")
B = release("acs5_2022", 2018, 2022, "2018-2022 ACS", boundary="GENZ2022")
OLD = release("acs5_2017", 2013, 2017, "2013-2017 ACS", boundary="GENZ2017")
ONE_YEAR = release("acs1_2023", 2023, 2023, "2023 ACS 1-year", dataset="acs/acs1")

GEO = {"36005", "36047", "36061", "36081", "36085"}


class PeriodLabelTests(unittest.TestCase):
    def test_a_five_year_period_may_not_be_labelled_with_one_year(self):
        bad = release("bad", 2019, 2023, "2020")
        with self.assertRaises(ConfigError) as ctx:
            _validate_release(bad)
        self.assertIn("may not be labelled '2020'", str(ctx.exception))

    def test_the_label_must_name_both_endpoints(self):
        with self.assertRaises(ConfigError):
            _validate_release(release("bad", 2019, 2023, "2019 ACS"))

    def test_the_real_label_passes(self):
        _validate_release(A)   # must not raise


class CompatibilityTests(unittest.TestCase):
    def test_one_year_and_five_year_may_not_be_spliced(self):
        r = compare.check(A, ONE_YEAR, "county", GEO, GEO)
        self.assertFalse(r.allowed)
        self.assertTrue(any("different survey products" in b for b in r.blocking))
        self.assertTrue(any("must not be spliced" in b for b in r.blocking))

    def test_overlapping_five_year_periods_are_allowed_but_disclosed(self):
        r = compare.check(A, B, "county", GEO, GEO)
        self.assertTrue(r.allowed)
        self.assertFalse(r.independent_observations)
        disclosure = " ".join(r.disclosures)
        self.assertIn("share the year(s) 2019, 2020, 2021, 2022", disclosure)
        self.assertIn("not independent observations", disclosure)

    def test_non_overlapping_periods_are_independent(self):
        r = compare.check(A, OLD, "county", GEO, GEO)
        self.assertTrue(r.allowed)
        self.assertTrue(r.independent_observations)

    def test_comparing_a_release_with_itself_is_refused(self):
        r = compare.check(A, A, "county", GEO, GEO)
        self.assertFalse(r.allowed)

    def test_geography_differences_are_reported_and_excluded(self):
        other = set(GEO) - {"36085"} | {"36999"}
        r = compare.check(A, B, "county", GEO, other)
        self.assertTrue(r.allowed, "a partial overlap is a disclosure, not a block")
        self.assertEqual(r.only_in_a, ["36085"])
        self.assertEqual(r.only_in_b, ["36999"])
        self.assertTrue(any("appear only in" in d for d in r.disclosures))

    def test_no_shared_geography_blocks_the_comparison(self):
        r = compare.check(A, B, "tract", {"36005000100"}, {"36047000100"})
        self.assertFalse(r.allowed)
        self.assertTrue(any("share no geographic identifiers" in b for b in r.blocking))

    def test_boundary_vintage_difference_is_noted_not_hidden(self):
        r = compare.check(A, B, "county", GEO, GEO)
        vintage = [c for c in r.checks if c["check"] == "boundary_vintage"]
        self.assertTrue(vintage)
        self.assertIn("GENZ2023", vintage[0]["detail"])

    def test_passing_checks_do_not_carry_a_failure_message(self):
        r = compare.check(A, B, "county", GEO, GEO)
        for check in r.checks:
            if check["passed"]:
                self.assertNotIn("must not", check["detail"])


class CutPointTests(unittest.TestCase):
    def test_pooled_cut_points_are_monotonic(self):
        cuts = compare.shared_cut_points([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 5)
        self.assertEqual(cuts, sorted(cuts))
        self.assertEqual(len(cuts), 4)

    def test_no_values_gives_no_cut_points(self):
        self.assertEqual(compare.shared_cut_points([], 5), [])

    def test_a_constant_series_collapses_to_one_cut(self):
        self.assertEqual(compare.shared_cut_points([7, 7, 7], 5), [7])

    def test_none_values_are_excluded_not_treated_as_zero(self):
        cuts = compare.shared_cut_points([None, 10, 20, 30, 40, 50], 5)
        self.assertTrue(all(c >= 10 for c in cuts))


if __name__ == "__main__":
    unittest.main()
