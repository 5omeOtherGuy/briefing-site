#!/usr/bin/env python3
"""Morning briefing static-site generator.

Reads ``data/<project>.json`` (and the optional ``content/<project>.md``), renders
the pages into ``docs/`` from ``templates/`` and copies the stylesheet.  Charts
are inline SVG generated here, so the published site makes no network requests
and uses no JavaScript.

Stdlib only.  Deterministic: the same inputs and environment always produce the
same bytes (no clock, no randomness, no network).

Usage
-----
    python3 build.py                       # build every data/*.json into docs/
    python3 build.py --artifact OUT.html   # one self-contained HTML page
    python3 build.py --check               # validate every data/*.json
    python3 build.py --check data/a.json   # validate specific files

Options
-------
    --data DIR        directory holding <project>.json        (default: data)
    --out DIR         output directory for the site          (default: docs)
    --artifact FILE   write one self-contained HTML file (no doctype/html/head/body,
                      styles inline) and exit
    --include-examples  with --artifact, also publish the data/example-*.json files
    --order LIST      comma-separated project slugs to show first, e.g. "api,pipeline"
                      (default: $BRIEFING_ORDER, else alphabetical)
    --templates DIR   directory holding the html/css templates (default: templates)
    --schema FILE     JSON schema to validate against         (default: schema/briefing.schema.json)
    --content DIR     directory holding optional <project>.md (default: content)

Environment
-----------
    BRIEFING_ORDER      same as --order (the flag wins)
    SOURCE_DATE_EPOCH   fixes the footer build time (reproducible builds)
    SOURCE_COMMIT       or GITHUB_SHA: the source revision shown in the footer
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import math
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent

DASH = "\u2014"  # em dash: the single rendering for every unknown value
CHART_WIDTH = 640
PALETTE_SIZE = 6

# Sections are rendered in this fixed order (the briefing's section order).
SECTION_ORDER = (
    "tiles",
    "timeline",
    "results",
    "decisions",
    "risks",
    "next24h",
    "evidence",
)

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class BuildError(Exception):
    """A fatal, user-facing build error."""


# ---------------------------------------------------------------------------
# position-aware JSON parser
# ---------------------------------------------------------------------------


class JsonError(Exception):
    """A JSON syntax error with a source position."""

    def __init__(self, message: str, line: int, col: int):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col


class _PosParser:
    """A small JSON parser that records the (line, column) of every value.

    ``positions`` maps a path tuple (e.g. ``("results", 0, "series")``) to the
    1-based ``(line, column)`` where that value begins in the source text.
    """

    def __init__(self, text: str):
        self.text = text
        self.i = 0
        self.n = len(text)
        self.positions: dict[tuple, tuple[int, int]] = {}

    # -- helpers ----------------------------------------------------------
    def _linecol(self, idx: int) -> tuple[int, int]:
        line = self.text.count("\n", 0, idx) + 1
        col = idx - self.text.rfind("\n", 0, idx)
        return (line, col)

    def error(self, message: str, idx: int | None = None):
        i = self.i if idx is None else idx
        line, col = self._linecol(i)
        raise JsonError(message, line, col)

    def skip_ws(self):
        t, n = self.text, self.n
        while self.i < n and t[self.i] in " \t\n\r":
            self.i += 1

    def peek(self) -> str:
        return self.text[self.i] if self.i < self.n else ""

    # -- grammar ----------------------------------------------------------
    def parse(self, path: tuple = ()):
        self.skip_ws()
        self.positions[path] = self._linecol(self.i)
        c = self.peek()
        if c == "{":
            return self._object(path)
        if c == "[":
            return self._array(path)
        if c == '"':
            return self._string()
        if self.text.startswith("true", self.i):
            self.i += 4
            return True
        if self.text.startswith("false", self.i):
            self.i += 5
            return False
        if self.text.startswith("null", self.i):
            self.i += 4
            return None
        if c == "-" or c.isdigit():
            return self._number()
        self.error("unexpected character %r" % (c or "<eof>"))

    def _object(self, path: tuple):
        obj: dict = {}
        self.i += 1
        self.skip_ws()
        if self.peek() == "}":
            self.i += 1
            return obj
        while True:
            self.skip_ws()
            if self.peek() != '"':
                self.error("expected a property name string")
            key_start = self.i
            key = self._string()
            if key in obj:
                self.error("duplicate property %r" % key, key_start)
            self.skip_ws()
            if self.peek() != ":":
                self.error("expected ':' after property name")
            self.i += 1
            obj[key] = self.parse(path + (key,))
            self.skip_ws()
            c = self.peek()
            if c == ",":
                self.i += 1
                continue
            if c == "}":
                self.i += 1
                return obj
            self.error("expected ',' or '}' in object")

    def _array(self, path: tuple):
        arr: list = []
        self.i += 1
        self.skip_ws()
        if self.peek() == "]":
            self.i += 1
            return arr
        idx = 0
        while True:
            arr.append(self.parse(path + (idx,)))
            idx += 1
            self.skip_ws()
            c = self.peek()
            if c == ",":
                self.i += 1
                continue
            if c == "]":
                self.i += 1
                return arr
            self.error("expected ',' or ']' in array")

    def _string(self) -> str:
        start = self.i
        j = self.i + 1
        while j < self.n:
            ch = self.text[j]
            if ch == "\\":
                j += 2
                continue
            if ch == '"':
                raw = self.text[start : j + 1]
                try:
                    value = json.loads(raw)
                except ValueError as exc:  # bad escape / bad escape sequence
                    self.error("invalid string: %s" % exc, start)
                self.i = j + 1
                return value
            if ord(ch) < 0x20:
                self.error("control character in string", j)
            j += 1
        self.error("unterminated string", start)

    def _number(self):
        m = re.match(
            r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?",
            self.text[self.i :],
        )
        if not m:
            self.error("invalid number")
        raw = m.group(0)
        self.i += len(raw)
        if any(ch in raw for ch in ".eE"):
            return float(raw)
        return int(raw)


def load_json_with_positions(path: Path):
    """Return ``(value, positions)`` for a JSON file."""
    text = path.read_text(encoding="utf-8")
    parser = _PosParser(text)
    parser.skip_ws()
    if parser.i >= parser.n:
        raise JsonError("empty document", 1, 1)
    value = parser.parse(())
    parser.skip_ws()
    if parser.i != parser.n:
        parser.error("trailing content after the top-level value")
    return value, parser.positions


# ---------------------------------------------------------------------------
# JSON-schema validation (subset of draft 2020-12, with source positions)
# ---------------------------------------------------------------------------


def _json_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_ok(value, declared: str) -> bool:
    if declared == "object":
        return isinstance(value, dict)
    if declared == "array":
        return isinstance(value, list)
    if declared == "string":
        return isinstance(value, str)
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared == "null":
        return value is None
    return True


def _resolve_ref(ref: str, root):
    if not ref.startswith("#"):
        raise ValueError("only local $ref values are supported: %s" % ref)
    node = root
    if ref != "#":
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def validate(instance, schema, root, path: tuple = ()):
    """Return a list of ``(path, message)`` schema violations."""
    if not isinstance(schema, dict):
        return []
    if "$ref" in schema:
        return validate(instance, _resolve_ref(schema["$ref"], root), root, path)

    errs: list[tuple[tuple, str]] = []
    declared = schema.get("type")
    if declared is not None:
        types = declared if isinstance(declared, list) else [declared]
        if not any(_type_ok(instance, t) for t in types):
            return [(path, "expected %s, got %s" % ("|".join(types), _json_type(instance)))]

    if "enum" in schema and instance not in schema["enum"]:
        errs.append((path, "not one of %s" % ", ".join(repr(x) for x in schema["enum"])))
    if "const" in schema and instance != schema["const"]:
        errs.append((path, "must equal %r" % (schema["const"],)))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errs.append((path, "shorter than %d characters" % schema["minLength"]))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errs.append((path, "longer than %d characters" % schema["maxLength"]))
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errs.append((path, "does not match %r" % schema["pattern"]))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errs.append((path, "less than the minimum %s" % schema["minimum"]))
        if "maximum" in schema and instance > schema["maximum"]:
            errs.append((path, "greater than the maximum %s" % schema["maximum"]))

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errs.append((path, "missing required property %r" % key))
        props = schema.get("properties", {})
        addl = schema.get("additionalProperties", True)
        for key, value in instance.items():
            sub = path + (key,)
            if key in props:
                errs += validate(value, props[key], root, sub)
            elif addl is False:
                errs.append((sub, "unexpected property %r" % key))
            elif isinstance(addl, dict):
                errs += validate(value, addl, root, sub)

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errs.append((path, "fewer than %d items" % schema["minItems"]))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errs.append((path, "more than %d items" % schema["maxItems"]))
        items = schema.get("items")
        if isinstance(items, dict):
            for i, value in enumerate(instance):
                errs += validate(value, items, root, path + (i,))

    for sub in schema.get("allOf", []):
        errs += validate(instance, sub, root, path)
    if "anyOf" in schema:
        if not any(not validate(instance, s, root, path) for s in schema["anyOf"]):
            errs.append((path, "does not match any of the allowed shapes"))
    if "oneOf" in schema:
        matched = sum(1 for s in schema["oneOf"] if not validate(instance, s, root, path))
        if matched != 1:
            errs.append((path, "does not match exactly one allowed shape (%d matched)" % matched))
    if "not" in schema and not validate(instance, schema["not"], root, path):
        errs.append((path, "matches a forbidden shape"))

    # conditional schemas (used to tie a chart's kind to its series shape)
    if "if" in schema:
        if not validate(instance, schema["if"], root, path):
            errs += validate(instance, schema.get("then", {}), root, path)
        else:
            errs += validate(instance, schema.get("else", {}), root, path)

    return errs


def format_path(path: tuple) -> str:
    if not path:
        return "$"
    out = "$"
    for part in path:
        if isinstance(part, int):
            out += "[%d]" % part
        elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(part)):
            out += "." + str(part)
        else:
            out += "[%r]" % (part,)
    return out


# ---------------------------------------------------------------------------
# data loading / checking
# ---------------------------------------------------------------------------


def discover_data_files(data_dir: Path) -> list[Path]:
    if not data_dir.is_dir():
        return []
    return sorted(p for p in data_dir.glob("*.json") if p.is_file())


def check_file(path: Path, schema: dict) -> list[str]:
    """Validate one file, returning formatted ``file:line:col: ...`` messages."""
    try:
        value, positions = load_json_with_positions(path)
    except JsonError as exc:
        return ["%s:%d:%d: %s" % (path, exc.line, exc.col, exc.message)]
    except OSError as exc:
        return ["%s: %s" % (path, exc)]

    errors = validate(value, schema, schema)
    messages = []
    for err_path, message in errors:
        line, col = positions.get(err_path, (1, 1))
        messages.append(
            "%s:%d:%d: %s: %s" % (path, line, col, format_path(err_path), message)
        )
    return messages


# ---------------------------------------------------------------------------
# value formatting (unknown -> em dash, never 0)
# ---------------------------------------------------------------------------


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def fmt_num(value) -> str:
    if not is_number(value):
        return DASH
    f = float(value)
    if f.is_integer():
        return str(int(f))
    text = ("%.2f" % f).rstrip("0").rstrip(".")
    return text or "0"


def fmt_value(value, unit: str | None = None) -> str:
    if not is_number(value):
        return DASH
    return attach_unit(fmt_num(value), unit)


SYMBOL_UNITS = ("%", "\u2030")


def attach_unit(text: str, unit: str | None) -> str:
    if not unit:
        return text
    if unit in SYMBOL_UNITS:
        return text + unit
    return "%s %s" % (text, unit)


def fmt_percent(value) -> str:
    if not is_number(value):
        return DASH
    f = float(value)
    return ("%.1f" % f).rstrip("0").rstrip(".") + "%"


def fmt_compact(value, unit: str | None = None) -> str:
    """One-line rendering of a tile-style value (number, string or unknown)."""
    if is_number(value):
        return attach_unit(fmt_num(value), unit)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DASH


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def _short(text: str, limit: int = 14) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def safe_url(url) -> str | None:
    """Return the URL if it is safe to put in an href, else ``None``."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    if re.match(r"^(https?:|mailto:)", url, re.I):
        return url
    if url.startswith(("#", "/", "./", "../")):
        return url
    if re.match(r"^[\w.\-]+$", url):  # bare relative file such as "core.html"
        return url
    return None


# ---------------------------------------------------------------------------
# markdown subset: headings, bullets, links, bold
# ---------------------------------------------------------------------------


def _inline_markdown(text: str) -> str:
    out = html.escape(text, quote=True)

    def link_repl(match: re.Match) -> str:
        label = match.group(1)
        url = safe_url(match.group(2))
        if url is None:
            return label
        return '<a href="%s">%s</a>' % (esc(url), label)

    out = _LINK_RE.sub(link_repl, out)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    return out


def render_markdown(text: str) -> str:
    """Render the documented markdown subset to HTML."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    para: list[str] = []
    bullets: list[str] = []

    def flush_para():
        if para:
            blocks.append("<p>%s</p>" % _inline_markdown(" ".join(para)))
            para.clear()

    def flush_bullets():
        if bullets:
            items = "".join("<li>%s</li>" % _inline_markdown(b) for b in bullets)
            blocks.append("<ul>%s</ul>" % items)
            bullets.clear()

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_para()
            flush_bullets()
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_para()
            flush_bullets()
            level = len(heading.group(1))
            blocks.append("<h%d>%s</h%d>" % (level, _inline_markdown(heading.group(2)), level))
            continue
        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        if bullet:
            flush_para()
            bullets.append(bullet.group(1))
            continue
        if bullets:  # lazy continuation of a wrapped bullet
            bullets[-1] += " " + stripped
            continue
        flush_bullets()
        para.append(stripped)

    flush_para()
    flush_bullets()
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# SVG charts
# ---------------------------------------------------------------------------


def _nice_ticks(vmax: float, target: int = 4):
    if not (vmax > 0):
        vmax = 1.0
    raw = vmax / target
    mag = 10 ** math.floor(math.log10(raw))
    step = mag * 10
    for mult in (1, 2, 2.5, 5, 10):
        candidate = mult * mag
        if candidate >= raw:
            step = candidate
            break
    top = math.ceil(vmax / step) * step
    ticks = []
    value = 0.0
    while value <= top + 1e-9:
        ticks.append(value)
        value += step
    return ticks, top


def _svg_open(title: str, height: int, extra_class: str = "") -> str:
    cls = ("chart " + extra_class).strip()
    return (
        '<svg class="%s" viewBox="0 0 %d %d" width="100%%" height="%d" '
        'preserveAspectRatio="xMidYMid meet" role="img" aria-label="%s">'
        % (cls, CHART_WIDTH, height, height, esc(title))
    )


def svg_bar(items: list, unit: str | None, title: str, width: int = CHART_WIDTH, height: int = 260) -> str:
    left, right, top, bottom = 48, 16, 28, 56
    plot_w = width - left - right
    plot_h = height - top - bottom
    values = [it.get("value") for it in items if isinstance(it, dict)]
    numeric = [v for v in values if is_number(v)]
    ticks, vmax = _nice_ticks(max(numeric) if numeric else 1.0)
    baseline = top + plot_h

    parts = [_svg_open(title, height)]
    for tick in ticks:
        y = baseline - (tick / vmax) * plot_h
        parts.append(
            '<line class="grid" x1="%d" y1="%s" x2="%d" y2="%s"/>'
            % (left, fmt_num(round(y, 2)), left + plot_w, fmt_num(round(y, 2)))
        )
        parts.append(
            '<text class="tick" x="%d" y="%s" text-anchor="end">%s</text>'
            % (left - 8, fmt_num(round(y + 3, 2)), esc(fmt_num(tick)))
        )
    parts.append(
        '<line class="axis" x1="%d" y1="%d" x2="%d" y2="%d"/>'
        % (left, baseline, left + plot_w, baseline)
    )

    n = max(len(items), 1)
    step = plot_w / n
    bar_w = min(step * 0.62, 72)
    for i, item in enumerate(items):
        item = item if isinstance(item, dict) else {}
        value = item.get("value")
        cx = left + step * (i + 0.5)
        x = cx - bar_w / 2
        label = _short(item.get("label", ""), 14)
        if is_number(value) and value > 0:
            h = (float(value) / vmax) * plot_h
            y = baseline - h
            parts.append(
                '<rect class="s0 bar" x="%s" y="%s" width="%s" height="%s" rx="2"/>'
                % (fmt_num(round(x, 2)), fmt_num(round(y, 2)),
                   fmt_num(round(bar_w, 2)), fmt_num(round(h, 2)))
            )
            parts.append(
                '<text class="value" x="%s" y="%s" text-anchor="middle">%s</text>'
                % (fmt_num(round(cx, 2)), fmt_num(round(y - 6, 2)),
                   esc(fmt_value(value, item.get("unit", unit))))
            )
        else:
            parts.append(
                '<line class="zero" x1="%s" y1="%d" x2="%s" y2="%d"/>'
                % (fmt_num(round(x, 2)), baseline, fmt_num(round(x + bar_w, 2)), baseline)
            )
            parts.append(
                '<text class="unknown" x="%s" y="%s" text-anchor="middle">%s</text>'
                % (fmt_num(round(cx, 2)), fmt_num(round(baseline - 6, 2)), DASH)
            )
        parts.append(
            '<text class="cat" x="%s" y="%d" text-anchor="middle">%s</text>'
            % (fmt_num(round(cx, 2)), baseline + 20, esc(label))
        )
    parts.append("</svg>")
    return "".join(parts)


def svg_hbar(items: list, unit: str | None, title: str, width: int = CHART_WIDTH, height: int | None = None) -> str:
    labels = [str((it or {}).get("label", "")) for it in items]
    left = int(min(240, max(84, 16 + (max((len(s) for s in labels), default=0) * 6.8))))
    right, top = 72, 20
    row_h = 30
    height = height or top * 2 + row_h * max(len(items), 1)
    plot_w = width - left - right
    values = [it.get("value") for it in items if isinstance(it, dict)]
    numeric = [v for v in values if is_number(v)]
    vmax = max(numeric) if numeric else 1.0
    if vmax <= 0:
        vmax = 1.0

    parts = [_svg_open(title, height, "hbar")]
    for i, item in enumerate(items):
        item = item if isinstance(item, dict) else {}
        value = item.get("value")
        y = top + i * row_h
        cy = y + row_h / 2 - 4
        parts.append(
            '<text class="cat" x="%d" y="%s" text-anchor="end">%s</text>'
            % (left - 10, fmt_num(round(cy, 2)), esc(_short(item.get("label", ""), 22)))
        )
        parts.append(
            '<rect class="track" x="%d" y="%s" width="%d" height="12" rx="2"/>'
            % (left, fmt_num(round(cy - 9, 2)), plot_w)
        )
        if is_number(value) and value > 0:
            w = (float(value) / vmax) * plot_w
            parts.append(
                '<rect class="s0 bar" x="%d" y="%s" width="%s" height="12" rx="2"/>'
                % (left, fmt_num(round(cy - 9, 2)), fmt_num(round(w, 2)))
            )
            parts.append(
                '<text class="value" x="%s" y="%s">%s</text>'
                % (fmt_num(round(left + w + 8, 2)), fmt_num(round(cy, 2)),
                   esc(fmt_value(value, item.get("unit", unit))))
            )
        else:
            parts.append(
                '<text class="unknown" x="%d" y="%s">%s</text>'
                % (left + 8, fmt_num(round(cy, 2)), DASH)
            )
    parts.append("</svg>")
    return "".join(parts)


def svg_line(series: list, x_labels: list | None, unit: str | None, title: str,
             width: int = CHART_WIDTH, height: int = 260) -> str:
    left, right, top, bottom = 48, 96, 28, 52
    plot_w = width - left - right
    plot_h = height - top - bottom
    baseline = top + plot_h

    counts = [len((s or {}).get("points", []) or []) for s in series]
    npoints = max(counts) if counts else 1
    npoints = max(npoints, 1)
    all_values = [p for s in series for p in ((s or {}).get("points") or []) if is_number(p)]
    lo = min([0.0] + all_values)
    hi = max([1.0] + all_values)
    ticks, top_val = _nice_ticks(hi)
    span = top_val - min(lo, 0.0)
    span = span if span > 0 else 1.0
    base_y = baseline - ((0 - min(lo, 0.0)) / span) * plot_h

    def ypos(v: float) -> float:
        return baseline - ((v - min(lo, 0.0)) / span) * plot_h

    parts = [_svg_open(title, height)]
    for tick in ticks:
        y = ypos(tick)
        if top <= y <= baseline + 0.5:
            parts.append(
                '<line class="grid" x1="%d" y1="%s" x2="%d" y2="%s"/>'
                % (left, fmt_num(round(y, 2)), left + plot_w, fmt_num(round(y, 2)))
            )
            parts.append(
                '<text class="tick" x="%d" y="%s" text-anchor="end">%s</text>'
                % (left - 8, fmt_num(round(y + 3, 2)), esc(fmt_num(tick)))
            )
    parts.append(
        '<line class="axis" x1="%d" y1="%s" x2="%d" y2="%s"/>'
        % (left, fmt_num(round(base_y, 2)), left + plot_w, fmt_num(round(base_y, 2)))
    )

    def xpos(i: int) -> float:
        if npoints == 1:
            return left + plot_w / 2
        return left + plot_w * (i / (npoints - 1))

    for si, s in enumerate(series):
        points = (s or {}).get("points") or []
        segments = []
        current = []
        for i, p in enumerate(points):
            if is_number(p):
                current.append((xpos(i), ypos(float(p))))
            else:
                if len(current) > 1:
                    segments.append(current)
                elif len(current) == 1:
                    segments.append(current)
                current = []
        if current:
            segments.append(current)
        for seg in segments:
            if len(seg) == 1:
                x, y = seg[0]
                parts.append('<circle class="s%d dot" cx="%s" cy="%s" r="3"/>'
                             % (si % PALETTE_SIZE, fmt_num(round(x, 2)), fmt_num(round(y, 2))))
            else:
                pts = " ".join("%s,%s" % (fmt_num(round(x, 2)), fmt_num(round(y, 2))) for x, y in seg)
                parts.append('<polyline class="s%d line" points="%s"/>' % (si % PALETTE_SIZE, pts))
        for i, p in enumerate(points):
            if is_number(p):
                parts.append('<circle class="s%d dot" cx="%s" cy="%s" r="2.5"/>'
                             % (si % PALETTE_SIZE, fmt_num(round(xpos(i), 2)), fmt_num(round(ypos(float(p)), 2))))
            else:
                parts.append('<text class="unknown" x="%s" y="%s" text-anchor="middle">%s</text>'
                             % (fmt_num(round(xpos(i), 2)), fmt_num(round(baseline - 6, 2)), DASH))
        # legend
        if len(series) > 1 or (s or {}).get("label"):
            ly = top + 6 + si * 18
            parts.append('<rect class="s%d bar" x="%d" y="%s" width="12" height="12" rx="2"/>'
                         % (si % PALETTE_SIZE, width - right + 8, fmt_num(round(ly, 2))))
            parts.append('<text class="legend" x="%d" y="%s">%s%s</text>'
                         % (width - right + 26, fmt_num(round(ly + 11, 2)),
                            esc(_short((s or {}).get("label", ""), 10)),
                            esc(" (%s)" % (s.get("unit") or unit) if (s.get("unit") or unit) else "")))

    for i in range(npoints):
        if x_labels and i < len(x_labels):
            label = x_labels[i]
        else:
            label = str(i + 1)
        parts.append('<text class="cat" x="%s" y="%d" text-anchor="middle">%s</text>'
                     % (fmt_num(round(xpos(i), 2)), baseline + 20, esc(_short(label, 8))))
    parts.append("</svg>")
    return "".join(parts)


def svg_stacked(items: list, unit: str | None, title: str,
                width: int = CHART_WIDTH, height: int = 300) -> str:
    left, right, top, bottom = 48, 132, 28, 56
    plot_w = width - left - right
    plot_h = height - top - bottom
    baseline = top + plot_h

    totals = []
    for it in items:
        segs = (it or {}).get("segments", []) or []
        totals.append(sum(float(s.get("value")) for s in segs if isinstance(s, dict) and is_number(s.get("value"))))
    ticks, vmax = _nice_ticks(max(totals) if totals else 1.0)

    parts = [_svg_open(title, height)]
    for tick in ticks:
        y = baseline - (tick / vmax) * plot_h
        parts.append('<line class="grid" x1="%d" y1="%s" x2="%d" y2="%s"/>'
                     % (left, fmt_num(round(y, 2)), left + plot_w, fmt_num(round(y, 2))))
        parts.append('<text class="tick" x="%d" y="%s" text-anchor="end">%s</text>'
                     % (left - 8, fmt_num(round(y + 3, 2)), esc(fmt_num(tick))))
    parts.append('<line class="axis" x1="%d" y1="%d" x2="%d" y2="%d"/>'
                 % (left, baseline, left + plot_w, baseline))

    n = max(len(items), 1)
    step = plot_w / n
    bar_w = min(step * 0.6, 64)
    legend_seen: list[str] = []
    for i, item in enumerate(items):
        item = item if isinstance(item, dict) else {}
        cx = left + step * (i + 0.5)
        x = cx - bar_w / 2
        y = baseline
        segs = item.get("segments", []) or []
        for seg in segs:
            seg = seg if isinstance(seg, dict) else {}
            value = seg.get("value")
            label = str(seg.get("label", ""))
            if label and label not in legend_seen:
                legend_seen.append(label)
            if not is_number(value) or value <= 0:
                continue
            h = (float(value) / vmax) * plot_h
            y -= h
            parts.append('<rect class="s%d bar" x="%s" y="%s" width="%s" height="%s"/>'
                         % (legend_seen.index(label) % PALETTE_SIZE if label else 0,
                            fmt_num(round(x, 2)), fmt_num(round(y, 2)),
                            fmt_num(round(bar_w, 2)), fmt_num(round(h, 2))))
        has_unknown = any(isinstance(s, dict) and not is_number(s.get("value")) for s in segs)
        if has_unknown:
            parts.append('<text class="unknown" x="%s" y="%s" text-anchor="middle">%s</text>'
                         % (fmt_num(round(cx, 2)), fmt_num(round(baseline - 8, 2)), DASH))
        if item.get("label"):
            parts.append('<text class="cat" x="%s" y="%d" text-anchor="middle">%s</text>'
                         % (fmt_num(round(cx, 2)), baseline + 20, esc(_short(item.get("label"), 12))))

    for k, label in enumerate(legend_seen):
        ly = top + 6 + k * 18
        parts.append('<rect class="s%d bar" x="%d" y="%s" width="12" height="12" rx="2"/>'
                     % (k % PALETTE_SIZE, width - right + 8, fmt_num(round(ly, 2))))
        parts.append('<text class="legend" x="%d" y="%s">%s</text>'
                     % (width - right + 26, fmt_num(round(ly + 11, 2)), esc(_short(label, 12))))
    if unit:
        parts.append('<text class="legend" x="%d" y="%s">unit: %s</text>'
                     % (width - right + 8, fmt_num(round(top + 6 + len(legend_seen) * 18 + 11, 2)), esc(unit)))
    parts.append("</svg>")
    return "".join(parts)


def svg_quota(items: list, unit: str | None, title: str,
              width: int = CHART_WIDTH, height: int | None = None) -> str:
    left, right, top = 150, 150, 24
    row_h = 34
    height = height or top * 2 + row_h * max(len(items), 1)
    track_w = width - left - right

    parts = [_svg_open(title, height, "quota")]
    for i, item in enumerate(items):
        item = item if isinstance(item, dict) else {}
        y = top + i * row_h
        cy = y + row_h / 2 - 4
        used = item.get("used")
        limit = item.get("limit")
        u_unit = item.get("unit", unit)
        parts.append('<text class="cat" x="%d" y="%s" text-anchor="end">%s</text>'
                     % (left - 10, fmt_num(round(cy, 2)), esc(_short(item.get("label", ""), 20))))
        parts.append('<rect class="track" x="%d" y="%s" width="%d" height="14" rx="3"/>'
                     % (left, fmt_num(round(cy - 10, 2)), track_w))
        if is_number(used) and is_number(limit) and float(limit) > 0:
            pct = float(used) / float(limit) * 100.0
            fill = max(0.0, min(pct, 100.0))
            w = track_w * fill / 100.0
            hit = pct >= 100.0
            parts.append('<rect class="s%s bar" x="%d" y="%s" width="%s" height="14" rx="3"/>'
                         % ("4" if hit else ("2" if pct >= 80 else "0"),
                            left, fmt_num(round(cy - 10, 2)), fmt_num(round(max(w, 1.0), 2))))
            parts.append('<text class="value" x="%d" y="%s">%s</text>'
                         % (left + track_w + 8, fmt_num(round(cy, 2)),
                            esc("%s (%s / %s)" % (fmt_percent(pct),
                                                  fmt_num(used), fmt_num(limit)))))
            if hit:
                badge_x = left + track_w + 86
                parts.append('<rect class="badge-bg" x="%d" y="%s" width="58" height="16" rx="8"/>'
                             % (badge_x, fmt_num(round(cy - 12, 2))))
                parts.append('<text class="badge" x="%d" y="%s" text-anchor="middle">HIT LIMIT</text>'
                             % (badge_x + 29, fmt_num(round(cy, 2))))
        else:
            parts.append('<text class="unknown" x="%d" y="%s">%s</text>'
                         % (left + 8, fmt_num(round(cy, 2)), DASH))
        if u_unit:
            parts.append('<text class="legend" x="%d" y="%s">%s</text>'
                         % (left, fmt_num(round(cy + 14, 2)), esc(u_unit)))
    parts.append("</svg>")
    return "".join(parts)


CHART_RENDERERS = {
    "bar": lambda res: svg_bar(res.get("series") or [], res.get("unit"), res.get("title", "chart")),
    "hbar": lambda res: svg_hbar(res.get("series") or [], res.get("unit"), res.get("title", "chart")),
    "line": lambda res: svg_line(res.get("series") or [], res.get("x"), res.get("unit"), res.get("title", "chart")),
    "stacked": lambda res: svg_stacked(res.get("series") or [], res.get("unit"), res.get("title", "chart")),
    "quota": lambda res: svg_quota(res.get("series") or [], res.get("unit"), res.get("title", "chart")),
}


# ---------------------------------------------------------------------------
# html section builders
# ---------------------------------------------------------------------------


def build_tiles(tiles: list) -> str:
    if not tiles:
        return '<p class="empty">No key numbers reported.</p>'
    cells = []
    for tile in tiles:
        tile = tile if isinstance(tile, dict) else {}
        unit = tile.get("unit")
        value = tile.get("value")
        if is_number(value):
            value_html = fmt_num(value)
            if unit:
                tight = " tight" if unit in SYMBOL_UNITS else ""
                unit_html = '<span class="tile-unit%s">%s</span>' % (tight, esc(unit))
            else:
                unit_html = ""
            unknown = False
        elif isinstance(value, str) and value.strip():
            value_html, unit_html, unknown = esc(value.strip()), "", False
        else:
            value_html, unit_html, unknown = DASH, "", True
        delta = tile.get("delta")
        if is_number(delta):
            delta_text = ("+" if float(delta) > 0 else "") + fmt_num(delta)
            if unit:
                delta_text += " " + str(unit)
            delta_cls = "up" if float(delta) > 0 else ("down" if float(delta) < 0 else "flat")
        elif isinstance(delta, str) and delta.strip():
            delta_text = delta.strip()
            delta_cls = "up" if delta_text.startswith("+") else ("down" if delta_text.startswith("-") else "flat")
        else:
            delta_text, delta_cls = None, "flat"
        cells.append(
            '<div class="tile">'
            '<p class="tile-label">%s</p>'
            '<p class="tile-value%s">%s%s</p>'
            "%s"
            "%s"
            "</div>"
            % (
                esc(tile.get("label", "")),
                " unknown" if unknown else "",
                value_html,
                unit_html,
                ('<p class="tile-delta %s">%s</p>' % (delta_cls, esc(delta_text))) if delta_text else "",
                ('<p class="tile-note">%s</p>' % esc(tile.get("note"))) if tile.get("note") else "",
            )
        )
    return '<div class="tiles">%s</div>' % "".join(cells)


def mini_tiles(tiles: list, limit: int) -> str:
    """The first ``limit`` tiles as the compact ``<li>`` list used by overview cards."""
    items = []
    for tile in (tiles or [])[:limit]:
        tile = tile if isinstance(tile, dict) else {}
        value = tile.get("value")
        known = is_number(value) or (isinstance(value, str) and value.strip())
        items.append('<li><span class="m-label">%s</span><span class="m-value%s">%s</span></li>'
                     % (esc(tile.get("label", "")),
                        "" if known else " unknown",
                        esc(fmt_compact(value, tile.get("unit")))))
    return "".join(items)


def build_timeline(items: list) -> str:
    if not items:
        return '<p class="empty">No timeline entries.</p>'
    rows = []
    for item in items:
        item = item if isinstance(item, dict) else {}
        url = safe_url(item.get("link"))
        text = esc(item.get("text", ""))
        if url:
            text = '<a href="%s">%s</a>' % (esc(url), text)
        rows.append(
            '<li><span class="t-time">%s</span><span class="t-text">%s</span></li>'
            % (esc(item.get("time", "")), text)
        )
    return '<ol class="timeline">%s</ol>' % "".join(rows)


def build_results(results: list) -> str:
    if not results:
        return '<p class="empty">No results reported.</p>'
    cards = []
    for res in results:
        res = res if isinstance(res, dict) else {}
        kind = res.get("kind")
        render = CHART_RENDERERS.get(kind)
        if render is None:
            chart = '<p class="empty">Unsupported chart kind: %s</p>' % esc(kind)
        else:
            chart = render(res)
        note = ('<p class="chart-note">%s</p>' % esc(res.get("note"))) if res.get("note") else ""
        cards.append(
            '<figure class="card chart-card">'
            '<figcaption><h3>%s</h3><p class="kind">%s</p></figcaption>'
            "%s%s</figure>"
            % (esc(res.get("title", "")), esc(kind), chart, note)
        )
    return "".join(cards)


def _letter(index: int) -> str:
    return chr(ord("A") + index) if index < 26 else str(index + 1)


def _is_recommended(option: str, recommended, index: int) -> bool:
    if recommended is None:
        return False
    rec = str(recommended).strip()
    opt = str(option).strip()
    if rec == opt:
        return True
    low_rec = rec.lower()
    if low_rec in ("%s" % _letter(index).lower(), "%s)" % _letter(index).lower(), "option %s" % _letter(index).lower()):
        return True
    if opt.lower().startswith(low_rec + ")") or opt.lower().startswith(low_rec + "."):
        return True
    if low_rec and opt.lower().startswith(low_rec):
        return True
    return False


def build_decisions(decisions: list) -> str:
    if not decisions:
        return '<p class="empty">No decisions pending.</p>'
    cards = []
    for decision in decisions:
        decision = decision if isinstance(decision, dict) else {}
        options = decision.get("options") or []
        rec = decision.get("recommended")
        option_html = []
        for i, opt in enumerate(options):
            cls = "rec" if _is_recommended(opt, rec, i) else ""
            badge = '<span class="rec-badge">recommended</span>' if cls else ""
            option_html.append('<li class="%s"><span class="opt-letter">%s</span>%s%s</li>'
                               % (cls, esc(_letter(i)), esc(opt), badge))
        cards.append(
            '<article class="card decision">'
            '<p class="d-num">Decision %s</p>'
            '<h3>%s</h3>'
            '<ul class="options">%s</ul>'
            "</article>"
            % (esc(decision.get("n", "")), esc(decision.get("question", "")), "".join(option_html))
        )
    return "".join(cards)


def _list_item_html(item) -> str:
    if isinstance(item, str):
        return esc(item)
    if isinstance(item, dict):
        text = esc(item.get("text", ""))
        url = safe_url(item.get("link"))
        if url:
            text = '<a href="%s">%s</a>' % (esc(url), text)
        note = item.get("note")
        if note:
            text += ' <span class="note">%s</span>' % esc(note)
        return text
    return esc(item)


def build_simple_list(items: list, kind: str) -> str:
    if not items:
        return '<p class="empty">None reported.</p>'
    rows = []
    for item in items:
        severity = ""
        if kind == "risks" and isinstance(item, dict) and item.get("severity"):
            sev = str(item["severity"]).lower()
            severity = '<span class="sev sev-%s">%s</span>' % (esc(sev), esc(sev))
        assignee = ""
        if kind == "next24h" and isinstance(item, dict) and item.get("assignee"):
            assignee = '<span class="assignee">%s</span>' % esc(item["assignee"])
        rows.append("<li>%s%s%s</li>" % (_list_item_html(item), severity, assignee))
    return '<ul class="%s">%s</ul>' % (kind, "".join(rows))


def build_evidence(items: list) -> str:
    if not items:
        return '<p class="empty">No evidence links.</p>'
    rows = []
    for item in items:
        item = item if isinstance(item, dict) else {}
        url = safe_url(item.get("link"))
        label = esc(item.get("label", ""))
        if url:
            rows.append('<li><a href="%s">%s</a></li>' % (esc(url), label))
        else:
            rows.append("<li>%s</li>" % label)
    return '<ul class="evidence">%s</ul>' % "".join(rows)


# ---------------------------------------------------------------------------
# date / metadata helpers
# ---------------------------------------------------------------------------


def parse_as_of(value) -> _dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if text.endswith("Z"):
            return _dt.datetime.fromisoformat(text[:-1]).replace(tzinfo=_dt.timezone.utc)
        dt = _dt.datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        pass
    try:
        return _dt.datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def human_time(value) -> str:
    dt = parse_as_of(value)
    if dt is None:
        return str(value)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def build_time(data_docs: list[dict]) -> str:
    """Deterministic build timestamp.

    ``SOURCE_DATE_EPOCH`` wins when set (CI / reproducible builds); otherwise the
    newest ``as_of`` across the inputs is used so that repeated builds of the same
    data produce identical bytes.
    """
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            dt = _dt.datetime.fromtimestamp(int(epoch), tz=_dt.timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OverflowError, OSError):
            pass
    stamps = [parse_as_of(doc.get("as_of")) for doc in data_docs if isinstance(doc, dict)]
    stamps = [s for s in stamps if s is not None]
    newest = max(stamps) if stamps else _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
    return newest.strftime("%Y-%m-%dT%H:%M:%SZ")


def source_commit() -> str:
    commit = os.environ.get("SOURCE_COMMIT") or os.environ.get("GITHUB_SHA") or "unknown"
    commit = commit.strip()
    if re.match(r"^[0-9a-fA-F]{40}$", commit):
        return commit[:7]
    return commit or "unknown"


# ---------------------------------------------------------------------------
# template rendering
# ---------------------------------------------------------------------------


def render_template(text: str, mapping: dict) -> str:
    def repl(match: re.Match) -> str:
        key = match.group(1)
        if key not in mapping:
            raise BuildError("template placeholders %r is not provided" % key)
        return str(mapping[key])

    return _PLACEHOLDER_RE.sub(repl, text)


def read_template(templates_dir: Path, name: str) -> str:
    path = templates_dir / name
    if not path.is_file():
        raise BuildError("missing template: %s" % path)
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def slug_of(data_path: Path) -> str:
    return data_path.stem


def load_content(content_dir: Path, slug: str) -> str | None:
    path = content_dir / ("%s.md" % slug)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def build_site(data_dir: Path, out_dir: Path, templates_dir: Path,
               schema_path: Path, content_dir: Path, order=None) -> list[Path]:
    schema = json.loads(schema_path.read_text(encoding="utf-8")) if schema_path.is_file() else {}

    files = discover_data_files(data_dir)
    if not files:
        raise BuildError("no data/*.json files found in %s" % data_dir)

    docs: list[tuple[Path, dict]] = []
    all_errors: list[str] = []
    for path in files:
        errors = check_file(path, schema)
        if errors:
            all_errors.extend(errors)
            continue
        try:
            value, _ = load_json_with_positions(path)
        except JsonError as exc:  # pragma: no cover - already covered above
            all_errors.append("%s:%d:%d: %s" % (path, exc.line, exc.col, exc.message))
            continue
        docs.append((path, value))
    if all_errors:
        raise BuildError("data failed validation:\n  " + "\n  ".join(all_errors))

    base = read_template(templates_dir, "base.html")
    index_tpl = read_template(templates_dir, "index.html")
    project_tpl = read_template(templates_dir, "project.html")
    style = read_template(templates_dir, "style.css")

    built = build_time([value for _, value in docs])
    commit = source_commit()
    out_dir.mkdir(parents=True, exist_ok=True)

    # project pages -------------------------------------------------------
    page_meta = []
    written: list[Path] = []
    for path, data in docs:
        slug = slug_of(path)
        project_name = str(data.get("project") or slug)
        as_of = data.get("as_of", "")
        markdown = load_content(content_dir, slug)
        details = ""
        if markdown:
            details = ('<section id="details" class="section"><h2>Details</h2>'
                       '<div class="markdown">%s</div></section>' % render_markdown(markdown))
        mapping = {
            "title": "%s — Morning Briefing" % project_name,
            "page_kind": "project",
            "as_of_iso": str(as_of),
            "as_of_human": human_time(as_of),
            "build_iso": built,
            "build_human": human_time(built),
            "commit": commit,
            "footer_note": "Generated by build.py from data/%s.json · no secrets, numbers and links only."
                           % slug,
            "project": project_name,
            "headline": data.get("headline", ""),
            "details": details,
            "tiles": build_tiles(data.get("tiles") or []),
            "timeline": build_timeline(data.get("timeline") or []),
            "results": build_results(data.get("results") or []),
            "decisions": build_decisions(data.get("decisions") or []),
            "risks": build_simple_list(data.get("risks") or [], "risks"),
            "next24h": build_simple_list(data.get("next24h") or [], "next24h"),
            "evidence": build_evidence(data.get("evidence") or []),
        }
        content = render_template(project_tpl, mapping)
        mapping["content"] = content
        page = render_template(base, mapping)
        target = out_dir / ("%s.html" % slug)
        target.write_text(page, encoding="utf-8")
        written.append(target)
        page_meta.append((slug, project_name, data))

    # index page ----------------------------------------------------------
    page_meta.sort(key=lambda item: order_key(item[0], resolve_order(order)))
    newest = max((d.get("as_of", "") for _, _, d in page_meta), default="")
    cards = []
    for slug, name, data in page_meta:
        cards.append(
            '<article class="card project-card">'
            '<h2><a href="%s.html">%s</a></h2>'
            '<p class="as-of">As of %s</p>'
            '<p class="headline">%s</p>'
            '<ul class="mini-tiles">%s</ul>'
            '<p class="more"><a href="%s.html">Open briefing →</a></p>'
            "</article>"
            % (esc(slug), esc(name), esc(human_time(data.get("as_of", ""))),
               esc(data.get("headline", "")), mini_tiles(data.get("tiles") or [], 4), esc(slug))
        )
    index_mapping = {
        "title": "Morning Briefing",
        "page_kind": "index",
        "as_of_iso": str(newest),
        "as_of_human": human_time(newest),
        "build_iso": built,
        "build_human": human_time(built),
        "commit": commit,
        "footer_note": "Generated by build.py · one page per project · no secrets, numbers and links only.",
        "lede": "One page per project. Key numbers, what happened, decisions to make and what is next.",
        "project_cards": "".join(cards),
    }
    content = render_template(index_tpl, index_mapping)
    index_mapping["content"] = content
    index = render_template(base, index_mapping)
    target = out_dir / "index.html"
    target.write_text(index, encoding="utf-8")
    written.append(target)

    style_target = out_dir / "style.css"
    style_target.write_text(style, encoding="utf-8")
    written.append(style_target)

    nojekyll = out_dir / ".nojekyll"
    nojekyll.write_text("", encoding="utf-8")
    written.append(nojekyll)

    return written


# ---------------------------------------------------------------------------
# single-file artifact
# ---------------------------------------------------------------------------

#: ``data/example-*.json`` is a worked example of a real project; it is only
#: published on request, and its anchor slug drops the marker.
EXAMPLE_PREFIX = "example-"

#: Environment variable holding the leading projects, comma- or space-separated,
#: e.g. ``BRIEFING_ORDER=pipeline,api``.  ``--order`` overrides it; with neither
#: every project is shown alphabetically.
ORDER_ENV = "BRIEFING_ORDER"


def parse_order(value) -> tuple[str, ...]:
    """Split a ``--order`` / ``$BRIEFING_ORDER`` value into project slugs."""
    if not value:
        return ()
    items = value if isinstance(value, (list, tuple)) else re.split(r"[,\s]+", str(value))
    order: list[str] = []
    for item in items:
        slug = str(item).strip().lstrip("./")
        if slug.endswith(".json"):
            slug = slug[: -len(".json")]
        if slug.startswith(EXAMPLE_PREFIX):
            slug = slug[len(EXAMPLE_PREFIX):]
        if slug and slug not in order:
            order.append(slug)
    return tuple(order)


def resolve_order(value=None) -> tuple[str, ...]:
    """The project order to use: the given value, else ``$BRIEFING_ORDER``, else none."""
    if value is None:
        value = os.environ.get(ORDER_ENV)
    return parse_order(value)


def order_key(slug: str, order: tuple[str, ...]):
    """Sort key: the slugs named in ``order`` first (in that order), then alphabetical."""
    rank = order.index(slug) if slug in order else len(order)
    return (rank, slug)


def artifact_slug(data_path: Path) -> str:
    """The section id / anchor for one project file.

    ``data/report.json`` -> ``report``; an example file stands in for the project
    it demonstrates, so ``data/example-report.json`` -> ``report`` too.
    """
    stem = data_path.stem
    if stem.startswith(EXAMPLE_PREFIX):
        return stem[len(EXAMPLE_PREFIX):] or stem
    return stem


def artifact_files(data_dir: Path, include_examples: bool) -> list[Path]:
    """The data files the artifact publishes, in discovery order."""
    files = discover_data_files(data_dir)
    if not include_examples:
        files = [p for p in files if not p.stem.startswith(EXAMPLE_PREFIX)]
    return files


def _artifact_section(title: str, body: str) -> str:
    return '<section class="section">\n<h2>%s</h2>\n%s\n</section>' % (esc(title), body)


def build_open_decisions(slug: str, count: int) -> str:
    """The overview card's decision line, linking to the project's section."""
    if count:
        text = "%d open decision%s" % (count, "" if count == 1 else "s")
        cls = "open-decisions"
    else:
        text, cls = "No open decisions", "open-decisions none"
    return '<p class="%s"><a href="#%s">%s</a></p>' % (cls, esc(slug), esc(text))


def build_artifact_card(slug: str, data: dict) -> str:
    """One overview card: headline, a few key numbers, decisions pending."""
    return (
        '<article class="card project-card">'
        '<h2><a href="#%s">%s</a></h2>'
        '<p class="headline">%s</p>'
        '<ul class="mini-tiles">%s</ul>'
        "%s"
        "</article>"
        % (esc(slug), esc(data.get("project") or slug), esc(data.get("headline", "")),
           mini_tiles(data.get("tiles") or [], 3),
           build_open_decisions(slug, len(data.get("decisions") or [])))
    )


def build_artifact_project(slug: str, data: dict, markdown: str | None) -> str:
    """One project block: the same section order and the same renderers as its page."""
    as_of = data.get("as_of", "")
    parts = [
        '<section id="%s" class="project">' % esc(slug),
        '<header class="project-head">',
        '<h1 class="page-title">%s</h1>' % esc(data.get("project") or slug),
        '<p class="headline">%s</p>' % esc(data.get("headline", "")),
        '<p class="stamp">As of <time datetime="%s">%s</time></p>'
        % (esc(as_of), esc(human_time(as_of))),
        "</header>",
    ]
    if markdown:
        parts.append(
            '<section class="section">\n<h2>Details</h2>\n'
            '<div class="markdown">%s</div>\n</section>' % render_markdown(markdown)
        )
    parts += [
        _artifact_section("Key numbers", build_tiles(data.get("tiles") or [])),
        _artifact_section("What happened", build_timeline(data.get("timeline") or [])),
        _artifact_section("Results", '<div class="chart-scroll">%s</div>'
                          % build_results(data.get("results") or [])),
        _artifact_section("Decisions needed", build_decisions(data.get("decisions") or [])),
        _artifact_section("Risks", build_simple_list(data.get("risks") or [], "risks")),
        _artifact_section("Next 24 hours", build_simple_list(data.get("next24h") or [], "next24h")),
        _artifact_section("Evidence", build_evidence(data.get("evidence") or [])),
        '<p class="back-top"><a href="#top">Back to top</a></p>',
        "</section>",
    ]
    return "\n".join(parts)


def _load_artifact_projects(files: list[Path], schema: dict, order: tuple[str, ...] = ()):
    """Validate the files and return ``[(slug, file stem, data)]`` in artifact order."""
    projects: list[tuple[str, str, dict]] = []
    seen: dict[str, Path] = {}
    all_errors: list[str] = []
    clashes: list[str] = []
    for path in files:
        errors = check_file(path, schema)
        if errors:
            all_errors.extend(errors)
            continue
        try:
            value, _ = load_json_with_positions(path)
        except JsonError as exc:  # pragma: no cover - check_file reports this first
            all_errors.append("%s:%d:%d: %s" % (path, exc.line, exc.col, exc.message))
            continue
        slug = artifact_slug(path)
        if slug in seen:
            clashes.append("%s and %s would both be #%s" % (seen[slug], path, slug))
            continue
        seen[slug] = path
        projects.append((slug, path.stem, value))
    if all_errors:
        raise BuildError("data failed validation:\n  " + "\n  ".join(all_errors))
    if clashes:
        raise BuildError("section ids must be unique:\n  " + "\n  ".join(clashes))
    projects.sort(key=lambda item: order_key(item[0], order))
    return projects


def build_artifact(data_dir: Path, out_path: Path, templates_dir: Path,
                   schema_path: Path, content_dir: Path,
                   include_examples: bool = False, order=None) -> Path:
    """Write one self-contained HTML file and return its path."""
    schema = json.loads(schema_path.read_text(encoding="utf-8")) if schema_path.is_file() else {}

    files = artifact_files(data_dir, include_examples)
    if not files:
        if discover_data_files(data_dir):
            raise BuildError(
                "nothing to publish: %s holds only %s*.json examples; "
                "add data/<project>.json or pass --include-examples"
                % (data_dir, EXAMPLE_PREFIX))
        raise BuildError("no data/*.json files found in %s" % data_dir)

    projects = _load_artifact_projects(files, schema, resolve_order(order))

    template = read_template(templates_dir, "artifact.html")
    style = read_template(templates_dir, "artifact.css")

    blocks = []
    for slug, stem, data in projects:
        blocks.append(build_artifact_project(slug, data, load_content(content_dir, stem)))
    newest = max((str(data.get("as_of", "")) for _, _, data in projects), default="")
    built = build_time([data for _, _, data in projects])
    mapping = {
        "title": "Morning Briefing",
        "style": style,
        "as_of_iso": newest,
        "as_of_human": human_time(newest),
        "lede": "One page, important things first: the numbers, what happened, "
                "what needs a decision and what is next.",
        "project_cards": "".join(build_artifact_card(slug, data) for slug, _, data in projects),
        "projects": "\n".join(blocks),
        "build_iso": built,
        "build_human": human_time(built),
        "commit": source_commit(),
        "footer_note": "Generated by build.py as one self-contained page · "
                       "no secrets, numbers and links only.",
    }
    page = render_template(template, mapping)

    out_path = Path(out_path)
    if str(out_path.parent):
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------


def _check_files(files: list[Path], schema_path: Path) -> int:
    if not schema_path.is_file():
        print("error: schema not found: %s" % schema_path, file=sys.stderr)
        return 1
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    failed = 0
    for path in files:
        messages = check_file(Path(path), schema)
        if messages:
            failed += 1
            for message in messages:
                print(message, file=sys.stderr)
        else:
            print("ok: %s" % path)
    if failed:
        print("%d of %d file(s) failed validation" % (failed, len(files)), file=sys.stderr)
        return 1
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the static morning-briefing site.")
    parser.add_argument("--check", nargs="*", metavar="FILE", default=None,
                        help="validate data files (and exit); with no files, validate all of data/*.json")
    parser.add_argument("--data", default=str(ROOT / "data"))
    parser.add_argument("--out", default=str(ROOT / "docs"))
    parser.add_argument("--artifact", metavar="FILE", default=None,
                        help="write one self-contained HTML file and exit")
    parser.add_argument("--include-examples", action="store_true",
                        help="with --artifact, also publish the data/example-*.json files")
    parser.add_argument("--order", metavar="SLUG,SLUG", default=None,
                        help="project slugs to show first, e.g. 'pipeline,api' "
                             "(default: $BRIEFING_ORDER, else alphabetical)")
    parser.add_argument("--templates", default=str(ROOT / "templates"))
    parser.add_argument("--schema", default=str(ROOT / "schema" / "briefing.schema.json"))
    parser.add_argument("--content", default=str(ROOT / "content"))
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.check is not None:
        if args.check:
            files = [Path(p) for p in args.check]
        else:
            files = discover_data_files(Path(args.data))
        if not files:
            print("error: no data files to check", file=sys.stderr)
            return 1
        return _check_files(files, Path(args.schema))

    if args.artifact:
        try:
            target = build_artifact(Path(args.data), Path(args.artifact), Path(args.templates),
                                    Path(args.schema), Path(args.content), args.include_examples,
                                    order=args.order)
        except BuildError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
        print("wrote %s" % target)
        return 0

    try:
        written = build_site(Path(args.data), Path(args.out), Path(args.templates),
                             Path(args.schema), Path(args.content), order=args.order)
    except BuildError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    for path in written:
        print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
