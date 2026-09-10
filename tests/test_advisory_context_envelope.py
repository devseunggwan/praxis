"""The five mutation advisories emit ONE well-formed context envelope (#1265).

A substring check on stdout (`"additionalContext" in out`) passes on output the
harness cannot use: stdout takes exactly one JSON document, so a hook that emits
a second one produces a pair that `json.loads` rejects — and the dispatcher then
drops the model-facing context entirely while every substring assertion still
passes. That is not hypothetical: `external-write-path-existence-check` walks one
`--body-file` per iteration, and a command carrying two of them emitted two
objects before this test existed.

So each hook is run against a payload that makes it fire, and its stdout is
parsed as a whole document: one object, `hookEventName` correct, and the hook's
own marker inside `additionalContext`.

Run: python3 -m pytest tests/test_advisory_context_envelope.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "hooks" / "advisory-nudge"

# Env that must not leak in from the developer's shell. A strict flag routes the
# hook down the exit-2 path, which emits no stdout at all; a bypass flag makes it
# return before it decides anything, so the hook exits 0 silently and the
# envelope assertion fails for a reason that has nothing to do with the code.
_STRICT_VARS = (
    "PRAXIS_DESTRUCTIVE_BASH_STRICT",
    "PRAXIS_PROTECTED_PATHS_STRICT",
    "PRAXIS_PERSONAL_LEAK_STRICT",
    "PRAXIS_PHANTOM_PATH_STRICT",
)
#: Bypass vars are cleared by prefix rather than by name, so a hook that gains
#: one later is covered without this list being updated.
_BYPASS_PREFIX = "PRAXIS_HOOK_BYPASS_"


def _run(hook: str, payload: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    for var in _STRICT_VARS:
        e.pop(var, None)
    for var in [v for v in e if v.startswith(_BYPASS_PREFIX)]:
        e.pop(var)
    # The self-edit guard skips synthesized paths under the real plugin root.
    e["CLAUDE_PLUGIN_ROOT"] = "/nonexistent-plugin-root-for-tests"
    e.update(env or {})
    return subprocess.run(
        [sys.executable, str(HOOKS / hook / "impl.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=e,
    )


def _assert_envelope(proc: subprocess.CompletedProcess, marker: str) -> str:
    """Assert stdout is exactly one context envelope carrying `marker`."""
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "hook did not fire — payload no longer triggers it"
    # Whole-document parse: a second emitted object fails here, which is the
    # regression this file exists for.
    obj = json.loads(proc.stdout)
    inner = obj["hookSpecificOutput"]
    assert inner["hookEventName"] == "PreToolUse"
    context = inner["additionalContext"]
    assert marker in context
    # The stderr line is what the fire ledger grades the fire on, so it is not
    # optional — the two channels are emitted together or the grade changes.
    assert marker in proc.stderr
    return context


_BASH = "Bash"

CASES = [
    (
        "destructive-bash-guard",
        {"tool_name": _BASH, "tool_input": {"command": "rm -rf ./build"}},
        "[destructive-bash-guard]",
    ),
    (
        "protected-paths-guard",
        {"tool_name": "Write", "tool_input": {"file_path": "/Users/alice/.ssh/id_rsa", "content": "k"}},
        "[protected-paths-guard]",
    ),
    (
        "block-personal-asset-leak",
        {"tool_name": _BASH, "tool_input": {
            "command": 'gh issue create --title foo --body "see /Users/alice/.claude/settings.json for config"'}},
        "REMINDER",
    ),
    (
        "secret-print-redaction-advisory",
        {"tool_name": _BASH, "tool_input": {"command": (
            "TOKEN=$(aws secretsmanager get-secret-value --secret-id fake "
            '--query SecretString --output text); echo "$TOKEN"')}},
        "[secret-print-redaction-advisory]",
    ),
]


@pytest.mark.parametrize("hook,payload,marker", CASES, ids=[c[0] for c in CASES])
def test_single_context_envelope(hook: str, payload: dict, marker: str) -> None:
    _assert_envelope(_run(hook, payload), marker)


def test_phantom_path_aggregates_multiple_body_files(tmp_path: Path) -> None:
    """Two `--body-file` arguments must still produce ONE envelope, naming both.

    Per-body emission produced two JSON objects, which the dispatcher's
    `json.loads` rejects — the context was silently lost for exactly the command
    that had the most to report.
    """
    first = tmp_path / "a.md"
    second = tmp_path / "b.md"
    first.write_text("# A\n\nsee [x](hooks/pre-tool-use/aaa.sh)\n")
    second.write_text("# B\n\nsee [y](hooks/pre-tool-use/bbb.sh)\n")

    proc = _run(
        "external-write-path-existence-check",
        {
            "session_id": "envelope-test",
            "tool_name": _BASH,
            "tool_input": {"command": (
                f"gh issue create --title T --body-file {first} && "
                f"gh pr comment 1 --body-file {second}")},
            "cwd": str(REPO_ROOT),
        },
    )
    context = _assert_envelope(proc, "[phantom-path]")
    assert "hooks/pre-tool-use/aaa.sh" in context
    assert "hooks/pre-tool-use/bbb.sh" in context
