"""Rewrite arm of gh-flag-verify — issue #1334.

The deny behaviour itself is covered by the sibling shell test; this file
covers only what the arm adds, and the input surface it has to survive:

  fires         exactly one allowed long flag one edit away, and the offending
                token carried a value (inline or as the next word)
  must NOT fire two or more candidates one edit away
                no candidate at all
                a short flag (every single letter is one edit from every other)
                a value-taking replacement with no value supplied
                a compound command
                a command carrying a line continuation
  arm switch    unset / "0" / "1"
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "preflight-gate" / "gh-flag-verify" / "impl.py"

spec = importlib.util.spec_from_file_location("gh_flag_verify", HOOK_PATH)
assert spec is not None and spec.loader is not None
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)  # type: ignore[union-attr]

ENV = "PRAXIS_GH_FLAG_VERIFY_REWRITE"


def _run(monkeypatch, capsys, command: str, arm: str | None = "1"):
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "d"},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    if arm is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, arm)
    rc = hook.main()
    return rc, json.loads(capsys.readouterr().out or "{}")


def _hso(out: dict) -> dict:
    return out["hookSpecificOutput"]


# --- the arm fires -----------------------------------------------------------

def test_a_unique_near_miss_with_a_value_is_corrected(monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys, "gh pr list --stat open")
    assert rc == 0
    assert _hso(out)["updatedInput"]["command"] == "gh pr list --state open"


def test_an_inline_value_is_carried_through(monkeypatch, capsys):
    _, out = _run(monkeypatch, capsys, "gh pr list --stat=open")
    assert _hso(out)["updatedInput"]["command"] == "gh pr list --state=open"


def test_the_correction_keeps_the_other_tool_input_fields(monkeypatch, capsys):
    _, out = _run(monkeypatch, capsys, "gh pr list --stat open")
    assert _hso(out)["updatedInput"]["description"] == "d"


def test_the_context_names_both_flags_and_both_commands(monkeypatch, capsys):
    _, out = _run(monkeypatch, capsys, "gh pr list --stat open")
    context = _hso(out)["additionalContext"]
    assert "--stat" in context and "--state" in context
    assert "gh pr list --stat open" in context
    assert "gh pr list --state open" in context


def test_the_corrected_command_no_longer_denies(monkeypatch, capsys):
    _, out = _run(monkeypatch, capsys, "gh pr list --stat open")
    fixed = _hso(out)["updatedInput"]["command"]
    assert _run(monkeypatch, capsys, fixed) == (0, {})


# --- the arm stays off -------------------------------------------------------

@pytest.mark.parametrize("arm", [None, "0", "", "true", "11"])
def test_the_arm_is_opt_in(monkeypatch, capsys, arm):
    rc, out = _run(monkeypatch, capsys, "gh pr list --stat open", arm=arm)
    assert rc == 2
    assert _hso(out)["permissionDecision"] == "deny"


def test_a_value_taking_flag_with_no_value_still_denies(monkeypatch, capsys):
    # `gh pr list --state` is itself rejected by gh, so swapping the name
    # trades one error for another rather than correcting anything.
    rc, out = _run(monkeypatch, capsys, "gh pr list --stat")
    assert rc == 2
    assert _hso(out)["permissionDecision"] == "deny"


def test_a_flag_with_no_near_miss_still_denies(monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys, "gh pr list --zzzzzz value")
    assert rc == 2
    assert _hso(out)["permissionDecision"] == "deny"


def test_a_compound_command_still_denies(monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys, "gh pr list --stat open && echo done")
    assert rc == 2
    assert _hso(out)["permissionDecision"] == "deny"


def test_a_line_continuation_still_denies(monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys, "gh pr list \\\n--stat open")
    assert rc == 2
    assert _hso(out)["permissionDecision"] == "deny"


def test_a_valid_command_is_untouched(monkeypatch, capsys):
    assert _run(monkeypatch, capsys, "gh pr list --state open") == (0, {})


# --- unique_near_miss --------------------------------------------------------

def test_two_candidates_one_edit_away_produce_no_correction():
    allowed = {"--state": True, "--stats": True}
    assert hook.unique_near_miss("--stat", allowed, True) is None


def test_one_candidate_one_edit_away_produces_it():
    allowed = {"--state": True, "--limit": True}
    assert hook.unique_near_miss("--stat", allowed, True) == "--state"


def test_a_short_flag_never_produces_a_correction():
    # `-b` sits one edit from every other single letter, so uniqueness here
    # says nothing about what was meant.
    assert hook.unique_near_miss("-b", {"-B": False, "--base": True}, True) is None


@pytest.mark.parametrize("offender", ["--sta", "--staaate", "--limit"])
def test_a_flag_more_than_one_edit_away_is_not_a_near_miss(offender):
    assert hook.unique_near_miss(offender, {"--state": True}, True) is None


@pytest.mark.parametrize("a,b,expected", [
    ("--stat", "--state", True),    # insertion
    ("--statee", "--state", True),  # deletion
    ("--stete", "--state", True),   # substitution
    ("--state", "--state", False),  # identical is zero edits, not one
    ("--st", "--state", False),     # two insertions
])
def test_edit_distance_1(a, b, expected):
    assert hook._edit_distance_1(a, b) is expected
