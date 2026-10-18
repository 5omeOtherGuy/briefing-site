# AGENTS.md

## Profile
- Purpose: Static morning-briefing site generator: one JSON per project in, HTML with inline SVG charts out (stdlib only).
- Stage: prototype (repository standard 2026-10-18)
- Gate: `scripts/gate` (CI check `gate`)
- Landing: direct to main after a green gate; agents land: yes (default)
- Release: none; agents release: no (default)
- Outside issues and PRs: agents triage (default); outside PRs merge: owner approves (default)
- Sensitive paths: `AGENTS.md`, `.agents/`, `.github/workflows/`, `scripts/gate`, `tools/` (secret scan and its pre-push hook), `schema/briefing.schema.json` and the CLI and schema validator in `build.py` (public interfaces)

No deliberate deviations from the repository standard.

## Map

- `build.py`: the whole generator, CLI and schema validator (stdlib only).
- `data/<project>.json`: one briefing per project; `data/example-*.json` are invented examples.
- `content/<project>.md`: optional narrative for a project page.
- `schema/briefing.schema.json`: the data contract, validated on every build.
- `templates/`: HTML and CSS templates for the site and the single-file artifact.
- `docs/`: the generated site, committed. The tests rebuild it and compare it byte for byte.
- `tests/`: unittest suite; `tests/_bootstrap.py` holds shared paths and helpers.
- `tools/secret-scan.sh`, `tools/hooks/pre-push`, `tools/install-git-hooks.sh`: the secret scanner and its optional pre-push hook.
- `scripts/gate`: the gate. `.github/workflows/gate.yml` runs it on Python 3.10 and 3.14.
- `.agents/setup`: orb setup (checks Python, reports whether `gh` is authenticated).

## Landing (prototype)

1. Make one small commit per concern, with a Conventional Commits subject.
2. Run `scripts/gate`; it prints nothing when everything passes.
3. A change to a sensitive path (see the profile) gets an independent review of its diff (another agent or the owner) before it is pushed; record the verdict in the commit body.
4. Push to `main`. Push at the end of every session.
5. Try risky ideas on a `spike/<slug>` branch; merge it when done, or tag it `archive/<slug>`, push the tag and delete the branch.

## Rules for this repository

- Standard library only, at runtime and in tests. Adding a dependency needs the owner.
- Supported Python: 3.10 (oldest with security support) to the newest stable release. When a version reaches end of life, change the CI matrix and the README Install section together.
- After changing `build.py`, `data/`, `content/` or `templates/`, run `python3 build.py` and commit the regenerated `docs/` in the same commit; the gate fails on a stale `docs/`.
- The examples are invented. Never add real, private or production briefing data: this repository is public and `docs/` publishes every file in `data/`.
- Tests build credential-shaped strings from parts so the secret scan stays clean; never commit a literal token, even a fake one.
- There are no live tests and no credentials; the gate is hermetic.
- CI runs the gate in the matrix job `python`, whose checks are named `python (3.10)` and `python (3.14)`; the aggregate job `gate` (check `gate`) succeeds only when both legs succeeded. Require `gate` in rulesets and tools.
- `tools/install-git-hooks.sh` installs into the directory `git rev-parse --git-path hooks` names. When `git config core.hooksPath` is set (as in orbs), that is a global directory shared by every repository, so check it before running the script.

## Focused tests

```sh
python3 -m unittest discover -s tests -p test_charts.py -v           # one module
python3 -m unittest discover -s tests -p test_charts.py -k TestCharts  # one class or test name
```
