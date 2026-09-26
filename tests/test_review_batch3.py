"""Regressions for the controlled-uncertainty defect found in ae2f8de.

A margin of error that could not be computed is *unknown*. A margin of error
that is zero because every contributing estimate is controlled to an
independent population total is *absent*. Those are different statements, and
reporting the first as the second understates uncertainty.

The defect was inferring the second from a substring in a source flag. Flags
from the numerator and the denominator are pooled, so a share whose
denominator was controlled and whose numerator had no usable margin of error
carried a "controlled" flag while its own margin of error was None — and was
described as carrying no sampling error.

These tests drive the real path: classified source cells through
``measures.compute``, then the compaction, then every consumer.
"""

from __future__ import annotations

import unittest

from census_explorer import benchmark as benchmark_mod, brief, dataset, measures, server
from census_explorer.config import MeasureDef
from census_explorer.sentinels import classify

SHARE = MeasureDef(
    measure_id="share", label="share", concept="c", unit="percent", kind="share",
    numerator_cells=["B05002_013"], denominator_cells=["B05002_001"],
    universe_note="u", definition_note="d")
COUNT = MeasureDef(
    measure_id="count", label="count", concept="c", unit="persons", kind="count",
    numerator_cells=["B05002_013"], denominator_cells=[],
    universe_note="u", definition_note="d")
TWO_CELL_COUNT = MeasureDef(
    measure_id="two", label="two", concept="c", unit="persons", kind="count",
    numerator_cells=["B06009_005", "B06009_006"], denominator_cells=[],
    universe_note="u", definition_note="d")

#: The published annotation meaning "controlled to an independent estimate".
CONTROLLED = "-555555555"
#: The published annotation meaning "margin of error could not be computed".
NO_MOE = "-222222222"


def compute(measure, cells):
    """``cells`` maps a cell code to (estimate_text, moe_text)."""
    est = {c: classify(e) for c, (e, _m) in cells.items()}
    moe = {c: classify(m) for c, (_e, m) in cells.items()}
    return measures.compute(measure, "36005", est, moe)


def compact(value):
    return dataset.compact_value(value.to_json())


class ControlledProvenanceTests(unittest.TestCase):
    """What ``compute`` is entitled to claim about a result's uncertainty."""

    def test_controlled_denominator_with_unknown_numerator_moe_is_not_controlled(self):
        """The reproduction. Unknown must stay unknown."""
        v = compute(SHARE, {"B05002_013": ("300", NO_MOE),
                            "B05002_001": ("1000", CONTROLLED)})
        self.assertEqual(v.estimate, 30.0)
        self.assertEqual(v.moe_status, measures.UNAVAILABLE)
        self.assertIsNone(v.moe)
        self.assertFalse(v.controlled,
                         "a controlled denominator does not make the share controlled")
        self.assertNotIn("ctl", compact(v))

    def test_that_value_is_not_described_as_having_no_sampling_error(self):
        v = compact(compute(SHARE, {"B05002_013": ("300", NO_MOE),
                                    "B05002_001": ("1000", CONTROLLED)}))
        self.assertFalse(server.is_controlled_value(v))
        words = server._reliability_words(v, "percent")
        self.assertNotIn("no sampling error", words)
        self.assertIn("unavailable", words)

    def test_a_share_with_a_positive_margin_of_error_is_not_controlled(self):
        v = compute(SHARE, {"B05002_013": ("300", "25"),
                            "B05002_001": ("1000", CONTROLLED)})
        self.assertEqual(v.moe_status, measures.OK)
        self.assertGreater(v.moe, 0)
        self.assertFalse(v.controlled)
        self.assertFalse(server.is_controlled_value(compact(v)))

    def test_a_share_is_controlled_only_when_both_sides_are(self):
        v = compute(SHARE, {"B05002_013": ("300", CONTROLLED),
                            "B05002_001": ("1000", CONTROLLED)})
        self.assertEqual(v.moe_status, measures.OK)
        self.assertEqual(v.moe, 0.0)
        self.assertTrue(v.controlled)
        self.assertTrue(server.is_controlled_value(compact(v)))

    def test_an_actual_controlled_count_is_controlled(self):
        v = compute(COUNT, {"B05002_013": ("1419250", CONTROLLED)})
        self.assertEqual(v.estimate, 1419250)
        self.assertEqual(v.moe, 0.0)
        self.assertTrue(v.controlled)
        self.assertIn("controlled", (v.moe_reason or "").lower())

    def test_mixed_count_components_are_not_controlled(self):
        v = compute(TWO_CELL_COUNT, {"B06009_005": ("100", CONTROLLED),
                                     "B06009_006": ("50", "40")})
        self.assertEqual(v.estimate, 150)
        self.assertAlmostEqual(v.moe, 40.0)
        self.assertFalse(v.controlled,
                         "one controlled component does not control the sum")

    def test_mixed_components_that_happen_to_sum_to_zero_are_not_controlled(self):
        """A published zero is not evidence of control."""
        v = compute(TWO_CELL_COUNT, {"B06009_005": ("100", CONTROLLED),
                                     "B06009_006": ("50", "0")})
        self.assertEqual(v.moe, 0.0)
        self.assertFalse(v.controlled)
        self.assertNotIn("ctl", compact(v))

    def test_an_ordinary_zero_margin_of_error_is_reported_as_published(self):
        v = compute(COUNT, {"B05002_013": ("100", "0")})
        self.assertEqual(v.moe, 0.0)
        self.assertEqual(v.moe_status, measures.OK)
        self.assertFalse(v.controlled)
        words = server._reliability_words(compact(v), "persons")
        self.assertNotIn("no sampling error", words)

    def test_an_unavailable_count_moe_stays_unavailable(self):
        v = compute(COUNT, {"B05002_013": ("100", NO_MOE)})
        self.assertEqual(v.estimate, 100)
        self.assertEqual(v.moe_status, measures.UNAVAILABLE)
        self.assertFalse(v.controlled)


class ConsumerAuditTests(unittest.TestCase):
    """Every consumer must read the explicit flag, not guess from text."""

    def poisoned(self):
        """Unknown uncertainty, carrying a controlled flag from another cell."""
        return compact(compute(SHARE, {"B05002_013": ("300", NO_MOE),
                                       "B05002_001": ("1000", CONTROLLED)}))

    def genuinely_controlled(self):
        return compact(compute(COUNT, {"B05002_013": ("1419250", CONTROLLED)}))

    def test_no_consumer_infers_control_from_a_flag_substring(self):
        import inspect
        for module in (server, benchmark_mod, brief):
            source = inspect.getsource(module)
            for line in source.splitlines():
                if "controlled" in line and "in flag" in line:
                    self.fail(f"{module.__name__} still infers control from a flag "
                              f"substring: {line.strip()}")

    def test_the_brief_row_does_not_claim_control_for_unknown_uncertainty(self):
        row = {"name": "Bronx", "estimate": 30.0, "moe": None,
               "controlled": server.is_controlled_value(self.poisoned()),
               "quality": server._reliability_words(self.poisoned(), "percent")}
        html = brief.build(
            question={"title": "Q", "answers": "a", "not_answered": []},
            summary={"question": "Q", "places": "Bronx", "period": "p",
                     "measure": "m", "counted": "c", "out_of": "o", "unit": "u"},
            rows=[row], measure={"label": "m", "unit": "percent",
                                 "definition_note": "d", "universe_note": "u",
                                 "universe_published": ["u"]},
            release={"period_label": "2019-2023 ACS"}, figure_svg="",
            quality={"uncertainty": {"state": "s", "class": "ok", "lines": []},
                     "comparison": {"state": "s", "class": "ok", "lines": []},
                     "freshness": {"state": "s", "class": "ok", "lines": []}},
            benchmark=None, limitations=[], sources=[], reproducibility={"inputs": []})
        self.assertNotIn(">none<", html,
                         "an unknown margin of error must not read as absent")
        self.assertIn("—", html)

    def test_the_brief_row_does_say_none_for_a_genuine_controlled_total(self):
        row = {"name": "Bronx", "estimate": 1419250, "moe": 0.0,
               "controlled": server.is_controlled_value(self.genuinely_controlled()),
               "quality": "controlled"}
        html = brief.build(
            question={"title": "Q", "answers": "a", "not_answered": []},
            summary={"question": "Q", "places": "Bronx", "period": "p",
                     "measure": "m", "counted": "c", "out_of": "o", "unit": "u"},
            rows=[row], measure={"label": "m", "unit": "persons",
                                 "definition_note": "d", "universe_note": "u",
                                 "universe_published": ["u"]},
            release={"period_label": "2019-2023 ACS"}, figure_svg="",
            quality={"uncertainty": {"state": "s", "class": "ok", "lines": []},
                     "comparison": {"state": "s", "class": "ok", "lines": []},
                     "freshness": {"state": "s", "class": "ok", "lines": []}},
            benchmark=None, limitations=[], sources=[], reproducibility={"inputs": []})
        self.assertIn(">none<", html)
        self.assertNotIn("±0", html)


class BenchmarkControlTests(unittest.TestCase):
    COUNT = MeasureDef(
        measure_id="c", label="c", concept="c", unit="persons", kind="count",
        numerator_cells=["B05002_013"], denominator_cells=[],
        universe_note="u", definition_note="d")

    def controlled_component(self, estimate=100):
        return compact(compute(COUNT, {"B05002_013": (str(estimate), CONTROLLED)}))

    def measured_component(self, estimate=100, moe=30):
        return compact(compute(COUNT, {"B05002_013": (str(estimate), str(moe))}))

    def test_all_controlled_components_give_a_controlled_reference(self):
        values = {g: self.controlled_component() for g in ("a", "b")}
        bench = benchmark_mod.aggregate(self.COUNT, values, ["a", "b"], "x", "x", "b")
        self.assertTrue(bench.controlled)
        self.assertEqual(bench.moe, 0.0)
        self.assertIn("no sampling error", bench.moe_reason)

    def test_one_measured_component_makes_the_reference_uncontrolled(self):
        values = {"a": self.controlled_component(), "b": self.measured_component()}
        bench = benchmark_mod.aggregate(self.COUNT, values, ["a", "b"], "x", "x", "b")
        self.assertFalse(bench.controlled)
        self.assertAlmostEqual(bench.moe, 30.0)

    def test_a_component_with_unknown_uncertainty_blocks_the_reference_moe(self):
        poisoned = compact(compute(SHARE, {"B05002_013": ("300", NO_MOE),
                                           "B05002_001": ("1000", CONTROLLED)}))
        values = {"a": self.controlled_component(), "b": poisoned}
        bench = benchmark_mod.aggregate(self.COUNT, values, ["a", "b"], "x", "x", "b")
        self.assertFalse(bench.controlled)
        self.assertEqual(bench.moe_status, "unavailable")
        self.assertIn("not zero", bench.moe_reason)


class PreviousRepairsStillHoldTests(unittest.TestCase):
    """The earlier batch must not have regressed."""

    def setUp(self):
        from tests.helpers import offline, temp_root
        from tests.test_end_to_end import build, stage_project
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

    def test_a_partial_city_is_still_refused(self):
        sel = server.build_selection(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_population",
            "level": "county"})
        ds = self.state.dataset("testrel")
        ds["areas"] = [a for a in ds["areas"]
                       if a["level"] != "county" or a["geoid"] == "36005"]
        bench = server.build_benchmark(self.state, sel, benchmark_mod.NYC)
        self.assertFalse(bench.available)
        self.assertIn("36047", bench.unavailable_reason)

    def test_subgroup_denominators_are_still_named(self):
        from census_explorer import questions
        option = next(o for o in questions.QUESTION_MEASURES[questions.WHO_LIVES_HERE]
                      if o.measure_id == "naturalized_share_of_foreign_born")
        described = questions.describe(questions.WHO_LIVES_HERE, option,
                                       "2019-2023 ACS", "Bronx")
        self.assertIn("foreign-born", described["unit"])
        self.assertNotEqual(described["unit"], "percent of residents")

    def test_nativity_labels_are_still_corrected(self):
        """The one measure that may say "born outside the United States" is the
        one that means it: the native-born category from B05002."""
        from census_explorer import questions
        for option in questions.QUESTION_MEASURES[questions.WHO_LIVES_HERE]:
            if option.measure_id == "native_born_outside_us_share":
                self.assertIn("native-born", option.label.lower())
                continue
            self.assertNotIn("born outside the united states", option.label.lower())


if __name__ == "__main__":
    unittest.main()
