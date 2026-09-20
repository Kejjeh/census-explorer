"""Regressions for the second review batch against 7d2323c.

Each test was written to fail before the corresponding fix:

1. a partial set of boroughs presented as "all five";
2. every percentage described as "percent of residents" whatever its universe;
3. the foreign-born measures labelled "born outside the United States";
4. the brief printing "±0" for a controlled total and dropping a requested
   reference that turned out to be unavailable.
"""

from __future__ import annotations

import json
import re
import unittest

from census_explorer import benchmark as benchmark_mod, questions, server
from census_explorer.config import MeasureDef
from tests.helpers import offline, temp_root
from tests.test_end_to_end import build, stage_project

COUNT = MeasureDef(
    measure_id="foreign_born_population", label="c", concept="c", unit="persons",
    kind="count", numerator_cells=["B05002_013"], denominator_cells=[],
    universe_note="u", definition_note="d")


class _Fixture(unittest.TestCase):
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

    def selection(self, measure_id="foreign_born_population", **over):
        payload = {"release_id": "testrel", "measure_id": measure_id,
                   "level": "county"}
        payload.update(over)
        return server.build_selection(self.state, payload)


# ---------------------------------------------------------------------------
# 1. A city reference must be the whole city
# ---------------------------------------------------------------------------

class CityMembershipTests(_Fixture):
    def drop_counties(self, keep: list[str]):
        ds = self.state.dataset("testrel")
        ds["areas"] = [a for a in ds["areas"]
                       if a["level"] != "county" or a["geoid"] in keep]

    def make_all_usable(self, measure_id="foreign_born_population"):
        """The fixture leaves one borough annotated on purpose; this is the
        complete-city case, so every borough has to carry a value."""
        values = self.state.values("testrel", measure_id)
        for index, geoid in enumerate(self.cfg.county_geoids):
            values[geoid] = {"e": 100 * (index + 1), "es": "ok",
                             "m": 10, "ms": "ok"}

    def test_the_complete_city_is_available(self):
        self.make_all_usable()
        bench = server.build_benchmark(self.state, self.selection(),
                                       benchmark_mod.NYC)
        self.assertTrue(bench.available, bench.unavailable_reason)
        self.assertEqual(bench.component_count, 5)
        self.assertEqual(sorted(bench.components), self.cfg.county_geoids)
        self.assertEqual(bench.estimate, 100 + 200 + 300 + 400 + 500)

    def test_a_partial_city_is_refused_and_names_what_is_missing(self):
        self.drop_counties(["36005"])
        bench = server.build_benchmark(self.state, self.selection(),
                                       benchmark_mod.NYC)
        self.assertFalse(bench.available,
                         "one borough must never be presented as the city")
        self.assertIsNone(bench.estimate)
        for missing in ("36047", "36061", "36081", "36085"):
            self.assertIn(missing, bench.unavailable_reason)

    def test_a_partial_city_keeps_its_name_but_not_its_number(self):
        self.drop_counties(["36005", "36047"])
        bench = server.build_benchmark(self.state, self.selection(),
                                       benchmark_mod.NYC)
        self.assertIn("New York City", bench.label)
        self.assertEqual(bench.estimate_status, "unavailable")
        self.assertEqual(bench.component_count, 0)

    def test_an_unusable_component_blocks_the_whole_city(self):
        self.make_all_usable()
        values = self.state.values("testrel", "foreign_born_population")
        values["36047"] = {"e": None, "es": "unavailable",
                           "er": "too few sample cases"}
        bench = server.build_benchmark(self.state, self.selection(),
                                       benchmark_mod.NYC)
        self.assertFalse(bench.available or bench.estimate is not None)
        self.assertIn("36047", (bench.unavailable_reason or "")
                      + (bench.estimate_reason or ""))

    def test_a_reduced_configured_county_set_fails_closed(self):
        self.make_all_usable()
        self.cfg.counties = {"005": "Bronx", "047": "Brooklyn (Kings County)"}
        bench = server.build_benchmark(self.state, self.selection(),
                                       benchmark_mod.NYC)
        self.assertFalse(bench.available)
        self.assertIn("documented", bench.unavailable_reason.lower())

    def test_a_different_configured_county_set_fails_closed(self):
        self.make_all_usable()
        self.cfg.counties = dict(self.cfg.counties)
        self.cfg.counties.pop("085")
        self.cfg.counties["119"] = "Westchester"
        bench = server.build_benchmark(self.state, self.selection(),
                                       benchmark_mod.NYC)
        self.assertFalse(bench.available)
        self.assertIn("documented", bench.unavailable_reason.lower())

    def test_required_membership_is_enforced_by_the_aggregator_itself(self):
        values = {g: {"e": 10, "es": "ok", "m": 1, "ms": "ok"}
                  for g in ("a", "b", "c")}
        bench = benchmark_mod.aggregate(
            COUNT, values, ["a", "b"], "x", "Composite", "basis",
            required_members=["a", "b", "c"])
        self.assertFalse(bench.available)
        self.assertIn("c", bench.unavailable_reason)

    def test_an_extra_component_is_refused_too(self):
        values = {g: {"e": 10, "es": "ok", "m": 1, "ms": "ok"}
                  for g in ("a", "b", "c")}
        bench = benchmark_mod.aggregate(
            COUNT, values, ["a", "b", "c"], "x", "Composite", "basis",
            required_members=["a", "b"])
        self.assertFalse(bench.available)


# ---------------------------------------------------------------------------
# 2. A percentage must name its own denominator
# ---------------------------------------------------------------------------

class DenominatorLabelTests(_Fixture):
    def test_each_percent_option_reports_its_own_universe(self):
        seen = {}
        for qid in questions.QUESTIONS:
            for option in questions.QUESTION_MEASURES[qid]:
                if option.unit != "percent":
                    continue
                described = questions.describe(qid, option, "2019-2023 ACS", "Queens")
                seen[option.measure_id] = described["unit"]
                self.assertIn(option.out_of.split(",")[0][:24], described["unit"],
                              f"{option.measure_id}: unit '{described['unit']}' does "
                              f"not name its denominator '{option.out_of}'")
        # The three universes this build actually uses must all be represented.
        self.assertIn("residents", seen["foreign_born_share"])
        self.assertIn("foreign-born",
                      seen["fb_dominican_republic_share_of_foreign_born"])
        self.assertIn("25 and over", seen["bachelors_plus_share_all"])

    def test_the_three_universes_differ_from_one_another(self):
        def unit_for(measure_id, qid):
            option = next(o for o in questions.QUESTION_MEASURES[qid]
                          if o.measure_id == measure_id)
            return questions.describe(qid, option, "2019-2023 ACS", "Q")["unit"]

        residents = unit_for("foreign_born_share", questions.WHO_LIVES_HERE)
        adults = unit_for("bachelors_plus_share_all", questions.WHO_LIVES_HERE)
        naturalised = unit_for("naturalized_share_of_foreign_born",
                               questions.WHO_LIVES_HERE)
        self.assertNotEqual(residents, adults)
        self.assertNotEqual(residents, naturalised)
        self.assertNotEqual(adults, naturalised)

    def test_a_count_option_is_not_described_as_a_percentage(self):
        option = next(o for o in questions.QUESTION_MEASURES[questions.WHO_LIVES_HERE]
                      if o.unit == "persons")
        described = questions.describe(questions.WHO_LIVES_HERE, option,
                                       "2019-2023 ACS", "Q")
        self.assertNotIn("percent", described["unit"])
        self.assertIn("people", described["unit"])

    def test_the_brief_states_the_subgroup_universe_not_residents(self):
        sel = self.selection("naturalized_share_of_foreign_born")
        html = server.render_brief(self.state, sel, questions.WHO_LIVES_HERE)
        units = re.search(r"<dt>Units</dt><dd>(.*?)</dd>", html)
        self.assertIsNotNone(units)
        self.assertIn("foreign-born", units.group(1),
                      f"Units said {units.group(1)!r}")

    def test_the_brief_states_the_adult_universe_for_education(self):
        sel = self.selection("bachelors_plus_share_all")
        html = server.render_brief(self.state, sel, questions.WHO_LIVES_HERE)
        units = re.search(r"<dt>Units</dt><dd>(.*?)</dd>", html)
        self.assertIn("25 and over", units.group(1))

    def test_percentage_margins_of_error_are_labelled_percentage_points(self):
        sel = self.selection("foreign_born_share")
        html = server.render_brief(self.state, sel, questions.WHO_LIVES_HERE)
        self.assertIn("percentage points", html)
        self.assertNotRegex(html, r"±\d+\.\d%</td>",
                            "a margin of error on a percentage is in points")


# ---------------------------------------------------------------------------
# 3. Foreign-born is a citizenship-at-birth category, not a birthplace one
# ---------------------------------------------------------------------------

class NativityLabelTests(unittest.TestCase):
    def nativity_options(self):
        seen = {}
        for qid in questions.QUESTIONS:
            for option in questions.QUESTION_MEASURES[qid]:
                if ("foreign_born" in option.measure_id
                        or "black_alone_foreign_born" in option.measure_id):
                    seen[option.measure_id] = option
        return seen

    def test_no_nativity_measure_is_labelled_as_a_birthplace(self):
        for measure_id, option in self.nativity_options().items():
            text = f"{option.label}".lower()
            self.assertNotIn("born outside the united states", text,
                             f"{measure_id} labels a citizenship category as a "
                             "birthplace one")

    def test_nativity_measures_say_foreign_born(self):
        for measure_id, option in self.nativity_options().items():
            self.assertIn("foreign-born", option.label.lower(), measure_id)

    def test_the_plain_language_matches_the_official_definition(self):
        for measure_id, option in self.nativity_options().items():
            self.assertIn("citizen", option.counts_what.lower(), measure_id)
            self.assertIn("birth", option.counts_what.lower(), measure_id)

    def test_the_question_answer_text_does_not_contradict_the_measures(self):
        answers = questions.QUESTIONS[questions.WHO_LIVES_HERE].answers.lower()
        self.assertNotIn("born outside the united states", answers)
        self.assertIn("foreign-born", answers)

    def test_the_distinction_is_stated_somewhere_a_reader_will_see_it(self):
        joined = " ".join(
            questions.QUESTIONS[questions.WHO_LIVES_HERE].not_answered).lower()
        self.assertIn("u.s. citizen parent", joined,
                      "the brief should say why born-abroad is not foreign-born")

    def test_the_official_definition_is_archived_with_its_source(self):
        from census_explorer import definitions
        record = definitions.get("foreign_born")
        self.assertIn("not a U.S. citizen at birth", record["definition"])
        self.assertIn("census.gov", record["source_url"])
        self.assertEqual(len(record["document_sha256"]), 64)


# ---------------------------------------------------------------------------
# 4. The brief must stand alone about its reference
# ---------------------------------------------------------------------------

class BriefBenchmarkTests(_Fixture):
    def make_controlled(self, measure_id="foreign_born_population"):
        values = self.state.values("testrel", measure_id)
        for geoid in self.cfg.county_geoids:
            values[geoid] = {
                "e": 100, "es": "ok", "m": 0, "ms": "ok",
                "flags": ["B05002_013 controlled estimate (MOE treated as zero)"]}

    def test_a_controlled_total_is_not_printed_as_plus_minus_zero(self):
        self.make_controlled()
        sel = self.selection()
        bench = server.build_benchmark(self.state, sel, benchmark_mod.NYC)
        self.assertTrue(bench.controlled, "fixture precondition")
        html = server.render_brief(self.state, sel, questions.WHO_LIVES_HERE,
                                   benchmark_mod.NYC)
        row = next(r for r in html.split("<tr") if "New York City" in r)
        self.assertNotIn("±0", row)
        self.assertIn("controlled", row.lower())

    def test_a_requested_but_unavailable_reference_still_appears(self):
        sel = self.selection(areas=["36005"])
        bench = server.build_benchmark(self.state, sel,
                                       benchmark_mod.CONTAINING_BOROUGH)
        self.assertFalse(bench.available, "fixture precondition")
        html = server.render_brief(self.state, sel, questions.WHO_LIVES_HERE,
                                   benchmark_mod.CONTAINING_BOROUGH)
        self.assertIn("containing borough", html.lower())
        self.assertIn("not available", html.lower())
        self.assertIn(bench.unavailable_reason.split(";")[0][:40], html)

    def test_the_brief_carries_the_reference_basis_and_uncertainty_reason(self):
        values = self.state.values("testrel", "foreign_born_share")
        for geoid in self.cfg.county_geoids:
            values[geoid] = {"e": 30.0, "es": "ok", "n": 300, "d": 1000,
                             "m": 1.0, "ms": "ok"}
        sel = self.selection("foreign_born_share")
        bench = server.build_benchmark(self.state, sel, benchmark_mod.NYC)
        self.assertTrue(bench.available, bench.unavailable_reason)
        self.assertEqual(bench.moe_status, "unavailable", "fixture precondition")
        html = server.render_brief(self.state, sel, questions.WHO_LIVES_HERE,
                                   benchmark_mod.NYC)
        self.assertIn("added together", html, "the basis must travel with the brief")
        self.assertIn(bench.moe_reason.split(".")[0][:40], html)

    def test_an_aggregate_is_not_called_exact_without_qualification(self):
        import inspect
        from census_explorer import benchmark as module
        source = inspect.getsource(module)
        for match in re.finditer(r"[^.]*\bexact\b[^.]*\.", source):
            sentence = match.group(0)
            self.assertTrue(
                "arithmetic" in sentence or "counties" in sentence
                or "sample" in sentence,
                f"unqualified claim of exactness: {sentence.strip()[:120]}")

    def test_exporting_a_controlled_benchmark_keeps_the_wording(self):
        self.make_controlled()
        result = server.run_export(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_population",
            "level": "county", "question_id": questions.WHO_LIVES_HERE,
            "benchmark_id": benchmark_mod.NYC, "include_figure": False})
        html = (self.root / result["export_dir"] / "brief.html").read_text(
            encoding="utf-8")
        self.assertIn("controlled", html.lower())
        self.assertNotIn("±0", html)

    def test_exporting_an_unavailable_benchmark_keeps_the_reason(self):
        result = server.run_export(self.state, {
            "release_id": "testrel", "measure_id": "foreign_born_population",
            "level": "county", "areas": ["36005"],
            "question_id": questions.WHO_LIVES_HERE,
            "benchmark_id": benchmark_mod.CONTAINING_BOROUGH,
            "include_figure": False})
        html = (self.root / result["export_dir"] / "brief.html").read_text(
            encoding="utf-8")
        self.assertIn("not available", html.lower())


if __name__ == "__main__":
    unittest.main()
