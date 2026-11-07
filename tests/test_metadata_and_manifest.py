"""Metadata resolution, drift detection, and cache integrity."""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from census_explorer import metadata, provenance
from census_explorer.config import MeasureDef, Release
from census_explorer.retrieve import reference
from tests.helpers import FIXTURES, offline, temp_root

RELEASE = Release(
    release_id="testrel", provider="test", dataset="acs/acs5", vintage=2023,
    period_start=2019, period_end=2023, period_label="2019-2023 ACS",
    product_label="test", boundary_release="GENZ2023",
    geography_vintage="2023 test", api_base="", summary_file_base="", citation="test",
)
DRIFTED = Release(**{**RELEASE.__dict__, "release_id": "drifted"})

SHARE = MeasureDef(
    measure_id="fb_share", label="share", concept="test", unit="percent", kind="share",
    numerator_cells=["B05002_013"], denominator_cells=["B05002_001"],
    universe_note="t", definition_note="t",
)
MIXED_UNIVERSE = MeasureDef(
    measure_id="mixed", label="mixed", concept="test", unit="percent", kind="share",
    numerator_cells=["B05002_013"], denominator_cells=["B06009_001"],
    universe_note="t", definition_note="t",
)
GHOST = MeasureDef(
    measure_id="ghost", label="ghost", concept="test", unit="persons", kind="count",
    numerator_cells=["B05002_999"], denominator_cells=[],
    universe_note="t", definition_note="t",
)


def stage_metadata(root: Path, release_id: str, source: str) -> None:
    dest = root / f"data/raw/metadata/{release_id}/groups/B05002.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "metadata" / source, dest)


class MetadataTests(unittest.TestCase):
    def test_cells_resolve_to_published_estimate_moe_and_annotation_names(self):
        with temp_root() as root, offline():
            stage_metadata(root, "testrel", "B05002.json")
            meta = metadata.load_release_metadata(root, RELEASE, ["B05002"])
        cell = meta.cell("B05002_013")
        self.assertEqual(cell.estimate_var, "B05002_013E")
        self.assertEqual(cell.moe_var, "B05002_013M")
        self.assertEqual(cell.estimate_annotation_var, "B05002_013EA")
        self.assertEqual(cell.moe_annotation_var, "B05002_013MA")
        self.assertEqual(cell.universe, "Total population")
        self.assertEqual(cell.label, "Total / Foreign-born")

    def test_an_unknown_cell_is_an_error_not_a_guess(self):
        with temp_root() as root, offline():
            stage_metadata(root, "testrel", "B05002.json")
            meta = metadata.load_release_metadata(root, RELEASE, ["B05002"])
            with self.assertRaises(metadata.MetadataError) as ctx:
                meta.cell("B05002_999")
        self.assertIn("never invented", str(ctx.exception))

    def test_uncached_metadata_tells_you_which_command_to_run(self):
        with temp_root() as root, offline():
            with self.assertRaises(metadata.MetadataError) as ctx:
                metadata.load_release_metadata(root, RELEASE, ["B05002"])
        self.assertIn("fetch metadata", str(ctx.exception))

    def test_measure_verification_rejects_a_missing_cell(self):
        with temp_root() as root, offline():
            stage_metadata(root, "testrel", "B05002.json")
            meta = metadata.load_release_metadata(root, RELEASE, ["B05002"])
            problems = metadata.verify_measures(meta, [GHOST])
        self.assertTrue(problems)
        self.assertIn("not present in release", problems[0])

    def test_measure_verification_rejects_mismatched_universes(self):
        with temp_root() as root, offline():
            stage_metadata(root, "testrel", "B05002.json")
            meta = metadata.load_release_metadata(root, RELEASE, ["B05002"])
            # The denominator's table is not cached at all, which is itself the
            # first failure the reviewer must see.
            problems = metadata.verify_measures(meta, [MIXED_UNIVERSE])
        self.assertTrue(problems)

    def test_variable_drift_between_releases_is_detected(self):
        with temp_root() as root, offline():
            stage_metadata(root, "testrel", "B05002.json")
            stage_metadata(root, "drifted", "B05002_drifted.json")
            a = metadata.load_release_metadata(root, RELEASE, ["B05002"])
            b = metadata.load_release_metadata(root, DRIFTED, ["B05002"])
        self.assertEqual(a.cell("B05002_013").label, "Total / Foreign-born")
        self.assertEqual(b.cell("B05002_013").label, "Total / Foreign born")
        self.assertTrue(a.has_cell("B05002_014"))
        self.assertFalse(b.has_cell("B05002_014"),
                         "a cell dropped by the newer release must not appear to exist")

    def test_good_measure_passes_verification(self):
        with temp_root() as root, offline():
            stage_metadata(root, "testrel", "B05002.json")
            meta = metadata.load_release_metadata(root, RELEASE, ["B05002"])
            self.assertEqual(metadata.verify_measures(meta, [SHARE]), [])


class AnnotationParserTests(unittest.TestCase):
    def test_parser_refuses_an_empty_extraction(self):
        with self.assertRaises(Exception) as ctx:
            reference.parse_annotation_values("<html><body>no table here</body></html>")
        self.assertIn("no annotation values parsed", str(ctx.exception))

    def test_parser_extracts_value_symbol_and_meaning(self):
        html = ("<table><tr><td>-555555555</td><td>*****</td>"
                "<td>A margin of error is not appropriate.</td></tr></table>")
        values = reference.parse_annotation_values(html)
        self.assertEqual(values["-555555555"]["symbol"], "*****")
        self.assertIn("not appropriate", values["-555555555"]["meaning"])


class ManifestTests(unittest.TestCase):
    def _manifest_with_artifact(self, root: Path, payload: bytes):
        rel = "data/raw/acs/testrel/summary_file/B05002.psv"
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        m = provenance.Manifest.new("t", "test", "live", root)
        m.add(provenance.RetrievalRecord(
            artifact_id="a", provider="p", kind="observations",
            source_url="https://example.invalid/x", request={},
            retrieved_at=provenance.utc_now(), http_status=200,
            content_bytes=len(payload), sha256=provenance.sha256_bytes(payload),
            cache_path=rel,
        ))
        return m, path

    def test_intact_cache_verifies(self):
        with temp_root() as root, offline():
            m, _ = self._manifest_with_artifact(root, b"GEO_ID|A\nx|1\n")
            self.assertEqual(m.verify(root), [])

    def test_corrupted_cache_is_detected_by_checksum(self):
        with temp_root() as root, offline():
            m, path = self._manifest_with_artifact(root, b"GEO_ID|A\nx|1\n")
            path.write_bytes(b"GEO_ID|A\nx|9\n")   # same length, different content
            problems = m.verify(root)
        self.assertEqual(len(problems), 1)
        self.assertIn("checksum mismatch", problems[0])

    def test_truncated_cache_is_detected_by_size_and_checksum(self):
        with temp_root() as root, offline():
            m, path = self._manifest_with_artifact(root, b"GEO_ID|A\nx|1\n")
            path.write_bytes(b"GEO_ID|A\n")
            problems = m.verify(root)
        self.assertTrue(any("size mismatch" in p for p in problems))

    def test_deleted_cache_is_detected(self):
        with temp_root() as root, offline():
            m, path = self._manifest_with_artifact(root, b"x")
            path.unlink()
            problems = m.verify(root)
        self.assertIn("missing cached artifact", problems[0])

    def test_manifest_round_trips_through_disk(self):
        with temp_root() as root, offline():
            m, _ = self._manifest_with_artifact(root, b"x")
            p = root / "m.json"
            m.save(p)
            again = provenance.Manifest.load(p)
        self.assertEqual(again.manifest_id, m.manifest_id)
        self.assertEqual(len(again.records), 1)
        self.assertEqual(again.schema_version, provenance.MANIFEST_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
