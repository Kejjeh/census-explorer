"""Brief-accuracy regressions found at e6750a8.

A brief is read by someone who was not there when it was made, so every
sentence in it has to be true of the brief in front of them:

1. it must describe the measure it actually contains, not the measures its
   starting question is capable of answering;
2. an unsaved brief must not claim the verification behaviour of a saved one;
3. the quality copy must match the selection, not assume several places;
4. the published source-table universe and the measure's own denominator are
   different things and must be labelled as such.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from census_explorer import benchmark as benchmark_mod, questions, server
from tests.helpers import offline, temp_root
from tests.test_end_to_end import build, stage_project


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

    def selection(self, measure_id="naturalized_share_of_foreign_born", **over):
        payload = {"release_id": "testrel", "measure_id": measure_id,
                   "level": "county"}
        payload.update(over)
        return server.build_selection(self.state, payload)

    def brief(self, sel=None, **kw):
        return server.render_brief(self.state, sel or self.selection(),
                                   questions.WHO_LIVES_HERE, **kw)

    @staticmethod
    def shows_sentence(html):
        return re.search(r"</dl>\s*<p>(.*?)</p>", html, re.S).group(1)

    @staticmethod
    def caption(html):
        return re.search(r"<figcaption>(.*?)</figcaption>", html, re.S).group(1)


# ---------------------------------------------------------------------------
# 1. Describe what is in the brief
# ---------------------------------------------------------------------------

class ContentsSentenceTests(_Fixture):
    def test_the_brief_does_not_promise_measures_it_does_not_contain(self):
        sentence = self.shows_sentence(self.brief())
        for absent in ("how many people live", "adults aged 25 and over hold",
                       "born in this state or another state"):
            self.assertNotIn(absent, sentence.lower(),
                             "the brief promised a measure it does not include")

    def test_the_sentence_names_the_measure_that_is_included(self):
        sentence = self.shows_sentence(self.brief())
        self.assertIn("Naturalised share of foreign-born residents", sentence)

    def test_the_sentence_names_the_place_and_the_period(self):
        sel = self.selection(areas=["36005"])
        sentence = self.shows_sentence(self.brief(sel))
        self.assertIn("Bronx", sentence)
        self.assertIn("2019-2023 ACS", sentence)

    def test_several_places_are_counted_not_promised_individually(self):
        sel = self.selection(areas=["36005", "36047"])
        sentence = self.shows_sentence(self.brief(sel))
        self.assertIn("2", sentence)
        self.assertNotIn("adults aged 25", sentence.lower())

    def test_a_reference_is_mentioned_only_when_one_was_built(self):
        sel = self.selection("foreign_born_share")
        values = self.state.values("testrel", "foreign_born_share")
        for geoid in self.cfg.county_geoids:
            values[geoid] = {"e": 30.0, "es": "ok", "n": 300, "d": 1000,
                             "m": 1.0, "ms": "ok"}
        with_ref = self.shows_sentence(
            self.brief(sel, benchmark_id=benchmark_mod.NYC))
        without = self.shows_sentence(self.brief(sel))
        self.assertIn("New York City", with_ref)
        self.assertNotIn("New York City", without)

    def test_the_sentence_is_derived_not_taken_from_the_question(self):
        """A different measure under the same question gives a different sentence."""
        a = self.shows_sentence(self.brief(self.selection("foreign_born_share")))
        b = self.shows_sentence(self.brief(self.selection(
            "naturalized_share_of_foreign_born")))
        self.assertNotEqual(a, b)


# ---------------------------------------------------------------------------
# 2. Only a saved brief claims saved-brief behaviour
# ---------------------------------------------------------------------------

class SavedClaimTests(_Fixture):
    def test_an_unsaved_brief_does_not_claim_it_will_be_checked_on_reopening(self):
        html = self.brief()
        self.assertNotIn("The saved brief records", html)
        self.assertNotIn("Reopening it checks them", html)

    def test_an_unsaved_brief_says_what_it_actually_is(self):
        html = self.brief()
        self.assertIn("generated", html.lower())
        self.assertIn("cannot verify", html.lower())
        self.assertNotIn("Saved as project", html)

    def test_a_brief_exported_from_a_saved_project_names_it(self):
        server.save_project(self.state, {
            "project_id": "bronx-profile", "release_id": "testrel",
            "measure_id": "naturalized_share_of_foreign_born", "level": "county",
            "areas": ["36005"], "question_id": questions.WHO_LIVES_HERE})
        result = server.run_export(self.state, {"project_id": "bronx-profile",
                                                "include_figure": False})
        html = (self.root / result["export_dir"] / "brief.html").read_text(
            encoding="utf-8")
        self.assertIn("bronx-profile", html)
        self.assertIn("Reopening it in the app", html)

    def test_an_export_without_a_project_is_still_a_snapshot(self):
        result = server.run_export(self.state, {
            "release_id": "testrel", "measure_id": "naturalized_share_of_foreign_born",
            "level": "county", "question_id": questions.WHO_LIVES_HERE,
            "include_figure": False})
        html = (self.root / result["export_dir"] / "brief.html").read_text(
            encoding="utf-8")
        self.assertNotIn("Saved as project", html)
        self.assertIn("cannot verify", html.lower())

    def test_both_wordings_keep_the_not_an_archive_statement(self):
        for html in (self.brief(),):
            self.assertIn("not an archive", html)

    def test_a_relative_repository_root_still_exports(self):
        """export_dir resolves its path, so the root has to resolve too."""
        import os
        previous = os.getcwd()
        os.chdir(self.root)
        try:
            state = server.ServiceState(Path("."))
            state.config = self.cfg
            result = server.run_export(state, {
                "release_id": "testrel",
                "measure_id": "naturalized_share_of_foreign_born",
                "level": "county", "question_id": questions.WHO_LIVES_HERE,
                "include_figure": False})
            self.assertTrue(result["export_dir"].startswith("artifacts/"))
        finally:
            os.chdir(previous)


# ---------------------------------------------------------------------------
# 3. Quality copy matches the selection
# ---------------------------------------------------------------------------

class QualityCopyTests(_Fixture):
    def quality(self, sel, benchmark=None):
        values = self.state.values(sel.primary_release.release_id,
                                   sel.primary_measure.measure_id)
        return server.quality_report(self.state, sel, values, benchmark=benchmark)

    def test_one_area_with_no_reference_is_not_described_as_a_comparison(self):
        report = self.quality(self.selection(areas=["36005"]))
        joined = " ".join(report["comparison"]["lines"]).lower()
        self.assertNotIn("places are compared with each other", joined)
        self.assertIn("single area", joined)

    def test_several_areas_are_described_as_a_comparison(self):
        report = self.quality(self.selection(areas=["36005", "36047"]))
        joined = " ".join(report["comparison"]["lines"]).lower()
        self.assertIn("compared with each other", joined)

    def test_one_area_with_a_reference_says_what_it_is_read_against(self):
        # The fixture leaves two boroughs unusable on purpose; the city
        # reference only exists once every borough carries a value.
        values = self.state.values("testrel", "foreign_born_share")
        for geoid in self.cfg.county_geoids:
            values[geoid] = {"e": 30.0, "es": "ok", "n": 300, "d": 1000,
                             "m": 1.0, "ms": "ok"}
        sel = self.selection("foreign_born_share", areas=["36005"])
        bench = server.build_benchmark(self.state, sel, benchmark_mod.NYC)
        self.assertTrue(bench.available, bench.unavailable_reason)
        report = self.quality(sel, benchmark=bench.to_json())
        joined = " ".join(report["comparison"]["lines"])
        self.assertIn("New York City", joined)
        self.assertIn("single area", joined)

    def test_one_area_whose_reference_failed_says_that_instead(self):
        sel = self.selection("foreign_born_share", areas=["36005"])
        bench = server.build_benchmark(self.state, sel, benchmark_mod.NYC)
        self.assertFalse(bench.available, "fixture precondition")
        joined = " ".join(self.quality(sel, benchmark=bench.to_json())
                          ["comparison"]["lines"]).lower()
        self.assertIn("could not be built", joined)
        self.assertNotIn("places are compared with each other", joined)

    def test_no_cross_period_claim_is_made_when_there_is_no_second_release(self):
        """The fixture project has one release, so there is nothing to compare
        it against and the copy must not imply otherwise."""
        self.assertEqual(self.state.available_releases(), ["testrel"])
        for areas in (["36005"], ["36005", "36047"]):
            joined = " ".join(self.quality(self.selection(areas=areas))
                              ["comparison"]["lines"]).lower()
            self.assertNotIn("another period", joined)
            self.assertNotIn("boundaries have not been verified", joined)

    def test_the_brief_carries_the_selection_aware_copy(self):
        html = self.brief(self.selection(areas=["36005"]))
        self.assertNotIn("Places are compared with each other", html)


# ---------------------------------------------------------------------------
# 4. Source-table universe and measure denominator are different things
# ---------------------------------------------------------------------------

class UniverseAndDenominatorTests(_Fixture):
    def test_the_caption_labels_the_published_table_universe_as_such(self):
        caption = self.caption(self.brief())
        self.assertIn("source table", caption.lower())
        self.assertIn("Total population", caption)

    def test_the_caption_states_the_measure_denominator_separately(self):
        caption = self.caption(self.brief())
        self.assertIn("denominator", caption.lower())
        self.assertIn("foreign-born", caption.lower())

    def test_the_two_are_not_presented_as_one_statement(self):
        caption = self.caption(self.brief())
        # The bare "Universe: Total population." on a naturalisation share read
        # as though the share were out of the whole population.
        self.assertNotRegex(caption, r"(?<!source table )Universe: Total population\.")

    def test_a_measure_whose_universe_matches_its_denominator_still_states_both(self):
        caption = self.caption(self.brief(self.selection("foreign_born_share")))
        self.assertIn("source table", caption.lower())
        self.assertIn("denominator", caption.lower())

    def test_a_count_has_no_denominator_claim(self):
        caption = self.caption(self.brief(self.selection("foreign_born_population")))
        self.assertIn("source table", caption.lower())
        self.assertIn("not applicable", caption.lower())

    def test_the_technical_metadata_is_preserved(self):
        html = self.brief()
        self.assertIn("B05002", html)
        self.assertIn("Published universe", html)


if __name__ == "__main__":
    unittest.main()
