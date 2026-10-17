# briefing-site

A static site generator for short, recurring status briefings. One JSON file per
project describes its current state — key numbers, what happened, decisions to
make, risks, the next 24 hours, links. `build.py` turns those files into a
multi-page static site with inline SVG charts, and optionally into a single
self-contained HTML file that can be pasted into a chat tool or wiki page.

`build.py` uses the Python standard library only. There is no server, no
JavaScript, no network access and no third-party package; a build is
deterministic (the same inputs and environment produce the same bytes).

## Layout

```
build.py                      generator (stdlib only)
data/<project>.json           one file per project — the file most people edit
data/example-*.json           worked examples, labelled EXAMPLE
content/<project>.md          optional narrative (minimal markdown subset)
schema/briefing.schema.json   the data contract, validated on every build
templates/                    base.html, index.html, project.html, style.css,
                              artifact.html, artifact.css
tests/                        unittest suite
docs/                         generated site (committed; the tests rebuild it
                              and compare it byte for byte)
tools/secret-scan.sh          credential scanner
tools/hooks/pre-push          git hook that runs the scanner on outgoing commits
tools/install-git-hooks.sh    installs that hook
```

## Install

Nothing to install beyond Python:

- Python 3.10 or newer (`python3 --version`). No pip packages, no virtualenv
  needed. CI runs the test suite on Python 3.10 and 3.14.

```sh
git clone <this repository> briefing-site
cd briefing-site
python3 build.py --check             # validate every data/*.json
python3 build.py                     # write the site into docs/
python3 -m unittest discover -s tests   # run the test suite
```

## Worked example

The two files in `data/` are examples; the site built from them is committed in
`docs/`.

```sh
python3 build.py --check data/example-api.json      # ok: data/example-api.json
python3 build.py                                    # docs/index.html, docs/example-api.html,
                                                    # docs/example-pipeline.html, docs/style.css
python3 build.py --artifact out/briefing.html --include-examples
                                                    # one self-contained page
```

Open `docs/index.html` in a browser: an index listing both projects, then one
page per project with the fixed section order **Details** (only when a
`content/<project>.md` exists) → **Key numbers** → **What happened** →
**Results** → **Decisions needed** → **Risks** → **Next 24 hours** →
**Evidence**. `out/briefing.html` contains the same sections as one fragment
(`<title>`, one `<style>` block, one `<div>`) for hosts that wrap it themselves.

To publish your own briefing, copy `data/example-api.json` to
`data/<project>.json`, edit it, run `python3 build.py`, and commit `docs/`.
`data/example-api.json` also shows every chart kind except `hbar` and `stacked`,
which are in `data/example-pipeline.json`; `content/example-api.md` shows the
markdown subset.

## Configuration

CLI options (all paths default to the repository root):

| option | default | meaning |
|---|---|---|
| `--data DIR` | `data` | directory of `<project>.json` files |
| `--out DIR` | `docs` | output directory for the site |
| `--artifact FILE` | — | write one self-contained HTML fragment and exit |
| `--include-examples` | off | with `--artifact`, also publish `data/example-*.json` |
| `--order SLUG,SLUG` | alphabetical | project slugs to show first (artifact and index) |
| `--templates DIR` | `templates` | html/css templates |
| `--schema FILE` | `schema/briefing.schema.json` | JSON schema to validate against |
| `--content DIR` | `content` | optional `<project>.md` files |
| `--check [FILE ...]` | — | validate the given files, or all of `--data`, and exit |

Environment variables:

| variable | meaning |
|---|---|
| `BRIEFING_ORDER` | same as `--order` (the flag wins if both are set) |
| `SOURCE_DATE_EPOCH` | fixes the footer build time (reproducible builds) |
| `SOURCE_COMMIT` / `GITHUB_SHA` | revision shown in the footer (short form when it is a 40-character sha) |

Without `SOURCE_DATE_EPOCH` the footer build time is the newest `as_of` across
the inputs, so repeated builds of unchanged data are byte-identical. Without
`SOURCE_COMMIT`/`GITHUB_SHA` the footer shows `unknown`.

## Data contract

One file per project at `data/<project>.json`; the file name is the URL slug
(`data/core.json` → `docs/core.html`). Unknown values must be `null` or omitted
and always render as an em dash `—`, never as `0`. Required keys: `project`,
`as_of` (ISO 8601), `headline`.

| key | type | notes |
|---|---|---|
| `tiles` | array | up to ~6 key numbers, shown as a grid |
| `timeline` | array | what happened, oldest first |
| `results` | array | charts (five kinds, below) |
| `decisions` | array | open questions, one recommended option highlighted |
| `risks` | array | strings, or `{text, severity: low\|medium\|high, note}` |
| `next24h` | array | strings, or `{text, assignee, when}` |
| `evidence` | array | `{label, link}` — links, not raw data |

Every number carries its unit in a `unit` string (`"ms"`, `"%"`, `"memories"`,
`"USD"`, …), not inside the number. Percentages are the number itself (`94` with
`"unit": "%"`).

```json
{"label": "Latency p95", "value": 180, "unit": "ms", "delta": -22, "note": "target 250 ms"}
```

`value` is a number, a short string, or `null`; `delta` is a number or a short
string and the `+`/`-` prefix colours it.

Charts — each result is `{"title", "kind", "series", "note"?, "unit"?}`, and
`series` depends on `kind`:

- `bar` / `hbar` (vertical / horizontal): `{"label": "09-24", "value": 1284, "unit": "requests"}`
- `line` (sparkline): `{"label": "p95", "points": [231, 226, 180], "unit": "ms"}`
  plus an optional result-level `"x": ["09-22", "09-23", "09-24"]`
- `stacked`: `{"label": "PM", "segments": [{"label": "shipped", "value": 90}, {"label": "blocked", "value": 60}]}`
- `quota` (hit-list): `{"label": "/v1/search", "used": 820000, "limit": 1000000, "unit": "calls/day"}`;
  the bar shows `used / limit` as a percentage, at or above the limit gets a
  **HIT LIMIT** badge and 80–99% uses the warning colour

Any `value`, `used` or `limit` may be `null` and renders as `—`. Decisions are
`{"n", "question", "recommended", "options"}` where `recommended` is an option
letter (`"B"`) or its exact text. `content/<project>.md` renders as the
*Details* block and supports `#`–`######` headings, `-`/`*`/`+` bullets,
`[text](link)` and `**bold**`; HTML is escaped and only `https`, `mailto`,
relative and `#fragment` links are allowed.

`build.py --check` validates every file against `schema/briefing.schema.json`
(a subset of JSON Schema draft 2020-12 implemented in `build.py`) and reports
errors with `file:line:column`.

## Limits and caveats

- The section order and the five chart kinds are fixed by the generator; there
  is no plugin system, no theming beyond editing `templates/`.
- The output is static HTML and inline SVG: no interactivity, no client-side
  filtering, no server, no database.
- `docs/` is committed, and the test suite fails when it does not match a fresh
  build — re-run `python3 build.py` after changing `data/`, `content/` or
  `templates/`.
- The site build publishes every `data/*.json` file in `--data` (examples
  included; they carry `EXAMPLE` in their text). `--artifact` leaves
  `data/example-*.json` out unless `--include-examples` is given.
- The artifact is a fragment without `<!doctype>`, `<html>`, `<head>` or
  `<body>`; it is meant to be embedded by a host that supplies those.
- The data contract cannot enforce "numbers and links only". Keep secrets out by
  review, and use `tools/secret-scan.sh` (below).
- The example files contain invented data. No real, private or production data
  is in this repository.

## Tests

```sh
python3 -m unittest discover -s tests
```

123 tests, standard library only, no network. They cover the schema validator
and its error positions, every chart kind, the markdown subset, escaping of
hostile input, the fixed section order, determinism (two builds are byte
comparison-identical), the committed `docs/` versus a fresh build, and the
repository's own secret scanner. Run them from the repository root.

The subprocess-based tests (the scanner and the git hook) run with `HOME` and
the `XDG_*` directories pointed at a temporary directory, so they do not read
your configuration. If `bash` or `grep` is missing they are **skipped** with a
message naming the missing program, and if `git` is missing the hook test is
skipped — the other tests still run.

## Secret scan

```sh
tools/secret-scan.sh          # scan the tree; exit 0 clean, 1 hit, 2 scanner error
tools/install-git-hooks.sh    # install the pre-push hook (runs the scan on outgoing commits)
```

The hook fails closed: a missing scanner or any scanner error refuses the push.
Git does not clone hooks, so run `tools/install-git-hooks.sh` once per clone.

## License

MIT — see `LICENSE`.
