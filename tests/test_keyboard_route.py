"""The keyboard route to the result actions stays valid.

Offline and without a browser: the markup and script are read as text. What
the route does in a real browser (key-press counts, focus visibility,
disabled actions while loading) is measured by the live acceptance journey
in tests/acceptance/journeys.mjs; see docs/ACCEPTANCE.md.
"""

from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser

from census_explorer import server

WEB = server.WEB_DIR


class _Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: dict[str, dict] = {}
        self.skips: list[dict] = []
        self.tabindexes: list[str] = []
        self.order: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids[a["id"]] = {"tag": tag, **a}
            self.order.append(a["id"])
        if tag == "a" and "data-skip" in a:
            self.skips.append(a)
        if "tabindex" in a:
            self.tabindexes.append(a["tabindex"])


class KeyboardRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.js = (WEB / "app.js").read_text(encoding="utf-8")
        cls.page = _Collect()
        cls.page.feed(cls.html)

    def test_every_skip_link_names_a_real_destination(self):
        self.assertGreaterEqual(len(self.page.skips), 5)
        for link in self.page.skips:
            target = link["href"].lstrip("#")
            self.assertIn(target, self.page.ids, link)
            self.assertTrue(link["data-skip"] in ("stage", "table", "actions"), link)

    def test_destinations_can_take_focus_without_joining_the_tab_order(self):
        for name in ("stage", "result-actions", "table-title"):
            self.assertEqual(self.page.ids[name].get("tabindex"), "-1", name)
        group = self.page.ids["result-actions"]
        self.assertEqual(group.get("role"), "group")
        self.assertIn("Result actions", group.get("aria-label", ""))

    def test_the_actions_are_inside_the_group(self):
        start = self.html.index('id="result-actions"')
        end = self.html.index("</div>", start)
        block = self.html[start:end]
        for button in ("btn-save", "btn-share", "btn-brief", "btn-export"):
            self.assertIn(f'id="{button}"', block, button)
        self.assertNotIn('id="btn-method"', block, "Method & sources is not a result action")

    def test_the_route_sits_next_to_the_table_and_after_the_actions(self):
        order = self.page.order
        self.assertLess(order.index("data-table"), order.index("table-skip-actions"))
        self.assertLess(order.index("table-skip-actions"), order.index("table-pager"))
        self.assertLess(order.index("btn-export"), order.index("actions-skip-table"))

    def test_no_positive_tabindex_anywhere(self):
        self.assertTrue(all(int(t) <= 0 for t in self.page.tabindexes), self.page.tabindexes)
        self.assertFalse(re.search(r"tabIndex\s*=\s*[1-9]", self.js))
        self.assertFalse(re.search(r"setAttribute\('tabindex',\s*'[1-9]", self.js))

    def test_no_global_keyboard_shortcut_is_intercepted(self):
        # The only document-level key handler closes panels on Escape.
        handlers = re.findall(r"document\.addEventListener\('keydown'[\s\S]{0,160}", self.js)
        self.assertEqual(len(handlers), 1)
        self.assertIn("if (e.key !== 'Escape') return;", handlers[0])

    def test_skip_links_move_focus_instead_of_rewriting_the_address(self):
        self.assertIn("function wireSkipLinks()", self.js)
        body = self.js[self.js.index("function wireSkipLinks()"):]
        body = body[: body.index("\n}\n")]
        self.assertIn("e.preventDefault()", body)
        self.assertIn(".focus()", body)
        self.assertNotIn("disabled = false", body, "the route must never enable an action")


if __name__ == "__main__":
    unittest.main()
