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
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Load the hook module directly (not via subprocess for unit coverage)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "completion-verify" / "completion-signal-gate" / "impl.py"

spec = importlib.util.spec_from_file_location("completion_signal_gate", HOOK_PATH)
assert spec is not None and spec.loader is not None
csg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csg)  # type: ignore[union-attr]


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
    assert stderr == "", f"advisory hook must keep stderr empty; got: {stderr!r}"
    assert "[praxis:completion-signal-gate]" in _msg(stdout), (
        f"advisory not emitted for phrase {phrase!r}; stdout={stdout!r}"
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
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", (
        f"must not trigger when Bash tool is present; stdout={stdout!r}"
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
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", (
        f"must not trigger when Read tool is present; stdout={stdout!r}"
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
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", (
        f"must not trigger with cited output; stdout={stdout!r}"
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
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", f"FP1 false-positive: {stderr!r}"


def test_fp2_no_completion_phrase(tmp_path: Path) -> None:
    """Response without any completion phrase — must NOT trigger. (FP2)"""
    events = [
        mk_user("어떻게 진행할까요?"),
        mk_assistant("다음 단계는 PR 생성입니다. 브랜치를 먼저 확인하겠습니다."),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", f"FP2 false-positive: {stderr!r}"


def test_fp3_read_tool_with_completion_phrase(tmp_path: Path) -> None:
    """Completion phrase + Read tool → suppressed. (FP3)"""
    read_use = mk_read_use("fp3r", "/Users/jane/projects/praxis/hooks/hooks.json")
    result = mk_tool_result("fp3r", '{"hooks": {...}}')
    events = [
        mk_user("hooks.json 확인해주세요"),
        mk_assistant("읽겠습니다.", [read_use]),
        result,
        mk_assistant("hooks.json 확인 완료. 이상 없음."),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", f"FP3 false-positive: {stderr!r}"


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
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", f"FP4 false-positive: {stderr!r}"


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
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", f"FP5 false-positive: {stderr!r}"


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
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stderr == "", f"firing path must keep stderr empty; got {stderr!r}"
    assert "[praxis:completion-signal-gate]" in _msg(stdout), (
        f"Event 1 quote must trigger advisory; stdout={stdout!r}"
    )


# ---------------------------------------------------------------------------
# Rule 1b — GO verdict + unresolved-gap co-occurence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase,gap",
    [
        ("보내도 됩니다", "미해소"),
        ("문제 없음", "검증 증거 부재"),
        ("ready to merge", "not verified"),
    ],
)
def test_rule1b_triggers_on_go_verdict_and_gap(
    phrase: str, gap: str, tmp_path: Path
) -> None:
    """Rule 1b should fire when GO phrase and gap marker coexist in one turn."""
    events = [
        mk_user("PR 상태를 판단해 주세요"),
        mk_assistant(f"최종 판단: {phrase}. 현재는 {gap}가 남았습니다."),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stderr == "", f"advisory hook must keep stderr empty; got {stderr!r}"
    msg = _msg(stdout)
    assert "go-verdict phrase detected together with unresolved-gap marker" in msg, (
        f"Rule 1b did not fire for {phrase!r}/{gap!r}; stdout={stdout!r}"
    )


def test_rule1_suppressed_when_evidence_present_despite_go_verdict(tmp_path: Path) -> None:
    """Rule 1 should be suppressed by cited evidence even with a GO phrase."""
    events = [
        mk_user("결과 점검해주세요"),
        mk_assistant(
            "ready to merge\n"
            "검증 결과:\n"
            "$ pytest tests/ → 2 passed"
        ),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stderr == "", f"hook must keep stderr empty; got {stderr!r}"
    assert stdout == "", f"evidence must suppress Rule 1 when present; stdout={stdout!r}"


@pytest.mark.parametrize(
    "verdict",
    [
        "not ready to merge",
        "머지 가능하지 않습니다",
        "문제 없음이 아닙니다",
    ],
)
def test_rule1b_ignores_negated_go_verdicts(verdict: str, tmp_path: Path) -> None:
    """A negated GO phrase reports the gap; it does not override it."""
    events = [
        mk_user("머지해도 되는지 판단해주세요"),
        mk_assistant(f"{verdict} — 미해소 항목이 남아 있습니다."),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stderr == "", f"hook must keep stderr empty; got {stderr!r}"
    assert "go-verdict phrase detected" not in _msg(stdout), (
        f"negated verdict {verdict!r} must not fire Rule 1b; stdout={stdout!r}"
    )


@pytest.mark.parametrize(
    "turn",
    [
        # A gap marker reported as resolved is not a gap.
        "미해소 항목 없음. 문제 없음",
        "검증 갭 없음. 머지 가능",
    ],
)
def test_rule1b_ignores_resolved_gap_reports(turn: str, tmp_path: Path) -> None:
    """`미해소 항목 없음` states the absence of a gap, not a gap."""
    events = [mk_user("머지해도 되는지 판단해주세요"), mk_assistant(turn)]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stderr == "", f"hook must keep stderr empty; got {stderr!r}"
    assert "go-verdict phrase detected" not in _msg(stdout), (
        f"resolved-gap report {turn!r} must not fire Rule 1b; stdout={stdout!r}"
    )


@pytest.mark.parametrize(
    "turn",
    [
        # Korean negation trailing an unrelated clause, not the English verdict.
        "ready to merge — 아직 실환경은 unverified",
        # English negation leading an unrelated clause, not the Korean verdict.
        "No blockers. 머지 가능 — 실환경 unverified",
    ],
)
def test_rule1b_guard_does_not_cross_languages(turn: str, tmp_path: Path) -> None:
    """A negation in the other language must not silence a real contradiction."""
    events = [mk_user("머지해도 되는지 판단해주세요"), mk_assistant(turn)]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stderr == "", f"hook must keep stderr empty; got {stderr!r}"
    assert "go-verdict phrase detected together with unresolved-gap marker" in _msg(
        stdout
    ), f"{turn!r} is a real contradiction and must fire Rule 1b; stdout={stdout!r}"


def test_rule1b_uppercase_approve_fires(tmp_path: Path) -> None:
    """`APPROVE 가능` is the same verdict as `approve 가능`."""
    events = [
        mk_user("머지해도 되는지 판단해주세요"),
        mk_assistant("APPROVE 가능 — 미해소 항목 있음"),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stderr == "", f"hook must keep stderr empty; got {stderr!r}"
    assert "go-verdict phrase detected together with unresolved-gap marker" in _msg(
        stdout
    ), f"uppercase APPROVE must fire Rule 1b; stdout={stdout!r}"


def test_rule1b_fires_even_with_cited_evidence(tmp_path: Path) -> None:
    """Evidence answers 'was anything verified', not 'is the gap resolved'."""
    events = [
        mk_user("머지해도 되는지 판단해주세요"),
        mk_assistant(
            "ready to merge\n"
            "$ pytest tests/ → 2 passed\n"
            "다만 실환경 전송은 not verified 상태입니다."
        ),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stderr == "", f"hook must keep stderr empty; got {stderr!r}"
    assert "go-verdict phrase detected together with unresolved-gap marker" in _msg(
        stdout
    ), f"Rule 1b must fire despite evidence; stdout={stdout!r}"


def test_rule1b_only_gap_without_go_does_not_fire(tmp_path: Path) -> None:
    """Gap marker alone must not trigger Rule 1b."""
    events = [
        mk_user("테스트 결과를 정리해줘"),
        mk_assistant("현재 미해소 항목은 1개입니다."),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stderr == "", f"should remain silent; got {stderr!r}"
    assert stdout == "", f"gap-only case should be silent; stdout={stdout!r}"


# ---------------------------------------------------------------------------
# Rule 1b — self-application (issue #845 probe, issue #916 risk item)
#
# These are CHARACTERIZATION tests: they fix what the hook does today when its
# own spec.md is the payload, not what it ought to do. The spec quotes every
# trigger token to document it, so a turn discussing this hook trips it — an
# inherent property of a lexical scanner, not a defect with a cheap fix.
#
# Do NOT "fix" this by adding a suppressor without first re-running it against
# the genuine fires. The obvious suppressor (skip when the gap is already
# graded `블로커 아님`/`BLOCKED`/`non-blocking`) was measured over the full local
# transcript corpus (54,086 turns, hook-faithful: last assistant message per
# turn, sidechains skipped): it removes 15 of the 41 fires and BOTH genuine
# fires die with them, because each reports its PR's own
# `mergeStateStatus: BLOCKED` in its body. Suppression is inverted here.
# ---------------------------------------------------------------------------

SPEC_PATH = HOOK_PATH.parent / "spec.md"

# Trigger tokens the spec quotes verbatim in its own documentation tables.
_SPEC_GO_PHRASES = (
    "보내도 됩니다",
    "보내도 좋습니다",
    "머지 가능",
    "approve 가능",
    "문제 없음",
    "ready to merge",
)
_SPEC_GAP_MARKERS = (
    "⚠",
    "미해소",
    "갭",
    "검증 증거 부재",
    "not verified",
    "unverified",
)


def _spec_text() -> str:
    return SPEC_PATH.read_text(encoding="utf-8")


def test_rule1b_self_application_own_spec_fires(tmp_path: Path) -> None:
    """The hook's own spec.md as payload DOES trip Rule 1b — measured, not desired.

    The sibling guard in test_write_decision_consistency_gate.sh asserts silence
    for that gate's spec; here the measured answer is the opposite, so the
    assertion follows the measurement rather than the sibling's direction.
    """
    events = [
        mk_user("이 훅 spec 을 검토해줘"),
        mk_assistant(_spec_text()),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stderr == "", f"hook must keep stderr empty; got {stderr!r}"
    assert "go-verdict phrase detected together with unresolved-gap marker" in _msg(
        stdout
    ), (
        "characterization: own spec.md trips Rule 1b because the spec quotes "
        f"every trigger token; stdout={stdout!r}"
    )


def test_rule1b_self_application_negative_control_go_removed(tmp_path: Path) -> None:
    """Negative control: neutralise only the GO phrases → silence.

    Distinguishes "fires because the GO tokens are present" from a detector
    that is simply stuck on. Without this, the test above passes for a Rule 1b
    that fires unconditionally.
    """
    text = _spec_text()
    for phrase in _SPEC_GO_PHRASES:
        text = text.replace(phrase, "<GO-PHRASE>")
    assert csg._has_unresolved_gap(text, text.lower()), (
        "control is only meaningful while the gap markers survive"
    )

    events = [mk_user("이 훅 spec 을 검토해줘"), mk_assistant(text)]
    tp = write_jsonl(events, tmp_path)
    stdout, _stderr, rc = run_hook(tp)
    assert rc == 0
    assert "go-verdict phrase detected together with unresolved-gap marker" not in _msg(
        stdout
    ), f"GO phrases removed → Rule 1b must fall silent; stdout={stdout!r}"


def test_rule1b_self_application_negative_control_gap_removed(tmp_path: Path) -> None:
    """Negative control: neutralise only the gap markers → silence."""
    text = _spec_text()
    for marker in _SPEC_GAP_MARKERS:
        text = text.replace(marker, "<GAP-MARKER>")
    assert not csg._has_unresolved_gap(text, text.lower()), (
        "control requires the gap markers to be gone"
    )

    events = [mk_user("이 훅 spec 을 검토해줘"), mk_assistant(text)]
    tp = write_jsonl(events, tmp_path)
    stdout, _stderr, rc = run_hook(tp)
    assert rc == 0
    assert "go-verdict phrase detected together with unresolved-gap marker" not in _msg(
        stdout
    ), f"gap markers removed → Rule 1b must fall silent; stdout={stdout!r}"


# ---------------------------------------------------------------------------
# Rule 1b — corpus fixtures (issue #845 post-hoc measurement)
#
# Every payload below is a real turn from the local transcript corpus, trimmed
# and with PR numbers / repo names / user ids replaced. They are kept as a pair:
# the shape the rule EXISTS for, and the shape it fires on by construction.
# ---------------------------------------------------------------------------

# The genuine fire, and the reason Rule 1b was written: the verdict is handed
# down first and the unresolved gap trails it, so the verdict is not
# conditioned on the gap. The very next turn in that session retracted it.
_CORPUS_UNGRADED_VERDICT = """확인 완료했습니다. 스레드 맥락과 PR #NNNN 을 코드 레벨까지 검증한 결과입니다.

## PR #NNNN 검증 결과

**상태**: OPEN / MERGEABLE / `BLOCKED`(= REVIEW_REQUIRED, 승인만 없음)

## 판단: 리뷰어에게 보내도 됩니다. 단, 아래 1건은 헤드라인으로 남습니다

**⚠️ 미해소 갭 — 데이터 동일성 검증 증거 부재**
스레드에서 리뷰어가 명시적으로 요청한 건 *"마이그레이션 하고 기존 데이터와
동일한지"* 인데, mart 데이터 대조는 0건입니다.
"""

# Graded-gap shape: the verdict is GO and the gap is explicitly filed as
# non-blocking. Rule 1b fires anyway. This is characterization, not approval —
# the suppressor that would silence it takes the genuine fire above with it
# (see the header comment on the self-application block).
_CORPUS_GRADED_GAP = """## 검증 결과 — PR #NNNN (HEAD `0000000`)

### 결론: 머지 가능. 다만 이슈 완료조건 중 manager 경로 한 갈래가 미커버입니다.

### 남은 갭 (머지 차단 아님, 확인 범위: 변경 파일 2개 + 전체 consumer grep)

**① manager 경로의 error payload는 여전히 조용히 빈 결과 (확인: 코드)**
"""

# Same class, different session: the gap is parked under an explicit
# "미검증으로 남는 것" heading below a GO verdict.
_CORPUS_GRADED_GAP_TRAILING = """PR #NN 검토 결과입니다.

### 결론

머지 가능합니다. 변경은 4파일 86/-5 로 최소이고, CI 2개 job 전부 pass 입니다.

### 미검증으로 남는 것

앵커의 `unverified` 가 이미 짚은 preview 실적재 외에, 카탈로그 연동 경로는
CI 에서 전부 mock 이라 런타임 영향이 확인되지 않았습니다.
"""


def _fires(payload: str, tmp_path: Path) -> bool:
    events = [mk_user("PR 검토해줘"), mk_assistant(payload)]
    stdout, stderr, rc = run_hook(write_jsonl(events, tmp_path))
    assert rc == 0, f"hook must exit 0; rc={rc}"
    assert stderr == "", f"hook must keep stderr empty; got {stderr!r}"
    return "go-verdict phrase detected together with unresolved-gap marker" in _msg(
        stdout
    )


# The second genuine fire, graded a weak positive by the 2026-08-03 probe. The
# verdict is scoped to runtime impact while the merge-approval requirement sits
# unverified in its own section — the gap the verdict does not account for is a
# different gap from the one the turn headlines.
#
# Note which token actually trips the rule: `미검증` is NOT a gap marker, so the
# fire comes from the `⚠️` in the headline. The turn is a genuine instance on
# content grounds and a marginal one on lexical grounds, which is exactly what
# "weak" means here — keep the `⚠️` when editing this fixture or it stops
# firing for a reason that has nothing to do with the shape being pinned.
_CORPUS_WEAK_GENUINE = """## PR #NNNN merge-ready 준비 완료 — 그러나 헤드라인 발견 2건

### ⚠️ 헤드라인 1: 이 PR 의 실질 기능이 이미 중복입니다

동일한 unique constraint 가 이미 다른 PR 로 머지돼 있습니다.

### 미검증 항목

- `mergeStateStatus` 는 `BLOCKED` 입니다(`mergeable: MERGEABLE`, CI 는 전부
  green). Copilot 이 자동으로 코멘트를 달 때까지는 실제 승인 요건 충족 여부를
  제가 확인할 수 없습니다.

### 리스크 / 권고

- 지금 상태로 머지해도 **런타임 영향은 없습니다**(모델 선언만, 기존 테이블
  미변경). 안전하게 머지 가능한 상태입니다.
"""


@pytest.mark.parametrize(
    "payload",
    [_CORPUS_UNGRADED_VERDICT, _CORPUS_WEAK_GENUINE],
    ids=["unambiguous", "weak-scoped-verdict"],
)
def test_rule1b_genuine_fires_are_retained(payload: str, tmp_path: Path) -> None:
    """Regression guard for the two fires Rule 1b was built to catch.

    Any change to the Rule 1b predicate must keep both firing. These are the
    genuine fires in the measured corpus — everything else the rule emits is a
    false positive, so a change that improves the fire count while dropping one
    of these has made the rule worse, not better.
    """
    assert _fires(payload, tmp_path), (
        "a GO verdict that does not account for its own unresolved gap is the "
        "rule's whole purpose"
    )


@pytest.mark.parametrize(
    "payload",
    [_CORPUS_GRADED_GAP, _CORPUS_GRADED_GAP_TRAILING],
    ids=["graded-non-blocking", "graded-trailing-section"],
)
def test_rule1b_graded_gap_still_fires(payload: str, tmp_path: Path) -> None:
    """Characterization: a GO verdict over an explicitly graded gap fires too.

    Both payloads are post-release fires and both are false positives — the
    author separated verdict from gap correctly. They are pinned as FIRING
    because the only suppressor that reaches them also kills the genuine fire
    above; changing this assertion to silence means that trade was accepted.
    """
    assert _fires(payload, tmp_path), (
        "graded gaps are not suppressed today; see the header comment for why"
    )


# A refusal opens the turn and the GO token appears 800 chars later as a table
# heading naming the attribute ("머지 가능 상태" = mergeability status). The
# predicate's KO guard only scans 12 chars after the match, so the refusal is
# structurally out of its reach.
_CORPUS_LEADING_REFUSAL = """아니요. 지금 내놓으면 안 됩니다.

### 결론

방금 재조회한 PR 상태 기준으로 **5건 중 4건이 `BLOCKED`** 입니다.

### 차단 요인 (피해 순)

**1. 숫자 정규화 갭 — 고객 데이터가 조용히 사라집니다 (미착수)**

**3. PR 머지 가능 상태 (방금 재조회)**

| PR | base | 상태 |
|---|---|---|
| BE #NNNN | dev | BLOCKED |
"""

# The retraction turn from the genuine fire's session: it quotes its own
# earlier "보내도 됩니다" in order to withdraw it. A mention, not a use.
_CORPUS_QUOTED_RETRACTION = """아니요, 솔직히 말하면 **"문제가 없다"를 검증한 게 아닙니다.**
제가 한 건 정적 코드 리뷰 + CI 확인까지이고, 그걸 "보내도 됩니다" 로 표현한
건 과했습니다.

## 검증 안 하고 가정으로 넘어간 것

리뷰어가 명시적으로 요청한 "기존 데이터와 동일한지" 는 전혀 안 봤고, 저도
갭으로 표시만 했습니다.
"""


@pytest.mark.parametrize(
    "payload",
    [_CORPUS_LEADING_REFUSAL, _CORPUS_QUOTED_RETRACTION],
    ids=["refusal-then-status-label", "refusal-then-quoted-retraction"],
)
def test_rule1b_leading_refusal_is_silent(payload: str, tmp_path: Path) -> None:
    """A turn that opens by refusing is a NO-GO report; Rule 1b must not fire."""
    assert not _fires(payload, tmp_path), (
        "a leading refusal makes every later GO token a label or a quotation"
    )


@pytest.mark.parametrize(
    "payload",
    [
        "No blockers. 머지 가능 — 실환경은 unverified 입니다.",
        "No further work needed. 머지 가능 하지만 미해소 갭 1건이 남습니다.",
        "ready to merge — 아직 실환경은 unverified 입니다.",
        "이 방법은 안 됩니다만 저 방법은 됩니다. 머지 가능합니다. 다만 미해소 갭 1건이 남습니다.",
        "리뷰어 승인이 필요한 건 아닙니다. 머지 가능합니다. 미해소 갭 1건.",
        "squash 는 안됩니다만 merge commit 은 됩니다. 머지 가능. 검증 증거 부재 1건.",
    ],
    ids=[
        "en-no-blockers",
        "en-no-further-work",
        "en-ready-to-merge",
        "ko-partial-negation-만",
        "ko-clause-negation-아닙니다",
        "ko-partial-negation-안됩니다",
    ],
)
def test_rule1b_partial_negation_is_not_a_refusal(payload: str, tmp_path: Path) -> None:
    """Positive control: a negation that scopes to one clause is not a refusal.

    Every payload here is a genuine GO-verdict-over-gap contradiction that
    happens to open with a negation word. Silencing any of them is a false
    NEGATIVE, and a false negative is invisible in a fire count — the corpus
    number keeps improving while the rule quietly stops working.

    Both directions are real, not hypothetical. `No blockers. 머지 가능 — 실환경
    unverified` was silenced twice: once by the round that shipped Rule 1b, and
    once by the first draft of the refusal guard (`^(no|nope|not yet)\\b`),
    which this control caught. The Korean side is symmetric — `안 됩니다` /
    `아닙니다` negate whatever clause they sit in, so an earlier draft that
    scanned 120 chars for them swallowed all three KO cases below.
    """
    assert _fires(payload, tmp_path), (
        "clause-scoped negation must not be read as a turn-level refusal"
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
    assert result.stderr == "", f"firing path must keep stderr empty; got {result.stderr!r}"
    msg = _msg(result.stdout)
    assert "[praxis:completion-signal-gate]" in msg, (
        f"Rule 2 must fire for foreign plugin command; stdout={result.stdout!r}"
    )
    # The {plugin} placeholder must render the real cwd plugin name, not the
    # literal '{praxis}' the missing f-prefix used to produce (#1097).
    assert "plugin is 'praxis'" in msg, (
        f"Rule 2 must render the real plugin name; stdout={result.stdout!r}"
    )
    assert "{praxis}" not in msg, (
        f"Rule 2 must not leak the literal placeholder; stdout={result.stdout!r}"
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
    assert result.stderr == "", f"firing path must keep stderr empty; got {result.stderr!r}"
    assert "[praxis:completion-signal-gate]" in _msg(result.stdout), (
        f"Rule 2 must fire for bare foreign skill `/release`; stdout={result.stdout!r}"
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
    assert result.stdout == "", (
        f"Rule 2 must NOT fire for unknown bare commands; stdout={result.stdout!r}"
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
    assert result.stdout == "", (
        f"Rule 2 must NOT fire in non-praxis cwd; stdout={result.stdout!r}"
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
    assert result.stdout == ""
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
    assert result.stdout == ""
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
    assert result.stdout == ""
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


# ---------------------------------------------------------------------------
# Defect 3 (issue #515) — negation / progressive completion phrasing must NOT
# count as a completion signal (negation/status-form rule).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # English negated / not-yet forms
        "not done yet",
        "this is not complete",
        "not ready to merge",
        "it isn't done",
        "the migration is not done",
        "still completing the work",  # progressive: "completing" != "complete"
        "we are not all set",
        # Korean negated / not-yet forms
        "완료되지 않았습니다",
        "완료 안 됨",
        "아직 완료 전입니다",
        "완료하지 못했습니다",
        "완료 안 됐어요",
    ],
)
def test_negated_completion_not_signal(text: str) -> None:
    """Negated/progressive completion phrasing → _has_completion_signal False."""
    assert csg._has_completion_signal(text) is False, (
        f"negated phrase {text!r} must NOT count as a completion signal"
    )


@pytest.mark.parametrize(
    "text",
    [
        # Genuine (un-negated) completion forms must still register.
        "done",
        "the implementation is complete",
        "ready to merge",
        "no fixes needed",
        "all set",
        "완료",
        "실질적 수정은 없습니다",
        "결함 없음",
        "이상 없음",
        "머지하셔도 됩니다",
    ],
)
def test_genuine_completion_still_signal(text: str) -> None:
    """Genuine completion phrasing must still register after negation guard."""
    assert csg._has_completion_signal(text) is True, (
        f"genuine completion {text!r} must still count as a completion signal"
    )


def test_negated_completion_no_advisory_end_to_end(tmp_path: Path) -> None:
    """End-to-end: a negated completion last turn → no Rule 1 advisory."""
    events = [
        mk_user("작업 상태 보고"),
        mk_assistant("아직 작업이 완료되지 않았습니다. 다음 단계가 남아 있습니다."),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", (
        f"negated completion must not trigger Rule 1 advisory; stdout={stdout!r}"
    )


def test_negated_completion_en_no_advisory_end_to_end(tmp_path: Path) -> None:
    """End-to-end (EN): 'not done yet' last turn → no Rule 1 advisory."""
    events = [
        mk_user("status?"),
        mk_assistant("The refactor is not done yet — two modules remain."),
    ]
    tp = write_jsonl(events, tmp_path)
    stdout, stderr, rc = run_hook(tp)
    assert rc == 0
    assert stdout == "", (
        f"negated EN completion must not trigger advisory; stdout={stdout!r}"
    )


def test_fail_open_decorator_applied() -> None:
    """main() opts into the shared @fail_open guard — assert decorator is present.

    Fail-open behaviour itself is covered in tests/test_hook_runtime.sh.
    """
    assert getattr(csg.main, "__wrapped__", None) is not None, (
        "main is not @fail_open-wrapped"
    )


# ---------------------------------------------------------------------------
# SubagentStop (issue #1337)
# ---------------------------------------------------------------------------
#
# On SubagentStop the payload carries the PARENT session's transcript in
# `transcript_path` and the subagent's own in `agent_transcript_path`. Reading
# the wrong one does not error — it answers about the wrong conversation — so
# every case below makes the two transcripts disagree.


def _write_named(events: list[dict], tmp_path: Path, name: str) -> str:
    p = tmp_path / name
    with p.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return str(p)


def run_subagent_hook(
    main_path: str, agent_path: str, last_message: str | None = None
) -> tuple[str, str, int]:
    payload: dict[str, Any] = {
        "hook_event_name": "SubagentStop",
        "transcript_path": main_path,
        "agent_transcript_path": agent_path,
        "stop_hook_active": False,
        "agent_id": "def456",
        "agent_type": "Explore",
        "session_id": "test-subagent",
    }
    if last_message is not None:
        payload["last_assistant_message"] = last_message
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout, result.stderr, result.returncode


def _parent_with_evidence(tmp_path: Path) -> str:
    """A parent turn that clears the gate: a Bash call and a cited line."""
    return _write_named(
        [
            mk_user("delegate the check"),
            mk_assistant("running", [mk_bash_use("p1", "pytest tests/")]),
            mk_tool_result("p1", "9 passed"),
            mk_assistant("Parent output: 9 passed. Done."),
        ],
        tmp_path,
        "main.jsonl",
    )


def test_subagent_stop_reads_the_agent_transcript(tmp_path: Path) -> None:
    """The parent turn carries evidence and the subagent's does not; the
    advisory must be about the subagent."""
    main = _parent_with_evidence(tmp_path)
    agent = _write_named(
        [mk_user("check the config"), mk_assistant("All set.")],
        tmp_path,
        "agent.jsonl",
    )
    stdout, _stderr, rc = run_subagent_hook(main, agent)
    assert rc == 0
    assert "completion-signal-gate" in _msg(stdout)


def test_subagent_stop_clears_on_the_subagents_own_evidence(tmp_path: Path) -> None:
    """The mirror image: reading the parent's bare turn would advise on a
    subagent run that did verify."""
    main = _write_named(
        [mk_user("delegate"), mk_assistant("All set.")], tmp_path, "main.jsonl"
    )
    agent = _write_named(
        [
            mk_user("check the config"),
            mk_assistant("running", [mk_bash_use("a1", "pytest tests/unit")]),
            mk_tool_result("a1", "3 passed"),
            mk_assistant("Subagent output: 3 passed. Done."),
        ],
        tmp_path,
        "agent.jsonl",
    )
    stdout, _stderr, rc = run_subagent_hook(main, agent)
    assert rc == 0
    assert stdout.strip() == ""


def test_sidechain_marked_agent_transcript_is_still_read(tmp_path: Path) -> None:
    """Were the markers kept, the turn would come back empty and the gate
    would pass every subagent silently — a false clear, not a false fire."""
    main = _parent_with_evidence(tmp_path)
    events = [mk_user("check the config"), mk_assistant("All set.")]
    for ev in events:
        ev["isSidechain"] = True
    agent = _write_named(events, tmp_path, "agent.jsonl")
    stdout, _stderr, rc = run_subagent_hook(main, agent)
    assert rc == 0
    assert "completion-signal-gate" in _msg(stdout)


def test_payload_last_assistant_message_is_preferred(tmp_path: Path) -> None:
    """The transcript "may lag the in-memory conversation"; the payload field
    is the documented source for the final text."""
    main = _parent_with_evidence(tmp_path)
    agent = _write_named(
        [mk_user("check the config"), mk_assistant("Reviewed the config.")],
        tmp_path,
        "agent.jsonl",
    )
    stdout, _stderr, rc = run_subagent_hook(main, agent, last_message="All set.")
    assert rc == 0
    assert "completion-signal-gate" in _msg(stdout)


def test_unflushed_agent_transcript_does_not_grade_the_parent(tmp_path: Path) -> None:
    """Never the parent's turn. The parent here would trip the advisory, so a
    fallback emits a verdict about the wrong conversation."""
    main = _write_named(
        [mk_user("delegate"), mk_assistant("All set.")], tmp_path, "main.jsonl"
    )
    missing = str(tmp_path / "subagents" / "never-written.jsonl")
    stdout, _stderr, rc = run_subagent_hook(main, missing)
    assert rc == 0
    assert stdout.strip() == ""


def test_unflushed_agent_transcript_cannot_borrow_parent_evidence(
    tmp_path: Path,
) -> None:
    """The other direction the fallback failed in: the parent's Bash call and
    cited line would CLEAR a subagent claim carried by the payload, against
    evidence the subagent never produced (CodeRabbit on #1358)."""
    main = _parent_with_evidence(tmp_path)
    missing = str(tmp_path / "subagents" / "never-written.jsonl")
    stdout, _stderr, rc = run_subagent_hook(main, missing, last_message="All set.")
    assert rc == 0
    assert stdout.strip() == ""


def test_subagent_stop_without_the_key_still_refuses_the_parent(
    tmp_path: Path,
) -> None:
    main = _write_named(
        [mk_user("delegate"), mk_assistant("All set.")], tmp_path, "main.jsonl"
    )
    payload = json.dumps(
        {
            "hook_event_name": "SubagentStop",
            "transcript_path": main,
            "stop_hook_active": False,
            "session_id": "test-subagent-no-key",
        }
    )
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
