#!/usr/bin/env python3
"""PreToolUse advisory: external write after an unquoted negation-marker answer.

Issue #1441. A free-text `AskUserQuestion` answer that opens with a negation
marker is a correction, and a correction has more than one reading. In the
recorded incident the answer was `아니지 bump 만 언급해요 다른거도 있는데`
("no, I only mentioned the bump, there are others too"). Two readings existed —
drop the non-bump lines, or add the other PRs — and the agent took the second,
re-asked with a question that presupposed it and quoted nothing, got a `post`
back, and sent two messages to a shared channel where one had been asked for.

No hook fired anywhere on that path. The sibling external-write hooks scan the
OUTGOING body (`external-write-falsify-check` for hypothesis markers,
`source-citation-probe-gate` for citations); none reads the INCOMING answer, so
a misread correction reaches the channel with nothing in between.

## Decision predicate

Ask when all of these hold:

1. The pending call is an external write — the shared surface predicate in
   `_external_write_body` (gh comment/create/edit/review, a write-method
   `gh api` on a comments/reviews endpoint, Slack send/post/update, Notion
   page writes).
2. Within the scanned tail, an `AskUserQuestion` returned a FREE-TEXT answer
   whose first token is a negation marker.
3. No assistant text and no later `AskUserQuestion` question quotes that
   answer's opening verbatim.

`ask`, never deny: whether the reading was right is the user's call, and this
hook cannot judge it. What it checks is narrower and mechanical — whether the
reading was ever shown to the person before the write went out.

## Why free text only

The runtime records the two answer kinds under different prefixes: a chosen
option comes back as `Your questions have been answered: …`, free text as
`The user answered: … Read the answers carefully — they may request
clarification, changes, or that you not proceed`. Only the second is a
correction the agent had to interpret; an option label was authored by the
agent itself, so there is no reading to surface. Measured over 851 local
transcripts: 2236 option answers, of which 6 open with a negation marker and
all 6 are labels the agent wrote (`아니오, 독립적인 버그픽스 (권장)`). Keying on
the option kind would fire on the agent's own words.

## Silent by design

  - An answer phrased as a question (`아니 로컬에서 랙이 걸리는거 너때문인지?`)
    — that asks the agent something rather than correcting a proposal. It is
    the one false positive the corpus replay produced, and the question-mark
    exclusion is what removes it.
  - `아니면` ("or else") — a disjunction, not a refusal. The marker is
    negative-lookahead scoped so the two do not collapse.
  - An option-label answer, for the reason above.
  - An answer already quoted in assistant prose — the reading was surfaced;
    whether the person then confirmed it is not this hook's question.
  - An answer older than the scanned tail, or a transcript past the byte
    bound. This gate FAILS OPEN there, unlike `rejected-mutation-reconsent-gate`
    which asks: a standing refusal keeps its force for the whole session, while
    a correction is consumed by the work that follows it, so an old unquoted
    answer is mostly noise rather than a live hazard.

Bypass: `PRAXIS_NEGATION_ANSWER_BYPASS=1`.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _external_write_body import (  # type: ignore[import-not-found]  # noqa: E402
    is_gh_external_write,
    is_mcp_external_write,
)
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import load_recent_events  # type: ignore[import-not-found]  # noqa: E402

_HOOK_NAME = "negation-answer-quote-advisory"
BYPASS_ENV = "PRAXIS_NEGATION_ANSWER_BYPASS"

# The runtime's own prefix for a free-text answer. An option pick carries
# "Your questions have been answered:" instead, and is not in scope.
FREE_TEXT_PREFIX = "The user answered:"

# `"<question>"="<answer>"`, repeated per question. Escaped quotes inside
# either side are consumed by the `\\.` alternative, so an answer containing a
# quoted string does not end the match early.
_PAIR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"="((?:[^"\\]|\\.)*)"')

# Opening negation markers. `아니(?!면)` keeps the disjunction `아니면`
# ("or else") out; `no(?![a-z])` keeps `note` / `nobody` out — a plain `\bno\b`
# would not, because Python's `\b` is Unicode-aware and the mixed Korean text
# these answers are written in offers no ASCII boundary to lean on.
_NEGATION_RE = re.compile(
    r"^\s*(?:아니(?!면)|아뇨|아닙니다|아닌데|말고|틀렸|nope|no(?![a-z])|not\s+that)",
    re.IGNORECASE,
)

# How much of the answer has to reappear for it to count as quoted. Short
# enough that a paraphrase built around the user's own words still clears it,
# long enough that a single shared word does not.
QUOTE_PREFIX_CHARS = 20

# An answer shorter than this cannot carry a distinctive quote, so requiring
# one would be requiring something unverifiable.
MIN_ANSWER_CHARS = 8

# Tail budget. The turn boundary alone is not enough: the answer and the write
# can sit in one long turn with the re-ask in between, which is the incident's
# own shape.
TAIL_MIN_EVENTS = 150


def unescape(value: str) -> str:
    """Undo the backslash escaping the runtime applies inside the pair text."""
    return value.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def free_text_answers(result: str, labels: frozenset[str] = frozenset()) -> list[str]:
    """Typed answers from one AskUserQuestion tool_result, or [] if not free text.

    One free-text answer puts the whole result under the free-text prefix, so a
    sibling question answered by picking an option rides along; its label is
    still the agent's own words and is dropped here.
    """
    if not result.startswith(FREE_TEXT_PREFIX):
        return []
    answers = (unescape(answer) for _question, answer in _PAIR_RE.findall(result))
    return [answer for answer in answers if answer not in labels]


def option_labels(tool_input: object) -> frozenset[str]:
    """Every option label the agent offered in one AskUserQuestion call."""
    if not isinstance(tool_input, dict):
        return frozenset()
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return frozenset()
    return frozenset(
        option["label"]
        for question in questions
        if isinstance(question, dict) and isinstance(question.get("options"), list)
        for option in question["options"]
        if isinstance(option, dict) and isinstance(option.get("label"), str)
    )


def is_negation_answer(answer: str) -> bool:
    """True for a correction opening with a negation marker.

    A question is not a correction: the user is asking the agent something,
    and there is no reading of it to surface before a write.
    """
    text = answer.strip()
    if len(text) < MIN_ANSWER_CHARS:
        return False
    if text.endswith(("?", "？")):
        return False
    return bool(_NEGATION_RE.match(text))


def result_text(block: dict) -> str:
    """Flatten a tool_result's content to text, whichever shape it arrived in."""
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def quotes(text: str, answer: str) -> bool:
    return answer.strip()[:QUOTE_PREFIX_CHARS] in text


def _strings(value: object) -> list[str]:
    """Every string leaf of a tool_input, in no particular order.

    A follow-up question is compared against the answer as the runtime stored
    it — decoded. Serialising the input to JSON first compares it against an
    escaped form instead, so an answer holding a quote or a newline never
    matches its own verbatim quotation.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def is_human_turn_start(event: dict) -> bool:
    """True for a user event that is a person typing, not a tool result.

    A correction is consumed by the work the user asks for next, so it does not
    carry into the turn after it. Without this boundary the scanned tail reaches
    back past the answer's own turn and arms a write that never touched it.
    """
    message = event.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    return not any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def pending_negation_answer(events: list[dict]) -> str | None:
    """The newest negation answer that nothing has quoted back since.

    One pass, oldest to newest: an answer arms the state and a later quote —
    in assistant prose or in a following question — disarms it. A delegated
    agent's events ride inline in the main transcript under `isSidechain`, and
    they are another conversation: its answers are not corrections the main
    agent received, and its prose is not the main agent showing a reading.
    Counting them arms a write nobody corrected and disarms one nobody quoted.
    """
    ask_labels: dict[str, frozenset[str]] = {}
    armed: str | None = None
    for event in events:
        if event.get("isSidechain"):
            continue
        if is_human_turn_start(event):
            armed = None
            ask_labels.clear()
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                if block.get("name") == "AskUserQuestion":
                    ask_labels[str(block.get("id"))] = option_labels(block.get("input"))
                    if armed and any(
                        quotes(text, armed) for text in _strings(block.get("input"))
                    ):
                        armed = None
            elif kind == "text":
                body = block.get("text")
                if armed and isinstance(body, str) and quotes(body, armed):
                    armed = None
            elif kind == "tool_result" and str(block.get("tool_use_id")) in ask_labels:
                labels = ask_labels.pop(str(block.get("tool_use_id")))
                for answer in free_text_answers(result_text(block), labels):
                    if is_negation_answer(answer):
                        armed = answer
    return armed


def is_external_write(payload: dict) -> bool:
    """True iff the pending tool call writes to a shared external surface."""
    tool_name = str(payload.get("tool_name") or "")
    if is_mcp_external_write(tool_name):
        return True
    if tool_name != "Bash":
        return False
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return False
    tokens = safe_tokenize(command.replace("\\\n", " "))
    if not tokens:
        return False
    return any(is_gh_external_write(argv) for argv in iter_command_starts(tokens))


def build_reason(answer: str) -> str:
    """The ask text: the answer verbatim, and what to do before writing."""
    quoted = answer.strip().replace("\n", " ")
    if len(quoted) > 200:
        quoted = quoted[:200] + "…"
    return (
        "⚠️ 외부 쓰기 전 — 직전 정정이 인용되지 않았습니다\n\n"
        f'마지막 자유입력 답변: "{quoted}"\n\n'
        "Why: 부정 표지로 시작하는 답변은 정정이고, 정정에는 대개 두 가지 읽기가 "
        "있습니다. 이 답변 이후 어떤 assistant 텍스트나 재질의도 답변을 그대로 "
        "인용하지 않았습니다. 지금 나가는 외부 쓰기는 사용자의 말이 아니라 그 말에 "
        "대한 나의 해석 위에 서 있습니다.\n"
        "Correct path: 답변을 그대로 인용하고 어떤 읽기를 택했는지 한 줄로 적은 뒤 "
        "쓰기를 다시 실행하세요. 읽기가 갈리면 쓰기 전에 물으세요.\n"
        f"Bypass: {BYPASS_ENV}=1\n"
        "Reference: issue #1441; hooks/advisory-nudge/"
        f"{_HOOK_NAME}/spec.md"
    )


@fail_open
def main() -> int:
    """Hook entry point: external write + unquoted negation answer → ask."""
    if os.environ.get(BYPASS_ENV) == "1":
        return 0

    payload = read_payload()
    if payload is None:
        return 0

    # Cheapest discriminator first: a non-write call never reads the transcript.
    if not is_external_write(payload):
        return 0

    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return 0

    events = load_recent_events(transcript_path, min_events=TAIL_MIN_EVENTS)
    if not events:
        return 0  # unreadable or past the byte bound — fail open

    answer = pending_negation_answer(events)
    if answer is None:
        return 0

    emit_decision("ask", build_reason(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
