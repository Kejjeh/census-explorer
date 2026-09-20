"""Sentinel, annotation and missing-value handling."""

from __future__ import annotations

import unittest

from census_explorer import sentinels
from tests.helpers import offline


class ClassifyTests(unittest.TestCase):
    def test_documented_sentinels_are_meanings_not_numbers(self):
        with offline():
            for code in ("-999999999", "-888888888", "-666666666",
                         "-555555555", "-333333333", "-222222222"):
                cell = sentinels.classify(code)
                self.assertEqual(cell.status, sentinels.ANNOTATED, code)
                self.assertIsNone(cell.value, f"{code} must not become a number")
                self.assertTrue(cell.meaning, f"{code} must carry its published meaning")

    def test_annotation_table_comes_from_an_archived_official_document(self):
        prov = sentinels.reference_provenance()
        self.assertIn("census.gov", prov["source_url"])
        self.assertEqual(len(prov["document_sha256"]), 64)
        self.assertTrue(prov["retrieved_at"])

    def test_missing_and_empty_are_missing_not_zero(self):
        for raw in (None, "", "   "):
            cell = sentinels.classify(raw)
            self.assertEqual(cell.status, sentinels.MISSING)
            self.assertIsNone(cell.value)

    def test_real_numbers_parse(self):
        self.assertEqual(sentinels.classify("1419250").value, 1419250)
        self.assertEqual(sentinels.classify("0").value, 0)
        self.assertEqual(sentinels.classify("12.5").value, 12.5)
        self.assertTrue(sentinels.classify("0").is_number)

    def test_zero_is_a_measurement_and_stays_one(self):
        cell = sentinels.classify("0")
        self.assertEqual(cell.status, sentinels.OK)
        self.assertEqual(cell.value, 0)

    def test_undocumented_repunit_code_is_refused_rather_than_used(self):
        cell = sentinels.classify("-444444444")
        self.assertEqual(cell.status, sentinels.UNPARSEABLE)
        self.assertIsNone(cell.value)
        self.assertIn("undocumented", cell.meaning)

    def test_non_numeric_text_is_unparseable(self):
        cell = sentinels.classify("(X)")
        self.assertEqual(cell.status, sentinels.UNPARSEABLE)
        self.assertIsNone(cell.value)

    def test_controlled_flag_identifies_only_the_controlled_code(self):
        self.assertTrue(sentinels.is_controlled(sentinels.classify("-555555555")))
        self.assertFalse(sentinels.is_controlled(sentinels.classify("-222222222")))
        self.assertFalse(sentinels.is_controlled(sentinels.classify("100")))
        self.assertFalse(sentinels.is_controlled(None))


if __name__ == "__main__":
    unittest.main()
