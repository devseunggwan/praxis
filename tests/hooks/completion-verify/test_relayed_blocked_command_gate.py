"""Tests for the relayed-blocked-command-gate Stop hook."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HOOK = REPO / "hooks" / "completion-verify" / "relayed-blocked-command-gate" / "impl.py"


def _load():
    sys.path.insert(0, str(REPO / "hooks" / "_lib"))
    spec = importlib.util.spec_from_file_location("relayed_blocked_command_gate", HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load()

_BYPASS_ENV = "PRAXIS_RELAYED_BLOCK_BYPASS"
BLOCKED = 'git log --oneline -2 && git push origin main 2>&1; echo "rc=$?"; git status -sb | head -1'

# The message this gate was built from, trimmed to the lines that matter.
INCIDENT = """`git push origin main` 이 PreToolUse 훅 `branch-push-guard` 에 차단됐습니다.

선택지는 두 가지입니다.
1. **직접 push (추천)**: 아래 명령을 프롬프트에 입력하시면 됩니다.
   ```
   ! git -C /repo/schema push origin main
   ```
2. **PR 로 올리기**: 브랜치를 새로 만들어 PR 로 올립니다.
"""


def _sigs(command=BLOCKED):
    return gate.blocked_signatures(command)


# --------------------------------------------------------------------------
# signatures: what part of the blocked command has to reappear
# --------------------------------------------------------------------------


def test_only_the_mutating_segment_is_kept():
    assert _sigs() == [["git", "push", "origin", "main"]]


def test_flag_and_its_argument_drop_out():
    assert gate.signature(["git", "-C", "/x", "push", "origin", "main"]) == [
        "git", "push", "origin", "main",
    ]


def test_env_assignment_and_run_prefix_drop_out():
    assert gate.signature(["!", "FOO=1", "gh", "pr", "merge", "7"]) == ["gh", "pr", "merge", "7"]


def test_readonly_block_gives_no_signature():
    assert _sigs("git status -sb && git log -1") == []


def test_single_token_segment_is_too_short():
    assert _sigs("make") == []


# --------------------------------------------------------------------------
# relays: must block
# --------------------------------------------------------------------------


def test_incident_shape_is_caught():
    assert gate.relays(INCIDENT, _sigs()) == "! git -C /repo/schema push origin main"


def test_fenced_command_with_korean_run_phrase():
    msg = "직접 실행해 주세요.\n```\ngit push origin main\n```\n"
    assert gate.relays(msg, _sigs())


def test_inline_code_with_english_run_phrase():
    msg = "You can run `git push origin main` from your terminal to finish."
    assert gate.relays(msg, _sigs())


def test_run_prefix_alone_frames_the_line():
    msg = "다음 명령이면 됩니다:\n```\n! git push origin main\n```"
    assert gate.relays(msg, _sigs())


def test_dollar_prompt_and_list_marker_are_stripped():
    msg = "Please run:\n- `$ git push origin main`"
    assert gate.relays(msg, _sigs())


# --------------------------------------------------------------------------
# relays: must not block
# --------------------------------------------------------------------------


def test_reporting_the_block_is_not_a_relay():
    msg = "`git push origin main` 이 branch-push-guard 에 차단됐습니다. PR 로 올리겠습니다."
    assert gate.relays(msg, _sigs()) is None


def test_negated_run_phrase_is_not_a_frame():
    msg = "`git push origin main` 은 직접 실행하지 않고 PR 로 올리겠습니다."
    assert gate.relays(msg, _sigs()) is None


OFFERING_HOOK = "BLOCKED. Bypass (if truly needed): PRAXIS_SKIP_PUSH_CHECK=1 with a reason"
RELAYED_OFFER = "훅이 안내한 우회입니다. 직접 실행해 주세요:\n```\n! PRAXIS_SKIP_PUSH_CHECK=1 git push origin main\n```"


def test_env_var_the_hook_offered_is_sanctioned():
    offered = gate.offered_env_names([OFFERING_HOOK])
    assert offered == {"PRAXIS_SKIP_PUSH_CHECK"}
    assert gate.relays(RELAYED_OFFER, _sigs(), offered) is None


def test_env_var_the_hook_did_not_offer_still_relays():
    offered = gate.offered_env_names(["BLOCKED: push to main is not allowed."])
    assert gate.relays(RELAYED_OFFER, _sigs(), offered)


def test_agent_narrating_its_own_run_is_not_a_frame():
    msg = "훅 판정 함수를 제 명령에 직접 실행해 body 가 0바이트인 것을 확인했습니다. `git push origin main` 은 막혔습니다."
    assert gate.relays(msg, _sigs()) is None


def test_a_different_branch_is_a_different_command():
    msg = "직접 실행해 주세요:\n```\n! git push origin maintenance\n```"
    assert gate.relays(msg, _sigs()) is None


def test_readonly_segment_of_the_block_may_be_proposed():
    msg = "직접 실행해 주세요:\n```\n! git log --oneline -2\n```"
    assert gate.relays(msg, _sigs()) is None


def test_command_outside_code_is_not_matched():
    msg = "직접 실행해 주세요: git push origin main"
    assert gate.relays(msg, _sigs()) is None


def test_frame_far_from_the_code_line_does_not_count():
    msg = "직접 실행해 주세요.\n\n\n\n\n`git push origin main` 은 막혔습니다."
    assert gate.relays(msg, _sigs()) is None


@pytest.mark.parametrize("sigs,msg", [([], INCIDENT), (_sigs(), ""), (_sigs(), None)])
def test_empty_inputs(sigs, msg):
    assert gate.relays(msg, sigs) is None


# --------------------------------------------------------------------------
# turn scoping and denial kind
# --------------------------------------------------------------------------


def _call(tid, command=BLOCKED, tool="Bash"):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": tool, "input": {"command": command}}]}}


def _result(tid, text, kind="permission-rule", is_error=True):
    ev = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": is_error, "content": text}]}}
    if kind:
        ev["toolDenialKind"] = kind
    return ev


HOOK_REFUSAL = "PreToolUse:Bash hook error: guard"


def test_only_hook_blocks_of_bash_calls_count():
    turn = [
        _call("toolu_1"), _result("toolu_1", HOOK_REFUSAL),
        _call("toolu_2"), _result("toolu_2", "The user doesn't want to proceed", kind="user-rejected"),
        _call("toolu_3", tool="Edit"), _result("toolu_3", HOOK_REFUSAL),
        _call("toolu_4"), _result("toolu_4", "ok", kind=None, is_error=False),
    ]
    assert gate.turn_blocked_commands(turn) == [(BLOCKED, HOOK_REFUSAL)]


def test_settings_permission_rule_refusal_is_not_a_hook_block():
    turn = [_call("toolu_1"),
            _result("toolu_1", f"Permission to use Bash with command {BLOCKED} has been denied.")]
    assert gate.turn_blocked_commands(turn) == []


# --------------------------------------------------------------------------
# end-to-end through the real binary
# --------------------------------------------------------------------------


def _transcript(tmp_path, final_text, kind="permission-rule", result=None):
    tid = "toolu_x"
    if result is None:
        result = ("PreToolUse:Bash hook error: BRANCH PUSH GUARD"
                  if kind == "permission-rule"
                  else "The user doesn't want to proceed with this tool use.")
    lines = [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": BLOCKED}}]}},
        {"type": "user", "toolDenialKind": kind, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "is_error": True,
             "content": result}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": final_text}]}},
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


def _run(tmp_path, transcript, env=None, **extra):
    e = dict(os.environ)
    e.pop(_BYPASS_ENV, None)
    e["PRAXIS_FIRE_TELEMETRY_FILE"] = str(tmp_path / "fires.jsonl")
    e["TMPDIR"] = str(tmp_path)
    e.update(env or {})
    payload = {"session_id": "s1", "transcript_path": str(transcript),
               "stop_hook_active": False, **extra}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=e, cwd=str(REPO))


def test_e2e_incident_blocks(tmp_path):
    proc = _run(tmp_path, _transcript(tmp_path, INCIDENT))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["decision"] == "block"
    assert "spec.md" in out["reason"]


def test_e2e_fires_on_a_transcript_past_the_rejection_scan_bound(tmp_path):
    path = _transcript(tmp_path, INCIDENT)
    body = path.read_text()
    filler = json.dumps({"type": "assistant", "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "x" * 1_000_000}]}}) + "\n"
    prompt = json.dumps({"type": "user", "message": {"role": "user", "content": "push it"}}) + "\n"
    path.write_text(filler * 21 + prompt + body)
    proc = _run(tmp_path, path)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["decision"] == "block"


def test_e2e_report_only_is_silent(tmp_path):
    msg = "`git push origin main` 이 훅에 차단됐습니다. PR 로 올리겠습니다."
    proc = _run(tmp_path, _transcript(tmp_path, msg))
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_e2e_user_refusal_is_not_this_gate(tmp_path):
    proc = _run(tmp_path, _transcript(tmp_path, INCIDENT, kind="user-rejected"))
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_e2e_settings_rule_refusal_is_silent(tmp_path):
    refusal = f"Permission to use Bash with command {BLOCKED} has been denied."
    proc = _run(tmp_path, _transcript(tmp_path, INCIDENT, result=refusal))
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_e2e_relaying_the_offered_env_var_is_silent(tmp_path):
    proc = _run(tmp_path, _transcript(tmp_path, RELAYED_OFFER, result=OFFERING_HOOK))
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_e2e_bypass_is_silent(tmp_path):
    proc = _run(tmp_path, _transcript(tmp_path, INCIDENT), env={_BYPASS_ENV: "1"})
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_e2e_stop_hook_active_is_silent(tmp_path):
    proc = _run(tmp_path, _transcript(tmp_path, INCIDENT), stop_hook_active=True)
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_e2e_malformed_payload_fails_open():
    proc = subprocess.run([sys.executable, str(HOOK)], input="not json",
                          capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 0
