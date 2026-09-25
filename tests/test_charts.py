"""Every chart kind must render a well-formed, self-contained inline SVG."""

import unittest
import xml.etree.ElementTree as ET

from _bootstrap import build


def _parse(svg: str):
    return ET.fromstring(svg)


SAMPLE_RESULTS = {
    "bar": {
        "title": "bar chart",
        "kind": "bar",
        "unit": "x",
        "series": [{"label": "a", "value": 3}, {"label": "b", "value": None}, {"label": "c", "value": 7}],
    },
    "hbar": {
        "title": "hbar chart",
        "kind": "hbar",
        "series": [{"label": "alpha", "value": 5}, {"label": "beta", "value": None}],
    },
    "line": {
        "title": "line chart",
        "kind": "line",
        "unit": "ms",
        "x": ["m", "t", "w"],
        "series": [{"label": "p95", "points": [10, None, 30]}],
    },
    "stacked": {
        "title": "stacked chart",
        "kind": "stacked",
        "series": [
            {"label": "am", "segments": [{"label": "work", "value": 5}, {"label": "idle", "value": None}]},
            {"label": "pm", "segments": [{"label": "work", "value": 8}, {"label": "idle", "value": 2}]},
        ],
    },
    "quota": {
        "title": "quota chart",
        "kind": "quota",
        "unit": "calls",
        "series": [
            {"label": "route-a", "used": 90, "limit": 100},
            {"label": "route-b", "used": 150, "limit": 100},
            {"label": "route-c", "used": None, "limit": 100},
        ],
    },
}


class TestCharts(unittest.TestCase):
    def test_every_kind_is_registered(self):
        self.assertEqual(set(build.CHART_RENDERERS), {"bar", "hbar", "line", "stacked", "quota"})

    def test_every_kind_renders_valid_svg(self):
        for kind, result in SAMPLE_RESULTS.items():
            with self.subTest(kind=kind):
                svg = build.CHART_RENDERERS[kind](result)
                root = _parse(svg)  # raises if not well-formed XML
                self.assertEqual(root.tag, "svg")
                self.assertIn('role="img"', svg)
                self.assertIn("viewBox=", svg)

    def test_svg_makes_no_external_reference(self):
        for kind, result in SAMPLE_RESULTS.items():
            with self.subTest(kind=kind):
                svg = build.CHART_RENDERERS[kind](result)
                self.assertNotIn("http", svg)
                self.assertNotIn("<script", svg)

    def test_unknown_values_render_as_dash_not_zero(self):
        for kind, result in SAMPLE_RESULTS.items():
            with self.subTest(kind=kind):
                svg = build.CHART_RENDERERS[kind](result)
                self.assertIn(build.DASH, svg)

    def test_quota_hit_limit_badge(self):
        svg = build.CHART_RENDERERS["quota"](SAMPLE_RESULTS["quota"])
        self.assertIn("HIT LIMIT", svg)
        # a route at 90/100 must not get the badge twice
        self.assertEqual(svg.count("HIT LIMIT"), 1)

    def test_empty_series_still_valid(self):
        for kind in build.CHART_RENDERERS:
            with self.subTest(kind=kind):
                svg = build.CHART_RENDERERS[kind]({"title": "empty", "kind": kind, "series": []})
                self.assertEqual(_parse(svg).tag, "svg")

    def test_all_null_series_renders(self):
        result = {"title": "nulls", "kind": "bar",
                  "series": [{"label": "a", "value": None}, {"label": "b", "value": None}]}
        svg = build.CHART_RENDERERS["bar"](result)
        self.assertEqual(_parse(svg).tag, "svg")
        self.assertEqual(svg.count(build.DASH), 2)


class TestNumberFormatting(unittest.TestCase):
    def test_fmt_num_unknown(self):
        for bad in (None, "n/a", True, [], {}):
            with self.subTest(value=bad):
                self.assertEqual(build.fmt_num(bad), build.DASH)

    def test_fmt_num_trims(self):
        self.assertEqual(build.fmt_num(1000), "1000")
        self.assertEqual(build.fmt_num(2.5), "2.5")
        self.assertEqual(build.fmt_num(2.5000), "2.5")
        self.assertEqual(build.fmt_num(-3), "-3")


if __name__ == "__main__":
    unittest.main()
