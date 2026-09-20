"""Configuration validation and saved-project handling."""

from __future__ import annotations

import json
import unittest

from census_explorer import config as config_mod, projects
from census_explorer.config import MeasureDef, _validate_measure
from tests.helpers import offline, temp_root


def measure(**over):
    base = dict(
        measure_id="m", label="l", concept="c", unit="percent", kind="share",
        numerator_cells=["B05002_013"], denominator_cells=["B05002_001"],
        universe_note="u", definition_note="d",
    )
    base.update(over)
    return MeasureDef(**base)


class MeasureValidationTests(unittest.TestCase):
    def test_a_share_without_a_denominator_is_refused(self):
        with self.assertRaises(config_mod.ConfigError) as ctx:
            _validate_measure(measure(denominator_cells=[]))
        self.assertIn("not a display option", str(ctx.exception))

    def test_a_count_with_a_denominator_is_refused(self):
        with self.assertRaises(config_mod.ConfigError):
            _validate_measure(measure(kind="count", unit="persons"))

    def test_units_must_match_the_kind(self):
        with self.assertRaises(config_mod.ConfigError):
            _validate_measure(measure(unit="persons"))
        with self.assertRaises(config_mod.ConfigError):
            _validate_measure(measure(kind="count", unit="percent",
                                      denominator_cells=[]))

    def test_malformed_cell_codes_are_refused(self):
        with self.assertRaises(config_mod.ConfigError) as ctx:
            _validate_measure(measure(numerator_cells=["B05002-13"]))
        self.assertIn("malformed cell codes", str(ctx.exception))

    def test_a_valid_share_passes(self):
        _validate_measure(measure())


class RealConfigTests(unittest.TestCase):
    def test_the_shipped_configuration_loads_and_validates(self):
        with offline():
            cfg = config_mod.load()
        self.assertIn("acs5_2023", cfg.releases)
        self.assertEqual(cfg.release("acs5_2023").period_label, "2019-2023 ACS")
        self.assertEqual(cfg.county_geoids,
                         ["36005", "36047", "36061", "36081", "36085"])
        self.assertTrue(cfg.measures)

    def test_every_shipped_share_names_its_denominator_in_prose(self):
        cfg = config_mod.load()
        for m in cfg.measures.values():
            if m.kind == "share":
                self.assertTrue(m.universe_note.strip(), m.measure_id)
                self.assertTrue(m.denominator_cells, m.measure_id)

    def test_every_shipped_measure_carries_a_definition(self):
        cfg = config_mod.load()
        for m in cfg.measures.values():
            self.assertTrue(m.definition_note.strip(), m.measure_id)

    def test_birthplace_measures_say_they_are_stocks_not_flows(self):
        """Every place-of-birth count must state that it is not a migration flow."""
        cfg = config_mod.load()
        birthplace = [m for m in cfg.measures.values() if "B05006" in m.tables]
        self.assertTrue(birthplace)
        for m in birthplace:
            joined = " ".join(m.caveats).lower()
            self.assertIn("stock", joined, m.measure_id)
            self.assertIn("not a count of recent arrivals", joined, m.measure_id)

    def test_no_measure_claims_a_birthplace_is_a_recent_move_to_nyc(self):
        cfg = config_mod.load()
        for m in cfg.measures.values():
            text = f"{m.label} {m.definition_note}".lower()
            for forbidden in ("recent arrival", "recently arrived", "moved to new york",
                              "newcomer", "transplant"):
                self.assertNotIn(forbidden, text, m.measure_id)

    def test_born_in_state_measures_warn_that_ny_born_is_not_nyc_born(self):
        cfg = config_mod.load()
        targets = [m for m in cfg.measures.values()
                   if "born_in_state" in m.measure_id
                   or "born_in_state_of_residence" in m.measure_id]
        self.assertTrue(targets)
        for m in targets:
            joined = " ".join(m.caveats)
            self.assertIn("New York City", joined, m.measure_id)

    def test_unknown_release_names_the_configured_ones(self):
        cfg = config_mod.load()
        with self.assertRaises(config_mod.ConfigError) as ctx:
            cfg.release("acs5_1999")
        self.assertIn("acs5_2023", str(ctx.exception))


class ProjectTests(unittest.TestCase):
    def test_round_trip_preserves_the_requested_definition(self):
        with temp_root() as root, offline():
            p = projects.SavedProject(
                project_id="p1", title="t", release_id="acs5_2023", level="county",
                measure_id="foreign_born_share", manifest_ids=["m1"])
            projects.save(root, p)
            again = projects.load(root, "p1")
        self.assertEqual(again.requested["measure_id"], "foreign_born_share")
        self.assertEqual(again.manifest_ids, ["m1"])
        self.assertTrue(again.created_at and again.updated_at)

    def test_ids_are_constrained(self):
        for bad in ("", "../escape", "Has Space", "a" * 80):
            with self.assertRaises(projects.ProjectError):
                projects.validate_id(bad)

    def test_a_future_schema_version_is_refused_rather_than_misread(self):
        with temp_root() as root, offline():
            p = projects.SavedProject(
                project_id="p2", title="t", release_id="r", level="county",
                measure_id="m")
            path = projects.save(root, p)
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["schema_version"] = 99
            path.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(projects.ProjectError) as ctx:
                projects.load(root, "p2")
        self.assertIn("schema version 99", str(ctx.exception))

    def test_listing_and_deleting(self):
        with temp_root() as root, offline():
            projects.save(root, projects.SavedProject(
                project_id="a", title="A", release_id="r", level="county", measure_id="m"))
            self.assertEqual(len(projects.listing(root)), 1)
            self.assertTrue(projects.delete(root, "a"))
            self.assertEqual(projects.listing(root), [])
            self.assertFalse(projects.delete(root, "a"))

    def test_loading_a_missing_project_is_a_clear_error(self):
        with temp_root() as root, offline():
            with self.assertRaises(projects.ProjectError):
                projects.load(root, "nope")


if __name__ == "__main__":
    unittest.main()
