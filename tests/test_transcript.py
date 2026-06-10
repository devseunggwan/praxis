"""Contract + single-source tests for the hoisted transcript helpers (#643).

`hooks/_lib/_transcript.py` is the single source of truth for the JSONL
transcript readers that seven hooks previously re-implemented locally
(three Stop gates, two AskUserQuestion gates, two bounded preflight
readers) plus the TRANSCRIPT_SCAN_LINES constant two advisory hooks
re-declared. This suite locks three guarantees:

  1. **Contract** — each helper handles the transcript shapes the
     original call sites exercised: dict/str content, tool_result-only
     user entries, sidechain events, flat `{"role": ...}` entries,
     malformed JSON lines, non-dict objects, size bounds.

  2. **Single source** — every converted hook imports the SAME function
     object from _lib, so the parsers can no longer drift.

  3. **Fail-open** — missing files return the documented sentinel
     (None or []) and malformed input never raises.

Run: python3 -m pytest tests/test_transcript.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "hooks" / "_lib"

sys.path.insert(0, str(LIB_DIR))

import _transcript as T  # type: ignore[import-not-found]  # noqa: E402


def _write_jsonl(tmp_path: Path, lines: list) -> str:
    p = tmp_path / "transcript.jsonl"
    p.write_text(
        "\n".join(json.dumps(x) if not isinstance(x, str) else x for x in lines),
        encoding="utf-8",
    )
    return str(p)


def _user(text=None, blocks=None, sidechain=False) -> dict:
    content = text if text is not None else blocks
    ev = {"type": "user", "message": {"role": "user", "content": content}}
    if sidechain:
        ev["isSidechain"] = True
    return ev


def _assistant(text=None, blocks=None, sidechain=False) -> dict:
    content = text if text is not None else blocks
    ev = {"type": "assistant", "message": {"role": "assistant", "content": content}}
    if sidechain:
        ev["isSidechain"] = True
    return ev


# ---------------------------------------------------------------------------
# load_transcript
# ---------------------------------------------------------------------------

class TestLoadTranscript:
    def test_skips_blank_bad_json_and_non_dicts(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _user(text="hello"), "", "not json", json.dumps([1, 2]),
            _assistant(text="done"),
        ])
        events = T.load_transcript(path)
        assert len(events) == 2
        assert all(isinstance(e, dict) for e in events)

    def test_missing_file_returns_empty_list(self):
        assert T.load_transcript("/nonexistent/x.jsonl") == []


# ---------------------------------------------------------------------------
# load_transcript_objs / read_transcript_tail (bounded readers)
# ---------------------------------------------------------------------------

class TestBoundedReaders:
    def test_objs_keeps_non_dicts_and_skips_bad_lines(self, tmp_path):
        path = _write_jsonl(tmp_path, [_user(text="hi"), json.dumps([1]), "broken"])
        objs = T.load_transcript_objs(path, max_bytes=1 << 20)
        assert len(objs) == 2  # dict + list survive, broken line skipped
        assert isinstance(objs[1], list)

    def test_objs_over_max_bytes_returns_none(self, tmp_path):
        path = _write_jsonl(tmp_path, [_user(text="x" * 100)])
        assert T.load_transcript_objs(path, max_bytes=10) is None

    def test_objs_missing_file_returns_none(self):
        assert T.load_transcript_objs("/nonexistent/x.jsonl", max_bytes=100) is None

    def test_tail_returns_last_n_lines(self, tmp_path):
        path = _write_jsonl(tmp_path, [f'{{"i": {i}}}' for i in range(10)])
        tail = T.read_transcript_tail(path, max_lines=3, max_bytes=1 << 20)
        assert tail is not None
        assert tail.splitlines() == ['{"i": 7}', '{"i": 8}', '{"i": 9}']

    def test_tail_over_max_bytes_returns_none(self, tmp_path):
        path = _write_jsonl(tmp_path, ['{"k": "v"}'] * 5)
        assert T.read_transcript_tail(path, max_lines=2, max_bytes=3) is None


# ---------------------------------------------------------------------------
# get_current_turn
# ---------------------------------------------------------------------------

class TestGetCurrentTurn:
    def test_tool_result_only_user_msg_is_not_a_boundary(self):
        events = [
            _user(text="real question"),
            _assistant(blocks=[{"type": "tool_use", "name": "Bash", "id": "t1"}]),
            _user(blocks=[{"type": "tool_result", "tool_use_id": "t1"}]),
            _assistant(text="answer"),
        ]
        turn = T.get_current_turn(events)
        assert len(turn) == 3  # everything after the real user message

    def test_sidechain_user_msg_is_not_a_boundary(self):
        events = [
            _user(text="real"),
            _user(text="agent prompt", sidechain=True),
            _assistant(text="reply"),
        ]
        assert len(T.get_current_turn(events)) == 2

    def test_string_content_is_a_boundary(self):
        events = [_assistant(text="old"), _user(text="next"), _assistant(text="new")]
        turn = T.get_current_turn(events)
        assert len(turn) == 1

    def test_non_dict_blocks_are_tolerated(self):
        events = [
            {"type": "user", "message": {"role": "user", "content": ["bare-string"]}},
            _assistant(text="x"),
        ]
        # A list with no dict blocks has no non-tool_result dict → not a boundary.
        assert len(T.get_current_turn(events)) == 2

    def test_no_user_message_returns_all(self):
        events = [_assistant(text="a"), _assistant(text="b")]
        assert T.get_current_turn(events) == events


# ---------------------------------------------------------------------------
# extract_last_assistant_text / has_tool_in_turn
# ---------------------------------------------------------------------------

class TestAssistantHelpers:
    def test_extract_joins_text_blocks_and_skips_non_dicts(self):
        turn = [
            _assistant(blocks=[
                {"type": "text", "text": "part1"},
                "stray",
                {"type": "tool_use", "name": "Bash", "id": "t"},
                {"type": "text", "text": "part2"},
            ]),
        ]
        assert T.extract_last_assistant_text(turn) == "part1\npart2"

    def test_extract_takes_last_non_sidechain(self):
        turn = [
            _assistant(text="first"),
            _assistant(text="side", sidechain=True),
        ]
        assert T.extract_last_assistant_text(turn) == "first"

    def test_extract_string_content(self):
        assert T.extract_last_assistant_text([_assistant(text="plain")]) == "plain"

    def test_extract_empty_turn(self):
        assert T.extract_last_assistant_text([]) == ""

    def test_has_tool_in_turn(self):
        turn = [
            _assistant(blocks=[{"type": "tool_use", "name": "Bash", "id": "t"}]),
        ]
        assert T.has_tool_in_turn(turn, "Bash") is True
        assert T.has_tool_in_turn(turn, "Read") is False

    def test_has_tool_skips_non_dict_blocks(self):
        turn = [
            _assistant(blocks=["bare", {"type": "tool_use", "name": "Bash", "id": "t"}]),
        ]
        assert T.has_tool_in_turn(turn, "Bash") is True

    def test_has_tool_skips_sidechain(self):
        turn = [
            _assistant(
                blocks=[{"type": "tool_use", "name": "Bash", "id": "t"}],
                sidechain=True,
            ),
        ]
        assert T.has_tool_in_turn(turn, "Bash") is False


# ---------------------------------------------------------------------------
# read_last_user_message
# ---------------------------------------------------------------------------

class TestReadLastUserMessage:
    def test_skips_tool_result_only_entry(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _user(text="stop here please"),
            _user(blocks=[{"type": "tool_result", "tool_use_id": "t1"}]),
        ])
        assert T.read_last_user_message(path) == "stop here please"

    def test_flat_role_content_shape(self, tmp_path):
        path = _write_jsonl(tmp_path, [{"role": "user", "content": "flat shape"}])
        assert T.read_last_user_message(path) == "flat shape"

    def test_missing_file_returns_none(self):
        assert T.read_last_user_message("/nonexistent/x.jsonl") is None

    def test_empty_arg_returns_none(self):
        assert T.read_last_user_message("") is None

    def test_no_user_text_returns_empty_string(self, tmp_path):
        path = _write_jsonl(tmp_path, [_assistant(text="only assistant")])
        assert T.read_last_user_message(path) == ""

    def test_malformed_line_between_user_messages_is_skipped(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _user(text="older message"),
            "not-json {{{",
            _user(text="latest message"),
        ])
        assert T.read_last_user_message(path) == "latest message"

    def test_text_blocks_joined(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _user(blocks=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]),
        ])
        assert T.read_last_user_message(path) == "a\nb"


# ---------------------------------------------------------------------------
# Single source — every converted hook binds the SAME function objects
# ---------------------------------------------------------------------------

HOOKS = REPO_ROOT / "hooks"

_CONSUMERS = {
    HOOKS / "completion-verify" / "readonly-verify-deferral-gate" / "impl.py":
        ["load_transcript", "get_current_turn", "extract_last_assistant_text"],
    HOOKS / "completion-verify" / "completion-signal-gate" / "impl.py":
        ["load_transcript", "get_current_turn", "extract_last_assistant_text",
         "has_tool_in_turn"],
    HOOKS / "completion-verify" / "merge-state-claim-gate" / "impl.py":
        ["load_transcript", "get_current_turn", "extract_last_assistant_text"],
    HOOKS / "preflight-gate" / "block-gh-issue-create-without-dup-search" / "impl.py":
        ["read_transcript_tail"],
    HOOKS / "preflight-gate" / "block-sciomc-finding-commit" / "impl.py":
        ["load_transcript_objs"],
    HOOKS / "preflight-gate" / "block-ask-end-option" / "impl.py":
        ["read_last_user_message"],
    HOOKS / "preflight-gate" / "block-manufactured-action-menu" / "impl.py":
        ["read_last_user_message"],
}

_CONSTANT_CONSUMERS = [
    HOOKS / "advisory-nudge" / "external-write-falsify-check" / "impl.py",
    HOOKS / "advisory-nudge" / "pre-output-falsification-gate" / "impl.py",
]


def _load_module(path: Path):
    name = f"hookmod_{path.parent.name}".replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestSingleSource:
    def test_consumers_bind_lib_function_objects(self):
        for path, symbols in _CONSUMERS.items():
            mod = _load_module(path)
            for sym in symbols:
                assert getattr(mod, sym) is getattr(T, sym), (
                    f"{path.parent.name} binds a different {sym} object"
                )

    def test_constant_consumers_bind_lib_value(self):
        for path in _CONSTANT_CONSUMERS:
            mod = _load_module(path)
            assert mod.TRANSCRIPT_SCAN_LINES == T.TRANSCRIPT_SCAN_LINES

    def test_no_local_redefinitions_remain(self):
        offenders = []
        for impl in HOOKS.glob("*/*/impl.py"):
            text = impl.read_text(encoding="utf-8")
            for needle in (
                "def _load_transcript", "def _read_last_user_message",
                "def _read_transcript_tail", "def _get_current_turn",
                "def _extract_last_assistant_text",
                "def _has_tool_in_turn", "def _load_transcript_objs",
                "_TRANSCRIPT_SCAN_LINES =",
            ):
                if needle in text:
                    offenders.append(f"{impl}: {needle}")
        assert not offenders, f"local duplicates remain: {offenders}"
