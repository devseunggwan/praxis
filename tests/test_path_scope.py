"""Contract + single-source tests for `hooks/_lib/_path_scope.py` (#1362).

Two hooks — `protected-paths-guard` and `settings-path-advisory` — each wrote
their own "is this path scratch?" test, and both got the same two cases wrong:
a relative `tmp/x` was treated as `/tmp/x`, and `/tmp/../repo/x` kept the
exemption because `..` was never collapsed. The rule now lives here once.

This suite locks three things:

  1. **Contract** — absolute-prefix and fragment membership, including the two
     defects above and the traversal case for fragments.
  2. **Single source** — both hooks import the SAME function objects, so the
     two copies cannot drift apart again.
  3. **Purely lexical** — the verdict never depends on what exists on disk,
     which is what lets a PreToolUse hook judge a path before it is written.

Run: python3 -m pytest tests/test_path_scope.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "hooks" / "_lib"

sys.path.insert(0, str(LIB_DIR))

import _path_scope as P  # type: ignore[import-not-found]  # noqa: E402

TMP = ("/tmp/", "/private/tmp/")
FRAGS = ("/.omc/plans/", "/.claude/projects/")


# --- under_absolute_prefix: the two defects -------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "tmp/.env",
        "tmp/config/credentials",
        "private/tmp/.env",
        "./tmp/.env",
        "proj/tmp/cert.pem",
    ],
)
def test_relative_path_is_never_under_an_absolute_prefix(path):
    """The pre-fix helpers wrote `"/" + path.lstrip("/")` first, which handed
    the scratch exemption to every project `tmp/` directory."""
    assert P.under_absolute_prefix(path, TMP) is False


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/../proj/.env",
        "/private/tmp/../proj/.env",
        "/tmp/a/../../proj/.env",
    ],
)
def test_traversal_out_of_the_prefix_is_not_under_it(path):
    """Starts with the prefix as characters, resolves outside it."""
    assert P.under_absolute_prefix(path, TMP) is False


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/sketch.env",
        "/tmp/./sketch.env",
        "/tmp/a/b/../c/.env",
        "/private/tmp/.env",
    ],
)
def test_genuine_scratch_still_matches(path):
    assert P.under_absolute_prefix(path, TMP) is True


def test_prefix_directory_itself_does_not_match():
    """Prefixes carry their trailing slash, so `/tmp` is not inside `/tmp/`."""
    assert P.under_absolute_prefix("/tmp", TMP) is False


def test_lookalike_sibling_does_not_match():
    assert P.under_absolute_prefix("/tmpfoo/.env", TMP) is False


def test_backslash_reads_as_a_separator():
    assert P.under_absolute_prefix("/tmp\\sketch.env", TMP) is True


# --- contains_fragment ----------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/proj/.omc/plans/sketch.env",
        ".omc/plans/sketch.env",
        "/proj/.claude/projects/X/log.env",
        ".claude/projects/X/log.env",
    ],
)
def test_fragment_matches_relative_and_absolute(path):
    """Unlike a prefix, a fragment names a directory anywhere in the path, so
    a relative path matching it is the intent, not a defect."""
    assert P.contains_fragment(path, FRAGS) is True


@pytest.mark.parametrize(
    "path",
    [
        "/proj/.omc/plans/../../.env",
        "/proj/.claude/projects/X/../../../.env",
    ],
)
def test_traversal_out_of_the_fragment_loses_the_match(path):
    """Normalization runs before the slash is prepended, so `..` cannot walk
    out of the named directory and keep the exemption."""
    assert P.contains_fragment(path, FRAGS) is False


def test_fragment_is_component_bounded():
    assert P.contains_fragment("/proj/my.omc/plansX/.env", FRAGS) is False


# --- normalized -----------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("/tmp/../proj/.env", "/proj/.env"),
        ("a/./b/../c", "a/c"),
        ("/tmp\\x\\y", "/tmp/x/y"),
        ("", "."),
    ],
)
def test_normalized(raw, want):
    assert P.normalized(raw) == want


# --- lexical only ---------------------------------------------------------

def test_verdict_does_not_touch_the_filesystem(monkeypatch):
    """A PreToolUse hook judges a path the tool has not written yet, so the
    answer must not depend on what exists on the runner."""
    def boom(*a, **k):  # pragma: no cover - only reached on a regression
        raise AssertionError("path scope must not stat the filesystem")

    monkeypatch.setattr("os.path.exists", boom)
    monkeypatch.setattr("os.path.realpath", boom)
    monkeypatch.setattr("os.stat", boom)
    assert P.under_absolute_prefix("/tmp/../proj/.env", TMP) is False
    assert P.contains_fragment("/proj/.omc/plans/x", FRAGS) is True


# --- single source --------------------------------------------------------

def _load_hook(role: str, name: str):
    path = REPO_ROOT / "hooks" / role / name / "impl.py"
    spec = importlib.util.spec_from_file_location(f"_hook_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_both_hooks_share_the_same_function_objects():
    """The point of the module: the two copies can no longer drift."""
    guard = _load_hook("advisory-nudge", "protected-paths-guard")
    settings = _load_hook("advisory-nudge", "settings-path-advisory")
    assert guard.under_absolute_prefix is P.under_absolute_prefix
    assert settings.under_absolute_prefix is P.under_absolute_prefix
    assert guard.contains_fragment is P.contains_fragment
