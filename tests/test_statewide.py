"""Statewide coverage, with New York City as a documented subset of it.

Every case here runs offline against a synthetic state: the five New York
City counties, Erie County and Suffolk County, a published state row, the
release's own geography roster, and boundary files carrying STATEFP,
COUNTYFP and NAMELSAD as the Census Bureau's do. None of these numbers is a
census finding. The live statewide build is checked separately, against the
real release, and reported as live evidence rather than asserted here.
"""

from __future__ import annotations

import copy
import json
import shutil
import unittest
from pathlib import Path

from census_explorer import (benchmark as benchmark_mod, config as config_mod,
                             dataset, geography, metadata, reconcile, server)
from tests.helpers import (offline, square, temp_root,
                           write_shapefile_zip_fields)
from tests.test_end_to_end import FIXTURES, PROJECT_JSON

NYC = {"005": "Bronx", "047": "Brooklyn (Kings County)",
       "061": "Manhattan (New York County)", "081": "Queens",
       "085": "Staten Island (Richmond County)"}
OFFICIAL_COUNTY_NAMES = {"005": "Bronx County", "047": "Kings County",
                         "061": "New York County", "081": "Queens County",
                         "085": "Richmond County", "029": "Erie County",
                         "103": "Suffolk County"}

#: (tract GEOID, total population, foreign born, has a table row, has a polygon)
TRACTS = [
    ("36005000100", 1000, 400, True, True),
    ("36047000100", 2000, 700, True, True),
    ("36061000100", 1500, 600, True, True),
    ("36081000100", 1800, 900, True, True),
    ("36085000100", 900, 150, True, True),
    ("36029016600", 1200, 100, True, True),       # Erie County
    ("36029990000", 0, 0, True, False),           # water: row, no polygon
    ("36103145601", 0, 0, False, True),           # listed, polygon, no row
    ("36103122405", 1100, 200, True, True),       # Suffolk County
]

MEASURES = {
    "schema_version": 1,
    "measures": [
        {"measure_id": "foreign_born_population", "label": "Foreign-born population",
         "concept": "Nativity", "unit": "persons", "kind": "count",
         "numerator_cells": ["B05002_013"], "denominator_cells": [],
         "universe_note": "Total population (B05002).",
         "definition_note": "Residents not U.S. citizens at birth.",
         "caveats": [], "topics": ["nativity"]},
        {"measure_id": "foreign_born_share", "label": "Foreign-born share",
         "concept": "Nativity", "unit": "percent", "kind": "share",
         "numerator_cells": ["B05002_013"], "denominator_cells": ["B05002_001"],
         "universe_note": "Total population (B05002).",
         "definition_note": "Foreign-born residents as a share of all residents.",
         "caveats": [], "topics": ["nativity"]},
    ],
}


def county_totals():
    totals: dict[str, list[int]] = {}
    for geoid, pop, fb, has_row, _ in TRACTS:
        if has_row:
            t = totals.setdefault(geoid[:5], [0, 0])
            t[0] += pop
            t[1] += fb
    return totals


def stage_state(root: Path, *, state_override=None, drop_county=None,
                extra_table_row=None, bad_membership=False,
                statewide=True) -> config_mod.ProjectConfig:
    project = copy.deepcopy(PROJECT_JSON)
    project["first_project"]["modern_counties"] = NYC
    project["explorer"]["geography_levels"] = ["county", "tract"]
    if statewide:
        project["explorer"]["study_area"] = {
            "id": "nys", "label": "New York State", "coverage": "state",
            "state_fips": "36", "cache_suffix": "state36"}
    cdir = root / "config"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "project.json").write_text(json.dumps(project), encoding="utf-8")
    (cdir / "measures.json").write_text(json.dumps(MEASURES), encoding="utf-8")

    meta_dir = root / "data/raw/metadata/testrel/groups"
    meta_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "metadata" / "B05002.json", meta_dir / "B05002.json")

    counties = county_totals()
    if drop_county:
        counties.pop(drop_county)
    state_pop = sum(v[0] for v in county_totals().values())
    state_fb = sum(v[1] for v in county_totals().values())
    if state_override:
        state_pop, state_fb = state_override

    def row(geo_id, pop, fb):
        return f"{geo_id}|{pop}|10|{fb}|5"

    lines = ["GEO_ID|B05002_E001|B05002_M001|B05002_E013|B05002_M013",
             row("0400000US36", state_pop, state_fb)]
    lines += [row(f"0500000US{g}", p, f) for g, (p, f) in sorted(counties.items())]
    lines += [row(f"1400000US{g}", p, f) for g, p, f, has_row, _ in TRACTS if has_row]
    if extra_table_row:
        lines.append(row(f"1400000US{extra_table_row}", 10, 1))
    obs = root / "data/raw/acs/testrel/summary_file"
    obs.mkdir(parents=True, exist_ok=True)
    (obs / "B05002_state36.psv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # The release's own list of what it publishes.
    header = "FILEID|STUSAB|SUMLEVEL|COMPONENT|STATE|GEO_ID|NAME"
    roster = [header, "ACSSF|NY|040|00|36|0400000US36|New York"]
    for key, name in sorted(OFFICIAL_COUNTY_NAMES.items()):
        roster.append(f"ACSSF|NY|050|00|36|0500000US36{key}|{name}, New York")
    for geoid, *_ in TRACTS:
        number = str(int(geoid[5:9])) + ("." + geoid[9:] if geoid[9:] != "00" else "")
        county = OFFICIAL_COUNTY_NAMES[geoid[2:5]]
        roster.append(f"ACSSF|NY|140|00|36|1400000US{geoid}|"
                      f"Census Tract {number}, {county}, New York")
    (obs / "Geos20235YR_state36.psv").write_text("\n".join(roster) + "\n",
                                                 encoding="utf-8")

    geo = root / "data/raw/geo/GENZ2023"
    x = -79.0
    county_rows = []
    for key, name in sorted(OFFICIAL_COUNTY_NAMES.items()):
        county_rows.append(({"GEOID": f"36{key}", "STATEFP": "36",
                             "COUNTYFP": key, "NAMELSAD": name}, square(x, 42.0)))
        x += 0.5
    write_shapefile_zip_fields(geo / "cb_2023_us_county_500k.zip", county_rows)
    tract_rows = []
    for i, (geoid, _p, _f, _row, has_poly) in enumerate(TRACTS):
        if not has_poly:
            continue
        county_fp = geoid[2:5]
        if bad_membership and geoid == "36029016600":
            county_fp = "103"          # says Suffolk; the GEOID says Erie
        number = str(int(geoid[5:9])) + ("." + geoid[9:] if geoid[9:] != "00" else "")
        tract_rows.append(({"GEOID": geoid, "STATEFP": "36", "COUNTYFP": county_fp,
                            "NAMELSAD": f"Census Tract {number}"},
                           square(-79.0 + i * 0.2, 41.0, 0.1)))
    write_shapefile_zip_fields(geo / "cb_2023_36_tract_500k.zip", tract_rows)
    return config_mod.load(cdir)


def build_state(root: Path, cfg) -> dataset.BuildResult:
    release = cfg.release("testrel")
    meta = metadata.load_release_metadata(root, release, cfg.all_tables())
    result = dataset.build(root, cfg, release, meta, ["county", "tract"],
                           "summary-file")
    dataset.attach_geography(root, cfg, release, result, ["county", "tract"])
    dataset.write_processed(root, cfg, release, meta, result, "fixture-manifest",
                            "live", "summary-file")
    return result


class _State(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_root()
        self.root = self._tmp.__enter__()
        self._offline = offline()
        self._offline.__enter__()

    def tearDown(self):
        self._offline.__exit__(None, None, None)
        self._tmp.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 1. Coverage is proved against the release's own roster
# ---------------------------------------------------------------------------

class CoverageTests(_State):
    def test_every_county_and_tract_the_release_lists_is_carried(self):
        cfg = stage_state(self.root)
        result = build_state(self.root, cfg)
        levels = result.coverage["levels"]
        self.assertEqual(levels["county"]["listed_by_release"], 7)
        self.assertEqual(levels["county"]["with_table_rows"], 7)
        self.assertEqual(levels["tract"]["listed_by_release"], len(TRACTS))
        self.assertEqual(levels["tract"]["with_table_rows"],
                         sum(1 for t in TRACTS if t[3]))
        self.assertEqual(levels["tract"]["listed_without_table_rows"],
                         ["36103145601"])
        tracts = {g for g, a in result.areas.items() if a.level == "tract"}
        self.assertEqual(tracts, {t[0] for t in TRACTS})

    def test_a_listed_tract_with_no_table_row_says_why_rather_than_not_retrieved(self):
        cfg = stage_state(self.root)
        result = build_state(self.root, cfg)
        v = result.values["foreign_born_share"]["36103145601"]
        self.assertEqual(v["estimate_status"], "unavailable")
        self.assertEqual(v["estimate_reason"], dataset.ROSTER_ONLY_REASON)
        self.assertNotIn("not retrieved", v["estimate_reason"])
        self.assertIsNone(v["estimate"], "an unpublished row must never read as zero")

    def test_a_table_row_the_release_does_not_list_stops_the_build(self):
        cfg = stage_state(self.root, extra_table_row="36029999999")
        with self.assertRaises(dataset.DatasetError) as caught:
            build_state(self.root, cfg)
        self.assertIn("not in the release's geography file", str(caught.exception))

    def test_statewide_coverage_without_a_roster_refuses_to_guess(self):
        cfg = stage_state(self.root)
        (self.root / "data/raw/acs/testrel/summary_file/Geos20235YR_state36.psv").unlink()
        with self.assertRaises(dataset.DatasetError) as caught:
            build_state(self.root, cfg)
        self.assertIn("fetch roster", str(caught.exception))

    def test_the_state_row_is_kept_for_reference_and_named_as_the_state(self):
        cfg = stage_state(self.root)
        result = build_state(self.root, cfg)
        self.assertEqual(result.areas["36"].level, "state")
        self.assertEqual(result.areas["36"].name, "New York State")

    def test_every_unmatched_join_is_reported_by_identifier(self):
        cfg = stage_state(self.root)
        result = build_state(self.root, cfg)
        tract = next(r for r in result.join_reports if r["level"] == "tract")
        self.assertEqual(tract["unmatched_observations"], ["36029990000"])
        self.assertEqual(tract["unmatched_features"], [])
        county = next(r for r in result.join_reports if r["level"] == "county")
        self.assertEqual(county["matched"], 7)

    def test_a_statewide_pull_never_overwrites_a_narrower_one(self):
        cfg = stage_state(self.root)
        path = dataset.summary_file_path(self.root, cfg.release("testrel"),
                                         "B05002", cfg)
        self.assertEqual(path.name, "B05002_state36.psv")


# ---------------------------------------------------------------------------
# 2. County membership is checked, not assumed
# ---------------------------------------------------------------------------

class MembershipTests(_State):
    def test_membership_is_checked_against_the_boundary_file_s_own_fields(self):
        cfg = stage_state(self.root)
        build_state(self.root, cfg)
        geo = json.loads((self.root / "data/processed/testrel/geography_tract.geojson")
                         .read_text("utf-8"))
        self.assertEqual(geo["metadata"]["county_membership_checked"],
                         sum(1 for t in TRACTS if t[4]))

    def test_a_tract_whose_geoid_and_county_fields_disagree_stops_the_build(self):
        cfg = stage_state(self.root, bad_membership=True)
        with self.assertRaises(geography.GeographyError) as caught:
            build_state(self.root, cfg)
        self.assertIn("36029016600", str(caught.exception))


# ---------------------------------------------------------------------------
# 3. County language statewide, borough names only for New York City
# ---------------------------------------------------------------------------

class NamingTests(_State):
    def setUp(self):
        super().setUp()
        self.result = build_state(self.root, stage_state(self.root))
        self.names = {g: a.name for g, a in self.result.areas.items()}

    def test_a_new_york_city_county_keeps_its_borough_name(self):
        self.assertEqual(self.names["36005"], "Bronx")
        self.assertEqual(self.names["36047"], "Brooklyn (Kings County)")

    def test_every_other_county_is_called_by_its_official_name(self):
        self.assertEqual(self.names["36029"], "Erie County")
        self.assertEqual(self.names["36103"], "Suffolk County")

    def test_a_tract_is_named_with_its_county(self):
        self.assertEqual(self.names["36029016600"], "Census Tract 166, Erie County")
        self.assertEqual(self.names["36005000100"], "Census Tract 1, Bronx")

    def test_a_tract_with_no_polygon_is_named_from_the_roster(self):
        self.assertEqual(self.names["36029990000"], "Census Tract 9900, Erie County")

    def test_no_area_is_left_with_a_bare_geoid_for_a_name(self):
        bare = sorted(g for g, n in self.names.items() if n == g)
        self.assertEqual(bare, [])


# ---------------------------------------------------------------------------
# 4. New York City stays exactly five counties
# ---------------------------------------------------------------------------

class NewYorkCityReferenceTests(_State):
    def setUp(self):
        super().setUp()
        self.cfg = stage_state(self.root)
        build_state(self.root, self.cfg)
        self.state = server.ServiceState(self.root)
        self.state.config = self.cfg

    def test_the_city_reference_is_built_from_its_five_counties_only(self):
        for level in ("county", "tract"):
            sel = server.build_selection(self.state, {
                "release_id": "testrel", "measure_id": "foreign_born_share",
                "level": level})
            bench = server.build_benchmark(self.state, sel, benchmark_mod.NYC)
            self.assertTrue(bench.available, bench.unavailable_reason)
            self.assertEqual(sorted(bench.components),
                             ["36005", "36047", "36061", "36081", "36085"])
            self.assertNotIn("36029", bench.components)
            self.assertNotIn("36103", bench.components)

    def test_the_city_value_is_its_five_counties_not_the_state(self):
        sel = server.build_selection(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_population",
            "level": "county"})
        bench = server.build_benchmark(self.state, sel, benchmark_mod.NYC)
        five = sum(v[1] for g, v in county_totals().items() if g[2:] in NYC)
        self.assertEqual(bench.estimate, five)
        state_fb = sum(v[1] for v in county_totals().values())
        self.assertNotEqual(bench.estimate, state_fb)

    def test_the_borough_list_is_still_the_five_documented_counties(self):
        self.assertEqual(self.cfg.borough_geoids,
                         ["36005", "36047", "36061", "36081", "36085"])
        self.assertEqual(self.cfg.borough_geoids, self.cfg.county_geoids)


# ---------------------------------------------------------------------------
# 5. County rows reconcile to the published state row
# ---------------------------------------------------------------------------

class StateReconciliationTests(_State):
    def run_state(self, cfg):
        return reconcile.run_state(self.root, cfg, cfg.release("testrel"))

    def reconciled(self, report, cell):
        return next(r for r in report["results"] if r["cell"] == cell)

    def test_counties_that_add_up_to_the_state_pass(self):
        cfg = stage_state(self.root)
        report = self.run_state(cfg)
        row = self.reconciled(report, "B05002_001")
        self.assertEqual(row["status"], "match")
        self.assertEqual(row["counties_summed"], 7)
        self.assertTrue(row["county_roster_matches_release"])

    def test_a_state_row_that_does_not_add_up_is_reported_with_its_size(self):
        cfg = stage_state(self.root, state_override=(10, 5))
        row = self.reconciled(self.run_state(cfg), "B05002_001")
        self.assertEqual(row["status"], "MISMATCH")
        self.assertNotEqual(row["difference"], 0)

    def test_a_short_county_roster_cannot_pass_by_summing_fewer_counties(self):
        # Suffolk's county row is missing, and the state row is set to what
        # the remaining six add up to. A sum over whatever rows happened to
        # arrive would call that a match.
        remaining = {g: v for g, v in county_totals().items() if g != "36103"}
        cfg = stage_state(self.root, drop_county="36103",
                          state_override=(sum(v[0] for v in remaining.values()),
                                          sum(v[1] for v in remaining.values())))
        report = self.run_state(cfg)
        row = self.reconciled(report, "B05002_001")
        self.assertFalse(row["county_roster_matches_release"])
        self.assertEqual(row["status"], "not comparable")
        self.assertFalse(report["passed"])


# ---------------------------------------------------------------------------
# 6. Configuration
# ---------------------------------------------------------------------------

class StudyAreaConfigTests(unittest.TestCase):
    def load_with(self, study):
        with temp_root() as root:
            project = copy.deepcopy(PROJECT_JSON)
            project["explorer"]["study_area"] = study
            cdir = root / "config"
            cdir.mkdir(parents=True)
            (cdir / "project.json").write_text(json.dumps(project), encoding="utf-8")
            (cdir / "measures.json").write_text(json.dumps(MEASURES), encoding="utf-8")
            return config_mod.load(cdir)

    def test_statewide_coverage_needs_its_own_cache_name(self):
        with self.assertRaises(config_mod.ConfigError):
            self.load_with({"coverage": "state", "state_fips": "36"})

    def test_an_unknown_coverage_is_refused(self):
        with self.assertRaises(config_mod.ConfigError):
            self.load_with({"coverage": "nation", "state_fips": "36",
                            "cache_suffix": "x"})

    def test_study_area_membership_follows_the_state_prefix(self):
        cfg = self.load_with({"coverage": "state", "state_fips": "36",
                              "cache_suffix": "state36"})
        self.assertTrue(cfg.in_study_area("36029", "county"))
        self.assertTrue(cfg.in_study_area("36103145601", "tract"))
        self.assertTrue(cfg.in_study_area("36", "state"))
        self.assertFalse(cfg.in_study_area("34003", "county"))
        self.assertFalse(cfg.in_study_area("09001", "county"))

    def test_a_project_without_a_study_area_keeps_its_counties(self):
        with temp_root() as root:
            project = copy.deepcopy(PROJECT_JSON)
            cdir = root / "config"
            cdir.mkdir(parents=True)
            (cdir / "project.json").write_text(json.dumps(project), encoding="utf-8")
            (cdir / "measures.json").write_text(json.dumps(MEASURES), encoding="utf-8")
            cfg = config_mod.load(cdir)
        self.assertFalse(cfg.statewide)
        self.assertEqual(cfg.cache_suffix, "")
        self.assertTrue(cfg.in_study_area("36005", "county"))
        self.assertFalse(cfg.in_study_area("36029", "county"))

    def test_only_new_york_city_counties_have_a_borough_alias(self):
        cfg = self.load_with({"coverage": "state", "state_fips": "36",
                              "cache_suffix": "state36"})
        self.assertIsNotNone(cfg.borough_alias("36005"))
        self.assertIsNone(cfg.borough_alias("36029"))
        self.assertIsNone(cfg.borough_alias("36059"))


if __name__ == "__main__":
    unittest.main()
