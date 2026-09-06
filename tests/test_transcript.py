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

import builtins
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

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

    def test_sidechain_user_msg_is_skipped(self, tmp_path):
        # A Task-subagent prompt is a user-role event with isSidechain=True
        # but is assistant-authored — the real human message earlier in the
        # transcript must be returned instead (#1097).
        path = _write_jsonl(tmp_path, [
            _user(text="real human message"),
            _user(text="agent subagent prompt", sidechain=True),
        ])
        assert T.read_last_user_message(path) == "real human message"


# ---------------------------------------------------------------------------
# scan_user_rejections (#1007 / #1013)
# ---------------------------------------------------------------------------

REJECTION_SENTENCE = (
    "The user doesn't want to proceed with this tool use. The tool use was "
    "rejected (eg. if it was a file edit, the new_string was NOT written to "
    "the file). STOP what you are doing and wait for the user to tell you how "
    "to proceed."
)


def _asst_tool_use(uuid: str, tool_use_id: str, name: str, tool_input: dict) -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "isSidechain": False,
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input},
        ]},
    }


def _rejection(tool_use_id: str, source_uuid: str, *, is_error=True,
               denial_kind="user-rejected", sentence=REJECTION_SENTENCE) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": sentence}
    if is_error is not None:
        block["is_error"] = is_error
    ev = {
        "type": "user",
        "uuid": f"rej-{tool_use_id}",
        "timestamp": "2026-08-15T00:00:00Z",
        "toolUseResult": "User rejected tool use",
        "sourceToolAssistantUUID": source_uuid,
        "message": {"role": "user", "content": [block]},
    }
    if denial_kind is not None:
        ev["toolDenialKind"] = denial_kind
    return ev


class TestScanUserRejections:
    """The live-transcript shape, pinned in both directions.

    Each negative case removes exactly one of the three structural markers the
    scan requires. A one-directional suite (only 'it finds the rejection')
    would pass equally well for a scan that returned every tool_result.
    """

    def test_resolves_tool_name_and_input_through_the_uuid_index(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "AskUserQuestion",
                           {"questions": [{"question": "Delete s3://b/raw/2024/ ?"}]}),
            _rejection("toolu_1", "A1"),
        ])
        recs = T.scan_user_rejections(path)
        assert len(recs) == 1
        assert recs[0]["tool_name"] == "AskUserQuestion"
        assert recs[0]["tool_use_id"] == "toolu_1"
        assert recs[0]["source_uuid"] == "A1"
        assert "s3://b/raw/2024/" in recs[0]["text"]

    def test_flattens_every_string_leaf_of_the_input(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "AskUserQuestion", {"questions": [{
                "question": "q-text", "header": "h-text",
                "options": [{"label": "l-text", "description": "d-text"}],
            }]}),
            _rejection("toolu_1", "A1"),
        ])
        text = T.scan_user_rejections(path)[0]["text"]
        for needle in ("q-text", "h-text", "l-text", "d-text"):
            assert needle in text

    def test_non_askuserquestion_tool_is_returned_with_its_own_name(self, tmp_path):
        # The scan does not filter by tool — consumers do. Returning the Bash
        # rejection with tool_name="Bash" is what lets the #1007 gate skip it
        # while the #1013 lane still counts it.
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "Bash", {"command": "aws s3 rm s3://b/x"}),
            _rejection("toolu_1", "A1"),
        ])
        recs = T.scan_user_rejections(path)
        assert [r["tool_name"] for r in recs] == ["Bash"]

    def test_missing_denial_kind_is_not_a_rejection(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "AskUserQuestion", {"questions": []}),
            _rejection("toolu_1", "A1", denial_kind=None),
        ])
        assert T.scan_user_rejections(path) == []

    def test_missing_is_error_is_not_a_rejection(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "AskUserQuestion", {"questions": []}),
            _rejection("toolu_1", "A1", is_error=None),
        ])
        assert T.scan_user_rejections(path) == []

    def test_missing_fixed_sentence_is_not_a_rejection(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "AskUserQuestion", {"questions": []}),
            _rejection("toolu_1", "A1", sentence="Tool call failed."),
        ])
        assert T.scan_user_rejections(path) == []

    def test_ordinary_tool_error_is_not_a_rejection(self, tmp_path):
        # The nearby input a broken scan would also match: a real is_error
        # tool_result from a failed command.
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "Bash", {"command": "false"}),
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True,
                 "content": "Exit code 2"}]}},
        ])
        assert T.scan_user_rejections(path) == []

    def test_unresolvable_tool_use_id_still_reports_the_rejection(self, tmp_path):
        path = _write_jsonl(tmp_path, [_rejection("toolu_missing", "A-gone")])
        recs = T.scan_user_rejections(path)
        assert len(recs) == 1
        assert recs[0]["tool_name"] == ""
        assert recs[0]["tool_input"] == {}

    def test_uuid_cross_check_rejects_a_replayed_tool_use_id(self, tmp_path):
        # Same tool_use_id under a different assistant uuid must not be adopted.
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("OTHER", "toolu_1", "AskUserQuestion",
                           {"questions": [{"question": "wrong record"}]}),
            _rejection("toolu_1", "A1"),
        ])
        assert T.scan_user_rejections(path)[0]["tool_name"] == ""

    def test_returns_oldest_to_newest_and_honours_max_records(self, tmp_path):
        events = []
        for i in range(4):
            events.append(_asst_tool_use(f"A{i}", f"toolu_{i}", "AskUserQuestion",
                                         {"questions": [{"question": f"q{i}"}]}))
            events.append(_rejection(f"toolu_{i}", f"A{i}"))
        path = _write_jsonl(tmp_path, events)
        assert [r["tool_use_id"] for r in T.scan_user_rejections(path)] == [
            "toolu_0", "toolu_1", "toolu_2", "toolu_3"]
        assert [r["tool_use_id"] for r in T.scan_user_rejections(path, max_records=2)] == [
            "toolu_2", "toolu_3"]

    def test_missing_file_reads_as_no_rejections(self, tmp_path):
        # An absent transcript is not evidence that a session history exists,
        # so it stays [] — only the byte bound signals indeterminate (#1231).
        assert T.scan_user_rejections("/nonexistent/x.jsonl") == []

    def test_oversize_file_is_indeterminate_not_empty(self, tmp_path):
        # The negative reproduction for #1231: a transcript that REALLY holds a
        # rejection, read past the bound. Before the fix this asserted [], which
        # is the same answer a clean session gives.
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "AskUserQuestion", {"questions": []}),
            _rejection("toolu_1", "A1"),
        ])
        assert T.scan_user_rejections(path, max_bytes=10) is None
        # Positive control on the same fixture: under the bound the identical
        # file enumerates the rejection, so the None above is the bound talking
        # and not a scan that stopped finding things.
        assert len(T.scan_user_rejections(path)) == 1

    def test_file_exactly_at_the_bound_is_scanned(self, tmp_path):
        # The bound is "past", not "at": a file whose size equals max_bytes is
        # inside it (#1280 kept the streamed reader on the same edge).
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "AskUserQuestion", {"questions": []}),
            _rejection("toolu_1", "A1"),
        ])
        size = os.path.getsize(path)
        assert len(T.scan_user_rejections(path, max_bytes=size)) == 1
        assert T.scan_user_rejections(path, max_bytes=size - 1) is None

    def test_scan_memory_does_not_track_file_size(self, tmp_path):
        # Both passes stream (#1280): the earlier reader held the bound's worth
        # of text twice (string + splitlines list). The peak must not grow with
        # the file, so a 100x larger session costs about the same to scan.
        import tracemalloc

        def build(n, name):
            d = tmp_path / name
            d.mkdir()
            filler = [_user(text="x" * 200) for _ in range(n)]
            return _write_jsonl(d, filler + [
                _asst_tool_use("A1", "toolu_1", "AskUserQuestion", {"questions": []}),
                _rejection("toolu_1", "A1"),
            ])

        small, big = build(100, "s"), build(10000, "b")

        def peak(path):
            tracemalloc.start()
            try:
                assert len(T.scan_user_rejections(path, max_bytes=1 << 30)) == 1
                return tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()

        assert peak(big) < 4 * peak(small) + 1 * 1024 * 1024

    def test_one_oversized_line_is_refused_before_allocation(self, tmp_path):
        # A single line past the bound must not be read whole and only then
        # measured: each read is capped at the budget left (#1280 review).
        import tracemalloc

        path = tmp_path / "t.jsonl"
        path.write_bytes(b'{"type": "user", "pad": "' + b"x" * (4 * 1024 * 1024) + b'"}\n')
        tracemalloc.start()
        try:
            assert T.scan_user_rejections(str(path), max_bytes=64 * 1024) is None
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert peak < 1024 * 1024

    def test_unreadable_but_oversized_stays_indeterminate(self, tmp_path, monkeypatch):
        # EACCES cannot be provoked as root, so the open is stubbed: the
        # answer must follow the size, not collapse to "no rejections".
        import builtins

        path = _write_jsonl(tmp_path, [_user(text="x" * 100)])
        size = os.path.getsize(path)
        real_open = builtins.open

        def denied(p, *a, **k):
            if str(p) == path:
                raise PermissionError(p)
            return real_open(p, *a, **k)

        monkeypatch.setattr(builtins, "open", denied)
        assert T.scan_user_rejections(path, max_bytes=size - 1) is None
        assert T.scan_user_rejections(path, max_bytes=size) == []

    def test_malformed_line_next_to_a_rejection_is_skipped(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "AskUserQuestion",
                           {"questions": [{"question": "q"}]}),
            'not-json {{{ "toolDenialKind" "doesn\'t want to proceed"',
            _rejection("toolu_1", "A1"),
        ])
        assert len(T.scan_user_rejections(path)) == 1


# ---------------------------------------------------------------------------
# Single source — every converted hook binds the SAME function objects
# ---------------------------------------------------------------------------

HOOKS = REPO_ROOT / "hooks"

_CONSUMERS = {
    HOOKS / "completion-verify" / "readonly-verify-deferral-gate" / "impl.py":
        ["load_current_turn", "extract_last_assistant_text"],
    HOOKS / "completion-verify" / "completion-signal-gate" / "impl.py":
        ["load_current_turn", "extract_last_assistant_text", "has_tool_in_turn"],
    # Reads a fixed window past the turn (`events[-_EVIDENCE_WINDOW:]`), so it
    # binds the min_events reader and slices the turn out itself (#1076).
    HOOKS / "completion-verify" / "merge-state-claim-gate" / "impl.py":
        ["load_recent_events", "get_current_turn", "extract_last_assistant_text"],
    HOOKS / "completion-verify" / "negative-existence-verdict-gate" / "impl.py":
        ["load_current_turn", "extract_last_assistant_text"],
    HOOKS / "completion-verify" / "proposal-premise-gate" / "impl.py":
        ["load_current_turn", "extract_last_assistant_text"],
    HOOKS / "completion-verify" / "pr-claim-mutation-gate" / "impl.py":
        ["load_current_turn", "extract_last_assistant_text"],
    # Also streams the turns BEFORE the current one, reusing the shared boundary
    # predicate so the backward and forward directions cannot disagree (#1076).
    HOOKS / "completion-verify" / "runtime-state-claim-gate" / "impl.py":
        ["load_current_turn", "extract_last_assistant_text",
         "is_turn_boundary", "reduce_transcript_resumable", "stop_scan_cursor_path"],
    HOOKS / "completion-verify" / "artifact-verdict-evidence-gate" / "impl.py":
        ["load_current_turn", "extract_last_assistant_text"],
    # The one scan that genuinely needs the whole session; it streams instead
    # of materializing it (#1076).
    HOOKS / "completion-verify" / "pr-report-destination-gate" / "impl.py":
        ["reduce_transcript_resumable", "stop_scan_cursor_path"],
    # Same whole-session rationale as pr-report-destination-gate above (#1113).
    HOOKS / "completion-verify" / "pr-anchor-existence-gate" / "impl.py":
        ["reduce_transcript_resumable", "stop_scan_cursor_path"],
    # Matches search commands in the last N lines only; reads the tail from the
    # end instead of loading up to 50 MB to keep 400 lines (#1279).
    HOOKS / "preflight-gate" / "block-gh-issue-create-without-dup-search" / "impl.py":
        ["tail_lines", "TranscriptReadError"],
    # Whole-session scan, needle-prefiltered and resumable: one cursor per
    # transcript file and session, a byte budget per call (#1277).
    HOOKS / "preflight-gate" / "block-commit-without-codex-review" / "impl.py":
        ["scan_transcript_resumable", "scan_cursor_path", "TranscriptReadError"],
    # Whole-session scans under a byte cap, needle-prefiltered (#1312).
    HOOKS / "preflight-gate" / "skill-gate-commands" / "impl.py":
        ["iter_transcript_bounded", "TranscriptReadError", "json_needle"],
    HOOKS / "advisory-nudge" / "pre-commit-staged-file-enumeration" / "impl.py":
        ["iter_transcript_bounded", "TranscriptReadError"],
    HOOKS / "preflight-gate" / "block-ask-end-option" / "impl.py":
        ["read_last_user_message"],
    HOOKS / "preflight-gate" / "block-manufactured-action-menu" / "impl.py":
        ["read_last_user_message"],
    # Resumable through a session-keyed cursor so the byte bound is a budget
    # per call, not a ceiling on the session (#1280).
    HOOKS / "preflight-gate" / "rejected-mutation-reconsent-gate" / "impl.py":
        ["scan_user_rejections", "scan_cursor_path"],
    # Counts this turn's delegation targets and quotes the request they were
    # meant to serve, so it binds the turn reader and the user-message reader.
    HOOKS / "preflight-gate" / "fan-out-scope-gate" / "impl.py":
        ["load_current_turn", "read_last_user_message"],
    # Match only against the last N lines, so they read the tail instead of
    # `readlines()` over the whole transcript (#1240).
    HOOKS / "advisory-nudge" / "caller-probe-gate" / "impl.py": ["tail_lines"],
    HOOKS / "advisory-nudge" / "source-citation-probe-gate" / "impl.py": ["tail_lines"],
    HOOKS / "advisory-nudge" / "pre-output-falsification-gate" / "impl.py": ["tail_lines"],
    HOOKS / "advisory-nudge" / "external-write-falsify-check" / "impl.py": ["tail_lines"],
    # Needs the whole session (a dispatch or enumeration anywhere in it clears
    # the predicate), so it streams instead of reading a tail; the scan stops
    # as soon as the matched trigger's facts are settled (#1064) and resumes
    # from a per-trigger cursor on the next commit (#1278).
    HOOKS / "advisory-nudge" / "unenforced-step-advisory" / "impl.py":
        ["scan_transcript_resumable", "scan_cursor_path", "TranscriptReadError"],
    # Also correlates each Bash tool_use with its result, so it binds the
    # refusal sentence the never-ran markers are keyed on (#1117).
    HOOKS / "advisory-nudge" / "composed-command-gate" / "impl.py":
        ["tail_lines", "TranscriptReadError"],
}

# Constants are values, not bindings, so the function map above cannot pin them:
# a hook that redefines one locally keeps passing every binding test while its
# scan window or refusal wording silently drifts from the shared source.
_CONSTANT_CONSUMERS = {
    HOOKS / "advisory-nudge" / "external-write-falsify-check" / "impl.py":
        ["TRANSCRIPT_SCAN_LINES"],
    HOOKS / "advisory-nudge" / "pre-output-falsification-gate" / "impl.py":
        ["TRANSCRIPT_SCAN_LINES"],
    HOOKS / "advisory-nudge" / "momentum-rule-retrieval-gate" / "impl.py":
        ["TRANSCRIPT_SCAN_LINES"],
    HOOKS / "advisory-nudge" / "source-citation-probe-gate" / "impl.py":
        ["TRANSCRIPT_SCAN_LINES"],
    HOOKS / "advisory-nudge" / "composed-command-gate" / "impl.py":
        ["TRANSCRIPT_SCAN_LINES", "REJECTION_PHRASE"],
    HOOKS / "preflight-gate" / "block-gh-issue-create-without-dup-search" / "impl.py":
        ["TRANSCRIPT_SCAN_LINES"],
}


def _load_module(path: Path):
    name = f"hookmod_{path.parent.name}".replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestSingleSource:
    def test_consumers_list_every_lib_function_they_import(self):
        """A hook importing a reader the map omits is unpinned — it could
        redefine that reader locally and no test above would notice. Constants
        (`TRANSCRIPT_SCAN_LINES`) are not bindings and are skipped."""
        import ast

        for impl in sorted(HOOKS.glob("*/*/impl.py")):
            imported: set[str] = set()
            for node in ast.walk(ast.parse(impl.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module == "_transcript":
                    imported |= {a.name for a in node.names}
            callables = {s for s in imported if callable(getattr(T, s, None))}
            if not callables:
                continue
            declared = set(_CONSUMERS.get(impl, []))
            assert callables <= declared, (
                f"{impl.parent.name} imports {sorted(callables - declared)} "
                f"but _CONSUMERS does not list them"
            )

    def test_consumers_bind_lib_function_objects(self):
        for path, symbols in _CONSUMERS.items():
            mod = _load_module(path)
            for sym in symbols:
                assert getattr(mod, sym) is getattr(T, sym), (
                    f"{path.parent.name} binds a different {sym} object"
                )

    def test_constant_consumers_bind_lib_value(self):
        for path, names in _CONSTANT_CONSUMERS.items():
            mod = _load_module(path)
            for name in names:
                assert getattr(mod, name) == getattr(T, name), (
                    f"{path.parent.name} carries a different {name} value"
                )

    def test_constant_consumers_list_every_constant_imported(self):
        """A constant the map omits is unpinned — same gap as an omitted
        reader, but invisible to the binding tests because a constant is a
        value rather than an object identity."""
        import ast

        constants = {
            s
            for s in dir(T)
            if s.isupper() and not callable(getattr(T, s, None))
        }
        for impl in sorted(HOOKS.glob("*/*/impl.py")):
            imported: set[str] = set()
            for node in ast.walk(ast.parse(impl.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module == "_transcript":
                    imported |= {a.name for a in node.names}
            wanted = imported & constants
            if not wanted:
                continue
            declared = set(_CONSTANT_CONSUMERS.get(impl, []))
            assert wanted <= declared, (
                f"{impl.parent.name} imports {sorted(wanted - declared)} "
                f"but _CONSTANT_CONSUMERS does not list them"
            )

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


class TestTailReaders:
    """The bounded backward readers that replaced the whole-file loads (#1076).

    A Stop-event session JSONL reaches hundreds of MB; nine gates each parsed
    all of it on every response end. These cover that the tail readers agree
    with the forward path they replaced, that the bound is real, and that the
    two capped terminations behave as documented.
    """

    def test_current_turn_matches_the_forward_path(self, tmp_path):
        events = [
            _user(text="first"), _assistant(text="a1"),
            _user(text="second"), _assistant(text="a2"), _assistant(text="a3"),
        ]
        path = _write_jsonl(tmp_path, events)
        assert T.load_current_turn(path) == T.get_current_turn(T.load_transcript(path))

    def test_tool_result_only_user_entry_is_not_a_boundary(self, tmp_path):
        """The bridge for tool output is not human input — same rule as forward."""
        path = _write_jsonl(tmp_path, [
            _user(text="real input"),
            _assistant(text="a1"),
            _user(blocks=[{"type": "tool_result", "content": "out"}]),
            _assistant(text="a2"),
        ])
        turn = T.load_current_turn(path)
        assert turn == T.get_current_turn(T.load_transcript(path))
        # The turn reaches back past the tool_result entry to the real input,
        # so both assistant events are in it — 3 events, not 1.
        assert len(turn) == 3

    def test_no_boundary_anywhere_returns_every_event(self, tmp_path):
        """`get_current_turn` returns everything when it finds no user input."""
        path = _write_jsonl(tmp_path, [_assistant(text="a1"), _assistant(text="a2")])
        assert T.load_current_turn(path) == T.load_transcript(path)

    def test_missing_file_fails_open_empty(self, tmp_path):
        assert T.load_current_turn(str(tmp_path / "absent.jsonl")) == []

    def test_boundary_split_across_a_read_chunk(self, tmp_path):
        """A record straddling the chunk seam must parse whole, not as a fragment.

        The reader carries the leading partial line into the next chunk; without
        that the boundary record is silently dropped and the turn runs long.
        """
        events = [_user(text="B" * (T._TAIL_CHUNK_BYTES // 2))]
        events += [_assistant(text="x" * 4096) for _ in range(200)]
        path = _write_jsonl(tmp_path, events)
        assert T.load_current_turn(path) == T.get_current_turn(T.load_transcript(path))

    def test_min_events_reaches_past_the_turn(self, tmp_path):
        """merge-state-claim-gate's `events[-N:]` needs the window, not the turn."""
        events = [_assistant(text=f"old{i}") for i in range(50)]
        events += [_user(text="now"), _assistant(text="fresh")]
        path = _write_jsonl(tmp_path, events)
        full = T.load_transcript(path)
        assert T.load_recent_events(path, min_events=0) == full[-2:]
        assert T.load_recent_events(path, min_events=30)[-30:] == full[-30:]

    def test_the_bound_is_enforced(self, tmp_path):
        """A cap short of the boundary stops the read instead of walking to BOF.

        What it returns is asserted in `TestCappedScanFailsOpen`: the tail it
        holds has lost the front of the turn, so it fails open rather than
        passing a subset off as the turn.
        """
        events = [_user(text="boundary")] + [_assistant(text="y" * 512) for _ in range(200)]
        path = _write_jsonl(tmp_path, events)
        read_bytes = sum(
            len(raw) + 1 for raw in T._iter_lines_backwards(path, 4096)
        )
        assert read_bytes <= 4096 + T._TAIL_CHUNK_BYTES
        assert read_bytes < os.path.getsize(path)

    def test_last_user_message_matches_the_forward_path(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _user(text="older"), _assistant(text="a"), _user(text="newest"),
        ])
        assert T.read_last_user_message(path) == "newest"

    def test_last_user_message_returns_none_when_the_cap_cut_it_short(
        self, tmp_path, monkeypatch
    ):
        """"" means 'read it all, no signal' and callers act on it.

        When the scan cap stopped short of the start of the file that is not
        true, so the honest answer is the unreadable one — None, which every
        caller fails open on.
        """
        path = _write_jsonl(tmp_path, [
            _user(text="buried"),
            *[_assistant(text="z" * 512) for _ in range(50)],
        ])
        monkeypatch.setattr(T, "CURRENT_TURN_SCAN_MAX_BYTES", 1024)
        assert T.read_last_user_message(path) is None

    def test_iter_transcript_matches_load_transcript(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _user(text="hi"), "broken", json.dumps([1]), _assistant(text="a"),
        ])
        assert list(T.iter_transcript(path)) == T.load_transcript(path)

    def test_iter_transcript_missing_file_yields_nothing(self, tmp_path):
        assert list(T.iter_transcript(str(tmp_path / "absent.jsonl"))) == []

    def test_iter_transcript_needle_skips_lines_without_it(self, tmp_path, monkeypatch):
        """A line without the needle never reaches `json.loads`."""
        # The needle is a pre-parse reject (#1278): a line without it is never
        # handed to json.loads, one with it still goes through the full parse
        # and dict check. Counted through the parser so the test cannot pass
        # on a filter that merely drops the yielded dicts afterwards.
        path = _write_jsonl(tmp_path, [
            _user(text="plain"),
            _asst_tool_use("A1", "toolu_1", "Bash", {"command": "ls"}),
            'not json but has "tool_use"',
            _user(text="another"),
        ])
        calls = []
        real = T.json.loads

        def counting(s, *a, **k):
            """Record every string handed to the parser, then parse it."""
            calls.append(s)
            return real(s, *a, **k)

        monkeypatch.setattr(T.json, "loads", counting)
        got = list(T.iter_transcript(path, needle='"tool_use"'))
        assert len(got) == 1 and got[0]["uuid"] == "A1"
        assert len(calls) == 2  # the tool_use record and the non-JSON line only

    def test_iter_transcript_any_of_needle(self, tmp_path):
        """A tuple needle keeps a line carrying any one of its tokens."""
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "t1", "Bash", {"command": "ls"}),
            _asst_tool_use("A2", "t2", "Agent", {"prompt": "review"}),
            _asst_tool_use("A3", "t3", "Skill", {"skill": "x"}),
        ])
        got = [e["uuid"] for e in T.iter_transcript(path, needle=('"Agent"', '"Skill"'))]
        assert got == ["A2", "A3"]

    def test_iter_transcript_without_needle_is_unchanged(self, tmp_path):
        """No needle (or `needle=None`) yields every record as before."""
        path = _write_jsonl(tmp_path, [_user(text="a"), _user(text="b")])
        assert list(T.iter_transcript(path)) == list(T.iter_transcript(path, needle=None))
        assert len(list(T.iter_transcript(path))) == 2


class TestCappedScanFailsOpen:
    """A capped backward scan is a subset of the turn, never a superset.

    The scan runs end-to-start, so cutting it short at `max_bytes` drops the
    *earliest* events of the turn. A gate handed that tail would look for
    evidence that is present in the turn, not find it, and block (codex review
    round 1 on #1076, P1 — confirmed against a 1.33 MB turn under a 256 KiB cap:
    590 events, first event of the turn absent).
    """

    def _over_cap_turn(self, tmp_path):
        return _write_jsonl(tmp_path, [
            _user(text="the real user input"),
            _assistant(text="FIRST_AFTER_BOUNDARY"),
            *[_assistant(text="y" * 512) for _ in range(60)],
        ])

    def test_current_turn_is_empty_when_the_cap_hides_the_boundary(
        self, tmp_path
    ):
        path = self._over_cap_turn(tmp_path)
        assert T.load_current_turn(path, max_bytes=1024) == []

    def test_recent_events_is_empty_when_the_cap_hides_the_boundary(
        self, tmp_path
    ):
        path = self._over_cap_turn(tmp_path)
        assert T.load_recent_events(path, max_bytes=1024) == []

    def test_whole_file_without_a_boundary_is_still_returned(self, tmp_path):
        """Reaching the start of the file is not truncation."""
        path = _write_jsonl(tmp_path, [
            _assistant(text="a"), _assistant(text="b"),
        ])
        assert len(T.load_recent_events(path, max_bytes=10 * 1024)) == 2

    def test_boundary_found_within_the_cap_is_unaffected(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _user(text="hi"), _assistant(text="one"), _assistant(text="two"),
        ])
        turn = T.load_current_turn(path, max_bytes=10 * 1024)
        assert len(turn) == 2


class TestCapDecidedByTheReader:
    """`""` vs `None` is decided by whether the walk reached the file start.

    A size sampled before the walk cannot answer it: the transcript is appended
    to live, so a file that measured under the cap can be over it by the time
    the reader gets there — and `""` ("read it all, no signal") is acted on
    while `None` fails open (codex review #1083 P2).
    """

    @staticmethod
    def _drain(walk):
        """The value is carried by the StopIteration that ends the walk — a
        `for` loop swallows it, and a later `next()` sees a closed generator."""
        while True:
            try:
                next(walk)
            except StopIteration as done:
                return done.value

    def test_reader_reports_reaching_the_start(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text("a\nb\nc\n")
        assert self._drain(T._iter_lines_backwards(str(path), 1024)) is True

    def test_reader_reports_stopping_at_the_cap(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text(("x" * 100 + "\n") * 50)
        assert self._drain(T._iter_lines_backwards(str(path), 200)) is False

    def test_stale_size_cannot_turn_a_capped_scan_into_no_signal(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "t.jsonl"
        line = json.dumps(
            {"type": "assistant",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "x" * 80}]}}
        )
        path.write_text((line + "\n") * 60)
        monkeypatch.setattr(T, "CURRENT_TURN_SCAN_MAX_BYTES", 2000)
        # A size from before the file grew past the cap — the shape the live
        # transcript actually produces.
        real = os.path.getsize
        monkeypatch.setattr(
            T.os.path, "getsize", lambda q: 1500 if q == str(path) else real(q)
        )
        assert T.read_last_user_message(str(path)) is None

    def test_within_the_cap_still_answers_no_signal(self, tmp_path):
        """Positive control — `None` everywhere would pass the test above."""
        path = tmp_path / "t.jsonl"
        path.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                                     "content": []}}) + "\n")
        assert T.read_last_user_message(str(path)) == ""


class TestReadFailurePropagates:
    """A read that fails partway is not a transcript with no signal in it.

    `read_last_user_message` returning "" says "read it all, found nothing" and
    both preflight callers act on it; only None makes them fail open. An
    `OSError` raised after `getsize` succeeded used to end the iterator
    silently and produce "" (codex review round 1 on #1076, P1 — confirmed).
    """

    def _explode_on_binary_open(self, monkeypatch, path):
        real_open = builtins.open

        def boom(target, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "r")
            if str(target) == str(path) and "b" in mode:
                raise OSError("simulated read failure")
            return real_open(target, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", boom)

    def test_last_user_message_is_none_when_the_read_fails(
        self, tmp_path, monkeypatch
    ):
        path = _write_jsonl(tmp_path, [_user(text="hi")])
        self._explode_on_binary_open(monkeypatch, path)
        assert T.read_last_user_message(path) is None

    def test_recent_events_fails_open_when_the_read_fails(
        self, tmp_path, monkeypatch
    ):
        path = _write_jsonl(tmp_path, [_user(text="hi"), _assistant(text="a")])
        self._explode_on_binary_open(monkeypatch, path)
        assert T.load_recent_events(path) == []

    def test_iter_lines_backwards_raises_rather_than_ending(
        self, tmp_path, monkeypatch
    ):
        path = _write_jsonl(tmp_path, [_user(text="hi")])
        self._explode_on_binary_open(monkeypatch, path)
        with pytest.raises(T.TranscriptReadError):
            list(T._iter_lines_backwards(path, 1024))


class TestTailLines:
    """`tail_lines` — the last-N-lines reader behind the advisory hooks (#1240)."""

    @staticmethod
    def _write(path, lines, trailing_newline=True):
        text = "\n".join(lines) + ("\n" if trailing_newline else "")
        path.write_text(text, encoding="utf-8")

    def test_returns_the_last_n_lines_in_file_order(self, tmp_path):
        path = tmp_path / "t.jsonl"
        self._write(path, [f'{{"i": {i}}}' for i in range(10)])
        assert T.tail_lines(str(path), 3) == ['{"i": 7}', '{"i": 8}', '{"i": 9}']

    def test_file_shorter_than_n_returns_every_line(self, tmp_path):
        path = tmp_path / "t.jsonl"
        self._write(path, ['{"i": 1}', '{"i": 2}'])
        assert T.tail_lines(str(path), 400) == ['{"i": 1}', '{"i": 2}']

    def test_last_line_without_newline_is_included(self, tmp_path):
        path = tmp_path / "t.jsonl"
        self._write(path, ['{"i": 1}', '{"i": 2'], trailing_newline=False)
        assert T.tail_lines(str(path), 2) == ['{"i": 1}', '{"i": 2']

    def test_trailing_blank_lines_count_as_lines(self, tmp_path):
        """Only the split artifact after the final newline is dropped."""
        path = tmp_path / "t.jsonl"
        path.write_bytes(b'{"i": 1}\n{"i": 2}\n\n\n')
        assert T.tail_lines(str(path), 3) == ['{"i": 2}', "", ""]

    def test_matches_readlines_semantics_on_a_multi_chunk_file(self, tmp_path):
        path = tmp_path / "t.jsonl"
        lines = [f'{{"i": {i}, "pad": "{"x" * 3000}"}}' for i in range(500)]
        self._write(path, lines)
        expected = [ln.rstrip("\n") for ln in path.open(encoding="utf-8").readlines()][-400:]
        assert T.tail_lines(str(path), 400) == expected

    def test_default_read_is_not_capped_by_bytes(self, tmp_path):
        """The callers' contract is the last N lines, however wide they are:
        a cap that stopped short would hand a gate a window that looks
        complete but is missing the evidence just outside the suffix."""
        path = tmp_path / "t.jsonl"
        lines = [f'{{"i": {i}, "pad": "{"x" * 200_000}"}}' for i in range(60)]
        self._write(path, lines)  # 60 lines, ~12 MB — past the 8 MB turn bound
        got = T.tail_lines(str(path), 50)
        assert len(got) == 50 and got[0].startswith('{"i": 10,')

    def test_explicit_byte_cap_returns_the_most_recent_lines_it_reached(self, tmp_path):
        path = tmp_path / "t.jsonl"
        self._write(path, [f'{{"i": {i}}}' for i in range(100)])
        got = T.tail_lines(str(path), 100, max_bytes=40)
        assert got and got[-1] == '{"i": 99}' and len(got) < 100

    def test_missing_or_unreadable_file_is_empty(self, tmp_path):
        assert T.tail_lines(str(tmp_path / "nope.jsonl"), 5) == []
        assert T.tail_lines(str(tmp_path), 5) == []

    def test_undecodable_bytes_are_replaced_not_raised(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_bytes(b'{"ok": 1}\n\xff\xfe{"bad": 1}\n')
        got = T.tail_lines(str(path), 2)
        assert got[0] == '{"ok": 1}' and "\ufffd" in got[1]


class TestTailLinesStrict:
    def test_strict_raises_where_default_answers_empty(self, tmp_path):
        """`strict=True` raises on an unreadable path instead of folding it to `[]`."""
        absent = str(tmp_path / "absent.jsonl")
        assert T.tail_lines(absent, 3) == []
        with pytest.raises(T.TranscriptReadError):
            T.tail_lines(absent, 3, strict=True)

    def test_strict_keeps_the_empty_file_answer(self, tmp_path):
        """An empty but readable file is still `[]` under `strict=True`."""
        path = tmp_path / "empty.jsonl"
        path.write_bytes(b"")
        assert T.tail_lines(str(path), 3, strict=True) == []


class TestReduceTranscriptResumable:
    """`reduce_transcript_resumable` — the Stop gates' incremental scan (#1237)."""

    @staticmethod
    def _append(path, events):
        with open(path, "a", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")

    @staticmethod
    def _run(path, cursor, seen):
        def reduce_event(state, ev):
            state["n"] += 1
            seen.append(ev["i"])

        return T.reduce_transcript_resumable(str(path), cursor, lambda: {"n": 0}, reduce_event)

    def test_second_run_folds_only_the_appended_events(self, tmp_path):
        path = tmp_path / "t.jsonl"
        cursor = str(tmp_path / "cursor.json")
        self._append(path, [{"i": 1}, {"i": 2}])
        seen: list = []
        assert self._run(path, cursor, seen) == {"n": 2}
        self._append(path, [{"i": 3}])
        seen.clear()
        assert self._run(path, cursor, seen) == {"n": 3}
        assert seen == [3]

    def test_partial_trailing_line_is_read_once_it_completes(self, tmp_path):
        path = tmp_path / "t.jsonl"
        cursor = str(tmp_path / "cursor.json")
        self._append(path, [{"i": 1}])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write('{"i": 2')  # still being written when the Stop fires
        seen: list = []
        assert self._run(path, cursor, seen) == {"n": 1}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("}\n")
        seen.clear()
        assert self._run(path, cursor, seen) == {"n": 2}
        assert seen == [2]

    def test_shrunken_file_restarts_from_the_top(self, tmp_path):
        path = tmp_path / "t.jsonl"
        cursor = str(tmp_path / "cursor.json")
        self._append(path, [{"i": 1}, {"i": 2}, {"i": 3}])
        self._run(path, cursor, [])
        path.write_text(json.dumps({"i": 9}) + "\n", encoding="utf-8")
        seen: list = []
        assert self._run(path, cursor, seen) == {"n": 1}
        assert seen == [9]

    def test_offset_not_on_a_line_boundary_restarts_from_the_top(self, tmp_path):
        """A same-size rewrite that moves the record boundaries must not resume
        mid-record: the byte before the offset has to be a newline."""
        path = tmp_path / "t.jsonl"
        cursor = str(tmp_path / "cursor.json")
        self._append(path, [{"i": 1}])
        self._run(path, cursor, [])
        stored = json.loads(Path(cursor).read_text(encoding="utf-8"))
        # Same inode, longer file, and the old offset now lands inside the
        # one record the file holds.
        with open(path, "r+b") as fh:
            fh.seek(0)
            fh.write(b'{"i": 12345678901}\n')
        assert Path(path).stat().st_size > stored["offset"]
        seen: list = []
        assert self._run(path, cursor, seen) == {"n": 1}
        assert seen == [12345678901]

    def test_replaced_file_restarts_from_the_top(self, tmp_path):
        path = tmp_path / "t.jsonl"
        cursor = str(tmp_path / "cursor.json")
        self._append(path, [{"i": 1}, {"i": 2}])
        self._run(path, cursor, [])
        other = tmp_path / "other.jsonl"
        self._append(other, [{"i": 7}, {"i": 8}])
        os.replace(other, path)  # new inode at the same path
        seen: list = []
        assert self._run(path, cursor, seen) == {"n": 2}
        assert seen == [7, 8]

    def test_file_replaced_mid_scan_binds_the_cursor_to_the_scanned_inode(self, tmp_path):
        """The saved ino/dev must come from the handle that was read, not a
        later stat of the path: a transcript swapped during the scan would
        otherwise pair the new inode with the old file's offset and state,
        and the next run would resume inside a file it never folded."""
        path = tmp_path / "t.jsonl"
        cursor = str(tmp_path / "cursor.json")
        self._append(path, [{"i": 1}, {"i": 2}])
        other = tmp_path / "other.jsonl"
        self._append(other, [{"i": 7}, {"i": 8}, {"i": 9}])

        def swap_then_count(state, ev):
            state["n"] += 1
            if not state.get("swapped"):
                state["swapped"] = True
                os.replace(other, path)  # replaced while the old handle is scanned

        state = T.reduce_transcript_resumable(
            str(path), cursor, lambda: {"n": 0}, swap_then_count
        )
        assert state["n"] == 2
        seen: list = []
        assert self._run(path, cursor, seen) == {"n": 3}
        assert seen == [7, 8, 9]

    def test_unreadable_transcript_returns_the_resumed_state(self, tmp_path):
        """Fail-open keeps what the last Stop knew rather than an empty state."""
        path = tmp_path / "t.jsonl"
        cursor = str(tmp_path / "cursor.json")
        self._append(path, [{"i": 1}, {"i": 2}])
        assert self._run(path, cursor, []) == {"n": 2}
        path.unlink()
        assert self._run(path, cursor, []) == {"n": 2}
        assert self._run(tmp_path / "never.jsonl", None, []) == {"n": 0}

    def test_undecodable_cursor_state_restarts_without_raising(self, tmp_path):
        path = tmp_path / "t.jsonl"
        cursor = tmp_path / "cursor.json"
        self._append(path, [{"i": 1}])
        self._run(path, str(cursor), [])
        stored = json.loads(cursor.read_text(encoding="utf-8"))
        stored["state"] = {"unexpected": True}
        cursor.write_text(json.dumps(stored), encoding="utf-8")

        def decode(data):
            return {"n": data["n"]}

        seen: list = []
        state = T.reduce_transcript_resumable(
            str(path), str(cursor), lambda: {"n": 0},
            lambda st, ev: (st.__setitem__("n", st["n"] + 1), seen.append(ev["i"])),
            decode=decode,
        )
        assert state == {"n": 1} and seen == [1]

    def test_encode_and_decode_round_trip_a_set(self, tmp_path):
        path = tmp_path / "t.jsonl"
        cursor = str(tmp_path / "cursor.json")
        self._append(path, [{"i": 1}])

        def run():
            return T.reduce_transcript_resumable(
                str(path), cursor, lambda: {"ids": set()},
                lambda st, ev: st["ids"].add(ev["i"]),
                encode=lambda st: {"ids": sorted(st["ids"])},
                decode=lambda d: {"ids": set(d["ids"])},
            )

        assert run() == {"ids": {1}}
        self._append(path, [{"i": 2}])
        assert run() == {"ids": {1, 2}}

    def test_no_cursor_path_scans_fully_and_writes_nothing(self, tmp_path):
        path = tmp_path / "t.jsonl"
        self._append(path, [{"i": 1}, {"i": 2}])
        assert self._run(path, None, []) == {"n": 2}
        assert self._run(path, None, []) == {"n": 2}
        assert sorted(p.name for p in tmp_path.iterdir()) == ["t.jsonl"]

    def test_missing_transcript_yields_the_empty_state(self, tmp_path):
        assert self._run(tmp_path / "absent.jsonl", str(tmp_path / "c.json"), []) == {"n": 0}

    def test_cursor_path_is_session_keyed_or_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_HOME", str(tmp_path))
        assert T.stop_scan_cursor_path("some-gate", None) is None
        assert T.stop_scan_cursor_path("some-gate", "") is None
        p = T.stop_scan_cursor_path("some-gate", "sid-1")
        assert p == str(tmp_path / "cache" / "stop-scan-some-gate-sid-1.json")


class TestIterTranscriptBounded:
    """The shared bounded reader (#1277, #1312): one loop for every gate that
    scans a whole session under a size cap."""

    def test_yields_only_needle_lines_and_parses_them(self, tmp_path, monkeypatch):
        path = _write_jsonl(tmp_path, [
            _user(text="plain"),
            _asst_tool_use("A1", "toolu_1", "Skill", {"skill": "praxis:x"}),
            'not json but has "Skill"',
        ])
        calls = []
        real = T.json.loads

        def counting(s, *a, **k):
            calls.append(s)
            return real(s, *a, **k)

        monkeypatch.setattr(T.json, "loads", counting)
        got = list(T.iter_transcript_bounded(path, 1 << 20, b'"Skill"'))
        assert [g["uuid"] for g in got] == ["A1"]
        assert len(calls) == 2  # the record and the non-JSON line; the plain line never parsed

    def test_any_of_needle_and_no_needle(self, tmp_path):
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "t1", "Bash", {"command": "ls"}),
            _asst_tool_use("A2", "t2", "Skill", {"skill": "x"}),
            _user(text="hello"),
        ])
        assert [g["uuid"] for g in T.iter_transcript_bounded(path, 1 << 20, (b'"Bash"', b'"Skill"'))] == ["A1", "A2"]
        assert len(list(T.iter_transcript_bounded(path, 1 << 20))) == 3

    def test_missing_and_non_regular_paths_raise(self, tmp_path):
        import os

        with pytest.raises(T.TranscriptReadError):
            list(T.iter_transcript_bounded(str(tmp_path / "absent.jsonl"), 1 << 20))
        fifo = tmp_path / "t.fifo"
        os.mkfifo(fifo)
        with pytest.raises(T.TranscriptReadError):
            list(T.iter_transcript_bounded(str(fifo), 1 << 20))  # must not block on open()
        with pytest.raises(T.TranscriptReadError):
            list(T.iter_transcript_bounded("bad\x00path", 1 << 20))

    def test_over_the_bound_raises_too_large_before_reading(self, tmp_path, monkeypatch):
        path = _write_jsonl(tmp_path, [_user(text="x" * 100)] * 10)
        size = os.path.getsize(path)
        reads = []
        real = T._parse_line
        monkeypatch.setattr(T, "_parse_line", lambda raw: reads.append(raw) or real(raw))
        with pytest.raises(T.TranscriptTooLarge):
            list(T.iter_transcript_bounded(path, size - 1))
        assert reads == []  # fstat early-out: nothing was parsed
        assert len(list(T.iter_transcript_bounded(path, size))) == 10  # at the bound is inside

    def test_too_large_is_a_read_error(self):
        assert issubclass(T.TranscriptTooLarge, T.TranscriptReadError)

    def test_one_oversized_line_is_refused_before_allocation(self, tmp_path, monkeypatch):
        # The fstat early-out is what normally catches a file this size, so it
        # is stubbed to report a small file: the line itself must then be
        # stopped by the per-read cap, before it is allocated in full.
        import tracemalloc

        path = tmp_path / "t.jsonl"
        path.write_bytes(b'{"type": "user", "pad": "' + b"x" * (4 * 1024 * 1024) + b'"}\n')
        small = type("St", (), {"st_size": 0})()
        monkeypatch.setattr(T.os, "fstat", lambda fd: small)
        tracemalloc.start()
        try:
            with pytest.raises(T.TranscriptTooLarge):
                list(T.iter_transcript_bounded(str(path), 64 * 1024))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert peak < 1024 * 1024


class TestJsonNeedle:
    def test_plain_ascii_value_is_quoted(self):
        assert T.json_needle("praxis:codex-review-wrap") == b'"praxis:codex-review-wrap"'

    def test_values_the_encoder_rewrites_answer_none(self):
        assert T.json_needle('say "hi"') is None
        assert T.json_needle("a\\b") is None
        assert T.json_needle("praxis:회고") is None  # escaped by the default encoder


class TestScanTranscriptResumable:
    """`scan_transcript_resumable` — the budgeted, needle-prefiltered cursor
    scan behind the three whole-session gates (#1277 / #1278 / #1280)."""

    @staticmethod
    def _append(path, events):
        """Append `events` as JSONL, one complete line each."""
        with open(path, "a", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")

    @staticmethod
    def _run(path, cursor, seen, **kw):
        """Fold `path` into a counting state, recording each event's `i`."""
        def reduce_event(state, ev):
            """Count the event and record its `i`."""
            state["n"] += 1
            seen.append(ev["i"])

        return T.scan_transcript_resumable(str(path), cursor, lambda: {"n": 0}, reduce_event, **kw)

    def test_budget_cut_reports_incomplete_and_the_next_call_continues(self, tmp_path):
        """The budget is per call: the second call starts where the first stopped."""
        path = tmp_path / "t.jsonl"
        cursor = str(tmp_path / "cursor.json")
        events = [{"i": i, "pad": "x" * 90} for i in range(5)]
        self._append(path, events)
        line = len(json.dumps(events[0])) + 1
        seen: list = []
        state, complete = self._run(path, cursor, seen, max_bytes=2 * line + 5)
        assert (state, complete) == ({"n": 2}, False)
        assert seen == [0, 1]
        seen.clear()
        state, complete = self._run(path, cursor, seen, max_bytes=10 * line)
        assert (state, complete) == ({"n": 5}, True)
        assert seen == [2, 3, 4]

    def test_budget_spent_exactly_at_eof_is_complete(self, tmp_path):
        """A budget that ends on the last newline is not reported as a cut."""
        path = tmp_path / "t.jsonl"
        self._append(path, [{"i": 1}, {"i": 2}])
        size = path.stat().st_size
        assert self._run(path, None, [], max_bytes=size)[1] is True
        assert self._run(path, None, [], max_bytes=size - 1)[1] is False

    def test_needle_rejects_lines_before_the_parser(self, tmp_path, monkeypatch):
        """A line without the needle never reaches `json.loads`."""
        path = tmp_path / "t.jsonl"
        self._append(path, [{"i": 1, "k": "keep"}, {"i": 2}, {"i": 3, "k": "keep"}])
        calls: list = []
        real = T.json.loads

        def counting(s, *a, **k):
            """Record every string handed to the parser, then parse it."""
            calls.append(s)
            return real(s, *a, **k)

        monkeypatch.setattr(T.json, "loads", counting)
        seen: list = []
        self._run(path, None, seen, needle=b'"keep"')
        assert seen == [1, 3]
        assert len(calls) == 2

    def test_stop_when_ends_the_walk_and_a_settled_cursor_reads_nothing(self, tmp_path, monkeypatch):
        """Once the state has nothing left to learn, later calls do not parse."""
        path = tmp_path / "t.jsonl"
        cursor = str(tmp_path / "cursor.json")
        self._append(path, [{"i": 1}, {"i": 2}, {"i": 3}])
        seen: list = []
        state, complete = self._run(path, cursor, seen, stop_when=lambda s: s["n"] >= 2)
        assert (state, complete) == ({"n": 2}, True)
        assert seen == [1, 2]
        self._append(path, [{"i": 4}])
        calls: list = []
        monkeypatch.setattr(T, "_parse_line", lambda raw: calls.append(raw))
        seen.clear()
        state, complete = self._run(path, cursor, seen, stop_when=lambda s: s["n"] >= 2)
        assert (state, complete) == ({"n": 2}, True)
        assert calls == [] and seen == []

    def test_rewritten_file_with_a_boundary_at_the_old_offset_restarts(self, tmp_path):
        """Same inode, longer file, old offset on a newline — only the byte
        sample before the offset tells the cursor the content changed."""
        path = tmp_path / "t.jsonl"
        cursor = str(tmp_path / "cursor.json")
        self._append(path, [{"i": 1, "pad": "aaaa"}, {"i": 2, "pad": "aaaa"}])
        self._run(path, cursor, [])
        path.write_text(
            "".join(json.dumps(ev) + "\n" for ev in
                    [{"i": 7, "pad": "bbbb"}, {"i": 8, "pad": "bbbb"}, {"i": 9, "pad": "bbbb"}]),
            encoding="utf-8",
        )
        seen: list = []
        assert self._run(path, cursor, seen) == ({"n": 3}, True)
        assert seen == [7, 8, 9]

    def test_unterminated_last_record_is_folded_once_and_stepped_over(self, tmp_path):
        """A complete record missing only its newline counts now, and is not
        counted again when the newline and the next record arrive."""
        path = tmp_path / "t.jsonl"
        cursor = str(tmp_path / "cursor.json")
        path.write_text(json.dumps({"i": 1}) + "\n" + json.dumps({"i": 2}), encoding="utf-8")
        seen: list = []
        assert self._run(path, cursor, seen) == ({"n": 2}, True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n" + json.dumps({"i": 3}) + "\n")
        seen.clear()
        assert self._run(path, cursor, seen) == ({"n": 3}, True)
        assert seen == [3]

    def test_unreadable_paths_raise(self, tmp_path):
        """Missing file and a directory both raise the read error."""
        with pytest.raises(T.TranscriptReadError):
            self._run(tmp_path / "absent.jsonl", None, [])
        with pytest.raises(T.TranscriptReadError):
            self._run(tmp_path, None, [])

    def test_cursor_path_is_session_keyed(self, tmp_path, monkeypatch):
        """`scan_cursor_path` keeps the session token last so the sweep spares it."""
        monkeypatch.setenv("PRAXIS_HOME", str(tmp_path))
        import _paths as P  # type: ignore[import-not-found]

        path = T.scan_cursor_path("codex", "sess-1", "agent-0a/b")
        assert path is not None
        name = Path(path).name
        assert name == "scan-codex-agent-0a_b-sess-1.json"
        assert P._belongs_to_session(name, "sess-1")
        assert T.scan_cursor_path("codex", "", "root") is None
        assert T.scan_cursor_path("codex", None) is None


class TestScanUserRejectionsResumable:
    """The rejection scan through a cursor (#1280): indeterminate only until
    the scan catches up, and a rejection resolves against a `tool_use` read
    in an earlier call."""

    def test_catches_up_over_calls_and_resolves_across_them(self, tmp_path):
        """Call 1 is cut by the budget (None); call 2 finishes and resolves."""
        pad = json.dumps({"type": "system", "pad": "x" * 200})
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "AskUserQuestion",
                           {"questions": [{"question": "Delete s3://b/raw/2024/ ?"}]}),
            *([pad] * 40),
            _rejection("toolu_1", "A1"),
        ])
        cursor = str(tmp_path / "cursor.json")
        assert T.scan_user_rejections(path, max_bytes=2000, cursor_path=cursor) is None
        recs = T.scan_user_rejections(path, max_bytes=20_000, cursor_path=cursor)
        assert recs is not None and len(recs) == 1
        assert recs[0]["tool_name"] == "AskUserQuestion"
        assert "s3://b/raw/2024/" in recs[0]["text"]
        # Nothing appended: the answer stands without re-reading the file.
        assert T.scan_user_rejections(path, max_bytes=100, cursor_path=cursor) == recs

    def test_a_string_message_record_does_not_abort_the_scan(self, tmp_path):
        """`message` guarded like every other reader: a record whose message
        is a string is skipped, and the rejection after it still resolves."""
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "AskUserQuestion",
                           {"questions": [{"question": "Delete s3://b/raw/2024/ ?"}]}),
            {"type": "user", "message": "not a dict", "toolDenialKind": "user-rejected"},
            _rejection("toolu_1", "A1"),
        ])
        recs = T.scan_user_rejections(path)
        assert recs is not None and len(recs) == 1
        assert recs[0]["tool_name"] == "AskUserQuestion"

    def test_without_a_cursor_the_bound_still_means_indeterminate(self, tmp_path):
        """No cursor path: the pre-cursor contract, call after call."""
        pad = json.dumps({"type": "system", "pad": "x" * 200})
        path = _write_jsonl(tmp_path, [_rejection("toolu_1", "A1"), *([pad] * 40)])
        assert T.scan_user_rejections(path, max_bytes=2000) is None
        assert T.scan_user_rejections(path, max_bytes=2000) is None

    def test_oversized_input_keeps_its_text_but_not_the_dict(self, tmp_path):
        """A tool_input past the text bound is carried as text only, so the
        cursor file never stores a Write body on every call."""
        big = {"content": "y" * (T._REJECTION_INPUT_MAX_CHARS + 10)}
        path = _write_jsonl(tmp_path, [
            _asst_tool_use("A1", "toolu_1", "Write", big),
            _rejection("toolu_1", "A1"),
        ])
        recs = T.scan_user_rejections(path)
        assert recs[0]["tool_name"] == "Write"
        assert recs[0]["tool_input"] == {}
        assert recs[0]["text"].startswith("yyyy")


# ---------------------------------------------------------------------------
# load_current_turn memo (issue #1281): one parse per dispatch group run
# ---------------------------------------------------------------------------

@pytest.fixture
def _memo_off():
    # Every test in this module runs standalone (memo disabled); make sure a
    # test that enables it cannot leak the state into its neighbours.
    yield
    T.disable_turn_memo()


def _count_reads(monkeypatch) -> list[int]:
    calls = [0]
    real = T.load_recent_events

    def counting(path, min_events=0, max_bytes=T.CURRENT_TURN_SCAN_MAX_BYTES):
        calls[0] += 1
        return real(path, min_events=min_events, max_bytes=max_bytes)

    monkeypatch.setattr(T, "load_recent_events", counting)
    return calls


def test_turn_memo_is_off_standalone(tmp_path, monkeypatch, _memo_off):
    # The standalone process (one hook, one call) must be untouched: every
    # call parses, exactly as before the memo existed.
    path = _write_jsonl(tmp_path, [_user("q"), _assistant("a")])
    calls = _count_reads(monkeypatch)
    assert T._TURN_MEMO is None
    T.load_current_turn(path)
    T.load_current_turn(path)
    assert calls[0] == 2


def test_turn_memo_shares_one_parse_across_members(tmp_path, monkeypatch, _memo_off):
    path = _write_jsonl(tmp_path, [_user("q"), _assistant("a")])
    calls = _count_reads(monkeypatch)
    T.enable_turn_memo()
    first = T.load_current_turn(path)
    second = T.load_current_turn(path)
    assert calls[0] == 1
    assert first == second == [_assistant("a")]  # the turn starts after the user boundary


def test_turn_memo_hands_each_member_its_own_list(tmp_path, _memo_off):
    # A member's in-place edit must not leak into a sibling's view of the turn.
    path = _write_jsonl(tmp_path, [_user("q"), _assistant("a")])
    T.enable_turn_memo()
    first = T.load_current_turn(path)
    first.pop()
    first.append({"type": "assistant", "message": {"role": "assistant", "content": "tampered"}})
    assert T.load_current_turn(path) == [_assistant("a")]


def test_turn_memo_rereads_a_changed_transcript(tmp_path, monkeypatch, _memo_off):
    # The key carries size and mtime: a transcript appended between two
    # members is parsed again rather than answered from the stale tail.
    path = _write_jsonl(tmp_path, [_user("q"), _assistant("a")])
    calls = _count_reads(monkeypatch)
    T.enable_turn_memo()
    T.load_current_turn(path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n" + json.dumps(_assistant("more")))
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    turn = T.load_current_turn(path)
    assert calls[0] == 2
    assert turn[-1] == _assistant("more")


def test_turn_memo_keys_on_max_bytes(tmp_path, monkeypatch, _memo_off):
    # Two members with different scan bounds do not share an answer: the
    # capped termination differs per bound (see load_recent_events).
    path = _write_jsonl(tmp_path, [_user("q"), _assistant("a")])
    calls = _count_reads(monkeypatch)
    T.enable_turn_memo()
    T.load_current_turn(path)
    T.load_current_turn(path, max_bytes=10)
    assert calls[0] == 2


def test_turn_memo_missing_file_is_not_cached(tmp_path, monkeypatch, _memo_off):
    # A stat failure yields no key: the fail-open [] is recomputed each time,
    # so a transcript that appears mid-group is seen by the next member.
    path = str(tmp_path / "absent.jsonl")
    calls = _count_reads(monkeypatch)
    T.enable_turn_memo()
    assert T.load_current_turn(path) == []
    assert T.load_current_turn(path) == []
    assert calls[0] == 2
    assert T._TURN_MEMO == {}


def test_disable_turn_memo_drops_the_entries(tmp_path, _memo_off):
    path = _write_jsonl(tmp_path, [_user("q"), _assistant("a")])
    T.enable_turn_memo()
    T.load_current_turn(path)
    assert T._TURN_MEMO
    T.disable_turn_memo()
    assert T._TURN_MEMO is None
