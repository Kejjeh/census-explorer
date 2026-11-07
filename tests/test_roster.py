"""The cached geography roster reads the same whatever its line endings.

A cache written or checked out on Windows ends its lines in CRLF. Every case
here writes exact bytes, so it exercises that path on any platform rather
than depending on the platform the tests happen to run on. None of these
rows is a census finding.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from census_explorer import dataset, provenance, reconcile, server
# Looked up on the module at call time: tests.test_redaction reloads this
# module to prove importing it touches no network, and a class imported by
# name before that reload is no longer the one the reader raises.
from census_explorer.retrieve import acs_summary_file as asf
from tests.helpers import offline, temp_root
from tests.test_statewide import TRACTS, build_state, stage_state

HEADER = b"FILEID|STUSAB|SUMLEVEL|COMPONENT|STATE|GEO_ID|NAME"
ROWS = [
    b"ACSSF|NY|040|00|36|0400000US36|New York",
    b"ACSSF|NY|050|00|36|0500000US36029|Erie County, New York",
    b"ACSSF|NY|140|00|36|1400000US36029016600|Census Tract 166, Erie County, New York",
    b"ACSSF|NY|160|00|36|1600000US3611000|Buffalo city, New York",
]
EXPECTED = {
    "0400000US36": {"level": "state", "name": "New York"},
    "0500000US36029": {"level": "county", "name": "Erie County, New York"},
    "1400000US36029016600": {"level": "tract",
                             "name": "Census Tract 166, Erie County, New York"},
}


class ReadRosterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_root()
        self.root = self._tmp.__enter__()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def write(self, body: bytes) -> Path:
        path = self.root / "roster.psv"
        path.write_bytes(body)
        return path

    def test_lf(self):
        self.assertEqual(asf.read_roster(self.write(b"\n".join([HEADER, *ROWS]) + b"\n")),
                         EXPECTED)

    def test_crlf(self):
        got = asf.read_roster(self.write(b"\r\n".join([HEADER, *ROWS]) + b"\r\n"))
        self.assertEqual(got, EXPECTED)
        self.assertFalse(any(v["name"].endswith("\r") for v in got.values()))

    def test_crlf_without_a_final_line_ending(self):
        self.assertEqual(asf.read_roster(self.write(b"\r\n".join([HEADER, *ROWS]))), EXPECTED)

    def test_mixed_line_endings(self):
        body = HEADER + b"\r\n" + ROWS[0] + b"\n" + b"\r\n".join(ROWS[1:]) + b"\n"
        self.assertEqual(asf.read_roster(self.write(body)), EXPECTED)

    def test_a_byte_order_mark_is_ignored(self):
        body = b"\xef\xbb\xbf" + b"\r\n".join([HEADER, *ROWS]) + b"\r\n"
        self.assertEqual(asf.read_roster(self.write(body)), EXPECTED)

    def test_reading_never_changes_the_cached_bytes(self):
        body = b"\r\n".join([HEADER, *ROWS]) + b"\r\n"
        path = self.write(body)
        before = provenance.sha256_bytes(path.read_bytes())
        asf.read_roster(path)
        self.assertEqual(provenance.sha256_bytes(path.read_bytes()), before)
        self.assertEqual(path.read_bytes(), body)

    def test_a_missing_column_is_named(self):
        header = b"FILEID|STUSAB|SUMLEVEL|COMPONENT|STATE|GEO_ID"
        rows = [r.rsplit(b"|", 1)[0] for r in ROWS]
        with self.assertRaises(asf.RosterError) as caught:
            asf.read_roster(self.write(b"\r\n".join([header, *rows]) + b"\r\n"))
        self.assertIn("NAME", str(caught.exception))

    def test_a_short_or_long_row_stops_the_read_with_its_line(self):
        for bad in (b"ACSSF|NY|050|00|36|0500000US36103",
                    b"ACSSF|NY|050|00|36|0500000US36103|Suffolk County|extra"):
            with self.subTest(bad=bad):
                with self.assertRaises(asf.RosterError) as caught:
                    asf.read_roster(self.write(b"\r\n".join([HEADER, *ROWS, bad]) + b"\r\n"))
                self.assertIn(":6:", str(caught.exception))

    def test_a_geography_listed_twice_is_refused(self):
        with self.assertRaises(asf.RosterError) as caught:
            asf.read_roster(self.write(b"\n".join([HEADER, *ROWS, ROWS[1]]) + b"\n"))
        self.assertIn("0500000US36029", str(caught.exception))

    def test_an_empty_file_is_refused(self):
        for body in (b"", b"\r\n"):
            with self.subTest(body=body):
                with self.assertRaises(asf.RosterError):
                    asf.read_roster(self.write(body))

    def test_a_repeated_header_column_is_refused(self):
        header = HEADER + b"|NAME"
        rows = [r + b"|x" for r in ROWS]
        with self.assertRaises(asf.RosterError):
            asf.read_roster(self.write(b"\n".join([header, *rows]) + b"\n"))


def to_crlf(root: Path) -> list[Path]:
    """Rewrite every staged text cache with CRLF endings, as Windows writes it."""
    changed = []
    for path in (root / "data/raw").rglob("*.psv"):
        body = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        path.write_bytes(body)
        changed.append(path)
    return changed


class CrlfBuildTests(unittest.TestCase):
    """The whole statewide path, from CRLF caches, on any platform."""

    def setUp(self):
        self._tmp = temp_root()
        self.root = self._tmp.__enter__()
        self._offline = offline()
        self._offline.__enter__()
        self.cfg = stage_state(self.root)
        self.converted = to_crlf(self.root)

    def tearDown(self):
        self._offline.__exit__(None, None, None)
        self._tmp.__exit__(None, None, None)

    def test_the_fixture_really_is_crlf(self):
        self.assertTrue(self.converted)
        for path in self.converted:
            self.assertIn(b"\r\n", path.read_bytes(), path)

    def test_a_crlf_cache_builds_the_same_coverage_as_lf(self):
        result = build_state(self.root, self.cfg)
        tract = result.coverage["levels"]["tract"]
        self.assertEqual(tract["listed_by_release"], len(TRACTS))
        self.assertEqual(tract["listed_without_table_rows"], ["36103145601"])
        names = {g: a.name for g, a in result.areas.items()}
        self.assertEqual(names["36029"], "Erie County")
        self.assertFalse(any("\r" in n for n in names.values()))

    def test_state_reconciliation_reads_a_crlf_roster(self):
        report = reconcile.run_state(self.root, self.cfg, self.cfg.release("testrel"))
        row = next(r for r in report["results"] if r["cell"] == "B05002_001")
        self.assertTrue(row["county_roster_matches_release"], row)
        self.assertEqual(row["status"], "match")
        self.assertEqual(row["counties_summed"], 7)

    def test_a_damaged_roster_stops_the_build_with_the_file_named(self):
        roster = dataset.roster_path(self.root, self.cfg.release("testrel"), self.cfg)
        roster.write_bytes(roster.read_bytes() + b"ACSSF|NY|050|00|36\r\n")
        with self.assertRaises(dataset.DatasetError) as caught:
            build_state(self.root, self.cfg)
        self.assertIn("roster could not be read", str(caught.exception))
        self.assertIn(roster.name, str(caught.exception))

    def test_the_service_serves_a_crlf_build(self):
        build_state(self.root, self.cfg)
        state = server.ServiceState(self.root)
        state.config = self.cfg
        sel = server.build_selection(state, {
            "release_id": "testrel", "measure_id": "foreign_born_share",
            "level": "tract", "scope": "county:36029"})
        self.assertEqual(sel.scope_label, "2 census tracts in Erie County")


if __name__ == "__main__":
    unittest.main()
