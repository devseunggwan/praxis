"""merge-gate entry point: routing by event and folding of its checks (issue #1650).

The checks themselves are covered by `test_merge_gate_<check>.sh`, which run
each module on its own. These tests run `impl.py` — in process, with the
checks it loads patched where the real one would shell out to `gh`/`git` — and
the real PreToolUse(Bash) dispatch group for the cross-hook surface pins the
momentum test file used to hold.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
import time
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_DIR = REPO_ROOT / "hooks" / "preflight-gate" / "merge-gate"
IMPL = GATE_DIR / "impl.py"
LIB = REPO_ROOT / "hooks" / "_lib"
sys.path.insert(0, str(LIB))

import _hook_runtime  # noqa: E402
import _hermes  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"
NO_BRIEFING = FIXTURES / "momentum-merge-marker-no-briefing.jsonl"
COMPLETE_BRIEFING = FIXTURES / "momentum-merge-complete.jsonl"


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """A fresh impl.py module with fresh check modules, isolated from the env."""
    for var in list(os.environ):
        if var.startswith(("PRAXIS_MOMENTUM", "PRAXIS_MERGE", "PRAXIS_PR_STATE",
                           "PRAXIS_HOOK_BYPASS_MERGE")):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PRAXIS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PRAXIS_MEMORY_DIR", str(FIXTURES / "momentum-memories"))
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_DISABLE", "1")
    for name in [m for m in sys.modules if m.startswith("_praxis_merge_gate_")]:
        monkeypatch.delitem(sys.modules, name)
    spec = importlib.util.spec_from_file_location("merge_gate_impl_under_test", IMPL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(gate, payload: dict, monkeypatch, capsys) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    rc = gate.main()
    out, err = capsys.readouterr()
    return rc, out, err


def _bash(command: str, transcript: Path | None = None, **extra) -> dict:
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}, "session_id": "s1", **extra}
    if transcript is not None:
        payload["transcript_path"] = str(transcript)
    return payload


def _decision(out: str) -> str | None:
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out.strip() else None


def _no_worktree_conflict(gate, monkeypatch):
    worktree = gate._load("worktree")
    monkeypatch.setattr(worktree, "_resolve_head_branch", lambda *a, **k: "feature")
    monkeypatch.setattr(worktree, "_worktree_branches", lambda *a, **k: {})


def _worktree_conflict(gate, monkeypatch):
    worktree = gate._load("worktree")
    monkeypatch.setattr(worktree, "_resolve_head_branch", lambda *a, **k: "feature")
    monkeypatch.setattr(worktree, "_worktree_branches", lambda *a, **k: {"feature": "/w/feature"})


# --- routing ------------------------------------------------------------------


@pytest.mark.parametrize("payload, checks", [
    (_bash("ls"), ("approval", "worktree", "briefing")),
    ({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"}, ("refetch", "menu")),
    ({"hook_event_name": "Stop"}, ("claim",)),
    ({"hook_event_name": "SubagentStop"}, ("claim",)),
    ({}, ("claim",)),  # a Stop payload from a host that sends no event name
    ({"hook_event_name": "PreToolUse", "tool_name": "Edit"}, ()),
])
def test_each_event_runs_its_own_checks(gate, payload, checks):
    assert gate._checks_for(payload) == checks


@pytest.mark.parametrize("host, runs_briefing", [
    (None, True), ("claude", True), ("codex", True), ("hermes", True),
    ("cursor", False), ("gemini", False),
])
def test_briefing_runs_only_on_the_hosts_momentum_ran_on(gate, host, runs_briefing):
    _hook_runtime.set_member_host(host)
    try:
        checks = gate._checks_for(_bash("gh pr merge 1"))
    finally:
        _hook_runtime.set_member_host(None)
    assert ("briefing" in checks) is runs_briefing
    assert checks[:2] == ("approval", "worktree")


def test_claim_never_reads_a_tool_call(gate, monkeypatch, capsys):
    called = []
    claim = gate._load("claim")
    monkeypatch.setattr(claim, "main", lambda: called.append(1) or 0)
    _call(gate, _bash("gh pr view 1"), monkeypatch, capsys)
    assert called == []


# --- folding on gh pr merge ---------------------------------------------------


def test_merge_without_transcript_asks_and_prints_the_rule_surface(gate, monkeypatch, capsys):
    """No transcript: the briefing check fails open, so the approval ask is the answer."""
    rc, out, err = _call(gate, _bash("gh pr merge 42 --squash"), monkeypatch, capsys)
    assert rc == 0
    assert _decision(out) == "ask"
    assert "TRIGGER: gh pr merge" in err


def test_missing_briefing_denies_over_the_ask(gate, monkeypatch, capsys):
    rc, out, err = _call(gate, _bash("gh pr merge 833 --squash", NO_BRIEFING), monkeypatch, capsys)
    assert _decision(out) == "deny"
    assert "Pre-Merge Reporting briefing" in json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "TRIGGER: gh pr merge" in err


def test_complete_briefing_leaves_the_approval_ask(gate, monkeypatch, capsys):
    """Control for the deny above: same flow, briefing present, the human still answers."""
    rc, out, _ = _call(gate, _bash("gh pr merge --squash", COMPLETE_BRIEFING), monkeypatch, capsys)
    assert _decision(out) == "ask"
    assert "GH PR MERGE APPROVAL" in json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def test_worktree_block_ends_the_run_before_the_briefing(gate, monkeypatch, capsys):
    _worktree_conflict(gate, monkeypatch)
    rc, out, err = _call(gate, _bash("gh pr merge 7 --squash --delete-branch", NO_BRIEFING),
                         monkeypatch, capsys)
    assert rc == 2
    assert "/w/feature" in err
    assert "TRIGGER: gh pr merge" not in err  # briefing never ran
    assert _decision(out) is None             # the approval ask is not the answer


def test_worktree_without_conflict_falls_through(gate, monkeypatch, capsys):
    _no_worktree_conflict(gate, monkeypatch)
    rc, out, _ = _call(gate, _bash("gh pr merge 7 --squash --delete-branch"), monkeypatch, capsys)
    assert rc == 0 and _decision(out) == "ask"


def test_non_merge_command_is_silent(gate, monkeypatch, capsys):
    rc, out, err = _call(gate, _bash("gh pr view 7"), monkeypatch, capsys)
    assert (rc, out, err) == (0, "", "")


def test_a_check_that_raises_fails_open_and_names_itself(gate, monkeypatch, capsys):
    approval = gate._load("approval")

    def boom() -> int:
        raise RuntimeError("broken check")

    monkeypatch.setattr(approval, "main", boom)
    rc, out, err = _call(gate, _bash("gh pr merge 42 --squash"), monkeypatch, capsys)
    assert rc == 0 and out == ""
    assert "[merge-gate] check failed open: RuntimeError: broken check" in err


def test_malformed_payload_is_a_pass(gate, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    assert gate.main() == 0
    assert capsys.readouterr() == ("", "")


# --- AskUserQuestion and Stop -------------------------------------------------


def test_ask_checks_both_run_and_keep_their_stderr(gate, monkeypatch, capsys):
    refetch, menu = gate._load("refetch"), gate._load("menu")
    monkeypatch.setattr(refetch, "main", lambda: sys.stderr.write("refetch says\n") and 0)
    monkeypatch.setattr(menu, "main", lambda: sys.stderr.write("menu says\n") and 0)
    rc, out, err = _call(gate, {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"},
                         monkeypatch, capsys)
    assert rc == 0 and out == ""
    assert err == "refetch says\nmenu says\n"


def test_strict_refetch_block_is_the_answer(gate, monkeypatch, capsys):
    refetch, menu = gate._load("refetch"), gate._load("menu")
    monkeypatch.setattr(refetch, "main", lambda: sys.stderr.write("stale PR\n") and 2)
    monkeypatch.setattr(menu, "main", lambda: sys.stderr.write("menu says\n") and 0)
    rc, _, err = _call(gate, {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"},
                       monkeypatch, capsys)
    assert rc == 2 and err == "stale PR\n"


def test_stop_claim_output_passes_through(gate, monkeypatch, capsys):
    claim = gate._load("claim")
    advisory = json.dumps({"systemMessage": "[merge-state-claim-gate] check the PR"})
    monkeypatch.setattr(claim, "main", lambda: sys.stdout.write(advisory + "\n") and 0)
    rc, out, _ = _call(gate, {"hook_event_name": "Stop"}, monkeypatch, capsys)
    assert rc == 0 and json.loads(out) == json.loads(advisory)


# --- fire telemetry -----------------------------------------------------------


def test_checks_record_no_fire_of_their_own(gate):
    """impl.py peels each check's @fail_open: the hook records one fire per call."""
    worktree = gate._load("worktree")
    assert getattr(worktree.main, "__wrapped__", None) is not None
    assert gate._run.__doc__ and "peeled" in gate._run.__doc__


# --- the real dispatch group (cross-hook surfaces) ----------------------------


def _group(command: str) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_input": {"command": command}, "session_id": "s1",
                          "cwd": str(REPO_ROOT)})
    env = {k: v for k, v in os.environ.items() if not k.startswith("PRAXIS_")}
    env.update(PRAXIS_MEMORY_DIR=str(FIXTURES / "momentum-memories"),
               PRAXIS_FIRE_TELEMETRY_DISABLE="1")
    script = ("import sys; sys.path.insert(0, sys.argv[1]); import _dispatch; "
              "sys.exit(_dispatch.run_group('PreToolUse', 'Bash', sys.stdin.read(), 'claude'))")
    return subprocess.run([sys.executable, "-c", script, str(LIB)], input=payload,
                          capture_output=True, text=True, env=env, timeout=60)


@pytest.mark.parametrize("command, surfaces", [
    ('gh pr merge --squash && cmux new-workspace --command "claude -p next"',
     ("TRIGGER: gh pr merge", "TRIGGER: cmux new-workspace")),
    ("git push --force && gh pr merge --squash",
     ("TRIGGER: git push --force", "TRIGGER: gh pr merge")),
])
def test_a_compound_command_shows_every_surface_in_one_group_run(command, surfaces):
    """Replaces the momentum test file's compound cases: the two surfaces now
    come from two hooks, and the group run is where they meet."""
    proc = _group(command)
    for surface in surfaces:
        assert surface in proc.stderr, (surface, proc.stderr[-2000:])


# --- Hermes -------------------------------------------------------------------


def test_hermes_treats_the_merge_gate_ask_as_human_consent():
    human, self_check = _hermes.split_asks([("merge-gate", "approve?"), ("side-effect-scan", "x")])
    assert human == [("merge-gate", "approve?")]
    assert self_check == [("side-effect-scan", "x")]


def test_only_the_approval_check_can_ask():
    """HUMAN_APPROVAL_HOOKS is keyed by hook name, so every ask merge-gate can
    emit becomes a human approval on Hermes. Only approval.py may ask."""
    emits_ask = re.compile(r'emit_decision\(\s*"ask"|emit_ask\(')
    askers = sorted(p.name for p in GATE_DIR.glob("*.py")
                    if emits_ask.search(p.read_text(encoding="utf-8")))
    assert askers == ["approval.py"], askers


# --- one host timeout for every check (issue #1650 review) --------------------


def test_hook_timeout_matches_the_manifest(gate):
    manifest = json.loads((REPO_ROOT / "hooks" / "manifest.json").read_text(encoding="utf-8"))
    entry = next(h for h in manifest["hooks"] if h["name"] == "merge-gate")
    assert gate.HOOK_TIMEOUT_SEC == entry["timeout"]


def _stub(bin_dir: Path, name: str, sleep_sec: float) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\nsleep {sleep_sec}\nexit 1\n")
    path.chmod(0o755)


def test_checks_share_one_deadline_under_slow_gh_and_git(gate, tmp_path, monkeypatch, capsys):
    """refetch (3 PRs x 2s gh) then menu (9 x 0.3s + 1.5s git) adds up to
    10.2s of subprocess limits against merge-gate's 10s host timeout; each
    check used to have a timeout of its own. With the host timeout cut to 4s,
    every stub hanging to its limit, the run must still answer, strict block
    included, inside it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "gh", 30)
    # menu's worst case: each base candidate's merge-base answers inside its
    # 0.3s limit with a different sha (so every is-ancestor runs), then
    # is-ancestor and the diff hang to their limits.
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"merge-base --is-ancestor"*) sleep 30 ;;\n'
        '  *"merge-base"*) sleep 0.2; echo "sha-$4" ;;\n'
        "  *) sleep 30 ;;\n"
        "esac\n"
    )
    git.chmod(0o755)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("PRAXIS_MERGE_MENU_REVIEW_STRICT", "1")
    monkeypatch.setattr(gate, "HOOK_TIMEOUT_SEC", 4)
    payload = {
        "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": "s1",
        "cwd": str(repo),
        "tool_input": {"questions": [{
            "question": "Merge #11, #12 and #13 now?", "header": "Merge", "multiSelect": False,
            "options": [{"label": "squash merge", "description": "d"},
                        {"label": "wait", "description": "d"}],
        }]},
    }
    start = time.monotonic()
    rc, _, err = _call(gate, payload, monkeypatch, capsys)
    elapsed = time.monotonic() - start
    assert elapsed < 4, f"took {elapsed:.1f}s, over the 4s host timeout"
    assert rc == 2, err[-1500:]
