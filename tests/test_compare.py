"""Comparison compatibility rules and shared class breaks."""

from __future__ import annotations

import unittest

from census_explorer import compare
from census_explorer.config import ConfigError, MeasureDef, Release, _validate_release
from census_explorer.metadata import CellMeta, ReleaseMetadata


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

MEASURE = MeasureDef(
    measure_id="foreign_born_share", label="share", concept="Nativity",
    unit="percent", kind="share", numerator_cells=["B05002_013"],
    denominator_cells=["B05002_001"], universe_note="u", definition_note="d")


def fake_metadata(release_id: str, labels: dict[str, str] | None = None,
                  universes: dict[str, str] | None = None) -> ReleaseMetadata:
    """A metadata model built in memory, so these stay unit tests."""
    labels = labels or {}
    universes = universes or {}
    cells = {}
    for code in ("B05002_001", "B05002_013"):
        cells[code] = CellMeta(
            cell=code, table="B05002", estimate_var=f"{code}E", moe_var=f"{code}M",
            estimate_annotation_var=f"{code}EA", moe_annotation_var=f"{code}MA",
            label=labels.get(code, f"Total / {code}"), label_path=["Total"],
            concept="Place of Birth by Nativity",
            universe=universes.get(code, "Total population"), predicate_type="int")
    meta = ReleaseMetadata(release_id=release_id)
    meta.tables["B05002"] = cells
    meta.table_universe["B05002"] = "Total population"
    meta.table_concept["B05002"] = "Place of Birth by Nativity"
    meta.source_files["B05002"] = "fixture"
    return meta


def checked(a, b, level="county", geo_a=GEO, geo_b=GEO, measure=MEASURE,
            meta_a=None, meta_b=None, evidence="auto"):
    """Run a check with the evidence a real caller would have supplied."""
    if evidence == "auto":
        evidence = (compare.GeographyEvidence.same_vintage(level, a.boundary_release)
                    if a.boundary_release == b.boundary_release else None)
    return compare.check(
        a, b, level, geo_a, geo_b,
        meta_a if meta_a is not None else fake_metadata(a.release_id),
        meta_b if meta_b is not None else fake_metadata(b.release_id),
        measure, geography_evidence=evidence)


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
        r = checked(A, ONE_YEAR)
        self.assertFalse(r.allowed)
        self.assertTrue(any("different survey products" in b for b in r.blocking))
        self.assertTrue(any("must not be spliced" in b for b in r.blocking))

    def test_overlapping_five_year_periods_are_allowed_but_disclosed(self):
        # Same boundary vintage, so geography needs no further evidence.
        same_vintage_b = release("acs5_2022", 2018, 2022, "2018-2022 ACS")
        r = checked(A, same_vintage_b)
        self.assertTrue(r.allowed, r.blocking)
        self.assertFalse(r.independent_observations)
        disclosure = " ".join(r.disclosures)
        self.assertIn("share the year(s) 2019, 2020, 2021, 2022", disclosure)
        self.assertIn("not independent observations", disclosure)

    def test_non_overlapping_periods_are_independent(self):
        old_same_vintage = release("acs5_2017", 2013, 2017, "2013-2017 ACS")
        r = checked(A, old_same_vintage)
        self.assertTrue(r.allowed, r.blocking)
        self.assertTrue(r.independent_observations)

    def test_comparing_a_release_with_itself_is_refused(self):
        r = checked(A, A)
        self.assertFalse(r.allowed)

    def test_geography_differences_are_reported_and_actually_excluded(self):
        other = set(GEO) - {"36085"} | {"36999"}
        same_vintage_b = release("acs5_2022", 2018, 2022, "2018-2022 ACS")
        r = checked(A, same_vintage_b, geo_b=other)
        self.assertTrue(r.allowed, "a partial overlap is a disclosure, not a block")
        self.assertEqual(r.only_in_a, ["36085"])
        self.assertEqual(r.only_in_b, ["36999"])
        self.assertTrue(any("appear only in" in d for d in r.disclosures))
        # The promise in that disclosure has to be true of the data too.
        self.assertNotIn("36085", r.comparable_geoids)
        self.assertNotIn("36999", r.comparable_geoids)
        self.assertEqual(set(r.comparable_geoids), GEO - {"36085"})

    def test_no_shared_geography_blocks_the_comparison(self):
        r = checked(A, B, level="tract", geo_a={"36005000100"}, geo_b={"36047000100"})
        self.assertFalse(r.allowed)
        self.assertTrue(any("share no geographic identifiers" in b for b in r.blocking))

    def test_a_differing_boundary_vintage_blocks_without_evidence(self):
        r = checked(A, B, evidence=None)
        self.assertFalse(r.allowed)
        check = [c for c in r.checks if c["check"] == "boundary_vintage_equivalence"][0]
        self.assertFalse(check["passed"])
        self.assertEqual(r.comparable_geoids, [],
                         "nothing is comparable until equivalence is established")

    def test_a_differing_boundary_vintage_is_allowed_with_established_evidence(self):
        evidence = compare.GeographyEvidence(
            kind="computed_geometry", established=True, level="county",
            boundary_release_a="GENZ2023", boundary_release_b="GENZ2022",
            detail="polygons compared and identical within tolerance",
            areas_compared=len(GEO))
        r = checked(A, B, evidence=evidence)
        self.assertTrue(r.allowed, r.blocking)
        self.assertEqual(set(r.comparable_geoids), GEO)
        self.assertEqual(r.geography_evidence["kind"], "computed_geometry")

    def test_the_evidence_travels_in_the_report(self):
        r = checked(A, A.__class__(**{**A.__dict__, "release_id": "other"}))
        self.assertIsNotNone(r.geography_evidence)
        self.assertEqual(r.geography_evidence["kind"], "same_vintage")

    def test_passing_checks_do_not_carry_a_failure_message(self):
        same_vintage_b = release("acs5_2022", 2018, 2022, "2018-2022 ACS")
        r = checked(A, same_vintage_b)
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
