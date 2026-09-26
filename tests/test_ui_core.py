"""Runs the browser module's deterministic tests, when Node is present.

`web/core.js` holds the page logic that has no DOM and no network: which of
several overlapping loads may commit, how values divide into shaded classes,
and what the map may claim about coverage. Those are the parts that break
silently, so they are tested rather than eyeballed.

They are written in JavaScript because that is the language they run in, and
executed by Node's own built-in test runner. Node is not a dependency of this
project — nothing in the package, the service, the offline Python tests or the
browser needs it — so this skips with a clear reason when it is absent, in the
same way the live network tests do.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
JS_TESTS = REPO / "tests" / "js" / "core.test.js"


def _node() -> str | None:
    """A Node executable, if this machine has one."""
    explicit = os.environ.get("CENSUS_EXPLORER_NODE")
    if explicit:
        return explicit if Path(explicit).exists() else None
    for name in ("node", "node.exe", "nodejs"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (Path("/opt/node22/bin/node"), Path("/usr/local/bin/node")):
        if candidate.exists():
            return str(candidate)
    return None


class BrowserCoreTests(unittest.TestCase):
    def test_the_pure_browser_logic_passes_its_own_tests(self):
        node = _node()
        if node is None:
            self.skipTest(
                "Node is not installed, so the browser module's tests cannot "
                "run here. They are not required for the service or the data "
                "pipeline; install Node, or set CENSUS_EXPLORER_NODE, to run "
                f"them: {node} --test tests/js/core.test.js")
        self.assertTrue(JS_TESTS.is_file(), f"missing {JS_TESTS}")
        # The reporter is pinned: newer Node versions default to the spec
        # reporter, whose "fail 0" line is decorated, so a test that scanned
        # the default output passed on one machine and failed on another for
        # no reason but the Node version. The exit status is the real signal;
        # the TAP plan is a second check on top of it.
        proc = subprocess.run(
            [node, "--test", "--test-reporter=tap", str(JS_TESTS)],
            cwd=REPO, capture_output=True, text=True, timeout=300,
            # No network is needed and none should be reachable from a test.
            env={**os.environ, "NODE_OPTIONS": ""},
        )
        if proc.returncode != 0:
            self.fail("the browser module's tests failed:\n"
                      + proc.stdout[-4000:] + "\n" + proc.stderr[-2000:])
        self.assertRegex(proc.stdout, r"(?m)^#\s*fail\s+0\s*$",
                         "the TAP summary did not report zero failures")


if __name__ == "__main__":
    unittest.main()
