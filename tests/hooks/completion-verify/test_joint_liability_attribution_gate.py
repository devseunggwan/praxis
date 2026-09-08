"""Unit tests for hooks/completion-verify/joint-liability-attribution-gate — Issue #1391.

Every case below is an entry from the input-surface enumeration run before the
hook was written (`praxis:surface-enumeration`, mandatory for a classifier):

  First-paragraph resolution: heading-first, fenced-code-first, CRLF separator,
    leading blank lines, single paragraph, heading-only, empty
  Axis conjunction: both axes, subject only, non-ownership only, English form
  Position: the same sentence later in the message must not fire
  Clear: routing question in the most recent user message
  Regression: the two designs falsified against the motivating paragraph
  Env modes: STRICT -> block, BYPASS -> silent
  Fail-open: malformed payload, stop_hook_active, absent transcript
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = (
    REPO_ROOT
    / "hooks"
    / "completion-verify"
    / "joint-liability-attribution-gate"
    / "impl.py"
)

spec = importlib.util.spec_from_file_location("joint_liability_attribution_gate", HOOK_PATH)
assert spec is not None and spec.loader is not None
jlag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jlag)

# The paragraph that motivated the hook, verbatim from the session transcript.
MOTIVATING = (
    "이 세션에는 제가 한 작업 기록이 없어서, 어떤 건을 말씀하시는지 먼저 "
    "확인이 필요합니다. 다만 다른 세션 소행이든 제 소행이든 처리는 여기서 "
    "하겠습니다(구체적으로 지목해 주시면 바로 고칩니다)."
)


def fires(text: str) -> bool:
    return jlag.is_opening_attribution(jlag.first_paragraph(text))


# --- the motivating case, and the two designs it falsified -----------------


def test_motivating_paragraph_fires():
    """The whole point: the violation that produced this hook must be caught."""
    assert fires(MOTIVATING)


def test_regression_forbidden_phrase_list_would_have_missed_it():
    """Design 1 was falsified here — the rule's printed examples do not match."""
    printed_examples = [
        "제 세션에서 한 작업이 아닙니다",
        "다른 세션이 한 일입니다",
        "그 워크트리는 제 담당이 아닙니다",
        "that was the other agent",
    ]
    assert not any(p in MOTIVATING for p in printed_examples)
    assert "제 세션" not in MOTIVATING
    assert fires(MOTIVATING)  # the shipped conjunction catches it anyway


def test_regression_fix_vocabulary_must_not_clear():
    """Design 2 was falsified here — the violating paragraph contains '처리'."""
    assert "처리" in MOTIVATING
    assert fires(MOTIVATING)


# --- first-paragraph resolution --------------------------------------------


@pytest.mark.parametrize(
    "prefix",
    [
        "### 상태\n\n",  # ATX heading block
        "```\nfoo\n```\n\n",  # fenced code
        "\n\n\n",  # leading blank lines
        "### 상태\r\n\r\n",  # CRLF separator
    ],
    ids=["heading", "fence", "blank-lines", "crlf"],
)
def test_opening_block_skips_non_prose(prefix):
    assert fires(prefix + MOTIVATING)


@pytest.mark.parametrize(
    "text", ["", "   \n\n  ", "### 끝", "```\n제 세션이 아닙니다\n```"],
    ids=["empty", "whitespace", "heading-only", "fence-only"],
)
def test_no_opening_prose_is_a_pass(text):
    assert not fires(text)


# --- axis conjunction -------------------------------------------------------


def test_subject_without_disown_passes():
    assert not fires("이 세션에서 훅 3개를 추가했습니다.")


def test_disown_without_subject_passes():
    assert not fires("해당 컬럼은 테이블에 없습니다.")


def test_english_form_fires():
    assert fires("That was the other agent, not mine.")


# --- position is the discriminator -----------------------------------------


def test_same_sentence_later_in_the_message_passes():
    """The rule permits attribution as a routing fact once the report has started."""
    text = "원인은 캐시 무효화 누락입니다.\n\n" + MOTIVATING
    assert not fires(text)


# --- the clear --------------------------------------------------------------


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("이거 누가 한 작업이야?", True),
        ("어느 세션에서 나온 거야", True),
        ("who did this?", True),
        ("이거 고쳐줘", False),
        ("", False),
        (None, False),
    ],
)
def test_routing_clear(msg, expected):
    assert jlag.user_asked_for_routing(msg) is expected


# --- end-to-end: tiers, bypass, fail-open ----------------------------------


def run_hook(payload: str, env_extra: dict[str, str] | None = None, tmp=None) -> tuple[int, str]:
    import os

    env = dict(os.environ)
    # Never let a test write the operational ledger.
    env["PRAXIS_FIRE_TELEMETRY_FILE"] = str(
        (tmp or Path("/tmp")) / "jlag-test-ledger.jsonl"
    )
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout


def payload_for(text: str, stop_hook_active: bool = False) -> str:
    return json.dumps(
        {
            "session_id": "test-session",
            "stop_hook_active": stop_hook_active,
            "last_assistant_message": text,
        }
    )


def test_default_tier_is_advisory(tmp_path):
    code, out = run_hook(payload_for(MOTIVATING), tmp=tmp_path)
    assert code == 0
    assert json.loads(out)["systemMessage"]


def test_strict_env_escalates_to_block(tmp_path):
    code, out = run_hook(
        payload_for(MOTIVATING), {"PRAXIS_JOINT_LIABILITY_STRICT": "1"}, tmp=tmp_path
    )
    assert code == 0
    assert json.loads(out)["decision"] == "block"


def test_bypass_env_is_silent(tmp_path):
    code, out = run_hook(
        payload_for(MOTIVATING), {"PRAXIS_JOINT_LIABILITY_BYPASS": "1"}, tmp=tmp_path
    )
    assert (code, out.strip()) == (0, "")


def test_stop_hook_active_does_not_refire(tmp_path):
    code, out = run_hook(payload_for(MOTIVATING, stop_hook_active=True), tmp=tmp_path)
    assert (code, out.strip()) == (0, "")


def test_clean_report_is_silent(tmp_path):
    code, out = run_hook(
        payload_for("원인은 캐시 무효화 누락입니다. 수정해서 푸시했습니다."), tmp=tmp_path
    )
    assert (code, out.strip()) == (0, "")


@pytest.mark.parametrize("bad", ["not json", "", "[]", "null"])
def test_malformed_payload_fails_open(bad, tmp_path):
    code, out = run_hook(bad, tmp=tmp_path)
    assert (code, out.strip()) == (0, "")


def test_advisory_body_leads_with_english(tmp_path):
    """DESIGN.md: an emitted body starts in English, Korean after a newline.

    The repo gate (tests/test_emit_english_lead.py) keys on the literal's first
    character, not on the whole line being ASCII — an em dash inside an English
    sentence is fine. Assert the contract, not something stricter than it.
    """
    _, out = run_hook(payload_for(MOTIVATING), tmp=tmp_path)
    lines = json.loads(out)["systemMessage"].splitlines()
    assert lines[0][0].isascii() and lines[0][0].isalpha()
