"""Rewrite arm of pipefail-advisory — issue #1334.

The advisory behaviour itself is covered by the sibling shell test; this file
covers only what the arm adds, plus the `_has_pipefail` predicate the arm and
the advisory now share.

  fires         a mutating command piped into tail / head / grep
  must NOT fire the `&&` masked-gating path (the hook's own primary remedy
                there is a restructuring it cannot perform)
  pipefail state ordering (a `set` after the pipeline does not count) and
                subshell scope (a `set` inside a pipeline does not count)
                a command carrying a line continuation
                a command that already sets pipefail
                a non-mutating pipeline
  arm switch    unset / "0" / " 1 " / "true" / "1"
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = (
    REPO_ROOT / "hooks" / "advisory-nudge" / "pipefail-advisory" / "impl.py"
)

spec = importlib.util.spec_from_file_location("pipefail_advisory", HOOK_PATH)
assert spec is not None and spec.loader is not None
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)  # type: ignore[union-attr]

ENV = "PRAXIS_PIPEFAIL_ADVISORY_REWRITE"
PIPED = "git commit -m x | tail -3"


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
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _rewrite(out: str) -> dict:
    return json.loads(out)["hookSpecificOutput"]


# --- the arm fires -----------------------------------------------------------

@pytest.mark.parametrize("command", [
    "git commit -m x | tail -3",
    "git push | head -1",
    "gh pr merge 1 2>&1 | tail -3",
    "git rebase main | grep error",
])
def test_a_mutating_pipeline_is_corrected(monkeypatch, capsys, command):
    rc, out, err = _run(monkeypatch, capsys, command)
    assert rc == 0
    hso = _rewrite(out)
    assert hso["updatedInput"]["command"] == "set -o pipefail; " + command
    # The advisory is replaced by the correction, not emitted alongside it.
    assert err == ""


def test_the_correction_keeps_the_other_tool_input_fields(monkeypatch, capsys):
    _, out, _ = _run(monkeypatch, capsys, PIPED)
    assert _rewrite(out)["updatedInput"]["description"] == "d"


def test_the_context_names_the_original_and_the_correction(monkeypatch, capsys):
    _, out, _ = _run(monkeypatch, capsys, PIPED)
    context = _rewrite(out)["additionalContext"]
    assert PIPED in context
    assert "set -o pipefail; " + PIPED in context


def test_the_corrected_command_no_longer_trips_the_advisory(monkeypatch, capsys):
    _, out, _ = _run(monkeypatch, capsys, PIPED)
    fixed = _rewrite(out)["updatedInput"]["command"]
    rc, out2, err2 = _run(monkeypatch, capsys, fixed)
    assert (rc, out2, err2) == (0, "", "")


# --- the arm stays off -------------------------------------------------------

@pytest.mark.parametrize("arm", [None, "0", "", "true", "11", " "])
def test_the_arm_is_opt_in(monkeypatch, capsys, arm):
    rc, out, err = _run(monkeypatch, capsys, PIPED, arm=arm)
    assert rc == 0
    assert out == ""
    assert "pipefail-advisory" in err


def test_a_padded_one_still_arms_it(monkeypatch, capsys):
    # `.strip() == "1"` is the documented contract, shared with the siblings.
    _, out, _ = _run(monkeypatch, capsys, PIPED, arm=" 1 ")
    assert _rewrite(out)["updatedInput"]["command"].startswith("set -o pipefail;")


def test_the_masked_gating_path_advises_instead_of_rewriting(monkeypatch, capsys):
    # The piped segment is non-mutating, so only `_masked_gating_advisory`
    # fires. Its own first remedy is splitting the chain, which no rewrite
    # can express, so the arm must not pick the second one on the actor's
    # behalf.
    rc, out, err = _run(
        monkeypatch, capsys, "git switch main 2>&1 | tail -1 && gh pr merge 1"
    )
    assert rc == 0
    assert out == ""
    assert "masked exit code gates an irreversible command" in err


def test_a_line_continuation_advises_instead_of_rewriting(monkeypatch, capsys):
    rc, out, err = _run(monkeypatch, capsys, "git commit -m x \\\n| tail -3")
    assert rc == 0
    assert out == ""
    assert "pipefail-advisory" in err


@pytest.mark.parametrize("command", [
    "ls -la | tail -3",           # not a mutating command
    "git commit -m x | cat",      # not a truncating sink
    "git commit -m x",            # no pipe at all
])
def test_a_command_with_nothing_to_fix_is_silent(monkeypatch, capsys, command):
    assert _run(monkeypatch, capsys, command) == (0, "", "")


# --- _has_pipefail -----------------------------------------------------------

@pytest.mark.parametrize("command", [
    "set -o pipefail; git commit -m x | tail -3",
    "set -eo pipefail; git commit -m x | tail -3",
    "set -euo pipefail && git push | head -1",
])
def test_an_existing_pipefail_silences_the_hook(monkeypatch, capsys, command):
    assert _run(monkeypatch, capsys, command) == (0, "", "")


@pytest.mark.parametrize("command", [
    # Ordering: the option cannot retroactively unmask a pipeline that has
    # already run, so a `set` AFTER the pipeline leaves the finding standing.
    "git commit -m x | tail -3; set -o pipefail",
    # Every segment of a pipeline runs in a subshell, so this turns the option
    # on in the subshell and leaves the parent exactly as it was.
    "set -o pipefail | cat; git commit -m x | tail -3",
    # `set` is not in command position, so this only mentions the option.
    "echo set -o pipefail; git commit -m x | tail -3",
    # `-o` without the option word after it does not turn pipefail on.
    "set -o errexit; git commit -m x | tail -3",
    # the word alone, with no `set` builtin
    "grep pipefail notes.txt; git commit -m x | tail -3",
])
def test_only_a_pipefail_that_precedes_the_pipeline_silences_it(
    monkeypatch, capsys, command
):
    rc, out, err = _run(monkeypatch, capsys, command, arm=None)
    assert rc == 0
    assert "pipefail-advisory" in err
