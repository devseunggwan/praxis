#!/usr/bin/env python3
"""PreToolUse guard: a turn opened by a user question does not mutate.

Issue #1486. The user challenged the agent ("you are supposed to follow these
rules, so what is this"), the agent wrote one sentence of acknowledgement, and
in the same turn ran eight tool calls, four of them state-changing, the ninth
opening a pull request. The user wanted an answer and got a chain of actions
before they could reply.

Every other gate keys on what a call is. This one keys on the conversational
state the call is made in: when the human message that opened the current
turn reads as a question or a challenge, every mutating call in that turn asks
first. Read-only calls stay silent, so the agent can still probe before it
answers. The next human message opens a new turn and lifts the gate.

"Text since the user message" is deliberately not consulted: the one-line
acknowledgement in the incident would have satisfied it.

The classifier reads the last sentence of that message, because that is where
a message puts what it asks for: "why did this fail? fix it" is an instruction,
"fix it. why did this fail?" is a question. A question mark alone is not
enough (the incident message had none), so interrogative openers and endings
and a short list of challenge phrases count too, and a request form
("can you ...?", "...해줘") counts as an instruction even with a question mark.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402
from _mutating_call import is_mutating_call  # type: ignore[import-not-found]  # noqa: E402
from _transcript import read_last_user_message  # type: ignore[import-not-found]  # noqa: E402

EXTRA_MARKERS_ENV = "PRAXIS_QUESTION_TURN_MARKERS"

# A request is an instruction even when it is phrased with a question mark.
_EN_REQUEST_OPENERS = (
    "please", "pls", "can you", "could you", "would you", "will you",
    "can we", "could we", "let's", "lets",
)
_KO_REQUEST_ENDINGS = (
    "줘", "줘요", "주세요", "줄래", "줄래요", "주실래요", "주시겠어요", "줄 수 있어",
    "해봐", "하자", "해라", "부탁", "부탁해", "부탁드려요", "부탁드립니다",
)

# Openers that make an English sentence a question without a question mark.
# `do`, `can`, `could`, `will` and `should` are absent: each opens a request
# or an imperative as often as a question ("do the migration"). So is `when`,
# which opens a conditional instruction ("when done, print OK").
_EN_QUESTION_OPENERS = frozenset((
    "why", "what", "how", "who", "whom", "whose", "where", "which",
    "is", "are", "was", "were", "did", "does", "has", "have",
    "isn't", "aren't", "wasn't", "weren't", "didn't", "doesn't",
    "hasn't", "haven't",
))

# Korean sentence endings that only a question takes. Endings shared with a
# statement ("했어", "있어") are absent: "I committed it" and "did you commit
# it" differ only by intonation, which text does not carry. A bare "니까" is
# absent too: "하라니까" repeats an instruction, so only the formal endings count.
_KO_QUESTION_ENDINGS = (
    "뭐야", "뭐지", "뭔데", "뭐임", "뭐냐", "왜야", "왜지", "왜죠", "왜요",
    "인가요", "인가", "인지", "는지", "나요", "까요", "습니까", "입니까", "을까",
    "냐", "거야", "건가", "건가요", "거지", "거죠", "어때", "어때요", "맞나",
    "맞아요", "맞지", "아닌가", "아닌가요", "아냐", "않나", "않았나", "없나",
    "있나", "었나", "았나", "했나", "됐나", "는건데", "는 건데",
)

# Challenge phrases: a reproach about what the agent did, question mark or not.
_EN_CHALLENGE = (
    "what is this", "what's this", "what the", "why did you", "why didn't you",
    "why are you", "why would you", "supposed to", "i told you", "didn't i",
    "i already said", "answer first", "answer me",
)
_KO_CHALLENGE = (
    "이게 뭐", "뭐하는", "뭐 하는", "했잖아", "라고 했", "하라고 했", "말했잖",
    "먼저 답", "답부터", "대답부터", "어쩌자는", "어쩌라는", "뭐하자는",
)

# Blocks the host or the user wrapped in tags (system reminders, command
# echoes, pasted text) and fenced code carry no ask of the user's own.
_TAGGED_BLOCK_RE = re.compile(r"<([A-Za-z][\w-]*)\b[^>]*>.*?</\1>", re.DOTALL)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_SENTENCE_RE = re.compile(r"[^.!?。！？\n]*[.!?。！？]*")
_TRAILING_NOISE = " \t~.!…ㅋㅎㅠㅜ^;:)"


def _last_sentence(text: str) -> str:
    text = _FENCE_RE.sub(" ", _TAGGED_BLOCK_RE.sub(" ", text))
    sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]
    return sentences[-1] if sentences else ""


def _extra_markers() -> tuple[str, ...]:
    raw = os.environ.get(EXTRA_MARKERS_ENV, "")
    return tuple(m.strip().lower() for m in raw.split(",") if m.strip())


def _is_request(sentence: str, core: str) -> bool:
    if re.match(r"(?:%s)\b" % "|".join(re.escape(o) for o in _EN_REQUEST_OPENERS), sentence):
        return True
    return core.endswith(_KO_REQUEST_ENDINGS)


def question_marker(text: str) -> str | None:
    """The marker that makes `text` a question or a challenge, or None."""
    sentence = _last_sentence(text).lower()
    if not sentence:
        return None
    core = sentence.rstrip(_TRAILING_NOISE + "?？").rstrip()
    for marker in _EN_CHALLENGE + _KO_CHALLENGE + _extra_markers():
        if marker in sentence:
            return marker
    if _is_request(sentence, core):
        return None
    if sentence.rstrip(_TRAILING_NOISE).endswith(("?", "？")):
        return "?"
    first = re.split(r"[\s,]+", core, maxsplit=1)[0]
    if first in _EN_QUESTION_OPENERS:
        return first
    for ending in _KO_QUESTION_ENDINGS:
        if core.endswith(ending):
            return ending
    return None


def _message(tool_name: str, marker: str) -> str:
    return format_block(
        rule_name="question turn mutation",
        why="this turn was opened by a question; answer it and end the turn "
            f"before acting -- {tool_name} changes state (matched: {marker!r})",
        correct_path="answer the question in text and end the turn; act on the "
                     "next message if the user asks for it. Read-only probes "
                     "stay allowed.",
        # The ask is the user's decision to let the action ride on a question;
        # an agent-attachable bypass would let the agent make it instead.
        bypass_env=None,
        reference="hooks/preflight-gate/question-turn-mutation-gate/spec.md",
    )


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0
    # A subagent's turn is opened by its delegator, not by the human.
    if payload.get("agent_id"):
        return 0
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict) or not is_mutating_call(tool_name, tool_input):
        return 0
    transcript = str(payload.get("transcript_path") or "")
    text = read_last_user_message(transcript, human_only=True)
    if not text:
        return 0
    marker = question_marker(text)
    if marker is None:
        return 0
    emit_decision("ask", _message(tool_name, marker))
    return 0


if __name__ == "__main__":
    sys.exit(main())
