"""Schema validation and the ``--check`` mode."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from _bootstrap import DATA_DIR, EXAMPLE_FILES, SCHEMA_PATH, build, load_schema, write_json


class TestExampleData(unittest.TestCase):
    def test_examples_exist(self):
        names = {p.name for p in EXAMPLE_FILES}
        self.assertIn("example-api.json", names)
        self.assertIn("example-pipeline.json", names)

    def test_examples_validate(self):
        schema = load_schema()
        for path in EXAMPLE_FILES:
            with self.subTest(path=path.name):
                self.assertEqual(build.check_file(path, schema), [])

    def test_examples_use_every_chart_kind(self):
        kinds = set()
        for path in EXAMPLE_FILES:
            data, _ = build.load_json_with_positions(path)
            for result in data.get("results", []):
                kinds.add(result["kind"])
        self.assertEqual(kinds, {"bar", "hbar", "line", "stacked", "quota"})


class TestCheckErrors(unittest.TestCase):
    def setUp(self):
        self.schema = load_schema()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _check(self, obj, name="bad.json"):
        path = write_json(self.dir / name, obj)
        return build.check_file(path, self.schema)

    def test_missing_required_property_is_line_precise(self):
        path = self.dir / "bad.json"
        path.write_text('{\n  "project": "x",\n  "as_of": "2026-09-25T06:30:00Z"\n}\n', encoding="utf-8")
        messages = build.check_file(path, self.schema)
        self.assertTrue(messages, "expected a validation error")
        joined = "\n".join(messages)
        self.assertRegex(joined, r"bad\.json:\d+:\d+: \$: missing required property 'headline'")

    def test_wrong_type_points_at_the_offending_value(self):
        messages = self._check({
            "project": "x",
            "as_of": "2026-09-25T06:30:00Z",
            "headline": "h",
            "results": [{"title": "t", "kind": "bar", "series": "not-an-array"}],
        })
        joined = "\n".join(messages)
        self.assertRegex(joined, r"\$\.results\[0\]\.series: expected array, got string")

    def test_kind_ties_series_shape(self):
        # a stacked series supplied to a bar chart must fail
        messages = self._check({
            "project": "x",
            "as_of": "2026-09-25T06:30:00Z",
            "headline": "h",
            "results": [{"title": "t", "kind": "bar",
                         "series": [{"label": "a", "segments": [{"label": "s", "value": 1}]}]}],
        })
        joined = "\n".join(messages)
        self.assertIn("series[0]", joined)
        self.assertIn("missing required property 'value'", joined)

    def test_unknown_chart_kind_rejected(self):
        messages = self._check({
            "project": "x",
            "as_of": "2026-09-25T06:30:00Z",
            "headline": "h",
            "results": [{"title": "t", "kind": "pie", "series": []}],
        })
        self.assertTrue(any("not one of" in m for m in messages))

    def test_null_is_allowed_for_unknown_values(self):
        messages = self._check({
            "project": "x",
            "as_of": "2026-09-25T06:30:00Z",
            "headline": "h",
            "tiles": [{"label": "unknown", "value": None, "unit": None, "delta": None}],
        })
        self.assertEqual(messages, [])

    def test_syntax_error_is_line_precise(self):
        path = self.dir / "broken.json"
        path.write_text('{\n  "project": "x",\n}\n', encoding="utf-8")
        messages = build.check_file(path, self.schema)
        self.assertEqual(len(messages), 1)
        self.assertRegex(messages[0], r"broken\.json:3:\d+:")

    def test_duplicate_key_is_rejected(self):
        path = self.dir / "dup.json"
        path.write_text('{"project": "a", "project": "b",\n "as_of": "x", "headline": "h"}', encoding="utf-8")
        messages = build.check_file(path, self.schema)
        self.assertTrue(any("duplicate property" in m for m in messages))


class TestCheckCli(unittest.TestCase):
    def test_cli_passes_on_examples(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = build.main(["--check"] + [str(p) for p in EXAMPLE_FILES])
        self.assertEqual(code, 0, stderr.getvalue())

    def test_cli_fails_on_bad_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp) / "bad.json", {"project": "x"})
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                code = build.main(["--check", str(path)])
        self.assertEqual(code, 1)
        self.assertIn("failed validation", stderr.getvalue())

    def test_cli_reports_missing_schema(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = build.main(["--schema", "/nonexistent/schema.json",
                               "--check", str(EXAMPLE_FILES[0])])
        self.assertEqual(code, 1)
        self.assertIn("schema not found", stderr.getvalue())

    def test_schema_file_is_valid_json(self):
        self.assertIsInstance(load_schema(), dict)
        self.assertTrue(SCHEMA_PATH.is_file())


if __name__ == "__main__":
    unittest.main()
