"""Unit tests for hooks/completion-signal-gate.py — Issue #392.

Tests cover:
  Rule 1 — completion-signal phrase without evidence-block:
    EN phrases: no fixes needed, ready to merge, all set, done, complete
    KR phrases: 실질적 수정.*없, 머지하셔도, 완료, 결함 없음, 이상 없음
    Evidence inhibitors: Bash tool call, Read tool call, cited $ … → output

  Rule 2 — plugin-context anchoring (cross-plugin slash command)

  False-positive cross-checks (5 normal-completion samples):
    - Turn with Bash tool + evidence signal
    - Mid-message 완료 (not in last turn's assistant output standalone)
    - Read tool call without completion phrase
    - Completion phrase with Bash evidence
    - Completion phrase with cited output line

  Fail-safe paths:
    - Malformed JSON stdin
    - Missing transcript_path
    - stop_hook_active=true
    - Empty transcript
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Load the hook module directly (not via subprocess for unit coverage)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "completion-signal-gate.py"

spec = importlib.util.spec_from_file_location("completion_signal_gate", HOOK_PATH)
assert spec is not None and spec.loader is not None
csg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csg)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# JSONL fixture builders
# ---------------------------------------------------------------------------


def mk_user(text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "isSidechain": False,
        "message": {"role": "user", "content": text},
    }


def mk_assistant(text: str, tools: list | None = None) -> dict[str, Any]:
    content: list[dict] = [{"type": "text", "text": text}]
    if tools:
        content.extend(tools)
    return {
        "type": "assistant",
        "isSidechain": False,
        "message": {"role": "assistant", "content": content},
    }


def mk_bash_use(id_: str, cmd: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": id_, "name": "Bash", "input": {"command": cmd}}


def mk_read_use(id_: str, path: str) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": id_,
        "name": "Read",
        "input": {"file_path": path},
    }


def mk_tool_result(tool_id: str, content: str) -> dict[str, Any]:
    return {
        "type": "user",
        "isSidechain": False,
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": content}
            ],
        },
    }


def write_jsonl(events: list[dict], tmp_path: Path) -> str:
    p = tmp_path / "transcript.jsonl"
    with p.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return str(p)


def run_hook(transcript_path: str) -> tuple[str, str, int]:
    """Run hook via subprocess, return (stdout, stderr, returncode)."""
    payload = json.dumps(
        {
            "transcript_path": transcript_path,
            "stop_hook_active": False,
            "session_id": "test-session",
        }
    )
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout, result.stderr, result.returncode


# ---------------------------------------------------------------------------
# Rule 1 — completion-signal phrases that SHOULD trigger advisory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        # EN phrases
        "no fixes needed",
        "No Fixes Needed",
        "ready to merge",
        "Ready to Merge",
        "all set",
        "All Set",
        "done",
        "DONE",
        "complete",
        "COMPLETE",
        # KR phrases from issue Event 1
        "실질적 수정은 없습니다. 머지하셔도 무방합니다.",
        "머지하셔도 됩니다",
        "완료",
        "결함 없음",
        "이상 없음",
    ],
)
def test_rule1_triggers_on_phrase(phrase: str, tmp_path: Path) -> None:
    """Completion-signal phrase without evidence → advisory emitted."""
    events = [
        mk_user("작업 결과를 보고해주세요"),
        mk_assistant(f"검토 결과: {phrase}"),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0, f"hook must exit 0 (advisory); got {rc}"
    assert stdout == "", f"advisory hook must produce no stdout; got: {stdout!r}"
    assert "[praxis:completion-signal-gate]" in stderr, (
        f"advisory not emitted for phrase {phrase!r}; stderr={stderr!r}"
    )


# ---------------------------------------------------------------------------
# Rule 1 — evidence-block suppressors that should PREVENT advisory
# ---------------------------------------------------------------------------


def test_rule1_suppressed_by_bash_tool(tmp_path: Path) -> None:
    """Bash tool call in same turn suppresses completion-signal advisory."""
    bash_use = mk_bash_use("b1", "python3 -m pytest tests/")
    result = mk_tool_result("b1", "5 tests passed")
    events = [
        mk_user("테스트 돌려주세요"),
        mk_assistant("테스트 실행합니다.", [bash_use]),
        result,
        mk_assistant("5 tests passed. 완료."),
    ]
    tp = write_jsonl(events, tmp_path)
    _, stderr, rc = run_hook(tp)
    assert rc == 0
    assert "[praxis:completion-signal-gate]" not in stderr, (
        f"must not trigger when Bash tool is present; stderr={stderr!r}"
    )


def test_rule1_suppressed_by_read_tool(tmp_path: Path) -> None:
    """Read tool call in same turn suppresses completion-signal advisory."""
    read_use = mk_read_use("r1", "/tmp/file.py")
    events = [
        mk_user("파일 확인해주세요"),
        mk_assistant("파일 읽겠습니다.", [read_use]),
        mk_assistant("파일 확인 완료. 이상 없음."),
    ]
    tp = write_jsonl(events, tmp_path)
    _, stderr, rc = run_hook(tp)
    assert rc == 0
    assert "[praxis:completion-signal-gate]" not in stderr, (
        f"must not trigger when Read tool is present; stderr={stderr!r}"
    )


def test_rule1_suppressed_by_cited_output_line(tmp_path: Path) -> None:
    """Cited '$ command → output' line suppresses advisory."""
    events = [
        mk_user("결과 확인"),
        mk_assistant(
            "검증 결과:\n$ pytest tests/ → 15 passed\n\n완료."
        ),
    ]
    tp = write_jsonl(events, tmp_path)
    _, stderr, rc = run_hook(tp)
    assert rc == 0
    assert "[praxis:completion-signal-gate]" not in stderr, (
        f"must not trigger with cited output; stderr={stderr!r}"
    )


# ---------------------------------------------------------------------------
# False-positive cross-checks (5 normal-completion samples)
# ---------------------------------------------------------------------------


def test_fp1_bash_evidence_complete(tmp_path: Path) -> None:
    """Normal completion with Bash + evidence — must NOT trigger. (FP1)"""
    bash_use = mk_bash_use("fp1", "python3 -m pytest tests/ -v")
    result = mk_tool_result("fp1", "15 tests passed in 1.2s")
    events = [
        mk_user("테스트 결과?"),
        mk_assistant("실행 중...", [bash_use]),
        result,
        mk_assistant("15 tests passed in 1.2s. 완료."),
    ]
    tp = write_jsonl(events, tmp_path)
    _, stderr, rc = run_hook(tp)
    assert rc == 0
    assert "[praxis:completion-signal-gate]" not in stderr, f"FP1 false-positive: {stderr!r}"


def test_fp2_no_completion_phrase(tmp_path: Path) -> None:
    """Response without any completion phrase — must NOT trigger. (FP2)"""
    events = [
        mk_user("어떻게 진행할까요?"),
        mk_assistant("다음 단계는 PR 생성입니다. 브랜치를 먼저 확인하겠습니다."),
    ]
    tp = write_jsonl(events, tmp_path)
    _, stderr, rc = run_hook(tp)
    assert rc == 0
    assert "[praxis:completion-signal-gate]" not in stderr, f"FP2 false-positive: {stderr!r}"


def test_fp3_read_tool_with_completion_phrase(tmp_path: Path) -> None:
    """Completion phrase + Read tool → suppressed. (FP3)"""
    read_use = mk_read_use("fp3r", "/Users/nathan/projects/praxis/hooks/hooks.json")
    result = mk_tool_result("fp3r", '{"hooks": {...}}')
    events = [
        mk_user("hooks.json 확인해주세요"),
        mk_assistant("읽겠습니다.", [read_use]),
        result,
        mk_assistant("hooks.json 확인 완료. 이상 없음."),
    ]
    tp = write_jsonl(events, tmp_path)
    _, stderr, rc = run_hook(tp)
    assert rc == 0
    assert "[praxis:completion-signal-gate]" not in stderr, f"FP3 false-positive: {stderr!r}"


def test_fp4_bash_lint_clean(tmp_path: Path) -> None:
    """Lint-clean completion with Bash — must NOT trigger. (FP4)"""
    bash_use = mk_bash_use("fp4", "ruff check hooks/")
    result = mk_tool_result("fp4", "All checks passed. 0 errors.")
    events = [
        mk_user("lint 결과는?"),
        mk_assistant("실행 중...", [bash_use]),
        result,
        mk_assistant("0 errors. All done."),
    ]
    tp = write_jsonl(events, tmp_path)
    _, stderr, rc = run_hook(tp)
    assert rc == 0
    assert "[praxis:completion-signal-gate]" not in stderr, f"FP4 false-positive: {stderr!r}"


def test_fp5_assistant_in_progress(tmp_path: Path) -> None:
    """Mid-task assistant message without completion phrase — must NOT trigger. (FP5)"""
    bash_use = mk_bash_use("fp5", "git status")
    result = mk_tool_result("fp5", "On branch main\nnothing to commit")
    events = [
        mk_user("현재 상태 확인"),
        mk_assistant("확인 중...", [bash_use]),
        result,
        mk_assistant("브랜치 상태 확인됨. 다음으로 PR을 생성하겠습니다."),
    ]
    tp = write_jsonl(events, tmp_path)
    _, stderr, rc = run_hook(tp)
    assert rc == 0
    assert "[praxis:completion-signal-gate]" not in stderr, f"FP5 false-positive: {stderr!r}"


# ---------------------------------------------------------------------------
# Rule 1 — Event 1 reproduction (exact issue quote)
# ---------------------------------------------------------------------------


def test_event1_exact_quote(tmp_path: Path) -> None:
    """Event 1 from issue body: '실질적 수정은 없습니다 ... 머지하셔도 무방합니다'."""
    events = [
        mk_user("PR #388/#389/#390 리뷰 결과 보고"),
        mk_assistant(
            "PR #389 결과:\n\n실질적 수정은 없습니다. 머지하셔도 무방합니다."
        ),
    ]
    tp = write_jsonl(events, tmp_path)
    _, stderr, rc = run_hook(tp)
    assert rc == 0
    assert "[praxis:completion-signal-gate]" in stderr, (
        f"Event 1 quote must trigger advisory; stderr={stderr!r}"
    )


# ---------------------------------------------------------------------------
# Rule 2 — plugin-context anchoring
# ---------------------------------------------------------------------------


def test_rule2_foreign_plugin_command(tmp_path: Path) -> None:
    """Foreign plugin /laplace-dev-hub:release in praxis cwd → advisory."""
    events = [
        mk_user("다음 단계는 뭔가요?"),
        mk_assistant(
            "현재 이슈를 닫으려면 /laplace-dev-hub:close-hub-issue 를 실행하거나 "
            "/release 스킬을 사용할 수 있습니다."
        ),
    ]
    tp = write_jsonl(events, tmp_path)
    # Run hook from praxis worktree cwd (has .claude-plugin/marketplace.json)
    payload = json.dumps(
        {"transcript_path": tp, "stop_hook_active": False, "session_id": "test-rule2"}
    )
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(REPO_ROOT),  # Run from praxis root so plugin name = "praxis"
    )
    assert result.returncode == 0
    # Should emit advisory about foreign namespace
    assert "[praxis:completion-signal-gate]" in result.stderr, (
        f"Rule 2 must fire for foreign plugin command; stderr={result.stderr!r}"
    )


def test_rule2_bare_foreign_skill(tmp_path: Path) -> None:
    """Bare `/release` (no namespace) in praxis cwd → advisory (Event 2 trigger)."""
    events = [
        mk_user("Hub 이슈들을 일괄 정리해주세요"),
        mk_assistant("`/release` 스킬을 사용하면 일괄 처리할 수 있습니다."),
    ]
    tp = write_jsonl(events, tmp_path)
    payload = json.dumps(
        {"transcript_path": tp, "stop_hook_active": False, "session_id": "test-rule2-bare"}
    )
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "[praxis:completion-signal-gate]" in result.stderr, (
        f"Rule 2 must fire for bare foreign skill `/release`; stderr={result.stderr!r}"
    )


def test_rule2_bare_unknown_command_silent(tmp_path: Path) -> None:
    """Bare unknown word in praxis cwd → silent (false-positive guard)."""
    events = [
        mk_user("어디서 봤어?"),
        mk_assistant("`/some-random-word` 는 무관한 문자열입니다."),
    ]
    tp = write_jsonl(events, tmp_path)
    payload = json.dumps(
        {"transcript_path": tp, "stop_hook_active": False, "session_id": "test-rule2-bare-unknown"}
    )
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "[praxis:completion-signal-gate]" not in result.stderr, (
        f"Rule 2 must NOT fire for unknown bare commands; stderr={result.stderr!r}"
    )


def test_rule2_namespaced_foreign_silent_in_non_praxis_cwd(tmp_path: Path) -> None:
    """Namespaced foreign command in laplace-dev-hub cwd → no advisory.

    Rule 2 namespaced branch fires only when cwd_plugin == 'praxis'.
    When working inside another plugin (e.g. laplace-dev-hub), the hook
    must remain silent even if a foreign-namespaced command appears in output.
    """
    # Simulate laplace-dev-hub cwd by creating a .claude-plugin/marketplace.json
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "marketplace.json").write_text(
        json.dumps({"name": "laplace-dev-hub"})
    )
    events = [
        mk_user("다음 단계는 뭔가요?"),
        mk_assistant(
            "/oh-my-claudecode:ralph 스킬로 루프를 돌려보세요."
        ),
    ]
    tp = write_jsonl(events, tmp_path)
    payload = json.dumps(
        {
            "transcript_path": tp,
            "stop_hook_active": False,
            "session_id": "test-rule2-non-praxis",
        }
    )
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(tmp_path),  # laplace-dev-hub cwd, NOT praxis
    )
    assert result.returncode == 0
    assert "[praxis:completion-signal-gate]" not in result.stderr, (
        f"Rule 2 must NOT fire in non-praxis cwd; stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Fail-safe paths
# ---------------------------------------------------------------------------


def test_failsafe_malformed_json() -> None:
    """Malformed JSON stdin → exit 0, no advisory."""
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input="{not valid json",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_failsafe_stop_hook_active(tmp_path: Path) -> None:
    """stop_hook_active=true → exit 0, no advisory."""
    events = [
        mk_user("테스트"),
        mk_assistant("이상 없음."),
    ]
    tp = write_jsonl(events, tmp_path)
    payload = json.dumps(
        {"transcript_path": tp, "stop_hook_active": True, "session_id": "test"}
    )
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stderr == ""


def test_failsafe_missing_transcript() -> None:
    """Missing transcript_path → exit 0, no advisory."""
    payload = json.dumps(
        {
            "transcript_path": "/nonexistent/path.jsonl",
            "stop_hook_active": False,
            "session_id": "test",
        }
    )
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stderr == ""


def test_failsafe_empty_transcript(tmp_path: Path) -> None:
    """Empty transcript → exit 0, no advisory."""
    tp = str(tmp_path / "empty.jsonl")
    Path(tp).write_text("")
    payload = json.dumps(
        {"transcript_path": tp, "stop_hook_active": False, "session_id": "test"}
    )
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# Internal unit tests for helper functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("no fixes needed here", True),
        ("ready to merge", True),
        ("all set", True),
        ("The implementation is done.", True),
        ("complete", True),
        ("실질적 수정은 없습니다", True),
        ("머지하셔도 됩니다", True),
        ("완료", True),
        ("결함 없음", True),
        ("이상 없음", True),
        # Should NOT match
        ("disclose all the details", False),
        ("completely different topic", False),
        ("the task is done/not done, unclear", True),  # "done" matched
        ("undone", False),  # word-boundary check
        ("incomplete", False),  # word-boundary: "complete" in "incomplete"
    ],
)
def test_has_completion_signal(text: str, expected: bool) -> None:
    assert csg._has_completion_signal(text) == expected, (
        f"_has_completion_signal({text!r}) expected {expected}"
    )


@pytest.mark.parametrize(
    "last_text,has_bash,has_read,expected",
    [
        ("done", True, False, True),   # bash tool present
        ("done", False, True, True),   # read tool present
        ("$ pytest → 5 passed\ndone", False, False, True),  # cited output
        ("done", False, False, False),  # no evidence
    ],
)
def test_has_evidence_block(
    last_text: str, has_bash: bool, has_read: bool, expected: bool
) -> None:
    assert csg._has_evidence_block(last_text, has_bash, has_read) == expected
