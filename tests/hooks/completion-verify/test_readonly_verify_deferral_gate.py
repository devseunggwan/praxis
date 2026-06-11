"""Unit tests for hooks/completion-verify/readonly-verify-deferral-gate/impl.py — Issue #641.

Tests cover:
  Three-signal AND gate (read-only ∧ deferral ∧ ¬mutation):
    Signal A variants: SELECT…FROM, SHOW TABLES, DESCRIBE, kubectl get,
      git status, aws list-, gh pr view, --dry-run
    Signal B variants: EN (should I / want me to / shall I / would you like /
      let me know if you), KO (진행할까요 / 확인할까요 / 원하시면 / 알려주세요)
    Mutation carve-out: git push, gh pr merge, kubectl delete, uppercase
      INSERT/DELETE, curl -X POST, KO 머지/배포/삭제

  Content-aware suppressor: read tool actually run in the turn → no advisory
  Negative cross-checks: read w/o deferral, deferral w/o read
  Env modes: STRICT → exit 2, BYPASS → silent, SIGNALS extension
  Fail-safe paths: malformed JSON, stop_hook_active, missing/empty transcript
  Internal unit tests for helper functions
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Load the hook module directly (not via subprocess for unit coverage)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = (
    REPO_ROOT
    / "hooks"
    / "completion-verify"
    / "readonly-verify-deferral-gate"
    / "impl.py"
)

spec = importlib.util.spec_from_file_location("readonly_verify_deferral_gate", HOOK_PATH)
assert spec is not None and spec.loader is not None
rvg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rvg)  # type: ignore[union-attr]

PREFIX = "[praxis:readonly-verify-deferral-gate]"


# ---------------------------------------------------------------------------
# JSONL fixture builders
# ---------------------------------------------------------------------------



def _msg(stdout: str) -> str:
    """Extract the systemMessage from the hook's stdout JSON.

    Issue #647 H3: advisories arrive as `{"systemMessage": ...}` on stdout
    (stderr stays empty). Empty stdout (silent pass) maps to "".
    """
    if not stdout.strip():
        return ""
    return json.loads(stdout)["systemMessage"]


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


def mk_mcp_use(id_: str, name: str, inp: dict) -> dict[str, Any]:
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


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


def run_hook(
    transcript_path: str,
    env_extra: dict[str, str] | None = None,
    stop_hook_active: bool = False,
) -> tuple[str, str, int]:
    """Run hook via subprocess, return (stdout, stderr, returncode)."""
    payload = json.dumps(
        {
            "transcript_path": transcript_path,
            "stop_hook_active": stop_hook_active,
            "session_id": "test-session",
        }
    )
    env = dict(os.environ)
    # Clear inherited toggles so the harness is deterministic regardless of the
    # developer's shell.
    for k in ("PRAXIS_READONLY_VERIFY_STRICT", "PRAXIS_READONLY_VERIFY_BYPASS",
              "PRAXIS_READONLY_VERIFY_SIGNALS"):
        env.pop(k, None)
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    return result.stdout, result.stderr, result.returncode


# ---------------------------------------------------------------------------
# Trigger cases — read-only offer (Signal A + Signal B, no mutation)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Signal A variants paired with a deferral
        "should I run `SELECT count(*) FROM orders` to confirm?",
        "want me to `SHOW TABLES` first?",
        "shall I `DESCRIBE users` to verify the column?",
        "should I check `kubectl get pods -n prod`?",
        "want me to look at `git status` before continuing?",
        "should I run `aws s3 list-buckets`?",
        "shall I `gh pr view 123` to confirm the state?",
        "should I re-run it with `--dry-run`?",
        # Korean deferral forms
        "`SELECT * FROM users` 조회를 진행할까요?",
        "`kubectl get pods` 로 확인할까요?",
        "원하시면 `git diff` 결과를 알려주세요... 아 `git diff` 를 보여드릴까요?",
        "`git status` 확인이 필요하시면 알려주세요.",
    ],
)
def test_triggers_on_readonly_offer(text: str, tmp_path: Path) -> None:
    """Read-only command named + deferred to user → advisory emitted."""
    events = [mk_user("작업 결과 보고"), mk_assistant(text)]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0, f"advisory mode must exit 0; got {rc}"
    assert stderr == "", f"advisory hook must keep stderr empty; got {stderr!r}"
    assert PREFIX in _msg(stdout), f"advisory not emitted for {text!r}; stdout={stdout!r}"


# ---------------------------------------------------------------------------
# Mutation carve-out — asking is legitimate → suppressed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Read signal + deferral but a mutation is also present
        "should I check `git status` and then `git push`?",
        "want me to run the migration `INSERT INTO audit ...` after `SELECT`?",
        "`SELECT ...` 확인 후 `gh pr merge` 진행할까요?",
        "should I `kubectl get pods` then `kubectl delete pod x`?",
        "want me to `git diff` and then DELETE the stale rows?",
        "`git status` 확인하고 배포를 진행할까요?",
        "should I check it with `curl -X POST https://api/x`? want me to run it?",
    ],
)
def test_mutation_carveout_suppresses(text: str, tmp_path: Path) -> None:
    """Mutation present → asking is legitimate → no advisory."""
    events = [mk_user("진행 방법"), mk_assistant(text)]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", f"mutation carve-out failed: {text!r}; stdout={stdout!r}"


# ---------------------------------------------------------------------------
# Already-ran suppressor — past-tense run cue in the SAME offer sentence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # read + deferral + past-tense run cue all in one sentence → already run
        "`SELECT count(*) FROM orders` 를 실행했고 결과를 확인할까요?",
        "I ran `git status` — want me to look at the diff?",
        "`kubectl get pods` 조회했는데 더 확인할까요?",
        "I checked `git status`; should I show you the full output?",
    ],
)
def test_already_ran_cue_suppresses(text: str, tmp_path: Path) -> None:
    """An offer sentence carrying a past-tense run cue → read already ran → no warn."""
    events = [mk_user("상태"), mk_assistant(text)]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", f"already-ran cue must suppress: {text!r}; stdout={stdout!r}"


def test_declarative_report_then_courtesy_no_advisory(tmp_path: Path) -> None:
    """Read ran via tool, final text reports it declaratively + courtesy close → no warn.

    The read and the deferral land in different sentences, so the same-sentence
    gate suppresses regardless of tool history.
    """
    bash = mk_bash_use("b1", "git status")
    res = mk_tool_result("b1", "On branch main\nnothing to commit")
    events = [
        mk_user("상태 확인"),
        mk_assistant("확인하겠습니다.", [bash]),
        res,
        mk_assistant("`git status` 확인했습니다. 추가로 진행할까요?"),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", f"declarative report must not warn; stdout={stdout!r}"


def test_mixed_turn_offered_read_differs_from_run_read_warns(tmp_path: Path) -> None:
    """Mixed turn: a read ran earlier, but the FINAL text offers a DIFFERENT read.

    Codex #641 P2: a coarse 'any read tool ran this turn' suppressor would wrongly
    silence this. The offered read (SELECT) was NOT run — only `git status` was —
    so the offer sentence has no past-run cue and the advisory must fire.
    """
    bash = mk_bash_use("b1", "git status")
    res = mk_tool_result("b1", "On branch main\nnothing to commit")
    events = [
        mk_user("확인 후 다음 작업"),
        mk_assistant("먼저 상태를 봅니다.", [bash]),
        res,
        mk_assistant("Should I run `SELECT count(*) FROM orders` next?"),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert PREFIX in _msg(stdout), (
        f"mixed turn (offered read != run read) must warn; stdout={stdout!r}"
    )


# ---------------------------------------------------------------------------
# Negative cross-checks — only one of the two signals present
# ---------------------------------------------------------------------------


def test_read_without_deferral_no_advisory(tmp_path: Path) -> None:
    """Declarative read mention with no deferral phrasing → no advisory."""
    events = [
        mk_user("결과?"),
        mk_assistant("`SELECT count(*) FROM orders` 결과는 1,024건입니다."),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", f"read-without-deferral false positive: {stderr!r}"


def test_deferral_without_read_no_advisory(tmp_path: Path) -> None:
    """Deferral phrasing with no read-only command named → no advisory."""
    events = [
        mk_user("다음?"),
        mk_assistant("다음 단계를 진행할까요? 설계 방향을 먼저 정해야 합니다."),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", f"deferral-without-read false positive: {stderr!r}"


# ---------------------------------------------------------------------------
# Cross-sentence false-positive class (issue #641 review — MEDIUM)
# A declarative read REPORT closing with a generic courtesy phrase, or a
# deferral about the *next* step, splits read and deferral across sentences and
# must NOT warn. Requiring same-sentence co-occurrence removes this FP class.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # read report (sentence 1) + generic courtesy close (sentence 2)
        "Ran `git status` — clean. Let me know if you want anything else.",
        "I checked `kubectl get pods`, all running. Let me know if you need more detail.",
        "`git diff` 로 확인한 변경은 3개 파일입니다. 더 필요하시면 알려주세요.",
        # read report (sentence 1) + deferral about the NEXT step (sentence 2)
        "`SELECT count(*) FROM orders` returned 1024. Should I proceed with the next step?",
        "`git status` 결과는 clean 입니다. 다음 단계를 진행할까요?",
    ],
)
def test_cross_sentence_read_and_deferral_no_advisory(text: str, tmp_path: Path) -> None:
    """Read and deferral in different sentences → not an offer → no advisory."""
    events = [mk_user("상태 보고"), mk_assistant(text)]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", f"cross-sentence false positive: {text!r}; stdout={stdout!r}"


# ---------------------------------------------------------------------------
# Project-specific Signal A extension via env
# ---------------------------------------------------------------------------


def test_signals_env_extends_read_detection(tmp_path: Path) -> None:
    """A project read-only CLI named only in the offer is detected via SIGNALS env."""
    events = [
        mk_user("확인?"),
        mk_assistant("`mycli inspect orders` 로 확인할까요?"),
    ]
    tp = write_jsonl(events, tmp_path)
    # Without the env var, no built-in pattern matches → no advisory.
    stdout_off, _, _ = run_hook(tp)
    assert PREFIX not in _msg(stdout_off), f"unexpected match w/o env: {stdout_off!r}"
    # With the env var, the project read CLI matches → advisory.
    stdout_on, _, rc = run_hook(
        tp, env_extra={"PRAXIS_READONLY_VERIFY_SIGNALS": r"\bmycli\s+inspect\b"}
    )
    assert rc == 0
    assert PREFIX in _msg(stdout_on), f"SIGNALS env did not extend detection; stdout={stdout_on!r}"


# ---------------------------------------------------------------------------
# Env modes — STRICT / BYPASS
# ---------------------------------------------------------------------------


def test_strict_mode_blocks(tmp_path: Path) -> None:
    """STRICT=1 escalates a real offer to a {decision: block} JSON (blocks Stop)."""
    events = [
        mk_user("확인"),
        mk_assistant("should I run `SELECT 1 FROM dual` to verify?"),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp, env_extra={"PRAXIS_READONLY_VERIFY_STRICT": "1"})
    assert rc == 0, f"STRICT must exit 0 (block is carried by JSON); got {rc}"
    assert stderr == "", f"strict path must keep stderr empty; got {stderr!r}"
    decision = json.loads(stdout)
    assert decision["decision"] == "block"
    assert PREFIX in decision["reason"]


def test_bypass_silences(tmp_path: Path) -> None:
    """BYPASS=1 silences the hook even on a real offer."""
    events = [
        mk_user("확인"),
        mk_assistant("should I run `SELECT 1 FROM dual` to verify?"),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp, env_extra={"PRAXIS_READONLY_VERIFY_BYPASS": "1"})
    assert rc == 0
    assert stdout == "" and stderr == "", f"BYPASS must silence; stdout={stdout!r}"


# ---------------------------------------------------------------------------
# Fail-safe paths
# ---------------------------------------------------------------------------


def test_failsafe_malformed_json() -> None:
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
    events = [
        mk_user("확인"),
        mk_assistant("should I run `SELECT 1 FROM dual`?"),
    ]
    tp = write_jsonl(events, tmp_path)
    _, stderr, rc = run_hook(tp, stop_hook_active=True)
    assert rc == 0
    assert stderr == ""


def test_failsafe_missing_transcript() -> None:
    _, stderr, rc = run_hook("/nonexistent/path.jsonl")
    assert rc == 0
    assert stderr == ""


def test_failsafe_empty_transcript(tmp_path: Path) -> None:
    tp = str(tmp_path / "empty.jsonl")
    Path(tp).write_text("")
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == ""
    assert stderr == ""


# ---------------------------------------------------------------------------
# Internal unit tests for helper functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("SELECT id FROM t", True),
        ("show tables", True),
        ("describe users", True),
        ("kubectl get pods", True),
        ("git status", True),
        ("aws s3 list-buckets", True),
        ("gh pr view 1", True),
        ("run with --dry-run", True),
        ("just refactor the function", False),
        ("update the documentation", False),
    ],
)
def test_has_read_signal(text: str, expected: bool) -> None:
    assert rvg._has_read_signal(text, None) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("should I check this", True),
        ("want me to run it", True),
        ("shall I query the table", True),
        ("would you like me to verify", True),
        ("let me know if you need it", True),
        ("진행할까요?", True),
        ("확인할까요?", True),
        ("원하시면 알려주세요", True),
        ("here is the result", False),
        ("I ran the query", False),
    ],
)
def test_has_deferral(text: str, expected: bool) -> None:
    assert rvg._has_deferral(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("git push origin main", True),
        ("gh pr merge 1", True),
        ("kubectl delete pod x", True),
        ("INSERT INTO t VALUES (1)", True),
        ("DELETE FROM t", True),
        ("curl -X POST https://api/x", True),
        ("배포를 진행", True),
        ("머지하겠습니다", True),
        # prose lowercase should NOT count as mutation
        ("update the doc and post a note", False),
        ("git status", False),
        ("SELECT id FROM t", False),
    ],
)
def test_has_mutation(text: str, expected: bool) -> None:
    assert rvg._has_mutation(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("should I run `SELECT 1 FROM t`?", True),
        ("`git status` 확인할까요?", True),
        # read + deferral but mutation present → not an offer to suppress-warn
        ("should I `SELECT` then DELETE?", False),
        # read but no deferral
        ("`SELECT 1 FROM t` returned 1 row.", False),
        # deferral but no read
        ("should I redesign the schema?", False),
        # read + deferral + already-ran cue in same sentence → already executed
        ("I ran `git status` — want me to show the diff?", False),
        ("`SELECT 1 FROM t` 를 실행했는데 결과를 확인할까요?", False),
    ],
)
def test_is_readonly_offer(text: str, expected: bool) -> None:
    assert rvg._is_readonly_offer(text, None) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("I ran the query", True),
        ("executed it", True),
        ("checked the pods", True),
        ("`SELECT` 를 실행했고", True),
        ("조회한 결과", True),
        ("확인했습니다", True),
        # future / gerund forms must NOT count as already-ran
        ("should I run it", False),
        ("re-run with --dry-run", False),
        ("running the query now", False),
        ("확인할까요", False),
        ("조회할까요", False),
    ],
)
def test_has_already_ran_cue(text: str, expected: bool) -> None:
    assert rvg._has_already_ran_cue(text) == expected


def test_fail_open_decorator_applied() -> None:
    """main() opts into the shared @fail_open guard."""
    assert getattr(rvg.main, "__wrapped__", None) is not None, (
        "main is not @fail_open-wrapped"
    )
