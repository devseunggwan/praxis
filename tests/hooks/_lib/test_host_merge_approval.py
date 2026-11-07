"""Host approval provider for the merge gates (issue #1639).

Runs both real gate impls as hook processes against a stand-in provider
executable and transcripts from `tests/fixtures/merge_gate_fixtures.py`. Every
fixture's transcript path alone denies the merge (the human's answer was
relayed by the host, not typed), so a pass can only come from the provider.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "hooks" / "_lib"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures"))

import _host_merge_approval as host  # noqa: E402
import merge_gate_fixtures as fx  # noqa: E402

MOMENTUM = REPO_ROOT / "hooks" / "advisory-nudge" / "momentum-rule-retrieval-gate" / "impl.py"
PRE_MERGE = REPO_ROOT / "hooks" / "preflight-gate" / "pre-merge-approval-gate" / "impl.py"
MERGE = "gh pr merge 999 --repo o/r --squash"
BRIEFING = fx.section("o/r#999") + "\nApprove merge of #999?"

_PROVIDER = """#!/usr/bin/env python3
import json, os, sys, time
spec = json.load(open(os.environ["FAKE_PROVIDER_SPEC"]))
with open(os.environ["FAKE_PROVIDER_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
time.sleep(spec.get("sleep", 0))
sys.stdout.write(spec.get("stdout", ""))
sys.exit(spec.get("exit", 0))
"""


def _record(**over) -> dict:
    rec = {"approved": True, "ref": "approval-1@abc", "repo": "o/r", "pr": 999,
           "briefing": BRIEFING, "approved_at": time.time() - 60}
    rec.update(over)
    return rec


@pytest.fixture
def env(tmp_path):
    provider = tmp_path / "provider"
    provider.write_text(_PROVIDER)
    provider.chmod(0o755)
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("PRAXIS_MOMENTUM", "PRAXIS_STATE_DIR", "PRAXIS_MERGE_APPROVAL"))}
    base.update(PRAXIS_HOME=str(tmp_path / "home"),
                PRAXIS_MERGE_APPROVAL_PROVIDER=str(provider),
                FAKE_PROVIDER_SPEC=str(tmp_path / "spec.json"),
                FAKE_PROVIDER_LOG=str(tmp_path / "calls.log"))
    return base


def _provider(env: dict, record: dict | None = None, *, stdout: str | None = None,
              code: int = 0, sleep: float = 0) -> None:
    out = stdout if stdout is not None else json.dumps(record if record is not None else _record())
    Path(env["FAKE_PROVIDER_SPEC"]).write_text(json.dumps({"stdout": out, "exit": code, "sleep": sleep}))


def _calls(env: dict) -> list[list[str]]:
    log = Path(env["FAKE_PROVIDER_LOG"])
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _transcript(tmp_path: Path, case: str = "momentum-merge-host-relayed.jsonl") -> Path:
    return fx.write(tmp_path, case, fx.CASES[case]())


def _run(hook: Path, env: dict, transcript: Path, command: str = MERGE,
         tool_use_id: str = "t1") -> str:
    payload = {"tool_name": "Bash", "tool_input": {"command": command},
               "transcript_path": str(transcript), "session_id": "s1",
               "tool_use_id": tool_use_id, "hook_event_name": "PreToolUse"}
    proc = subprocess.run([sys.executable, str(hook)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _decision(out: str) -> str | None:
    if not out.strip():
        return None
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"]


def _both(env, transcript, command=MERGE, tool_use_id="t1", order=(PRE_MERGE, MOMENTUM)):
    return {hook.parent.name: _run(hook, env, transcript, command, tool_use_id) for hook in order}


@pytest.mark.parametrize("order", [(PRE_MERGE, MOMENTUM), (MOMENTUM, PRE_MERGE)],
                         ids=["manifest-order", "reverse-order"])
def test_valid_record_releases_both_gates_with_one_provider_call(env, tmp_path, order):
    _provider(env)
    out = _both(env, _transcript(tmp_path), order=order)
    assert _decision(out["momentum-rule-retrieval-gate"]) is None
    assert _decision(out["pre-merge-approval-gate"]) is None
    assert _calls(env) == [["merge-approval", "--repo", "o/r", "--pr", "999", "--session", "s1"]]


def test_without_provider_the_relayed_answer_is_denied(env, tmp_path):
    """Control: the fixture's transcript path alone stops the merge."""
    _provider(env)
    del env["PRAXIS_MERGE_APPROVAL_PROVIDER"]
    out = _both(env, _transcript(tmp_path))
    assert _decision(out["momentum-rule-retrieval-gate"]) == "deny"
    assert _decision(out["pre-merge-approval-gate"]) == "ask"
    assert "host approval provider" not in json.dumps(out)
    assert _calls(env) == []


@pytest.mark.parametrize("record, why", [
    (_record(pr=1000), "names PR 1000"),
    (_record(repo="o/other"), "names repo 'o/other'"),
    (_record(briefing="- What changed: x\n- Verified: y\n- Risk: z"), "fewer than 4"),
    (_record(approved=False), "no approval"),
    (_record(approved="true"), "no approval"),
    (_record(pr="999"), "names PR '999'"),
    (_record(ref=""), "has no ref"),
    (_record(approved_at=time.time() - 40 * 24 * 3600), "older than 30 days"),
    (_record(approved_at=None), "no approved_at"),
])
def test_record_that_does_not_match_the_merge_is_refused(env, tmp_path, record, why):
    _provider(env, record)
    out = _both(env, _transcript(tmp_path))
    assert _decision(out["momentum-rule-retrieval-gate"]) == "deny"
    assert _decision(out["pre-merge-approval-gate"]) == "ask"
    for text in out.values():
        assert "did not release this merge" in text and why in text
    assert len(_calls(env)) == 1


@pytest.mark.parametrize("stdout, code, why", [
    (None, 3, "exited 3"),
    ("not json", 0, "not one JSON object"),
    ("[1, 2]", 0, "not one JSON object"),
    (json.dumps(_record()) + "\n" + json.dumps(_record()), 0, "not one JSON object"),
])
def test_provider_failure_is_refused(env, tmp_path, stdout, code, why):
    _provider(env, stdout=stdout, code=code)
    out = _both(env, _transcript(tmp_path))
    assert _decision(out["momentum-rule-retrieval-gate"]) == "deny"
    assert _decision(out["pre-merge-approval-gate"]) == "ask"
    assert why in out["momentum-rule-retrieval-gate"]


@pytest.mark.parametrize("command, why", [
    ("gh pr merge 999 --repo o/r --squash && gh pr merge 1000 --repo o/r --squash",
     "not one merge"),
    ("for n in 999; do gh pr merge $n --repo o/r; done", "not one merge"),
    ("cd ../other && gh pr merge 999 --repo o/r --squash", "not one merge"),
    ("gh pr merge 999 --squash", "does not name its repo"),
    ("gh pr merge --squash", "not one merge"),
])
def test_merge_without_one_bound_target_never_calls_the_provider(env, tmp_path, command, why):
    _provider(env)
    out = _run(MOMENTUM, env, _transcript(tmp_path), command)
    assert _decision(out) == "deny" and why in out
    assert _calls(env) == []


def test_pull_url_names_the_repo(env, tmp_path):
    _provider(env)
    out = _run(MOMENTUM, env, _transcript(tmp_path), "gh pr merge https://github.com/o/r/pull/999 --squash")
    assert _decision(out) is None
    assert _calls(env)[0][1:5] == ["--repo", "o/r", "--pr", "999"]


@pytest.mark.parametrize("path", ["provider-missing", "relative/provider"])
def test_unusable_provider_path_is_refused(env, tmp_path, path):
    env["PRAXIS_MERGE_APPROVAL_PROVIDER"] = str(tmp_path / path) if path == "provider-missing" else path
    out = _run(MOMENTUM, env, _transcript(tmp_path))
    assert _decision(out) == "deny" and "not an absolute path to an executable" in out


def test_inline_env_prefix_does_not_reach_the_hook(env, tmp_path):
    _provider(env)
    provider = env.pop("PRAXIS_MERGE_APPROVAL_PROVIDER")
    out = _run(MOMENTUM, env, _transcript(tmp_path), f"PRAXIS_MERGE_APPROVAL_PROVIDER={provider} {MERGE}")
    assert _decision(out) == "deny"
    assert _calls(env) == []


def test_missing_tool_use_id_is_refused(env, tmp_path):
    _provider(env)
    out = _run(MOMENTUM, env, _transcript(tmp_path), tool_use_id="")
    assert _decision(out) == "deny" and "no session_id or tool_use_id" in out
    assert _calls(env) == []


def test_one_approval_releases_one_merge(env, tmp_path):
    _provider(env)
    transcript = _transcript(tmp_path)
    assert _decision(_run(MOMENTUM, env, transcript, tool_use_id="t1")) is None
    again = _run(MOMENTUM, env, transcript, tool_use_id="t2")
    assert _decision(again) == "deny" and "already released another merge" in again


def test_ref_spent_on_a_merge_that_ran_stays_spent(env, tmp_path):
    _provider(env)
    first = _transcript(tmp_path, "momentum-merge-host-relayed.jsonl")
    assert _decision(_run(MOMENTUM, env, first, tool_use_id="m1")) is None
    later = _transcript(tmp_path, "momentum-merge-host-relayed-merged.jsonl")
    assert _decision(_run(MOMENTUM, env, later, tool_use_id="t2")) == "deny"


def test_ref_spent_on_a_merge_that_failed_is_freed(env, tmp_path):
    """A sibling gate blocked the released call, so nothing merged: the retry
    releases on the same approval."""
    _provider(env)
    first = _transcript(tmp_path, "momentum-merge-host-relayed.jsonl")
    assert _decision(_run(MOMENTUM, env, first, tool_use_id="m1")) is None
    later = _transcript(tmp_path, "momentum-merge-host-relayed-failed-merge.jsonl")
    assert _decision(_run(MOMENTUM, env, later, tool_use_id="t2")) is None
    third = _run(MOMENTUM, env, later, tool_use_id="t3")
    assert _decision(third) == "deny", "the retry spent it again"


def test_failed_call_frees_the_ref_only_in_its_own_session(env, tmp_path):
    _provider(env)
    transcript = _transcript(tmp_path, "momentum-merge-host-relayed-failed-merge.jsonl")
    decide = _decide_in_process(env)
    assert decide(session_id="other", tool_use_id="m1", transcript=transcript).released
    assert not decide(session_id="s1", tool_use_id="t2", transcript=transcript).released


def _decide_in_process(env):
    """`host.decide` with the test's environment, as the gates call it."""
    def call(*, session_id, tool_use_id, transcript, pr="999", repo="o/r"):
        entries = [json.loads(line) for line in Path(transcript).read_text().splitlines()]
        failed = {b["tool_use_id"] for e in entries for b in (e["message"]["content"]
                  if isinstance(e["message"]["content"], list) else [])
                  if b.get("type") == "tool_result" and b.get("is_error")}
        with _environ(env):
            return host.decide(session_id=session_id, tool_use_id=tool_use_id, repo=repo, pr=pr,
                               refusal=None, count_items=lambda t: 6 if "Approve" in t else 0,
                               min_items=4, call_failed=lambda tid: tid in failed)
    return call


@contextlib.contextmanager
def _environ(env):
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_provider_timeout_is_refused(env, tmp_path, monkeypatch):
    monkeypatch.setattr(host, "PROVIDER_TIMEOUT_SEC", 0.5)
    _provider(env, sleep=3)
    decision = _decide_in_process(env)(session_id="s1", tool_use_id="t1",
                                       transcript=_transcript(tmp_path))
    assert not decision.released and "did not answer within 0.5 s" in decision.detail


def test_unlocked_state_never_releases(env, tmp_path, monkeypatch):
    @contextlib.contextmanager
    def no_lock(path, timeout=None):
        yield False

    monkeypatch.setattr(host, "state_lock", no_lock)
    _provider(env)
    decision = _decide_in_process(env)(session_id="s1", tool_use_id="t1",
                                       transcript=_transcript(tmp_path))
    assert not decision.released and "lock was not acquired" in decision.detail
    assert _calls(env) == []


def test_refusal_is_recorded_once_per_call(env, tmp_path):
    """The second gate reads the first gate's outcome, refusals included, so a
    provider that answers differently on a second call cannot split the gates."""
    _provider(env, code=1)
    transcript = _transcript(tmp_path)
    assert _decision(_run(PRE_MERGE, env, transcript)) == "ask"
    _provider(env)
    assert _decision(_run(MOMENTUM, env, transcript)) == "deny"
    assert len(_calls(env)) == 1
