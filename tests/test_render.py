"""Value rendering, the markdown subset and small helpers."""

import unittest

from _bootstrap import build


class TestTiles(unittest.TestCase):
    def test_number_shows_value_and_unit_once(self):
        html = build.build_tiles([{"label": "n", "value": 1284, "unit": "memories"}])
        self.assertIn(">1284<", html)
        self.assertIn(">memories<", html)
        self.assertEqual(html.count("memories"), 1)

    def test_string_value_is_shown_verbatim(self):
        html = build.build_tiles([{"label": "status", "value": "green"}])
        self.assertIn(">green<", html)
        self.assertNotIn(build.DASH, html)

    def test_percent_unit_attaches_without_space(self):
        html = build.build_tiles([{"label": "rate", "value": 94, "unit": "%"}])
        self.assertIn('>94<span class="tile-unit tight">%</span>', html)
        self.assertEqual(build.fmt_compact(120, "%"), "120%")
        self.assertEqual(build.fmt_compact(120, "ms"), "120 ms")

    def test_null_value_renders_dash(self):
        html = build.build_tiles([{"label": "x", "value": None, "unit": "GB"}])
        self.assertIn(build.DASH, html)
        self.assertIn("unknown", html)
        self.assertNotIn(">0<", html)

    def test_delta_colours(self):
        up = build.build_tiles([{"label": "a", "value": 1, "delta": 5}])
        down = build.build_tiles([{"label": "a", "value": 1, "delta": -5}])
        flat = build.build_tiles([{"label": "a", "value": 1, "delta": 0}])
        self.assertIn('class="tile-delta up"', up)
        self.assertIn('class="tile-delta down"', down)
        self.assertIn('class="tile-delta flat"', flat)

    def test_empty_tiles(self):
        self.assertIn("empty", build.build_tiles([]))


class TestMarkdown(unittest.TestCase):
    def test_headings(self):
        html = build.render_markdown("# One\n\n### Three")
        self.assertIn("<h1>One</h1>", html)
        self.assertIn("<h3>Three</h3>", html)

    def test_bullets(self):
        html = build.render_markdown("- a\n- b\n* c")
        self.assertEqual(html.count("<li>"), 3)
        self.assertIn("<ul>", html)

    def test_bold(self):
        self.assertIn("<strong>bold</strong>", build.render_markdown("this is **bold** text"))

    def test_links(self):
        html = build.render_markdown("see [docs](https://example.com/a)")
        self.assertIn('<a href="https://example.com/a">docs</a>', html)

    def test_relative_links_allowed(self):
        html = build.render_markdown("[home](index.html)")
        self.assertIn('<a href="index.html">home</a>', html)

    def test_unsafe_link_renders_as_text(self):
        html = build.render_markdown("[click](javascript:alert(1))")
        self.assertNotIn("<a", html)
        self.assertIn("click", html)

    def test_html_is_escaped(self):
        html = build.render_markdown("<b>not html</b>")
        self.assertIn("&lt;b&gt;", html)
        self.assertNotIn("<b>", html)

    def test_wrapped_bullet_is_joined(self):
        html = build.render_markdown("- first line\n  continued here")
        self.assertEqual(html.count("<li>"), 1)
        self.assertIn("first line continued here", html)

    def test_paragraph(self):
        html = build.render_markdown("hello\nworld")
        self.assertIn("<p>hello world</p>", html)


class TestSafeUrl(unittest.TestCase):
    def test_allowed(self):
        for url in ("https://x.example/a", "http://x.example", "mailto:a@b.example",
                    "index.html", "../pipeline.html", "#frag", "/abs/path"):
            with self.subTest(url=url):
                self.assertIsNotNone(build.safe_url(url))

    def test_rejected(self):
        for url in ("javascript:alert(1)", "data:text/html,x", "vbscript:x", "", None):
            with self.subTest(url=url):
                self.assertIsNone(build.safe_url(url))


class TestDecisions(unittest.TestCase):
    def test_recommended_by_letter(self):
        html = build.build_decisions([{
            "n": 1, "question": "q", "recommended": "B",
            "options": ["A) a", "B) b", "C) c"],
        }])
        self.assertEqual(html.count('class="rec"'), 1)
        self.assertIn("recommended", html)

    def test_recommended_by_text(self):
        html = build.build_decisions([{
            "n": 2, "question": "q", "recommended": "Keep it",
            "options": ["Drop it", "Keep it"],
        }])
        self.assertEqual(html.count('class="rec"'), 1)

    def test_recommended_by_prefix(self):
        html = build.build_decisions([{
            "n": 3, "question": "q", "recommended": "Warn only",
            "options": ["Block", "Warn only for two weeks", "Nothing"],
        }])
        self.assertEqual(html.count('class="rec"'), 1)


class TestLists(unittest.TestCase):
    def test_risk_string_and_object(self):
        html = build.build_simple_list(["plain risk", {"text": "bad", "severity": "high"}], "risks")
        self.assertIn("plain risk", html)
        self.assertIn("sev-high", html)

    def test_next_assignee(self):
        html = build.build_simple_list([{"text": "do it", "assignee": "team-a"}], "next24h")
        self.assertIn("team-a", html)
        self.assertIn('class="assignee"', html)

    def test_evidence_link(self):
        html = build.build_evidence([{"label": "dash", "link": "https://example.com/d"}])
        self.assertIn('<a href="https://example.com/d">dash</a>', html)


if __name__ == "__main__":
    unittest.main()
