"""Reading cached responses: formats, duplicates, malformed rows, denominators."""

from __future__ import annotations

import json
import unittest

from census_explorer import dataset
from census_explorer.sentinels import classify
from tests.helpers import FIXTURES, offline, temp_root


class SummaryFileReaderTests(unittest.TestCase):
    def test_reads_estimate_and_moe_pairs_by_cell(self):
        with offline():
            rows = dataset.read_summary_file(FIXTURES / "summary_file" / "B05002.psv")
        self.assertIn("0500000US36005", rows)
        est, moe = rows["0500000US36005"]["B05002_013"]
        self.assertEqual(est, "300")
        self.assertEqual(moe, "25")

    def test_sentinels_survive_reading_as_raw_text(self):
        rows = dataset.read_summary_file(FIXTURES / "summary_file" / "B05002.psv")
        _est, moe = rows["0500000US36005"]["B05002_001"]
        self.assertEqual(moe, "-555555555")
        self.assertIsNone(classify(moe).value)

    def test_duplicate_geo_id_is_refused(self):
        with temp_root() as root:
            p = root / "dup.psv"
            p.write_text(
                "GEO_ID|B05002_E001|B05002_M001\n"
                "0500000US36005|1|2\n0500000US36005|3|4\n", encoding="utf-8")
            with self.assertRaises(dataset.DatasetError) as ctx:
                dataset.read_summary_file(p)
        self.assertIn("duplicate GEO_ID", str(ctx.exception))

    def test_short_row_is_refused_rather_than_padded(self):
        with temp_root() as root:
            p = root / "short.psv"
            p.write_text("GEO_ID|B05002_E001|B05002_M001\n0500000US36005|1\n",
                         encoding="utf-8")
            with self.assertRaises(dataset.DatasetError) as ctx:
                dataset.read_summary_file(p)
        self.assertIn("header has", str(ctx.exception))

    def test_wrong_first_column_is_refused(self):
        with temp_root() as root:
            p = root / "wrong.psv"
            p.write_text("ID|B05002_E001\nx|1\n", encoding="utf-8")
            with self.assertRaises(dataset.DatasetError):
                dataset.read_summary_file(p)

    def test_corrupted_cache_file_does_not_produce_silent_values(self):
        with temp_root() as root:
            p = root / "corrupt.psv"
            p.write_text("GEO_ID|B05002_E001|B05002_M001\n"
                         "0500000US36005|\x00\x01garbage|2\n", encoding="utf-8")
            rows = dataset.read_summary_file(p)
            cell = classify(rows["0500000US36005"]["B05002_001"][0])
        self.assertIsNone(cell.value)
        self.assertEqual(cell.status, "unparseable")


class ApiReaderTests(unittest.TestCase):
    def test_reads_the_census_api_array_shape(self):
        with offline():
            rows = dataset.read_api_response(FIXTURES / "api" / "B05002_county_00.json")
        self.assertEqual(rows["0500000US36005"]["B05002_013"], ("300", "25"))

    def test_json_null_becomes_missing_not_zero(self):
        rows = dataset.read_api_response(FIXTURES / "api" / "B05002_county_00.json")
        est, _moe = rows["0500000US36061"]["B05002_013"]
        self.assertIsNone(est)
        self.assertEqual(classify(est).status, "missing")

    def test_response_without_geo_id_is_refused(self):
        with temp_root() as root:
            p = root / "bad.json"
            p.write_text(json.dumps([["NAME", "B05002_001E"], ["x", "1"]]), encoding="utf-8")
            with self.assertRaises(dataset.DatasetError) as ctx:
                dataset.read_api_response(p)
        self.assertIn("no GEO_ID", str(ctx.exception))

    def test_non_array_response_is_refused(self):
        with temp_root() as root:
            p = root / "bad.json"
            p.write_text(json.dumps({"error": "nope"}), encoding="utf-8")
            with self.assertRaises(dataset.DatasetError):
                dataset.read_api_response(p)

    def test_both_transports_agree_on_the_same_values(self):
        sf = dataset.read_summary_file(FIXTURES / "summary_file" / "B05002.psv")
        api = dataset.read_api_response(FIXTURES / "api" / "B05002_county_00.json")
        for geo in ("0500000US36005", "0500000US36047", "0500000US36085"):
            for cell in ("B05002_001", "B05002_013"):
                self.assertEqual(sf[geo][cell], api[geo][cell],
                                 f"{geo} {cell} differs between transports")


class CompactionTests(unittest.TestCase):
    def test_unavailable_values_keep_their_status_when_compacted(self):
        compact = dataset.compact_value({
            "estimate": None, "estimate_status": "unavailable",
            "estimate_reason": "annotated", "moe": None, "moe_status": "unavailable",
            "moe_reason": "annotated", "cv_percent": None, "reliability": "not available",
            "numerator": None, "denominator": None, "kind": "count", "source_flags": [],
        })
        self.assertIsNone(compact["e"])
        self.assertEqual(compact["es"], "unavailable")
        self.assertEqual(compact["er"], "annotated")
        self.assertNotIn("cv", compact)

    def test_a_zero_estimate_compacts_to_zero_not_to_absent(self):
        compact = dataset.compact_value({
            "estimate": 0, "estimate_status": "ok", "estimate_reason": None,
            "moe": 12, "moe_status": "ok", "moe_reason": None, "cv_percent": None,
            "reliability": "not available", "numerator": 0, "denominator": None,
            "kind": "count", "source_flags": [],
        })
        self.assertEqual(compact["e"], 0)
        self.assertEqual(compact["es"], "ok")


if __name__ == "__main__":
    unittest.main()


class CachePathTests(unittest.TestCase):
    """A narrower pull must never overwrite a wider one already in the cache."""

    def test_different_selections_of_a_table_use_different_cache_files(self):
        from census_explorer.config import Release
        from census_explorer.retrieve import acs_summary_file
        from census_explorer import reconcile

        release = Release(
            release_id="r", provider="p", dataset="acs/acs5", vintage=2023,
            period_start=2019, period_end=2023, period_label="2019-2023 ACS",
            product_label="p", boundary_release="GENZ2023", geography_vintage="v",
            api_base="", summary_file_base="", citation="c")
        boroughs = acs_summary_file.cache_rel_path(release, "B01003")
        city = reconcile.cache_rel_path(release, "B01003")
        self.assertNotEqual(boroughs, city)
        self.assertTrue(city.endswith("B01003_place_nyc.psv"))


class ReconciliationTests(unittest.TestCase):
    def test_reconciliation_reports_a_mismatch_instead_of_absorbing_it(self):
        from census_explorer import config as config_mod, reconcile
        from census_explorer.config import Release

        release = Release(
            release_id="r", provider="p", dataset="acs/acs5", vintage=2023,
            period_start=2019, period_end=2023, period_label="2019-2023 ACS",
            product_label="p", boundary_release="GENZ2023", geography_vintage="v",
            api_base="", summary_file_base="", citation="c")
        cfg = config_mod.load()
        with temp_root() as root:
            d = root / "data/raw/acs/r/summary_file"
            d.mkdir(parents=True)
            (d / "B01003.psv").write_text(
                "GEO_ID|B01003_E001|B01003_M001\n"
                + "".join(f"0500000US{g}|100|10\n" for g in cfg.county_geoids),
                encoding="utf-8")
            (d / "B01003_place_nyc.psv").write_text(
                "GEO_ID|B01003_E001|B01003_M001\n1600000US3651000|999|10\n",
                encoding="utf-8")
            report = reconcile.run(root, cfg, release)
        checked = [r for r in report["results"] if r["cell"] == "B01003_001"][0]
        self.assertEqual(checked["status"], "MISMATCH")
        self.assertEqual(checked["borough_sum"], 500)
        self.assertEqual(checked["published_city_value"], 999)
        self.assertEqual(checked["difference"], -499)
        self.assertFalse(report["passed"])

    def test_an_annotated_borough_value_makes_the_check_not_comparable(self):
        from census_explorer import config as config_mod, reconcile
        from census_explorer.config import Release

        release = Release(
            release_id="r", provider="p", dataset="acs/acs5", vintage=2023,
            period_start=2019, period_end=2023, period_label="2019-2023 ACS",
            product_label="p", boundary_release="GENZ2023", geography_vintage="v",
            api_base="", summary_file_base="", citation="c")
        cfg = config_mod.load()
        with temp_root() as root:
            d = root / "data/raw/acs/r/summary_file"
            d.mkdir(parents=True)
            rows = [f"0500000US{g}|100|10\n" for g in cfg.county_geoids[:-1]]
            rows.append(f"0500000US{cfg.county_geoids[-1]}|-999999999|-999999999\n")
            (d / "B01003.psv").write_text(
                "GEO_ID|B01003_E001|B01003_M001\n" + "".join(rows), encoding="utf-8")
            (d / "B01003_place_nyc.psv").write_text(
                "GEO_ID|B01003_E001|B01003_M001\n1600000US3651000|500|10\n",
                encoding="utf-8")
            report = reconcile.run(root, cfg, release)
        checked = [r for r in report["results"] if r["cell"] == "B01003_001"][0]
        self.assertEqual(checked["status"], "not comparable")
        self.assertIn("insufficient number of sample cases", checked["note"])
