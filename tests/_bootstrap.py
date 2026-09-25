"""Shared bootstrap for the briefing tests.

Every test module imports ``build`` from the repository root (the parent of
``tests/``).  unittest discovery from ``tests/`` puts this directory on
``sys.path``, so ``import _bootstrap`` works from the sibling modules.
"""

import json
import os
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import build  # noqa: E402  (imported after sys.path is fixed)

DATA_DIR = REPO / "data"
TEMPLATES_DIR = REPO / "templates"
CONTENT_DIR = REPO / "content"
SCHEMA_PATH = REPO / "schema" / "briefing.schema.json"
DOCS_DIR = REPO / "docs"

EXAMPLE_FILES = sorted(DATA_DIR.glob("*.json"))


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, obj) -> pathlib.Path:
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def isolated_env(home: pathlib.Path) -> dict:
    """A minimal environment for tests that run a subprocess.

    ``HOME`` and every ``XDG_*`` directory point inside ``home`` (a temporary
    directory the caller owns), so nothing outside it is read or written and the
    test does not depend on the machine it runs on.
    """
    home = pathlib.Path(home)
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "LANG": "C",
    }


def missing_tools(*names: str) -> list:
    """The names from ``names`` that are not executable on PATH.

    Used to skip a shell-dependent test with a clear message instead of
    weakening the assertion it makes.
    """
    return [name for name in names if shutil.which(name) is None]

