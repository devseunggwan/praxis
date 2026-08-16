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

import json
import subprocess
import sys
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
    assert len(members) == 50, f"expected 50 exact-Bash members, got {len(members)}"
    # every impl path must exist on disk
    for role, name, impl in members:
        assert impl.exists(), f"missing impl for {role}/{name}: {impl}"
    roles = [role for role, _name, _impl in members]
    assert roles.count("preflight-gate") == 27
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

    assert len(unfiltered) == 50  # host=None -> canonical, unfiltered view
    # the only host-restricted Bash members are the 6 claude-only guards
    assert names(claude) == names(unfiltered)
    assert "block-commit-without-codex-review" not in names(codex)
    assert "block-rename-sweep-survivors" not in names(codex)
    assert "pre-commit-staged-file-enumeration" not in names(codex)
    assert "commit-decomposition-advisory" not in names(codex)
    assert "model-routing-advisory" not in names(codex)
    assert "block-unmatched-glob" not in names(codex)
    assert len(codex) == 44


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


def _patch_members(monkeypatch, members):
    # 3-arg signature: run_group calls group_members(event, matcher, host).
    monkeypatch.setattr(_dispatch, "group_members", lambda _e, _m, _h=None: members)


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
# latency (informational guard, not byte-exact)
# --------------------------------------------------------------------------- #

def test_group_latency_under_threshold():
    # one full group pass should be far under the 37-process parallel baseline (~1.87s)
    t0 = time.time()
    _dispatch.run_group("PreToolUse", "Bash", NOOP_PAYLOAD)
    elapsed = time.time() - t0
    assert elapsed < 1.0, f"group pass took {elapsed*1000:.0f}ms, expected < 1000ms"
