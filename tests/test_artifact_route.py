"""The read-only route that hands an export bundle to the browser.

It exists so a reader can open the brief and save the CSV without copying a
path into a terminal. That makes it the one place where a local HTTP request
names a file on disk, so it has to be exactly as narrow as its purpose: inside
the artifacts directory, only the file types an export writes, read-only, and
proof against a path or a symbolic link that tries to leave.
"""

from __future__ import annotations

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from census_explorer import questions, server
from tests.helpers import offline, temp_root
from tests.test_end_to_end import build, stage_project


class _Service(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_root()
        self.root = self._tmp.__enter__()
        self._offline = offline()
        self._offline.__enter__()
        self.cfg = stage_project(self.root)
        build(self.root, self.cfg)
        state = server.ServiceState(self.root)
        state.config = self.cfg
        handler = type("H", (server.Handler,), {"state": state})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.state = state

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._offline.__exit__(None, None, None)
        self._tmp.__exit__(None, None, None)

    def fetch(self, path):
        """Return (status, body, headers) without raising on an error status."""
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{self.port}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def export_one_borough(self):
        return server.run_export(self.state, {
            "release_id": "testrel",
            "measure_id": "naturalized_share_of_foreign_born",
            "level": "county", "areas": ["36005"],
            "question_id": questions.WHO_LIVES_HERE, "include_figure": True})


class ExportDeliveryTests(_Service):
    def test_an_export_names_every_file_in_a_form_a_browser_can_open(self):
        res = self.export_one_borough()
        urls = {a["name"]: a["url"] for a in res["artifacts"]}
        self.assertEqual(set(urls), {"brief.html", "data.csv", "provenance.json",
                                     "figure.svg", "README.txt"})
        for name, url in urls.items():
            self.assertTrue(url.startswith("/artifacts/"), f"{name}: {url}")
            status, body, _ = self.fetch(url)
            self.assertEqual(status, 200, f"{name} was not served: {body[:200]!r}")
            self.assertTrue(body, f"{name} came back empty")

    def test_every_file_says_in_words_what_it_is(self):
        for artifact in self.export_one_borough()["artifacts"]:
            self.assertTrue(artifact["label"].strip())
            self.assertNotEqual(artifact["label"], artifact["name"],
                                f"{artifact['name']} has no description")

    def test_the_served_brief_is_the_one_that_was_exported(self):
        res = self.export_one_borough()
        url = next(a["url"] for a in res["artifacts"] if a["name"] == "brief.html")
        _, body, headers = self.fetch(url)
        html = body.decode("utf-8")
        self.assertIn("Bronx", html)
        self.assertIn("text/html", headers["Content-Type"])
        # A bundle is a snapshot; only a saved project re-checks its inputs.
        self.assertIn("generated snapshot, not a saved project", html)

    def test_asking_to_download_offers_the_file_rather_than_rendering_it(self):
        res = self.export_one_borough()
        url = next(a["url"] for a in res["artifacts"] if a["name"] == "data.csv")
        _, _, plain = self.fetch(url)
        self.assertNotIn("Content-Disposition", plain)
        _, body, headers = self.fetch(url + "?download=1")
        self.assertIn('attachment; filename="data.csv"',
                      headers["Content-Disposition"])
        self.assertIn("36005", body.decode("utf-8"))

    def test_the_served_data_covers_exactly_the_exported_selection(self):
        res = self.export_one_borough()
        url = next(a["url"] for a in res["artifacts"] if a["name"] == "data.csv")
        _, body, _ = self.fetch(url)
        rows = [r for r in body.decode("utf-8").strip().split("\n")[1:] if r]
        self.assertEqual(len(rows), 1)
        self.assertIn("36005", rows[0])


class ContainmentTests(_Service):
    def setUp(self):
        super().setUp()
        self.export_one_borough()
        (self.root / "secret.txt").write_text("a credential", encoding="utf-8")
        (self.root / "config" / "project.json")  # exists already

    def assert_refused(self, path):
        status, body, _ = self.fetch(path)
        self.assertIn(status, (403, 404),
                      f"{path} was served with {status}: {body[:200]!r}")
        self.assertNotIn(b"a credential", body)
        return status

    def test_a_traversal_cannot_reach_a_file_outside_the_artifacts_directory(self):
        for path in ("/artifacts/../secret.txt",
                     "/artifacts/../../etc/hostname",
                     "/artifacts/%2e%2e/secret.txt",
                     "/artifacts/%2e%2e%2fsecret.txt",
                     "/artifacts/a/../../secret.txt",
                     "/artifacts/./../config/project.json"):
            self.assert_refused(path)

    def test_an_absolute_path_is_refused(self):
        self.assert_refused("/artifacts//etc/hostname")
        self.assert_refused("/artifacts/" + str(self.root / "secret.txt"))

    @unittest.skipUnless(hasattr(os, "symlink"), "no symbolic links on this platform")
    def test_a_symbolic_link_cannot_be_used_to_escape(self):
        bundle = next((self.root / "artifacts").iterdir())
        link = bundle / "escape.txt"
        try:
            link.symlink_to(self.root / "secret.txt")
        except (OSError, NotImplementedError):
            self.skipTest("this platform does not allow creating symbolic links")
        rel = link.relative_to(self.root / "artifacts").as_posix()
        self.assert_refused(f"/artifacts/{rel}")

    @unittest.skipUnless(hasattr(os, "symlink"), "no symbolic links on this platform")
    def test_a_linked_directory_cannot_be_used_to_escape(self):
        link = self.root / "artifacts" / "out"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this platform does not allow creating symbolic links")
        self.assert_refused("/artifacts/out/secret.txt")

    def test_a_file_type_an_export_never_writes_is_refused(self):
        bundle = next((self.root / "artifacts").iterdir())
        (bundle / "notes.md").write_text("# not part of a bundle", encoding="utf-8")
        (bundle / "script.py").write_text("print('no')", encoding="utf-8")
        rel = bundle.relative_to(self.root / "artifacts").as_posix()
        self.assertEqual(self.assert_refused(f"/artifacts/{rel}/notes.md"), 403)
        self.assertEqual(self.assert_refused(f"/artifacts/{rel}/script.py"), 403)

    def test_a_directory_is_not_listed(self):
        bundle = next((self.root / "artifacts").iterdir())
        rel = bundle.relative_to(self.root / "artifacts").as_posix()
        self.assert_refused(f"/artifacts/{rel}")
        self.assert_refused("/artifacts/")

    def test_a_file_that_does_not_exist_says_so_without_naming_the_disk(self):
        status, body, _ = self.fetch("/artifacts/nope/data.csv")
        self.assertEqual(status, 404)
        text = json.loads(body)["error"]
        self.assertNotIn(str(self.root), text)

    def test_the_route_is_read_only(self):
        url = f"http://127.0.0.1:{self.port}/artifacts/anything.csv"
        req = urllib.request.Request(
            url, data=b"{}", method="POST",
            headers={"Content-Type": "application/json",
                     "Host": f"127.0.0.1:{self.port}"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=10)
        self.assertIn(caught.exception.code, (403, 404, 405))


if __name__ == "__main__":
    unittest.main()
