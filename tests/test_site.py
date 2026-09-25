"""Whole-site behaviour: determinism, self-containment, prebuilt docs, hygiene."""

import filecmp
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _bootstrap import (CONTENT_DIR, DATA_DIR, DOCS_DIR, EXAMPLE_FILES, REPO,
                        SCHEMA_PATH, TEMPLATES_DIR, build)

# Secret-looking patterns.  Built from fragments so this file never matches
# itself; the scan also skips tests/ entirely.
SECRET_PATTERNS = [
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bgh" + r"p_[A-Za-z0-9]{20,}\b",
    r"\bgithub_" + r"pat_[A-Za-z0-9_]{20,}\b",
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b",
    r"\bsk-[A-Za-z0-9]{24,}\b",
    r"(?i)\b(api[_-]?key|passwd|password|secret|auth[_-]?token)\b\s*[:=]\s*['\"][^'\"]{8,}['\"]",
]


def _fresh_env():
    """Return an env without CI-provided commit/build-time variables."""
    env = dict(os.environ)
    for key in ("GITHUB_SHA", "SOURCE_COMMIT", "SOURCE_DATE_EPOCH"):
        env.pop(key, None)
    return env


class TestDeterminism(unittest.TestCase):
    def _build_into(self, out_dir: Path):
        build.build_site(DATA_DIR, out_dir, TEMPLATES_DIR, SCHEMA_PATH, CONTENT_DIR)
        return out_dir

    def test_two_builds_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with mock.patch.dict(os.environ, _fresh_env(), clear=True):
                self._build_into(Path(a))
                self._build_into(Path(b))
            left = sorted(p.name for p in Path(a).iterdir())
            right = sorted(p.name for p in Path(b).iterdir())
            self.assertEqual(left, right)
            for name in left:
                with self.subTest(file=name):
                    self.assertTrue(
                        filecmp.cmp(Path(a) / name, Path(b) / name, shallow=False),
                        "%s differs between builds" % name,
                    )

    def test_output_has_no_timestamp_drift(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with mock.patch.dict(os.environ, _fresh_env(), clear=True):
                self._build_into(Path(a))
                self._build_into(Path(b))
            first = (Path(a) / "index.html").read_text(encoding="utf-8")
            second = (Path(b) / "index.html").read_text(encoding="utf-8")
            self.assertEqual(first, second)

    def test_source_date_epoch_is_honoured(self):
        with tempfile.TemporaryDirectory() as out:
            env = _fresh_env()
            env["SOURCE_DATE_EPOCH"] = "1700000000"
            env["SOURCE_COMMIT"] = "abcdef1234567890abcdef1234567890abcdef12"
            with mock.patch.dict(os.environ, env, clear=True):
                build.build_site(DATA_DIR, Path(out), TEMPLATES_DIR, SCHEMA_PATH, CONTENT_DIR)
            html = (Path(out) / "index.html").read_text(encoding="utf-8")
            self.assertIn("2023-11-14", html)
            self.assertIn("abcdef1", html)


class TestPrebuiltDocs(unittest.TestCase):
    def test_docs_exist(self):
        for name in ("index.html", "example-api.html", "example-pipeline.html", "style.css"):
            with self.subTest(file=name):
                self.assertTrue((DOCS_DIR / name).is_file(), "missing docs/%s" % name)

    def test_index_links_to_both_projects(self):
        html = (DOCS_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("example-api.html", html)
        self.assertIn("example-pipeline.html", html)

    def test_committed_docs_match_a_fresh_build(self):
        with tempfile.TemporaryDirectory() as out:
            with mock.patch.dict(os.environ, _fresh_env(), clear=True):
                build.build_site(DATA_DIR, Path(out), TEMPLATES_DIR, SCHEMA_PATH, CONTENT_DIR)
            for path in sorted(DOCS_DIR.iterdir()):
                if not path.is_file():
                    continue
                with self.subTest(file=path.name):
                    fresh = Path(out) / path.name
                    self.assertTrue(fresh.is_file(), "fresh build missing %s" % path.name)
                    self.assertEqual(
                        path.read_bytes(), fresh.read_bytes(),
                        "docs/%s is stale; re-run python3 build.py" % path.name,
                    )

    def test_section_order_is_fixed(self):
        html = (DOCS_DIR / "example-api.html").read_text(encoding="utf-8")
        order = ["details", "tiles", "timeline", "results", "decisions", "risks", "next24h", "evidence"]
        positions = [html.find('id="%s"' % name) for name in order]
        for name, pos in zip(order, positions):
            with self.subTest(section=name):
                self.assertNotEqual(pos, -1, "section %s missing" % name)
        self.assertEqual(positions, sorted(positions), "sections are out of order")


class TestNoExternalRequests(unittest.TestCase):
    def test_templates_contain_no_urls(self):
        for path in sorted(TEMPLATES_DIR.iterdir()):
            text = path.read_text(encoding="utf-8")
            with self.subTest(template=path.name):
                self.assertNotIn("http", text.lower())
                self.assertNotIn("@import", text)
                self.assertNotIn("<script", text)

    def test_generated_pages_load_no_external_resources(self):
        for path in sorted(DOCS_DIR.glob("*.html")):
            html = path.read_text(encoding="utf-8")
            with self.subTest(page=path.name):
                self.assertNotIn("<script", html)
                self.assertNotIn("<iframe", html)
                self.assertNotIn("<img", html)
                for match in re.finditer(r"<link\b[^>]*>", html):
                    self.assertNotRegex(match.group(0), r'href\s*=\s*"https?:')

    def test_stylesheet_has_no_remote_assets(self):
        css = (DOCS_DIR / "style.css").read_text(encoding="utf-8")
        self.assertNotIn("@import", css)
        self.assertNotRegex(css, r"url\(\s*['\"]?https?:")

    def test_pages_declare_dark_and_light(self):
        css = (DOCS_DIR / "style.css").read_text(encoding="utf-8")
        self.assertIn("prefers-color-scheme: dark", css)
        self.assertIn("prefers-color-scheme", css)
        for path in sorted(DOCS_DIR.glob("*.html")):
            html = path.read_text(encoding="utf-8")
            with self.subTest(page=path.name):
                self.assertIn('name="color-scheme" content="light dark"', html)
                self.assertIn('name="viewport"', html)

    def test_print_styles_present(self):
        css = (DOCS_DIR / "style.css").read_text(encoding="utf-8")
        self.assertIn("@media print", css)


class TestNoSecrets(unittest.TestCase):
    def test_repository_has_no_secret_like_strings(self):
        offenders = []
        for path in sorted(REPO.rglob("*")):
            if not path.is_file() or "tests" in path.parts or ".git" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for pattern in SECRET_PATTERNS:
                if re.search(pattern, text):
                    offenders.append("%s matched %s" % (path.relative_to(REPO), pattern))
        self.assertEqual(offenders, [], "possible secrets: %s" % offenders)

    def test_examples_are_labelled_example(self):
        for path in EXAMPLE_FILES:
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertIn("EXAMPLE", text)


class TestBuildErrors(unittest.TestCase):
    def test_build_refuses_invalid_data(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            Path(data, "bad.json").write_text('{"project": "x"}', encoding="utf-8")
            with self.assertRaises(build.BuildError):
                build.build_site(Path(data), Path(out), TEMPLATES_DIR, SCHEMA_PATH, CONTENT_DIR)

    def test_build_refuses_empty_data_dir(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            with self.assertRaises(build.BuildError):
                build.build_site(Path(data), Path(out), TEMPLATES_DIR, SCHEMA_PATH, CONTENT_DIR)

    def test_missing_template_is_reported(self):
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as tpl:
            with self.assertRaises(build.BuildError):
                build.build_site(DATA_DIR, Path(out), Path(tpl), SCHEMA_PATH, CONTENT_DIR)


if __name__ == "__main__":
    unittest.main()
