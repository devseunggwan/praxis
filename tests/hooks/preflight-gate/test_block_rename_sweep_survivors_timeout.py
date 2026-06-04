"""Unit tests for TimeoutExpired handling in block-rename-sweep-survivors.

Verifies that a hung subprocess.run call (git grep or git diff) raises
subprocess.TimeoutExpired and that @fail_open converts it to exit 0.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Load the hook module directly for unit coverage
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = (
    REPO_ROOT
    / "hooks"
    / "preflight-gate"
    / "block-rename-sweep-survivors"
    / "impl.py"
)

spec = importlib.util.spec_from_file_location("block_rename_sweep_survivors", HOOK_PATH)
assert spec is not None and spec.loader is not None
brs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brs)  # type: ignore[union-attr]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COMMIT_PAYLOAD = json.dumps(
    {"tool_name": "Bash", "tool_input": {"command": "git commit -m test"}}
)


def _run_main(payload: str, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    return brs.main()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_timeout_expired_on_git_diff_returns_0(monkeypatch: pytest.MonkeyPatch) -> None:
    """git diff --cached hanging raises TimeoutExpired; @fail_open returns 0."""

    def _raise_timeout(*args: object, **kwargs: object) -> None:
        cmd = args[0] if args else []
        if "diff" in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)
        # git grep should not be reached when git diff raises
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr("subprocess.run", _raise_timeout)
    result = _run_main(_COMMIT_PAYLOAD, monkeypatch)
    assert result == 0


def test_timeout_expired_on_git_grep_returns_0(monkeypatch: pytest.MonkeyPatch) -> None:
    """git grep hanging raises TimeoutExpired; @fail_open returns 0."""

    # Simulate: git diff succeeds (returns diff with a sweep), git grep hangs.
    # We need a diff that contains a sweep so _survivors() is actually called.
    _sweep_diff = (
        "diff --git a/f1.py b/f1.py\n"
        "--- a/f1.py\n"
        "+++ b/f1.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-lifecycle_stage = 1\n"
        "-lifecycle_stage = 2\n"
        "-lifecycle_stage = 3\n"
        "+lifecycle_phase = 1\n"
        "+lifecycle_phase = 2\n"
        "+lifecycle_phase = 3\n"
    )

    call_count = {"n": 0}

    def _side_effect(*args: object, **kwargs: object) -> object:
        cmd = args[0] if args else []
        call_count["n"] += 1
        if "diff" in cmd:
            # Return a fake CompletedProcess with the sweep diff
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=_sweep_diff, stderr="")
        if "grep" in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr("subprocess.run", _side_effect)
    result = _run_main(_COMMIT_PAYLOAD, monkeypatch)
    assert result == 0
    # Both diff and grep were attempted
    assert call_count["n"] >= 2
