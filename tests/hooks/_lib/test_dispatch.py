"""Tests for hooks/_lib/_dispatch.py — single-process group runner (ADR-0002, #613).

Coverage:
  - group_members: the PreToolUse(Bash) group resolves to the expected count/roles
  - per-hook equivalence: for a no-op payload, dispatcher `run_one` produces the
    same (exit, stdout, stderr) as running the hook's impl.py in a fresh
    subprocess — i.e. stdin reinjection + in-process invocation preserve behaviour
  - run_group on a no-op payload allows (exit 0, no decision JSON)
  - aggregation priority (deny > ask > allow) via fake members
  - crash isolation: a raising member is contained (fail_open) and does not abort
    the group
  - latency: the whole group runs well under the 37-process baseline
  - Stop group (issue #1281): shell members (body: impl.sh) run as a
    deadline-clamped subprocess, the systemMessage advisory lane merges into
    the one emitted object, and the transcript turn memo spans one group run
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "hooks" / "_lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import _dispatch  # noqa: E402

# Reuse the dispatcher's own decision markers so the tests track any change to
# them (single source of truth — see _dispatch._ASK_MARKER / _DENY_MARKER).
_ASK = _dispatch._ASK_MARKER
_DENY = _dispatch._DENY_MARKER


NOOP_PAYLOAD = json.dumps(
    {
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la"},
        "cwd": str(REPO_ROOT),
        "session_id": "test-dispatch",
    }
)


# --------------------------------------------------------------------------- #
# group resolution
# --------------------------------------------------------------------------- #

def test_group_members_count_and_roles():
    # Exact-`Bash` matcher only. Multi-tool hooks
    # (secret-print-redaction-advisory, external-api-literal-trigger,
    # block-personal-asset-leak) are NOT in this group — since #1168 the
    # `Bash|Edit|Write` trio forms its own dispatch group. Hooks that ALSO
    # fire on other tools (fan-out-scope-gate, memory-hint,
    # approval-premise-reread-gate) register their Bash leg as its own
    # exact-`Bash` entry (#1239), so they are members here and standalone on
    # their remaining matcher.
    members = _dispatch.group_members("PreToolUse", "Bash")
    assert len(members) == 55, f"expected 55 exact-Bash members, got {len(members)}"
    # every impl path must exist on disk
    for role, name, impl in members:
        assert impl.exists(), f"missing impl for {role}/{name}: {impl}"
    roles = [role for role, _name, _impl in members]
    assert roles.count("preflight-gate") == 29
    assert roles.count("advisory-nudge") == 26


def test_group_members_host_filter():
    # build-plugin-manifests strips host-restricted hooks per platform; the
    # dispatcher must apply the SAME filter so it never re-includes a hook the
    # build stripped (a Codex install must not run a hosts:["claude"] guard).
    def names(ms):
        return {n for _r, n, _i in ms}

    unfiltered = _dispatch.group_members("PreToolUse", "Bash")
    claude = _dispatch.group_members("PreToolUse", "Bash", host="claude")
    codex = _dispatch.group_members("PreToolUse", "Bash", host="codex")

    assert len(unfiltered) == 55  # host=None -> canonical, unfiltered view
    # the only host-restricted Bash members are the 6 claude-only guards
    assert names(claude) == names(unfiltered)
    assert "block-commit-without-codex-review" not in names(codex)
    assert "block-rename-sweep-survivors" not in names(codex)
    assert "pre-commit-staged-file-enumeration" not in names(codex)
    assert "commit-decomposition-advisory" not in names(codex)
    assert "model-routing-advisory" not in names(codex)
    assert "block-unmatched-glob" not in names(codex)
    assert len(codex) == 48


# --------------------------------------------------------------------------- #
# Non-Bash dispatch groups (#1168)
#
# Membership pins are explicit; the noop / parity / sh-execution tests below
# are parametrized over `dispatch_groups` from the manifest, so a future group
# (e.g. a Stop lane) gets coverage for free the moment it is declared. The
# exact-Bash group keeps its dedicated tests above (including firing payloads).
#
# Two Edit groups on purpose: the `Edit|Write` members must NOT be folded into
# the `Edit|NotebookEdit|Write` group — they would start firing on NotebookEdit
# calls their matcher never covered.
# --------------------------------------------------------------------------- #

EDIT_WRITE_MEMBERS = {
    "comment-yap-advisory",
    "settings-path-advisory",
    "worktree-edit-gate",
    "write-decision-consistency-gate",
    "advisory-wrapper-signature-verify",
    "exclusion-probe-gate",
}
EDIT_NOTEBOOK_WRITE_MEMBERS = {
    "protected-paths-guard",
    "pre-edit-protected-branch-guard",
    "path-probe-gate",
    "bulk-write-memory-checkpoint",
}


def _manifest_dispatch_groups() -> list[tuple[str, str]]:
    """Every (event, matcher) the manifest declares as a dispatch group.

    Derived, never listed by hand: a scope that names its groups outright stops
    following the manifest the moment one is added, which is how two
    fixed-timeout spawns sat unscanned in the write-path groups (issue #1227).
    """
    manifest = json.loads((REPO_ROOT / "hooks" / "manifest.json").read_text())
    # A matcher-less group (Stop, issue #1281) OMITS the key; None is its
    # runtime identity (see _dispatch.NO_MATCHER_ARG).
    return [
        (g["event"], g.get("matcher")) for g in manifest.get("dispatch_groups", [])
    ]


# Every declared group except exact-Bash (which has its own suite above).
NON_BASH_GROUPS = [
    g for g in _manifest_dispatch_groups() if g != ("PreToolUse", "Bash")
]

# Representative no-op tool_input per tool name; a group's payload uses the
# first token of its matcher. Extend this map when a new group's leading tool
# appears — the KeyError from an unknown tool is deliberate (fail loud).
_NOOP_TOOL_INPUTS = {
    "Bash": {"command": "ls -la"},
    "Edit": {
        "file_path": "/tmp/praxis-dispatch-test/note.md",
        "old_string": "alpha",
        "new_string": "beta",
    },
    "Write": {
        "file_path": "/tmp/praxis-dispatch-test/note.md",
        "content": "alpha",
    },
    "NotebookEdit": {
        "notebook_path": "/tmp/praxis-dispatch-test/nb.ipynb",
        "new_source": "alpha",
    },
    "AskUserQuestion": {"questions": []},
}


def _noop_payload_for(event: str, matcher: str | None) -> str:
    if matcher is None:
        # A matcher-less (non-tool) event carries no tool fields. A missing
        # transcript is the no-op for every Stop gate: nothing to read, pass.
        return json.dumps({
            "hook_event_name": event,
            "transcript_path": "/nonexistent/praxis-dispatch-noop.jsonl",
            "stop_hook_active": False,
            "cwd": str(REPO_ROOT),
            "session_id": "test-dispatch-noop",
        })
    tool = matcher.split("|")[0]
    payload = {
        "hook_event_name": event,
        "tool_name": tool,
        "tool_input": _NOOP_TOOL_INPUTS[tool],
        "cwd": str(REPO_ROOT),
        "session_id": "test-dispatch-noop",
    }
    if event == "PostToolUse":
        # A PostToolUse member reads the tool's result; without it the payload
        # would exercise the members' PreToolUse legs instead.
        payload["tool_response"] = {"stdout": "", "stderr": "", "interrupted": False}
    return json.dumps(payload)


def test_memory_hint_runs_before_any_bash_deny():
    # memory-hint speaks through stderr only and its spec promises the hint
    # co-fires with a deny. In the group a deny returns at once and stderr is
    # forwarded per member as it resolves, so the leg has to run first.
    members = _dispatch.group_members("PreToolUse", "Bash")
    assert members[0][1] == "memory-hint"


def test_edit_write_group_members():
    members = _dispatch.group_members("PreToolUse", "Edit|Write")
    assert {n for _r, n, _i in members} == EDIT_WRITE_MEMBERS
    for _role, name, impl in members:
        assert impl.exists(), f"missing impl for {name}: {impl}"


def test_edit_notebook_write_group_members():
    members = _dispatch.group_members("PreToolUse", "Edit|NotebookEdit|Write")
    assert {n for _r, n, _i in members} == EDIT_NOTEBOOK_WRITE_MEMBERS
    for _role, name, impl in members:
        assert impl.exists(), f"missing impl for {name}: {impl}"


# Declared run order. bypass-telemetry comes first: it is the audit record
# for an active bypass var, and as the last member it would be the one
# skipped when the gh-calling members ahead of it drain the group deadline.
POSTTOOLUSE_BASH_MEMBERS = (
    "bypass-telemetry",
    "anchor-comment-gate",
    "push-remote-ref-verify",
    "pr-thread-resolve-advisory",
)


def test_posttooluse_bash_group_members():
    # #1239: the PostToolUse(Bash) hooks run as one process. The group's
    # budget is the max member timeout (anchor-comment-gate's 25 s), so a
    # slow gh call in one member is what the clamp tests below guard.
    members = _dispatch.group_members("PostToolUse", "Bash")
    assert tuple(n for _r, n, _i in members) == POSTTOOLUSE_BASH_MEMBERS
    for _role, name, impl in members:
        assert impl.exists(), f"missing impl for {name}: {impl}"
    _members, budget, _timeouts = _dispatch.load_group("PostToolUse", "Bash")
    assert budget == 25


def test_bypass_telemetry_is_not_the_member_the_deadline_skips(tmp_path, monkeypatch, capsys):
    # The recorder runs first so that a slow sibling cannot cost the audit
    # line: with the deadline already spent, the group skips whatever is
    # still ahead, and standalone the recorder had its own 5 s node.
    members = _dispatch.group_members("PostToolUse", "Bash")
    assert members[0][1] == "bypass-telemetry"


def test_posttooluse_group_forwards_member_stderr_and_context(tmp_path, monkeypatch, capsys):
    # A PostToolUse member speaks through stderr (advisory text) and through
    # `hookSpecificOutput.additionalContext`; the group must forward both
    # unchanged, and a member's exit 2 must end the group with exit 2.
    def member(name: str, body: str) -> tuple[str, str, Path]:
        impl = tmp_path / name / "impl.py"
        impl.parent.mkdir()
        impl.write_text(body)
        return ("advisory-nudge", name, impl)

    talkative = member("talkative", (
        "import json, sys\n"
        "def main():\n"
        "    sys.stderr.write('[talkative] advisory line\\n')\n"
        "    print(json.dumps({'continue': True, 'hookSpecificOutput': {"
        "'hookEventName': 'PostToolUse', 'additionalContext': 'ctx-from-talkative'}}))\n"
        "    return 0\n"
    ))
    quiet = member("quiet", "def main():\n    return 0\n")
    roster = [talkative, quiet]
    timeouts = {(r, n): 5.0 for r, n, _i in roster}
    monkeypatch.setattr(_dispatch, "load_group", lambda e, m, h=None: (roster, 25.0, timeouts))
    monkeypatch.setattr(_dispatch, "_record_fires", lambda *a, **k: None)

    rc = _dispatch.run_group("PostToolUse", "Bash", NOOP_PAYLOAD)
    out = capsys.readouterr()
    assert rc == 0
    assert "[talkative] advisory line" in out.err
    merged = json.loads(out.out)
    assert merged["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "ctx-from-talkative" in merged["hookSpecificOutput"]["additionalContext"]

    # An exit-2 member FIRST must not silence the members after it: after
    # the tool has run every member still speaks (standalone they all ran),
    # and the group returns 2 only once the roster is exhausted. A PreToolUse
    # group returns at the first deny instead — the tool call is blocked, so
    # nothing after it has anything left to gate.
    strict = member("strict", "import sys\ndef main():\n    sys.stderr.write('blocked\\n')\n    return 2\n")
    roster[:] = [strict, talkative, quiet]
    timeouts[("advisory-nudge", "strict")] = 5.0
    rc = _dispatch.run_group("PostToolUse", "Bash", NOOP_PAYLOAD)
    out = capsys.readouterr()
    assert rc == 2
    assert "blocked" in out.err
    assert "[talkative] advisory line" in out.err
    assert out.out == ""  # the blocking member wrote nothing; no context merge on exit 2

    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    out = capsys.readouterr()
    assert rc == 2
    assert "blocked" in out.err
    assert "[talkative] advisory line" not in out.err


def test_posttooluse_members_clamp_their_spawns_to_the_member_deadline(monkeypatch):
    # push-remote-ref-verify and pr-thread-resolve-advisory spawn git / gh
    # through fixed default timeouts (10 s / 5 s). Inside the group those must
    # shrink to the member's remaining budget, and a sub-floor slice must not
    # spawn at all — otherwise one slow call outlives the node timeout.
    import _git_push_target
    import _hook_runtime
    spec = importlib.util.spec_from_file_location(
        "_pr_thread_resolve_advisory_for_test",
        REPO_ROOT / "hooks" / "advisory-nudge" / "pr-thread-resolve-advisory" / "impl.py",
    )
    pr_thread = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pr_thread)

    seen: list[float] = []

    def fake_run(*_a, **kw):
        seen.append(kw["timeout"])
        raise OSError("not spawned for real")

    monkeypatch.setattr(_git_push_target.subprocess, "run", fake_run)
    monkeypatch.setattr(pr_thread.subprocess, "run", fake_run)
    try:
        _hook_runtime.set_member_deadline(time.monotonic() + 2.0)
        assert _git_push_target.git(".", ["status"]) == (None, "")
        assert pr_thread._gh(["pr", "view"], ".") is None
        assert seen and all(t <= 2.0 for t in seen), seen
        seen.clear()
        _hook_runtime.set_member_deadline(time.monotonic() + 0.1)
        assert _git_push_target.git(".", ["status"]) == (None, "")
        assert pr_thread._gh(["pr", "view"], ".") is None
        assert seen == []  # sub-floor: nothing spawned
    finally:
        _hook_runtime.set_member_deadline(None)
    # Standalone (no deadline) the defaults are used unchanged.
    _git_push_target.git(".", ["status"])
    pr_thread._gh(["pr", "view"], ".")
    assert seen == [_git_push_target.GIT_TIMEOUT_SEC, pr_thread._DEFAULT_GH_TIMEOUT]


def test_edit_groups_host_filter():
    # exclusion-probe-gate / path-probe-gate are hosts:["claude","codex"];
    # a cursor install's dispatcher must not re-include them.
    def names(ms):
        return {n for _r, n, _i in ms}

    assert "exclusion-probe-gate" not in names(
        _dispatch.group_members("PreToolUse", "Edit|Write", host="cursor")
    )
    assert "path-probe-gate" not in names(
        _dispatch.group_members("PreToolUse", "Edit|NotebookEdit|Write", host="cursor")
    )


@pytest.mark.parametrize(
    "event,matcher", NON_BASH_GROUPS, ids=[f"{e}:{m}" for e, m in NON_BASH_GROUPS]
)
def test_group_noop_allows(event, matcher):
    rc = _dispatch.run_group(event, matcher, _noop_payload_for(event, matcher))
    assert rc == 0


@pytest.mark.parametrize(
    "event,matcher,member",
    [
        (e, m, member)
        for e, m in NON_BASH_GROUPS
        for member in _dispatch.group_members(e, m)
    ],
    ids=lambda v: f"{v[0]}/{v[1]}" if isinstance(v, tuple) else str(v),
)
def test_run_one_matches_subprocess_for_group_noop(event, matcher, member):
    role, name, impl = member
    payload = _noop_payload_for(event, matcher)
    # A shell member (body: impl.sh) is what its wrapper exec'd: the impl
    # itself, under its own shebang — the same thing run_one now execs.
    argv = [str(impl)] if _dispatch.is_shell_member(Path(impl)) else [sys.executable, str(impl)]
    direct = subprocess.run(
        argv,
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PRAXIS_FIRE_TELEMETRY_DISABLE": "1"},
    )
    rc, so, se = _dispatch.run_one(role, name, impl, payload)
    assert rc == direct.returncode, (
        f"{role}/{name}: exit mismatch in-process={rc} subprocess={direct.returncode}"
    )
    assert so == direct.stdout, f"{role}/{name}: stdout mismatch"
    assert se == direct.stderr, f"{role}/{name}: stderr mismatch"


# --------------------------------------------------------------------------- #
# generated-command execution through a REAL shell (#1198 review, critical)
#
# Rule 14 and every test above invoke the dispatcher in-process or via
# `python3 impl.py`, bypassing the shell — which is exactly why an UNQUOTED
# pipe in the generated command (`... PreToolUse Edit|NotebookEdit|Write
# claude`) shipped: `sh -c` parsed it as a 3-command pipeline, the dispatcher
# ran with matcher 'Edit' (the wrong group) and its decision was swallowed.
# These tests run the command strings from the committed hooks.json through
# `sh -c`, end to end.
# --------------------------------------------------------------------------- #

CLAUDE_HOOKS_JSON = REPO_ROOT / ".claude-plugin" / "hooks" / "hooks.json"


def _generated_dispatcher_commands() -> list[tuple[str, str, str]]:
    data = json.loads(CLAUDE_HOOKS_JSON.read_text())
    out = []
    for event, groups in data.get("hooks", {}).items():
        for group in groups:
            for node in group.get("hooks", []):
                cmd = node.get("command", "")
                if "_dispatch.sh" in cmd:
                    out.append((event, group.get("matcher"), cmd))
    return out


# One throwaway state root for all sh -c runs in this module: PRAXIS_* is
# scrubbed below for determinism, and these two puts the hooks' state/ledger
# writes back into an isolated dir instead of the developer's real ~/.praxis
# (the fire ledger does NOT fall under PRAXIS_HOME — see scripts/run-tests.sh).
_SH_TEST_HOME = tempfile.mkdtemp(prefix="praxis-dispatch-sh-")


def _sh_env(**extra: str) -> dict:
    # Scrub EVERY ambient PRAXIS_* var (strict/bypass/state alike) so the
    # sh -c executions are deterministic regardless of the developer's shell.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PRAXIS_")}
    # The committed command interpolates ${CLAUDE_PLUGIN_ROOT}; for the claude
    # platform the plugin root is the repo root.
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
    env["PRAXIS_HOME"] = _SH_TEST_HOME
    env["PRAXIS_FIRE_TELEMETRY_FILE"] = os.path.join(
        _SH_TEST_HOME, "fire-events-test.jsonl"
    )
    env.update(extra)
    return env


def test_generated_dispatcher_commands_cover_all_groups():
    # every declared dispatch group must appear as a dispatcher command in the
    # committed claude hooks.json (guards the fixture the sh -c tests run on)
    generated = {(e, m) for e, m, _c in _generated_dispatcher_commands()}
    assert generated == set(_manifest_dispatch_groups())


@pytest.mark.parametrize(
    "event,matcher,cmd",
    _generated_dispatcher_commands(),
    ids=[f"{e}:{m}" for e, m, _c in _generated_dispatcher_commands()],
)
def test_generated_command_executes_via_sh(event, matcher, cmd):
    result = subprocess.run(
        ["sh", "-c", cmd],
        input=_noop_payload_for(event, matcher),
        capture_output=True,
        text=True,
        env=_sh_env(),
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"{cmd!r}: rc={result.returncode} stderr={result.stderr[:400]!r}"
    )
    # `sh: 1: NotebookEdit: not found` is the unquoted-pipe signature.
    assert "not found" not in result.stderr, result.stderr[:400]


def test_generated_command_deny_flows_through_sh():
    # A strict-mode protected-paths deny must survive the real shell path:
    # the generated command executed via `sh -c` returns the deny exit code.
    _e, _m, cmd = next(
        c
        for c in _generated_dispatcher_commands()
        if c[0] == "PreToolUse" and c[1] == "Edit|NotebookEdit|Write"
    )
    payload = json.dumps(
        {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/home/user/praxis-e2e/.env",
                "content": "SECRET=1",
            },
            "cwd": "/home/user",
            "session_id": "test-dispatch-sh-deny",
        }
    )
    result = subprocess.run(
        ["sh", "-c", cmd],
        input=payload,
        capture_output=True,
        text=True,
        env=_sh_env(PRAXIS_PROTECTED_PATHS_STRICT="1"),
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 2, (
        f"expected deny exit 2 through sh -c, got {result.returncode}; "
        f"stderr={result.stderr[:400]!r}"
    )
    assert "protected-paths-guard" in result.stderr


# --------------------------------------------------------------------------- #
# per-hook equivalence: in-process run_one == subprocess impl.py
# --------------------------------------------------------------------------- #

def _members():
    return _dispatch.group_members("PreToolUse", "Bash")


@pytest.mark.parametrize("member", _members(), ids=lambda m: f"{m[0]}/{m[1]}")
def test_run_one_matches_subprocess_for_noop(member):
    role, name, impl = member

    direct = subprocess.run(
        [sys.executable, str(impl)],
        input=NOOP_PAYLOAD,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    rc, so, se = _dispatch.run_one(role, name, impl, NOOP_PAYLOAD)

    assert rc == direct.returncode, (
        f"{role}/{name}: exit mismatch in-process={rc} subprocess={direct.returncode}"
    )
    assert so == direct.stdout, f"{role}/{name}: stdout mismatch"
    assert se == direct.stderr, f"{role}/{name}: stderr mismatch"


def test_run_group_noop_allows():
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    assert rc == 0


# --------------------------------------------------------------------------- #
# real-impl GATE-FIRING equivalence (ADR-0002 §3.3 / §5.2)
#
# The no-op test only exercises the allow path. ADR-0002 mandates
# dispatcher-vs-subprocess parity for a *gate-firing* payload too, hook by hook,
# so in-process marker/exit detection is regression-guarded against the REAL
# impls denying/asking — not just the synthetic fakes below. Firing commands were
# confirmed empirically (probe 2026-06-05); members inspect the command STRING
# (they never execute it), so firing is deterministic without real git/gh state.
# --------------------------------------------------------------------------- #

# `gh search ... --state all` -> block-gh-state-all denies unconditionally (rc 2).
_FIRING_DENY = "gh search issues foo --state all"
# `git push --force ...` -> side-effect-scan asks (rc 0 + ask JSON on stdout).
_FIRING_ASK = "git push --force origin main"
# Malformed commit title -> commit-title-format-check denies WHILE side-effect-scan
# + commit-title-length-check ask: exercises deny-beats-ask on REAL impls.
_FIRING_DENY_OVER_ASK = (
    'git commit -m "intentionally very long commit title '
    'exceeding the fifty char limit by a wide margin"'
)


def _payload(command: str) -> str:
    return json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(REPO_ROOT),
            "session_id": "test-dispatch-firing",
        }
    )


@pytest.mark.parametrize(
    "command,expected_rc,ask_on_stdout",
    [
        (_FIRING_DENY, 2, False),
        (_FIRING_ASK, 0, True),
    ],
    ids=["deny", "ask"],
)
def test_run_group_aggregates_real_firing(command, expected_rc, ask_on_stdout, capsys):
    rc = _dispatch.run_group("PreToolUse", "Bash", _payload(command))
    out = capsys.readouterr().out
    assert rc == expected_rc, f"{command!r}: group rc {rc}, expected {expected_rc}"
    if ask_on_stdout:
        assert _ASK in out, f"{command!r}: expected ask JSON on stdout"
    else:
        # a pure deny must not also surface an ask decision on stdout
        assert _ASK not in out


def test_run_group_deny_beats_ask_on_real_impls(monkeypatch, capsys):
    # Pin strict so commit-title-format-check denies deterministically regardless
    # of ambient env; subprocess and in-process both inherit the same value.
    monkeypatch.setenv("PRAXIS_COMMIT_TITLE_FORMAT_STRICT", "1")
    rc = _dispatch.run_group("PreToolUse", "Bash", _payload(_FIRING_DENY_OVER_ASK))
    out = capsys.readouterr().out
    assert rc == 2, "deny must win over the concurrent asks"
    assert _ASK not in out, "deny wins; the ask decision must not be surfaced on stdout"


def test_run_one_matches_subprocess_for_firing_payload(monkeypatch):
    # ADR-0002 §3.3: per-hook dispatcher-vs-subprocess parity on a gate-firing
    # payload. The malformed-commit payload fires 1 deny + 2 ask members on real
    # impls, so this exercises in-process deny AND ask marker reproduction.
    monkeypatch.setenv("PRAXIS_COMMIT_TITLE_FORMAT_STRICT", "1")
    payload = _payload(_FIRING_DENY_OVER_ASK)
    mismatches: list[str] = []
    in_proc_denies = 0
    in_proc_asks = 0
    for role, name, impl in _dispatch.group_members("PreToolUse", "Bash"):
        direct = subprocess.run(
            [sys.executable, str(impl)],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        rc, so, se = _dispatch.run_one(role, name, impl, payload)
        if (rc, so, se) != (direct.returncode, direct.stdout, direct.stderr):
            mismatches.append(
                f"{role}/{name}: in-proc rc={rc} vs subproc rc={direct.returncode}"
            )
        if rc == 2 or _DENY in so:
            in_proc_denies += 1
        elif _ASK in so:
            in_proc_asks += 1
    assert not mismatches, "firing-payload parity mismatches:\n" + "\n".join(mismatches)
    assert in_proc_denies >= 1, "deny marker/exit path not exercised in-process"
    assert in_proc_asks >= 1, "ask marker path not exercised in-process"


# --------------------------------------------------------------------------- #
# aggregation priority via fake members
# --------------------------------------------------------------------------- #

def _write_fake(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "impl.py"
    p.write_text(body)
    return p


_FAKE_PASS = "def main():\n    return 0\n"
_FAKE_DENY_EXIT = "import sys\ndef main():\n    return 2\n"
_FAKE_ASK = (
    "import json, sys\n"
    "def main():\n"
    "    json.dump({'hookSpecificOutput': {'hookEventName': 'PreToolUse',"
    " 'permissionDecision': 'ask', 'permissionDecisionReason': 'r'}}, sys.stdout)\n"
    "    sys.stdout.write('\\n')\n"
    "    return 0\n"
)
_FAKE_ADVISORY = "import sys\ndef main():\n    sys.stderr.write('nudge\\n')\n    return 0\n"
_FAKE_CRASH = "def main():\n    raise RuntimeError('boom')\n"


def _patch_members(monkeypatch, members, budget=15.0, timeouts=None):
    # run_group resolves roster + budget through the single-read load_group
    # (issue #1167 review round: one manifest parse per dispatch), so fakes
    # are injected there. `budget`/`timeouts` default to today's real group
    # budget with no per-member caps.
    monkeypatch.setattr(
        _dispatch,
        "load_group",
        lambda _e, _m, _h=None: (members, budget, dict(timeouts or {})),
    )


def test_deny_wins_over_ask_and_advisory(tmp_path, monkeypatch, capsys):
    members = [
        ("advisory-nudge", "adv", _write_fake(tmp_path, "adv", _FAKE_ADVISORY)),
        ("preflight-gate", "ask", _write_fake(tmp_path, "ask", _FAKE_ASK)),
        ("preflight-gate", "deny", _write_fake(tmp_path, "deny", _FAKE_DENY_EXIT)),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    captured = capsys.readouterr()
    assert rc == 2
    assert "nudge" in captured.err  # advisory preserved even under deny


_FAKE_MARK = (
    "import pathlib\n"
    "def main():\n"
    "    pathlib.Path(__file__).with_name('ran').write_text('1')\n"
    "    return 0\n"
)
_FAKE_DENY_MARK = (
    "import pathlib, sys\n"
    "def main():\n"
    "    pathlib.Path(__file__).with_name('ran').write_text('1')\n"
    "    sys.stderr.write('blocked\\n')\n"
    "    return 2\n"
)


def test_deny_returns_before_later_members_run(tmp_path, monkeypatch, capsys):
    """A deny leaves the dispatcher immediately, taking nothing with it.

    Buffering the decision to the end of the group meant every member after the
    deny kept spending the group budget, and a host kill at the group timeout
    then discarded the deny along with the process — a gate that had decided to
    block an edit silently did not. A host kill cannot be staged from inside the
    process, so the assertion is its observable equivalent: once a member denies,
    nothing after it runs, which is what leaves no window to be killed in.
    """
    deny = _write_fake(tmp_path, "deny", _FAKE_DENY_MARK)
    later = _write_fake(tmp_path, "later", _FAKE_MARK)
    _patch_members(
        monkeypatch,
        [("preflight-gate", "deny", deny), ("advisory-nudge", "later", later)],
    )
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    captured = capsys.readouterr()
    assert rc == 2
    assert deny.with_name("ran").exists(), "denying member never ran"
    assert not later.with_name("ran").exists(), "a member ran after the deny"
    assert "blocked" in captured.err  # the deny reason still reached stderr


def test_ask_when_no_deny(tmp_path, monkeypatch, capsys):
    members = [
        ("advisory-nudge", "adv", _write_fake(tmp_path, "adv", _FAKE_ADVISORY)),
        ("preflight-gate", "ask", _write_fake(tmp_path, "ask", _FAKE_ASK)),
        ("preflight-gate", "pass", _write_fake(tmp_path, "pass", _FAKE_PASS)),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    captured = capsys.readouterr()
    assert rc == 0
    assert '"permissionDecision": "ask"' in captured.out
    assert "nudge" in captured.err


def test_all_pass_allows(tmp_path, monkeypatch, capsys):
    members = [
        ("preflight-gate", "p1", _write_fake(tmp_path, "p1", _FAKE_PASS)),
        ("preflight-gate", "p2", _write_fake(tmp_path, "p2", _FAKE_PASS)),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""


def test_crashing_member_is_isolated(tmp_path, monkeypatch):
    members = [
        ("preflight-gate", "crash", _write_fake(tmp_path, "crash", _FAKE_CRASH)),
        ("preflight-gate", "deny", _write_fake(tmp_path, "deny", _FAKE_DENY_EXIT)),
    ]
    _patch_members(monkeypatch, members)
    # crash must NOT abort the group: the later deny still wins.
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    assert rc == 2


def test_crash_only_fails_open(tmp_path, monkeypatch):
    members = [
        ("preflight-gate", "crash", _write_fake(tmp_path, "crash", _FAKE_CRASH)),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    assert rc == 0  # a lone crashing hook fails open, never blocks


def test_import_error_fails_open_but_not_silent(tmp_path):
    # A member whose impl.py raises at IMPORT time (missing dep, syntax error)
    # must fail OPEN (never block) yet surface the traceback — fail-open is not
    # fail-silent, matching the per-process `python3 impl.py` wrapper.
    broken = _write_fake(
        tmp_path, "broken", "import _no_such_module_xyz123\n\n\ndef main():\n    return 0\n"
    )
    rc, so, se = _dispatch.run_one("preflight-gate", "broken", broken, NOOP_PAYLOAD)
    assert rc == 0  # fail OPEN
    assert so == ""
    assert "import failed" in se  # NOT fail-silent
    assert "_no_such_module_xyz123" in se


# --------------------------------------------------------------------------- #
# non-decision additionalContext forwarding (issue #874)
# --------------------------------------------------------------------------- #

def _fake_context(event: str, text: str) -> str:
    return (
        "import json, sys\n"
        "def main():\n"
        "    json.dump({'hookSpecificOutput': {'hookEventName': %r,"
        " 'additionalContext': %r}}, sys.stdout)\n"
        "    sys.stdout.write('\\n')\n"
        "    return 0\n" % (event, text)
    )


def _sole_hso(stdout: str) -> dict:
    # One JSON object, not N concatenated — concatenation is invalid JSON and
    # would lose every member, which is the failure this forwarding exists to fix.
    return json.loads(stdout)["hookSpecificOutput"]


def test_matching_event_contexts_merge_into_one_object(tmp_path, monkeypatch, capsys):
    members = [
        ("advisory-nudge", "c1", _write_fake(tmp_path, "c1", _fake_context("PreToolUse", "first"))),
        ("advisory-nudge", "c2", _write_fake(tmp_path, "c2", _fake_context("PreToolUse", "second"))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    hso = _sole_hso(capsys.readouterr().out)
    assert rc == 0
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["additionalContext"] == "first\n\nsecond"


def test_mismatched_event_context_is_dropped(tmp_path, monkeypatch, capsys):
    # Adopting a member's own event name would let one wrong member label the
    # merged object; Claude Code discards an object whose event does not match
    # the hook it invoked, so every member's context would be lost with it.
    members = [
        ("advisory-nudge", "bad", _write_fake(tmp_path, "bad", _fake_context("PostToolUse", "wrong-event"))),
        ("advisory-nudge", "good", _write_fake(tmp_path, "good", _fake_context("PreToolUse", "right-event"))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    hso = _sole_hso(capsys.readouterr().out)
    assert rc == 0
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["additionalContext"] == "right-event"


def test_all_events_mismatched_writes_nothing(tmp_path, monkeypatch, capsys):
    # The negative side of the case above: with nothing left to merge the
    # dispatcher must stay silent rather than emit an empty context object.
    members = [
        ("advisory-nudge", "bad1", _write_fake(tmp_path, "bad1", _fake_context("PostToolUse", "a"))),
        ("advisory-nudge", "bad2", _write_fake(tmp_path, "bad2", _fake_context("", "b"))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    assert rc == 0
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------- #
# per-member deadline / group budget (issue #1167)
# --------------------------------------------------------------------------- #

_FAKE_SLOW = (
    "import time, sys\n"
    "def main():\n"
    "    time.sleep(1.2)\n"
    "    return 0\n"
)
# Reports the budget the dispatcher published for this member (via the
# _hook_runtime accessor) on stderr; 99.0 is the "no deadline" sentinel.
_FAKE_BUDGET_PROBE = (
    "import sys\n"
    "import _hook_runtime\n"
    "def main():\n"
    "    sys.stderr.write('budget=%.3f' % _hook_runtime.remaining_budget(99.0))\n"
    "    return 0\n"
)


def test_load_group_matches_manifest():
    # Budget derivation must mirror the build's dispatcher-node timeout: the
    # MAX member timeout across the group (scripts/build-plugin-manifests.py,
    # filter_hooks_for_host's dispatch_timeout pre-pass). The manifest is the
    # shared source of truth, read ONCE per dispatch; group_members is a thin
    # projection of the same load_group read.
    members, budget, timeouts = _dispatch.load_group("PreToolUse", "Bash")
    assert set(timeouts) == {(r, n) for r, n, _i in members}
    assert budget == max(timeouts.values())
    assert budget == 15  # the two 15s gh gates set today's group budget
    assert timeouts[("preflight-gate", "gh-label-verify")] == 15
    assert _dispatch.group_members("PreToolUse", "Bash") == members


def _fixed_timeout_spawns(source: str) -> list[str]:
    """Every `subprocess.*` call in `source` whose timeout cannot shrink.

    Call-site granularity, not file granularity. A file-wide grep for the
    budget API passes as soon as ONE call reads the budget, so a hook that
    budgets its later probes and leaves its FIRST subprocess on a module
    constant looks clean — which is exactly the shape that reached review.

    A timeout is "fixed" when its expression is built only from constants and
    module-level ALL-CAPS names: nothing in it can shrink when the group is
    already short on time. `min(_GH_TIMEOUT_SEC, budget)` mentions a local, so
    it passes; a bare `_PROBE_TIMEOUT_SEC` does not.

    A `subprocess.Popen(..., start_new_session=True)` whose handle is
    discarded (a bare expression statement) is exempt: it is a detached child
    the member never waits on, so it has no timeout to size and cannot hold
    the group past its deadline. A Popen whose handle is kept — assigned,
    chained into `.wait()` / `.communicate()` — is a spawn the member may
    join and is held to the same rule as `run`.
    """
    tree = ast.parse(source)
    module_consts = {
        t.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name) and t.id.lstrip("_").isupper()
    }
    discarded = {
        id(node.value) for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    }
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "subprocess"
                and f.attr in {"run", "Popen", "check_output", "call"}):
            continue
        if f.attr == "Popen" and id(node) in discarded and any(
            k.arg == "start_new_session" and isinstance(k.value, ast.Constant)
            and k.value.value is True for k in node.keywords
        ):
            continue  # detached child the member never joins (#1238 rollover)
        kw = next((k for k in node.keywords if k.arg == "timeout"), None)
        if kw is None:
            offenders.append(f"line {node.lineno}: no timeout=")
            continue
        names = {n.id for n in ast.walk(kw.value) if isinstance(n, ast.Name)}
        if names <= module_consts:
            offenders.append(f"line {node.lineno}: timeout is fixed")
    return offenders


def _lib_closure(impl: Path) -> list[Path]:
    """`impl` plus every hooks/_lib module it reaches, transitively.

    The member impl alone stopped being the whole spawn surface once the shared
    helpers were extracted (issue #1178): `run_git` moved the subprocess out of
    six hook files into `_lib/_git.py`, and a scan pinned to impl.py would have
    reported a clean group while the spawn merely changed address. Following the
    imports keeps the invariant measuring what the member actually executes.
    """
    modules = {p.stem: p for p in LIB.glob("*.py")}
    seen, queue, out = set(), [impl], [impl]
    while queue:
        tree = ast.parse(queue.pop().read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            else:
                continue
            for mod in names:
                if mod in modules and mod not in seen:
                    seen.add(mod)
                    out.append(modules[mod])
                    queue.append(modules[mod])
    return out


def test_the_lib_closure_reaches_the_extracted_spawn():
    # Positive control for the scan range below: an empty offender list has to
    # mean "no fixed timeouts", not "the closure stopped following imports".
    # pre-gh-pr-create-dedup-gate spawns no subprocess of its own any more — its
    # git probe lives in _lib/_git.py — so the closure MUST reach that file or
    # the widened scan is measuring nothing.
    members, _budget, _timeouts = _dispatch.load_group("PreToolUse", "Bash")
    impl = next(Path(i) for r, n, i in members if n == "pre-gh-pr-create-dedup-gate")
    reached = {p.name for p in _lib_closure(impl)}
    assert "_git.py" in reached
    assert "_payload.py" in reached  # transitive, not a direct import of impl.py


def test_the_dispatch_group_scan_reaches_the_write_path_groups():
    # Positive control for the scope of the invariant below: an empty offender
    # list has to mean "no fixed timeouts anywhere", not "the derivation
    # returned one group" or "it returned none at all". A renamed manifest key
    # yields [] and a hand-listed scope yields Bash only; both pass the scan
    # while measuring nothing, which is the exact failure this test names.
    #
    # Naming the two hooks is what makes this a control rather than a shape
    # check: "some non-Bash group exists" still passes while the groups that
    # actually carried the fixed-timeout spawns are the ones left out, which
    # is the same blind spot one level up.
    groups = _manifest_dispatch_groups()
    assert ("PreToolUse", "Bash") in groups
    assert [g for g in groups if g != ("PreToolUse", "Bash")]
    scanned = {n for e, m in groups for _r, n, _i in _dispatch.load_group(e, m)[0]}
    assert {"block-personal-asset-leak", "path-probe-gate"} <= scanned
    assert all(_dispatch.load_group(e, m)[0] for e, m in groups)


def test_every_subprocess_member_is_budget_aware():
    # The dispatcher no longer decides per member whether a shortened cap is
    # safe: it clamps every member's deadline to the group's and relies on
    # each member sizing its own subprocess timeouts from the shared budget.
    # That reliance is the invariant here. A member spawning a subprocess
    # under a fixed timeout can outlive the group deadline and get the whole
    # dispatcher killed by the host, and nothing at the call site shows it.
    # The timeout does not have to exceed the group budget to do that: the
    # skip floor lets a member start with 0.5s of runway left, so any fixed
    # timeout above the floor overruns the deadline from that position.
    #
    # The scan follows each member into hooks/_lib: extraction moves a spawn
    # without changing what runs inside the group deadline (issue #1178).
    offenders = {}
    for event, matcher in _manifest_dispatch_groups():
        members, _budget, _timeouts = _dispatch.load_group(event, matcher)
        for role, name, impl in members:
            if _dispatch.is_shell_member(Path(impl)):
                # A shell member has no spawn to size: the dispatcher runs
                # the whole impl as ONE subprocess whose timeout IS the
                # member deadline (run_shell_member), so it is clamped by
                # construction — covered by
                # test_shell_member_past_its_deadline_fails_open below.
                continue
            for src in _lib_closure(Path(impl)):
                bad = _fixed_timeout_spawns(
                    src.read_text(encoding="utf-8", errors="replace"))
                if bad:
                    offenders[f"{event}/{matcher} {role}/{name} -> {src.name}"] = bad
    assert offenders == {}


def test_the_fixed_timeout_detector_can_fail():
    # Positive control for the test above: an empty offender list has to mean
    # "no fixed timeouts", not "the detector stopped matching". Both shapes it
    # is meant to catch are checked, plus one it must NOT flag.
    fixed = textwrap.dedent("""\
        import subprocess
        T = 5
        def f():
            subprocess.run(['x'], timeout=T)
    """)
    missing = textwrap.dedent("""\
        import subprocess
        def f():
            subprocess.run(['x'])
    """)
    budgeted = textwrap.dedent("""\
        import subprocess
        T = 5
        def f(budget):
            subprocess.run(['x'], timeout=min(T, budget))
    """)
    detached = textwrap.dedent("""\
        import subprocess
        def f():
            subprocess.Popen(['x'], start_new_session=True)
    """)
    joined = textwrap.dedent("""\
        import subprocess
        def f():
            subprocess.Popen(['x']).wait()
    """)
    joined_detached = textwrap.dedent("""\
        import subprocess
        def f():
            subprocess.Popen(['x'], start_new_session=True).wait()
    """)
    kept_detached = textwrap.dedent("""\
        import subprocess
        def f():
            p = subprocess.Popen(['x'], start_new_session=True)
            return p
    """)
    assert _fixed_timeout_spawns(fixed) == ["line 4: timeout is fixed"]
    assert _fixed_timeout_spawns(missing) == ["line 3: no timeout="]
    assert _fixed_timeout_spawns(budgeted) == []
    assert _fixed_timeout_spawns(detached) == []
    assert _fixed_timeout_spawns(joined) == ["line 3: no timeout="]
    assert _fixed_timeout_spawns(joined_detached) == ["line 3: no timeout="]
    assert _fixed_timeout_spawns(kept_detached) == ["line 3: no timeout="]


def test_budget_exhausting_member_starves_no_one_silently(
    tmp_path, monkeypatch, capsys
):
    # An early member that eats the whole group budget must not make later
    # members vanish: they are skipped fail-open WITH a record — a merged
    # additionalContext notice on stdout (the one exit-0 PreToolUse channel
    # that reaches the model), a stderr line, and a decision="skip"
    # fire-ledger row (the issue's core complaint is invisible starvation).
    members = [
        ("preflight-gate", "slow", _write_fake(tmp_path, "slow", _FAKE_SLOW)),
        ("advisory-nudge", "late1", _write_fake(tmp_path, "late1", _FAKE_ADVISORY)),
        ("preflight-gate", "late2", _write_fake(tmp_path, "late2", _FAKE_PASS)),
    ]
    # Budget 2s − 1s margin ⇒ ~1s of runway; the slow member sleeps 1.2s.
    _patch_members(monkeypatch, members, budget=2.0)
    ledger = tmp_path / "fire-events-test.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(ledger))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)

    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    captured = capsys.readouterr()
    assert rc == 0  # skips are fail-open, never a block
    assert f"{_dispatch._SKIP_MARKER} advisory-nudge/late1" in captured.err
    assert f"{_dispatch._SKIP_MARKER} preflight-gate/late2" in captured.err
    # The model-visible channel: skip notices ride the #874 additionalContext
    # merge (stderr never reaches the model on an exit-0 PreToolUse).
    hso = _sole_hso(captured.out)
    assert hso["hookEventName"] == "PreToolUse"
    assert f"{_dispatch._SKIP_MARKER} advisory-nudge/late1" in hso["additionalContext"]
    assert f"{_dispatch._SKIP_MARKER} preflight-gate/late2" in hso["additionalContext"]

    records = {
        rec["hook"]: rec["decision"]
        for rec in map(json.loads, ledger.read_text().splitlines())
    }
    assert records["slow"] == "pass"  # ran (its sleep is not a decision)
    assert records["late1"] == "skip"
    assert records["late2"] == "skip"


def test_member_whose_timeout_no_longer_fits_still_runs_clamped(
    tmp_path, monkeypatch, capsys
):
    # The floor is the ONLY skip condition. Gating on `remaining >=
    # member_timeout` measured the budget against a worst case almost no
    # member reaches, so one slow call early in the group silently skipped
    # every deny-capable gate behind it. A member whose
    # manifest timeout no longer fits must still RUN — under a deadline
    # clamped to the group's, which is what it sizes its subprocesses from.
    members = [("preflight-gate", "big", _write_fake(tmp_path, "big", _FAKE_BUDGET_PROBE))]
    # budget 3 − 1 margin ⇒ ~2s remaining < the member's 5s manifest timeout.
    _patch_members(
        monkeypatch, members, budget=3.0,
        timeouts={("preflight-gate", "big"): 5.0},
    )
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    err = capsys.readouterr().err
    assert rc == 0
    assert _dispatch._SKIP_MARKER not in err
    # It ran, and what it saw is the group's remaining time, not its own 5s.
    seen = float(err.partition("budget=")[2].split()[0])
    assert 0.0 < seen <= 2.0


def test_member_is_skipped_only_once_below_the_floor(tmp_path, monkeypatch, capsys):
    # The other side of the same condition: below the floor there is not
    # enough runway for even a minimal subprocess, so the member is skipped
    # with a record rather than started and killed mid-call.
    members = [
        ("preflight-gate", "slow", _write_fake(tmp_path, "slow", _FAKE_SLOW)),
        ("preflight-gate", "late", _write_fake(tmp_path, "late", _FAKE_PASS)),
    ]
    # Budget 2s − 1s margin ⇒ ~1s of runway; the slow member sleeps 1.2s, so
    # `late` sees remaining < _MEMBER_SKIP_FLOOR_SEC.
    _patch_members(monkeypatch, members, budget=2.0)
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    err = capsys.readouterr().err
    assert rc == 0
    assert f"{_dispatch._SKIP_MARKER} preflight-gate/late" in err


def test_fires_are_recorded_incrementally_not_after_the_loop(
    tmp_path, monkeypatch, capsys
):
    # Round-2 review: a batch write after the loop would be erased along with
    # everything else if the host killed the dispatcher mid-group. Each
    # member's record must land BEFORE the next member runs — proven by a
    # second member that reads the ledger while it executes.
    ledger = tmp_path / "fire-events-test.jsonl"
    reader = (
        "import os, sys\n"
        "def main():\n"
        "    text = ''\n"
        "    try:\n"
        "        with open(os.environ['PRAXIS_FIRE_TELEMETRY_FILE']) as fh:\n"
        "            text = fh.read()\n"
        "    except OSError:\n"
        "        pass\n"
        "    sys.stderr.write('first_recorded=%d' % ('\"first\"' in text))\n"
        "    return 0\n"
    )
    members = [
        ("preflight-gate", "first", _write_fake(tmp_path, "first", _FAKE_PASS)),
        ("advisory-nudge", "reader", _write_fake(tmp_path, "reader", reader)),
    ]
    _patch_members(monkeypatch, members)
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(ledger))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    err = capsys.readouterr().err
    assert rc == 0
    assert "first_recorded=1" in err  # first member's fire visible mid-group
    # and the reader's own fire is recorded too, after it ran
    assert '"reader"' in ledger.read_text()


def test_normal_budget_runs_every_member(tmp_path, monkeypatch, capsys):
    # Normal path unchanged: with headroom, nothing is skipped and the
    # aggregate decision is exactly what the pre-#1167 dispatcher produced.
    members = [
        ("advisory-nudge", "adv", _write_fake(tmp_path, "adv", _FAKE_ADVISORY)),
        ("preflight-gate", "ask", _write_fake(tmp_path, "ask", _FAKE_ASK)),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    captured = capsys.readouterr()
    assert rc == 0
    assert _ASK in captured.out
    assert "nudge" in captured.err
    assert _dispatch._SKIP_MARKER not in captured.err


def test_run_one_publishes_member_budget_and_clears_it(tmp_path):
    import _hook_runtime

    probe = _write_fake(tmp_path, "probe", _FAKE_BUDGET_PROBE)
    _rc, _so, se = _dispatch.run_one(
        "advisory-nudge", "probe", probe, NOOP_PAYLOAD,
        deadline=time.monotonic() + 5.0,
    )
    published = float(se.partition("=")[2])
    assert 0.0 < published <= 5.0  # the member sees (at most) its cap
    # Deadline must not leak past the member: standalone reads get the default.
    assert _hook_runtime.remaining_budget(42.0) == 42.0


def test_run_one_publishes_deadline_before_the_cold_import(tmp_path):
    # Round-2 review: the deadline must be live during _load_main's import,
    # so a slow cold import erodes the member's own budget instead of the
    # group's post-loop margin. The probe samples remaining_budget at MODULE
    # BODY (import) time and reports that sample from main().
    body = (
        "import sys\n"
        "import _hook_runtime\n"
        "_AT_IMPORT = _hook_runtime.remaining_budget(99.0)\n"
        "def main():\n"
        "    sys.stderr.write('at_import=%.3f' % _AT_IMPORT)\n"
        "    return 0\n"
    )
    probe = _write_fake(tmp_path, "import-probe", body)
    _rc, _so, se = _dispatch.run_one(
        "advisory-nudge", "import-probe", probe, NOOP_PAYLOAD,
        deadline=time.monotonic() + 5.0,
    )
    at_import = float(se.partition("=")[2])
    assert 0.0 < at_import <= 5.0  # 99.0 would mean it was published too late


def test_run_one_without_budget_publishes_no_deadline(tmp_path):
    probe = _write_fake(tmp_path, "probe2", _FAKE_BUDGET_PROBE)
    _rc, _so, se = _dispatch.run_one("advisory-nudge", "probe2", probe, NOOP_PAYLOAD)
    assert se == "budget=99.000"  # accessor returned the standalone default


# --------------------------------------------------------------------------- #
# Stop-lane decision:block aggregation (issue #1169)
# --------------------------------------------------------------------------- #

STOP_PAYLOAD = json.dumps(
    {
        "hook_event_name": "Stop",
        "transcript_path": "/nonexistent/transcript.jsonl",
        "stop_hook_active": False,
        "cwd": str(REPO_ROOT),
        "session_id": "test-dispatch-stop",
    }
)


def _fake_raw_stdout(text: str) -> str:
    # Writes `text` to stdout UNCHANGED. _fake_context cannot be used to carry
    # a permissionDecision marker: json.dump escapes the inner quotes, so the
    # literal marker never survives into stdout and the substring lanes are
    # never reached — a test built on it passes with the event scoping removed
    # (issue #1199 review). This is also the real shape of the threat: an
    # advisory hook printing help or example text verbatim.
    return (
        "import sys\n"
        "def main():\n"
        "    sys.stdout.write(%r + '\\n')\n"
        "    return 0\n" % text
    )


def _fake_stop_block(reason: str) -> str:
    # Mirrors _hook_io.emit_stop_block / the shell siblings' jq form: blocking
    # is carried by the JSON `decision` field at exit 0, NOT the exit code.
    return (
        "import json, sys\n"
        "def main():\n"
        "    json.dump({'decision': 'block', 'reason': %r}, sys.stdout)\n"
        "    sys.stdout.write('\\n')\n"
        "    return 0\n" % reason
    )


def test_stop_member_block_is_not_swallowed(tmp_path, monkeypatch, capsys):
    # The issue-#1169 regression case: one Stop member blocks at exit 0; the
    # group must emit the block instead of falling through to the context-merge
    # path and swallowing it.
    members = [
        ("completion-verify", "pass", _write_fake(tmp_path, "pass", _FAKE_PASS)),
        ("completion-verify", "gate", _write_fake(tmp_path, "gate", _fake_stop_block("no evidence"))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    out = capsys.readouterr().out
    obj = json.loads(out)  # exactly ONE JSON object on stdout
    assert rc == 0  # Stop blocking is carried by the JSON, not the exit code
    assert obj["decision"] == "block"
    assert obj["reason"] == "[praxis:gate] no evidence"


def test_stop_all_pass_stays_silent(tmp_path, monkeypatch, capsys):
    members = [
        ("completion-verify", "p1", _write_fake(tmp_path, "p1", _FAKE_PASS)),
        ("completion-verify", "p2", _write_fake(tmp_path, "p2", _FAKE_PASS)),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_stop_multiple_blocks_merge_into_one_object(tmp_path, monkeypatch, capsys):
    # Concatenating two decision objects is invalid JSON; both reasons must
    # survive in ONE block, blank-line joined, each attributed to its hook.
    # A reason that already carries its own [praxis:<hook>] prefix (the
    # convention the python emitters use) is not double-tagged.
    members = [
        ("completion-verify", "g1", _write_fake(tmp_path, "g1", _fake_stop_block("first reason"))),
        ("completion-verify", "g2", _write_fake(
            tmp_path, "g2", _fake_stop_block("[praxis:g2] second reason"))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    obj = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert obj["decision"] == "block"
    assert obj["reason"] == "[praxis:g1] first reason\n\n[praxis:g2] second reason"


def test_stop_block_wins_over_context(tmp_path, monkeypatch, capsys):
    # Mixed block + context: only one JSON object can reach the host, and the
    # block is the restrictive outcome — the context member's output must not
    # corrupt or displace it (members re-run on the next stop attempt).
    members = [
        ("completion-verify", "ctx", _write_fake(tmp_path, "ctx", _fake_context("Stop", "fyi"))),
        ("completion-verify", "gate", _write_fake(tmp_path, "gate", _fake_stop_block("blocked"))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    obj = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert obj == {"decision": "block", "reason": "[praxis:gate] blocked"}


def test_stop_contexts_merge_when_no_block(tmp_path, monkeypatch, capsys):
    # Without a blocking member the pre-existing additionalContext lane still
    # applies to a Stop group (issue #874 path, event-name-checked).
    members = [
        ("completion-verify", "c1", _write_fake(tmp_path, "c1", _fake_context("Stop", "one"))),
        ("completion-verify", "c2", _write_fake(tmp_path, "c2", _fake_context("Stop", "two"))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    hso = _sole_hso(capsys.readouterr().out)
    assert rc == 0
    assert hso["hookEventName"] == "Stop"
    assert hso["additionalContext"] == "one\n\ntwo"


def test_stop_block_recognition_is_parse_based_not_substring(tmp_path, monkeypatch, capsys):
    # A context string that merely MENTIONS the block shape must not be read as
    # a block — recognition parses the whole stdout object (issue #1169).
    prose = 'docs say emit {"decision": "block", "reason": ...} to block'
    members = [
        ("completion-verify", "ctx", _write_fake(tmp_path, "ctx", _fake_context("Stop", prose))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    out = capsys.readouterr().out
    assert rc == 0
    obj = json.loads(out)
    assert "decision" not in obj  # forwarded as context, never as a block
    assert obj["hookSpecificOutput"]["additionalContext"] == prose


def test_stop_quoted_marker_cannot_shadow_a_real_block(tmp_path, monkeypatch, capsys):
    # Adversarial (issue #1199 review): a Stop member's additionalContext that
    # QUOTES a permissionDecision marker must not be surfaced as a fake
    # ask/deny that shadows (and drops) a later member's real block — the
    # substring lanes are PreToolUse-only.
    quoting = f"the PreToolUse hooks emit {_ASK} or {_DENY} on stdout"
    members = [
        ("completion-verify", "ctx", _write_fake(tmp_path, "ctx", _fake_raw_stdout(quoting))),
        ("completion-verify", "gate", _write_fake(tmp_path, "gate", _fake_stop_block("real block"))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    obj = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert obj == {"decision": "block", "reason": "[praxis:gate] real block"}


def test_stop_quoted_marker_context_still_merges_without_block(tmp_path, monkeypatch, capsys):
    # Same quoting member with no blocker: the context must MERGE (not be
    # dropped as a mistaken decision payload) — the marker exclusion in the
    # context lane is PreToolUse-only too.
    quoting = f"docs: {_ASK} is the PreToolUse ask marker"
    members = [
        ("completion-verify", "ctx", _write_fake(tmp_path, "ctx", _fake_context("Stop", quoting))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    hso = _sole_hso(capsys.readouterr().out)
    assert rc == 0
    assert hso["additionalContext"] == quoting


def test_stop_raw_marker_fixture_discriminates(tmp_path, monkeypatch, capsys):
    """The fixture above must fail when the event scoping is removed.

    Without this, `test_stop_quoted_marker_cannot_shadow_a_real_block` can pass
    for the wrong reason — as it did while its member routed the marker through
    json.dump. Here the marker reaches stdout unescaped, so the PreToolUse-only
    substring lane is genuinely exercised: the assertion below is exactly the
    one that flips when `is_pretooluse` stops gating it.
    """
    quoting = f"the PreToolUse hooks emit {_DENY} on stdout"
    members = [
        ("completion-verify", "ctx", _write_fake(tmp_path, "ctx", _fake_raw_stdout(quoting))),
        ("completion-verify", "gate", _write_fake(tmp_path, "gate", _fake_stop_block("real block"))),
    ]
    _patch_members(monkeypatch, members)
    assert _DENY in quoting, "fixture no longer carries the literal marker"
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    out = capsys.readouterr().out
    assert rc == 0, "the quoting member's text was read as a deny"
    assert json.loads(out) == {"decision": "block", "reason": "[praxis:gate] real block"}


def test_stop_block_lane_does_not_run_on_other_events(tmp_path, monkeypatch, capsys):
    # `{"decision": "block"}` is Stop's vocabulary. Re-emitting it as the
    # group's answer on another event answers a question that event never
    # asked (CodeRabbit, issue #1199 review).
    members = [
        ("completion-verify", "gate", _write_fake(tmp_path, "gate", _fake_stop_block("blocked"))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("PostToolUse", None, STOP_PAYLOAD)
    assert rc == 0
    assert capsys.readouterr().out == "", "a Stop block was re-emitted on PostToolUse"
    # Control: the identical member on Stop DOES block, so the assertion above
    # is not passing because the fixture is inert.
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_escaped_decision_key_still_blocks(tmp_path, monkeypatch, capsys):
    # `{"\u0064ecision": "block"}` is valid JSON for the same object. A literal
    # substring pre-filter dropped it before the parse that is supposed to be
    # the authority (CodeRabbit, issue #1199 review).
    escaped = '{"\\u0064ecision": "block", "reason": "escaped"}'
    assert '"decision"' not in escaped, "fixture is not actually escaped"
    members = [
        ("completion-verify", "gate", _write_fake(tmp_path, "gate", _fake_raw_stdout(escaped))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {
        "decision": "block", "reason": "[praxis:gate] escaped"
    }


def test_stop_block_with_malformed_reason_still_blocks(tmp_path, monkeypatch, capsys):
    # A parsed block whose `reason` is missing/non-string still blocks (empty
    # reason -> the attribution tag alone, no trailing space): dropping it for
    # a malformed reason field would be the exact silent swallow this lane
    # exists to prevent.
    body = (
        "import json, sys\n"
        "def main():\n"
        "    json.dump({'decision': 'block', 'reason': 123}, sys.stdout)\n"
        "    sys.stdout.write('\\n')\n"
        "    return 0\n"
    )
    members = [
        ("completion-verify", "gate", _write_fake(tmp_path, "gate", body)),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    obj = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert obj == {"decision": "block", "reason": "[praxis:gate]"}


# --------------------------------------------------------------------------- #
# argv protocol: matcher-less groups (issue #1199 review)
# --------------------------------------------------------------------------- #

def test_main_maps_no_matcher_sentinel_to_none(monkeypatch):
    # The build renders NO_MATCHER_ARG into the dispatcher command for
    # matcher-less groups; main() must map it back to None. An f-string render
    # of None would have passed the literal "None", which matches no manifest
    # entry — errorlessly resolving an empty group.
    captured: dict = {}

    def fake_run_group(event, matcher, payload_raw, host=None):
        captured.update(event=event, matcher=matcher, host=host)
        return 0

    monkeypatch.setattr(_dispatch, "run_group", fake_run_group)
    monkeypatch.setattr(sys, "argv", ["_dispatch.py", "Stop", _dispatch.NO_MATCHER_ARG, "claude"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(STOP_PAYLOAD))
    assert _dispatch.main() == 0
    assert captured == {"event": "Stop", "matcher": None, "host": "claude"}


def test_main_argv_path_runs_matcherless_stop_group_end_to_end(tmp_path, monkeypatch, capsys):
    # Full argv path: manifest Stop entries carry NO matcher key, so the argv
    # sentinel must resolve them and the member's block must come out — the
    # regression case where a literal "None" matcher silently disabled the
    # whole group.
    _write_fake(tmp_path / "completion-verify", "gate", _fake_stop_block("blocked"))
    _patch_manifest(tmp_path, monkeypatch, [
        {"name": "gate", "role": "completion-verify", "event": "Stop", "timeout": 10},
    ])
    monkeypatch.setattr(_dispatch, "_HOOKS_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["_dispatch.py", "Stop", _dispatch.NO_MATCHER_ARG])
    monkeypatch.setattr(sys, "stdin", io.StringIO(STOP_PAYLOAD))
    rc = _dispatch.main()
    obj = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert obj == {"decision": "block", "reason": "[praxis:gate] blocked"}


# --------------------------------------------------------------------------- #
# args-declaring member guard (issue #1169)
# --------------------------------------------------------------------------- #

def _patch_manifest(tmp_path: Path, monkeypatch, hooks: list[dict]) -> None:
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps({"hooks": hooks}))
    monkeypatch.setattr(_dispatch, "_MANIFEST", mf)


def test_args_declaring_member_is_excluded_silently(tmp_path, monkeypatch, capsys):
    # Dispatch groups invoke members with stdin only; a member whose manifest
    # entry declares "args" would silently run with its argv dropped. It is
    # excluded at group-resolution time. Silently (issue #1281): the build
    # keeps such a member as its own standalone node beside the dispatcher
    # node (#1199) and Rule 14 fails the build if that node is missing, so
    # the exclusion is the designed arrangement — and under the Stop group
    # the former stderr line would have reached the user on every turn.
    _patch_manifest(tmp_path, monkeypatch, [
        {"name": "argsy", "role": "completion-verify", "body": "impl.sh",
         "event": "Stop", "args": ["stop"], "timeout": 5},
        {"name": "plain", "role": "completion-verify", "event": "Stop",
         "timeout": 5},
    ])
    members = _dispatch.group_members("Stop", None)
    err = capsys.readouterr().err
    assert [n for _r, n, _i in members] == ["plain"]
    assert err == ""


def test_args_declaring_member_group_still_fails_open(tmp_path, monkeypatch, capsys):
    # A group containing only an args-declaring member still runs end to end
    # and never blocks on its account: the member is excluded, nothing is
    # left, and the group is a silent allow on both streams.
    _patch_manifest(tmp_path, monkeypatch, [
        {"name": "argsy", "role": "completion-verify", "body": "impl.sh",
         "event": "Stop", "args": ["stop"], "timeout": 5},
    ])
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""  # no member left -> silent allow, never a block
    assert captured.err == ""


def test_real_stop_group_keeps_strike_counter_standalone():
    # The live arrangement the two fakes above model: strike-counter (args)
    # is outside the group and holds its own node in every platform hooks.json
    # next to the one Stop dispatcher node (Rule 14 asserts the node shape;
    # this pins the runtime half).
    names = [n for _r, n, _i in _dispatch.group_members("Stop", None, host="claude")]
    assert "strike-counter" not in names
    assert names[:2] == ["completion-verify", "retrospect-mix-check"]  # shell members
    assert len(names) == 12


# --------------------------------------------------------------------------- #
# shell members (body: impl.sh) run as a subprocess (issue #1281)
# --------------------------------------------------------------------------- #

def _write_fake_sh(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "impl.sh"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return p


def test_shell_member_block_reaches_the_stop_lane(tmp_path, monkeypatch, capsys):
    # The jq-shaped block the two shell Stop gates emit, produced by a shell
    # member run through run_one, is recognized and attributed like a Python
    # member's. The child sees the payload on stdin (it echoes the session id
    # back into the reason to prove it).
    members = [
        ("completion-verify", "shelly", _write_fake_sh(
            tmp_path, "shelly",
            'payload=$(cat)\n'
            'case "$payload" in *test-dispatch-stop*) sid=test-dispatch-stop;; *) sid=none;; esac\n'
            'printf \'{"decision": "block", "reason": "seen %s"}\\n\' "$sid"\n'
        )),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out) == {
        "decision": "block",
        "reason": "[praxis:shelly] seen test-dispatch-stop",
    }


def test_shell_member_child_has_its_own_ledger_arming_disabled(tmp_path, monkeypatch):
    # The group records a shell member's fire from the returned triple; the
    # child's own record_fire.sh arming must be off or the audit that reads
    # the ledger counts every shell fire twice (issue #848 shape).
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    impl = _write_fake_sh(tmp_path, "envy", 'printf "%s" "${PRAXIS_FIRE_TELEMETRY_DISABLE:-unset}" >&2\n')
    rc, so, se = _dispatch.run_one("completion-verify", "envy", impl, STOP_PAYLOAD)
    assert (rc, so, se) == (0, "", "1")


def test_shell_member_past_its_deadline_fails_open(tmp_path, monkeypatch):
    # The member deadline IS the subprocess timeout: a shell member that
    # outlives it is killed and reported as a pass with a stderr note — never
    # a block nobody decided, never a silent disappearance. The deadline here
    # is short enough to be sure the kill is what ends the child, and the
    # elapsed bound is what proves it (the sleep alone would take 30s).
    impl = _write_fake_sh(tmp_path, "sleepy", "sleep 30\nexit 2\n")
    t0 = time.monotonic()
    rc, so, se = _dispatch.run_one(
        "completion-verify", "sleepy", impl, STOP_PAYLOAD,
        deadline=time.monotonic() + 0.6,
    )
    assert time.monotonic() - t0 < 5
    assert rc == 0 and so == ""
    assert "[dispatch] completion-verify/sleepy" in se and "fail-open" in se


def test_shell_member_missing_impl_passes_silently(tmp_path):
    # The launcher's `[ -f "$IMPL" ] || exit 0` contract (issue #1053), kept
    # under the dispatcher: a partial install is a pass, not exit 2.
    impl = tmp_path / "ghost" / "impl.sh"
    assert _dispatch.run_one("completion-verify", "ghost", impl, STOP_PAYLOAD) == (0, "", "")


def test_shell_member_exit_2_is_a_block_like_any_member(tmp_path, monkeypatch, capsys):
    # Exit 2 stays event-agnostic for a shell member too (exit codes cannot be
    # faked by quoted text), and on Stop it does not silence later members.
    members = [
        ("completion-verify", "two", _write_fake_sh(tmp_path, "two", "cat >/dev/null\nexit 2\n")),
        ("completion-verify", "later", _write_fake(tmp_path, "later", _FAKE_ADVISORY)),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    captured = capsys.readouterr()
    assert rc == 2
    assert "nudge" in captured.err  # the member after the exit-2 one still ran


# --------------------------------------------------------------------------- #
# Stop advisory lane: systemMessage merge (issue #1281)
# --------------------------------------------------------------------------- #

def _fake_stop_advisory(message: str) -> str:
    # Mirrors _hook_io.emit_stop_advisory: a top-level systemMessage at exit 0.
    return (
        "import json, sys\n"
        "def main():\n"
        "    json.dump({'systemMessage': %r}, sys.stdout)\n"
        "    sys.stdout.write('\\n')\n"
        "    return 0\n" % message
    )


def test_stop_advisory_alone_is_forwarded_and_attributed(tmp_path, monkeypatch, capsys):
    # The regression the Stop group waited on: a grouped member's
    # systemMessage used to fall through to the context merge and vanish.
    members = [
        ("completion-verify", "adv", _write_fake(tmp_path, "adv", _fake_stop_advisory("mind the gap"))),
        ("completion-verify", "quiet", _write_fake(tmp_path, "quiet", _FAKE_PASS)),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out) == {"systemMessage": "[praxis:adv] mind the gap"}


def test_stop_advisories_merge_into_one_message(tmp_path, monkeypatch, capsys):
    members = [
        ("completion-verify", "a1", _write_fake(tmp_path, "a1", _fake_stop_advisory("one"))),
        ("completion-verify", "a2", _write_fake(tmp_path, "a2", _fake_stop_advisory("[praxis:a2] two"))),
    ]
    _patch_members(monkeypatch, members)
    _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    out = json.loads(capsys.readouterr().out)
    # Already-attributed text is not tagged twice; the join is a blank line,
    # the same shape as the merged block reason.
    assert out == {"systemMessage": "[praxis:a1] one\n\n[praxis:a2] two"}


def test_stop_advisory_rides_on_a_sibling_block(tmp_path, monkeypatch, capsys):
    # `systemMessage` is a field common to every hook event, so the host
    # reads it and the block from the ONE object it accepts; the advisory is
    # neither dropped by the block nor emitted as a second JSON object.
    members = [
        ("completion-verify", "adv", _write_fake(tmp_path, "adv", _fake_stop_advisory("fyi"))),
        ("completion-verify", "blk", _write_fake(tmp_path, "blk", _fake_stop_block("no evidence"))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out) == {
        "decision": "block",
        "reason": "[praxis:blk] no evidence",
        "systemMessage": "[praxis:adv] fyi",
    }
    assert list(json.loads(out)) == ["decision", "reason", "systemMessage"]


def test_stop_advisory_is_parsed_not_probed(tmp_path, monkeypatch, capsys):
    # Prose that mentions the key, an empty message, and a non-string message
    # are not advisories; only a parsed non-empty string is forwarded.
    members = [
        ("completion-verify", "prose", _write_fake(tmp_path, "prose", _fake_raw_stdout('systemMessage: "x"'))),
        ("completion-verify", "empty", _write_fake(tmp_path, "empty", _fake_stop_advisory(""))),
        ("completion-verify", "num", _write_fake(tmp_path, "num", _fake_raw_stdout('{"systemMessage": 7}'))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("Stop", None, STOP_PAYLOAD)
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_stop_advisory_is_scoped_to_stop(tmp_path, monkeypatch, capsys):
    # On another event a top-level systemMessage is not this dispatcher's
    # answer (the lane is scoped like the block lane, issue #1199 review).
    members = [
        ("advisory-nudge", "adv", _write_fake(tmp_path, "adv", _fake_stop_advisory("fyi"))),
    ]
    _patch_members(monkeypatch, members)
    rc = _dispatch.run_group("PostToolUse", "Bash", NOOP_PAYLOAD)
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_stop_advisory_is_recorded_as_advise_in_the_ledger():
    # A Stop advisory lives on stdout, not stderr, so the stderr-based
    # classification recorded it as "pass" — invisible to the prune audit.
    import _fire_ledger
    advisory = json.dumps({"systemMessage": "fyi"}) + "\n"
    assert _fire_ledger.classify_decision(0, advisory, "", "Stop") == "advise"
    assert _fire_ledger.classify_decision(0, advisory, "", "PostToolUse") == "pass"
    block = json.dumps({"decision": "block", "reason": "r", "systemMessage": "fyi"})
    assert _fire_ledger.classify_decision(0, block, "", "Stop") == "block"


# --------------------------------------------------------------------------- #
# shared transcript parse across Stop members (issue #1281)
# --------------------------------------------------------------------------- #

def test_run_group_enables_the_turn_memo_only_for_its_span(tmp_path, monkeypatch):
    import _transcript
    seen: list[object] = []
    probe = _write_fake(tmp_path, "probe", (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "import _transcript\n"
        "def main():\n"
        "    sys.stderr.write('memo=%%s\\n' %% (_transcript._TURN_MEMO is not None))\n"
        "    return 0\n" % str(LIB)
    ))
    _patch_members(monkeypatch, [("completion-verify", "probe", probe)])
    assert _transcript._TURN_MEMO is None
    saved = sys.stderr
    sys.stderr = io.StringIO()
    try:
        _dispatch.run_group("Stop", None, STOP_PAYLOAD)
        seen.append(sys.stderr.getvalue())
    finally:
        sys.stderr = saved
    assert "memo=True" in seen[0]
    assert _transcript._TURN_MEMO is None  # dropped on the way out


# --------------------------------------------------------------------------- #
# latency (informational guard, not byte-exact)
# --------------------------------------------------------------------------- #

def test_group_latency_under_threshold():
    # one full group pass should be far under the 37-process parallel baseline (~1.87s)
    t0 = time.time()
    _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    elapsed = time.time() - t0
    assert elapsed < 1.0, f"group pass took {elapsed*1000:.0f}ms, expected < 1000ms"
