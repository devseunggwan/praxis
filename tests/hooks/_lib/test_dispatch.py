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
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
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
    # Exact-`Bash` matcher only. Three advisory hooks (memory-hint,
    # external-api-literal-trigger, block-personal-asset-leak) carry multi-tool
    # matchers (Bash|Edit|Write|…) and are intentionally excluded — see
    # ADR-0002 §2 scope note.
    members = _dispatch.group_members("PreToolUse", "Bash")
    assert len(members) == 49, f"expected 49 exact-Bash members, got {len(members)}"
    # every impl path must exist on disk
    for role, name, impl in members:
        assert impl.exists(), f"missing impl for {role}/{name}: {impl}"
    roles = [role for role, _name, _impl in members]
    assert roles.count("preflight-gate") == 26
    assert roles.count("advisory-nudge") == 23


def test_group_members_host_filter():
    # build-plugin-manifests strips host-restricted hooks per platform; the
    # dispatcher must apply the SAME filter so it never re-includes a hook the
    # build stripped (a Codex install must not run a hosts:["claude"] guard).
    def names(ms):
        return {n for _r, n, _i in ms}

    unfiltered = _dispatch.group_members("PreToolUse", "Bash")
    claude = _dispatch.group_members("PreToolUse", "Bash", host="claude")
    codex = _dispatch.group_members("PreToolUse", "Bash", host="codex")

    assert len(unfiltered) == 49  # host=None -> canonical, unfiltered view
    # the only host-restricted Bash members are the 6 claude-only guards
    assert names(claude) == names(unfiltered)
    assert "block-commit-without-codex-review" not in names(codex)
    assert "block-rename-sweep-survivors" not in names(codex)
    assert "pre-commit-staged-file-enumeration" not in names(codex)
    assert "commit-decomposition-advisory" not in names(codex)
    assert "model-routing-advisory" not in names(codex)
    assert "block-unmatched-glob" not in names(codex)
    assert len(codex) == 43


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
    manifest = json.loads((REPO_ROOT / "hooks" / "manifest.json").read_text())
    return [
        (g["event"], g["matcher"]) for g in manifest.get("dispatch_groups", [])
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


def _noop_payload_for(matcher: str) -> str:
    tool = matcher.split("|")[0]
    return json.dumps(
        {
            "tool_name": tool,
            "tool_input": _NOOP_TOOL_INPUTS[tool],
            "cwd": str(REPO_ROOT),
            "session_id": "test-dispatch-noop",
        }
    )


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
    rc = _dispatch.run_group(event, matcher, _noop_payload_for(matcher))
    assert rc == 0


@pytest.mark.parametrize(
    "matcher,member",
    [
        (m, member)
        for _e, m in NON_BASH_GROUPS
        for member in _dispatch.group_members(_e, m)
    ],
    ids=lambda v: v if isinstance(v, str) else f"{v[0]}/{v[1]}",
)
def test_run_one_matches_subprocess_for_group_noop(matcher, member):
    role, name, impl = member
    payload = _noop_payload_for(matcher)
    direct = subprocess.run(
        [sys.executable, str(impl)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
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


def _sh_env(**extra: str) -> dict:
    env = dict(os.environ)
    # The committed command interpolates ${CLAUDE_PLUGIN_ROOT}; for the claude
    # platform the plugin root is the repo root.
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
    env.pop("PRAXIS_HOOK_BYPASS_PROTECTED_PATHS", None)
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
        input=_noop_payload_for(matcher),
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
    """
    tree = ast.parse(source)
    module_consts = {
        t.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name) and t.id.lstrip("_").isupper()
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
        kw = next((k for k in node.keywords if k.arg == "timeout"), None)
        if kw is None:
            offenders.append(f"line {node.lineno}: no timeout=")
            continue
        names = {n.id for n in ast.walk(kw.value) if isinstance(n, ast.Name)}
        if names <= module_consts:
            offenders.append(f"line {node.lineno}: timeout is fixed")
    return offenders


def test_every_subprocess_member_is_budget_aware():
    # The dispatcher no longer decides per member whether a shortened cap is
    # safe: it clamps every member's deadline to the group's and relies on
    # each member sizing its own subprocess timeouts from the shared budget.
    # That reliance is the invariant here. A member spawning a subprocess
    # under a fixed timeout can outlive the group deadline and get the whole
    # dispatcher killed by the host, and nothing at the call site shows it.
    members, _budget, _timeouts = _dispatch.load_group("PreToolUse", "Bash")
    offenders = {}
    for role, name, impl in members:
        bad = _fixed_timeout_spawns(
            Path(impl).read_text(encoding="utf-8", errors="replace"))
        if bad:
            offenders[f"{role}/{name}"] = bad
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
    assert _fixed_timeout_spawns(fixed) == ["line 4: timeout is fixed"]
    assert _fixed_timeout_spawns(missing) == ["line 3: no timeout="]
    assert _fixed_timeout_spawns(budgeted) == []


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
# latency (informational guard, not byte-exact)
# --------------------------------------------------------------------------- #

def test_group_latency_under_threshold():
    # one full group pass should be far under the 37-process parallel baseline (~1.87s)
    t0 = time.time()
    _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    elapsed = time.time() - t0
    assert elapsed < 1.0, f"group pass took {elapsed*1000:.0f}ms, expected < 1000ms"
