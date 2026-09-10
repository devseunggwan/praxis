"""Tests for hooks/_lib/_block_repeat.py — per-session block repeat counter (#1405).

Coverage:
  - first block of a rule is silent; the second and later carry the notice
  - the count is per `rule_name`, so two gates do not pool into one threshold
  - session id: CLAUDE_SESSION_ID wins, the strike-counter latch is the
    fallback, neither present means no counting and an unchanged message
  - PRAXIS_BLOCK_REPEAT_DISABLE turns it off
  - unreadable / corrupt state reads back as zero rather than raising
  - emit_block appends the notice on the second call and nothing on the first
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "hooks" / "_lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import _block_repeat  # noqa: E402


def _load_block_message():
    spec = importlib.util.spec_from_file_location(
        "block_message_under_test", LIB / "block_message.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# threshold
# --------------------------------------------------------------------------- #


def test_first_block_is_silent_second_carries_notice(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-1")
    monkeypatch.setenv("PRAXIS_BLOCK_REPEAT_FILE", str(tmp_path / "state.json"))

    assert _block_repeat.record("gh search --state all") == ""
    second = _block_repeat.record("gh search --state all")
    assert "Repeat: GH SEARCH --STATE ALL has blocked 2 times" in second
    third = _block_repeat.record("gh search --state all")
    assert "blocked 3 times" in third


def test_count_is_per_rule_name(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-1")
    monkeypatch.setenv("PRAXIS_BLOCK_REPEAT_FILE", str(tmp_path / "state.json"))

    assert _block_repeat.record("rule-a") == ""
    # A different rule crossing at the same time must not lift rule-a's count.
    assert _block_repeat.record("rule-b") == ""
    assert "blocked 2 times" in _block_repeat.record("rule-a")


def test_notice_points_at_the_reference_line_not_a_bypass(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-1")
    monkeypatch.setenv("PRAXIS_BLOCK_REPEAT_FILE", str(tmp_path / "state.json"))

    _block_repeat.record("rule-a")
    notice = _block_repeat.record("rule-a")
    assert "Reference line" in notice
    assert "decision predicate" in notice
    # The remedy is reading the spec — a bypass is named only as the narrower
    # documented case, never as the way past the block.
    assert "bypass token is only for a skip condition" in notice


# --------------------------------------------------------------------------- #
# session id resolution
# --------------------------------------------------------------------------- #


def test_env_session_id_wins_over_latch(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / ".current-session").write_text("latch-sid\n", encoding="utf-8")
    monkeypatch.setenv("PRAXIS_STATE_DIR", str(state))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "env-sid")

    assert _block_repeat.session_id() == "env-sid"


def test_latch_is_the_fallback(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / ".current-session").write_text("latch-sid\n", encoding="utf-8")
    monkeypatch.setenv("PRAXIS_STATE_DIR", str(state))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

    assert _block_repeat.session_id() == "latch-sid"


def test_no_session_id_means_no_counting(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_STATE_DIR", str(tmp_path / "absent"))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.setenv("PRAXIS_BLOCK_REPEAT_FILE", str(tmp_path / "state.json"))

    assert _block_repeat.session_id() is None
    assert _block_repeat.record("rule-a") == ""
    assert _block_repeat.record("rule-a") == ""


def test_disable_env_turns_it_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-1")
    monkeypatch.setenv("PRAXIS_BLOCK_REPEAT_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("PRAXIS_BLOCK_REPEAT_DISABLE", "1")

    assert _block_repeat.record("rule-a") == ""
    assert _block_repeat.record("rule-a") == ""


# --------------------------------------------------------------------------- #
# degraded state
# --------------------------------------------------------------------------- #


def test_corrupt_state_reads_back_as_zero(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text('{"rules": {"rule-a": 4', encoding="utf-8")  # truncated
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-1")
    monkeypatch.setenv("PRAXIS_BLOCK_REPEAT_FILE", str(path))

    # Losing the count is the documented degraded outcome; raising is not.
    assert _block_repeat.record("rule-a") == ""
    assert json.loads(path.read_text(encoding="utf-8"))["rules"]["rule-a"] == 1


def test_unwritable_state_dir_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-1")
    monkeypatch.setenv("PRAXIS_BLOCK_REPEAT_FILE", str(tmp_path / "nodir" / "x" / "s.json"))
    os.makedirs(tmp_path / "nodir", exist_ok=True)
    os.chmod(tmp_path / "nodir", 0o500)
    try:
        assert _block_repeat.record("rule-a") == ""
    finally:
        os.chmod(tmp_path / "nodir", 0o700)


# --------------------------------------------------------------------------- #
# emit_block integration
# --------------------------------------------------------------------------- #


def test_emit_block_appends_notice_only_from_the_second(tmp_path, monkeypatch):
    import io

    monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-1")
    monkeypatch.setenv("PRAXIS_BLOCK_REPEAT_FILE", str(tmp_path / "state.json"))
    bm = _load_block_message()

    first, second = io.StringIO(), io.StringIO()
    for stream in (first, second):
        bm.emit_block(
            rule_name="demo gate",
            why="w",
            correct_path="c",
            bypass_env=None,
            reference="r",
            stream=stream,
        )

    assert "Repeat:" not in first.getvalue()
    assert "Repeat: DEMO GATE has blocked 2 times" in second.getvalue()
    # The five mandatory fields survive the append.
    assert second.getvalue().startswith("⚠️ DEMO GATE blocked")
    assert "Reference: r" in second.getvalue()
