"""Tests for the hook fire-rate ledger (issue #710).

Two surfaces:
  1. Writer — hooks/_lib/_fire_ledger.py: classify_decision() precedence and
     record_group_fires() JSONL output / opt-out / fail-open.
  2. Reader — skills/bypass-review/bypass-review fire-rate mode: aggregate_fires,
     bash_group_roster, and end-to-end report rendering from synthetic fixtures.

Field names in the fixtures are produced by the writer under test, so the
reader half is verified against the writer's real output (no SUT-mirrored mock):
test_writer_reader_roundtrip feeds record_group_fires output straight into the
CLI loader.

Run: python3 -m pytest tests/test_fire_ledger.py -q
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _load(modname: str, path: Path):
    # SourceFileLoader (not spec_from_file_location) so the extensionless
    # `bypass-review` CLI loads — file-suffix inference returns no loader for it.
    loader = SourceFileLoader(modname, str(path))
    spec = importlib.util.spec_from_loader(modname, loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


fl = _load("_fire_ledger", _REPO / "hooks" / "_lib" / "_fire_ledger.py")
cli = _load("bypass_review_cli", _REPO / "skills" / "bypass-review" / "bypass-review")

_DENY = '{"hookSpecificOutput": {"permissionDecision": "deny"}}'
_ASK = '{"hookSpecificOutput": {"permissionDecision": "ask"}}'


# ---------------------------------------------------------------------------
# Writer: classify_decision precedence (the load-bearing logic)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rc,stdout,stderr,expected", [
    (2, "", "", "block"),                 # exit 2 -> block
    (0, _DENY, "", "block"),              # deny marker -> block
    (2, "", "an advisory nudge", "block"),  # exit 2 wins over stderr
    (0, _ASK, "", "ask"),                 # ask marker -> ask
    (0, _ASK, "nudge", "ask"),            # ask wins over advise
    (0, "", "an advisory nudge", "advise"),  # stderr -> advise
    (0, "", "", "pass"),                  # silent allow -> pass
    (0, "", "   \n  ", "pass"),           # whitespace-only stderr -> pass
])
def test_classify_decision(rc, stdout, stderr, expected):
    assert fl.classify_decision(rc, stdout, stderr) == expected


def test_block_is_not_misread_as_pass():
    """Falsification: a blocking hook (exit 2) must NOT record as pass.

    This is the smallest check that fails if the decision precedence regresses
    to 'exit code ignored' — the whole ledger would then under-count blocks.
    """
    assert fl.classify_decision(2, "", "") != "pass"


# ---------------------------------------------------------------------------
# Writer: record_group_fires JSONL output
# ---------------------------------------------------------------------------

def _payload(session="sess-1", tool="Bash") -> str:
    return json.dumps({"session_id": session, "tool_name": tool, "tool_input": {"command": "ls"}})


def test_record_group_fires_writes_one_line_per_member(tmp_path, monkeypatch):
    out = tmp_path / "fire-events.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)

    members = [
        ("preflight-gate", "block-foo", Path("x")),
        ("advisory-nudge", "nudge-bar", Path("y")),
    ]
    results = [(2, "", ""), (0, "", "a nudge")]
    fl.record_group_fires(members, results, _payload("sess-9", "Bash"))

    lines = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0] == {
        "timestamp": lines[0]["timestamp"],  # opaque; presence checked below
        "session_id": "sess-9", "tool": "Bash",
        "hook": "block-foo", "role": "preflight-gate", "decision": "block",
        "granularity": "rich",
    }
    assert lines[0]["timestamp"]  # non-empty ISO timestamp
    assert lines[1]["hook"] == "nudge-bar"
    assert lines[1]["decision"] == "advise"


def test_opt_out_writes_nothing(tmp_path, monkeypatch):
    out = tmp_path / "fire-events.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_DISABLE", "1")
    fl.record_group_fires([("r", "h", Path("x"))], [(0, "", "")], _payload())
    assert not out.exists()


def test_malformed_payload_still_records(tmp_path, monkeypatch):
    """A non-JSON payload degrades session/tool to '' but still logs the fire."""
    out = tmp_path / "fire-events.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    fl.record_group_fires([("r", "h", Path("x"))], [(0, "", "")], "not json{")
    rec = json.loads(out.read_text().splitlines()[0])
    assert rec["session_id"] == "" and rec["tool"] == "" and rec["hook"] == "h"


# ---------------------------------------------------------------------------
# Reader: aggregate + roster + end-to-end report
# ---------------------------------------------------------------------------

def test_aggregate_fires_counts_by_decision():
    events = [
        {"hook": "a", "role": "preflight-gate", "decision": "block", "session_id": "s1", "timestamp": "2026-06-26T01:00:00+00:00"},
        {"hook": "a", "role": "preflight-gate", "decision": "pass", "session_id": "s2", "timestamp": "2026-06-26T02:00:00+00:00"},
        {"hook": "b", "role": "advisory-nudge", "decision": "advise", "session_id": "s1", "timestamp": "2026-06-26T03:00:00+00:00"},
    ]
    agg = cli.aggregate_fires(events)
    assert agg["a"]["fires"] == 2
    assert agg["a"]["block"] == 1 and agg["a"]["pass"] == 1
    assert agg["a"]["sessions"] == {"s1", "s2"}
    assert agg["a"]["last_seen"] == "2026-06-26T02:00:00+00:00"
    assert agg["b"]["advise"] == 1


def test_bash_group_roster_filters_to_pretooluse_bash(tmp_path):
    manifest = {
        "hooks": [
            {"name": "bash-gate", "role": "preflight-gate", "event": "PreToolUse", "matcher": "Bash"},
            {"name": "stop-gate", "role": "completion-verify", "event": "Stop", "matcher": ""},
            {"name": "multi", "role": "preflight-gate", "entries": [
                {"event": "UserPromptSubmit", "matcher": ""},
                {"event": "PreToolUse", "matcher": "Bash", "file": "impl.py"},
            ]},
        ]
    }
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest))
    roster = cli.bash_group_roster(mpath)
    assert roster == {"bash-gate", "multi"}


def test_bash_group_roster_missing_manifest_returns_none(tmp_path):
    assert cli.bash_group_roster(tmp_path / "nope.json") is None


def test_bash_group_roster_malformed_manifest_does_not_crash(tmp_path):
    """A valid-JSON-but-misstructured manifest skips bad items, never raises."""
    manifest = {"hooks": [
        "not-a-dict",                                       # non-dict hook
        {"name": "good", "event": "PreToolUse", "matcher": "Bash"},
        {"name": "bad-entries", "entries": "not-a-list"},   # non-list entries
        {"name": "bad-entry-item", "entries": ["not-a-dict"]},
    ]}
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest))
    # Skips the three malformed items, keeps the one valid Bash hook.
    assert cli.bash_group_roster(mpath) == {"good"}


def test_writer_reader_roundtrip(tmp_path, monkeypatch):
    """End-to-end: writer output -> CLI fire-rate report, no mirrored mock.

    record_group_fires writes today's fire-events file; the CLI loads it via
    --dir and renders. The 'never fired' set uses a synthetic manifest roster.
    """
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    telem_dir = tmp_path / "telemetry"
    telem_dir.mkdir()
    out = telem_dir / f"fire-events-{today}.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)

    members = [
        ("preflight-gate", "block-foo", Path("x")),
        ("advisory-nudge", "nudge-bar", Path("y")),
    ]
    fl.record_group_fires(members, [(2, "", ""), (0, "", "n")], _payload())

    manifest = {"hooks": [
        {"name": "block-foo", "role": "preflight-gate", "event": "PreToolUse", "matcher": "Bash"},
        {"name": "nudge-bar", "role": "advisory-nudge", "event": "PreToolUse", "matcher": "Bash"},
        {"name": "silent-gate", "role": "preflight-gate", "event": "PreToolUse", "matcher": "Bash"},
    ]}
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest))

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["fire-rate", "--dir", str(telem_dir), "--manifest", str(mpath)])
    report = buf.getvalue()

    assert rc == 0
    assert "block-foo" in report and "nudge-bar" in report
    assert "Hooks fired : 2" in report
    # silent-gate is in the roster but never fired -> appears in Never Fired set
    assert "silent-gate" in report


def test_fire_rate_empty_window(tmp_path, capsys):
    rc = cli.main(["fire-rate", "--dir", str(tmp_path)])
    assert rc == 0
    assert "No fire events found" in capsys.readouterr().out


def test_default_mode_still_bypass(tmp_path, capsys):
    """Back-compat: no positional arg keeps the original bypass report."""
    rc = cli.main(["--dir", str(tmp_path)])
    assert rc == 0
    assert "Bypass Telemetry Report" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Coverage expansion (issue #710): standalone coarse recording + fail_open wiring
# ---------------------------------------------------------------------------

def test_record_standalone_fire_writes_coarse(tmp_path, monkeypatch):
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    monkeypatch.setattr(fl, "_DISPATCHER_PROCESS", False)
    fl.record_standalone_fire("stop-gate", "completion-verify", 2)
    fl.record_standalone_fire("nudge", "advisory-nudge", 0)
    recs = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(recs) == 2
    assert recs[0]["decision"] == "block" and recs[0]["granularity"] == "coarse"
    assert recs[0]["hook"] == "stop-gate" and recs[0]["session_id"] == ""
    # rc 0 collapses to pass (coarse cannot distinguish ask/advise/pass)
    assert recs[1]["decision"] == "pass"


def test_record_standalone_fire_skipped_in_dispatcher(tmp_path, monkeypatch):
    """In the dispatcher process, coarse recording is suppressed (no double-count)."""
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.setattr(fl, "_DISPATCHER_PROCESS", True)
    fl.record_standalone_fire("x", "y", 2)
    assert not out.exists()


def test_record_standalone_fire_opt_out(tmp_path, monkeypatch):
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_DISABLE", "1")
    monkeypatch.setattr(fl, "_DISPATCHER_PROCESS", False)
    fl.record_standalone_fire("x", "y", 0)
    assert not out.exists()


def test_roster_split_separates_shell_hooks(tmp_path):
    manifest = {"hooks": [
        {"name": "a", "event": "PreToolUse", "matcher": "Bash"},   # py (instrumentable)
        {"name": "b", "event": "Stop"},                            # py (instrumentable)
        {"name": "sh1", "event": "Stop", "body": "impl.sh"},       # shell (uninstrumentable)
        "not-a-dict",
        {"event": "Stop"},  # no name -> skipped
    ]}
    mpath = tmp_path / "m.json"
    mpath.write_text(json.dumps(manifest))
    instrumentable, uninstrumentable = cli.roster_split(mpath)
    assert instrumentable == {"a", "b"}
    assert uninstrumentable == {"sh1"}


def _reset_real_dispatcher_flag(monkeypatch):
    """Reset _DISPATCHER_PROCESS on the SAME _fire_ledger that fail_open imports.

    fail_open's _maybe_record_fire does `import _fire_ledger` (sys.modules), while
    the test's `fl` is a separate SourceFileLoader instance. In the full suite a
    sibling test that runs the dispatcher in-process can flip that shared flag to
    True, suppressing coarse recording here. Reset the real module (auto-restored).
    """
    import importlib
    lib = str(_REPO / "hooks" / "_lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    real_fl = importlib.import_module("_fire_ledger")
    monkeypatch.setattr(real_fl, "_DISPATCHER_PROCESS", False)


def test_fail_open_records_coarse_fire_and_preserves_return(tmp_path, monkeypatch):
    """The universal @fail_open decorator records a coarse fire for a standalone hook."""
    hr = _load("_hook_runtime", _REPO / "hooks" / "_lib" / "_hook_runtime.py")
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    _reset_real_dispatcher_flag(monkeypatch)

    @hr.fail_open
    def blocking_main() -> int:
        return 2

    rc = blocking_main()
    assert rc == 2  # return value unchanged by instrumentation
    recs = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(recs) == 1
    assert recs[0]["decision"] == "block" and recs[0]["granularity"] == "coarse"


def test_fail_open_swallows_exception_and_still_records(tmp_path, monkeypatch):
    hr = _load("_hook_runtime_exc", _REPO / "hooks" / "_lib" / "_hook_runtime.py")
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    _reset_real_dispatcher_flag(monkeypatch)

    @hr.fail_open
    def boom() -> int:
        raise RuntimeError("kaboom")

    rc = boom()
    assert rc == 0  # exception -> fail-open 0
    recs = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert recs and recs[0]["decision"] == "pass"  # rc 0 after swallow


def test_dispatcher_process_writes_rich_not_coarse(tmp_path, monkeypatch):
    """In the dispatcher process the rich record persists and the coarse path is
    suppressed — locks the no-double-count contract at the role boundary."""
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    monkeypatch.setattr(fl, "_DISPATCHER_PROCESS", False)
    fl.mark_dispatcher_process()  # run_group calls this at entry
    fl.record_group_fires([("preflight-gate", "h", Path("x"))], [(2, "", "")], _payload())
    fl.record_standalone_fire("h", "preflight-gate", 2)  # member fail_open — suppressed
    recs = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(recs) == 1 and recs[0]["granularity"] == "rich"


def test_atomic_append_skips_non_regular_file(tmp_path):
    """Security guard: a FIFO target is skipped, never opened (no block, no raise)."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    fl._atomic_append(fifo, ['{"x":1}'])  # must return without blocking/raising
    assert stat.S_ISFIFO(os.lstat(fifo).st_mode)  # untouched, still a FIFO


def test_fail_open_records_on_systemexit_and_reraises(tmp_path, monkeypatch):
    """sys.exit() inside main() raises SystemExit (BaseException) — telemetry must
    still record (from the exit code) and the exit must propagate (CodeRabbit)."""
    hr = _load("_hook_runtime_se", _REPO / "hooks" / "_lib" / "_hook_runtime.py")
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    _reset_real_dispatcher_flag(monkeypatch)

    @hr.fail_open
    def exits() -> int:
        raise SystemExit(2)

    with pytest.raises(SystemExit) as ei:
        exits()
    assert ei.value.code == 2  # exit semantics preserved
    recs = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert recs and recs[0]["decision"] == "block"  # exit 2 -> block


def test_roster_split_skips_non_string_name(tmp_path):
    """A non-string `name` must be skipped, else render's sorted() raises (CodeRabbit)."""
    manifest = {"hooks": [{"name": 1}, {"name": "ok", "event": "Stop"}]}
    mpath = tmp_path / "m.json"
    mpath.write_text(json.dumps(manifest))
    instrumentable, _ = cli.roster_split(mpath)
    assert instrumentable == {"ok"} and 1 not in instrumentable


def test_aggregate_marks_coarse_hooks():
    events = [
        {"hook": "c", "role": "completion-verify", "decision": "pass",
         "granularity": "coarse", "session_id": "", "timestamp": "2026-06-26T01:00:00+00:00"},
        {"hook": "r", "role": "preflight-gate", "decision": "block",
         "granularity": "rich", "session_id": "s1", "timestamp": "2026-06-26T02:00:00+00:00"},
    ]
    agg = cli.aggregate_fires(events)
    assert agg["c"]["coarse"] is True
    assert agg["r"]["coarse"] is False


# ---------------------------------------------------------------------------
# issue #710 remaining scope: advise_ignored_rate
# ---------------------------------------------------------------------------

def test_advise_ignored_counts_recurrence_without_escalation():
    """advise@0's next fire is advise@10 (ignored); advise@10's next fire is
    block@20 (escalated, not ignored) -> observed=2, ignored=1, rate=0.5."""
    events = [
        {"hook": "h", "session_id": "s1", "decision": "advise", "timestamp": "2026-06-26T00:00:00+00:00"},
        {"hook": "h", "session_id": "s1", "decision": "advise", "timestamp": "2026-06-26T00:00:10+00:00"},
        {"hook": "h", "session_id": "s1", "decision": "block", "timestamp": "2026-06-26T00:00:20+00:00"},
    ]
    result = cli.compute_advise_ignored(events)
    row = result["h"]
    assert row["advise_fires"] == 2
    assert row["observed"] == 2
    assert row["ignored"] == 1
    assert row["rate"] == 0.5


def test_advise_ignored_escalation_to_block_is_not_ignored():
    events = [
        {"hook": "h", "session_id": "s1", "decision": "advise", "timestamp": "2026-06-26T00:00:00+00:00"},
        {"hook": "h", "session_id": "s1", "decision": "block", "timestamp": "2026-06-26T00:00:10+00:00"},
    ]
    result = cli.compute_advise_ignored(events)
    row = result["h"]
    assert row["observed"] == 1
    assert row["ignored"] == 0
    assert row["rate"] == 0.0


def test_advise_ignored_last_fire_in_session_is_right_censored():
    """A trailing advise with no follow-up in the session is excluded from
    the observed denominator — it must not be silently counted either way."""
    events = [
        {"hook": "h", "session_id": "s1", "decision": "advise", "timestamp": "2026-06-26T00:00:00+00:00"},
    ]
    result = cli.compute_advise_ignored(events)
    row = result["h"]
    assert row["advise_fires"] == 1
    assert row["observed"] == 0
    assert row["ignored"] == 0
    assert row["rate"] is None


def test_advise_ignored_scoped_per_session_not_cross_session():
    """The 'next fire' lookup must not cross session boundaries — a fire in
    session s2 must never resolve session s1's advise outcome."""
    events = [
        {"hook": "h", "session_id": "s1", "decision": "advise", "timestamp": "2026-06-26T00:00:00+00:00"},
        {"hook": "h", "session_id": "s2", "decision": "block", "timestamp": "2026-06-26T00:00:05+00:00"},
    ]
    result = cli.compute_advise_ignored(events)
    row = result["h"]
    assert row["observed"] == 0  # s1's lone advise has no same-session follow-up
    assert row["rate"] is None


def test_advise_ignored_pass_after_advise_is_heeded_not_ignored():
    """A 'pass' after 'advise' means the hook stopped flagging — the call
    changed enough to satisfy it. Must NOT be counted as ignored (codex
    review finding, praxis PR): counting pass as ignored inflates the rate
    on the exact case the metric exists to distinguish from real recurrence."""
    events = [
        {"hook": "h", "session_id": "s1", "decision": "advise", "timestamp": "2026-06-26T00:00:00+00:00"},
        {"hook": "h", "session_id": "s1", "decision": "pass", "timestamp": "2026-06-26T00:00:10+00:00"},
    ]
    result = cli.compute_advise_ignored(events)
    row = result["h"]
    assert row["observed"] == 1
    assert row["ignored"] == 0
    assert row["rate"] == 0.0


# ---------------------------------------------------------------------------
# issue #710 remaining scope: bypass_count join
# ---------------------------------------------------------------------------

def test_join_bypass_to_hooks_unique_family_match():
    fire_events = [
        {"hook": "protected-paths-guard", "session_id": "s1", "timestamp": "2026-06-26T00:00:00+00:00"},
    ]
    bypass_events = [
        {"session_id": "s1", "timestamp": "2026-06-26T00:00:05+00:00",
         "bypass_env_vars": ["PRAXIS_HOOK_BYPASS_PROTECTED_PATHS"]},
    ]
    result = cli.join_bypass_to_hooks(fire_events, bypass_events)
    assert result["protected-paths-guard"]["bypass_count"] == 1
    assert result["protected-paths-guard"]["sessions"] == {"s1"}
    assert cli.UNATTRIBUTED not in result


def test_join_bypass_to_hooks_no_matching_hook_in_session_is_unattributed():
    """A bypass var with no hook of that family firing in the session must
    be counted, not silently dropped."""
    fire_events = [
        {"hook": "unrelated-hook", "session_id": "s1", "timestamp": "2026-06-26T00:00:00+00:00"},
    ]
    bypass_events = [
        {"session_id": "s1", "timestamp": "2026-06-26T00:00:05+00:00",
         "bypass_env_vars": ["PRAXIS_HOOK_BYPASS_PROTECTED_PATHS"]},
    ]
    result = cli.join_bypass_to_hooks(fire_events, bypass_events)
    assert "protected-paths-guard" not in result
    assert result[cli.UNATTRIBUTED]["bypass_count"] == 1


def test_join_bypass_to_hooks_ambiguous_family_resolved_by_nearest_timestamp():
    """Two hooks in the same session both subset-match the family tokens;
    the one whose fire timestamp is closer to the bypass event wins."""
    fire_events = [
        {"hook": "push-verify-a", "session_id": "s1", "timestamp": "2026-06-26T00:00:00+00:00"},
        {"hook": "push-verify-b", "session_id": "s1", "timestamp": "2026-06-26T00:10:00+00:00"},
    ]
    bypass_events = [
        {"session_id": "s1", "timestamp": "2026-06-26T00:00:02+00:00",
         "bypass_env_vars": ["PRAXIS_PUSH_VERIFY_BYPASS"]},
    ]
    result = cli.join_bypass_to_hooks(fire_events, bypass_events)
    assert result["push-verify-a"]["bypass_count"] == 1
    assert "push-verify-b" not in result


def test_join_bypass_to_hooks_session_scoped():
    """A bypass event must only match hooks that fired in the SAME session."""
    fire_events = [
        {"hook": "protected-paths-guard", "session_id": "s2", "timestamp": "2026-06-26T00:00:00+00:00"},
    ]
    bypass_events = [
        {"session_id": "s1", "timestamp": "2026-06-26T00:00:05+00:00",
         "bypass_env_vars": ["PRAXIS_HOOK_BYPASS_PROTECTED_PATHS"]},
    ]
    result = cli.join_bypass_to_hooks(fire_events, bypass_events)
    assert "protected-paths-guard" not in result
    assert result[cli.UNATTRIBUTED]["bypass_count"] == 1


def test_match_family_to_hooks_subset_semantics():
    hooks = {"protected-paths-guard", "destructive-bash-guard"}
    assert cli.match_family_to_hooks("PROTECTED_PATHS", hooks) == {"protected-paths-guard"}
    assert cli.match_family_to_hooks("DESTRUCTIVE_BASH", hooks) == {"destructive-bash-guard"}
    assert cli.match_family_to_hooks("NOTHING_MATCHES", hooks) == set()


# ---------------------------------------------------------------------------
# issue #710 remaining scope: exact manifest mode.bypass_env mapping
# (codex review finding, praxis PR: the token heuristic alone sent
# CLAUDE_HOOK_BYPASS_DUP_GATE to '(unattributed)' even though the manifest
# already declares its exact owning hook)
# ---------------------------------------------------------------------------

def test_bypass_env_exact_map_reads_manifest_declaration(tmp_path):
    manifest = {"hooks": [
        {"name": "block-gh-issue-create-without-dup-search",
         "mode": {"bypass_env": ["CLAUDE_HOOK_BYPASS_DUP_GATE"]}},
        {"name": "no-bypass-hook"},
    ]}
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest))
    result = cli.bypass_env_exact_map(mpath)
    assert result == {"CLAUDE_HOOK_BYPASS_DUP_GATE": "block-gh-issue-create-without-dup-search"}


def test_bypass_env_exact_map_excludes_ambiguous_var(tmp_path):
    """A var declared on >1 hook is ambiguous -> excluded (falls through to
    the heuristic) rather than picking one arbitrarily."""
    manifest = {"hooks": [
        {"name": "hook-a", "mode": {"bypass_env": ["PRAXIS_SHARED_BYPASS"]}},
        {"name": "hook-b", "mode": {"bypass_env": ["PRAXIS_SHARED_BYPASS"]}},
    ]}
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest))
    assert cli.bypass_env_exact_map(mpath) == {}


def test_bypass_env_exact_map_missing_manifest_returns_empty(tmp_path):
    assert cli.bypass_env_exact_map(tmp_path / "nope.json") == {}


def test_join_bypass_to_hooks_exact_map_takes_priority_over_heuristic():
    """DUP_GATE's family tokens {dup, gate} do not subset
    'block-gh-issue-create-without-dup-search' (no 'gate' token in the hook
    name) -- the heuristic alone would misfire this to unattributed. The
    exact map must resolve it correctly regardless."""
    fire_events = [
        {"hook": "block-gh-issue-create-without-dup-search", "session_id": "s1",
         "timestamp": "2026-06-26T00:00:00+00:00"},
    ]
    bypass_events = [
        {"session_id": "s1", "timestamp": "2026-06-26T00:00:05+00:00",
         "bypass_env_vars": ["CLAUDE_HOOK_BYPASS_DUP_GATE"]},
    ]
    # Heuristic alone (no exact_map) -> unattributed, proving the bug existed.
    heuristic_only = cli.join_bypass_to_hooks(fire_events, bypass_events)
    assert "block-gh-issue-create-without-dup-search" not in heuristic_only
    assert heuristic_only[cli.UNATTRIBUTED]["bypass_count"] == 1

    # With the exact map -> correctly attributed.
    exact_map = {"CLAUDE_HOOK_BYPASS_DUP_GATE": "block-gh-issue-create-without-dup-search"}
    result = cli.join_bypass_to_hooks(fire_events, bypass_events, exact_map=exact_map)
    assert result["block-gh-issue-create-without-dup-search"]["bypass_count"] == 1
    assert cli.UNATTRIBUTED not in result


def test_join_bypass_to_hooks_exact_map_attributes_even_without_session_fire():
    """Exact-map attribution does not require the hook to have fired in the
    bypass event's session — the manifest is authoritative on hook identity."""
    fire_events: list[dict] = []  # hook never fired in fire-events at all
    bypass_events = [
        {"session_id": "s1", "timestamp": "2026-06-26T00:00:05+00:00",
         "bypass_env_vars": ["CLAUDE_HOOK_BYPASS_DUP_GATE"]},
    ]
    exact_map = {"CLAUDE_HOOK_BYPASS_DUP_GATE": "block-gh-issue-create-without-dup-search"}
    result = cli.join_bypass_to_hooks(fire_events, bypass_events, exact_map=exact_map)
    assert result["block-gh-issue-create-without-dup-search"]["bypass_count"] == 1


def test_bypass_env_exact_map_matches_real_manifest_dup_gate_entry():
    """Falsification against the REAL repo manifest (not a synthetic fixture)
    -- confirms the codex review's cited example still holds in this tree."""
    real_manifest = _REPO / "hooks" / "manifest.json"
    result = cli.bypass_env_exact_map(real_manifest)
    assert result.get("CLAUDE_HOOK_BYPASS_DUP_GATE") == "block-gh-issue-create-without-dup-search"


# ---------------------------------------------------------------------------
# issue #710 remaining scope: outcome-proxy (strike_count, best-effort)
# ---------------------------------------------------------------------------

def test_load_strike_state_reads_count_and_reasons(tmp_path):
    state_dir = tmp_path / "strikes"
    state_dir.mkdir()
    (state_dir / "sess-1.json").write_text(json.dumps({"count": 2, "reasons": ["a", "b"]}))
    state = cli.load_strike_state(state_dir, "sess-1")
    assert state == {"count": 2, "reasons": ["a", "b"]}


def test_load_strike_state_missing_file_returns_none(tmp_path):
    assert cli.load_strike_state(tmp_path / "strikes", "sess-missing") is None


def test_load_strike_state_malformed_json_returns_none(tmp_path):
    state_dir = tmp_path / "strikes"
    state_dir.mkdir()
    (state_dir / "sess-bad.json").write_text("not json{")
    assert cli.load_strike_state(state_dir, "sess-bad") is None


def test_compute_outcome_proxy_joins_fire_sessions_to_strike_state(tmp_path):
    state_dir = tmp_path / "strikes"
    state_dir.mkdir()
    (state_dir / "s1.json").write_text(json.dumps({"count": 3, "reasons": ["x"]}))
    fire_events = [
        {"hook": "h", "session_id": "s1", "timestamp": "2026-06-26T00:00:00+00:00"},
        {"hook": "h", "session_id": "s2", "timestamp": "2026-06-26T00:00:05+00:00"},
    ]
    result = cli.compute_outcome_proxy(fire_events, state_dir)
    assert result["s1"] == {
        "strike_count": 3, "strike_state_available": True,
        "external_write_revert_count": 0,
    }
    assert result["s2"] == {
        "strike_count": 0, "strike_state_available": False,
        "external_write_revert_count": 0,
    }


# issue #737: external-write-revert coarse proxy (destructive-bash-guard
# non-pass fires) — see compute_external_write_revert_counts.

def test_compute_external_write_revert_counts_counts_non_pass_fires():
    fire_events = [
        {"hook": "destructive-bash-guard", "session_id": "s1", "decision": "advise"},
        {"hook": "destructive-bash-guard", "session_id": "s1", "decision": "advise"},
        {"hook": "destructive-bash-guard", "session_id": "s2", "decision": "pass"},
        {"hook": "other-hook", "session_id": "s1", "decision": "advise"},
    ]
    counts = cli.compute_external_write_revert_counts(fire_events)
    assert counts == {"s1": 2}


def test_compute_external_write_revert_counts_ignores_missing_session():
    fire_events = [
        {"hook": "destructive-bash-guard", "session_id": "", "decision": "advise"},
        {"hook": "destructive-bash-guard", "decision": "advise"},
    ]
    assert cli.compute_external_write_revert_counts(fire_events) == {}


def test_compute_outcome_proxy_surfaces_external_write_revert_count(tmp_path):
    state_dir = tmp_path / "strikes"
    state_dir.mkdir()
    fire_events = [
        {"hook": "destructive-bash-guard", "session_id": "s1", "decision": "advise",
         "timestamp": "2026-06-26T00:00:00+00:00"},
    ]
    result = cli.compute_outcome_proxy(fire_events, state_dir)
    assert result["s1"]["external_write_revert_count"] == 1


def test_fire_rate_report_shows_nonzero_external_write_revert_signal(tmp_path, monkeypatch):
    """Acceptance criterion (issue #737): a synthetic fixture that triggers one
    of the 3 new revert-signal patterns (here: `git revert`, represented by the
    real writer's own destructive-bash-guard advise fire) shows a nonzero
    external-write-revert value in the Outcome Proxy section."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    telem_dir = tmp_path / "telemetry"
    telem_dir.mkdir()
    fire_out = telem_dir / f"fire-events-{today}.jsonl"
    state_dir = tmp_path / "strikes"
    state_dir.mkdir()

    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(fire_out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)

    # Real writer output: destructive-bash-guard fires with stderr set, as it
    # does when impl.py detects `git revert` (see _signal_text in impl.py).
    members = [("advisory-nudge", "destructive-bash-guard", Path("x"))]
    fl.record_group_fires(
        members, [(0, "", "[destructive-bash-guard] outcome-proxy signal detected")],
        _payload("s-revert", "Bash"),
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main([
            "fire-rate", "--dir", str(telem_dir), "--state-dir", str(state_dir),
        ])
    report = buf.getvalue()

    assert rc == 0
    assert "Outcome Proxy" in report
    assert "Sessions with external-write-revert signal : 1" in report
    assert "s-revert" in report


def test_default_strike_state_dir_respects_praxis_state_dir_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_STATE_DIR", str(tmp_path))
    assert cli.default_strike_state_dir() == tmp_path / "strikes"


# ---------------------------------------------------------------------------
# issue #710 remaining scope: end-to-end fire-rate report renders new sections
# ---------------------------------------------------------------------------

def test_fire_rate_report_includes_remaining_scope_sections(tmp_path, monkeypatch):
    """Real writer output (record_group_fires + bypass hook's own JSONL schema)
    flows through run_fire_rate end-to-end and produces non-trivial values for
    all three remaining-scope metrics — no mirrored/mocked business logic."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    telem_dir = tmp_path / "telemetry"
    telem_dir.mkdir()
    fire_out = telem_dir / f"fire-events-{today}.jsonl"
    bypass_out = telem_dir / f"bypass-events-{today}.jsonl"
    state_dir = tmp_path / "strikes"
    state_dir.mkdir()

    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(fire_out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)

    # Two dispatches of the same hook in the same session: advise then advise
    # again (ignored), via the real writer.
    members = [("advisory-nudge", "protected-paths-guard", Path("x"))]
    fl.record_group_fires(members, [(0, "", "nudge")], _payload("s1", "Write"))
    fl.record_group_fires(members, [(0, "", "nudge")], _payload("s1", "Write"))

    # bypass-events file uses the real writer's own field schema (session_id,
    # tool, bypass_env_vars, tool_input, tool_result_status).
    bypass_record = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "session_id": "s1",
        "tool": "Write",
        "bypass_env_vars": ["PRAXIS_HOOK_BYPASS_PROTECTED_PATHS"],
        "tool_input": "echo hi",
        "tool_result_status": "ok",
    }
    bypass_out.write_text(json.dumps(bypass_record) + "\n")

    (state_dir / "s1.json").write_text(json.dumps({"count": 1, "reasons": ["late verification"]}))

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main([
            "fire-rate", "--dir", str(telem_dir), "--state-dir", str(state_dir),
        ])
    report = buf.getvalue()

    assert rc == 0
    assert "Advise-Ignored Detail" in report
    assert "protected-paths-guard" in report
    assert "100%" in report  # both advise fires ignored (no escalation)
    assert "Bypass Attribution" in report
    assert "Outcome Proxy" in report
    assert "s1" in report and "1" in report  # strike_count surfaced
