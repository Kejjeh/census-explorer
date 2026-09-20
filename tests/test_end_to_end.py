"""End-to-end: fixture cache -> build -> local service -> project replay -> export.

This exercises the real code paths the browser uses, over real HTTP against the
real handler, with the network switched off for the whole test.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from census_explorer import (config as config_mod, dataset, exports, figures,
                             metadata, pipeline, projects, provenance, server)
from tests.helpers import FIXTURES, offline, square, temp_root, write_shapefile_zip

COUNTIES = {"005": "Bronx", "047": "Brooklyn (Kings County)",
            "061": "Manhattan (New York County)", "081": "Queens",
            "085": "Staten Island (Richmond County)"}

PROJECT_JSON = {
    "schema_version": 1, "name": "test", "status": "test", "execution_mode": "local",
    "first_project": {"id": "t", "modern_state_fips": "36", "modern_counties": COUNTIES},
    "explorer": {
        "default_release": "testrel", "comparison_release": "", 
        "geography_levels": ["county"],
        "releases": {
            "testrel": {
                "provider": "US Census Bureau", "dataset": "acs/acs5", "vintage": 2023,
                "period_start": 2019, "period_end": 2023,
                "period_label": "2019-2023 ACS",
                "product_label": "American Community Survey 5-year estimates",
                "boundary_release": "GENZ2023",
                "geography_vintage": "2023 cartographic boundary files (1:500,000)",
                "api_base": "", "summary_file_base": "",
                "citation": "U.S. Census Bureau, 2019-2023 ACS 5-Year Estimates.",
            },
        },
    },
}

MEASURES_JSON = {
    "schema_version": 1,
    "measures": [
        {"measure_id": "foreign_born_population", "label": "Foreign-born population",
         "concept": "Nativity", "unit": "persons", "kind": "count",
         "numerator_cells": ["B05002_013"], "denominator_cells": [],
         "universe_note": "Total population (B05002).",
         "definition_note": "Residents not U.S. citizens at birth.",
         "caveats": ["A birthplace stock, not a flow."], "topics": ["nativity"]},
        {"measure_id": "foreign_born_share", "label": "Foreign-born share of population",
         "concept": "Nativity", "unit": "percent", "kind": "share",
         "numerator_cells": ["B05002_013"], "denominator_cells": ["B05002_001"],
         "universe_note": "Total population (B05002).",
         "definition_note": "Foreign-born residents as a percentage of all residents.",
         "caveats": ["NY-born is not NYC-born."], "topics": ["nativity"]},
    ],
}


def stage_project(root: Path) -> config_mod.ProjectConfig:
    """Write a self-contained fixture project and cache into a temporary root."""
    cdir = root / "config"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "project.json").write_text(json.dumps(PROJECT_JSON), encoding="utf-8")
    (cdir / "measures.json").write_text(json.dumps(MEASURES_JSON), encoding="utf-8")

    meta_dir = root / "data/raw/metadata/testrel/groups"
    meta_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "metadata" / "B05002.json", meta_dir / "B05002.json")

    obs_dir = root / "data/raw/acs/testrel/summary_file"
    obs_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "summary_file" / "B05002.psv", obs_dir / "B05002.psv")

    geo_dir = root / "data/raw/geo/GENZ2023"
    geo_dir.mkdir(parents=True, exist_ok=True)
    write_shapefile_zip(
        geo_dir / "cb_2023_us_county_500k.zip",
        [("36005", square(-73.90, 40.84)), ("36047", square(-73.95, 40.65)),
         ("36061", square(-73.97, 40.78)), ("36081", square(-73.80, 40.70)),
         # 36085 is deliberately absent: the join must report it, not drop it.
         ("36999", square(-75.00, 41.00))])
    return config_mod.load(cdir)


def build(root: Path, cfg: config_mod.ProjectConfig):
    release = cfg.release("testrel")
    meta = metadata.load_release_metadata(root, release, cfg.all_tables())
    result = dataset.build(root, cfg, release, meta, ["county"], "summary-file")
    dataset.add_cross_table_diagnostics(root, release, result, "summary-file")
    dataset.attach_geography(root, cfg, release, result, ["county"])
    dataset.write_processed(root, cfg, release, meta, result, "fixture-manifest",
                            "live", "summary-file")
    return result


class BuildTests(unittest.TestCase):
    def test_build_produces_the_expected_values_and_reports_the_join(self):
        with temp_root() as root, offline():
            cfg = stage_project(root)
            result = build(root, cfg)

            share = result.values["foreign_born_share"]
            # 300 / 1000 = 30%
            self.assertAlmostEqual(share["36005"]["estimate"], 30.0)
            # Zero denominator: undefined, not zero.
            self.assertEqual(share["36061"]["estimate_status"], "unavailable")
            self.assertIn("undefined, not zero", share["36061"]["estimate_reason"])
            # Fully annotated row: unavailable.
            self.assertEqual(share["36081"]["estimate_status"], "unavailable")

            join = [j for j in result.join_reports if j["level"] == "county"][0]
            self.assertEqual(join["matched"], 4)
            self.assertEqual(join["unmatched_observations"], ["36085"])
            self.assertFalse(join["complete"])
            self.assertIsNone(join["unmatched_observation_population"],
                              "population is unknown here and must not be reported as 0")

    def test_areas_get_their_published_names(self):
        with temp_root() as root, offline():
            cfg = stage_project(root)
            result = build(root, cfg)
        self.assertEqual(result.areas["36005"].name, "Bronx")

    def test_dataset_json_keeps_units_universes_and_codes(self):
        with temp_root() as root, offline():
            cfg = stage_project(root)
            build(root, cfg)
            doc = json.loads((root / "data/processed/testrel/dataset.json").read_text())
        m = [x for x in doc["measures"] if x["measure_id"] == "foreign_born_share"][0]
        self.assertEqual(m["unit"], "percent")
        self.assertEqual(m["universe_published"], ["Total population"])
        self.assertEqual([c["cell"] for c in m["cells"]], ["B05002_013", "B05002_001"])
        self.assertEqual(doc["release"]["period_label"], "2019-2023 ACS")

    def test_county_csv_marks_unavailable_values_without_using_zero(self):
        with temp_root() as root, offline():
            cfg = stage_project(root)
            build(root, cfg)
            text = (root / "data/processed/testrel/observations_county.csv").read_text()
        rows = [r for r in text.splitlines() if ",36061," in r and "foreign_born_share" in r]
        self.assertTrue(rows)
        self.assertIn(",unavailable,", rows[0])
        self.assertNotIn(",0,unavailable", rows[0])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_root()
        self.root = self._tmp.__enter__()
        self._offline = offline()
        self._offline.__enter__()
        self.cfg = stage_project(self.root)
        build(self.root, self.cfg)
        state = server.ServiceState(self.root)
        state.config = self.cfg
        handler = type("H", (server.Handler,), {"state": state})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.state = state

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._offline.__exit__(None, None, None)
        self._tmp.__exit__(None, None, None)

    def get(self, path, raw=False):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{self.port}"})
        # urlopen here is a loopback call to our own handler, not provider access.
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
        return body if raw else json.loads(body)

    def post(self, path, payload):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{self.port}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def test_status_reports_the_period_label_and_the_offline_guarantee(self):
        doc = self.get("/api/status")
        self.assertEqual(doc["releases"][0]["period_label"], "2019-2023 ACS")
        self.assertTrue(doc["offline"], "the service must have network access disabled")
        self.assertIn("census.gov", doc["annotation_reference"]["source_url"])

    def test_no_endpoint_leaks_a_configured_credential(self):
        secret = "SECRET-KEY-VALUE-abcdef0123456789"
        with unittest.mock.patch.dict(os.environ, {"CENSUS_API_KEY": secret}):
            bodies = [
                self.get("/api/status", raw=True),
                self.get("/api/dataset?release=testrel", raw=True),
                self.get("/api/values?release=testrel&measure=foreign_born_share", raw=True),
                self.get("/api/geography?release=testrel&level=county", raw=True),
                self.get("/api/figure?kind=map&release=testrel&level=county"
                         "&measure=foreign_born_share", raw=True),
                self.get("/", raw=True),
            ]
            result = self.post("/api/export", {
                "release_id": "testrel", "measure_id": "foreign_born_share",
                "level": "county"})
            out = self.root / result["export_dir"]
            bodies.append((out / "provenance.json").read_bytes())
            bodies.append((out / "data.csv").read_bytes())
        for body in bodies:
            self.assertNotIn(secret.encode("utf-8"), body)

    def test_index_page_is_served(self):
        body = self.get("/", raw=True)
        self.assertIn(b"Census Explorer", body)

    def test_path_traversal_is_refused(self):
        with self.assertRaises(Exception):
            self.get("/../config/project.json", raw=True)

    def test_values_endpoint_carries_status_not_just_numbers(self):
        doc = self.get("/api/values?release=testrel&measure=foreign_born_share")
        self.assertEqual(doc["values"]["36005"]["es"], "ok")
        self.assertEqual(doc["values"]["36061"]["es"], "unavailable")
        self.assertIsNone(doc["values"]["36061"]["e"])

    def test_geography_endpoint_declares_its_vintage(self):
        doc = self.get("/api/geography?release=testrel&level=county")
        self.assertEqual(doc["metadata"]["boundary_release"], "GENZ2023")
        self.assertFalse(doc["metadata"]["synthetic"])

    def test_map_figure_shows_missing_areas_as_no_data(self):
        svg = self.get(
            "/api/figure?kind=map&release=testrel&level=county&measure=foreign_born_share",
            raw=True).decode("utf-8")
        self.assertIn("no usable estimate", svg)
        self.assertIn("2019-2023 ACS", svg)
        self.assertNotIn("2020 census", svg)

    def test_chart_figure_draws_a_gap_for_an_unavailable_value(self):
        svg = self.get(
            "/api/figure?kind=chart&release=testrel&level=county&measure=foreign_born_share",
            raw=True).decode("utf-8")
        self.assertIn("stroke-dasharray", svg)
        self.assertIn("margin of error", svg)

    def test_project_saves_pins_a_manifest_and_replays(self):
        saved = self.post("/api/projects", {
            "project_id": "replay-test", "release_id": "testrel", "level": "county",
            "measure_id": "foreign_born_share", "title": "Replay"})
        self.assertEqual(saved["saved"], "replay-test")

        listing = self.get("/api/projects")["projects"]
        self.assertEqual(listing[0]["project_id"], "replay-test")

        reopened = self.get("/api/project?id=replay-test")
        self.assertEqual(reopened["measure_id"], "foreign_born_share")
        self.assertEqual(reopened["manifest_ids"], ["fixture-manifest"])
        self.assertEqual(reopened["requested"]["release_id"], "testrel")

        # Replaying the saved definition must reproduce the same numbers.
        before = self.get("/api/values?release=testrel&measure=foreign_born_share")["values"]
        again = self.get(
            f"/api/values?release={reopened['release_id']}"
            f"&measure={reopened['measure_id']}")["values"]
        self.assertEqual(before, again)

    def test_export_bundle_has_data_provenance_figure_and_readme(self):
        result = self.post("/api/export", {
            "release_id": "testrel", "measure_id": "foreign_born_share",
            "level": "county", "figure_kind": "map"})
        out = self.root / result["export_dir"]
        self.assertTrue((out / "data.csv").exists())
        self.assertTrue((out / "provenance.json").exists())
        self.assertTrue((out / "figure.svg").exists())
        self.assertTrue((out / "README.txt").exists())

        csv_text = (out / "data.csv").read_text(encoding="utf-8")
        header = csv_text.splitlines()[0]
        for column in ("period_label", "universe", "moe_90pct", "estimate_status",
                       "geography_vintage", "numerator_cells", "denominator_cells"):
            self.assertIn(column, header)
        self.assertIn("2019-2023 ACS", csv_text)

        prov = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(prov["releases"][0]["period_label"], "2019-2023 ACS")
        self.assertTrue(prov["measures"][0]["universe_note"])
        self.assertIn("never a zero", prov["reading_the_columns"]["estimate"])

    def test_export_unavailable_value_is_blank_with_a_reason(self):
        result = self.post("/api/export", {
            "release_id": "testrel", "measure_id": "foreign_born_share",
            "level": "county", "include_figure": False})
        csv_text = (self.root / result["export_dir"] / "data.csv").read_text()
        row = [r for r in csv_text.splitlines() if ",36061," in r][0]
        fields = row.split(",")
        self.assertIn("unavailable", row)
        self.assertNotIn("0.0", fields[12:14])

    def test_unknown_measure_is_a_clean_404(self):
        try:
            self.get("/api/values?release=testrel&measure=not_a_measure")
            self.fail("expected an error")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)
            self.assertIn("not in this dataset", json.loads(exc.read())["error"])


class FigureTests(unittest.TestCase):
    def test_quantile_cuts_ignore_missing_values(self):
        self.assertEqual(figures.class_of(None, [1, 2, 3]), None)
        self.assertEqual(figures.class_of(0.5, [1, 2, 3]), 0)
        self.assertEqual(figures.class_of(9.0, [1, 2, 3]), 3)

    def test_formatting_states_the_unit(self):
        self.assertEqual(figures.fmt(12.345, "percent"), "12.3%")
        self.assertEqual(figures.fmt(1234, "persons"), "1,234")
        self.assertEqual(figures.fmt(None, "persons"), "no data")


if __name__ == "__main__":
    unittest.main()
