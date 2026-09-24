"""Printed figures: text that must not collide on paper.

Found by looking at the printed brief (docs/ACCEPTANCE.md): the chart's tick
labels were drawn over its subtitle. Offline; no browser needed.
"""

from __future__ import annotations

import re
import unittest

from census_explorer import figures


class GroupChartTests(unittest.TestCase):
    def svg(self, data_mode="live"):
        return figures.group_chart_svg(
            rows=[{"label": "Bronx", "values": [{"e": 53.7, "m": 0.8, "note": ""}]}],
            title="Naturalized share", subtitle="2019-2023 ACS · county · bars show the "
            "estimate, whiskers the 90% margin of error", unit="percent",
            source_lines=["Source"], series_labels=["2019-2023 ACS"], data_mode=data_mode)

    def test_tick_labels_clear_the_subtitle(self):
        for mode in ("live", "fixture"):
            svg = self.svg(mode)
            subtitle = re.search(r'<text x="16" y="([\d.]+)" font-size="12"', svg)
            ticks = [float(y) for y in re.findall(
                r'<text x="[\d.]+" y="([\d.]+)" text-anchor="middle" font-size="10"', svg)]
            self.assertTrue(subtitle and ticks, "figure layout changed; update this test")
            # A 10 px label's top is about 8 px above its baseline; the 12 px
            # subtitle's descenders reach about 3 px below its own.
            self.assertGreaterEqual(min(ticks) - 8, float(subtitle.group(1)) + 3, mode)


if __name__ == "__main__":
    unittest.main()
