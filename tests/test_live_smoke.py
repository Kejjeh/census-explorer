"""Live network smoke test.

Skipped unless you opt in explicitly, because the rest of the suite must never
touch the network::

    # PowerShell
    $env:CENSUS_EXPLORER_LIVE = "1"; python -m unittest tests.test_live_smoke

    # POSIX shell
    CENSUS_EXPLORER_LIVE=1 python -m unittest tests.test_live_smoke

The keyless Summary File checks run with the flag alone.  The keyed API check
additionally needs ``CENSUS_API_KEY`` in the environment and is skipped without
one, so a machine with no credentials still reports a meaningful result rather
than a failure.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from census_explorer import config as config_mod, dataset, pipeline, provenance
from census_explorer.retrieve import acs_api, acs_metadata, acs_summary_file
from census_explorer.sentinels import classify
from tests.helpers import temp_root

LIVE = os.environ.get("CENSUS_EXPLORER_LIVE") == "1"


@unittest.skipUnless(LIVE, "set CENSUS_EXPLORER_LIVE=1 to run the live smoke test")
class LiveMetadataTests(unittest.TestCase):
    def test_table_metadata_is_reachable_without_a_credential(self):
        cfg = config_mod.load()
        release = cfg.release(cfg.raw["explorer"]["default_release"])
        with temp_root() as root:
            manifest = provenance.Manifest.new("live", "smoke", "live", root)
            path = acs_metadata.fetch_group(root, release, "B05002", manifest)
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 1000)
            self.assertEqual(len(manifest.records), 1)
            self.assertNotIn("key=", manifest.records[0]["source_url"])


@unittest.skipUnless(LIVE, "set CENSUS_EXPLORER_LIVE=1 to run the live smoke test")
class LiveSummaryFileTests(unittest.TestCase):
    def test_five_boroughs_return_a_usable_population_estimate(self):
        cfg = config_mod.load()
        release = cfg.release(cfg.raw["explorer"]["default_release"])
        with temp_root() as root:
            manifest = provenance.Manifest.new("live", "smoke", "live", root)
            path = acs_summary_file.fetch_table(
                root, release, "B01003", [("county", cfg.county_geoids)], manifest)
            rows = dataset.read_summary_file(path)
            record = manifest.records[0]
            self.assertTrue(record["upstream_full_sha256"])
            self.assertGreater(record["upstream_full_bytes"], 0)
            self.assertEqual(manifest.verify(root), [])
        for geoid in cfg.county_geoids:
            cell = classify(rows[f"0500000US{geoid}"]["B01003_001"][0])
            self.assertTrue(cell.is_number, geoid)
            self.assertGreater(cell.value, 100_000, geoid)


@unittest.skipUnless(LIVE and acs_api.has_api_key(),
                     "needs CENSUS_EXPLORER_LIVE=1 and CENSUS_API_KEY")
class LiveApiTests(unittest.TestCase):
    def test_keyed_api_returns_the_same_borough_totals_as_the_summary_file(self):
        cfg = config_mod.load()
        release = cfg.release(cfg.raw["explorer"]["default_release"])
        with temp_root() as root:
            manifest = provenance.Manifest.new("live", "smoke", "live", root)
            api_paths = acs_api.fetch_variables(
                root, release, "B01003", "county",
                ["B01003_001E", "B01003_001M"],
                acs_api.geography_params("county", cfg.state_fips), manifest)
            sf_path = acs_summary_file.fetch_table(
                root, release, "B01003", [("county", cfg.county_geoids)], manifest)
            api_rows = dataset.read_api_response(api_paths[0])
            sf_rows = dataset.read_summary_file(sf_path)

            key = acs_api.api_key()
            for record in manifest.records:
                self.assertNotIn(key, str(record))
                self.assertNotIn("key=", record["source_url"])

        for geoid in cfg.county_geoids:
            gid = f"0500000US{geoid}"
            self.assertEqual(
                classify(api_rows[gid]["B01003_001"][0]).value,
                classify(sf_rows[gid]["B01003_001"][0]).value,
                f"transports disagree for {geoid}")


if __name__ == "__main__":
    unittest.main()
