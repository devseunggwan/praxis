"""Tests for scripts/transcript-block-census.py (issue #1502).

The fixture is synthetic and mirrors the split-message shape Claude Code
writes: one content block per JSONL line, lines of one API message sharing
``message.id``. It carries each shape RUNTIME_CONSTRAINTS.md entry 11 records —
an empty thinking block, a thinking block with a progress note, a mid-turn
``text`` note in a ``tool_use`` message, and a final ``end_turn`` reply — plus
the lines the census must ignore or survive.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "transcript-block-census.py"


def _load():
    spec = importlib.util.spec_from_file_location("transcript_block_census", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


census_mod = _load()


def _a(mid: str, stop: str, block: dict) -> str:
    return json.dumps({"type": "assistant",
                       "message": {"id": mid, "role": "assistant",
                                   "stop_reason": stop, "content": [block]}})


FIXTURE = [
    json.dumps({"type": "user", "message": {"role": "user",
                                            "content": [{"type": "text", "text": "go"}]}}),
    # message m1: hidden reasoning, progress note in thinking, tool call
    _a("m1", "tool_use", {"type": "thinking", "thinking": "", "signature": "s"}),
    _a("m1", "tool_use", {"type": "thinking", "thinking": "Checked X; now Y.", "signature": "s"}),
    _a("m1", "tool_use", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}),
    # message m2: mid-turn note as a text block
    _a("m2", "tool_use", {"type": "thinking", "thinking": "", "signature": "s"}),
    _a("m2", "tool_use", {"type": "text", "text": "Found it, fixing."}),
    _a("m2", "tool_use", {"type": "tool_use", "id": "t2", "name": "Edit", "input": {}}),
    # whitespace-only thinking counts as empty
    _a("m3", "tool_use", {"type": "thinking", "thinking": "  \n", "signature": "s"}),
    # final reply
    _a("m4", "end_turn", {"type": "text", "text": "Done."}),
    # string content counts as one text block
    json.dumps({"type": "assistant", "message": {"id": "m5", "stop_reason": "end_turn",
                                                 "content": "plain"}}),
    "{not json",
    "",
    json.dumps({"type": "system", "subtype": "x"}),
]


def test_census_counts_every_shape():
    r = census_mod.census(FIXTURE)
    assert r["assistant_lines"] == 9
    assert r["skipped_lines"] == 1
    assert r["blocks_by_type"] == {"text": 3, "thinking": 4, "tool_use": 2}
    assert r["thinking_nonempty"] == 1
    assert r["thinking_nonempty_by_stop_reason"] == {"tool_use": 1}
    assert r["text_by_stop_reason"] == {"end_turn": 2, "tool_use": 1}


def test_cli_prints_census_and_exits_zero(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(FIXTURE) + "\n", encoding="utf-8")
    out = subprocess.run([sys.executable, str(_SCRIPT), str(p)],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0
    assert "blocks by type: text=3, thinking=4, tool_use=2" in out.stdout
    assert "thinking with non-empty text: 1 of 4" in out.stdout
    assert "text blocks by stop_reason: end_turn=2, tool_use=1" in out.stdout


def test_cli_missing_file_exits_two(tmp_path):
    out = subprocess.run([sys.executable, str(_SCRIPT), str(tmp_path / "nope.jsonl")],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 2
    assert "cannot read" in out.stderr


def test_empty_transcript_reports_none():
    text = census_mod.render(census_mod.census([]))
    assert "assistant lines: 0" in text
    assert "thinking with non-empty text: 0 of 0" in text


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(_SCRIPT), *args],
                          capture_output=True, text=True, check=False)


def test_nonempty_thinking_is_keyed_by_its_own_stop_reason():
    # A non-empty thinking block outside a tool_use message is counted under
    # that message's stop_reason, not under a fixed key.
    lines = [
        _a("e1", "end_turn", {"type": "thinking", "thinking": "summary", "signature": "s"}),
        _a("e2", "max_tokens", {"type": "thinking", "thinking": "cut", "signature": "s"}),
        _a("e3", "tool_use", {"type": "thinking", "thinking": "note", "signature": "s"}),
    ]
    r = census_mod.census(lines)
    assert r["thinking_nonempty"] == 3
    assert r["thinking_nonempty_by_stop_reason"] == {
        "end_turn": 1, "max_tokens": 1, "tool_use": 1}


def test_non_dict_block_is_ignored():
    line = json.dumps({"type": "assistant", "message": {
        "id": "n1", "stop_reason": "end_turn",
        "content": ["x", {"type": "text", "text": "ok"}]}})
    r = census_mod.census([line])
    assert r["assistant_lines"] == 1
    assert r["blocks_by_type"] == {"text": 1}


def test_cli_keeps_a_record_with_raw_line_separators_whole(tmp_path):
    # JSON may carry U+2028 / U+2029 / U+0085 unescaped inside a string;
    # str.splitlines() would cut the record there.
    rec = json.dumps({"type": "assistant", "message": {
        "id": "u1", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "a b c\u0085d"}]}},
        ensure_ascii=False)
    p = tmp_path / "t.jsonl"
    p.write_text(rec + "\n", encoding="utf-8")
    out = _run(str(p))
    assert out.returncode == 0
    assert "skipped (unparseable) lines: 0" in out.stdout
    assert "assistant lines: 1" in out.stdout
    assert "blocks by type: text=1" in out.stdout


def test_cli_survives_invalid_utf8(tmp_path):
    good = _a("v1", "end_turn", {"type": "text", "text": "fine"}).encode("utf-8")
    p = tmp_path / "t.jsonl"
    p.write_bytes(b'{"type": "assistant", "bad": "\xff\xfe"}\n' + good + b"\n")
    out = _run(str(p))
    assert out.returncode == 0, out.stderr
    assert "blocks by type: text=1" in out.stdout


def test_cli_without_argument_exits_two():
    out = _run()
    assert out.returncode == 2
    assert "usage:" in out.stdout
