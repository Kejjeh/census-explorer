"""The explorer's catalog and selection behaviour.

The interface is a thin layer over these: a topic sidebar that offers what the
build carries at the chosen level, a selection that drives the map, the table,
the brief and the export together, and a question resolved from that selection
so the brief can be framed at all. Everything here runs offline against the
fixture project, so none of it depends on a built NYC dataset.

These are contract tests, not markup tests: they assert what the page is
allowed to offer and what a selection must produce, which is what breaks
silently when the catalog and the build drift apart.
"""

from __future__ import annotations

import unittest

from census_explorer import questions, server
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
        self.dataset = self.state.dataset("testrel")

    def tearDown(self):
        self._offline.__exit__(None, None, None)
        self._tmp.__exit__(None, None, None)

    def selection(self, **over):
        payload = {"release_id": "testrel",
                   "measure_id": "naturalized_share_of_foreign_born",
                   "level": "county"}
        payload.update(over)
        return server.build_selection(self.state, payload)


# ---------------------------------------------------------------------------
# 1. The catalog offers exactly what the build carries
# ---------------------------------------------------------------------------

class CatalogTests(_Fixture):
    def test_the_catalog_never_offers_a_measure_the_build_lacks(self):
        built = {m["measure_id"] for m in self.dataset["measures"]}
        offered = {o.measure_id for o in questions.catalog(self.dataset, "county")}
        self.assertTrue(offered)
        self.assertTrue(offered <= built,
                        f"offered but not built: {sorted(offered - built)}")

    def test_the_catalog_offers_every_measure_available_at_that_level(self):
        # The sidebar is the only route to a measure. A measure the build
        # computed and the catalog forgets is invisible to the whole product,
        # which is how the tract level ended up with no nativity measures.
        for level in ("county", "tract"):
            available = {m["measure_id"] for m in self.dataset["measures"]
                         if m.get("availability", {}).get(level)}
            offered = {o.measure_id for o in questions.catalog(self.dataset, level)}
            self.assertEqual(available - offered, set(),
                             f"{level}: built and available but not offered: "
                             f"{sorted(available - offered)}")

    def test_a_measure_unavailable_at_a_level_is_not_offered_there(self):
        faked = dict(self.dataset)
        faked["measures"] = [
            {**m, "availability": {**m.get("availability", {}), "tract": False}}
            for m in self.dataset["measures"]]
        self.assertEqual(questions.catalog(faked, "tract"), [])

    def test_the_catalog_lists_each_measure_once(self):
        ids = [o.measure_id for o in questions.catalog(self.dataset, "county")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_offered_share_names_its_denominator(self):
        for level in ("county", "tract"):
            for option in questions.catalog(self.dataset, level):
                if option.unit == "percent":
                    self.assertTrue(option.out_of.strip(), option.measure_id)
                else:
                    self.assertEqual(option.out_of, "", option.measure_id)


# ---------------------------------------------------------------------------
# 2. Every selection resolves to a question the brief will accept
# ---------------------------------------------------------------------------

class QuestionResolutionTests(_Fixture):
    def test_every_catalog_measure_resolves_to_a_question_that_lists_it(self):
        # `brief_context` refuses a measure its question does not offer, so a
        # measure the sidebar shows and no question carries is a dead end the
        # user only discovers at export time.
        for level in ("county", "tract"):
            for option in questions.catalog(self.dataset, level):
                for area_count in (1, 2, 25):
                    qid = questions.question_for(option.measure_id, level, area_count)
                    listed = {o.measure_id for o in questions.QUESTION_MEASURES[qid]}
                    self.assertIn(option.measure_id, listed,
                                  f"{option.measure_id} at {level}/{area_count} "
                                  f"resolved to {qid}, which does not list it")

    def test_one_borough_and_a_profile_measure_reads_as_a_place_profile(self):
        self.assertEqual(
            questions.question_for("foreign_born_share", "county", 1),
            questions.WHO_LIVES_HERE)

    def test_several_places_read_as_a_comparison(self):
        self.assertEqual(
            questions.question_for("foreign_born_share", "county", 2),
            questions.COMPARE_PLACES)

    def test_a_birthplace_measure_across_tracts_reads_as_a_concentration(self):
        self.assertEqual(
            questions.question_for("fb_china_share_of_foreign_born", "tract", 2327),
            questions.BIRTHPLACE_CONCENTRATION)

    def test_an_unknown_measure_is_refused_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            questions.question_for("median_rent", "county", 1)

    def test_the_service_resolves_the_question_for_the_current_selection(self):
        sel = self.selection(areas=["36005"])
        values = self.state.values("testrel", sel.primary_measure.measure_id)
        payload = {
            "quality": server.quality_report(self.state, sel, values),
            "question_id": questions.question_for(
                sel.primary_measure.measure_id, sel.level, len(sel.areas)),
        }
        self.assertEqual(payload["question_id"], questions.WHO_LIVES_HERE)
        # The resolved question must be one the brief actually renders.
        html = server.render_brief(self.state, sel, payload["question_id"])
        self.assertIn(questions.QUESTIONS[payload["question_id"]].title, html)
        self.assertIn("all foreign-born residents of the place", html)


# ---------------------------------------------------------------------------
# 3. Tract caveats belong to the geography, not to the question
# ---------------------------------------------------------------------------

class TractCaveatTests(unittest.TestCase):
    def test_a_tract_brief_says_tracts_are_not_neighbourhoods(self):
        for qid in questions.QUESTIONS:
            lines = " ".join(
                questions.base_limitations(questions.QUESTIONS[qid], "tract")).lower()
            self.assertIn("not neighbourhood", lines, qid)
            self.assertIn("large margins of error", lines, qid)

    def test_a_borough_brief_does_not_borrow_the_tract_caveats(self):
        lines = questions.base_limitations(
            questions.QUESTIONS[questions.COMPARE_PLACES], "county")
        self.assertEqual(lines,
                         list(questions.QUESTIONS[questions.COMPARE_PLACES].not_answered))

    def test_the_tract_question_does_not_repeat_its_own_caveats(self):
        lines = questions.base_limitations(
            questions.QUESTIONS[questions.BIRTHPLACE_CONCENTRATION], "tract")
        self.assertEqual(len(lines), len(set(lines)))


# ---------------------------------------------------------------------------
# 4. One selection drives map, table, brief and export alike
# ---------------------------------------------------------------------------

class SelectionScopeTests(_Fixture):
    def test_two_chosen_places_scope_every_output_to_those_two(self):
        sel = self.selection(areas=["36005", "36047"])
        self.assertEqual(sel.areas, ["36005", "36047"])
        rows = server.brief_context(
            self.state, sel, questions.COMPARE_PLACES)["rows"]
        # Rows are ranked by estimate, so compare the set, not the order.
        self.assertEqual({r["geoid"] for r in rows}, {"36005", "36047"})
        out = server.run_export(self.state, {
            "release_id": "testrel",
            "measure_id": "naturalized_share_of_foreign_born",
            "level": "county", "areas": ["36005", "36047"],
            "question_id": questions.COMPARE_PLACES, "include_figure": False})
        self.assertEqual(out["rows"], 2)

    def test_clearing_the_chosen_places_restores_every_area_at_that_level(self):
        limited = self.selection(areas=["36005"])
        self.assertEqual(len(limited.areas), 1)
        cleared = self.selection()
        every = {a["geoid"] for a in self.dataset["areas"] if a["level"] == "county"}
        self.assertEqual(set(cleared.areas), every)
        self.assertGreater(len(cleared.areas), len(limited.areas))

    def test_one_chosen_place_exports_exactly_one_row(self):
        out = server.run_export(self.state, {
            "release_id": "testrel",
            "measure_id": "naturalized_share_of_foreign_born",
            "level": "county", "areas": ["36005"],
            "question_id": questions.WHO_LIVES_HERE, "include_figure": False})
        self.assertEqual(out["rows"], 1)
        self.assertEqual(out["selection"]["area_count"], 1)

    def test_a_place_that_does_not_exist_is_reported_not_silently_dropped(self):
        sel = self.selection(areas=["36005", "99999"])
        self.assertEqual(sel.areas, ["36005"])
        self.assertIn("99999", sel.excluded_areas)
        self.assertTrue(sel.exclusion_reason,
                        "an area was dropped without saying why")
        self.assertEqual(sel.requested_areas, ["36005", "99999"])


# ---------------------------------------------------------------------------
# 5. The sidebar payload carries what the page needs, with string identifiers
# ---------------------------------------------------------------------------

class CatalogPayloadTests(_Fixture):
    def setUp(self):
        super().setUp()
        self.payload = server.measure_catalog(self.state, "testrel")

    def test_the_payload_groups_every_offered_measure(self):
        level = self.payload["levels"]["county"]
        grouped = [m for g in level["groups"] for m in g["measures"]]
        self.assertEqual(len(grouped), level["measure_count"])
        self.assertEqual(
            {m["measure_id"] for m in grouped},
            {o.measure_id for o in questions.catalog(self.dataset, "county")})

    def test_group_headings_are_plain_language_not_table_codes(self):
        for g in self.payload["levels"]["county"]["groups"]:
            self.assertTrue(g["heading"])
            self.assertNotRegex(g["heading"], r"B\d{5}")

    def test_the_payload_names_the_period_without_relabelling_it(self):
        self.assertEqual(self.payload["period_label"],
                         self.dataset["release"]["period_label"])
        self.assertNotEqual(self.payload["period_label"], "2020")

    def test_identifiers_stay_strings_so_leading_zeroes_survive(self):
        for area in self.dataset["areas"]:
            self.assertIsInstance(area["geoid"], str)
        for g in self.payload["levels"]["county"]["groups"]:
            for m in g["measures"]:
                self.assertIsInstance(m["measure_id"], str)

    def test_a_level_the_build_has_no_areas_for_is_not_offered(self):
        # The fixture project is county-only, so the interface must not show a
        # tract switch that leads nowhere.
        self.assertNotIn("tract", self.payload["levels"])
        self.assertEqual(self.payload["level_order"], ["county"])

    def test_each_measure_carries_the_wording_the_sidebar_shows(self):
        for g in self.payload["levels"]["county"]["groups"]:
            for m in g["measures"]:
                self.assertTrue(m["label"].strip())
                self.assertTrue(m["counts_what"].strip())
                self.assertIn(m["unit"], ("percent", "persons"))


# ---------------------------------------------------------------------------
# 6. The starting examples describe what they actually open
# ---------------------------------------------------------------------------

class StarterTests(_Fixture):
    def setUp(self):
        super().setUp()
        self.payload = server.measure_catalog(self.state, "testrel")
        self.offered = self.payload["starters"]

    def options(self, level):
        return {o.measure_id: o for o in questions.catalog(self.dataset, level)}

    def test_every_offered_starter_opens_a_measure_this_build_carries(self):
        self.assertTrue(self.offered, "no starting example was offered")
        for starter in self.offered:
            self.assertIn(starter["level"], self.payload["levels"])
            self.assertIn(starter["measure_id"], self.options(starter["level"]),
                          f"{starter['starter_id']} opens a measure the "
                          f"{starter['level']} level does not offer")

    def test_every_place_a_starter_names_exists_at_that_level(self):
        for starter in self.offered:
            present = {a["geoid"] for a in self.dataset["areas"]
                       if a["level"] == starter["level"]}
            named = [*starter["areas"], *starter["compare"]]
            if starter["pick"]:
                named.append(starter["pick"])
            for geoid in named:
                self.assertIn(geoid, present,
                              f"{starter['starter_id']} names {geoid}, which "
                              f"is not in this build at {starter['level']}")

    def test_a_starter_names_the_places_it_opens(self):
        for starter in self.offered:
            self.assertEqual(len(starter["area_names"]), len(starter["areas"]))
            self.assertEqual(len(starter["compare_names"]), len(starter["compare"]))
            for name in (*starter["area_names"], *starter["compare_names"]):
                self.assertTrue(name.strip())

    def test_a_starter_borrows_the_catalog_s_own_wording(self):
        # A card must not paraphrase a measure. If the catalog says what it
        # counts and what it is out of, that is what the card says.
        for starter in self.offered:
            option = self.options(starter["level"])[starter["measure_id"]]
            self.assertEqual(starter["measure_label"], option.label)
            self.assertEqual(starter["counts_what"], option.counts_what)
            self.assertEqual(starter["out_of"], option.out_of)
            self.assertEqual(starter["unit"], option.unit)

    def test_a_share_starter_names_a_denominator_and_a_count_does_not(self):
        for starter in self.offered:
            if starter["unit"] == "percent":
                self.assertTrue(starter["out_of"].strip(), starter["starter_id"])
            else:
                self.assertEqual(starter["out_of"], "", starter["starter_id"])

    def test_every_starter_says_what_its_view_does_not_say(self):
        for starter in self.offered:
            self.assertTrue(starter["caution"].strip(), starter["starter_id"])

    def test_no_starter_names_a_population_this_data_does_not_identify(self):
        # These words are wrong here whatever the sentence around them: the
        # tables record place of birth and citizenship at birth, not ancestry,
        # not descent, and not immigration status.
        never = ("immigrant", "immigration", "migrant", "ancestry",
                 "descent", "heritage", "city-born", "born in new york city")
        for starter in self.offered:
            text = " ".join([starter["title"], starter["measure_label"],
                             starter["counts_what"], starter["out_of"],
                             starter["caution"]]).lower()
            for word in never:
                self.assertNotIn(word, text,
                                 f"{starter['starter_id']} says '{word}'")

    def test_no_starter_headline_asserts_a_trend_or_a_cause(self):
        # The caution is exempt on purpose: it is where a starter says what
        # the view does *not* show, so it may name the thing it rules out.
        import re as _re
        claims = ("increase", "decrease", "growth", "decline", "rising",
                  "falling", "trend", "influx", "wave", "because", "caused",
                  "drove", "led to", "gentrif", "displace", "over time")
        for starter in self.offered:
            headline = f"{starter['title']} {starter['measure_label']}".lower()
            for word in claims:
                self.assertIsNone(
                    _re.search(rf"\b{word}", headline),
                    f"{starter['starter_id']} headline says '{word}'")

    def test_a_place_of_birth_starter_says_it_is_not_a_record_of_a_move(self):
        # Decided by the published concept, not by the word "born" appearing
        # in a label: a naturalisation share is a citizenship measure and
        # carries a different caution.
        concepts = {m["measure_id"]: m.get("concept", "")
                    for m in self.dataset["measures"]}
        for starter in self.offered:
            concept = concepts.get(starter["measure_id"], "").lower()
            if "place of birth" not in concept and "nativity" not in concept:
                continue
            caution = starter["caution"].lower()
            self.assertTrue(
                "not a settlement history" in caution
                or "does not say when" in caution
                or "not a record" in caution,
                f"{starter['starter_id']} does not separate a birthplace "
                "count from a move")

    def test_a_tract_starter_says_tracts_are_not_neighbourhoods(self):
        for starter in self.offered:
            if starter["level"] != "tract":
                continue
            self.assertIn("not neighbourhood", starter["caution"].lower(),
                          starter["starter_id"])

    def test_a_comparison_starter_refuses_to_imply_significance(self):
        for starter in self.offered:
            if not starter["compare"]:
                continue
            self.assertIn("statistical significance",
                          starter["caution"].lower(), starter["starter_id"])

    def test_a_starter_asks_only_for_a_reference_its_level_offers(self):
        from census_explorer import benchmark as benchmark_mod
        for starter in self.offered:
            options = {o["benchmark_id"] for o in benchmark_mod.options(
                starter["level"], starter["areas"],
                self.state.config.county_geoids, {})}
            self.assertIn(starter["benchmark"], options, starter["starter_id"])

    def test_a_level_this_build_lacks_takes_its_starters_with_it(self):
        # The fixture project has no tract level, so the tract example must
        # not be offered rather than opening something else.
        self.assertNotIn("tract", self.payload["levels"])
        self.assertNotIn("birthplace_group",
                         {s["starter_id"] for s in self.offered})

    def test_a_starter_whose_measure_is_missing_is_dropped_not_adjusted(self):
        thinned = dict(self.dataset)
        thinned["measures"] = [m for m in self.dataset["measures"]
                               if m["measure_id"] != "foreign_born_share"]
        offered = questions.starters(
            thinned, lambda key: f"36{key}", self.payload["levels"])
        self.assertNotIn("one_place", {s["starter_id"] for s in offered})

    def test_a_starter_whose_place_is_missing_is_dropped_not_adjusted(self):
        thinned = dict(self.dataset)
        thinned["areas"] = [a for a in self.dataset["areas"]
                            if a["geoid"] != "36005"]
        offered = questions.starters(
            thinned, lambda key: f"36{key}", self.payload["levels"])
        self.assertEqual(offered, [],
                         "a starter opened without the place it names")


if __name__ == "__main__":
    unittest.main()
