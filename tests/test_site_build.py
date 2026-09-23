"""The static build that a published site is made of.

A published copy has no Python behind it, so two things have to be true of
what the build writes. It must say the same things the local service says —
same estimates, same margins of error, same denominators, same coverage — and
it must contain nothing that is not the official published aggregate data and
this project's own wording about it.

Everything here runs offline against the fixture project. The round-trip
against `web/static.js` runs under Node when Node is present, and skips with
an explanation when it is not; Node is not a dependency of this project.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import unittest
from pathlib import Path

from census_explorer import exports, questions, server, site
from tests.helpers import offline, temp_root
from tests.test_end_to_end import build, stage_project
from tests.test_ui_core import _node

REPO = Path(__file__).resolve().parents[1]


class _Built(unittest.TestCase):
    """A fixture project, built, with the static site written beside it."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = temp_root()
        cls.root = cls._tmp.__enter__()
        cls._offline = offline()
        cls._offline.__enter__()
        cls.cfg = stage_project(cls.root)
        build(cls.root, cls.cfg)
        cls.out = cls.root / "site"
        cls.report = site.build(cls.root, cls.out, "testrel",
                                base_path="/census-explorer/", log=lambda *a: None)
        cls.state = server.ServiceState(cls.root)
        cls.state.config = cls.cfg

    @classmethod
    def tearDownClass(cls):
        cls._offline.__exit__(None, None, None)
        cls._tmp.__exit__(None, None, None)

    def data(self, *parts) -> dict:
        return json.loads((self.out / "data" / Path(*parts)).read_text("utf-8"))

    def all_files(self):
        return [p for p in self.out.rglob("*") if p.is_file()]


# ---------------------------------------------------------------------------
# 1. It runs with no service behind it
# ---------------------------------------------------------------------------

class NoServiceTests(_Built):
    def test_shared_snapshot_stays_stable_across_republishing(self):
        from unittest.mock import patch
        def config(directory):
            html = (directory / "index.html").read_text("utf-8")
            return json.loads(re.search(r"window.CENSUS_EXPLORER_STATIC = (.*?);</script>", html).group(1))
        before = config(self.out)
        other = self.root / "republished"
        with patch("census_explorer.site.provenance.utc_now", return_value="2099-01-01T00:00:00Z"):
            site.build(self.root, other, "testrel", base_path="/census-explorer/", log=lambda *a: None)
        after = config(other)
        self.assertEqual(before["snapshot"], after["snapshot"])
        self.assertNotEqual(before["dataDigests"]["data/manifest.json"], after["dataDigests"]["data/manifest.json"])
        self.assertRegex(before["snapshot"], r"^[0-9a-f]{64}$")
        for name, digest in before["dataDigests"].items():
            self.assertEqual(digest, site.provenance.sha256_bytes((self.out / name).read_bytes()))

    def test_the_page_never_asks_for_an_api_endpoint(self):
        html = (self.out / "index.html").read_text("utf-8")
        self.assertNotIn("/api/", html)

    def test_nothing_shipped_points_at_a_local_service(self):
        for path in self.all_files():
            if path.suffix not in (".html", ".js", ".css", ".json"):
                continue
            text = path.read_text("utf-8", errors="ignore")
            for forbidden in ("127.0.0.1", "localhost", "http://0.0.0.0", ":8765"):
                self.assertNotIn(forbidden, text,
                                 f"{path.name} names a local service: {forbidden}")

    def test_every_asset_is_referenced_relatively(self):
        html = (self.out / "index.html").read_text("utf-8")
        for match in re.findall(r'(?:src|href)="([^"]+)"', html):
            if match.startswith("#"):
                continue
            self.assertFalse(match.startswith("/"),
                             f"{match} is absolute and breaks under a subpath")
            self.assertFalse(match.startswith("http"),
                             f"{match} is an external request")

    def test_the_page_declares_its_static_mode_and_the_data_root(self):
        html = (self.out / "index.html").read_text("utf-8")
        self.assertIn("window.CENSUS_EXPLORER_STATIC", html)
        self.assertIn('"base":"data/"', html)
        self.assertIn('<script src="static.js"></script>', html)

    def test_every_file_the_page_loads_exists(self):
        html = (self.out / "index.html").read_text("utf-8")
        for match in re.findall(r'(?:src|href)="([^"#]+)"', html):
            if match.startswith("http"):
                continue
            self.assertTrue((self.out / match).is_file(), f"missing {match}")

    def test_jekyll_is_turned_off_so_no_file_is_dropped(self):
        self.assertTrue((self.out / ".nojekyll").is_file())


# ---------------------------------------------------------------------------
# 1b. It never destroys a directory that is not its own output
# ---------------------------------------------------------------------------

class OutputDirectoryTests(_Built):
    """`--out` is the one argument that deletes things, so it is checked."""

    def keeper(self, name: str) -> Path:
        target = self.root / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "important.txt").write_text("keep me", encoding="utf-8")
        return target

    def assert_refused(self, target, fragment):
        with self.assertRaises(site.UnsafeOutputDirectory) as caught:
            site.build(self.root, target, "testrel", log=lambda *a: None)
        message = str(caught.exception)
        self.assertIn(fragment, message)
        self.assertIn("Nothing was deleted", message)

    def test_a_directory_with_someone_else_s_files_is_refused_untouched(self):
        target = self.keeper("not-ours")
        self.assert_refused(target, "not a previous build")
        self.assertEqual((target / "important.txt").read_text("utf-8"), "keep me")

    def test_the_repository_itself_is_refused(self):
        self.assert_refused(self.root, "that is the repository itself")

    def test_a_directory_containing_the_repository_is_refused(self):
        self.assert_refused(self.root.parent, "contains the repository")

    def test_the_filesystem_root_is_refused(self):
        self.assert_refused(Path(self.root.anchor or "/"), "root of the filesystem")

    def test_a_git_checkout_is_refused(self):
        target = self.keeper("a-checkout")
        (target / ".git").mkdir()
        self.assert_refused(target, "git repository")

    def test_a_file_is_refused_rather_than_replaced(self):
        target = self.root / "a-file"
        target.write_text("not a directory", encoding="utf-8")
        self.assert_refused(target, "a file, not a directory")
        self.assertEqual(target.read_text("utf-8"), "not a directory")

    def test_an_empty_directory_is_accepted(self):
        target = self.root / "empty-out"
        target.mkdir()
        report = site.build(self.root, target, "testrel", log=lambda *a: None)
        self.assertTrue((report.out_dir / "index.html").is_file())

    def test_a_previous_build_is_replaced(self):
        target = self.root / "again"
        site.build(self.root, target, "testrel", log=lambda *a: None)
        (target / "data" / "values" / "left-over.json").write_text("{}", encoding="utf-8")
        site.build(self.root, target, "testrel", log=lambda *a: None)
        self.assertFalse((target / "data" / "values" / "left-over.json").exists(),
                         "a rebuild left a file from the previous build behind")
        self.assertTrue((target / "data" / "manifest.json").is_file())

    def test_a_build_that_fails_leaves_the_previous_copy_alone(self):
        from unittest.mock import patch
        target = self.root / "survivor"
        site.build(self.root, target, "testrel", log=lambda *a: None)
        before = (target / "data" / "manifest.json").read_bytes()
        with patch("census_explorer.site.server.measure_catalog",
                    side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                site.build(self.root, target, "testrel", log=lambda *a: None)
        self.assertEqual((target / "data" / "manifest.json").read_bytes(), before)
        leftovers = [p.name for p in target.parent.iterdir()
                     if p.name.startswith(".") and p.name.endswith(".building")]
        self.assertEqual(leftovers, [], "a failed build left its staging directory")

    def test_the_report_names_the_directory_the_caller_asked_for(self):
        target = self.root / "named"
        report = site.build(self.root, target, "testrel", log=lambda *a: None)
        self.assertEqual(report.out_dir, target.resolve())
        for path in report.files:
            self.assertTrue(path.is_file(), f"{path} is not where the report says")


# ---------------------------------------------------------------------------
# 2. It contains only what may be published
# ---------------------------------------------------------------------------

class PublishableContentsTests(_Built):
    ALLOWED_SUFFIXES = {".html", ".js", ".css", ".json", ""}

    def test_only_expected_file_types_are_written(self):
        for path in self.all_files():
            self.assertIn(path.suffix, self.ALLOWED_SUFFIXES,
                          f"{path.name} is not a publishable file type")

    def test_no_raw_cache_saved_view_or_manifest_directory_is_copied(self):
        names = {p.relative_to(self.out).as_posix() for p in self.all_files()}
        for forbidden in ("data/raw", "data/projects", "data/manifests",
                          "artifacts", ".env"):
            self.assertFalse(any(n.startswith(forbidden) for n in names),
                             f"{forbidden} was copied into the site")

    def test_no_local_path_is_written_into_the_output(self):
        root_text = str(self.root)
        for path in self.all_files():
            if path.suffix not in (".html", ".js", ".json"):
                continue
            text = path.read_text("utf-8", errors="ignore")
            self.assertNotIn(root_text, text,
                             f"{path.name} contains the build machine's path")
            self.assertNotIn("/home/", text, f"{path.name} contains a home path")
            self.assertNotIn("C:\\\\", text, f"{path.name} contains a Windows path")

    def test_no_reference_to_the_build_machine_s_files_survives(self):
        # The built dataset records where each input was cached. That is for a
        # local run to trace a number back to a file; it says nothing a reader
        # of the site needs and something about the machine that built it.
        for path in self.all_files():
            if path.suffix != ".json":
                continue
            text = path.read_text("utf-8", errors="ignore")
            for forbidden in ("data/raw", "data/processed", "data/manifests"):
                self.assertNotIn(forbidden, text,
                                 f"{path.name} points into the local data tree")

    def test_path_bearing_keys_are_removed_rather_than_emptied(self):
        stripped = site.strip_local_paths({
            "keep": 1,
            "metadata_cache_path": "data/raw/metadata/B01003.json",
            "value_files": {"m": "values/m.json"},
            "nested": [{"source_dir": "/tmp/x", "label": "kept"}],
        })
        self.assertEqual(stripped,
                         {"keep": 1, "nested": [{"label": "kept"}]})

    def test_no_credential_shaped_value_is_written(self):
        for path in self.all_files():
            if path.suffix not in (".html", ".js", ".json"):
                continue
            text = path.read_text("utf-8", errors="ignore")
            for forbidden in ("CENSUS_API_KEY", "api_key=", "&key=", "Bearer "):
                self.assertNotIn(forbidden, text,
                                 f"{path.name} looks like it carries a credential")

    def test_the_manifest_names_the_source_and_the_release(self):
        manifest = self.data("manifest.json")
        self.assertEqual(manifest["release"]["period_label"], "2019-2023 ACS")
        self.assertIn("Census Bureau", manifest["source"]["provider"])
        self.assertTrue(manifest["source"]["citation"])
        self.assertTrue(manifest["source"]["tables"])
        self.assertEqual(manifest["base_path"], "/census-explorer/")
        self.assertIn("No raw retrieval cache", manifest["contents"])

    def test_the_manifest_records_what_this_copy_cannot_do(self):
        manifest = self.data("manifest.json")
        for key in ("export", "projects"):
            self.assertIn(key, manifest["unsupported_here"])
            self.assertTrue(manifest["unsupported_here"][key].strip())

    def test_every_published_file_carries_a_digest(self):
        digests = self.data("digests.json")["files"]
        for path in self.all_files():
            rel = path.relative_to(self.out).as_posix()
            if path.suffix in (".json", ".js", ".css", ".html") \
                    and rel != "data/digests.json":
                self.assertIn(rel, digests, f"{rel} has no digest")

    def test_a_fixture_build_is_labelled_as_fixture_data(self):
        # Never publishable as a finding, and the manifest has to say so in
        # the file itself, not only in the interface.
        with temp_root() as root, offline():
            cfg = stage_project(root)
            build(root, cfg)
            processed = root / "data/processed/testrel/dataset.json"
            doc = json.loads(processed.read_text("utf-8"))
            doc["data_mode"] = "fixture"
            processed.write_text(json.dumps(doc), encoding="utf-8")
            out = root / "fixture-site"
            site.build(root, out, "testrel", log=lambda *a: None)
            manifest = json.loads(
                (out / "data" / "manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["data_mode"], "fixture")
            self.assertIn("FIXTURE DATA", manifest["warning"])
            self.assertEqual(
                json.loads((out / "data" / "status.json").read_text("utf-8"))
                ["data_mode"], "fixture")


# ---------------------------------------------------------------------------
# 3. It carries the same numbers as the service
# ---------------------------------------------------------------------------

class SameNumbersTests(_Built):
    def test_the_value_encoding_loses_nothing(self):
        geoids = self.data("areas.json")["geoids"]
        for measure_id in ("foreign_born_population", "naturalized_share_of_foreign_born"):
            encoded = self.data("values", f"{measure_id}.json")
            notes = encoded["notes"]
            rebuilt = {}
            for i, geoid in enumerate(geoids):
                record = {}
                for key, column in encoded["numeric"].items():
                    if column[i] is not None:
                        record[key] = column[i]
                for key, column in encoded["text"].items():
                    if column[i] is not None:
                        record[key] = notes[column[i]]
                if encoded.get("ctl", [0] * len(geoids))[i] == 1:
                    record["ctl"] = True
                flags = (encoded.get("flags") or [[]] * len(geoids))[i]
                if flags:
                    record["flags"] = flags
                rebuilt[geoid] = record
            expected = {g: {k: v for k, v in record.items() if v is not None}
                        for g, record in
                        self.state.values("testrel", measure_id).items()}
            self.assertEqual(rebuilt, expected,
                             f"{measure_id} does not survive the encoding")

    def test_only_null_valued_keys_are_dropped_by_the_encoding(self):
        # The encoding is allowed to drop "m": None, because that says the same
        # thing as having no "m" at all. It is not allowed to drop anything
        # else, and this is what stops that rule from widening.
        geoids = self.data("areas.json")["geoids"]
        for measure_id in ("foreign_born_population",
                           "naturalized_share_of_foreign_born"):
            encoded = self.data("values", f"{measure_id}.json")
            columns = set(encoded["numeric"]) | set(encoded["text"])
            for geoid, record in self.state.values("testrel", measure_id).items():
                for key, value in record.items():
                    if key in ("ctl", "flags"):
                        continue
                    if value is not None:
                        self.assertIn(key, columns,
                                      f"{measure_id}/{geoid}: {key} was dropped")

    def test_an_unknown_value_field_fails_the_build_rather_than_vanishing(self):
        with self.assertRaises(ValueError) as caught:
            site._encode_values({"36005": {"e": 1.0, "surprise": 2}}, ["36005"])
        self.assertIn("surprise", str(caught.exception))

    def test_the_reference_is_the_one_the_service_computes(self):
        from census_explorer import benchmark as benchmark_mod
        areas = [a["geoid"] for a in self.data("dataset.json")["areas"]
                 if a["level"] == "county"]
        for measure_id in ("foreign_born_population", "naturalized_share_of_foreign_born"):
            sel = server.build_selection(self.state, {
                "release_id": "testrel", "measure_id": measure_id,
                "level": "county", "areas": areas})
            expected = server.build_benchmark(
                self.state, sel, benchmark_mod.NYC).to_json()
            published = self.data("reference", f"{measure_id}.json")["nyc"]["county"]
            self.assertEqual(published, expected)

    def test_a_reference_this_build_cannot_compute_is_listed_and_refused(self):
        from census_explorer import benchmark as benchmark_mod
        options = {o["benchmark_id"]: o
                   for o in self.data("benchmarks", "county.json")["benchmarks"]}
        self.assertIn(benchmark_mod.SELECTED, options,
                      "an option the interface offers was left out silently")
        selected = options[benchmark_mod.SELECTED]
        self.assertFalse(selected["available_in_static_build"])
        self.assertIn("local service", selected["unavailable_reason"])
        self.assertTrue(options[benchmark_mod.NYC]["available_in_static_build"])

    def test_the_catalog_offers_the_same_measures_as_the_service(self):
        published = self.data("catalog.json")
        expected = server.measure_catalog(self.state, "testrel")
        self.assertEqual(published, expected)

    def test_the_period_label_is_never_shortened_to_a_year(self):
        for name in ("manifest.json", "status.json", "dataset.json"):
            text = (self.out / "data" / name).read_text("utf-8")
            self.assertNotRegex(text, r'"period_label":"20\d\d"')


# ---------------------------------------------------------------------------
# 4. The browser reproduces the service, case by case
# ---------------------------------------------------------------------------

class RoundTripTests(_Built):
    CASES = [
        ("all boroughs, a count", "foreign_born_population", "county", None, None),
        ("all boroughs, a share", "naturalized_share_of_foreign_born",
         "county", None, None),
        ("one borough, no reference", "naturalized_share_of_foreign_born",
         "county", 1, None),
        ("one borough, with a reference", "naturalized_share_of_foreign_born",
         "county", 1, {"available": True,
                       "label": "New York City (all five boroughs)"}),
        ("one borough, reference unavailable", "foreign_born_population",
         "county", 1, {"available": False,
                       "label": "New York City (all five boroughs)"}),
        ("two boroughs", "foreign_born_share", "county", 2, None),
        ("an area with no published estimate", "foreign_born_share",
         "county", 3, None),
    ]

    def build_expected(self, bundle: Path) -> None:
        dataset = self.state.dataset("testrel")
        areas = [a["geoid"] for a in dataset["areas"] if a["level"] == "county"]
        area_records = self.state.areas("testrel")
        cases = []
        values_dump = {}
        for name, measure_id, level, limit, benchmark in self.CASES:
            selected = areas if limit is None else areas[:limit]
            sel = server.build_selection(self.state, {
                "release_id": "testrel", "measure_id": measure_id,
                "level": level, "areas": selected})
            values = self.state.values("testrel", measure_id)
            values_dump[measure_id] = values
            report = server.quality_report(self.state, sel, values,
                                           benchmark=benchmark)
            csv_text = exports.build_csv_for(
                sel, area_records, {("testrel", measure_id): values})
            cases.append({
                "name": name, "measure_id": measure_id, "level": level,
                "areas": list(sel.areas), "benchmark": benchmark,
                "expected_uncertainty": report["uncertainty"],
                "expected_comparison": report["comparison"],
                "expected_freshness": report["freshness"],
                "expected_csv": csv_text,
            })
        (bundle / "expected.json").write_text(
            json.dumps({"cases": cases, "values": values_dump}), encoding="utf-8")

    def test_the_browser_matches_the_service_for_every_case(self):
        node = _node()
        if node is None:
            self.skipTest(
                "Node is not installed, so the published site's own code "
                "cannot be exercised here. Install Node, or set "
                "CENSUS_EXPLORER_NODE, to run this round-trip.")
        bundle = self.root / "roundtrip"
        bundle.mkdir(exist_ok=True)
        if not (bundle / "site").exists():
            import shutil
            shutil.copytree(self.out, bundle / "site")
        self.build_expected(bundle)
        proc = subprocess.run(
            [node, "--test", "--test-reporter=tap",
             str(REPO / "tests" / "js" / "roundtrip.test.js")],
            cwd=REPO, capture_output=True, text=True, timeout=300,
            env={**os.environ, "NODE_OPTIONS": "",
                 "CENSUS_EXPLORER_ROUNDTRIP": str(bundle)},
        )
        if proc.returncode != 0:
            self.fail("the published site does not reproduce the service:\n"
                      + proc.stdout[-6000:] + "\n" + proc.stderr[-2000:])
        self.assertRegex(proc.stdout, r"(?m)^#\s*fail\s+0\s*$")


if __name__ == "__main__":
    unittest.main()
