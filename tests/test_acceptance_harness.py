"""The live browser acceptance scripts refuse anything but a live build.

The scripts in tests/acceptance run against a live build and are not part of
this offline suite (see docs/ACCEPTANCE.md). What is tested here is their
gate: before Playwright is loaded, before an output directory exists and
before any report is written, each target's own status metadata must say
data_mode "live". A fixture build, a missing or unknown mode, an HTTP error
or a response that is not a JSON object is refused with exit status 2.

Each case runs the real script under Node with --preflight-only against a
loopback stub that returns a controlled status response. No browser is
started and nothing is downloaded. Skips without Node.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.test_ui_core import _node

HERE = Path(__file__).resolve().parent / "acceptance"
SCRIPTS = ("briefs.mjs", "journeys.mjs")


class _Stub:
    """A loopback server answering one path with a chosen status and body."""

    def __init__(self):
        self.routes: dict[str, tuple[int, bytes]] = {}
        self.seen: list[str] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                stub.seen.append(self.path)
                code, body = stub.routes.get(self.path, (404, b"not found"))
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def status(mode_or_body) -> tuple[int, bytes]:
    if isinstance(mode_or_body, bytes):
        return 200, mode_or_body
    return 200, json.dumps(mode_or_body).encode()


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.node = _node()
        if self.node is None:
            self.skipTest("Node is not installed")
        self.stub = _Stub()
        self.addCleanup(self.stub.close)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def run_script(self, script, *targets):
        out = Path(self._tmp.name) / f"out-{script}-{len(self.stub.seen)}"
        args = [self.node, str(HERE / script), *targets, "--out", str(out), "--preflight-only"]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
        return proc, out

    def assertRefused(self, script, *targets, reason):
        proc, out = self.run_script(script, *targets)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("REFUSED", proc.stdout)
        self.assertIn(reason, proc.stdout)
        self.assertIn("nothing was rendered or reported", proc.stderr)
        self.assertFalse(out.exists(), "a refused run must not create its output directory")

    # -- the reviewed gap: a local target alone ------------------------------

    def test_a_local_fixture_service_is_refused(self):
        self.stub.routes["/api/status"] = status({"data_mode": "fixture"})
        for script in SCRIPTS:
            with self.subTest(script=script):
                self.assertRefused(script, "--local", self.stub.base + "/",
                                   reason='data_mode is "fixture"')
        self.assertIn("/api/status", self.stub.seen)
        # Nothing past the gate was requested: no brief, no page.
        self.assertEqual(set(self.stub.seen), {"/api/status"})

    def test_missing_or_unknown_modes_are_refused(self):
        cases = {
            "no data_mode": (status({"releases": []}), "has no data_mode"),
            "unknown mode": (status({"data_mode": "synthetic"}), 'data_mode is "synthetic"'),
            "null mode": (status({"data_mode": None}), "data_mode is null"),
            "not JSON": (status(b"<html>hello</html>"), "not JSON"),
            "a JSON list": (status(["live"]), "not a JSON object"),
            "HTTP error": ((500, b"{}"), "HTTP 500"),
            "no status route": ((404, b"{}"), "HTTP 404"),
        }
        for name, (route, reason) in cases.items():
            self.stub.routes["/api/status"] = route
            for script in SCRIPTS:
                with self.subTest(case=name, script=script):
                    self.assertRefused(script, "--local", self.stub.base + "/", reason=reason)

    def test_an_unreachable_target_is_refused(self):
        closed = _Stub()
        base = closed.base
        closed.close()
        self.assertRefused("briefs.mjs", "--local", base + "/", reason="could not reach it")

    # -- a live target passes the gate --------------------------------------

    def test_a_live_local_service_passes_the_gate(self):
        self.stub.routes["/api/status"] = status({"data_mode": "live", "releases": []})
        for script in SCRIPTS:
            with self.subTest(script=script):
                proc, out = self.run_script(script, "--local", self.stub.base + "/")
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn('live  local', proc.stdout)
                self.assertFalse(out.exists(), "--preflight-only renders and reports nothing")

    def test_a_static_site_is_checked_through_its_status_file(self):
        base = self.stub.base + "/census-explorer/"
        self.stub.routes["/census-explorer/data/status.json"] = status({"data_mode": "live"})
        proc, _ = self.run_script("briefs.mjs", "--static", base)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.stub.routes["/census-explorer/data/status.json"] = status({"data_mode": "fixture"})
        self.assertRefused("briefs.mjs", "--static", base, reason='data_mode is "fixture"')

    def test_one_refused_target_refuses_the_whole_run(self):
        self.stub.routes["/api/status"] = status({"data_mode": "live"})
        self.stub.routes["/census-explorer/data/status.json"] = status({"data_mode": "fixture"})
        self.assertRefused("journeys.mjs", "--local", self.stub.base + "/",
                           "--static", self.stub.base + "/census-explorer/",
                           reason='data_mode is "fixture"')


class HarnessSyntaxTests(unittest.TestCase):
    def test_every_script_parses(self):
        node = _node()
        if node is None:
            self.skipTest("Node is not installed")
        scripts = sorted(HERE.glob("*.mjs"))
        self.assertEqual([p.name for p in scripts], ["briefs.mjs", "journeys.mjs", "lib.mjs"])
        for script in scripts:
            proc = subprocess.run([node, "--check", str(script)],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, f"{script.name}: {proc.stderr}")


if __name__ == "__main__":
    unittest.main()
