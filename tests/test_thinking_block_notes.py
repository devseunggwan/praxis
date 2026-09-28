"""Window readers see a mid-turn note in either recorded shape (issue #1502).

A note the model writes between tool calls lands in the transcript as a `text`
block or as a `thinking` block that carries the note after an empty one in the
same message (RUNTIME_CONSTRAINTS.md entry 11). Each reader that scans
assistant prose across a window must return the same result for both shapes,
and a blank `thinking` block (recorded reasoning) must contribute nothing.

Run: python3 -m pytest tests/test_thinking_block_notes.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks" / "_lib"))

import _transcript as T  # type: ignore[import-not-found]  # noqa: E402


def _load(name: str, rel: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "hooks" / rel / "impl.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cited = _load("cited_rule_gate", "advisory-nudge/cited-rule-gate")
momentum = _load("momentum_rule_retrieval_gate", "advisory-nudge/momentum-rule-retrieval-gate")
negation = _load("negation_answer_quote_advisory", "advisory-nudge/negation-answer-quote-advisory")
pr_report = _load("pr_report_destination_gate", "completion-verify/pr-report-destination-gate")
runtime_claim = _load("runtime_state_claim_gate", "completion-verify/runtime-state-claim-gate")

SHAPES = ("text", "thinking")


def _assistant(blocks: list[dict], msg_id: str = "msg_1") -> dict:
    return {"type": "assistant",
            "message": {"id": msg_id, "role": "assistant", "content": blocks}}


def _note(shape: str, note: str, msg_id: str = "msg_1") -> list[dict]:
    """The note as it is recorded: one block per transcript line."""
    if shape == "text":
        return [_assistant([{"type": "text", "text": note}], msg_id)]
    return [
        _assistant([{"type": "thinking", "thinking": "", "signature": "s1"}], msg_id),
        _assistant([{"type": "thinking", "thinking": note, "signature": "s2"}], msg_id),
    ]


def _blank_thinking(msg_id: str = "msg_1") -> list[dict]:
    return [_assistant([{"type": "thinking", "thinking": " \n", "signature": "s"}], msg_id)]


class TestBlockProse:
    def test_text_block(self):
        assert T.block_prose({"type": "text", "text": "note"}) == "note"

    def test_thinking_block_with_text(self):
        assert T.block_prose({"type": "thinking", "thinking": "note"}) == "note"

    @pytest.mark.parametrize("blank", ["", " ", "\n\t"])
    def test_blank_thinking_block_carries_nothing(self, blank):
        assert T.block_prose({"type": "thinking", "thinking": blank}) is None

    @pytest.mark.parametrize("block", [
        {"type": "tool_use", "id": "t", "name": "Bash", "input": {}},
        {"type": "redacted_thinking", "data": "x"},
        {"type": "text", "text": 3},
        {"type": "thinking"},
        "text",
        None,
    ])
    def test_other_blocks_carry_nothing(self, block):
        assert T.block_prose(block) is None


class TestCitedRuleWindow:
    @pytest.mark.parametrize("shape", SHAPES)
    def test_note_before_the_call_is_in_the_window(self, shape):
        call = _assistant([{"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {}}])
        events = _note(shape, "Per `Scope Discipline`, pushing now.") + [call]
        assert cited.window_text(events, "tu_1") == "Per `Scope Discipline`, pushing now."

    def test_blank_thinking_adds_nothing(self):
        call = _assistant([{"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {}}])
        assert cited.window_text(_blank_thinking() + [call], "tu_1") == ""


class TestMomentumAssistantText:
    @pytest.mark.parametrize("shape", SHAPES)
    def test_note_is_counted(self, shape):
        events = _note(shape, "What was verified: 212 passed.")
        assert momentum._assistant_text(events, 0, len(events)) == "What was verified: 212 passed."

    def test_blank_thinking_adds_nothing(self):
        events = _blank_thinking()
        assert momentum._assistant_text(events, 0, len(events)) == ""


class TestNegationQuote:
    ANSWER = "아니요, 그 방식 말고 파일을 먼저 만들어 주세요"

    def _asked(self) -> list[dict]:
        ask = _assistant([{"type": "tool_use", "id": "q_1", "name": "AskUserQuestion",
                           "input": {"questions": []}}], "msg_0")
        answer = {"type": "user", "message": {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "q_1",
            "content": f'The user answered: "How?"="{self.ANSWER}"'}]}}
        return [ask, answer]

    def test_unquoted_answer_stays_armed(self):
        assert negation.pending_negation_answer(self._asked() + _blank_thinking()) == self.ANSWER

    @pytest.mark.parametrize("shape", SHAPES)
    def test_quoting_note_disarms(self, shape):
        events = self._asked() + _note(shape, f"You said: {self.ANSWER}. Writing the file first.")
        assert negation.pending_negation_answer(events) is None


class TestPrReportContext:
    @pytest.mark.parametrize("shape", SHAPES)
    def test_pr_named_in_note_is_context(self, shape):
        state = pr_report._new_state()
        for ev in _note(shape, "Opened https://github.com/o/r/pull/1234 for review."):
            pr_report._reduce_event(state, ev)
        assert state["context_prs"] == {"1234"}

    def test_blank_thinking_adds_nothing(self):
        state = pr_report._new_state()
        for ev in _blank_thinking():
            pr_report._reduce_event(state, ev)
        assert state["context_prs"] == set()


class TestRuntimeClaimMentions:
    NOTE = "All 12 tests passed."

    def test_note_states_a_claim(self):
        assert runtime_claim.extract_verdict_claims(self.NOTE)

    @pytest.mark.parametrize("shape", SHAPES)
    def test_claim_in_note_is_a_mention(self, shape):
        found: dict[str, str | None] = {}
        for ev in _note(shape, self.NOTE):
            found.update(runtime_claim._event_verdict_mentions(ev))
        assert set(found) == {c["key"] for c in runtime_claim.extract_verdict_claims(self.NOTE)}

    def test_blank_thinking_adds_nothing(self):
        assert runtime_claim._event_verdict_mentions(_blank_thinking()[0]) == {}
