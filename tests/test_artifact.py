"""The single-file artifact: fragment shape, theme contract, layout, content, safety."""

import contextlib
import io
import os
import re
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from _bootstrap import (CONTENT_DIR, DATA_DIR, DOCS_DIR, SCHEMA_PATH, TEMPLATES_DIR,
                        build, write_json)

ARTIFACT_NAME = "briefing.html"

#: Selectors that carry the palette (bare :root = light; the other two = dark).
PALETTE_SELECTORS = (":root", ':root:not([data-theme="light"])', ':root[data-theme="dark"]')

#: The section order the brief fixes for every project (titles from project.html).
SECTION_TITLES = ("Details", "Key numbers", "What happened", "Results",
                  "Decisions needed", "Risks", "Next 24 hours", "Evidence")

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_FILL_TOKENS = re.compile(r"^(?:var\(--[\w-]+\)|none|currentColor)$")
_COLOUR_LITERAL_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def fresh_env():
    """An environment without the CI-provided commit/build-time variables."""
    env = dict(os.environ)
    for key in ("GITHUB_SHA", "SOURCE_COMMIT", "SOURCE_DATE_EPOCH"):
        env.pop(key, None)
    return env


def build_artifact(out_dir, data=DATA_DIR, content=CONTENT_DIR, schema=SCHEMA_PATH,
                   templates=TEMPLATES_DIR, include_examples=True, **kwargs):
    return build.build_artifact(Path(data), Path(out_dir) / ARTIFACT_NAME, Path(templates),
                                Path(schema), Path(content), include_examples=include_examples,
                                **kwargs)


def artifact_text(out_dir, **kwargs):
    with mock.patch.dict(os.environ, fresh_env(), clear=True):
        build_artifact(out_dir, **kwargs)
    return (Path(out_dir) / ARTIFACT_NAME).read_text(encoding="utf-8")


def extract_style(html):
    match = re.search(r"<style>(.*)</style>", html, re.S)
    if match is None:
        raise AssertionError("no <style> block in the artifact")
    return match.group(1)


def _blocks(text):
    """Yield ``(prelude, body, start, end, body_start)`` for the top-level blocks of text."""
    i = 0
    while True:
        j = text.find("{", i)
        if j < 0:
            return
        depth = 0
        k = j
        while k < len(text):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        yield text[i:j].strip(), text[j + 1:k], i, k + 1, j + 1
        i = k + 1


def _declarations(body):
    decls = {}
    for chunk in body.split(";"):
        if ":" not in chunk:
            continue
        prop, _, value = chunk.partition(":")
        prop, value = prop.strip(), value.strip()
        if prop.startswith("--") or re.match(r"^[a-zA-Z-]+$", prop):
            decls[prop] = value
    return decls


def rules_of(css, media=None):
    """``[(media condition or None, selector, declarations)]`` for a stylesheet."""
    found = []
    for prelude, body, _start, _end, _body_start in _blocks(_COMMENT_RE.sub("", css)):
        if prelude.startswith("@media"):
            found.extend(rules_of(body, prelude[len("@media"):].strip()))
        else:
            found.append((media, prelude, _declarations(body)))
    return found


def declarations_for(rules, selector):
    matches = [decls for media, sel, decls in rules if media is None and sel == selector]
    if len(matches) != 1:
        raise AssertionError("expected one rule for %r, found %d" % (selector, len(matches)))
    return matches[0]


def _palette_spans(text, offset=0):
    spans = []
    for prelude, body, start, end, body_start in _blocks(text):
        if prelude in PALETTE_SELECTORS:
            spans.append((offset + start, offset + end))
        if prelude.startswith("@media"):
            spans.extend(_palette_spans(body, offset + body_start))
    return spans


def without_palette_blocks(css):
    """The stylesheet with the three palette blocks removed."""
    text = _COMMENT_RE.sub("", css)
    out, last = [], 0
    for start, end in sorted(_palette_spans(text)):
        out.append(text[last:start])
        last = end
    out.append(text[last:])
    return "".join(out)


def svg_blocks(html):
    return re.findall(r"<svg\b.*?</svg>", html, re.S)


def _rule_applies(selector, tag, classes):
    """True when the rightmost compound of ``selector`` can match this element."""
    for one in selector.split(","):
        compound = one.split()[-1]
        compound = re.split(r"[:[]", compound)[0]
        if not compound:
            continue
        name = re.match(r"^[a-zA-Z][\w-]*", compound)
        if name and name.group(0) != tag:
            continue
        wanted = re.findall(r"\.([\w-]+)", compound)
        if wanted and any(cls not in classes for cls in wanted):
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# the built artifact, shared by the read-only test cases
# ---------------------------------------------------------------------------


class ArtifactTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.html = artifact_text(cls._tmp.name)
        cls.style = extract_style(cls.html)
        cls.rules = rules_of(cls.style)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @property
    def section_ids(self):
        return re.findall(r'<section id="([^"]+)"', self.html)

    def project_block(self, slug):
        pattern = r'<section id="%s" class="project">.*?<p class="back-top">.*?</p>' % re.escape(slug)
        match = re.search(pattern, self.html, re.S)
        self.assertIsNotNone(match, "no project block for %s" % slug)
        return match.group(0)


# ---------------------------------------------------------------------------
# 1. it is a fragment, and self-contained
# ---------------------------------------------------------------------------


class TestFragmentShape(ArtifactTestCase):
    def test_starts_with_the_title(self):
        self.assertTrue(self.html.startswith("<title>Morning Briefing</title>"),
                        self.html[:80])

    def test_has_no_document_tags(self):
        self.assertNotRegex(self.html, r"(?i)<!doctype")
        self.assertNotRegex(self.html, r"(?i)</?(?:html|head|body)\b")

    def test_the_fragment_is_balanced(self):
        # wrapping it in one root makes the whole fragment parse: all tags are
        # closed and every value is escaped
        root = ET.fromstring("<root>" + self.html + "</root>")
        self.assertEqual([child.tag for child in root], ["title", "style", "div"])

    def test_has_exactly_one_style_block(self):
        self.assertEqual(self.html.count("<style"), 1)
        self.assertEqual(self.html.count("</style>"), 1)

    def test_has_no_script(self):
        self.assertNotIn("<script", self.html.lower())

    def test_link_tags_are_absent_or_google_fonts_only(self):
        for tag in re.findall(r"<link\b[^>]*>", self.html):
            self.assertTrue(tag.startswith('<link rel="stylesheet" href="https://fonts.googleapis.com/'),
                            "unexpected link tag: %s" % tag)

    def test_loads_no_external_resource(self):
        self.assertNotRegex(self.html, r"(?i)\bsrc\s*=")
        self.assertNotIn("@import", self.style)
        for url in re.findall(r"url\(([^)]*)\)", self.style):
            self.assertNotRegex(url, r"(?i)https?:")

    def test_no_inline_event_handlers(self):
        for tag in re.findall(r"<[a-zA-Z][^>]*>", self.html):
            self.assertNotRegex(tag, r"(?i)\son[a-z]+\s*=")

    def test_no_javascript_urls(self):
        for tag in re.findall(r"<[a-zA-Z][^>]*>", self.html):
            self.assertNotRegex(tag.lower(), r"(?:href|src)\s*=\s*\"?\s*javascript:")

    def test_an_external_host_only_ever_appears_in_an_anchor(self):
        # navigation is fine; loading another host's resource is not
        for tag in re.findall(r"<[a-zA-Z][^>]*>", self.html):
            if re.search(r"https?://", tag):
                self.assertTrue(tag.startswith("<a "), "external host in %s" % tag)

    def test_anchor_schemes_stay_inside_the_allowed_set(self):
        hrefs = re.findall(r'href="([^"]*)"', self.html)
        self.assertTrue(hrefs)
        for href in hrefs:
            self.assertIsNotNone(build.safe_url(href), "unsafe link scheme: %r" % href)


# ---------------------------------------------------------------------------
# 2. theme contract
# ---------------------------------------------------------------------------


class TestThemeContract(ArtifactTestCase):
    def bare_root(self):
        matches = [decls for media, sel, decls in self.rules if media is None and sel == ":root"]
        self.assertEqual(len(matches), 1, "expected exactly one bare :root block")
        return matches[0]

    def dark_blocks(self):
        system = [decls for media, sel, decls in self.rules
                  if media == "(prefers-color-scheme: dark)"
                  and sel == ':root:not([data-theme="light"])']
        forced = [decls for media, sel, decls in self.rules
                  if media is None and sel == ':root[data-theme="dark"]']
        self.assertEqual(len(system), 1, "missing the prefers-color-scheme dark block")
        self.assertEqual(len(forced), 1, "missing the [data-theme=dark] block")
        return system[0], forced[0]

    def test_bare_root_defines_the_light_palette(self):
        root = self.bare_root()
        for name in ("--bg", "--panel", "--ink", "--ink-soft", "--line", "--accent",
                     "--good", "--warn", "--critical"):
            with self.subTest(token=name):
                self.assertIn(name, root)
                self.assertRegex(root[name], r"^#[0-9a-fA-F]{3,8}$")

    def test_every_used_token_is_defined_on_bare_root(self):
        used = set(re.findall(r"var\((--[\w-]+)", self.style))
        self.assertTrue(used, "the stylesheet uses no tokens at all")
        self.assertLessEqual(used, set(self.bare_root()), "token used but never defined on :root")

    def test_both_dark_blocks_set_color_scheme_dark(self):
        system, forced = self.dark_blocks()
        self.assertEqual(system.get("color-scheme"), "dark")
        self.assertEqual(forced.get("color-scheme"), "dark")

    def test_dark_blocks_only_redefine_bare_root_tokens(self):
        root = self.bare_root()
        system, forced = self.dark_blocks()
        system_tokens = {name for name in system if name.startswith("--")}
        forced_tokens = {name for name in forced if name.startswith("--")}
        self.assertTrue(system_tokens)
        self.assertLessEqual(system_tokens, set(root), "dark block defines a token unknown to :root")
        self.assertEqual(system_tokens, forced_tokens, "the two dark blocks disagree")

    def test_every_colour_is_defined_for_light_mode_too(self):
        # a colour may never exist only inside a dark block
        root = self.bare_root()
        system, forced = self.dark_blocks()
        for decls in (system, forced):
            for name, value in decls.items():
                if name == "color-scheme":
                    continue
                with self.subTest(token=name):
                    self.assertIn(name, root)
                    self.assertNotEqual(root[name], value, "dark value is not a theme override")

    def test_no_colour_literal_outside_the_palette_blocks(self):
        self.assertNotRegex(without_palette_blocks(self.style), _COLOUR_LITERAL_RE)

    def test_a_forced_light_theme_never_restates_the_palette(self):
        light = [decls for media, sel, decls in self.rules
                 if media is None and sel == ':root[data-theme="light"]']
        for decls in light:
            self.assertEqual(decls, {"color-scheme": "light"})

    def test_body_paints_from_the_tokens(self):
        body = declarations_for(self.rules, "body")
        self.assertEqual(body.get("background"), "var(--bg)")
        self.assertEqual(body.get("color"), "var(--ink)")


# ---------------------------------------------------------------------------
# 3. charts use tokens only
# ---------------------------------------------------------------------------


class TestChartColours(ArtifactTestCase):
    def test_svg_has_no_hard_coded_colour(self):
        svgs = svg_blocks(self.html)
        self.assertTrue(svgs)
        for svg in svgs:
            with self.subTest(svg=svg[:60]):
                self.assertNotRegex(svg, _COLOUR_LITERAL_RE)
                for value in re.findall(r'fill="([^"]*)"', svg):
                    self.assertTrue(value.startswith("var(--"), "hard-coded fill: %r" % value)

    def test_fill_and_stroke_declarations_use_tokens(self):
        for media, selector, decls in self.rules:
            for prop in ("fill", "stroke"):
                if prop in decls:
                    with self.subTest(selector=selector, prop=prop):
                        self.assertRegex(decls[prop], _FILL_TOKENS)

    def test_every_drawn_shape_has_an_explicit_fill(self):
        rules = [(selector, decls) for _media, selector, decls in self.rules]
        shapes = 0
        for svg in svg_blocks(self.html):
            for tag, attrs in re.findall(r"<(rect|circle|text|polyline|line|path)\b([^>]*)>", svg):
                shapes += 1
                classes = set((re.search(r'class="([^"]*)"', attrs) or [""] * 2)[1].split())
                filled = any("fill" in decls and _rule_applies(selector, tag, classes)
                             for selector, decls in rules)
                with self.subTest(tag=tag, classes=sorted(classes)):
                    self.assertTrue(filled, "<%s class=%r> has no fill rule" % (tag, " ".join(classes)))
        self.assertGreater(shapes, 20)

    def test_quota_bars_and_badge_use_the_semantic_tokens(self):
        self.assertEqual(declarations_for(self.rules, ".chart .badge-bg").get("fill"),
                         "var(--critical)")
        self.assertEqual(declarations_for(self.rules, ".chart.quota .s4.bar").get("fill"),
                         "var(--critical)")
        self.assertEqual(declarations_for(self.rules, ".chart.quota .s2.bar").get("fill"),
                         "var(--warn)")

    def test_deltas_and_risks_use_the_semantic_tokens(self):
        for selector, token in ((".tile-delta.up", "--good"),
                                (".tile-delta.down", "--critical"),
                                (".sev-low", "--good"),
                                (".sev-medium", "--warn"),
                                (".sev-high", "--critical")):
            with self.subTest(selector=selector):
                self.assertEqual(declarations_for(self.rules, selector).get("color"),
                                 "var(%s)" % token)


# ---------------------------------------------------------------------------
# 4. responsive layout
# ---------------------------------------------------------------------------


class TestResponsiveLayout(ArtifactTestCase):
    def test_one_outer_wrapper_holds_everything(self):
        self.assertIn('<div class="page" id="top">', self.html)
        self.assertTrue(self.html.rstrip().endswith("</div>"))

    def test_outer_wrapper_pads_inline_and_block_without_a_shorthand(self):
        page = declarations_for(self.rules, ".page")
        self.assertNotIn("padding", page)
        inline = page.get("padding-inline", "")
        self.assertRegex(inline, r"^\d+px$")
        self.assertGreaterEqual(int(inline[:-2]), 16)
        block = page.get("padding-block", "")
        self.assertRegex(block, r"^\d+px( \d+px)?$")

    def test_wide_charts_sit_in_a_horizontal_scroller(self):
        self.assertIn('class="chart-scroll"', self.html)
        self.assertEqual(declarations_for(self.rules, ".chart-scroll").get("overflow-x"), "auto")

    def test_numbers_are_tabular(self):
        for selector in (".tile-value", ".m-value", ".t-time", ".open-decisions"):
            with self.subTest(selector=selector):
                self.assertEqual(declarations_for(self.rules, selector).get("font-variant-numeric"),
                                 "tabular-nums")

    def test_no_fixed_width_wider_than_a_phone(self):
        for value in re.findall(r"(?<![-\w])(?:min-)?width\s*:\s*(\d+)px", self.style):
            self.assertLessEqual(int(value), 400, "a fixed width would scroll at 400px")

    def test_typography_is_a_system_stack(self):
        font = self.bare_root_style("--font")
        self.assertIn("sans-serif", font)
        self.assertNotIn("http", font)

    def bare_root_style(self, token):
        matches = [decls for media, sel, decls in self.rules if media is None and sel == ":root"]
        return matches[0][token]


# ---------------------------------------------------------------------------
# 5. content: overview, order, anchor links, chart reuse
# ---------------------------------------------------------------------------


class TestArtifactContent(ArtifactTestCase):
    def test_projects_default_to_alphabetical_order(self):
        self.assertEqual(self.section_ids, ["api", "pipeline"])

    def test_the_order_can_be_configured(self):
        with tempfile.TemporaryDirectory() as out:
            html = artifact_text(out, order=("pipeline", "api"))
        self.assertEqual(re.findall(r'<section id="([^"]+)"', html), ["pipeline", "api"])

    def test_overview_links_match_the_section_ids(self):
        overview = self.html.split('class="overview"', 1)[1].split('<section id="', 1)[0]
        links = re.findall(r'href="#([^"]+)"', overview)
        self.assertEqual(set(links), set(self.section_ids))
        self.assertEqual(links[0], self.section_ids[0])

    def test_every_project_has_a_back_to_top_link(self):
        self.assertEqual(self.html.count('href="#top"'), len(self.section_ids))

    def test_overview_cards_show_headline_tiles_and_decision_count(self):
        cards = re.findall(r'<article class="card project-card">.*?</article>', self.html, re.S)
        self.assertEqual(len(cards), len(self.section_ids))
        for card in cards:
            with self.subTest(card=card[:60]):
                self.assertIn('class="headline"', card)
                tiles = card.count('<span class="m-label">')
                self.assertGreaterEqual(tiles, 2)
                self.assertLessEqual(tiles, 3)
        self.assertIn("2 open decisions", cards[0])
        self.assertIn("1 open decision", cards[1])

    def test_no_open_decisions_is_stated(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            write_json(Path(data) / "core.json", {
                "project": "core", "as_of": "2026-09-25T06:30:00Z", "headline": "h"})
            html = artifact_text(out, data=data, include_examples=False)
        self.assertIn("No open decisions", html)

    def test_every_project_keeps_the_briefing_section_order(self):
        expected = list(SECTION_TITLES)
        for slug in self.section_ids:
            block = self.project_block(slug)
            titles = re.findall(r'<section class="section">\n<h2>([^<]+)</h2>', block)
            with self.subTest(project=slug):
                self.assertEqual(titles, expected if "Details" in titles else expected[1:])

    def test_details_block_follows_the_optional_content_file(self):
        self.assertIn("What changed", self.project_block("api"))   # content/example-api.md
        self.assertNotIn("markdown", self.project_block("pipeline"))  # no content file

    def test_the_same_chart_svg_as_the_project_pages(self):
        for page in ("example-api.html", "example-pipeline.html"):
            html = (DOCS_DIR / page).read_text(encoding="utf-8")
            for svg in svg_blocks(html):
                with self.subTest(page=page, svg=svg[:60]):
                    self.assertIn(svg, self.html)


# ---------------------------------------------------------------------------
# 6. examples, CLI behaviour, safety
# ---------------------------------------------------------------------------


class TestExamplesAndCli(unittest.TestCase):
    def test_cli_writes_the_file_with_include_examples(self):
        with tempfile.TemporaryDirectory() as out:
            target = Path(out) / "briefing.html"
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, fresh_env(), clear=True), \
                    contextlib.redirect_stdout(stdout):
                code = build.main(["--artifact", str(target), "--include-examples"])
            self.assertEqual(code, 0)
            self.assertIn("wrote", stdout.getvalue())
            self.assertTrue(target.is_file())
            html = target.read_text(encoding="utf-8")
        self.assertIn('id="api"', html)
        self.assertIn('id="pipeline"', html)

    def test_without_real_data_and_without_the_flag_it_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            shutil.copy(DATA_DIR / "example-api.json", Path(data) / "example-api.json")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                code = build.main(["--artifact", str(Path(out) / ARTIFACT_NAME), "--data", data])
            self.assertEqual(code, 1)
            self.assertIn("example-", stderr.getvalue())
            self.assertFalse((Path(out) / ARTIFACT_NAME).exists())

    def test_with_no_data_at_all_it_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                code = build.main(["--artifact", str(Path(out) / ARTIFACT_NAME), "--data", data])
            self.assertEqual(code, 1)
            self.assertIn("no data", stderr.getvalue())

    def test_examples_are_left_out_without_the_flag(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            write_json(Path(data) / "core.json", {
                "project": "core", "as_of": "2026-09-25T06:30:00Z", "headline": "real data"})
            write_json(Path(data) / "example-wheel.json", {
                "project": "wheel (EXAMPLE)", "as_of": "2026-09-25T06:30:00Z", "headline": "example"})
            html = artifact_text(out, data=data, include_examples=False)
        self.assertIn('id="core"', html)
        self.assertNotIn("wheel", html)

    def test_the_flag_adds_the_examples(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            write_json(Path(data) / "core.json", {
                "project": "core", "as_of": "2026-09-25T06:30:00Z", "headline": "real data"})
            write_json(Path(data) / "example-wheel.json", {
                "project": "wheel (EXAMPLE)", "as_of": "2026-09-25T06:30:00Z", "headline": "example"})
            html = artifact_text(out, data=data, include_examples=True)
        self.assertIn('id="wheel"', html)
        self.assertIn('<a href="#wheel">', html)

    def test_invalid_data_is_refused(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            write_json(Path(data) / "bad.json", {"project": "x"})
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                code = build.main(["--artifact", str(Path(out) / ARTIFACT_NAME), "--data", data])
            self.assertEqual(code, 1)
            self.assertIn("failed validation", stderr.getvalue())

    def test_duplicate_anchor_ids_are_refused(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            payload = {"project": "core", "as_of": "2026-09-25T06:30:00Z", "headline": "h"}
            write_json(Path(data) / "core.json", payload)
            write_json(Path(data) / "example-core.json", payload)
            with self.assertRaises(build.BuildError) as ctx:
                build_artifact(out, data=data, include_examples=True)
            self.assertIn("both be #core", str(ctx.exception))

    def test_missing_artifact_template_is_reported(self):
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as tpl:
            with self.assertRaises(build.BuildError):
                build_artifact(out, templates=tpl)

    def test_dangerous_values_are_escaped(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            write_json(Path(data) / "core.json", {
                "project": "<script>alert(1)</script>",
                "as_of": "2026-09-25T06:30:00Z",
                "headline": "head <img src=x onerror=alert(1)>",
                "tiles": [{"label": "<b>bold</b>", "value": 1}],
                "evidence": [{"label": "bad", "link": "javascript:alert(1)"},
                             {"label": "good", "link": "https://example.com/x"}],
            })
            html = artifact_text(out, data=data, include_examples=False)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("<img", html)
        self.assertNotIn("javascript:", html.lower())
        self.assertIn('href="https://example.com/x"', html)


# ---------------------------------------------------------------------------
# 7. deterministic
# ---------------------------------------------------------------------------


class TestDeterminism(unittest.TestCase):
    def test_two_builds_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with mock.patch.dict(os.environ, fresh_env(), clear=True):
                first = build_artifact(a).read_bytes()
                second = build_artifact(b).read_bytes()
        self.assertEqual(first, second)

    def test_source_date_epoch_and_commit_are_honoured(self):
        env = fresh_env()
        env["SOURCE_DATE_EPOCH"] = "1700000000"
        env["SOURCE_COMMIT"] = "abcdef1234567890abcdef1234567890abcdef12"
        with tempfile.TemporaryDirectory() as out:
            with mock.patch.dict(os.environ, env, clear=True):
                build_artifact(out)
            html = (Path(out) / ARTIFACT_NAME).read_text(encoding="utf-8")
        self.assertIn("2023-11-14", html)
        self.assertIn("abcdef1", html)


if __name__ == "__main__":
    unittest.main()
