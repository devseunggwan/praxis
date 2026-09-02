"""Tests for hooks/_lib/_payload.py — shared stdin-payload reading (#1178).

Coverage:
  - read_bash_payload: happy path, explicit `"tool_input": null` (the
    46-site AttributeError hazard), malformed JSON, non-Bash tool, missing
    command, non-str command, non-dict top level
  - read_payload: no-filter passthrough, tool-name filter (match / reject /
    missing tool_name), malformed JSON
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "hooks" / "_lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from _payload import read_bash_payload, read_payload  # noqa: E402


def _stream(text: str) -> io.StringIO:
    return io.StringIO(text)


# ---------------------------------------------------------------------------
# read_bash_payload
# ---------------------------------------------------------------------------


def test_bash_happy_path():
    res = read_bash_payload(
        _stream('{"tool_name": "Bash", "tool_input": {"command": "git st"}}')
    )
    assert res is not None
    payload, command = res
    assert command == "git st"
    assert payload["tool_name"] == "Bash"


def test_bash_null_tool_input_is_safe():
    # The 46-site hazard: {"tool_input": null} must NOT raise AttributeError.
    res = read_bash_payload(_stream('{"tool_name": "Bash", "tool_input": null}'))
    assert res is not None
    payload, command = res
    assert command == ""
    assert payload["tool_input"] is None


@pytest.mark.parametrize(
    "literal",
    ['"a-string"', "[\"ls\"]", "0", "true", "1.5"],
)
def test_bash_non_dict_tool_input_yields_empty_str(literal):
    # `or {}` covers only the FALSY non-dicts. A truthy string or list still
    # reached `.get` and raised AttributeError, which pushed the caller into
    # its fail-open path — the gate goes silently quiet, the exact failure this
    # helper exists to end (CodeRabbit, PR #1232).
    res = read_bash_payload(
        _stream('{"tool_name": "Bash", "tool_input": %s}' % literal))
    assert res is not None
    _payload_obj, command = res
    assert command == ""


def test_bash_malformed_json_returns_none():
    assert read_bash_payload(_stream("{not json")) is None


def test_bash_empty_stream_returns_none():
    assert read_bash_payload(_stream("")) is None


def test_bash_non_bash_tool_returns_none():
    assert read_bash_payload(_stream('{"tool_name": "Edit"}')) is None


def test_bash_missing_tool_name_returns_none():
    assert read_bash_payload(_stream('{"tool_input": {"command": "x"}}')) is None


def test_bash_missing_command_yields_empty_str():
    res = read_bash_payload(_stream('{"tool_name": "Bash", "tool_input": {}}'))
    assert res is not None
    assert res[1] == ""


def test_bash_missing_tool_input_yields_empty_str():
    res = read_bash_payload(_stream('{"tool_name": "Bash"}'))
    assert res is not None
    assert res[1] == ""


def test_bash_non_str_command_yields_empty_str():
    res = read_bash_payload(
        _stream('{"tool_name": "Bash", "tool_input": {"command": 5}}')
    )
    assert res is not None
    assert res[1] == ""


def test_bash_non_dict_top_level_returns_none():
    assert read_bash_payload(_stream('["Bash"]')) is None
    assert read_bash_payload(_stream("null")) is None


# ---------------------------------------------------------------------------
# read_payload
# ---------------------------------------------------------------------------


def test_read_payload_no_filter_passthrough():
    payload = read_payload(stream=_stream('{"tool_name": "Edit", "extra": 1}'))
    assert payload == {"tool_name": "Edit", "extra": 1}


def test_read_payload_no_filter_accepts_missing_tool_name():
    # Stop / UserPromptSubmit payloads carry no tool_name at all.
    payload = read_payload(stream=_stream('{"prompt": "hi"}'))
    assert payload == {"prompt": "hi"}


def test_read_payload_filter_match():
    payload = read_payload(
        ("Edit", "Write"), _stream('{"tool_name": "Write"}')
    )
    assert payload is not None
    assert payload["tool_name"] == "Write"


def test_read_payload_filter_reject():
    assert read_payload(("Edit", "Write"), _stream('{"tool_name": "Bash"}')) is None


def test_read_payload_filter_rejects_missing_tool_name():
    assert read_payload(("Edit",), _stream('{"prompt": "hi"}')) is None


def test_read_payload_filter_accepts_frozenset():
    payload = read_payload(
        frozenset({"Edit", "Write"}), _stream('{"tool_name": "Edit"}')
    )
    assert payload is not None


def test_read_payload_malformed_json_returns_none():
    assert read_payload(stream=_stream("{oops")) is None


def test_read_payload_non_dict_returns_none():
    assert read_payload(stream=_stream("[1, 2]")) is None
