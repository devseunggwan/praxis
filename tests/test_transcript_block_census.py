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
