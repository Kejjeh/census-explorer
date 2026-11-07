"""What a view covers: New York City, the state, or one county's tracts.

All of this runs offline against the synthetic state in test_statewide: the
five New York City counties, Erie County and Suffolk County. None of these
numbers is a census finding. The same checks against the real release are
reported as live evidence, separately.
"""

from __future__ import annotations

import json
import subprocess
import unittest

from census_explorer import benchmark as benchmark_mod
from census_explorer import scope as scope_mod
from census_explorer import server, site
from tests.helpers import offline, temp_root
from tests.test_statewide import TRACTS, build_state, stage_state
from tests.test_ui_core import _node

REPO = server.WEB_DIR.parent
BOROUGHS = ["36005", "36047", "36061", "36081", "36085"]
CITY_TRACTS = sorted(g for g, *_ in TRACTS if g[:5] in BOROUGHS)
ERIE_TRACTS = sorted(g for g, *_ in TRACTS if g[:5] == "36029")


def areas(statewide_counties=True):
    counties = BOROUGHS + (["36029", "36103"] if statewide_counties else [])
    out = [{"geoid": g, "level": "county"} for g in counties]
    out += [{"geoid": g, "level": "tract"} for g, *_ in TRACTS if g[:5] in counties]
    return out


class ParseTests(unittest.TestCase):
    def test_no_scope_means_new_york_city_never_the_state(self):
        for text in (None, ""):
            self.assertEqual(scope_mod.parse(text).code, "nyc")

    def test_the_three_kinds_round_trip(self):
        for code in ("nyc", "nys", "county:36029"):
            self.assertEqual(scope_mod.parse(code).code, code)

    def test_anything_else_is_refused(self):
        for bad in ("NYC", "state", "county:3602", "county:36029x", "county:",
                    "borough:36005", " nyc"):
            with self.assertRaises(scope_mod.ScopeError, msg=bad):
                scope_mod.parse(bad)


class ResolveTests(unittest.TestCase):
    def resolve(self, code, level, statewide=True, built=None):
        return scope_mod.resolve(scope_mod.parse(code), level,
                                 built if built is not None else areas(),
                                 BOROUGHS, statewide)

    def test_the_city_is_its_five_counties_and_their_tracts(self):
        self.assertEqual(self.resolve("nyc", "county"), BOROUGHS)
        self.assertEqual(self.resolve("nyc", "tract"), CITY_TRACTS)

    def test_the_state_is_everything_at_the_level(self):
        self.assertEqual(self.resolve("nys", "county"),
                         sorted(BOROUGHS + ["36029", "36103"]))
        self.assertEqual(len(self.resolve("nys", "tract")), len(TRACTS))

    def test_a_county_scope_is_exactly_that_county_s_tracts(self):
        self.assertEqual(self.resolve("county:36029", "tract"), ERIE_TRACTS)
        self.assertEqual(self.resolve("county:36005", "tract"), ["36005000100"])

    def test_a_county_scope_only_applies_to_tracts(self):
        with self.assertRaises(scope_mod.ScopeError):
            self.resolve("county:36029", "county")

    def test_an_unknown_county_is_refused_not_empty(self):
        with self.assertRaises(scope_mod.ScopeError):
            self.resolve("county:36999", "tract")

    def test_the_state_needs_a_statewide_build(self):
        with self.assertRaises(scope_mod.ScopeError):
            self.resolve("nys", "county", statewide=False)

    def test_a_statewide_build_missing_a_borough_cannot_show_the_city(self):
        built = [a for a in areas() if not a["geoid"].startswith("36085")]
        with self.assertRaises(scope_mod.ScopeError) as caught:
            self.resolve("nyc", "county", built=built)
        self.assertIn("36085", str(caught.exception))

    def test_a_narrower_build_keeps_listing_what_it_has(self):
        built = [a for a in areas(False) if not a["geoid"].startswith("36085")]
        got = self.resolve("nyc", "county", statewide=False, built=built)
        self.assertEqual(got, BOROUGHS[:4])


class DescribeTests(unittest.TestCase):
    def test_counts_and_nouns(self):
        d = scope_mod.describe
        P = scope_mod.parse
        self.assertEqual(d(P("nyc"), "county", 5), "the 5 New York City boroughs")
        self.assertEqual(d(P("nys"), "county", 62), "all 62 counties in New York State")
        self.assertEqual(d(P("nyc"), "tract", 2327), "2,327 census tracts in New York City")
        self.assertEqual(d(P("nys"), "tract", 5411), "5,411 census tracts in New York State")
        self.assertEqual(d(P("county:36029"), "tract", 261, "Erie County"),
                         "261 census tracts in Erie County")

    def test_a_partial_city_says_so(self):
        text = scope_mod.describe(scope_mod.parse("nyc"), "county", 2, partial=True)
        self.assertIn("not all five", text)
        self.assertNotIn("the 2 New York City boroughs", text)

    def test_never_a_neighbourhood_and_never_an_upstate_borough(self):
        for code, name in (("county:36029", "Erie County"),
                           ("county:36103", "Suffolk County")):
            text = scope_mod.describe(scope_mod.parse(code), "tract", 10, name)
            self.assertNotIn("borough", text.lower())
            self.assertNotIn("neighbo", text.lower())


class _Built(unittest.TestCase):
    statewide = True

    def setUp(self):
        self._tmp = temp_root()
        self.root = self._tmp.__enter__()
        self._offline = offline()
        self._offline.__enter__()
        self.cfg = stage_state(self.root, statewide=self.statewide)
        if not self.statewide:
            # A narrower build reads the table from its own, unsuffixed cache.
            obs = self.root / "data/raw/acs/testrel/summary_file"
            (obs / "B05002.psv").write_bytes((obs / "B05002_state36.psv").read_bytes())
        build_state(self.root, self.cfg)
        self.state = server.ServiceState(self.root)
        self.state.config = self.cfg

    def tearDown(self):
        self._offline.__exit__(None, None, None)
        self._tmp.__exit__(None, None, None)

    def select(self, level="tract", measure="foreign_born_share", **kw):
        return server.build_selection(self.state, {
            "release_id": "testrel", "measure_id": measure, "level": level, **kw})


class SelectionScopeTests(_Built):
    def test_a_request_without_a_scope_is_still_new_york_city(self):
        """An old link or saved view must not widen to the state."""
        self.assertEqual(self.select("county").areas, BOROUGHS)
        self.assertEqual(self.select("tract").areas, CITY_TRACTS)
        self.assertEqual(self.select("tract").scope, "nyc")

    def test_the_state_and_one_county(self):
        sel = self.select("tract", scope="nys")
        self.assertEqual(len(sel.areas), len(TRACTS))
        self.assertIn("in New York State", sel.scope_label)
        erie = self.select("tract", scope="county:36029")
        self.assertEqual(erie.areas, ERIE_TRACTS)
        self.assertEqual(erie.scope_label,
                         f"{len(ERIE_TRACTS)} census tracts in Erie County")

    def test_a_borough_scope_is_called_a_borough(self):
        sel = self.select("tract", scope="county:36005")
        self.assertIn("Bronx, a New York City borough", sel.scope_label)

    def test_the_label_counts_the_scope_not_the_places_picked_inside_it(self):
        sel = self.select("tract", scope="county:36029", areas=["36029016600"])
        self.assertEqual(sel.areas, ["36029016600"])
        self.assertTrue(sel.scope_label.startswith(f"{len(ERIE_TRACTS)} "))

    def test_a_place_outside_the_scope_is_refused_not_dropped(self):
        with self.assertRaises(ValueError) as caught:
            self.select("tract", scope="county:36029", areas=["36005000100"])
        self.assertIn("outside the scope", str(caught.exception))
        # The default scope is the city: an Erie tract is outside it.
        with self.assertRaises(ValueError):
            self.select("tract", areas=["36029016600"])

    def test_the_query_string_carries_the_scope(self):
        payload = server._selection_payload({
            "release": ["testrel"], "measure": ["foreign_born_share"],
            "level": ["tract"], "scope": ["county:36029"]})
        self.assertEqual(payload["scope"], "county:36029")
        self.assertNotIn("scope", server._selection_payload({"release": ["x"]}))

    def test_a_snapshot_records_the_scope_and_replays_it(self):
        sel = self.select("tract", scope="county:36029")
        snap = server.make_snapshot(self.state, sel)
        self.assertEqual(snap.definitions["scope"], "county:36029")
        again = server.build_selection(self.state, {}, pinned=snap)
        self.assertEqual(again.areas, ERIE_TRACTS)

    def test_a_snapshot_saved_before_scopes_replays_as_the_city(self):
        snap = server.make_snapshot(self.state, self.select("tract"))
        del snap.definitions["scope"]
        again = server.build_selection(self.state, {}, pinned=snap)
        self.assertEqual(again.areas, CITY_TRACTS)

    def test_csv_export_covers_exactly_the_scope(self):
        from census_explorer import exports
        sel = self.select("tract", scope="county:36103")
        values = {("testrel", "foreign_born_share"):
                  self.state.values("testrel", "foreign_born_share")}
        text = exports.build_csv_for(sel, self.state.areas("testrel"), values)
        import csv
        import io
        rows = list(csv.DictReader(io.StringIO(text)))
        geoids = {r["geoid"] for r in rows}
        self.assertEqual(geoids, {g for g, *_ in TRACTS if g[:5] == "36103"})

    def test_the_brief_names_the_scope(self):
        from census_explorer import questions
        sel = self.select("tract", measure="foreign_born_share", scope="county:36029")
        ctx = server.brief_context(self.state, sel, questions.WHO_LIVES_HERE)
        self.assertIn("census tracts in Erie County", ctx["summary"]["places"])
        self.assertNotIn("New York City", ctx["summary"]["places"])
        county = self.select("county", measure="foreign_born_share", scope="nys")
        ctx = server.brief_context(self.state, county, questions.WHO_LIVES_HERE)
        self.assertIn("counties", ctx["contents"])
        self.assertNotIn("boroughs", ctx["contents"])


class BriefCoverageTests(_Built):
    """A brief says what its own map leaves out, not what the build does."""

    def limitations(self, **kw):
        from census_explorer import questions
        sel = self.select("tract", **kw)
        return server.brief_context(self.state, sel, questions.WHO_LIVES_HERE)["limitations"]

    def test_a_city_brief_does_not_claim_the_state_s_missing_boundaries(self):
        # The fixture's one tract without a polygon is in Erie County.
        lines = self.limitations()
        self.assertFalse(any("selected areas have no published boundary" in l for l in lines))
        build = next(l for l in lines if l.startswith("Across the whole build"))
        self.assertIn("0 of them are in this selection", build)
        self.assertNotIn(".0)", build)

    def test_a_county_brief_counts_its_own(self):
        lines = self.limitations(scope="county:36029")
        self.assertIn(f"1 of the {len(ERIE_TRACTS)} selected areas have no published boundary",
                      " ".join(lines))
        self.assertIn("1 of them is in this selection", " ".join(lines))


class ReferenceTests(_Built):
    def test_the_state_reference_is_the_state_s_own_row(self):
        values = self.state.values("testrel", "foreign_born_population")
        for level in ("county", "tract"):
            for scope in ("nyc", "nys"):
                sel = self.select(level, "foreign_born_population", scope=scope)
                bench = server.build_benchmark(self.state, sel, benchmark_mod.NYS)
                self.assertTrue(bench.available, bench.unavailable_reason)
                self.assertEqual(bench.components, ["36"])
                self.assertEqual(bench.estimate, values["36"]["e"])
                self.assertEqual(bench.moe, values["36"]["m"])

    def test_a_state_share_is_read_not_averaged(self):
        values = self.state.values("testrel", "foreign_born_share")
        sel = self.select("county", scope="nys")
        bench = server.build_benchmark(self.state, sel, benchmark_mod.NYS)
        self.assertAlmostEqual(bench.estimate, values["36"]["e"])
        shares = [values[g]["e"] for g in sel.areas]
        self.assertNotAlmostEqual(bench.estimate, sum(shares) / len(shares), places=3)

    def test_missing_state_uncertainty_is_unavailable_not_zero(self):
        values = self.state.values("testrel", "foreign_born_population")
        values["36"] = {**values["36"], "ms": "unavailable", "m": None,
                        "mr": "margin of error not published"}
        bench = server.build_benchmark(
            self.state, self.select("county", "foreign_born_population"),
            benchmark_mod.NYS)
        self.assertTrue(bench.available)
        self.assertEqual(bench.moe_status, "unavailable")
        self.assertIsNone(bench.moe)
        self.assertIn("not zero", bench.moe_reason)

    def test_the_city_reference_stays_five_counties_under_the_state_scope(self):
        sel = self.select("county", "foreign_born_population", scope="nys")
        bench = server.build_benchmark(self.state, sel, benchmark_mod.NYC)
        self.assertEqual(sorted(bench.components), BOROUGHS)

    def test_the_containing_county_is_named_as_a_county_outside_the_city(self):
        sel = self.select("tract", scope="county:36029")
        bench = server.build_benchmark(self.state, sel, benchmark_mod.CONTAINING_COUNTY)
        self.assertTrue(bench.available, bench.unavailable_reason)
        self.assertEqual(bench.label, "Erie County")
        self.assertNotIn("borough", bench.basis)
        self.assertEqual(bench.components, ["36029"])

    def test_the_old_identifier_still_resolves(self):
        sel = self.select("tract", scope="county:36005")
        bench = server.build_benchmark(self.state, sel, benchmark_mod.CONTAINING_BOROUGH)
        self.assertTrue(bench.available)
        self.assertIn("borough", bench.basis)

    def test_a_view_across_counties_has_no_containing_county(self):
        sel = self.select("tract", scope="nys")
        bench = server.build_benchmark(self.state, sel, benchmark_mod.CONTAINING_COUNTY)
        self.assertFalse(bench.available)
        self.assertIn("single county", bench.unavailable_reason)

    def test_options_offer_only_what_can_be_built(self):
        names = {a["geoid"]: a["name"] for a in self.state.dataset("testrel")["areas"]}
        ids = lambda opts: [o["benchmark_id"] for o in opts]
        whole = benchmark_mod.options("tract", [], BOROUGHS, names, scope="nys",
                                      statewide=True)
        self.assertIn("nys", ids(whole))
        self.assertNotIn("containing_county", ids(whole))
        erie = benchmark_mod.options("tract", [], BOROUGHS, names,
                                     scope="county:36029", statewide=True)
        label = next(o["label"] for o in erie
                     if o["benchmark_id"] == "containing_county")
        self.assertEqual(label, "The containing county (Erie County)")
        narrow = benchmark_mod.options("tract", [], BOROUGHS, names)
        self.assertNotIn("nys", ids(narrow))


class NarrowBuildReferenceTests(_Built):
    statewide = False

    def test_the_state_reference_is_unavailable_with_a_reason(self):
        bench = server.build_benchmark(
            self.state, self.select("county"), benchmark_mod.NYS)
        self.assertFalse(bench.available)
        self.assertIn("whole state", bench.unavailable_reason)

    def test_the_state_scope_is_refused(self):
        with self.assertRaises(scope_mod.ScopeError):
            self.select("county", scope="nys")


class CatalogTests(_Built):
    def test_regions_and_counties_carry_their_sizes(self):
        cat = server.measure_catalog(self.state, "testrel")
        self.assertTrue(cat["statewide"])
        self.assertEqual(cat["default_scope"], "nyc")
        regions = {r["scope"]: r for r in cat["regions"]}
        self.assertEqual(regions["nyc"]["counts"],
                         {"county": 5, "tract": len(CITY_TRACTS)})
        self.assertEqual(regions["nys"]["counts"],
                         {"county": 7, "tract": len(TRACTS)})
        self.assertEqual(regions["nyc"]["level_labels"]["county"]["label"], "Boroughs")
        self.assertEqual(regions["nys"]["level_labels"]["county"]["label"], "Counties")
        counties = {c["geoid"]: c for c in cat["counties"]}
        self.assertEqual(counties["36029"]["name"], "Erie County")
        self.assertFalse(counties["36029"]["borough"])
        self.assertTrue(counties["36005"]["borough"])
        # The Suffolk tract the release lists without estimates still counts:
        # the table shows it as unavailable rather than dropping it.
        self.assertEqual(counties["36103"]["tract_count"], 2)


class StaticBuildTests(_Built):
    def setUp(self):
        super().setUp()
        self.out = self.root / "site"
        site.build(self.root, self.out, release_id="testrel", base_path="/census-explorer/",
                   log=lambda *_: None)
        self.data = self.out / "data"

    def read(self, *parts):
        return json.loads((self.data.joinpath(*parts)).read_text(encoding="utf-8"))

    def test_references_are_written_for_the_state_and_every_county(self):
        ref = self.read("reference", "foreign_born_population.json")
        values = self.state.values("testrel", "foreign_born_population")
        self.assertEqual(ref["nys"]["county"]["estimate"], values["36"]["e"])
        self.assertEqual(sorted(ref["containing_county"]),
                         sorted(BOROUGHS + ["36029", "36103"]))
        self.assertEqual(ref["containing_county"]["36029"]["label"], "Erie County")
        self.assertEqual(sorted(ref["nyc"]["county"]["components"]), BOROUGHS)

    def test_the_containing_county_is_offered_per_county_only(self):
        doc = self.read("benchmarks", "tract.json")
        ids = [b["benchmark_id"] for b in doc["benchmarks"]]
        self.assertNotIn("containing_county", ids)
        self.assertNotIn("containing_borough", ids)
        self.assertEqual(doc["containing_county"]["36029"]["label"],
                         "The containing county (Erie County)")
        self.assertTrue(all(o["available_in_static_build"]
                            for o in doc["containing_county"].values()))

    def test_the_state_row_is_not_a_map_level(self):
        self.assertFalse((self.data / "geography" / "state.json").exists())
        self.assertEqual(sorted(self.read("catalog.json")["levels"]), ["county", "tract"])

    def test_the_browser_resolves_every_scope_as_the_service_does(self):
        node = _node()
        if node is None:
            self.skipTest("Node is not installed; the browser's scope rules "
                          "cannot be compared with the service's here.")
        ds = self.read("dataset.json")
        cat = self.read("catalog.json")
        cases = []
        codes = ["", "nyc", "nys"] + [f"county:{c['geoid']}" for c in cat["counties"]]
        for code in codes:
            for level in ("county", "tract"):
                try:
                    sel = self.select(level, scope=code) if code else self.select(level)
                    cases.append({"code": code, "level": level, "areas": sel.areas,
                                  "label": sel.scope_label, "error": None})
                except (ValueError, scope_mod.ScopeError):
                    cases.append({"code": code, "level": level, "areas": None,
                                  "label": None, "error": True})
        bundle = self.root / "scope_cases.json"
        bundle.write_text(json.dumps({"areas": ds["areas"], "catalog": {
            "boroughs": cat["boroughs"], "statewide": cat["statewide"]},
            "cases": cases}), encoding="utf-8")
        script = r"""
const fs = require('fs');
const core = require(process.argv[1] + '/web/core.js');
const doc = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const names = Object.fromEntries(doc.areas.map(a => [a.geoid, a.name]));
const counties = new Set(doc.areas.filter(a => a.level === 'county').map(a => a.geoid));
const partial = doc.catalog.boroughs.some(g => !counties.has(g));
const bad = [];
for (const c of doc.cases) {
  let got = null, label = null, error = null;
  try {
    const s = core.parseScope(c.code);
    got = core.resolveScope(s, c.level, doc.areas, doc.catalog.boroughs, doc.catalog.statewide);
    let name = s.county ? names[s.county] : null;
    if (s.county && doc.catalog.boroughs.includes(s.county)) name += ', a New York City borough';
    label = core.describeScope(s, c.level, got.length, name, s.kind === 'nyc' && partial);
  } catch (e) { error = true; }
  if (c.error ? !error : (error || JSON.stringify(got) !== JSON.stringify(c.areas) || label !== c.label)) {
    bad.push({case: c, got, label, error});
  }
}
if (bad.length) { console.log(JSON.stringify(bad, null, 1)); process.exit(1); }
console.log('ok ' + doc.cases.length);
"""
        proc = subprocess.run([node, "-e", script, str(REPO), str(bundle)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout[-4000:] + proc.stderr[-2000:])
        self.assertIn(f"ok {len(cases)}", proc.stdout)


if __name__ == "__main__":
    unittest.main()
