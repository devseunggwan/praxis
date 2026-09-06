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
import subprocess
import sys
from datetime import datetime, timedelta, timezone
import uuid
from contextlib import redirect_stdout
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
# Stop-lane block shape (issue #1169 / PR #1199): top-level decision at exit 0.
_STOP_BLOCK = '{"decision": "block", "reason": "no evidence"}'
# jq's pretty-printed form (shell Stop hooks) parses identically.
_STOP_BLOCK_JQ = '{\n  "decision": "block",\n  "reason": "no evidence"\n}\n'
# The non-blocking half of the same lane (`_hook_io.emit_stop_advisory`).
_STOP_ADVISORY = '{"systemMessage": "mind the gap"}'
# A context payload that merely QUOTES the block shape must NOT classify as a
# block — recognition is parse-based, not substring.
_STOP_QUOTING = (
    '{"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": '
    '"emit {\\"decision\\": \\"block\\"} JSON to block the stop"}}'
)


# ---------------------------------------------------------------------------
# Writer: classify_decision precedence (the load-bearing logic)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rc,stdout,stderr,event,expected", [
    (2, "", "", "Stop", "block"),         # exit 2 -> block
    (0, _DENY, "", "PreToolUse", "block"),  # deny marker -> block
    (2, "", "an advisory nudge", "Stop", "block"),  # exit 2 wins over stderr
    (0, _ASK, "", "PreToolUse", "ask"),   # ask marker -> ask
    (0, _ASK, "nudge", "PreToolUse", "ask"),  # ask wins over advise
    (0, "", "an advisory nudge", "Stop", "advise"),  # stderr -> advise
    (0, "", "", "Stop", "pass"),          # silent allow -> pass
    (0, "", "   \n  ", "Stop", "pass"),   # whitespace-only stderr -> pass
    # issue #1167: dispatcher budget-skip records carry the marker on stderr
    # (with the same note as an additionalContext object on stdout) and must
    # classify as "skip", NOT be mistaken for an advise.
    (0, "", "[dispatch] budget-skip r/n: 0.1s left of the 15s group budget; member not run (fail-open)\n", "Stop", "skip"),
    (0, '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "[dispatch] budget-skip r/n: ..."}}',
     "[dispatch] budget-skip r/n: 0.1s left of the 15s group budget; member not run (fail-open)\n", "Stop", "skip"),
    (0, _STOP_BLOCK, "", "Stop", "block"),  # Stop-lane block JSON at exit 0 -> block
    (0, _STOP_BLOCK_JQ, "", "Stop", "block"),  # jq pretty-printed form -> block
    (0, _STOP_QUOTING, "", "Stop", "pass"),  # QUOTED block shape -> parse says no block
    (0, _STOP_QUOTING, "nudge", "Stop", "advise"),  # quoted shape + stderr stays advise
    # issue #1337: SubagentStop carries a decision in the same top-level shape
    # (the dispatcher accepts it under both events), so the ledger must record
    # it under both too. Gated the same way the dispatcher gates it — under an
    # event with no Stop lane the object is not a decision and stays a pass.
    (0, _STOP_BLOCK, "", "SubagentStop", "block"),
    (0, _STOP_ADVISORY, "", "SubagentStop", "advise"),
    (0, _STOP_BLOCK, "", "PostToolUse", "pass"),
    (0, _STOP_ADVISORY, "", "PostToolUse", "pass"),
])
def test_classify_decision(rc, stdout, stderr, event, expected):
    # `event` is a column rather than a constant: every lane below the exit-2
    # one is event-gated, so a table pinned to one event silently stops
    # exercising the others. The gates' other sides have their own tests —
    # test_stop_block_classification_mirrors_the_dispatcher_gates and
    # test_marker_classification_mirrors_the_dispatcher_gate.
    assert fl.classify_decision(rc, stdout, stderr, event) == expected


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


def test_aggregate_fires_skip_is_tracked_separately_not_as_a_fire():
    """issue #1167 / PR #1195 review: a dispatcher budget-skip record means
    the member never RAN. Folding it into `fires` would inflate the fire-rate
    ledger the hook-prune audits score from, and its session must not count
    as an engaged session."""
    events = [
        {"hook": "a", "role": "preflight-gate", "decision": "pass", "session_id": "s1", "timestamp": "2026-06-26T01:00:00+00:00"},
        {"hook": "a", "role": "preflight-gate", "decision": "skip", "session_id": "s2", "timestamp": "2026-06-26T02:00:00+00:00"},
        {"hook": "b", "role": "advisory-nudge", "decision": "skip", "session_id": "s2", "timestamp": "2026-06-26T03:00:00+00:00"},
    ]
    agg = cli.aggregate_fires(events)
    assert agg["a"]["fires"] == 1  # skip excluded
    assert agg["a"]["skip"] == 1
    assert agg["a"]["sessions"] == {"s1"}  # skip's session not "engaged"
    assert agg["a"]["last_seen"] == "2026-06-26T02:00:00+00:00"  # still dated
    # a hook whose only records are skips shows zero fires, not phantom ones
    assert agg["b"]["fires"] == 0 and agg["b"]["skip"] == 1
    assert agg["b"]["sessions"] == set()


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
    monkeypatch.setattr(fl, "_IN_DISPATCHER", False)
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
    monkeypatch.setattr(fl, "_IN_DISPATCHER", True)
    fl.record_standalone_fire("x", "y", 2)
    assert not out.exists()


def test_record_standalone_fire_opt_out(tmp_path, monkeypatch):
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_DISABLE", "1")
    monkeypatch.setattr(fl, "_DISPATCHER_PROCESS", False)
    monkeypatch.setattr(fl, "_IN_DISPATCHER", False)
    fl.record_standalone_fire("x", "y", 0)
    assert not out.exists()


# ---------------------------------------------------------------------------
# Writer: record_session_fire (issue #740 — RICH single-event recording for
# standalone hooks outside the Bash dispatch group)
# ---------------------------------------------------------------------------

def test_record_session_fire_writes_rich_with_real_session(tmp_path, monkeypatch):
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    fl.record_session_fire(
        "askuserquestion-loop-signal", "postuse-correction", "pass",
        "sess-real", "AskUserQuestion",
    )
    recs = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(recs) == 1
    assert recs[0] == {
        "timestamp": recs[0]["timestamp"],
        "session_id": "sess-real",
        "tool": "AskUserQuestion",
        "hook": "askuserquestion-loop-signal",
        "role": "postuse-correction",
        "decision": "pass",
        "granularity": "rich",
    }


def test_escaped_decision_key_is_classified_as_block():
    # `{"\u0064ecision": "block"}` parses to the same object; the literal
    # substring pre-filter dropped it and classify_decision recorded a real
    # block as "pass" (CodeRabbit, issue #1199 review).
    escaped = r'{"\u0064ecision": "block", "reason": "r"}'
    assert '"decision"' not in escaped, "fixture is not actually escaped"
    assert json.loads(escaped) == {"decision": "block", "reason": "r"}
    assert fl._is_stop_block(escaped) is True
    assert fl.classify_decision(0, escaped, "", "Stop") == "block"
    # Control: a shape that is genuinely not a block still is not one.
    assert fl._is_stop_block('{"decision": "approve"}') is False


def test_record_session_fire_skipped_in_dispatcher(tmp_path, monkeypatch):
    """One fire, one rich record — not two (issue #1199 review).

    A grouped member already gets its rich record from record_group_fires. A
    member that also calls record_session_fire (every completion-verify hook
    does) produced a SECOND rich record for the same fire, identical in hook /
    decision / session_id, so nothing downstream could collapse them and every
    per-session count read double. _DISPATCHER_PROCESS suppressed only the
    COARSE path, which is why this survived.
    """
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    monkeypatch.setattr(fl, "_IN_DISPATCHER", True)
    assert fl.record_session_fire("x", "completion-verify", "block", "s", "") is False
    assert not out.exists()
    # Control: the identical call outside the dispatcher DOES write, so the
    # absence above is the suppression and not a broken invocation.
    monkeypatch.setattr(fl, "_IN_DISPATCHER", False)
    assert fl.record_session_fire("x", "completion-verify", "block", "s", "") is True
    recs = [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
    assert len(recs) == 1 and recs[0]["granularity"] == "rich"


def test_standalone_suppression_does_not_kill_its_own_rich_record(tmp_path, monkeypatch):
    """A standalone hook still gets its rich record after suppressing the coarse one.

    `suppress_coarse_duplicate` used to set `_DISPATCHER_PROCESS`, and two hooks
    (output-block-falsify-advisory, comment-yap-advisory) call it BEFORE their
    record_session_fire. Gating the rich write on that same flag therefore
    dropped those hooks' only telemetry — 8 assertions in
    tests/hooks/advisory-nudge/test_output_block_falsify_advisory.sh went from
    "1 rich record" to zero. The two meanings now live in separate flags.
    """
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    monkeypatch.setattr(fl, "_DISPATCHER_PROCESS", False)
    monkeypatch.setattr(fl, "_IN_DISPATCHER", False)
    fl.suppress_coarse_duplicate()  # the real call order in those two hooks
    assert fl._DISPATCHER_PROCESS is True, "coarse suppression must still arm"
    assert fl._IN_DISPATCHER is False, "a standalone hook is not the dispatcher"
    assert fl.record_session_fire("x", "advisory-nudge", "ask", "s", "T") is True
    recs = [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
    assert len(recs) == 1 and recs[0]["granularity"] == "rich"


def test_stop_block_classification_mirrors_the_dispatcher_gates():
    """The ledger records a Stop block only where the dispatcher enforces one.

    `run_group` accepts the `{"decision": "block"}` shape under two gates —
    `is_stop` and `rc == 0`. The ledger had neither, so a member that printed
    the JSON and then died (rc=1), or printed it under another event, was
    filed as a real block the dispatcher never propagated. The event gate is
    the newer half: scoping the dispatcher lane to Stop is what left the
    ledger behind.
    """
    block = '{"decision": "block", "reason": "r"}'
    # Both gates satisfied — the case that must still be a block.
    assert fl.classify_decision(0, block, "", "Stop") == "block"
    # rc gate: printed, then died.
    assert fl.classify_decision(1, block, "", "Stop") != "block"
    # event gate: the dispatcher's lane does not run here.
    assert fl.classify_decision(0, block, "", "PostToolUse") != "block"
    # Unknown event is not Stop.
    assert fl.classify_decision(0, block, "", None) != "block"
    # Exit 2 stays event-agnostic, matching the dispatcher's own exit-2 lane.
    assert fl.classify_decision(2, "", "", "PostToolUse") == "block"
    assert fl.classify_decision(2, "", "", None) == "block"


def test_marker_classification_mirrors_the_dispatcher_gate():
    """The two SUBSTRING marker lanes are PreToolUse-only, as in run_group.

    `run_group` probes `_DENY_MARKER` under `is_pretooluse` and `_ASK_MARKER`
    inside an `if is_pretooluse:` block, so on any other event a member whose
    stdout merely contains the marker text gets no decision from the
    dispatcher at all. The ledger probed both event-agnostically and filed a
    block or an ask nobody enforced — the same divergence the Stop lane above
    had, one lane over (issue #1199 review).
    """
    # PreToolUse — the lanes the dispatcher actually runs.
    assert fl.classify_decision(0, _DENY, "", "PreToolUse") == "block"
    assert fl.classify_decision(0, _ASK, "", "PreToolUse") == "ask"
    # Any other event: the dispatcher ignores the marker, so the ledger must.
    for event in ("Stop", "PostToolUse", None):
        assert fl.classify_decision(0, _DENY, "", event) != "block"
        assert fl.classify_decision(0, _ASK, "", event) != "ask"
    # The fall-through stays intact: stderr still classifies as advise.
    assert fl.classify_decision(0, _DENY, "nudge", "Stop") == "advise"
    # Exit 2 is unaffected — it never depended on the marker.
    assert fl.classify_decision(2, _DENY, "", "Stop") == "block"


def test_record_session_fire_opt_out(tmp_path, monkeypatch):
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_DISABLE", "1")
    fl.record_session_fire("h", "r", "pass", "sess-1", "AskUserQuestion")
    assert not out.exists()


def test_record_session_fire_non_string_session_and_tool_default_empty(tmp_path, monkeypatch):
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    fl.record_session_fire("h", "r", "pass", None, None)  # type: ignore[arg-type]
    rec = json.loads(out.read_text().splitlines()[0])
    assert rec["session_id"] == "" and rec["tool"] == ""


def test_record_session_fire_returns_true_on_success(tmp_path, monkeypatch):
    """Callers gate coarse suppression on this return (coderabbit finding on
    PR #855): a successful rich append reports True."""
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    assert fl.record_session_fire("h", "r", "advise", "s1", "Stop") is True


def test_record_session_fire_returns_false_when_disabled(tmp_path, monkeypatch):
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_DISABLE", "1")
    assert fl.record_session_fire("h", "r", "advise", "s1", "Stop") is False


def test_record_session_fire_returns_false_on_write_error(tmp_path, monkeypatch):
    """A swallowed write failure must report False so the caller keeps its
    coarse fallback instead of suppressing it — else the fire is dropped from
    both streams."""
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(fl, "_atomic_append", _boom)
    assert fl.record_session_fire("h", "r", "advise", "s1", "Stop") is False


# ---------------------------------------------------------------------------
# Reader: count_session_fires (issue #805 — in-session read path for a preflight
# gate to consume its own repeated-block signal)
# ---------------------------------------------------------------------------

def _write_records(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_count_session_fires_missing_file_returns_zero(tmp_path, monkeypatch):
    out = tmp_path / "nope.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    assert fl.count_session_fires("h", "s1", decision="block") == 0


def test_count_session_fires_filters_hook_session_and_decision(tmp_path, monkeypatch):
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    _write_records(out, [
        {"granularity": "rich", "hook": "gate", "session_id": "s1", "decision": "block"},
        {"granularity": "rich", "hook": "gate", "session_id": "s1", "decision": "block"},
        {"granularity": "rich", "hook": "gate", "session_id": "s1", "decision": "pass"},
        {"granularity": "rich", "hook": "other", "session_id": "s1", "decision": "block"},
        {"granularity": "rich", "hook": "gate", "session_id": "s2", "decision": "block"},
    ])
    # decision-filtered: only s1's own blocks of `gate`
    assert fl.count_session_fires("gate", "s1", decision="block") == 2
    # decision=None: every decision for (gate, s1) — 2 blocks + 1 pass
    assert fl.count_session_fires("gate", "s1") == 3
    assert fl.count_session_fires("gate", "s2", decision="block") == 1
    assert fl.count_session_fires("gate", "s3", decision="block") == 0
    assert fl.count_session_fires("other", "s1", decision="block") == 1


def test_count_session_fires_excludes_coarse_records(tmp_path, monkeypatch):
    """A coarse record carries session_id="" so it can never match a real
    session anyway, but the granularity filter makes the exclusion explicit —
    a future coarse record that DID carry a session_id must still not count."""
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    _write_records(out, [
        {"granularity": "rich", "hook": "gate", "session_id": "s1", "decision": "block"},
        {"granularity": "coarse", "hook": "gate", "session_id": "s1", "decision": "block"},
    ])
    assert fl.count_session_fires("gate", "s1", decision="block") == 1


def test_count_session_fires_empty_session_returns_zero(tmp_path, monkeypatch):
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    _write_records(out, [
        {"granularity": "rich", "hook": "gate", "session_id": "", "decision": "block"},
    ])
    assert fl.count_session_fires("gate", "", decision="block") == 0
    assert fl.count_session_fires("gate", None, decision="block") == 0  # type: ignore[arg-type]


def test_count_session_fires_opt_out_returns_zero(tmp_path, monkeypatch):
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_DISABLE", "1")
    _write_records(out, [
        {"granularity": "rich", "hook": "gate", "session_id": "s1", "decision": "block"},
    ])
    assert fl.count_session_fires("gate", "s1", decision="block") == 0


def test_count_session_fires_skips_malformed_lines(tmp_path, monkeypatch):
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    out.write_text(
        "not json{\n"
        + json.dumps({"granularity": "rich", "hook": "gate", "session_id": "s1", "decision": "block"}) + "\n"
        + "\n"  # blank line
        + json.dumps(["not", "a", "dict"]) + "\n",
        encoding="utf-8",
    )
    assert fl.count_session_fires("gate", "s1", decision="block") == 1


def test_count_session_fires_non_regular_file_returns_zero(tmp_path, monkeypatch):
    """Security/robustness guard: a FIFO at the path must not block or raise —
    mirrors _atomic_append's regular-file guard on the read side."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(fifo))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    assert fl.count_session_fires("gate", "s1", decision="block") == 0
    assert stat.S_ISFIFO(os.lstat(fifo).st_mode)  # untouched


def test_count_session_fires_roundtrip_with_real_writer(tmp_path, monkeypatch):
    """No SUT-mirrored mock: the dispatcher's own record_group_fires writes the
    RICH block record, and count_session_fires reads it back. This locks the
    exact field contract (hook/session_id/decision/granularity) both halves
    share — a rename on either side fails this test."""
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    members = [("preflight-gate", "block-commit-without-codex-review", Path("x"))]
    # Two blocking dispatches in the same session, as the real gate produces.
    fl.record_group_fires(members, [(2, "", "")], _payload("s-round", "Bash"))
    fl.record_group_fires(members, [(2, "", "")], _payload("s-round", "Bash"))
    assert fl.count_session_fires(
        "block-commit-without-codex-review", "s-round", decision="block"
    ) == 2


def test_roster_split_falls_back_to_the_extension_proxy(tmp_path):
    """No body on disk -> the pre-#892 extension proxy is the only signal left."""
    manifest = {"hooks": [
        {"name": "a", "event": "PreToolUse", "matcher": "Bash"},   # dispatch group
        {"name": "b", "event": "Stop"},                            # py (instrumentable)
        {"name": "sh1", "event": "Stop", "body": "impl.sh"},       # shell, unreadable
        "not-a-dict",
        {"event": "Stop"},  # no name -> skipped
    ]}
    mpath = tmp_path / "m.json"
    mpath.write_text(json.dumps(manifest))
    instrumentable, uninstrumentable = cli.roster_split(mpath)
    assert instrumentable == {"a", "b"}
    assert uninstrumentable == {"sh1"}


def _write_hook_body(hooks_dir, role, name, body, source):
    d = hooks_dir / role / name
    d.mkdir(parents=True, exist_ok=True)
    (d / body).write_text(source, encoding="utf-8")


def test_roster_split_reads_the_body_for_a_recording_chokepoint(tmp_path):
    """#892 gave shell hooks record_fire.sh — the extension alone is now wrong.

    Before this, `body: impl.sh` was read as "emits no fire events", so the
    report claimed the four shell hooks were unrecorded while its own per-hook
    table tabulated thousands of their fires. The classification must follow
    the chokepoint, not the file extension.
    """
    hooks_dir = tmp_path / "hooks"
    _write_hook_body(hooks_dir, "completion-verify", "recorded", "impl.sh",
                     '#!/bin/bash\nsource "$(dirname "$0")/../../_lib/record_fire.sh"\n')
    _write_hook_body(hooks_dir, "completion-verify", "silent", "impl.sh",
                     "#!/bin/bash\nexit 0\n")
    _write_hook_body(hooks_dir, "preflight-gate", "pyhook", "impl.py",
                     "from _fail_open import fail_open\n")
    _write_hook_body(hooks_dir, "preflight-gate", "pysilent", "impl.py",
                     "print('nothing here')\n")
    manifest = {"hooks": [
        {"name": "recorded", "role": "completion-verify", "body": "impl.sh",
         "event": "Stop"},
        {"name": "silent", "role": "completion-verify", "body": "impl.sh",
         "event": "Stop"},
        {"name": "pyhook", "role": "preflight-gate", "event": "PostToolUse"},
        {"name": "pysilent", "role": "preflight-gate", "event": "PostToolUse"},
    ]}
    mpath = hooks_dir / "manifest.json"
    mpath.write_text(json.dumps(manifest))
    instrumentable, uninstrumentable = cli.roster_split(mpath)
    assert instrumentable == {"recorded", "pyhook"}
    assert uninstrumentable == {"silent", "pysilent"}


# The input surface of records_fire_events(), enumerated up front rather than
# one reviewer round at a time. A marker can appear in a comment, inside a
# string, or as the prefix of an unrelated name — and the first two shapes are
# not hypothetical: every shell hook here carries
# `# shellcheck source=../../_lib/record_fire.sh` on the line directly above
# its real `.` source line.
#
# The two error directions are not symmetric. A missed real form leaves the
# hook in "cannot be judged", which is safe; a false match puts a hook that
# cannot record into the never-fired roster, where it reads as a prune
# candidate. Anchoring therefore errs toward rejecting.
RECORDS_FIRE_CASES = [
    # (label, source, expected)
    ("A1 shell comment-only", '#!/bin/bash\n# shellcheck source=../../_lib/record_fire.sh\nexit 0\n', False),
    ("A2 python comment mention", "# this module used to use @fail_open\nx = 1\n", False),
    ("A3 decorator + trailing comment", "@fail_open  # noqa\ndef run(p): pass\n", True),
    ("A4 source + trailing comment", '. "$(dirname "$0")/../../_lib/record_fire.sh"  # load\n', True),
    ("B1 marker inside a call argument", 'print("import fail_open")\n', False),
    ("B2 marker inside an assignment", 'MSG = "source record_fire.sh"\n', False),
    ("B3 marker inside a docstring", '"""record_fire is described in this docstring"""\n', False),
    ("B4 decorator text inside a string", 'msg = "@fail_open"\n', False),
    ("B5 source line echoed as text", 'echo ". ../_lib/record_fire.sh"\n', False),
    ("B6 import text inside a string", 'x = "from m import fail_open"\n', False),
    ("C1 unrelated @fail_opened", "@fail_opened\ndef run(p): pass\n", False),
    ("C2 unrelated @fail_open_v2", "@fail_open_v2\ndef run(p): pass\n", False),
    ("C3 bare decorator", "@fail_open\ndef run(p): pass\n", True),
    ("C4 decorator with args", "@fail_open()\ndef run(p): pass\n", True),
    ("C5 unrelated symbol imported", "from m import fail_opened\n", False),
    ("C6 neighbouring filename", ". ../_lib/record_fire.sh.bak\n", False),
    ("D1 indented source line", '    . "$(dirname "$0")/../../_lib/record_fire.sh" 2>/dev/null || true\n', True),
    ("D2 source keyword form", '    source "$(dirname "$0")/../../_lib/record_fire.sh"\n', True),
    ("F1 from-import with comment", "from _hook_runtime import fail_open  # noqa: E402\n", True),
    ("F2 bare import", "import fail_open\n", True),
    ("F3 aliased import", "from _hook_runtime import fail_open as fo\n", True),
]


@pytest.mark.parametrize("label,source,expected", RECORDS_FIRE_CASES,
                         ids=[c[0] for c in RECORDS_FIRE_CASES])
def test_records_fire_events_surface(label, source, expected):
    assert cli.records_fire_events(source) is expected


def test_roster_split_counts_a_comment_only_body_as_uninstrumented(tmp_path):
    """The end-to-end path, not just the matcher: a decorative marker must not
    move a hook out of the uninstrumented list."""
    hooks_dir = tmp_path / "hooks"
    _write_hook_body(hooks_dir, "completion-verify", "decorative", "impl.sh",
                     "#!/bin/bash\n# shellcheck source=../../_lib/record_fire.sh\nexit 0\n")
    manifest = {"hooks": [
        {"name": "decorative", "role": "completion-verify", "body": "impl.sh",
         "event": "Stop"},
    ]}
    mpath = hooks_dir / "manifest.json"
    mpath.write_text(json.dumps(manifest))
    instrumentable, uninstrumentable = cli.roster_split(mpath)
    assert instrumentable == set()
    assert uninstrumentable == {"decorative"}


def test_roster_split_counts_dispatch_group_membership_as_recorded(tmp_path):
    """A dispatch-group hook is recorded centrally, so its body carries no marker."""
    hooks_dir = tmp_path / "hooks"
    _write_hook_body(hooks_dir, "preflight-gate", "grouped", "impl.py",
                     "def run(payload):\n    return None\n")
    manifest = {"hooks": [
        {"name": "grouped", "role": "preflight-gate", "event": "PreToolUse",
         "matcher": "Bash"},
    ]}
    mpath = hooks_dir / "manifest.json"
    mpath.write_text(json.dumps(manifest))
    instrumentable, uninstrumentable = cli.roster_split(mpath)
    assert instrumentable == {"grouped"}
    assert uninstrumentable == set()


def test_roster_split_lets_one_recorded_entry_win_for_a_multi_event_hook(tmp_path):
    """Entry order must not decide: any recording entry makes the name recorded."""
    hooks_dir = tmp_path / "hooks"
    _write_hook_body(hooks_dir, "completion-verify", "multi", "impl.sh",
                     "#!/bin/bash\nexit 0\n")
    _write_hook_body(hooks_dir, "completion-verify", "multi", "impl2.sh",
                     '#!/bin/bash\nsource ../../_lib/record_fire.sh\n')
    manifest = {"hooks": [
        {"name": "multi", "role": "completion-verify", "body": "impl.sh",
         "event": "SessionStart"},
        {"name": "multi", "role": "completion-verify", "body": "impl2.sh",
         "event": "Stop"},
    ]}
    mpath = hooks_dir / "manifest.json"
    mpath.write_text(json.dumps(manifest))
    instrumentable, uninstrumentable = cli.roster_split(mpath)
    assert instrumentable == {"multi"}
    assert uninstrumentable == set()


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
    monkeypatch.setattr(real_fl, "_IN_DISPATCHER", False)


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
    monkeypatch.setattr(fl, "_IN_DISPATCHER", False)
    fl.mark_dispatcher_process()  # run_group calls this at entry
    fl.record_group_fires([("preflight-gate", "h", Path("x"))], [(2, "", "")], _payload())
    fl.record_standalone_fire("h", "preflight-gate", 2)  # member fail_open — suppressed
    recs = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(recs) == 1 and recs[0]["granularity"] == "rich"


def test_suppress_coarse_duplicate_skips_standalone_fire(tmp_path, monkeypatch):
    """Issue #787: a standalone hook that already wrote a RICH record via
    record_session_fire must be able to suppress its own subsequent COARSE
    record — otherwise aggregate_fires() double-counts the same call with a
    mismatched decision (rich=block/ask, coarse=pass), corrupting block-rate
    counts. suppress_coarse_duplicate reuses the dispatcher-process flag for
    this, same mechanism as test_dispatcher_process_writes_rich_not_coarse
    above, applied outside a real dispatcher context."""
    out = tmp_path / "fire.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    monkeypatch.setattr(fl, "_DISPATCHER_PROCESS", False)
    monkeypatch.setattr(fl, "_IN_DISPATCHER", False)
    fl.record_session_fire(
        "output-block-falsify-advisory", "advisory-nudge", "block",
        "sess-1", "AskUserQuestion",
    )
    fl.suppress_coarse_duplicate()
    fl.record_standalone_fire("output-block-falsify-advisory", "advisory-nudge", 0)
    recs = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(recs) == 1
    assert recs[0]["granularity"] == "rich" and recs[0]["decision"] == "block"


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


def test_aggregate_marks_mixed_granularity_stop_hook():
    """issue #847: a single-event-rich Stop hook records its escalation rich
    (real Block/Advise) but its silent passes stay coarse — the row carries
    BOTH flags and must render as G=M, not G=C (which the legend says folds
    Block into Pass, contradicting a visible Block=1)."""
    events = [
        {"hook": "merge-state-claim-gate", "role": "completion-verify", "decision": "block",
         "granularity": "rich", "session_id": "s1", "timestamp": "2026-06-26T01:00:00+00:00"},
        {"hook": "merge-state-claim-gate", "role": "completion-verify", "decision": "pass",
         "granularity": "coarse", "session_id": "", "timestamp": "2026-06-26T01:00:10+00:00"},
    ]
    agg = cli.aggregate_fires(events)
    assert agg["merge-state-claim-gate"]["coarse"] is True
    assert agg["merge-state-claim-gate"]["rich"] is True
    assert agg["merge-state-claim-gate"]["block"] == 1

    report = cli.render_fire_report(agg, 30, Path("/tmp"), None)
    # The row's G column is M (mixed), and the summary/legend name it.
    assert "1 mixed" in report
    assert "M=mixed" in report


def test_fire_report_header_excludes_skip_only_hooks():
    """issue #1167 / PR #1195 round-2 review: a hook whose only records in
    the window are dispatcher budget-skips never RAN — counting it in 'Hooks
    fired' (or bucketing it coarse/rich) overstates coverage. It is reported
    on its own Skip-only line instead."""
    events = [
        {"hook": "ran", "role": "preflight-gate", "decision": "pass",
         "granularity": "rich", "session_id": "s1", "timestamp": "2026-06-26T01:00:00+00:00"},
        {"hook": "starved", "role": "advisory-nudge", "decision": "skip",
         "granularity": "rich", "session_id": "s1", "timestamp": "2026-06-26T01:00:01+00:00"},
    ]
    agg = cli.aggregate_fires(events)
    report = cli.render_fire_report(agg, 30, Path("/tmp"), None)
    assert "Hooks fired : 1" in report
    assert "Skip-only hooks : 1" in report
    # the starved hook still appears in the table with its Skip count
    assert "starved" in report


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


def test_advise_ignored_skip_is_not_an_outcome():
    """issue #1167 / PR #1195 review: a budget-skip after an advise means the
    hook never EVALUATED the next call — it is not evidence the advisory was
    heeded. Skips are dropped from the stream: advise → skip → advise still
    scores as ignored (the condition recurred at the next real evaluation),
    and advise → skip alone stays right-censored."""
    events = [
        {"hook": "h", "session_id": "s1", "decision": "advise", "timestamp": "2026-06-26T00:00:00+00:00"},
        {"hook": "h", "session_id": "s1", "decision": "skip", "timestamp": "2026-06-26T00:00:10+00:00"},
        {"hook": "h", "session_id": "s1", "decision": "advise", "timestamp": "2026-06-26T00:00:20+00:00"},
        {"hook": "g", "session_id": "s1", "decision": "advise", "timestamp": "2026-06-26T00:00:00+00:00"},
        {"hook": "g", "session_id": "s1", "decision": "skip", "timestamp": "2026-06-26T00:00:10+00:00"},
    ]
    result = cli.compute_advise_ignored(events)
    assert result["h"]["observed"] == 1
    assert result["h"]["ignored"] == 1  # recurred at the next REAL evaluation
    assert result["g"]["advise_fires"] == 1
    assert result["g"]["observed"] == 0  # skip is not a follow-up: censored
    assert result["g"]["rate"] is None


def test_advise_ignored_excludes_partial_rich_stop_hook_when_scoped():
    """issue #847: a single-event-rich Stop hook records only escalations —
    its silent passes never reach the rich stream, so advise, advise (the
    intervening heeded pass invisible) would mis-score as ignored=100%. When a
    full-rich roster is supplied, the partial-stream hook is excluded entirely
    while a genuine full-rich hook is still scored."""
    events = [
        # partial-rich Stop hook: two advises, the heeded pass between them is
        # never recorded rich, so leaving it in would read as ignored.
        {"hook": "merge-state-claim-gate", "session_id": "s1", "decision": "advise", "timestamp": "2026-06-26T00:00:00+00:00"},
        {"hook": "merge-state-claim-gate", "session_id": "s1", "decision": "advise", "timestamp": "2026-06-26T00:00:20+00:00"},
        # full-rich Bash-group hook: advise then pass (heeded) — still scored.
        {"hook": "destructive-bash-guard", "session_id": "s1", "decision": "advise", "timestamp": "2026-06-26T00:00:00+00:00"},
        {"hook": "destructive-bash-guard", "session_id": "s1", "decision": "pass", "timestamp": "2026-06-26T00:00:10+00:00"},
    ]
    scoped = cli.compute_advise_ignored(events, {"destructive-bash-guard"})
    assert "merge-state-claim-gate" not in scoped  # partial stream — excluded
    assert scoped["destructive-bash-guard"]["observed"] == 1
    assert scoped["destructive-bash-guard"]["ignored"] == 0  # pass = heeded

    # None (roster unreadable) falls back to legacy unscoped behavior — the
    # partial-rich hook reappears and its advise, advise reads as ignored.
    legacy = cli.compute_advise_ignored(events)
    assert legacy["merge-state-claim-gate"]["ignored"] == 1


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
        "external_write_revert_count": 0, "reclarification_loop_count": 0,
        "rework_commit_count": 0,
    }
    assert result["s2"] == {
        "strike_count": 0, "strike_state_available": False,
        "external_write_revert_count": 0, "reclarification_loop_count": 0,
        "rework_commit_count": 0,
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


def test_compute_external_write_revert_counts_excludes_budget_skips():
    """issue #1167 / PR #1195 review: a budget-skip means the guard never ran
    — it is a non-pass decision but NOT a flagged destructive command, so it
    must not count toward the revert proxy."""
    fire_events = [
        {"hook": "destructive-bash-guard", "session_id": "s1", "decision": "advise"},
        {"hook": "destructive-bash-guard", "session_id": "s1", "decision": "skip"},
        {"hook": "destructive-bash-guard", "session_id": "s2", "decision": "skip"},
    ]
    counts = cli.compute_external_write_revert_counts(fire_events)
    assert counts == {"s1": 1}


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


# issue #740: re-clarification-loop coarse proxy (askuserquestion-loop-signal
# RICH fires) — see compute_reclarification_loop_counts.

def test_compute_reclarification_loop_counts_counts_rich_fires_only():
    fire_events = [
        {"hook": "askuserquestion-loop-signal", "session_id": "s1", "granularity": "rich"},
        {"hook": "askuserquestion-loop-signal", "session_id": "s1", "granularity": "rich"},
        # coarse duplicate from @fail_open (session_id="") must be excluded
        {"hook": "askuserquestion-loop-signal", "session_id": "", "granularity": "coarse"},
        {"hook": "other-hook", "session_id": "s1", "granularity": "rich"},
    ]
    counts = cli.compute_reclarification_loop_counts(fire_events)
    assert counts == {"s1": 2}


def test_compute_reclarification_loop_counts_ignores_missing_session():
    fire_events = [
        {"hook": "askuserquestion-loop-signal", "session_id": "", "granularity": "rich"},
        {"hook": "askuserquestion-loop-signal", "granularity": "rich"},
    ]
    assert cli.compute_reclarification_loop_counts(fire_events) == {}


def test_compute_outcome_proxy_surfaces_reclarification_loop_count(tmp_path):
    state_dir = tmp_path / "strikes"
    state_dir.mkdir()
    fire_events = [
        {"hook": "askuserquestion-loop-signal", "session_id": "s1", "granularity": "rich",
         "timestamp": "2026-06-26T00:00:00+00:00"},
        {"hook": "askuserquestion-loop-signal", "session_id": "s1", "granularity": "rich",
         "timestamp": "2026-06-26T00:00:05+00:00"},
    ]
    result = cli.compute_outcome_proxy(fire_events, state_dir)
    assert result["s1"]["reclarification_loop_count"] == 2


def test_fire_rate_report_shows_nonzero_reclarification_loop_signal(tmp_path, monkeypatch):
    """Acceptance criterion (issue #740): a synthetic fixture with 2
    AskUserQuestion calls in the same session (via the real writer's own
    askuserquestion-loop-signal RICH fire) shows a nonzero re-clarification-
    loop value in the Outcome Proxy section."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    telem_dir = tmp_path / "telemetry"
    telem_dir.mkdir()
    fire_out = telem_dir / f"fire-events-{today}.jsonl"
    state_dir = tmp_path / "strikes"
    state_dir.mkdir()

    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(fire_out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)

    # Real writer output: askuserquestion-loop-signal fires twice in one
    # session, as it does when the hook detects 2 AskUserQuestion calls.
    fl.record_session_fire(
        "askuserquestion-loop-signal", "postuse-correction", "pass",
        "s-reclarify", "AskUserQuestion",
    )
    fl.record_session_fire(
        "askuserquestion-loop-signal", "postuse-correction", "pass",
        "s-reclarify", "AskUserQuestion",
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main([
            "fire-rate", "--dir", str(telem_dir), "--state-dir", str(state_dir),
        ])
    report = buf.getvalue()

    assert rc == 0
    assert "Outcome Proxy" in report
    assert "Sessions with re-clarification-loop signal (>=2 AskUserQuestion calls) : 1" in report
    assert "s-reclarify" in report


def test_default_strike_state_dir_respects_praxis_state_dir_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_STATE_DIR", str(tmp_path))
    assert cli.default_strike_state_dir() == tmp_path / "strikes"


# ---------------------------------------------------------------------------
# issue #741: rework-commit outcome-proxy signal (trailer-first +
# timestamp-heuristic fallback, mirrors bypass_count's exact-map + heuristic
# structure — see _nearest_hook_by_timestamp above).
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    # Inline user.name/email/gpgsign (not global config) so the test doesn't
    # depend on the host's git config being pre-populated — commit.gpgsign=false
    # avoids a non-interactive gpg/pinentry failure if the host/CI has
    # gpgsign=true set globally (CodeRabbit PR #742 finding).
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    )


def test_parse_session_trailer_extracts_value():
    body = "feat: add thing\n\nSome body text.\n\nSession-Id: sess-abc-123\n"
    assert cli.parse_session_trailer(body) == "sess-abc-123"


def test_parse_session_trailer_missing_returns_none():
    assert cli.parse_session_trailer("feat: add thing\n\nNo trailer here.\n") is None


def test_parse_session_trailer_case_insensitive_key():
    assert cli.parse_session_trailer("fix: x\n\nsession-id: sess-lower\n") == "sess-lower"


def test_parse_session_trailer_empty_body_returns_none():
    assert cli.parse_session_trailer("") is None


def test_parse_session_trailer_ignores_prose_mention():
    # CodeRabbit PR #742 nitpick: a "Session-Id:"-shaped line in prose
    # (not the terminal trailer block) must not be misread as a trailer.
    body = "feat: x\n\nSee Session-Id: not-a-trailer for context.\n\nConfidence: high\n"
    assert cli.parse_session_trailer(body) is None


def test_compute_rework_commit_counts_trailer_exact_match():
    commits = [
        {"sha": "a1", "timestamp": datetime(2026, 6, 26, 0, 0, 0, tzinfo=timezone.utc),
         "body": "feat: x\n\nSession-Id: s-exact\n"},
    ]
    counts = cli.compute_rework_commit_counts(commits, fire_events=[])
    assert counts == {"s-exact": 1}


def test_compute_rework_commit_counts_timestamp_heuristic_fallback():
    commit_ts = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)
    commits = [{"sha": "b2", "timestamp": commit_ts, "body": "fix: y\n\nno trailer\n"}]
    fire_events = [
        {"session_id": "s-heuristic", "timestamp": "2026-06-26T12:03:00+00:00"},
    ]
    counts = cli.compute_rework_commit_counts(commits, fire_events)
    assert counts == {"s-heuristic": 1}


def test_compute_rework_commit_counts_outside_window_unattributed():
    commit_ts = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)
    commits = [{"sha": "c3", "timestamp": commit_ts, "body": "fix: z\n\nno trailer\n"}]
    fire_events = [
        # 1 hour away, default window is 900s (15 min) — outside the window.
        {"session_id": "s-far", "timestamp": "2026-06-26T13:00:00+00:00"},
    ]
    counts = cli.compute_rework_commit_counts(commits, fire_events)
    assert counts == {}


def test_compute_rework_commit_counts_trailer_priority_over_heuristic():
    commit_ts = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)
    commits = [{"sha": "d4", "timestamp": commit_ts,
                "body": "fix: z\n\nSession-Id: s-trailer\n"}]
    fire_events = [
        {"session_id": "s-nearby", "timestamp": "2026-06-26T12:00:05+00:00"},
    ]
    counts = cli.compute_rework_commit_counts(commits, fire_events)
    assert counts == {"s-trailer": 1}


def test_load_git_commits_reads_trailer_from_real_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("1")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "feat: add f\n\nSession-Id: s-real")
    commits = cli.load_git_commits(repo, days=7)
    assert len(commits) == 1
    assert cli.parse_session_trailer(commits[0]["body"]) == "s-real"


def test_load_git_commits_non_git_dir_returns_empty(tmp_path):
    assert cli.load_git_commits(tmp_path, days=7) == []


def test_load_git_commits_uses_committer_date_not_author_date(tmp_path):
    # CodeRabbit PR #742 Major finding: `git log --since` filters by
    # committer date, so the returned timestamp must be committer date too —
    # otherwise a rebased/backdated commit's author date (year 2000 here)
    # would skew the timestamp-heuristic proximity match.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("1")
    _git(repo, "add", "f.txt")
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = "2000-01-01T00:00:00+00:00"
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", "commit", "-q", "-m", "feat: old author date"],
        capture_output=True, text=True, check=True, env=env,
    )
    commits = cli.load_git_commits(repo, days=1)
    assert len(commits) == 1
    assert commits[0]["timestamp"].year != 2000


def test_compute_outcome_proxy_surfaces_rework_commit_count(tmp_path):
    state_dir = tmp_path / "strikes"
    state_dir.mkdir()
    fire_events = [
        {"hook": "h", "session_id": "s1", "timestamp": "2026-06-26T00:00:00+00:00"},
    ]
    result = cli.compute_outcome_proxy(fire_events, state_dir, rework_counts={"s1": 4})
    assert result["s1"]["rework_commit_count"] == 4


def test_fire_rate_report_shows_nonzero_rework_commit_signal_trailer(tmp_path, monkeypatch):
    """Acceptance criterion (issue #741): a trailer-tagged commit surfaces as
    a nonzero rework-commit value in the Outcome Proxy section."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    telem_dir = tmp_path / "telemetry"
    telem_dir.mkdir()
    fire_out = telem_dir / f"fire-events-{today}.jsonl"
    state_dir = tmp_path / "strikes"
    state_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("1")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "feat: x\n\nSession-Id: s-trailer-e2e")

    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(fire_out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    members = [("advisory-nudge", "protected-paths-guard", Path("x"))]
    fl.record_group_fires(members, [(0, "", "nudge")], _payload("s-trailer-e2e", "Write"))

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main([
            "fire-rate", "--dir", str(telem_dir), "--state-dir", str(state_dir),
            "--repo", str(repo),
        ])
    report = buf.getvalue()

    assert rc == 0
    assert "Sessions with rework-commit signal : 1" in report
    assert "s-trailer-e2e" in report


def test_fire_rate_report_shows_nonzero_rework_commit_signal_heuristic(tmp_path, monkeypatch):
    """Acceptance criterion (issue #741): a commit with no Session-Id trailer
    still surfaces via the timestamp-heuristic fallback (nearest session
    activity within the window)."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    telem_dir = tmp_path / "telemetry"
    telem_dir.mkdir()
    fire_out = telem_dir / f"fire-events-{today}.jsonl"
    state_dir = tmp_path / "strikes"
    state_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("1")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "fix: y (no trailer)")

    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(fire_out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    members = [("advisory-nudge", "protected-paths-guard", Path("x"))]
    fl.record_group_fires(members, [(0, "", "nudge")], _payload("s-heuristic-e2e", "Write"))

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main([
            "fire-rate", "--dir", str(telem_dir), "--state-dir", str(state_dir),
            "--repo", str(repo),
        ])
    report = buf.getvalue()

    assert rc == 0
    assert "Sessions with rework-commit signal : 1" in report
    assert "s-heuristic-e2e" in report


# ---------------------------------------------------------------------------
# issue #710 remaining scope: end-to-end fire-rate report renders new sections
# ---------------------------------------------------------------------------

def test_fire_rate_report_includes_remaining_scope_sections(tmp_path, monkeypatch):
    """Real writer output (record_group_fires + bypass hook's own JSONL schema)
    flows through run_fire_rate end-to-end and produces non-trivial values for
    all remaining-scope metrics — no mirrored/mocked business logic."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    telem_dir = tmp_path / "telemetry"
    telem_dir.mkdir()
    fire_out = telem_dir / f"fire-events-{today}.jsonl"
    bypass_out = telem_dir / f"bypass-events-{today}.jsonl"
    state_dir = tmp_path / "strikes"
    state_dir.mkdir()
    # Empty repo (no commits in window) — keeps the rework-commit signal
    # hermetic instead of depending on cwd's real git history.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")

    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(fire_out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)

    # Two dispatches of the same hook in the same session: advise then advise
    # again (ignored), via the real writer. Uses a genuine PreToolUse(Bash)
    # group hook so it survives the advise-ignored roster scoping (issue #847):
    # single-event-rich Stop hooks are excluded because their passes are not
    # rich, so the fixture must be a full-rich Bash-group hook.
    members = [("preflight-gate", "destructive-bash-guard", Path("x"))]
    fl.record_group_fires(members, [(0, "", "advise")], _payload("s1", "Bash"))
    fl.record_group_fires(members, [(0, "", "advise")], _payload("s1", "Bash"))

    # bypass-events file uses the real writer's own field schema (session_id,
    # tool, bypass_env_vars, tool_input, tool_result_status).
    bypass_record = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "session_id": "s1",
        "tool": "Bash",
        "bypass_env_vars": ["PRAXIS_HOOK_BYPASS_DESTRUCTIVE_BASH"],
        "tool_input": "rm -rf x",
        "tool_result_status": "ok",
    }
    bypass_out.write_text(json.dumps(bypass_record) + "\n")

    (state_dir / "s1.json").write_text(json.dumps({"count": 1, "reasons": ["late verification"]}))

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main([
            "fire-rate", "--dir", str(telem_dir), "--state-dir", str(state_dir),
            "--repo", str(repo),
        ])
    report = buf.getvalue()

    assert rc == 0
    assert "Advise-Ignored Detail" in report
    assert "destructive-bash-guard" in report
    assert "100%" in report  # both advise fires ignored (no escalation)
    assert "Bypass Attribution" in report
    assert "Outcome Proxy" in report
    assert "s1" in report and "1" in report  # strike_count surfaced


# ---------------------------------------------------------------------------
# Dev-checkout isolation (issue #934)
#
# Isolation used to live only at the full-suite entrypoints, so running one
# shell test directly wrote into the real ~/.praxis/telemetry ledger — 105 of
# 112 shell tests never set the override themselves, and CI always goes through
# run-tests.sh, so CI could not catch it. Seven days of ledger were 26% synthetic
# in the advise tier.
# ---------------------------------------------------------------------------


def test_override_still_wins_over_dev_checkout(tmp_path, monkeypatch):
    """PRAXIS_FIRE_TELEMETRY_FILE keeps absolute precedence."""
    target = tmp_path / "explicit.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(target))
    assert fl.resolve_path() == target


def test_dev_checkout_diverts_off_the_real_ledger(monkeypatch):
    """Running from a checkout must never resolve into ~/.praxis/telemetry."""
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_FILE", raising=False)
    resolved = fl.resolve_path()
    real = Path.home() / ".praxis" / "telemetry"
    assert real not in resolved.parents, f"{resolved} is inside the real ledger dir"
    assert resolved.parent.name == fl.DEV_LEDGER_DIRNAME
    # The tests run from a checkout, so this is the checkout branch by construction.
    assert (resolved.parent.parent / ".git").exists()


def test_installed_plugin_still_uses_the_real_ledger(monkeypatch):
    """No checkout above the module → unchanged production behaviour.

    `tests/conftest.py` roots PRAXIS_HOME per test, so the real ledger is only
    reached with the variable removed; the #1340 section below covers the
    relocated case.
    """
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_FILE", raising=False)
    monkeypatch.delenv("PRAXIS_HOME", raising=False)
    monkeypatch.setattr(fl, "_checkout_root", lambda: None)
    resolved = fl.resolve_path()
    assert resolved.parent == Path.home() / ".praxis" / "telemetry"


def test_direct_shell_test_run_does_not_touch_the_real_ledger(tmp_path, monkeypatch):
    """Reproduce the exact failure mode: one hook invoked with NO override.

    `HOME` is redirected into `tmp_path` first, so the production fallback
    resolves inside the sandbox and the developer's own ledger is neither read
    nor written. The oracle survives the redirect: if checkout detection
    regressed, the record would land in the sandbox ledger and the first
    assertion below would still fire.

    Scoped to THIS invocation on both sides, because neither file is quiet:
    concurrent live sessions append to the real ledger while the test runs (358
    records were observed during one shell-test run), so a whole-file size
    comparison fails spuriously; and a fixed session id would let a leftover
    record from an earlier run on the same UTC date satisfy the dev-ledger
    assertion even if this dispatcher wrote nothing at all.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    real_file = Path.home() / ".praxis" / "telemetry" / f"fire-events-{today}.jsonl"
    dev_file = _REPO / fl.DEV_LEDGER_DIRNAME / f"fire-events-{today}.jsonl"
    session_id = f"test-934-isolation-{uuid.uuid4()}"

    # Byte offsets, so only what this invocation appends is ever inspected.
    real_offset = real_file.stat().st_size if real_file.exists() else 0
    dev_offset = dev_file.stat().st_size if dev_file.exists() else 0

    env = {k: v for k, v in os.environ.items() if k != "PRAXIS_FIRE_TELEMETRY_FILE"}
    env.pop("PRAXIS_FIRE_TELEMETRY_DISABLE", None)
    payload = json.dumps({
        "session_id": session_id,
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi"},
    })
    proc = subprocess.run(
        [sys.executable, str(_REPO / "hooks" / "_lib" / "_dispatch.py"),
         "PreToolUse", "Bash", "claude"],
        input=payload, text=True, capture_output=True, env=env, check=False,
    )
    # A crashed dispatcher writes nothing anywhere, which would otherwise read
    # as "isolation works". 0 = allow, 2 = a member blocked; both are real runs.
    assert proc.returncode in (0, 2), (
        f"dispatcher failed (rc={proc.returncode}): {proc.stderr[:400]}"
    )

    def _tail(path: Path, offset: int) -> str:
        if not path.exists():
            return ""
        with path.open(encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            return fh.read()

    assert session_id not in _tail(real_file, real_offset), (
        "a hook run from the checkout appended this invocation to the real ledger"
    )
    assert session_id in _tail(dev_file, dev_offset), (
        "the dev ledger did not receive this invocation — telemetry silently off?"
    )


def test_install_layout_under_a_git_ancestor_is_not_a_checkout(tmp_path, monkeypatch):
    """An installed plugin nested inside someone's git repo stays production.

    `CONTRIBUTING.md` documents the config dir as relocatable, so the plugin
    cache can sit under a dotfiles repository. An unbounded ancestor walk finds
    that `.git` and diverts every live fire into a dev ledger — the whole
    telemetry stream disappears silently. Only the package root is inspected.
    """
    root = tmp_path / "dotfiles"
    (root / ".git").mkdir(parents=True)
    pkg = root / "cache" / "praxis" / "7.7.0"
    lib = pkg / "hooks" / "_lib"
    lib.mkdir(parents=True)
    installed = lib / "_fire_ledger.py"
    installed.write_text(
        (_REPO / "hooks" / "_lib" / "_fire_ledger.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    mod = _load("_fire_ledger_installed", installed)
    assert mod._checkout_root() is None, "install layout misread as a checkout"

    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_FILE", raising=False)
    # The production root — conftest relocates it per test via PRAXIS_HOME
    # (#1340); the point here is that it is not a dev ledger.
    assert mod.resolve_telemetry_dir() == Path(os.environ["PRAXIS_HOME"]) / "telemetry"
    assert mod.resolve_telemetry_dir().parent.name != fl.DEV_LEDGER_DIRNAME


def test_bypass_and_fire_writers_share_one_directory():
    """Split directories would corrupt both halves of the fire-rate report."""
    bypass = _load(
        "bypass_telemetry_impl",
        _REPO / "hooks" / "postuse-correction" / "bypass-telemetry" / "impl.py",
    )
    assert bypass.resolve_telemetry_path().parent == fl.resolve_telemetry_dir()


# ---------------------------------------------------------------------------
# PRAXIS_HOME relocation (issue #1340)
#
# PRIVACY.md lists PRAXIS_HOME as the override for ~/.praxis/telemetry, and
# every other runtime root followed it; the ledger alone kept defaulting to
# Path.home()/.praxis/telemetry, which scripts/run-tests.sh papered over with
# a suite-wide PRAXIS_FIRE_TELEMETRY_FILE (#849). The resolution now goes
# through _paths.praxis_home(), with the override and the dev-checkout branch
# still ahead of it.
# ---------------------------------------------------------------------------


def test_resolve_telemetry_dir_honours_praxis_home(tmp_path, monkeypatch):
    monkeypatch.setattr(fl, "_checkout_root", lambda: None)
    monkeypatch.setenv("PRAXIS_HOME", str(tmp_path / "relocated"))
    assert fl.resolve_telemetry_dir() == tmp_path / "relocated" / "telemetry"


def test_resolve_telemetry_dir_expands_a_tilde_praxis_home(tmp_path, monkeypatch):
    """A quoted PRAXIS_HOME=~/x reaches the process literally; _paths expands it."""
    monkeypatch.setattr(fl, "_checkout_root", lambda: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRAXIS_HOME", "~/relocated")
    assert fl.resolve_telemetry_dir() == tmp_path / "relocated" / "telemetry"


def test_resolve_telemetry_dir_default_when_praxis_home_is_unset_or_empty(monkeypatch):
    """Unset and empty both mean ~/.praxis, exactly as before #1340."""
    monkeypatch.setattr(fl, "_checkout_root", lambda: None)
    monkeypatch.delenv("PRAXIS_HOME", raising=False)
    assert fl.resolve_telemetry_dir() == Path.home() / ".praxis" / "telemetry"
    monkeypatch.setenv("PRAXIS_HOME", "")
    assert fl.resolve_telemetry_dir() == Path.home() / ".praxis" / "telemetry"


def test_resolve_path_precedence_is_override_then_checkout_then_praxis_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_HOME", str(tmp_path / "relocated"))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_FILE", raising=False)
    checkout = tmp_path / "checkout"
    monkeypatch.setattr(fl, "_checkout_root", lambda: checkout)
    assert fl.resolve_path().parent == checkout / fl.DEV_LEDGER_DIRNAME
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(tmp_path / "explicit.jsonl"))
    assert fl.resolve_path() == tmp_path / "explicit.jsonl"


def test_praxis_home_rule_survives_a_missing_paths_module(tmp_path, monkeypatch):
    """The fail-open fallback restates the rule; it must not revert to ~/.praxis.

    record_fire.sh's escape fallback imports _fire_ledger out of a probe _lib
    holding nothing else, so `_paths` (and the `_state_lock` it imports) can
    be absent. A None entry in sys.modules makes the import raise the way an
    absent file would.
    """
    monkeypatch.setitem(sys.modules, "_paths", None)
    monkeypatch.setattr(fl, "_checkout_root", lambda: None)
    monkeypatch.setenv("PRAXIS_HOME", str(tmp_path / "relocated"))
    assert fl.resolve_telemetry_dir() == tmp_path / "relocated" / "telemetry"
    monkeypatch.delenv("PRAXIS_HOME")
    assert fl.resolve_telemetry_dir() == Path.home() / ".praxis" / "telemetry"


def test_record_lands_under_praxis_home_outside_a_checkout(tmp_path):
    """End to end in a subprocess: a copy of _lib with no .git two levels up,
    no file override, PRAXIS_HOME set → today's file under $PRAXIS_HOME/telemetry
    and nothing under HOME/.praxis."""
    lib = tmp_path / "pkg" / "hooks" / "_lib"
    lib.mkdir(parents=True)
    for name in ("_fire_ledger.py", "_paths.py", "_state_lock.py"):
        (lib / name).write_bytes((_REPO / "hooks" / "_lib" / name).read_bytes())
    home = tmp_path / "relocated"
    env = {k: v for k, v in os.environ.items() if k != "PRAXIS_FIRE_TELEMETRY_FILE"}
    env["PRAXIS_HOME"] = str(home)
    env["HOME"] = str(tmp_path / "home")
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import _fire_ledger as f; "
        "assert f._checkout_root() is None; "
        "print(f.record_session_fire('h1340', 'r', 'pass', 'sess-1340', 'Bash'))"
    )
    proc = subprocess.run([sys.executable, "-c", code, str(lib)], env=env,
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0 and proc.stdout.strip() == "True", proc.stderr
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    ledger = home / "telemetry" / f"fire-events-{today}.jsonl"
    recs = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert [r["session_id"] for r in recs] == ["sess-1340"]
    assert not (tmp_path / "home" / ".praxis").exists()


def test_bypass_review_default_dir_follows_praxis_home(tmp_path, monkeypatch):
    """The reader resolves the same root as the writers, through _paths."""
    monkeypatch.setenv("PRAXIS_HOME", str(tmp_path / "relocated"))
    assert cli.default_telemetry_dir() == tmp_path / "relocated" / "telemetry"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRAXIS_HOME", "~/relocated")
    assert cli.default_telemetry_dir() == tmp_path / "relocated" / "telemetry"
    monkeypatch.delenv("PRAXIS_HOME")
    assert cli.default_telemetry_dir() == Path.home() / ".praxis" / "telemetry"
    assert cli._praxis_paths() is not None, "resolver did not load from the checkout"


# ---------------------------------------------------------------------------
# Retention sweep (#1078)
# ---------------------------------------------------------------------------
#
# The ledger was append-only with no sweep: 59 daily files over ~2.5 months
# reached 1.7 GB, growing ~680 MB a month. These cover that old files go, that
# recent ones and foreign files stay, that both dated families age out together
# (`bypass-review fire-rate` joins them), and that the sweep is reachable from
# the write path exactly on the day roll-over.


def _dated(directory: Path, prefix: str, days_ago: int) -> Path:
    stamp = (datetime.now(tz=timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    p = directory / f"{prefix}{stamp}.jsonl"
    p.write_text('{"hook": "x"}\n')
    return p


def test_prune_removes_only_files_past_the_window(tmp_path):
    old = _dated(tmp_path, "fire-events-", 40)
    edge = _dated(tmp_path, "fire-events-", 30)
    recent = _dated(tmp_path, "fire-events-", 5)
    removed = fl.prune_telemetry(tmp_path, days=30)
    assert not old.exists()
    assert recent.exists()
    assert edge.exists()  # exactly at the window is still inside it
    assert removed == 1


def test_prune_ages_both_families_together(tmp_path):
    """`bypass-review fire-rate` joins the two; sweeping one alone corrupts it."""
    fires = _dated(tmp_path, "fire-events-", 40)
    bypasses = _dated(tmp_path, "bypass-events-", 40)
    assert fl.prune_telemetry(tmp_path, days=30) == 2
    assert not fires.exists() and not bypasses.exists()


def test_prune_leaves_unrelated_files_alone(tmp_path):
    """Only known dated families are swept — never whatever else lives here."""
    notes = tmp_path / "notes.md"
    notes.write_text("keep me")
    undated = tmp_path / "fire-events-nope.jsonl"
    undated.write_text("{}\n")
    assert fl.prune_telemetry(tmp_path, days=1) == 0
    assert notes.exists() and undated.exists()


def test_zero_retention_keeps_everything(tmp_path, monkeypatch):
    """The pre-#1078 behavior stays reachable by configuration."""
    monkeypatch.setenv("PRAXIS_TELEMETRY_RETENTION_DAYS", "0")
    old = _dated(tmp_path, "fire-events-", 400)
    assert fl.prune_telemetry(tmp_path) == 0
    assert old.exists()


def test_write_sweeps_on_the_day_rollover(tmp_path, monkeypatch):
    """The first write into a new day's file is the once-a-day sweep trigger."""
    out = tmp_path / "fire-events-2099-01-01.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    monkeypatch.setenv("PRAXIS_TELEMETRY_RETENTION_DAYS", "30")
    stale = _dated(tmp_path, "fire-events-", 90)

    fl.record_group_fires([("r", "h", Path("x"))], [(0, "", "")], _payload())
    assert not stale.exists()          # today's file was new — swept

    survivor = _dated(tmp_path, "fire-events-", 90)
    fl.record_group_fires([("r", "h", Path("x"))], [(0, "", "")], _payload())
    assert survivor.exists()           # today's file existed — no second sweep


def test_count_session_fires_prefilter_matches_the_parse(tmp_path, monkeypatch):
    """The substring prefilter must not change a single count (#1078).

    A record that matches carries both literals, so rejecting on them is exact
    — but a near-miss (right hook, wrong session; right session, wrong hook)
    is what would break if the needles were built loosely.
    """
    out = tmp_path / "fire-events.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    for hook, sid, dec in (
        ("gate-a", "sess-1", "block"), ("gate-a", "sess-1", "block"),
        ("gate-a", "sess-1", "advise"),
        ("gate-a", "sess-2", "block"),      # right hook, other session
        ("gate-b", "sess-1", "block"),      # right session, other hook
    ):
        fl.record_session_fire(hook, "preflight-gate", dec, sid, "Bash")
    # A coarse record (session_id "") must never match a real session.
    fl.record_standalone_fire("gate-a", "preflight-gate", 2)

    assert fl.count_session_fires("gate-a", "sess-1", "block") == 2
    assert fl.count_session_fires("gate-a", "sess-1") == 3
    assert fl.count_session_fires("gate-a", "sess-2", "block") == 1
    assert fl.count_session_fires("gate-b", "sess-1") == 1
    assert fl.count_session_fires("gate-a", "") == 0


def test_count_session_fires_survives_an_escaped_session_id(tmp_path, monkeypatch):
    """A session_id JSON has to escape must not silently count zero (#1078).

    The prefilter's needle is raw, so a quote or a backslash in the value makes
    it miss every record the writer escaped on the way in. Such a value drops
    to the full parse instead (codex review round 1 — confirmed: the needle
    `"session_id": "abc"def"` is absent from the stored `abc\\"def`).
    """
    out = tmp_path / "fire-events.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(out))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    for sid in ('abc"def', "back\\slash", "plain-1"):
        fl.record_session_fire("gate-a", "preflight-gate", "block", sid, "Bash")

    assert fl.count_session_fires("gate-a", 'abc"def', "block") == 1
    assert fl.count_session_fires("gate-a", "back\\slash", "block") == 1
    assert fl.count_session_fires("gate-a", "plain-1", "block") == 1
    # The fast path survives for values that need no escaping.
    assert fl._raw_needle("plain-1") == '"plain-1"'
    assert fl._raw_needle('abc"def') is None


def test_count_session_fires_reads_a_compactly_written_record(tmp_path):
    """The prefilter must not assume one writer's JSON spacing (#1078).

    A needle carrying the key and its separator (`"session_id": "x"`) matches
    only records written with a space after the colon. The escalation ledger in
    `test_block_commit_without_codex_review.sh` is seeded compactly by hand, and
    a key-bearing needle skipped it — the second same-session block then read as
    the first and its escalation banner never appeared.
    """
    out = tmp_path / "fire-events.jsonl"
    out.write_text(
        '{"granularity":"rich","hook":"gate-a",'
        '"session_id":"sess-1","decision":"block"}\n',
        encoding="utf-8",
    )
    os.environ["PRAXIS_FIRE_TELEMETRY_FILE"] = str(out)
    os.environ.pop("PRAXIS_FIRE_TELEMETRY_DISABLE", None)
    try:
        assert fl.count_session_fires("gate-a", "sess-1", "block") == 1
    finally:
        del os.environ["PRAXIS_FIRE_TELEMETRY_FILE"]


def test_bypass_writer_sweeps_on_the_first_write_of_the_day(tmp_path, monkeypatch):
    """`bypass-events-` is a retention prefix, so its writer owns a sweep (#1078).

    The fire ledger's sweep runs inside `_atomic_append`, which the bypass hook
    never calls — with fire telemetry disabled, nothing would ever prune the
    bypass files (codex review round 1 — confirmed: `prune_telemetry` had one
    call site and it was in `_fire_ledger` itself).
    """
    bt = _load(
        "bypass_telemetry_sweep",
        _REPO / "hooks" / "postuse-correction" / "bypass-telemetry" / "impl.py",
    )
    stale = _dated(tmp_path, "bypass-events-", 90)
    today = tmp_path / (
        "bypass-events-"
        + datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        + ".jsonl"
    )
    monkeypatch.setenv("PRAXIS_BYPASS_TELEMETRY_FILE", str(today))

    bt.append_record({"event": "first write of the day"})
    assert not stale.exists()

    revived = _dated(tmp_path, "bypass-events-", 90)
    bt.append_record({"event": "second write, same day"})
    assert revived.exists()          # today's file existed — no second sweep


# ---------------------------------------------------------------------------
# #1238 — prior-day gzip rollover
# ---------------------------------------------------------------------------

def _read_gz_lines(path: Path) -> list[str]:
    import gzip
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [ln for ln in fh.read().splitlines() if ln]


def _dated_cold(directory: Path, prefix: str, days_ago: int) -> Path:
    """A finished day's file, old enough that the rollover will not defer it."""
    p = _dated(directory, prefix, days_ago)
    cold = datetime.now(tz=timezone.utc).timestamp() - fl._HOT_FILE_SEC - 1
    os.utime(p, (cold, cold))
    return p


def _archives(directory: Path, prefix: str, days_ago: int) -> list[Path]:
    stamp = (datetime.now(tz=timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return sorted(directory.glob(f"{prefix}{stamp}.*.jsonl.gz"))


def test_compress_gzips_prior_days_and_leaves_today(tmp_path):
    old = _dated_cold(tmp_path, "fire-events-", 1)
    today = _dated_cold(tmp_path, "fire-events-", 0)
    assert fl.compress_telemetry(tmp_path) == 1
    assert not old.exists()
    assert today.exists()
    (gz,) = _archives(tmp_path, "fire-events-", 1)
    assert _read_gz_lines(gz) == ['{"hook": "x"}']
    assert fl._file_date(gz.name) == old.name[len("fire-events-"):-len(".jsonl")]


def test_compress_covers_both_families(tmp_path):
    fires = _dated_cold(tmp_path, "fire-events-", 2)
    bypasses = _dated_cold(tmp_path, "bypass-events-", 2)
    assert fl.compress_telemetry(tmp_path) == 2
    assert not fires.exists() and not bypasses.exists()
    assert len(_archives(tmp_path, "fire-events-", 2)) == 1
    assert len(_archives(tmp_path, "bypass-events-", 2)) == 1


def test_compress_gives_a_straggler_its_own_archive(tmp_path):
    """A hook from yesterday recreating the plain file after the rollover must
    keep its rows without touching the archive already written."""
    old = _dated_cold(tmp_path, "fire-events-", 1)
    assert fl.compress_telemetry(tmp_path) == 1
    old.write_text('{"hook": "straggler"}\n')
    cold = datetime.now(tz=timezone.utc).timestamp() - fl._HOT_FILE_SEC - 1
    os.utime(old, (cold, cold))
    assert fl.compress_telemetry(tmp_path) == 1
    assert not old.exists()
    first, second = _archives(tmp_path, "fire-events-", 1)
    assert _read_gz_lines(first) + _read_gz_lines(second) == [
        '{"hook": "x"}', '{"hook": "straggler"}'
    ]


def test_compress_is_idempotent_after_a_crash_before_the_unlink(tmp_path):
    """Archive exposed by os.replace, then the process died: the plain
    tokened file is dropped, never re-archived (no duplicate rows)."""
    old = _dated_cold(tmp_path, "fire-events-", 1)
    claimed = fl._claim_for_compression(old)
    assert claimed is not None and claimed.exists() and not old.exists()
    assert fl._compress_claimed(claimed)
    (gz,) = _archives(tmp_path, "fire-events-", 1)
    claimed.write_text('{"hook": "x"}\n')  # simulate: gz written, unlink lost
    assert fl.compress_telemetry(tmp_path) == 1
    assert not claimed.exists()
    assert _archives(tmp_path, "fire-events-", 1) == [gz]
    assert _read_gz_lines(gz) == ['{"hook": "x"}']


def test_compress_resumes_a_claimed_file_with_no_archive(tmp_path):
    """Died after the rename, before the archive: the next run finishes it."""
    old = _dated_cold(tmp_path, "fire-events-", 1)
    claimed = fl._claim_for_compression(old)
    assert fl.compress_telemetry(tmp_path) == 1
    assert not claimed.exists()
    assert len(_archives(tmp_path, "fire-events-", 1)) == 1


def test_compress_preserves_the_plain_files_mode(tmp_path):
    old = _dated_cold(tmp_path, "fire-events-", 1)
    old.chmod(0o600)
    saved = os.umask(0)
    try:
        assert fl.compress_telemetry(tmp_path) == 1
    finally:
        os.umask(saved)
    (gz,) = _archives(tmp_path, "fire-events-", 1)
    assert stat.S_IMODE(gz.stat().st_mode) == 0o600


def test_compress_leaves_symlinks_unrelated_and_undated_files_alone(tmp_path):
    (tmp_path / "notes.md").write_text("keep")
    (tmp_path / "fire-events-nope.jsonl").write_text("{}\n")
    real = tmp_path / "elsewhere.jsonl"
    real.write_text("{}\n")
    stamp = (datetime.now(tz=timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    link = tmp_path / f"fire-events-{stamp}.jsonl"
    link.symlink_to(real)
    assert fl.compress_telemetry(tmp_path) == 0
    assert link.is_symlink() and real.exists()
    assert (tmp_path / "notes.md").exists()
    assert (tmp_path / "fire-events-nope.jsonl").exists()


def test_file_date_reads_every_rollover_shape():
    assert fl._file_date("fire-events-2026-01-02.jsonl") == "2026-01-02"
    assert fl._file_date("fire-events-2026-01-02.jsonl.gz") == "2026-01-02"
    assert fl._file_date("fire-events-2026-01-02.1a2b3c.jsonl") == "2026-01-02"
    assert fl._file_date("bypass-events-2026-01-02.1a2b3c.jsonl.gz") == "2026-01-02"
    assert fl._file_date("fire-events-2026-01-02.1a2b3c.jsonl.gz.123.tmp") is None
    assert fl._file_date("fire-events-2026-13-02.jsonl") is None


def test_prune_sweeps_archives_past_the_window(tmp_path):
    old = _dated_cold(tmp_path, "fire-events-", 40)
    gz = tmp_path / (old.name[:-len(".jsonl")] + ".abc.jsonl.gz")
    old.rename(gz)
    kept = _dated_cold(tmp_path, "fire-events-", 5)
    kept_gz = tmp_path / (kept.name + ".gz")
    kept.rename(kept_gz)
    assert fl.prune_telemetry(tmp_path, days=30) == 1
    assert not gz.exists()
    assert kept_gz.exists()


def _wait_for(predicate, timeout=10.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_compress_reclaims_a_dead_compressors_tmp_and_keeps_a_live_ones(tmp_path):
    stamp = (datetime.now(tz=timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    dead = tmp_path / f"fire-events-{stamp}.abc.jsonl.gz.999999.tmp"
    dead.write_bytes(b"partial")
    live = tmp_path / f"fire-events-{stamp}.def.jsonl.gz.{os.getpid()}.tmp"
    live.write_bytes(b"partial")
    assert fl.compress_telemetry(tmp_path) == 0
    assert not dead.exists()
    assert live.exists()


def test_compress_leaves_a_file_written_in_the_last_minute_alone(tmp_path):
    """A writer that opened yesterday's file just before midnight may still be
    mid-append; a fresh mtime defers that file to the next rollover."""
    hot = _dated(tmp_path, "fire-events-", 1)  # mtime = now
    assert fl.compress_telemetry(tmp_path) == 0
    assert hot.exists()
    cold = datetime.now(tz=timezone.utc).timestamp() - fl._HOT_FILE_SEC - 1
    os.utime(hot, (cold, cold))
    assert fl.compress_telemetry(tmp_path) == 1


def test_compress_reclaims_only_its_own_tmp_names(tmp_path):
    foreign = tmp_path / "other.jsonl.gz.999999.tmp"
    foreign.write_bytes(b"user's file")
    undated = tmp_path / "fire-events-notadate.abc.jsonl.gz.999999.tmp"
    undated.write_bytes(b"x")
    assert fl.compress_telemetry(tmp_path) == 0
    assert foreign.exists() and undated.exists()


def test_rotate_starts_one_child_per_directory_per_day(tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(fl, "_compress_detached", lambda d: spawned.append(d) or True)
    assert fl.rotate_telemetry(tmp_path)[0] is True
    assert fl.rotate_telemetry(tmp_path)[0] is False
    assert spawned == [tmp_path]
    stale = tmp_path / f"{fl._ROLLOVER_MARK}2000-01-01"
    stale.write_text("")
    unrelated = tmp_path / f"{fl._ROLLOVER_MARK}notes"
    unrelated.write_text("keep")
    fl.rotate_telemetry(tmp_path)
    assert not stale.exists()
    assert unrelated.exists()  # only date-shaped markers are the module's own
    assert len(list(tmp_path.glob(f"{fl._ROLLOVER_MARK}????-??-??"))) == 1


def test_rotate_releases_the_marker_when_the_child_cannot_start(tmp_path, monkeypatch):
    monkeypatch.setattr(fl, "_compress_detached", lambda d: False)
    assert fl.rotate_telemetry(tmp_path)[0] is False
    assert list(tmp_path.glob(f"{fl._ROLLOVER_MARK}*")) == []
    monkeypatch.setattr(fl, "_compress_detached", lambda d: True)
    assert fl.rotate_telemetry(tmp_path)[0] is True  # a later writer retries


def test_rotate_prunes_inline_and_compresses_in_a_detached_child(tmp_path):
    ancient = _dated_cold(tmp_path, "fire-events-", 40)
    recent = _dated_cold(tmp_path, "fire-events-", 1)
    started, removed = fl.rotate_telemetry(tmp_path)
    assert (started, removed) == (True, 1)
    assert not ancient.exists()
    assert _wait_for(lambda: not recent.exists())
    assert len(_archives(tmp_path, "fire-events-", 1)) == 1


def test_first_write_of_the_day_rolls_yesterday_over(tmp_path, monkeypatch):
    """The rollover fires on the same edge as the #1078 sweep, from both writers."""
    fire_yesterday = _dated_cold(tmp_path, "fire-events-", 1)
    today_stamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    fire_today = tmp_path / f"fire-events-{today_stamp}.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(fire_today))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)
    fl._atomic_append(fire_today, ['{"hook": "first of the day"}'])
    assert fire_today.exists()
    assert _wait_for(lambda: not fire_yesterday.exists())
    assert len(_archives(tmp_path, "fire-events-", 1)) == 1

    bt = _load(
        "bypass_telemetry_rollover",
        _REPO / "hooks" / "postuse-correction" / "bypass-telemetry" / "impl.py",
    )
    bdir = tmp_path / "bypass"
    bdir.mkdir()
    bypass_yesterday = _dated_cold(bdir, "bypass-events-", 1)
    bypass_today = bdir / f"bypass-events-{today_stamp}.jsonl"
    monkeypatch.setenv("PRAXIS_BYPASS_TELEMETRY_FILE", str(bypass_today))
    bt.append_record({"event": "first write of the day"})
    assert _wait_for(lambda: not bypass_yesterday.exists())
    assert len(_archives(bdir, "bypass-events-", 1)) == 1
