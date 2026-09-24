"""Tests for hooks/_lib/_mutating_call.py — shared mutating-call classifier (#1490)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "hooks" / "_lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from _mutating_call import (  # noqa: E402
    bash_is_readonly,
    is_mutating_call,
    mcp_is_mutating,
)


@pytest.mark.parametrize("command", [
    "git status",
    "git log --oneline -5",
    "gh pr list --repo o/r",
    "gh api repos/o/r/pulls",
    "gh api --method GET search/issues -f q=x",
    "kubectl get pods -n x | grep Running",
])
def test_readonly_bash_is_not_mutating(command):
    assert bash_is_readonly(command)
    assert not is_mutating_call("Bash", {"command": command})


@pytest.mark.parametrize("command", [
    "git push origin HEAD",
    "gh pr create --title t --body b",
    "gh api -X POST repos/o/r/issues",
    "gh api repos/o/r/issues -f title=t",
    "echo hi > out.txt",
    "echo $(rm -rf x)",
    "orgctl token fetch x",
    "git status && git push",
])
def test_writing_or_unknown_bash_is_mutating(command):
    assert not bash_is_readonly(command)
    assert is_mutating_call("Bash", {"command": command})


def test_empty_bash_command_is_mutating():
    # Nothing recognised is not a read-only shape; fail toward asking.
    assert is_mutating_call("Bash", {"command": ""})
    assert is_mutating_call("Bash", {})


@pytest.mark.parametrize("tool", ["Edit", "Write", "NotebookEdit"])
def test_file_edit_tools_are_mutating(tool):
    assert is_mutating_call(tool, {"file_path": "/x"})


@pytest.mark.parametrize("tool,expected", [
    ("mcp__github__create_pull_request", True),
    ("mcp__slack__slack_send_message", True),
    ("mcp__github__merge_pull_request", True),
    ("mcp__github__list_labels", False),
    ("mcp__trino__trino_query", False),
    ("mcp__s3__s3_count_records", False),
])
def test_mcp_by_write_verb(tool, expected):
    assert mcp_is_mutating(tool) is expected
    assert is_mutating_call(tool, {}) is expected


@pytest.mark.parametrize("tool", ["Read", "Grep", "Glob", "WebFetch"])
def test_non_mcp_read_tools_are_not_mutating(tool):
    assert not is_mutating_call(tool, {})
