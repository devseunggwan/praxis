"""Differential + contract tests for the hoisted git-argv parser (B2, #594).

`hooks/_lib/git_commit_titles.py` is the single source of truth for the
commit-title extraction that commit-title-format-check and
commit-title-length-check previously duplicated byte-for-byte. The two copies
had already drifted: length-check carried a dead `tok.startswith("-") is not
False` quirk that format-check did not. This suite locks three guarantees:

  1. **Contract** — `extract_git_titles()` returns the correct title list for
     the union of every argv shape the two oracle suites exercise
     (-m / --message / -m= / --message= / -mvalue / -am / -F file / global
     -C / -c, first-message-only, -S cluster rejection, ...).

  2. **Dead-quirk equivalence** — the `=`-embedded branch (where the two copies
     differed) behaves identically under the adopted clean form. Because
     `str.startswith` always returns a bool, `b is not False` ≡ `b`; the
     embedded `-m=`/`--message=` path is asserted directly so a regression in
     that exact branch fails here.

  3. **Single source** — both hooks import the SAME function object from _lib,
     so the parser can no longer drift across the two gates.

  4. **Fail-open** — malformed / dangling / non-git argv returns a list and
     never raises (the hooks are fail-open by contract).

Run: python3 -m pytest tests/test_git_commit_titles.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "hooks" / "_lib"
GATE_DIR = REPO_ROOT / "hooks" / "preflight-gate"

# git_commit_titles.py inserts its own dir on sys.path to find _hook_utils, but
# inserting here too makes the import order-independent.
sys.path.insert(0, str(LIB_DIR))


def _load(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Load the canonical module first so the two impl modules' `from
# git_commit_titles import ...` resolves to this exact object.
gct = _load("git_commit_titles", LIB_DIR / "git_commit_titles.py")
extract_git_titles = gct.extract_git_titles


# ---------------------------------------------------------------------------
# 1. Contract — union of oracle-suite argv shapes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "argv, expected",
    [
        # separate-token message forms
        (["git", "commit", "-m", "hello world"], ["hello world"]),
        (["git", "commit", "--message", "hello world"], ["hello world"]),
        # embedded '=' forms (THE branch that carried the dead quirk)
        (["git", "commit", "-m=hello"], ["hello"]),
        (["git", "commit", "--message=hello"], ["hello"]),
        # attached short-option form
        (["git", "commit", "-mhello"], ["hello"]),
        # combined short clusters terminating in -m
        (["git", "commit", "-am", "hello"], ["hello"]),
        (["git", "commit", "-vsm", "hello"], ["hello"]),
        # only the FIRST -m contributes the title; later -m are body
        (["git", "commit", "-m", "title", "-m", "body para"], ["title"]),
        # --amend before -m
        (["git", "commit", "--amend", "-m", "hi"], ["hi"]),
        # git global flags stripped before the subcommand
        (["git", "-C", "/some/path", "commit", "-m", "hi"], ["hi"]),
        (["git", "-c", "key=val", "commit", "-m", "hi"], ["hi"]),
        # -S<keyid> must NOT be misread as a message even though it ends in 'm'
        (["git", "commit", "-Smike@example.com"], []),
        # not a git-commit argv
        (["echo", "hi"], []),
        (["git", "status"], []),
        (["git", "commit", "--amend"], []),
        # a positional containing '=' that does not start with '-' is ignored
        (["git", "commit", "key=val", "-m", "hi"], ["hi"]),
    ],
)
def test_extract_contract(argv, expected):
    assert extract_git_titles(argv) == expected


# ---------------------------------------------------------------------------
# 2. Dead-quirk equivalence — embedded `=` branch
# ---------------------------------------------------------------------------

def test_embedded_message_branch_clean_form():
    """The `if "=" in tok and tok.startswith("-")` branch (clean form) must
    accept `-m=`/`--message=` exactly as the old `is not False` quirk did."""
    assert extract_git_titles(["git", "commit", "-m=quirk path"]) == ["quirk path"]
    assert extract_git_titles(["git", "commit", "--message=quirk path"]) == ["quirk path"]
    # a token with '=' but not starting with '-' takes neither branch
    assert extract_git_titles(["git", "commit", "a=b"]) == []


# ---------------------------------------------------------------------------
# 3. -F / --file forms (first line of the file is the title)
# ---------------------------------------------------------------------------

def test_file_message_forms(tmp_path):
    msg = tmp_path / "msg.txt"
    msg.write_text("feat: from file\n\nbody line\n", encoding="utf-8")
    assert extract_git_titles(["git", "commit", "-F", str(msg)]) == ["feat: from file"]
    assert extract_git_titles(["git", "commit", "--file", str(msg)]) == ["feat: from file"]
    assert extract_git_titles(["git", "commit", f"-F={msg}"]) == ["feat: from file"]


def test_file_relative_resolves_against_dash_C(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    msg = sub / "rel.txt"
    msg.write_text("fix: relative file\n", encoding="utf-8")
    argv = ["git", "-C", str(sub), "commit", "-F", "rel.txt"]
    assert extract_git_titles(argv) == ["fix: relative file"]


def test_file_stdin_and_unreadable_are_silent(tmp_path):
    # `-F -` is stdin → silent (no title)
    assert extract_git_titles(["git", "commit", "-F", "-"]) == []
    # unreadable path → silent (no raise, no title)
    missing = tmp_path / "does-not-exist.txt"
    assert extract_git_titles(["git", "commit", "-F", str(missing)]) == []


# ---------------------------------------------------------------------------
# 4. Single source of truth — both gates import the SAME object
# ---------------------------------------------------------------------------

def test_both_hooks_share_one_parser_object():
    fmt = _load(
        "impl_format_check",
        GATE_DIR / "commit-title-format-check" / "impl.py",
    )
    length = _load(
        "impl_length_check",
        GATE_DIR / "commit-title-length-check" / "impl.py",
    )
    # Both modules' `extract_git_titles` must be the very object defined in _lib.
    assert fmt.extract_git_titles is extract_git_titles
    assert length.extract_git_titles is extract_git_titles


# ---------------------------------------------------------------------------
# 5. Fail-open — malformed argv returns a list, never raises
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["git"],
        ["git", "commit"],
        ["git", "commit", "-m"],        # dangling -m, no value
        ["git", "commit", "-F"],        # dangling -F, no value
        ["git", "commit", "--message"],  # dangling long flag
        ["git", "-C"],                  # dangling global flag
        ["git", "commit", "-"],         # lone dash
        ["git", "commit", "--"],        # end-of-options sentinel
    ],
)
def test_fail_open_returns_list(argv):
    result = extract_git_titles(argv)
    assert isinstance(result, list)
