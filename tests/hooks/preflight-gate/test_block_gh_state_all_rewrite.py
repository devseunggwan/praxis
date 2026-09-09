"""Rewrite arm of block-gh-state-all — issue #1334.

The block behaviour itself is covered by the sibling shell test; this file
covers only what the arm adds, and the input surface it has to survive:

  spelling      `--state all` / `--state=all` / quoted value
  position      before and after the search object word
  must NOT fire compound commands (`--state all` is valid for `gh issue list`)
                a command carrying a line continuation
                lookalikes: `--state allowed`, `--state open`
  arm switch    unset / "0" / "1"
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "preflight-gate" / "block-gh-state-all" / "impl.py"

spec = importlib.util.spec_from_file_location("block_gh_state_all", HOOK_PATH)
assert spec is not None and spec.loader is not None
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)  # type: ignore[union-attr]


def _run(monkeypatch, capsys, command: str, arm: str | None = "1"):
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "search"},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    if arm is None:
        monkeypatch.delenv("PRAXIS_BLOCK_GH_STATE_ALL_REWRITE", raising=False)
    else:
        monkeypatch.setenv("PRAXIS_BLOCK_GH_STATE_ALL_REWRITE", arm)
    rc = hook.main()
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


@pytest.mark.parametrize("command,expected", [
    ("gh search issues praxis --state all", "gh search issues praxis"),
    ("gh search issues praxis --state=all", "gh search issues praxis"),
    ("gh search prs --state all --limit 5", "gh search prs --limit 5"),
    ('gh search issues x --state "all"', "gh search issues x"),
    ("gh search issues x --state 'all'", "gh search issues x"),
    ("gh -R o/r search issues x --state all", "gh -R o/r search issues x"),
])
def test_arm_corrects_every_spelling_and_position(monkeypatch, capsys, command, expected):
    rc, out, err = _run(monkeypatch, capsys, command)
    assert rc == 0
    hso = json.loads(out)["hookSpecificOutput"]
    assert hso["updatedInput"]["command"] == expected
    # The other fields of the input survive the correction.
    assert hso["updatedInput"]["description"] == "search"
    # The correction is announced: the transcript keeps the original command,
    # so an unannounced rewrite is invisible to every later reader.
    assert expected in hso["additionalContext"]
    assert err == ""


@pytest.mark.parametrize("arm", [None, "0", "", "true"])
def test_the_arm_is_off_by_default(monkeypatch, capsys, arm):
    rc, out, err = _run(monkeypatch, capsys, "gh search issues x --state all", arm)
    assert rc == 2
    assert out == ""
    assert err != ""


def test_compound_command_still_blocks(monkeypatch, capsys):
    # `--state all` is VALID for `gh issue list`, so a textual removal across a
    # compound command would break the half that was correct.
    rc, out, err = _run(
        monkeypatch, capsys,
        "gh search issues x --state all && gh issue list --state all",
    )
    assert rc == 2
    assert out == ""
    assert err != ""


def test_line_continuation_still_blocks(monkeypatch, capsys):
    rc, out, _err = _run(
        monkeypatch, capsys, "gh search issues x \\\n  --state all"
    )
    assert rc == 2
    assert out == ""


@pytest.mark.parametrize("command", [
    "gh search issues x --state open",
    "gh search issues x --state allowed",
    "gh issue list --state all",
    "gh pr list --state all",
])
def test_lookalikes_are_untouched(monkeypatch, capsys, command):
    # Negative control for the six corrections above: the arm must not fire on
    # a command the unarmed hook already passes.
    rc, out, err = _run(monkeypatch, capsys, command)
    assert (rc, out, err) == (0, "", "")


def test_corrected_refuses_what_it_cannot_certify():
    # The readback is the certification, so it has to be able to say no: a
    # command with two `--state all` occurrences is not a single-flag removal.
    assert hook.corrected("gh search issues x --state all --state all") is None
    assert hook.corrected("gh search issues x") is None


@pytest.mark.parametrize("command,expected", [
    ("gh search issues all --state all", "gh search issues all"),
    ("gh search issues all --state=all", "gh search issues all"),
    ("gh search prs all all --state all", "gh search prs all all"),
])
def test_all_as_a_query_term_survives_the_correction(monkeypatch, capsys, command, expected):
    # `all` is an ordinary search word. Locating the removal by token TEXT
    # dropped the query term from the expected list, so the readback refused a
    # command the hook can correct and it blocked instead.
    rc, out, _err = _run(monkeypatch, capsys, command)
    assert rc == 0
    assert json.loads(out)["hookSpecificOutput"]["updatedInput"]["command"] == expected
