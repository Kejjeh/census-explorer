"""GEOIDs, join accounting and the shapefile reader."""

from __future__ import annotations

import unittest
from pathlib import Path

from census_explorer import geography, shapefile
from census_explorer.config import Release
from tests.helpers import square, temp_root, write_shapefile_zip

RELEASE = Release(
    release_id="test", provider="test", dataset="acs/acs5", vintage=2023,
    period_start=2019, period_end=2023, period_label="2019-2023 ACS",
    product_label="test", boundary_release="GENZ2023",
    geography_vintage="2023 test", api_base="", summary_file_base="", citation="test",
)


class GeoidTests(unittest.TestCase):
    def test_geoid_must_be_a_string(self):
        with self.assertRaises(geography.GeographyError) as ctx:
            geography.validate_geoid(36005, "county")
        self.assertIn("leading zeroes", str(ctx.exception))

    def test_leading_zero_geoid_survives(self):
        self.assertEqual(geography.validate_geoid("01001", "county"), "01001")

    def test_wrong_length_is_rejected(self):
        with self.assertRaises(geography.GeographyError):
            geography.validate_geoid("3600", "county")
        with self.assertRaises(geography.GeographyError):
            geography.validate_geoid("36005", "tract")

    def test_full_geo_id_splits(self):
        self.assertEqual(geography.split_geo_id("0500000US36005"), ("0500000", "36005"))
        self.assertEqual(geography.split_geo_id("1400000US36005000100"),
                         ("1400000", "36005000100"))

    def test_unrecognised_geo_id_raises(self):
        with self.assertRaises(geography.GeographyError):
            geography.split_geo_id("36005")

    def test_level_inferred_from_length(self):
        self.assertEqual(geography.level_for_geoid("36005"), "county")
        self.assertEqual(geography.level_for_geoid("36005000100"), "tract")
        with self.assertRaises(geography.GeographyError):
            geography.level_for_geoid("360")


class JoinTests(unittest.TestCase):
    def test_duplicates_are_refused_before_they_multiply_rows(self):
        with self.assertRaises(geography.GeographyError) as ctx:
            geography.join(["36005", "36005"], ["36005"], "county", "GENZ2023")
        self.assertIn("duplicate", str(ctx.exception))

    def test_duplicate_observations_are_refused_too(self):
        with self.assertRaises(geography.GeographyError):
            geography.join(["36005"], ["36005", "36005"], "county", "GENZ2023")

    def test_unmatched_are_reported_on_both_sides(self):
        report = geography.join(["36005", "36047"], ["36005", "36061"],
                                "county", "GENZ2023")
        self.assertEqual(report.matched, 1)
        self.assertEqual(report.unmatched_features, ["36047"])
        self.assertEqual(report.unmatched_observations, ["36061"])
        self.assertFalse(report.is_complete)
        self.assertIn("without a boundary", report.summary())

    def test_a_complete_join_says_so(self):
        report = geography.join(["36005"], ["36005"], "county", "GENZ2023")
        self.assertTrue(report.is_complete)
        self.assertEqual(report.to_json()["unmatched_feature_count"], 0)


class ShapefileTests(unittest.TestCase):
    def test_reads_a_polygon_shapefile_and_its_attributes(self):
        with temp_root() as root:
            path = write_shapefile_zip(
                root / "b.zip",
                [("36005", square(-73.9, 40.8)), ("36047", square(-74.0, 40.7))])
            features = shapefile.read_zip(path)
        self.assertEqual([f.properties["GEOID"] for f in features], ["36005", "36047"])
        self.assertEqual(features[0].geometry["type"], "Polygon")
        # Rings are closed.
        ring = features[0].geometry["coordinates"][0]
        self.assertEqual(ring[0], ring[-1])

    def test_build_geojson_keeps_only_requested_areas_and_reports_them(self):
        with temp_root() as root:
            path = write_shapefile_zip(
                root / "b.zip",
                [("36005", square(-73.9, 40.8)), ("36047", square(-74.0, 40.7)),
                 ("36999", square(-75.0, 41.0))])
            collection, geoids = geography.build_geojson(
                path, "county", {"36005", "36047"}, RELEASE)
        self.assertEqual(sorted(geoids), ["36005", "36047"])
        self.assertFalse(collection["metadata"]["synthetic"])
        self.assertEqual(collection["metadata"]["boundary_release"], "GENZ2023")

    def test_duplicate_boundary_geoids_are_refused(self):
        with temp_root() as root:
            path = write_shapefile_zip(
                root / "b.zip",
                [("36005", square(-73.9, 40.8)), ("36005", square(-74.0, 40.7))])
            with self.assertRaises(geography.GeographyError):
                geography.build_geojson(path, "county", {"36005"}, RELEASE)

    def test_a_non_shapefile_is_rejected_rather_than_guessed_at(self):
        import zipfile
        with temp_root() as root:
            bad = root / "bad.zip"
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("test.shp", b"not a shapefile at all, really")
                zf.writestr("test.dbf", b"\x00" * 40)
            with self.assertRaises(shapefile.ShapefileError):
                shapefile.read_zip(bad)

    def test_coordinate_rounding_keeps_structure(self):
        geom = {"type": "Polygon", "coordinates": [[[1.123456789, 2.987654321]]]}
        out = shapefile.round_geometry(geom, 5)
        self.assertEqual(out["coordinates"], [[[1.12346, 2.98765]]])


if __name__ == "__main__":
    unittest.main()
