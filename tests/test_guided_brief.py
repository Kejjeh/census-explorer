"""The guided brief: questions, benchmarks, quality, and selection-to-export.

Everything here runs offline against the fixture project used by the
end-to-end tests, so it exercises the real code paths without a network or a
built NYC dataset.
"""

from __future__ import annotations

import json
import unittest

from census_explorer import (benchmark as benchmark_mod, brief as brief_mod,
                             questions, server, snapshot)
from census_explorer.config import MeasureDef
from tests.helpers import offline, temp_root
from tests.test_end_to_end import build, stage_project


class QuestionCatalogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_root()
        self.root = self._tmp.__enter__()
        self._offline = offline()
        self._offline.__enter__()
        self.cfg = stage_project(self.root)
        build(self.root, self.cfg)
        self.state = server.ServiceState(self.root)
        self.state.config = self.cfg
        self.dataset = self.state.dataset("testrel")

    def tearDown(self):
        self._offline.__exit__(None, None, None)
        self._tmp.__exit__(None, None, None)

    def test_only_measures_the_dataset_carries_are_offered(self):
        options = questions.available(questions.WHO_LIVES_HERE, self.dataset)
        offered = {o.measure_id for o in options}
        built = {m["measure_id"] for m in self.dataset["measures"]}
        self.assertTrue(offered)
        self.assertTrue(offered <= built,
                        f"offered measures not in the dataset: {offered - built}")

    def test_a_question_with_no_available_measure_is_marked_unsupported(self):
        # The fixture project has no tract level, so the tract question cannot run.
        catalog = {q["question_id"]: q for q in questions.to_json(self.dataset)}
        tract_q = catalog[questions.BIRTHPLACE_CONCENTRATION]
        self.assertFalse(tract_q["supported"])
        self.assertIn("tract", tract_q["unsupported_reason"])
        self.assertEqual(tract_q["measures"], [])

    def test_every_option_states_what_it_counts_and_out_of_what(self):
        for qid in questions.QUESTIONS:
            for option in questions.QUESTION_MEASURES[qid]:
                self.assertTrue(option.counts_what.strip(), option.measure_id)
                if option.unit == "percent":
                    self.assertTrue(option.out_of.strip(), option.measure_id)
                else:
                    self.assertEqual(option.out_of, "", option.measure_id)

    def test_counts_and_shares_are_offered_as_different_measures(self):
        ids = {o.measure_id for o in questions.QUESTION_MEASURES[questions.COMPARE_PLACES]}
        self.assertIn("foreign_born_population", ids)
        self.assertIn("foreign_born_share", ids)

    def test_no_option_advertises_a_topic_this_build_lacks(self):
        import re as _re
        forbidden = ("poverty", "income", "housing", "rent", "employment",
                     "unemployment", "neighborhood", "neighbourhood")
        for qid in questions.QUESTIONS:
            for option in questions.QUESTION_MEASURES[qid]:
                text = f"{option.label} {option.counts_what} {option.out_of}".lower()
                for word in forbidden:
                    self.assertIsNone(_re.search(rf"\b{word}\b", text),
                                      f"{option.measure_id} mentions {word}")

    def test_the_tract_question_calls_tracts_tracts(self):
        q = questions.QUESTIONS[questions.BIRTHPLACE_CONCENTRATION]
        joined = " ".join(q.not_answered).lower()
        self.assertIn("census tracts are statistical areas", joined)
        self.assertIn("not neighbourhood", joined)

    def test_the_summary_is_plain_language_without_cell_codes(self):
        option = questions.available(questions.WHO_LIVES_HERE, self.dataset)[0]
        summary = questions.describe(questions.WHO_LIVES_HERE, option,
                                     "2019-2023 ACS", "Bronx")
        blob = " ".join(summary.values())
        self.assertNotIn("B05002", blob)
        self.assertNotIn("_001", blob)
        self.assertIn("2019-2023 ACS", blob)
        self.assertIn("not a single year", blob)


class BenchmarkTests(unittest.TestCase):
    SHARE = MeasureDef(
        measure_id="s", label="s", concept="c", unit="percent", kind="share",
        numerator_cells=["B05002_013"], denominator_cells=["B05002_001"],
        universe_note="u", definition_note="d")
    COUNT = MeasureDef(
        measure_id="c", label="c", concept="c", unit="persons", kind="count",
        numerator_cells=["B05002_013"], denominator_cells=[],
        universe_note="u", definition_note="d")

    def test_a_share_benchmark_adds_counts_and_never_averages_percentages(self):
        values = {
            "a": {"e": 10.0, "es": "ok", "n": 100, "d": 1000, "m": 1.0, "ms": "ok"},
            "b": {"e": 50.0, "es": "ok", "n": 500, "d": 1000, "m": 1.0, "ms": "ok"},
            # A third area far larger than the others: averaging the percentages
            # would ignore that entirely.
            "c": {"e": 20.0, "es": "ok", "n": 2000, "d": 10000, "m": 1.0, "ms": "ok"},
        }
        bench = benchmark_mod.aggregate(self.SHARE, values, ["a", "b", "c"],
                                        "x", "All three", "sum of counts")
        expected = (100 + 500 + 2000) / (1000 + 1000 + 10000) * 100
        self.assertAlmostEqual(bench.estimate, expected)
        mean_of_percentages = (10.0 + 50.0 + 20.0) / 3
        self.assertNotAlmostEqual(bench.estimate, mean_of_percentages, places=3)
        self.assertEqual(bench.numerator, 2600)
        self.assertEqual(bench.denominator, 12000)

    def test_a_share_benchmark_reports_its_uncertainty_as_unavailable(self):
        values = {"a": {"e": 10.0, "es": "ok", "n": 100, "d": 1000,
                        "m": 1.0, "ms": "ok"}}
        bench = benchmark_mod.aggregate(self.SHARE, values, ["a"], "x", "x", "b")
        self.assertEqual(bench.moe_status, "unavailable")
        self.assertIn("sampling error", bench.moe_reason)
        self.assertIn("not quantified", bench.moe_reason)
        # The sum is arithmetically exact; that is not the same as accurate.
        self.assertIn("arithmetically exact", bench.moe_reason)

    def test_a_count_benchmark_combines_margins_of_error(self):
        values = {"a": {"e": 100, "es": "ok", "m": 30, "ms": "ok"},
                  "b": {"e": 50, "es": "ok", "m": 40, "ms": "ok"}}
        bench = benchmark_mod.aggregate(self.COUNT, values, ["a", "b"], "x", "x", "b")
        self.assertEqual(bench.estimate, 150)
        self.assertAlmostEqual(bench.moe, 50.0)

    def test_an_unusable_component_blocks_the_benchmark(self):
        values = {"a": {"e": 100, "es": "ok", "m": 30, "ms": "ok"},
                  "b": {"e": None, "es": "unavailable", "er": "annotated"}}
        bench = benchmark_mod.aggregate(self.COUNT, values, ["a", "b"], "x", "x", "b")
        self.assertEqual(bench.estimate_status, "unavailable")
        self.assertIn("cannot be added", bench.estimate_reason)

    def test_an_unusable_margin_of_error_leaves_the_estimate_intact(self):
        values = {"a": {"e": 100, "es": "ok", "m": 30, "ms": "ok"},
                  "b": {"e": 50, "es": "ok", "m": None, "ms": "unavailable"}}
        bench = benchmark_mod.aggregate(self.COUNT, values, ["a", "b"], "x", "x", "b")
        self.assertEqual(bench.estimate, 150)
        self.assertEqual(bench.moe_status, "unavailable")
        self.assertIn("not zero", bench.moe_reason)

    def test_too_many_components_reports_no_margin_of_error_rather_than_a_bad_one(self):
        values = {str(i): {"e": 10, "es": "ok", "m": 2, "ms": "ok"} for i in range(40)}
        bench = benchmark_mod.aggregate(self.COUNT, values, list(values), "x", "x", "b")
        self.assertEqual(bench.estimate, 400)
        self.assertEqual(bench.moe_status, "unavailable")
        self.assertIn("degrades", bench.moe_reason)

    def test_controlled_components_are_said_to_be_controlled_not_zero(self):
        # Built through the real path so the explicit provenance is genuine,
        # not asserted by hand.
        from census_explorer import dataset as dataset_mod, measures as measures_mod
        from census_explorer.sentinels import classify
        controlled = dataset_mod.compact_value(measures_mod.compute(
            self.COUNT, "a", {"B05002_013": classify("100")},
            {"B05002_013": classify("-555555555")}).to_json())
        self.assertTrue(controlled.get("ctl"), "fixture precondition")
        values = {g: dict(controlled) for g in ("a", "b")}
        bench = benchmark_mod.aggregate(self.COUNT, values, ["a", "b"], "x", "x", "b")
        self.assertTrue(bench.controlled)
        self.assertIn("no sampling error", bench.moe_reason)

    def test_a_zero_denominator_is_undefined_not_zero(self):
        values = {"a": {"e": None, "es": "ok", "n": 0, "d": 0, "m": 1, "ms": "ok"}}
        bench = benchmark_mod.aggregate(self.SHARE, values, ["a"], "x", "x", "b")
        self.assertEqual(bench.estimate_status, "unavailable")
        self.assertIn("undefined, not zero", bench.estimate_reason)

    def test_an_empty_selection_has_no_benchmark(self):
        bench = benchmark_mod.aggregate(self.COUNT, {}, [], "x", "x", "b")
        self.assertFalse(bench.available)


class BriefTests(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_root()
        self.root = self._tmp.__enter__()
        self._offline = offline()
        self._offline.__enter__()
        self.cfg = stage_project(self.root)
        build(self.root, self.cfg)
        self.state = server.ServiceState(self.root)
        self.state.config = self.cfg
        self.sel = server.build_selection(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_share",
            "level": "county"})

    def tearDown(self):
        self._offline.__exit__(None, None, None)
        self._tmp.__exit__(None, None, None)

    def render(self, **kw):
        return server.render_brief(self.state, self.sel, questions.WHO_LIVES_HERE, **kw)

    def test_the_brief_states_the_period_denominator_and_source(self):
        html = self.render()
        self.assertIn("2019-2023 ACS", html)
        self.assertNotIn(">2020<", html)
        self.assertIn("all residents of the place", html)
        self.assertIn("U.S. Census Bureau", html)
        self.assertIn("Margin of error", html)

    def test_the_brief_carries_no_cell_codes_outside_the_source_section(self):
        """A reader should never have to decode B05002_013 to read the brief."""
        html = self.render()
        head = html.split("<h2>Source</h2>")[0]
        self.assertNotIn("B05002_013", head)
        # The reason is still there, in words.
        self.assertIn("too few sample cases", head)

    def test_quality_is_three_statements_and_not_one_score(self):
        html = self.render()
        for word in ("Uncertainty", "Comparison", "Period"):
            self.assertIn(f">{word}</h3>", html)
        for forbidden in ("trust score", "quality score", "confidence score",
                          "grade:"):
            self.assertNotIn(forbidden, html.lower())

    def test_the_brief_makes_no_significance_or_causal_claim(self):
        html = self.render().lower()
        for forbidden in ("statistically significant", "significant change",
                          "caused by", "because of the", "driven by", "led to"):
            self.assertNotIn(forbidden, html)

    def test_an_unavailable_value_never_renders_as_zero(self):
        html = self.render()
        self.assertIn("no data", html)
        self.assertIn("It is never a zero.", html)

    def test_an_analyst_note_is_marked_as_written_not_computed(self):
        html = self.render(analyst_note="We think this understates the east side.")
        self.assertIn("We think this understates the east side.", html)
        self.assertIn("written by a person, not computed from the data", html)

    def test_no_note_means_no_note_block(self):
        self.assertNotIn('class="note"', self.render())

    def test_the_reproducibility_record_does_not_claim_to_be_an_archive(self):
        html = self.render()
        self.assertIn("tamper check, not an archive", html)
        self.assertIn("no copy of the data is kept", html)

    def test_an_unsaved_brief_does_not_claim_a_saved_brief_s_verification(self):
        html = self.render()
        self.assertIn("generated snapshot", html)
        self.assertIn("cannot verify them", html)

    def test_the_benchmark_appears_with_its_basis(self):
        # The fixture leaves two boroughs unusable on purpose, so give the
        # complete city a value before asking for the complete city.
        values = self.state.values("testrel", "foreign_born_share")
        for geoid in self.state.config.county_geoids:
            values[geoid] = {"e": 30.0, "es": "ok", "n": 300, "d": 1000,
                             "m": 1.0, "ms": "ok"}
        context = server.brief_context(self.state, self.sel,
                                       questions.WHO_LIVES_HERE, benchmark_mod.NYC)
        self.assertTrue(context["benchmark"]["available"],
                        context["benchmark"]["unavailable_reason"])
        self.assertIn("added together", context["benchmark"]["basis"])

    def test_an_incomplete_city_benchmark_is_reported_not_silently_rebased(self):
        """The fixture cannot form the whole city, so it must say so."""
        context = server.brief_context(self.state, self.sel,
                                       questions.WHO_LIVES_HERE, benchmark_mod.NYC)
        bench = context["benchmark"]
        self.assertFalse(bench["available"])
        self.assertIn("New York City", bench["label"])
        self.assertIsNone(bench["estimate"])

    def test_a_measure_outside_the_question_is_refused(self):
        sel = server.build_selection(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_population",
            "level": "county"})
        with self.assertRaises(ValueError) as ctx:
            server.brief_context(self.state, sel, questions.BIRTHPLACE_CONCENTRATION)
        self.assertIn("not one of the options", str(ctx.exception))

    def test_the_brief_is_self_contained(self):
        html = self.render()
        self.assertTrue("<script" not in html.lower(), "the brief must carry no script")
        remote = html.replace("http://www.w3.org", "")
        self.assertTrue("http://" not in remote and "https://" not in remote,
                        "the brief must not fetch anything remote")
        self.assertTrue("@media print" in html, "no print stylesheet")

    def test_fixture_mode_is_declared_in_the_brief(self):
        from census_explorer import fixtures
        fixtures.build_fixture_dataset(self.root, self.cfg, "data/fixture-processed")
        state = server.ServiceState(self.root, "data/fixture-processed")
        state.config = self.cfg
        sel = server.build_selection(state, {
            "release_id": fixtures.FIXTURE_RELEASE_ID,
            "measure_id": "foreign_born_share", "level": "county"})
        html = server.render_brief(state, sel, questions.WHO_LIVES_HERE)
        self.assertTrue("FIXTURE MODE" in html, "fixture banner missing")
        self.assertTrue("Nothing here is a census finding" in html,
                        "fixture disclaimer missing")
        self.assertTrue("not boundaries" in html, "synthetic geometry not declared")


class SelectionToExportTests(unittest.TestCase):
    """What the user chose is what every output describes."""

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

    def test_export_bundles_the_brief_alongside_the_data(self):
        result = server.run_export(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_share",
            "level": "county", "areas": ["36005", "36047"],
            "question_id": questions.COMPARE_PLACES,
            "benchmark_id": benchmark_mod.SELECTED,
            "figure_kind": "chart"})
        out = self.root / result["export_dir"]
        self.assertTrue(result["brief"])
        for name in ("brief.html", "data.csv", "provenance.json", "figure.svg"):
            self.assertTrue((out / name).exists(), name)

        brief_html = (out / "brief.html").read_text(encoding="utf-8")
        csv_text = (out / "data.csv").read_text(encoding="utf-8")
        figure = (out / "figure.svg").read_text(encoding="utf-8")

        # The same two areas, everywhere.
        self.assertEqual(len(csv_text.strip().splitlines()) - 1, 2)
        for name in ("Bronx", "Brooklyn"):
            self.assertIn(name, brief_html)
            self.assertIn(name, figure)
        for excluded in ("Manhattan", "Queens", "Staten Island"):
            self.assertNotIn(excluded, figure)
            self.assertNotIn(f"<td>{excluded}", brief_html)

    def test_a_saved_brief_replays_with_its_question_and_benchmark(self):
        server.save_project(self.state, {
            "project_id": "b1", "release_id": "testrel",
            "measure_id": "foreign_born_share", "level": "county",
            "areas": ["36005"], "question_id": questions.WHO_LIVES_HERE,
            "benchmark_id": benchmark_mod.NYC,
            "analyst_note": "Checked against the borough profile."})
        fresh = server.ServiceState(self.root)
        fresh.config = self.cfg
        replay = server.replay_project(fresh, "b1")
        self.assertTrue(replay["pin"]["verified"])
        self.assertEqual(replay["brief"]["question_id"], questions.WHO_LIVES_HERE)
        self.assertEqual(replay["brief"]["benchmark_id"], benchmark_mod.NYC)
        self.assertEqual(replay["brief"]["analyst_note"],
                         "Checked against the borough profile.")
        self.assertEqual(replay["selection"]["areas"], ["36005"])

    def test_exporting_a_saved_brief_reproduces_it(self):
        server.save_project(self.state, {
            "project_id": "b2", "release_id": "testrel",
            "measure_id": "foreign_born_share", "level": "county",
            "areas": ["36005"], "question_id": questions.WHO_LIVES_HERE,
            "benchmark_id": benchmark_mod.NYC, "analyst_note": "A note."})
        result = server.run_export(self.state, {"project_id": "b2"})
        html = (self.root / result["export_dir"] / "brief.html").read_text(encoding="utf-8")
        self.assertIn("Who lives here?", html)
        self.assertIn("A note.", html)
        self.assertIn("New York City (all five boroughs)", html)

    def test_a_tampered_saved_brief_refuses_to_export(self):
        server.save_project(self.state, {
            "project_id": "b3", "release_id": "testrel",
            "measure_id": "foreign_born_share", "level": "county",
            "question_id": questions.WHO_LIVES_HERE})
        path = self.root / "data/processed/testrel/values/foreign_born_share.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["values"]["36005"]["e"] = 99.0
        path.write_text(json.dumps(doc), encoding="utf-8")
        fresh = server.ServiceState(self.root)
        fresh.config = self.cfg
        with self.assertRaises(snapshot.SnapshotMismatch) as ctx:
            fresh_export = server.run_export(fresh, {"project_id": "b3"})
        self.assertIn("foreign_born_share.json", str(ctx.exception))

    def test_quality_report_separates_the_three_concerns(self):
        sel = server.build_selection(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_share",
            "level": "county"})
        values = self.state.values("testrel", "foreign_born_share")
        report = server.quality_report(self.state, sel, values)
        self.assertEqual(sorted(report), ["comparison", "freshness", "uncertainty"])
        for key in report:
            self.assertIn(report[key]["class"], ("ok", "caution", "blocked"))
            self.assertTrue(report[key]["state"])

    def test_quality_reports_areas_without_a_usable_estimate(self):
        sel = server.build_selection(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_share",
            "level": "county"})
        values = self.state.values("testrel", "foreign_born_share")
        report = server.quality_report(self.state, sel, values)
        joined = " ".join(report["uncertainty"]["lines"])
        self.assertIn("no data", joined)


if __name__ == "__main__":
    unittest.main()
