"""Tests for the denied-action-report-gate Stop hook (issue #1392)."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HOOK = REPO / "hooks" / "completion-verify" / "denied-action-report-gate" / "impl.py"


def _load():
    sys.path.insert(0, str(REPO / "hooks" / "_lib"))
    spec = importlib.util.spec_from_file_location("denied_action_report_gate", HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load()

_STRICT_ENV = "PRAXIS_DENIED_ACTION_STRICT"
_BYPASS_ENV = "PRAXIS_DENIED_ACTION_BYPASS"


def _rej(tool_use_id="toolu_1", tool_name="Bash", text="git push origin main"):
    return {"tool_use_id": tool_use_id, "tool_name": tool_name, "text": text}


def _turn(*tool_use_ids, is_error=True):
    return [
        {
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tid, "is_error": is_error}
                    for tid in tool_use_ids
                ],
            }
        }
    ]


# --------------------------------------------------------------------------
# turn scoping — the whole reason a session-wide scan is usable at all
# --------------------------------------------------------------------------


def test_turn_tool_use_ids_collects_result_ids():
    assert gate.turn_tool_use_ids(_turn("a", "b")) == {"a", "b"}


def test_turn_tool_use_ids_tolerates_junk_events():
    turn = [None, {}, {"message": "a string"}, {"message": {"content": "not a list"}}]
    assert gate.turn_tool_use_ids(turn) == set()


def test_turn_tool_use_ids_empty_turn():
    assert gate.turn_tool_use_ids([]) == set()
    assert gate.turn_tool_use_ids(None) == set()


def test_rejection_from_an_earlier_turn_is_not_reported():
    """The cursored scan accumulates session-wide; only the intersection scopes it."""
    old = _rej(tool_use_id="toolu_old")
    assert gate.unreported([old], {"toolu_now"}, "보고합니다.") == []


def test_rejection_from_this_turn_is_reported():
    now = _rej(tool_use_id="toolu_now")
    assert gate.unreported([now], {"toolu_now"}, "보고합니다.") == [now]


# --------------------------------------------------------------------------
# regression: the two designs falsified by corpus measurement
# --------------------------------------------------------------------------


def test_regression_askuserquestion_rejection_is_excluded():
    """7 of 14 refusals in the measured corpus were AskUserQuestion — the user
    dismissing a menu and answering in their own words, not a denied action."""
    r = _rej(tool_name="AskUserQuestion", text="어느 쪽으로 진행할까요?")
    assert gate.unreported([r], {"toolu_1"}, "그럼 그렇게 하겠습니다.") == []


def test_regression_identifier_overlap_must_not_clear():
    """The discarded design cleared on incidental token overlap between the
    refused command and unrelated prose."""
    r = _rej(text="git push origin main")
    msg = "main 브랜치 구조를 먼저 살펴보겠습니다."
    assert gate.is_acknowledged(r, msg) is False


def test_regression_korean_acknowledgement_without_identifier_clears():
    """The discarded design missed every report that owned the refusal in plain
    Korean without repeating an identifier."""
    r = _rej(text="git push origin main")
    assert gate.is_acknowledged(r, "매핑 표 추가를 취소하고 정리하겠습니다.") is True


# --------------------------------------------------------------------------
# acknowledgement axis
# --------------------------------------------------------------------------


def test_silent_report_is_not_acknowledgement():
    assert gate.is_acknowledged(_rej(), "11건 실패가 있습니다. 원인을 봅니다.") is False


def test_empty_report_is_not_acknowledgement():
    assert gate.is_acknowledged(_rej(), "") is False


def test_korean_refusal_vocabulary_clears():
    for word in ["거부", "거절", "차단", "반려", "중단", "철회", "보류", "미승인"]:
        assert gate.is_acknowledged(_rej(), f"푸시는 {word}되었습니다.") is True, word


def test_english_refusal_vocabulary_clears():
    for word in ["denied", "rejected", "blocked", "refused", "declined", "cancelled"]:
        assert gate.is_acknowledged(_rej(), f"The push was {word}.") is True, word


def test_english_vocabulary_is_word_bounded():
    """Substring matching would clear on unrelated words — `stopped` must not be
    matched inside `unstoppable`, nor `deny` inside `denylist`."""
    assert gate.is_acknowledged(_rej(), "The denylist is unstoppable.") is False


def test_tool_name_mention_clears():
    assert gate.is_acknowledged(_rej(tool_name="Bash"), "Bash 호출은 남겨둡니다.") is True


def test_tool_name_mention_is_word_bounded():
    r = _rej(tool_name="Read")
    assert gate.is_acknowledged(r, "Readme 를 갱신했습니다.") is False


def test_unresolvable_tool_name_never_clears_on_name():
    assert gate.is_acknowledged(_rej(tool_name=""), "무언가 했습니다.") is False


# --------------------------------------------------------------------------
# indeterminate branch (#1231)
# --------------------------------------------------------------------------


def test_turn_has_error_result_true():
    assert gate.turn_has_error_result(_turn("a", is_error=True)) is True


def test_turn_has_error_result_false_on_clean_results():
    assert gate.turn_has_error_result(_turn("a", is_error=False)) is False


def test_turn_has_error_result_false_on_empty_turn():
    assert gate.turn_has_error_result([]) is False


def test_indeterminate_message_says_it_is_not_an_absence():
    text = gate._indeterminate()
    assert "#1231" in text
    assert "indeterminate" in text.lower()


# --------------------------------------------------------------------------
# emitted bodies
# --------------------------------------------------------------------------


def test_advisory_names_every_refused_tool():
    text = gate._advisory([_rej(tool_name="Bash"), _rej(tool_name="Write")])
    assert "Bash" in text and "Write" in text
    assert "2 tool call(s)" in text


def test_advisory_leads_with_english():
    for text in (gate._advisory([_rej()]), gate._indeterminate()):
        first = text.splitlines()[0]
        assert first[0].isascii() and first[0].isalpha()


def test_bodies_reference_the_spec():
    assert "spec.md" in gate._advisory([_rej()])
    assert "spec.md" in gate._indeterminate()


# --------------------------------------------------------------------------
# end-to-end through the real binary
# --------------------------------------------------------------------------


def _run(payload, env=None, tmp_path=None):
    e = dict(os.environ)
    # Drop the gate's own knobs before applying `env`. Measured: with STRICT=1
    # inherited the advisory test fails on the missing `systemMessage` key, and
    # with BYPASS=1 that test and the strict one both fail on empty stdout — so
    # the leak turns working code red rather than hiding a defect. Unreproducible
    # failures cost a debugging session either way.
    e.pop(_STRICT_ENV, None)
    e.pop(_BYPASS_ENV, None)
    if tmp_path is not None:
        e["PRAXIS_FIRE_TELEMETRY_FILE"] = str(tmp_path / "fires.jsonl")
    e.update(env or {})
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=e,
        cwd=str(REPO),
    )


def _transcript_with_rejection(tmp_path, tool_use_id="toolu_x"):
    """A minimal transcript carrying the three co-agreeing refusal markers."""
    path = tmp_path / "t.jsonl"
    lines = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": "Bash",
                        "input": {"command": "git push origin main"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "toolDenialKind": "user-rejected",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "is_error": True,
                        "content": "The user doesn't want to proceed with this tool use.",
                    }
                ],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


def test_e2e_malformed_payload_fails_open():
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input="not json", capture_output=True, text=True
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_e2e_stop_hook_active_is_silent(tmp_path):
    t = _transcript_with_rejection(tmp_path)
    proc = _run(
        {
            "session_id": "s1",
            "stop_hook_active": True,
            "transcript_path": str(t),
            "last_assistant_message": "완료했습니다.",
        },
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_e2e_missing_transcript_is_silent(tmp_path):
    proc = _run(
        {
            "session_id": "s2",
            "stop_hook_active": False,
            "transcript_path": str(tmp_path / "nope.jsonl"),
            "last_assistant_message": "완료했습니다.",
        },
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_e2e_bypass_is_silent(tmp_path):
    t = _transcript_with_rejection(tmp_path)
    proc = _run(
        {
            "session_id": "s3",
            "stop_hook_active": False,
            "transcript_path": str(t),
            "last_assistant_message": "완료했습니다.",
        },
        env={"PRAXIS_DENIED_ACTION_BYPASS": "1"},
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_e2e_acknowledged_report_is_silent(tmp_path):
    """The negative control: the same transcript, a report that owns the refusal."""
    t = _transcript_with_rejection(tmp_path)
    proc = _run(
        {
            "session_id": "s4",
            "stop_hook_active": False,
            "transcript_path": str(t),
            "last_assistant_message": "푸시는 거부되어 로컬에만 남아 있습니다.",
        },
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_e2e_advisory_fires_on_a_silent_report(tmp_path):
    t = _transcript_with_rejection(tmp_path)
    proc = _run(
        {
            "session_id": "s5",
            "stop_hook_active": False,
            "transcript_path": str(t),
            "last_assistant_message": "11건 실패가 있습니다. 원인을 봅니다.",
        },
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert "systemMessage" in out
    assert "Bash" in out["systemMessage"]
    # The tier is part of the claim: a default run that blocked would also carry
    # a body, so asserting the body alone cannot tell advisory from block.
    assert out.get("decision") != "block"


def test_e2e_strict_escalates_to_block(tmp_path):
    """Same transcript and same payload as the advisory case; only the env moves."""
    t = _transcript_with_rejection(tmp_path)
    proc = _run(
        {
            "session_id": "s6",
            "stop_hook_active": False,
            "transcript_path": str(t),
            "last_assistant_message": "11건 실패가 있습니다. 원인을 봅니다.",
        },
        env={"PRAXIS_DENIED_ACTION_STRICT": "1"},
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["decision"] == "block"


def test_e2e_askuserquestion_refusal_does_not_fire(tmp_path):
    """The exclusion, end to end: half the measured corpus takes this path."""
    path = tmp_path / "ask.jsonl"
    lines = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_q",
                        "name": "AskUserQuestion",
                        "input": {"questions": [{"question": "어느 쪽으로 갈까요?"}]},
                    }
                ],
            },
        },
        {
            "type": "user",
            "toolDenialKind": "user-rejected",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_q",
                        "is_error": True,
                        "content": "The user doesn't want to proceed with this tool use.",
                    }
                ],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    proc = _run(
        {
            "session_id": "s7",
            "stop_hook_active": False,
            "transcript_path": str(path),
            "last_assistant_message": "그럼 그렇게 진행하겠습니다.",
        },
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# --------------------------------------------------------------------------
# acknowledgement is scoped to each refusal (CodeRabbit finding on PR #1395)
# --------------------------------------------------------------------------


def test_one_ack_word_does_not_clear_a_second_unnamed_refusal():
    """A refusal word has one referent. With two refusals in a turn, naming one
    says nothing about the other — the earlier global match retired both."""
    bash = _rej(tool_use_id="t1", tool_name="Bash")
    write = _rej(tool_use_id="t2", tool_name="Write", text="/etc/hosts")
    out = gate.unreported([bash, write], {"t1", "t2"}, "Bash 호출은 거부되어 실행하지 못했습니다.")
    assert [r["tool_name"] for r in out] == ["Write"]


def test_two_refusals_both_named_clears_both():
    bash = _rej(tool_use_id="t1", tool_name="Bash")
    write = _rej(tool_use_id="t2", tool_name="Write", text="/etc/hosts")
    assert gate.unreported([bash, write], {"t1", "t2"}, "Bash 와 Write 호출이 모두 막혔습니다.") == []


def test_two_refusals_neither_named_reports_both():
    bash = _rej(tool_use_id="t1", tool_name="Bash")
    write = _rej(tool_use_id="t2", tool_name="Write", text="/etc/hosts")
    out = gate.unreported([bash, write], {"t1", "t2"}, "푸시는 거부되어 로컬에만 남아 있습니다.")
    assert sorted(r["tool_name"] for r in out) == ["Bash", "Write"]


def test_sole_refusal_still_clears_on_a_bare_ack_word():
    """The single-refusal path is unchanged — that is every turn in the measured
    corpus, so the fix must not move the firing rate."""
    assert gate.unreported([_rej(tool_use_id="t1")], {"t1"}, "푸시는 거부되었습니다.") == []


def test_excluded_tool_does_not_count_toward_soleness():
    """A refused AskUserQuestion is out of scope, so it must not silently turn a
    sole real refusal into a multi-refusal turn and suppress the ack word."""
    ask = _rej(tool_use_id="t0", tool_name="AskUserQuestion", text="어느 쪽으로?")
    bash = _rej(tool_use_id="t1", tool_name="Bash")
    assert gate.unreported([ask, bash], {"t0", "t1"}, "푸시는 거부되었습니다.") == []


# --------------------------------------------------------------------------
# the indeterminate branch through main(), not just the message builder
# --------------------------------------------------------------------------


def _drive_main(monkeypatch, capsys, payload, scan_result, turn):
    """Run main() in-process with the scan's answer forced.

    Producing a real INDETERMINATE end to end needs a transcript that grows past
    the 20 MB budget between two Stop calls; building one in a fixture would add
    a 20 MB write to a suite already near its CI ceiling. Forcing the scan's
    return value exercises the same decision path in main() — which is the half
    that was untested — while the scan's own None contract is `_transcript`'s.
    """
    monkeypatch.setattr(gate, "scan_user_rejections", lambda *a, **k: scan_result)
    monkeypatch.setattr(gate, "load_stop_turn", lambda *a, **k: turn)
    monkeypatch.setattr(gate, "resolve_stop_transcript", lambda p: (__file__, False))
    monkeypatch.setattr(gate.sys, "stdin", io.StringIO(json.dumps(payload)))
    for var in (_STRICT_ENV, _BYPASS_ENV):
        monkeypatch.delenv(var, raising=False)
    rc = gate.main()
    return rc, capsys.readouterr().out


def test_indeterminate_reaches_the_output_when_the_turn_has_an_error(monkeypatch, capsys):
    rc, out = _drive_main(
        monkeypatch, capsys,
        {"session_id": "i1", "stop_hook_active": False, "last_assistant_message": "완료."},
        None, _turn("a", is_error=True),
    )
    assert rc == 0
    assert "#1231" in json.loads(out)["systemMessage"]


def test_indeterminate_is_silent_when_the_turn_has_no_error(monkeypatch, capsys):
    """The gate on the indeterminate branch: a scan that could not catch up must
    not speak on turns that plainly had nothing to refuse."""
    rc, out = _drive_main(
        monkeypatch, capsys,
        {"session_id": "i2", "stop_hook_active": False, "last_assistant_message": "완료."},
        None, _turn("a", is_error=False),
    )
    assert rc == 0
    assert out.strip() == ""


# --------------------------------------------------------------------------
# the SubagentStop registration, through the shipped dispatcher
# --------------------------------------------------------------------------


def test_subagent_stop_reaches_the_gate_through_the_dispatcher(tmp_path):
    """Run the wrapper the plugin ships, not the impl directly.

    The manifest entry and the impl can both be correct while the hook never
    runs on `SubagentStop` — host filtering or the dispatch group could drop it,
    and calling `impl.py` by path would leave that unpinned. `PRAXIS_HOME` and
    the telemetry file are redirected because this runs the whole group, so
    every member's state write lands in the tmp tree.
    """
    dispatcher = REPO / "hooks" / "_dispatch.sh"
    assert dispatcher.is_file()
    t = _transcript_with_rejection(tmp_path)
    e = dict(os.environ)
    e.pop(_STRICT_ENV, None)
    e.pop(_BYPASS_ENV, None)
    e["PRAXIS_HOME"] = str(tmp_path / "praxis")
    e["PRAXIS_FIRE_TELEMETRY_FILE"] = str(tmp_path / "fires.jsonl")
    proc = subprocess.run(
        [str(dispatcher), "SubagentStop", "-", "claude"],
        input=json.dumps(
            {
                "session_id": "sa1",
                "stop_hook_active": False,
                "transcript_path": str(t),
                "last_assistant_message": "11건 실패가 있습니다. 원인을 봅니다.",
            }
        ),
        capture_output=True,
        text=True,
        env=e,
        cwd=str(REPO),
    )
    assert proc.returncode == 0
    assert "denied-action-report-gate" in proc.stdout
    assert "Bash" in proc.stdout
