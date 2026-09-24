"""The live browser acceptance scripts stay loadable.

They run against a live build and are not part of this offline suite (see
docs/ACCEPTANCE.md). This only checks that they still parse, so a change
elsewhere cannot leave the recipe silently broken. Skips without Node.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from tests.test_ui_core import _node

HERE = Path(__file__).resolve().parent / "acceptance"


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

    def test_the_scripts_refuse_fixture_data(self):
        for name in ("journeys.mjs", "briefs.mjs"):
            text = (HERE / name).read_text(encoding="utf-8")
            self.assertIn("!== 'live'", text, name)


if __name__ == "__main__":
    unittest.main()
