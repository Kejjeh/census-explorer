"""Regression tests for the defects found in the review of PR #1.

Each test in this module was written to fail against commit 31e00ac and to
pass once the corresponding defect is fixed.  They are kept together so the
specific failures stay reproducible rather than being diffused into the
suites they also belong to.
"""

from __future__ import annotations

import json
import math
import shutil
import unittest
from pathlib import Path

from census_explorer import (compare, exports, geography, metadata, projects,
                             server, snapshot)
from census_explorer.config import MeasureDef, Release
from census_explorer.sentinels import classify
from tests.helpers import FIXTURES, offline, square, temp_root, write_shapefile_zip
from tests.test_end_to_end import build, stage_project

# ---------------------------------------------------------------------------
# A two-release fixture project, for the checks that need a comparison
# ---------------------------------------------------------------------------

def stage_two_release_project(root: Path):
    """Two releases of one table, the second missing a cell the first has.

    `foreign_born_share` is comparable across both. `naturalized_share` depends
    on B05002_014, whose published label changed substantively in the second
    release, so it is not.
    """
    import census_explorer.config as config_mod
    from tests.test_end_to_end import MEASURES_JSON, PROJECT_JSON

    project = json.loads(json.dumps(PROJECT_JSON))
    releases = project["explorer"]["releases"]
    releases["testrel2"] = json.loads(json.dumps(releases["testrel"]))
    releases["testrel2"].update({
        "vintage": 2022, "period_start": 2018, "period_end": 2022,
        "period_label": "2018-2022 ACS",
        "citation": "U.S. Census Bureau, 2018-2022 ACS 5-Year Estimates.",
    })
    project["explorer"]["comparison_release"] = "testrel2"

    measures = json.loads(json.dumps(MEASURES_JSON))
    measures["measures"].append({
        "measure_id": "naturalized_share",
        "label": "Naturalized citizens, share of the foreign-born population",
        "concept": "Citizenship", "unit": "percent", "kind": "share",
        "numerator_cells": ["B05002_014"], "denominator_cells": ["B05002_013"],
        "universe_note": "Foreign-born population (B05002).",
        "definition_note": "Foreign-born residents who have naturalized.",
        "caveats": [], "topics": ["citizenship"],
    })

    cdir = root / "config"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "project.json").write_text(json.dumps(project), encoding="utf-8")
    (cdir / "measures.json").write_text(json.dumps(measures), encoding="utf-8")

    base = json.loads((FIXTURES / "metadata" / "B05002.json").read_text(encoding="utf-8"))
    drifted = json.loads(json.dumps(base))
    for suffix in ("E", "M", "EA", "MA"):
        drifted["variables"][f"B05002_014{suffix}"]["label"] = (
            "Estimate!!Total:!!Foreign-born:!!Some entirely different category")

    for rid, doc in (("testrel", base), ("testrel2", drifted)):
        d = root / f"data/raw/metadata/{rid}/groups"
        d.mkdir(parents=True, exist_ok=True)
        (d / "B05002.json").write_text(json.dumps(doc), encoding="utf-8")
        o = root / f"data/raw/acs/{rid}/summary_file"
        o.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "summary_file" / "B05002.psv", o / "B05002.psv")

    geo = root / "data/raw/geo/GENZ2023"
    geo.mkdir(parents=True, exist_ok=True)
    write_shapefile_zip(
        geo / "cb_2023_us_county_500k.zip",
        [("36005", square(-73.90, 40.84)), ("36047", square(-73.95, 40.65)),
         ("36061", square(-73.97, 40.78)), ("36081", square(-73.80, 40.70)),
         ("36085", square(-74.15, 40.58))])
    return config_mod.load(cdir)


def build_release(root: Path, cfg, release_id: str):
    from census_explorer import dataset as dataset_mod
    release = cfg.release(release_id)
    tables = ["B05002"]
    meta = metadata.load_release_metadata(root, release, tables)
    result = dataset_mod.build(root, cfg, release, meta, ["county"], "summary-file")
    dataset_mod.attach_geography(root, cfg, release, result, ["county"])
    dataset_mod.write_processed(root, cfg, release, meta, result,
                                f"{release_id}-manifest", "live", "summary-file")
    return result


SHARE = MeasureDef(
    measure_id="s", label="s", concept="c", unit="percent", kind="share",
    numerator_cells=["B05002_013"], denominator_cells=["B05002_001"],
    universe_note="u", definition_note="d",
)


def release(rid, start, end, label, boundary="GENZ2023", dataset="acs/acs5"):
    return Release(
        release_id=rid, provider="p", dataset=dataset, vintage=end,
        period_start=start, period_end=end, period_label=label, product_label="p",
        boundary_release=boundary, geography_vintage=f"{boundary} test",
        api_base="", summary_file_base="", citation="c")


A = release("acs5_2023", 2019, 2023, "2019-2023 ACS", "GENZ2023")
B = release("acs5_2022", 2018, 2022, "2018-2022 ACS", "GENZ2022")
SAME_VINTAGE_B = release("acs5_2022b", 2018, 2022, "2018-2022 ACS", "GENZ2023")


# ---------------------------------------------------------------------------
# Defect 1: saved-project pinning was descriptive only
# ---------------------------------------------------------------------------

class PinningTests(unittest.TestCase):
    """A saved project must reproduce its figure or refuse to open."""

    def setUp(self):
        self._tmp = temp_root()
        self.root = self._tmp.__enter__()
        self._offline = offline()
        self._offline.__enter__()
        self.cfg = stage_project(self.root)
        build(self.root, self.cfg)
        self.state = server.ServiceState(self.root)
        self.state.config = self.cfg

    def tearDown(self):
        self._offline.__exit__(None, None, None)
        self._tmp.__exit__(None, None, None)

    def save(self, project_id="p", **over):
        payload = {
            "project_id": project_id, "title": "t", "release_id": "testrel",
            "level": "county", "measure_id": "foreign_born_share",
        }
        payload.update(over)
        return server.save_project(self.state, payload)

    def values_path(self):
        return self.root / "data/processed/testrel/values/foreign_born_share.json"

    def tamper_values(self, geoid="36005", estimate=99.0):
        path = self.values_path()
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["values"][geoid]["e"] = estimate
        path.write_text(json.dumps(doc), encoding="utf-8")

    def test_exact_replay_succeeds_and_returns_the_saved_numbers(self):
        self.save()
        fresh = server.ServiceState(self.root)
        fresh.config = self.cfg
        replay = server.replay_project(fresh, "p")
        self.assertAlmostEqual(replay["values"]["testrel"]["foreign_born_share"]["36005"]["e"],
                               30.0)
        self.assertTrue(replay["pin"]["verified"])

    def test_changed_values_block_replay_with_an_actionable_message(self):
        self.save()
        self.tamper_values()
        fresh = server.ServiceState(self.root)
        fresh.config = self.cfg
        with self.assertRaises(snapshot.SnapshotMismatch) as ctx:
            server.replay_project(fresh, "p")
        message = str(ctx.exception)
        self.assertIn("values/foreign_born_share.json", message)
        self.assertIn("changed since the project was saved", message)
        self.assertIn("re-save", message)

    def test_same_identifiers_but_changed_content_still_blocks(self):
        """The manifest id is unchanged; only the bytes moved."""
        self.save()
        before = projects.load(self.root, "p")
        self.tamper_values()
        after_manifests = projects.load(self.root, "p").manifest_ids
        self.assertEqual(before.manifest_ids, after_manifests,
                         "this test is only meaningful if the ids are identical")
        fresh = server.ServiceState(self.root)
        fresh.config = self.cfg
        with self.assertRaises(snapshot.SnapshotMismatch):
            server.replay_project(fresh, "p")

    def test_changed_measure_definition_blocks_replay(self):
        self.save()
        path = self.root / "data/processed/testrel/dataset.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        for m in doc["measures"]:
            if m["measure_id"] == "foreign_born_share":
                m["denominator_cells"] = ["B05002_002"]
        path.write_text(json.dumps(doc), encoding="utf-8")
        fresh = server.ServiceState(self.root)
        fresh.config = self.cfg
        with self.assertRaises(snapshot.SnapshotMismatch) as ctx:
            server.replay_project(fresh, "p")
        self.assertIn("dataset.json", str(ctx.exception))

    def test_changed_geometry_blocks_replay(self):
        self.save()
        path = self.root / "data/processed/testrel/geography_county.geojson"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["features"][0]["geometry"]["coordinates"][0][0][0] += 0.5
        path.write_text(json.dumps(doc), encoding="utf-8")
        fresh = server.ServiceState(self.root)
        fresh.config = self.cfg
        with self.assertRaises(snapshot.SnapshotMismatch) as ctx:
            server.replay_project(fresh, "p")
        self.assertIn("geography_county.geojson", str(ctx.exception))

    def test_missing_input_blocks_replay_and_never_substitutes(self):
        self.save()
        self.values_path().unlink()
        fresh = server.ServiceState(self.root)
        fresh.config = self.cfg
        with self.assertRaises(snapshot.SnapshotMismatch) as ctx:
            server.replay_project(fresh, "p")
        self.assertIn("is missing", str(ctx.exception))

    def test_replay_uses_the_pinned_definition_not_the_current_catalog(self):
        self.save()
        # Change the live catalog out from under the project.
        self.cfg.measures["foreign_born_share"] = MeasureDef(
            measure_id="foreign_born_share", label="RELABELLED", concept="c",
            unit="percent", kind="share", numerator_cells=["B05002_003"],
            denominator_cells=["B05002_001"], universe_note="u", definition_note="d")
        fresh = server.ServiceState(self.root)
        fresh.config = self.cfg
        replay = server.replay_project(fresh, "p")
        self.assertEqual(replay["selection"]["measures"][0]["label"],
                         "Foreign-born share of population")
        self.assertEqual(replay["selection"]["measures"][0]["denominator_cells"],
                         ["B05002_001"])

    def test_export_of_a_tampered_project_is_refused(self):
        self.save()
        self.tamper_values()
        fresh = server.ServiceState(self.root)
        fresh.config = self.cfg
        with self.assertRaises(snapshot.SnapshotMismatch):
            server.run_export(fresh, {"project_id": "p"})

    def test_provenance_lists_only_the_inputs_actually_used(self):
        self.save()
        result = server.run_export(self.state, {"project_id": "p"})
        prov = json.loads(
            (self.root / result["export_dir"] / "provenance.json").read_text(encoding="utf-8"))
        paths = {i["path"] for i in prov["inputs"]}
        self.assertIn("data/processed/testrel/values/foreign_born_share.json", paths)
        self.assertNotIn("data/processed/testrel/values/foreign_born_population.json", paths,
                         "a measure that was not exported must not appear as an input")
        for entry in prov["inputs"]:
            self.assertEqual(len(entry["sha256"]), 64)


# ---------------------------------------------------------------------------
# Defect 2: boundary vintage was passed on identifier overlap alone
# ---------------------------------------------------------------------------

class BoundaryEvidenceTests(unittest.TestCase):
    def test_cross_vintage_comparison_without_evidence_is_blocked(self):
        report = compare.check(A, B, "tract", {"36005000100"}, {"36005000100"},
                               measure=SHARE, meta_a=None, meta_b=None)
        self.assertFalse(report.allowed)
        self.assertTrue(
            any("boundary vintage" in b.lower() for b in report.blocking),
            f"expected a boundary-vintage block, got {report.blocking}")

    def test_same_vintage_needs_no_extra_evidence(self):
        evidence = compare.GeographyEvidence.same_vintage("tract", "GENZ2023")
        report = compare.check(A, SAME_VINTAGE_B, "tract", {"36005000100"},
                               {"36005000100"}, geography_evidence=evidence)
        self.assertFalse(any("boundary vintage" in b.lower() for b in report.blocking))

    def test_evidence_for_the_wrong_level_does_not_count(self):
        evidence = compare.GeographyEvidence(
            kind=geography.PROVIDER_CORRESPONDENCE, established=True, level="county",
            boundary_release_a="GENZ2023", boundary_release_b="GENZ2022",
            detail="county correspondence recorded")
        report = compare.check(A, B, "tract", {"36005000100"}, {"36005000100"},
                               geography_evidence=evidence)
        self.assertFalse(report.allowed)
        self.assertTrue(any("tract" in b for b in report.blocking))

    def test_a_footprint_comparison_never_establishes_equivalence_by_itself(self):
        """Measuring that two polygons look alike is not knowing they are the same area."""
        ring = square(-73.9, 40.8, 0.10)
        a = {"36005000100": _feature("36005000100", ring)}
        b = {"36005000100": _feature("36005000100", list(ring))}
        evidence = geography.footprint_comparison(
            a, b, "tract", "GENZ2023", "GENZ2022")
        self.assertFalse(evidence.established,
                         "identical polygons still do not establish equivalence")
        self.assertEqual(evidence.measurements["agreeing_area_count"], 1)
        self.assertIn("not an establishment of equivalence", evidence.detail)

    def test_equal_area_and_centroid_do_not_hide_a_different_footprint(self):
        """A square and an equal-area diamond share area and centroid.

        Summary statistics alone would call these the same area. The boundary
        distance is what reflects the footprint, and it does not.
        """
        cx, cy, s = -74.0, 40.7, 0.02
        square_ring = [(cx - s, cy - s), (cx - s, cy + s),
                       (cx + s, cy + s), (cx + s, cy - s)]
        d = s * math.sqrt(2)
        diamond_ring = [(cx, cy - d), (cx + d, cy), (cx, cy + d), (cx - d, cy)]
        a = {"36005000100": _feature("36005000100", square_ring)}
        b = {"36005000100": _feature("36005000100", diamond_ring)}

        m = geography.footprint_disagreement(
            a["36005000100"]["geometry"], b["36005000100"]["geometry"], 25.0)
        self.assertLess(m["relative_area_difference"], 1e-6,
                        "fixture precondition: the areas match")
        self.assertLess(m["centroid_shift_metres"], 1.0,
                        "fixture precondition: the centroids match")
        self.assertGreater(m["boundary_distance_metres"], 100.0,
                           "the footprints plainly differ and the measure must say so")

        evidence = geography.footprint_comparison(
            a, b, "tract", "GENZ2023", "GENZ2022")
        self.assertFalse(evidence.established)
        self.assertIn("36005000100", evidence.failing_areas)

    def test_a_computed_comparison_cannot_unblock_a_cross_vintage_comparison(self):
        ring = square(-73.9, 40.8, 0.10)
        a = {"36005000100": _feature("36005000100", ring)}
        b = {"36005000100": _feature("36005000100", list(ring))}
        evidence = geography.footprint_comparison(
            a, b, "tract", "GENZ2023", "GENZ2022")
        report = compare.check(A, B, "tract", {"36005000100"}, {"36005000100"},
                               measure=SHARE, geography_evidence=evidence)
        self.assertFalse(report.allowed)
        self.assertEqual(report.comparable_geoids, [])

    def test_recorded_provider_correspondence_does_establish_equivalence(self):
        evidence = geography.GeographyEvidence(
            kind=geography.PROVIDER_CORRESPONDENCE, established=True, level="tract",
            boundary_release_a="GENZ2023", boundary_release_b="GENZ2022",
            detail="provider states the areas correspond", established_areas=None)
        report = compare.check(A, B, "tract", {"36005000100"}, {"36005000100"},
                               geography_evidence=evidence)
        self.assertNotIn("boundary vintage",
                         " ".join(report.blocking).lower())

    def test_the_shipped_configuration_records_no_equivalence(self):
        """The repository must not ship an invented review."""
        rule = geography.equivalence_rule()
        self.assertEqual(rule["provider_correspondence"], [])
        self.assertEqual(rule["reviewed_equivalences"], [])

    def test_non_shared_areas_are_excluded_from_the_comparison_set(self):
        evidence = compare.GeographyEvidence.same_vintage("county", "GENZ2023")
        report = compare.check(A, SAME_VINTAGE_B, "county",
                               {"36005", "36047"}, {"36005", "36061"},
                               geography_evidence=evidence)
        self.assertEqual(report.comparable_geoids, ["36005"])
        self.assertEqual(report.only_in_a, ["36047"])
        self.assertEqual(report.only_in_b, ["36061"])


def _feature(geoid, ring):
    coords = [[x, y] for x, y in ring]
    coords.append(coords[0])
    return {"type": "Feature", "properties": {"GEOID": geoid},
            "geometry": {"type": "Polygon", "coordinates": [coords]}}


# ---------------------------------------------------------------------------
# Defect 3: semantic drift was only disclosed
# ---------------------------------------------------------------------------

def _second_table(universe: str) -> dict:
    """A minimal second table, so a measure can span two universes."""
    variables = {}
    for suffix, label, pred in (("E", "Estimate!!Total:", "int"),
                                ("M", "Margin of Error!!Total:", "int"),
                                ("EA", "Annotation of Estimate!!Total:", "string"),
                                ("MA", "Annotation of Margin of Error!!Total:", "string")):
        variables[f"B06009_001{suffix}"] = {
            "label": label, "concept": "Place of Birth by Educational Attainment",
            "predicateType": pred, "group": "B06009", "limit": 0, "universe": universe}
    return {"variables": variables}


def _stage_two_releases(root: Path, mutate=None, tables=("B05002",),
                        second_universe=("Population 25 years and over",
                                         "Population 25 years and over")):
    for index, rid in enumerate(("a", "b")):
        d = root / f"data/raw/metadata/{rid}/groups"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "metadata" / "B05002.json", d / "B05002.json")
        if "B06009" in tables:
            (d / "B06009.json").write_text(
                json.dumps(_second_table(second_universe[index])), encoding="utf-8")
    if mutate:
        path = root / "data/raw/metadata/b/groups/B05002.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        mutate(doc)
        path.write_text(json.dumps(doc), encoding="utf-8")
    ra = release("a", 2019, 2023, "2019-2023 ACS", "GENZ2023")
    rb = release("b", 2018, 2022, "2018-2022 ACS", "GENZ2023")
    return (ra, rb,
            metadata.load_release_metadata(root, ra, list(tables)),
            metadata.load_release_metadata(root, rb, list(tables)))


class SemanticDriftTests(unittest.TestCase):
    def _check(self, mutate=None, measure=SHARE, drop_metadata=False, **staging):
        with temp_root() as root, offline():
            ra, rb, ma, mb = _stage_two_releases(root, mutate, **staging)
            if drop_metadata:
                ma = mb = None
            return compare.check(
                ra, rb, "county", {"36005"}, {"36005"}, ma, mb, measure,
                geography_evidence=compare.GeographyEvidence.same_vintage(
                    "county", "GENZ2023"))

    def test_a_substantive_label_change_blocks_the_comparison(self):
        def mutate(doc):
            for suf in ("E", "M", "EA", "MA"):
                doc["variables"][f"B05002_013{suf}"]["label"] = (
                    "Estimate!!Entirely different demographic group")
        report = self._check(mutate)
        self.assertFalse(report.allowed)
        self.assertTrue(any("B05002_013" in b for b in report.blocking))
        self.assertTrue(any("not been reviewed" in b for b in report.blocking))

    def test_a_reviewed_punctuation_change_is_allowed_and_disclosed(self):
        def mutate(doc):
            for suf in ("E", "M", "EA", "MA"):
                doc["variables"][f"B05002_013{suf}"]["label"] = (
                    doc["variables"][f"B05002_013{suf}"]["label"]
                    .replace("Foreign-born:", "Foreign born:"))
        report = self._check(mutate)
        self.assertTrue(report.allowed, report.blocking)
        self.assertTrue(any("Foreign born" in d for d in report.disclosures))

    def test_a_mixed_universe_table_is_refused_before_it_can_be_compared(self):
        """A single table may only carry one universe; the loader enforces it."""
        with temp_root() as root, offline():
            d = root / "data/raw/metadata/a/groups"
            d.mkdir(parents=True)
            doc = json.loads((FIXTURES / "metadata" / "B05002.json").read_text())
            for suf in ("E", "M", "EA", "MA"):
                doc["variables"][f"B05002_013{suf}"]["universe"] = "Something else"
            (d / "B05002.json").write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(metadata.MetadataError) as ctx:
                metadata.load_release_metadata(
                    root, release("a", 2019, 2023, "2019-2023 ACS"), ["B05002"])
        self.assertIn("more than one universe", str(ctx.exception))

    def test_a_universe_swap_between_tables_blocks_though_the_set_is_unchanged(self):
        """The multiset of universes is identical; the per-cell mapping is not.

        Comparing universes as unordered sets would pass this. Comparing them
        cell by cell does not.
        """
        cross = MeasureDef(
            measure_id="cross", label="cross", concept="c", unit="percent",
            kind="share", numerator_cells=["B05002_013"],
            denominator_cells=["B06009_001"], universe_note="u", definition_note="d")
        report = self._check(
            measure=cross, tables=("B05002", "B06009"),
            second_universe=("Population 25 years and over", "Total population"))
        self.assertFalse(report.allowed)
        self.assertTrue(any("universe" in b.lower() for b in report.blocking),
                        report.blocking)

    def test_missing_metadata_blocks_rather_than_implying_a_passed_check(self):
        report = self._check(drop_metadata=True)
        self.assertFalse(report.allowed)
        self.assertTrue(any("not been verified" in b for b in report.blocking))

    def test_no_measure_means_semantic_compatibility_is_not_established(self):
        report = self._check(measure=None)
        self.assertFalse(report.allowed)
        self.assertTrue(any("measure" in b.lower() for b in report.blocking))

    def test_a_dropped_cell_blocks(self):
        def mutate(doc):
            for suf in ("E", "M", "EA", "MA"):
                doc["variables"].pop(f"B05002_013{suf}")
        report = self._check(mutate)
        self.assertFalse(report.allowed)


class ExportValidationTests(unittest.TestCase):
    """Every requested measure is validated, not only the first."""

    def setUp(self):
        self._tmp = temp_root()
        self.root = self._tmp.__enter__()
        self._offline = offline()
        self._offline.__enter__()
        self.cfg = stage_two_release_project(self.root)
        build_release(self.root, self.cfg, "testrel")
        build_release(self.root, self.cfg, "testrel2")
        self.state = server.ServiceState(self.root)
        self.state.config = self.cfg

    def tearDown(self):
        self._offline.__exit__(None, None, None)
        self._tmp.__exit__(None, None, None)

    def test_the_compatible_measure_alone_exports(self):
        result = server.run_export(self.state, {
            "release_id": "testrel", "comparison_release_id": "testrel2",
            "measure_ids": ["foreign_born_share"], "level": "county",
            "include_figure": False})
        self.assertGreater(result["rows"], 0)

    def test_an_incompatible_second_measure_cannot_ride_along(self):
        """The second release dropped a cell `naturalized_share` depends on."""
        alone = server.compatibility(self.state, "testrel", "testrel2",
                                     "county", "naturalized_share")
        self.assertFalse(alone["allowed"], "fixture precondition")
        with self.assertRaises(ValueError) as ctx:
            server.run_export(self.state, {
                "release_id": "testrel", "comparison_release_id": "testrel2",
                "measure_ids": ["foreign_born_share", "naturalized_share"],
                "level": "county", "include_figure": False})
        self.assertIn("naturalized_share", str(ctx.exception))

    def test_the_incompatible_measure_is_also_refused_on_its_own(self):
        with self.assertRaises(ValueError):
            server.run_export(self.state, {
                "release_id": "testrel", "comparison_release_id": "testrel2",
                "measure_ids": ["naturalized_share"], "level": "county",
                "include_figure": False})


# ---------------------------------------------------------------------------
# Defect 4: export scope did not match the figure
# ---------------------------------------------------------------------------

class ExportScopeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_root()
        self.root = self._tmp.__enter__()
        self._offline = offline()
        self._offline.__enter__()
        self.cfg = stage_project(self.root)
        build(self.root, self.cfg)
        self.state = server.ServiceState(self.root)
        self.state.config = self.cfg

    def tearDown(self):
        self._offline.__exit__(None, None, None)
        self._tmp.__exit__(None, None, None)

    def test_a_one_area_export_produces_a_one_area_figure(self):
        result = server.run_export(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_share",
            "level": "county", "areas": ["36005"], "figure_kind": "chart"})
        out = self.root / result["export_dir"]
        rows = (out / "data.csv").read_text(encoding="utf-8").strip().splitlines()
        svg = (out / "figure.svg").read_text(encoding="utf-8")
        self.assertEqual(len(rows) - 1, 1)
        self.assertIn("Bronx", svg)
        for excluded in ("Brooklyn", "Manhattan", "Queens", "Staten Island"):
            self.assertNotIn(excluded, svg,
                             f"{excluded} was excluded from the data but drawn in the figure")

    def test_a_one_area_map_export_draws_only_that_area(self):
        result = server.run_export(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_share",
            "level": "county", "areas": ["36005"], "figure_kind": "map"})
        svg = (self.root / result["export_dir"] / "figure.svg").read_text(encoding="utf-8")
        self.assertEqual(svg.count("<title>"), 1)
        self.assertIn("Bronx", svg)

    def test_the_export_records_the_selection_it_used(self):
        result = server.run_export(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_share",
            "level": "county", "areas": ["36005"]})
        prov = json.loads(
            (self.root / result["export_dir"] / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(prov["selection"]["areas"], ["36005"])
        self.assertEqual(prov["selection"]["measure_ids"], ["foreign_born_share"])
        self.assertEqual(prov["selection"]["level"], "county")


# ---------------------------------------------------------------------------
# Defect 5: output path containment and cross-origin POST
# ---------------------------------------------------------------------------

class OutputContainmentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_root()
        self.root = self._tmp.__enter__()
        self._offline = offline()
        self._offline.__enter__()
        self.cfg = stage_project(self.root)
        build(self.root, self.cfg)
        self.state = server.ServiceState(self.root)
        self.state.config = self.cfg

    def tearDown(self):
        self._offline.__exit__(None, None, None)
        self._tmp.__exit__(None, None, None)

    def _export(self, name):
        return server.run_export(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_share",
            "level": "county", "name": name, "include_figure": False})

    def test_traversal_names_are_refused_with_no_side_effects(self):
        before = sorted(p.name for p in self.root.iterdir())
        for name in ("../escaped", "..\\escaped", "a/b", "/abs", "C:\\win",
                     "..", ".", "con", "x\x00y"):
            with self.subTest(name=name):
                with self.assertRaises(exports.UnsafeExportName):
                    self._export(name)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before,
                         "a refused export must not create anything")

    def test_an_ordinary_name_still_works_and_stays_inside_artifacts(self):
        result = self._export("borough-shares")
        out = (self.root / result["export_dir"]).resolve()
        self.assertTrue(out.is_relative_to((self.root / "artifacts").resolve()))
        self.assertTrue((out / "data.csv").exists())

    def test_write_bundle_refuses_a_directory_outside_artifacts(self):
        with self.assertRaises(exports.UnsafeExportName):
            exports.write_bundle(self.root / "elsewhere", "a,b\n", {},
                                 artifacts_root=self.root / "artifacts")


class PostOriginTests(unittest.TestCase):
    def test_a_cross_origin_post_is_refused(self):
        self.assertFalse(server.origin_is_local("http://evil.example", 8765))
        self.assertFalse(server.origin_is_local("https://127.0.0.1.evil.test", 8765))
        self.assertFalse(server.origin_is_local("null", 8765))

    def test_same_origin_posts_are_accepted(self):
        for origin in ("http://127.0.0.1:8765", "http://localhost:8765",
                       "http://[::1]:8765"):
            self.assertTrue(server.origin_is_local(origin, 8765), origin)

    def test_a_missing_origin_is_accepted_because_json_forces_a_preflight(self):
        self.assertTrue(server.origin_is_local(None, 8765))

    def test_only_json_content_type_is_accepted_for_state_changing_routes(self):
        self.assertTrue(server.content_type_is_json("application/json"))
        self.assertTrue(server.content_type_is_json("application/json; charset=utf-8"))
        for bad in ("text/plain", "application/x-www-form-urlencoded",
                    "multipart/form-data", "", None):
            self.assertFalse(server.content_type_is_json(bad), bad)


# ---------------------------------------------------------------------------
# Non-finite source values
# ---------------------------------------------------------------------------

class NonFiniteTests(unittest.TestCase):
    def test_non_finite_text_is_refused(self):
        for raw in ("NaN", "nan", "Infinity", "-Infinity", "inf", "-inf"):
            with self.subTest(raw=raw):
                cell = classify(raw)
                self.assertEqual(cell.status, "unparseable")
                self.assertIsNone(cell.value)

    def test_non_finite_float_input_is_refused(self):
        self.assertEqual(classify(float("nan")).status, "unparseable")
        self.assertEqual(classify(float("inf")).status, "unparseable")

    def test_ordinary_numbers_still_parse(self):
        self.assertEqual(classify("42").value, 42)
        self.assertEqual(classify("-1.5").value, -1.5)


if __name__ == "__main__":
    unittest.main()
