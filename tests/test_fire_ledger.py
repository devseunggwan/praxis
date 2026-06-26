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
