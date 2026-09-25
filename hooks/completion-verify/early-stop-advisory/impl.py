#!/usr/bin/env python3
"""Stop hook advisory: a turn that ends while the requested work is still owed.

Issue #1498.

The Opus 5.5 prompting guide (§ Unattended agentic runs) names four ways a
model ends its turn with requested work still open:

1. a summary that closes by announcing the next step, with no tool call;
2. an offer to carry on unless the user would prefer otherwise;
3. a list of decisions for the user when none blocks the rest of the work;
4. stopping to report because the turn was long or a milestone is done.

praxis already reacts to (2) routed through `AskUserQuestion`
(`block-manufactured-action-menu`, `block-ask-end-option`) and to (3) written as
prose (`prose-option-menu-advisory`). Types 1, 4, and 2-as-prose reach Stop
with no hook reacting. This hook covers exactly those three.

## Decision predicate

Read the turn's last assistant text with fenced code blocks and `>` quote lines
removed. Advise when one of these holds:

- **Offer to continue (type 2, prose).** A sentence in the closing lines makes
  continuing conditional on the user's preference (`원하시면`, `할까요`,
  `if you'd like`, `want me to`, `unless you'd prefer`) AND names continuing
  the requested work (`이어서`, `남은`, `나머지`, `continue`, `remaining`).
  An offer of something new after the work is done ("원하시면 PR 설명도 써
  드릴게요") carries no continuation cue and stays silent.
- **Announced next step (type 1).** A sentence in the closing lines is a
  first-person future (`…겠습니다`, `…ㄹ게요`, `I'll`, `I will`, `I'm going
  to`) carrying a next-step cue (`다음 단계로`, `이제`, `이어서`, `남은`,
  `next`, `now`, `remaining`). Deferrals out of the turn's scope (`다음에는`,
  `next time`, `follow-up PR`) and closings (`마치겠습니다`, `wrap up`) are not
  next steps.
- **Interim report (type 4).** The message frames itself as an interim or
  milestone report (`중간 보고`, `진행 상황`, `이 시점에서`, `progress update`,
  `so far`, `at this point`) AND some line lists an unfinished item
  (`미착수`, `미완료`, `진행 중`, `not started`, `pending`, `remaining`) that is
  not negated ("남은 작업은 없습니다", "nothing remaining").

It stays silent — a stop the user wants — when any of these holds:

- the text names a blocker (missing credentials or access, an approval or
  decision only the user can give, `blocked`, `waiting on`);
- the human message that opened the turn asked for a report, a plan, or a
  pause, or asked a question (an interrogative word plus a question ending):
  there the stop is the answer;
- `prose-option-menu-advisory` would fire on the same text (type 3): that hook
  owns decision menus, and loading its predicate keeps the two disjoint;
- `stop_hook_active` is set — the host's continuation cap.

The closing lines are the last three non-empty lines. The guide's type 1 is a
summary that *closes* on an announcement; the same verb mid-report usually
narrates what was done in order.

## Why advisory and not a block

Every marker above is also written by a turn that finished correctly, and a
blocker the model did not name reads the same as no blocker. A block that
forced a continuation would override the stops the guide says to keep. The
advisory follows the guide's continuation message instead: it says what still
looks open and asks to continue or state what blocks it.

Advisory only: `{"systemMessage": ...}`, exit 0. Bypass:
`PRAXIS_EARLY_STOP_BYPASS=1`.

Fail-open contract: malformed stdin, missing transcript, no last assistant
text, `stop_hook_active`, or any uncaught exception → exit 0.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_stop_advisory  # type: ignore[import-not-found]  # noqa: E402
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    load_stop_turn,
    read_last_user_message,
    stop_last_assistant_text,
)

_PREFIX = "[early-stop-advisory]"
_HOOK_NAME = "early-stop-advisory"
_ROLE = "completion-verify"
_BYPASS_ENV = "PRAXIS_EARLY_STOP_BYPASS"

_MENU_IMPL = (
    _Path(__file__).resolve().parent.parent / "prose-option-menu-advisory" / "impl.py"
)

# How far from the end a type-1/type-2 sentence may sit and still be the
# message's closing move.
_CLOSING_LINES = 3

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。？])\s+")

# --- type 2: conditional offer to continue --------------------------------

_OFFER_RE = re.compile(
    r"원하시면|원하신다면|필요하시면|괜찮으시면|괜찮다면|괜찮으시다면"
    r"|(?:할|갈|볼|드릴|진행할|이어갈|계속할)까요"
    r"|\bif you(?:'d| would)? (?:like|want|prefer)\b|\bunless you(?:'d| would)? "
    r"(?:prefer|rather|like)\b|\bwant me to\b|\bshall I\b|\bshould I\b"
    r"|\bwould you like me to\b|\blet me know if you(?:'d| would)? (?:like|want)\b"
    r"|\bhappy to (?:continue|keep|carry|proceed|finish)",
    re.IGNORECASE,
)

_CONTINUE_RE = re.compile(
    r"이어서|이어가|계속|남은|나머지|마저"
    r"|\bcontinue\b|\bproceed\b|\bcarry on\b|\bkeep going\b|\bremaining\b"
    r"|\brest of\b|\bthe others\b|\bfinish\b|\bmove on\b",
    re.IGNORECASE,
)

# --- type 1: first-person future announcing the next step -----------------

_FUTURE_KO_RE = re.compile(r"겠습니다|겠어요")
# `…ㄹ게요` (할게요, 드릴게요, 옮길게요): the syllable before 게요 ends in ㄹ.
_GEYO_RE = re.compile(r"([가-힣])게요")
_JONGSEONG_RIEUL = 8
_FUTURE_EN_RE = re.compile(
    r"\bI(?:'ll| will| am going to|'m going to|'m about to| am about to)\b"
    r"|\b(?:now|next),? let me\b|\blet me now\b",
    re.IGNORECASE,
)

_NEXT_CUE_RE = re.compile(
    r"다음 ?단계|다음으로|다음 작업|이제|이어서|이어가|계속|남은|나머지|곧바로|그 ?다음"
    r"|\bnext\b|\bnow\b|\bthen\b|\bcontinue\b|\bproceed\b|\bremaining\b"
    r"|\brest of\b|\bmove on\b",
    re.IGNORECASE,
)

# "Next" that points outside this turn's scope, and sentences that close the
# message rather than announce work.
_NOT_NEXT_STEP_RE = re.compile(
    r"다음에는|다음번|다음 번|다음 기회|다음 세션|다음 PR|다음 이슈|후속"
    r"|않겠|안 하겠|마치겠|마무리하겠|종료하겠|줄이겠|멈추겠"
    r"|\bnext time\b|\bnext session\b|\bfollow-?up\b|\bnext PR\b"
    r"|\bwon't\b|\bwill not\b|\bwrap (?:up|it up)\b|\bstop here\b",
    re.IGNORECASE,
)

# --- type 4: interim / milestone report with open items -------------------

_INTERIM_RE = re.compile(
    r"중간 ?(?:보고|점검|공유|정리)|진행 ?(?:상황|현황)|현재까지|지금까지|여기까지"
    r"|이 시점에서|일단 (?:여기|공유|보고)|작업이 길어|길어져|마일스톤|1차 (?:보고|완료)"
    r"|\bprogress update\b|\bstatus update\b|\binterim\b|\bcheckpoint\b"
    r"|\bso far\b|\bpausing here\b|\bat this point\b|\bmilestone\b"
    r"|\blong (?:run|turn|session)\b",
    re.IGNORECASE,
)

_OPEN_ITEM_RE = re.compile(
    r"미착수|미완료|미진행|미반영|착수 전|진행 전|진행 중|남은 (?:작업|항목|엔드포인트)"
    r"|남아 있|TODO|대기 중|\[ \]"
    r"|\bnot (?:yet )?started\b|\bnot yet\b|\bpending\b|\bremaining\b"
    r"|\bstill to do\b|\bin progress\b|\bleft to do\b",
    re.IGNORECASE,
)

_OPEN_ITEM_NEGATED_RE = re.compile(
    r"없습니다|없음|없어요|없고|\bno (?:\w+ ){0,2}(?:remaining|pending|left)\b"
    r"|\bnothing (?:remaining|pending|left)\b|\bnone (?:remaining|pending|left)\b"
    r"|\b0 (?:remaining|pending)\b",
    re.IGNORECASE,
)

# --- silence: a named blocker ---------------------------------------------

_BLOCKER_RE = re.compile(
    r"자격 ?증명|크리덴셜|권한이 없|접근(?: 권한)?이 없|토큰이 없"
    r"|승인(?:이|을)? (?:필요|기다|대기|받아야|받은 ?후|받은 ?뒤|해 주셔야)|승인 대기"
    r"|(?:진행|계속|작업)할 수 없|진행이 불가|불가능합니다|막혀|블로커|차단되"
    r"|결정(?:이|을)? (?:필요|기다)|주셔야|해 주셔야|대기하고 있"
    r"|\bblocked\b|\bblocker\b|\bwaiting (?:on|for)\b|\bcredentials?\b"
    r"|\bneeds? your (?:approval|input|decision|confirmation|credentials)\b"
    r"|\brequires? your\b|\bcan(?:no|')t (?:proceed|continue)\b"
    r"|\bunable to (?:proceed|continue)\b|\bno access\b|\bpermission denied\b"
    r"|\bonce you (?:provide|approve|confirm|grant)\b",
    re.IGNORECASE,
)

# --- silence: the user asked for the stop ---------------------------------

_USER_WANTS_STOP_RE = re.compile(
    r"진행 ?(?:상황|현황)|현황|상태(?:만|를)? 알려|중간 ?보고|보고해|보고만"
    r"|계획(?:만|을 세워|을 짜|부터)|플랜|멈춰|멈추고|여기까지만|하나씩|단계별로 확인"
    r"|확인받|확인 받"
    r"|\bstatus\b|\bprogress\b|\breport back\b|\bcheck in with me\b"
    r"|\b(?:make|write|draft|give me|propose) (?:a |the )?plan\b|\bplan (?:only|first)\b"
    r"|\bpause\b|\bstop (?:after|here|there|when)\b|\bone (?:at a time|by one)\b"
    r"|\bstep by step\b",
    re.IGNORECASE,
)

_INTERROGATIVE_RE = re.compile(
    r"왜|뭐|무엇|무슨|어떻게|어떤|어느|언제|어디|누가|몇"
    r"|\b(?:why|what|how|which|when|where|who)\b",
    re.IGNORECASE,
)
_QUESTION_END_RE = re.compile(r"(?:[?？]|까|나요|가요|니|냐|지)\s*$")


def _content_lines(text: str) -> list[str]:
    """Non-empty lines outside fenced code blocks and `>` quotes."""
    lines: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = raw.strip()
        if not line or line.startswith(">"):
            continue
        lines.append(line)
    return lines


def _closing_sentences(lines: list[str]) -> list[str]:
    sentences: list[str] = []
    for line in lines[-_CLOSING_LINES:]:
        sentences.extend(s for s in _SENTENCE_SPLIT_RE.split(line) if s.strip())
    return sentences


def _is_offer_to_continue(sentence: str) -> bool:
    return bool(_OFFER_RE.search(sentence) and _CONTINUE_RE.search(sentence))


def _has_ko_first_person_future(sentence: str) -> bool:
    if _FUTURE_KO_RE.search(sentence):
        return True
    return any(
        (ord(m.group(1)) - 0xAC00) % 28 == _JONGSEONG_RIEUL
        for m in _GEYO_RE.finditer(sentence)
    )


def _is_announced_next_step(sentence: str) -> bool:
    if not (_has_ko_first_person_future(sentence) or _FUTURE_EN_RE.search(sentence)):
        return False
    if _NOT_NEXT_STEP_RE.search(sentence):
        return False
    return bool(_NEXT_CUE_RE.search(sentence))


def _open_item_line(lines: list[str]) -> str | None:
    for line in lines:
        if _OPEN_ITEM_RE.search(line) and not _OPEN_ITEM_NEGATED_RE.search(line):
            return line
    return None


def early_stop_signal(text: str) -> tuple[str, str] | None:
    """(kind, evidence line) when the text ends with requested work open.

    Blocker, menu and user-request exemptions are the caller's; this judges
    the text's own shape only.
    """
    lines = _content_lines(text)
    if not lines:
        return None
    body = "\n".join(lines)
    if _BLOCKER_RE.search(body):
        return None

    closing = _closing_sentences(lines)
    for sentence in closing:
        if _is_offer_to_continue(sentence):
            return "an offer to continue that waits on your preference", sentence
    for sentence in closing:
        if _is_announced_next_step(sentence):
            return "a next step announced but not taken", sentence

    if _INTERIM_RE.search(body):
        open_line = _open_item_line(lines)
        if open_line is not None:
            return "an interim report that lists unfinished items", open_line
    return None


def user_wants_the_stop(user_text: str | None) -> bool:
    """True when the opening human message asked for a report, plan, pause,
    or asked a question — the stop is then what was requested."""
    if not user_text:
        return False
    if _USER_WANTS_STOP_RE.search(user_text):
        return True
    last = next(
        (ln.strip() for ln in reversed(user_text.splitlines()) if ln.strip()), ""
    )
    return bool(_QUESTION_END_RE.search(last) and _INTERROGATIVE_RE.search(last))


_menu_module = None


def is_prose_menu(text: str, turn: list[dict]) -> bool:
    """`prose-option-menu-advisory`'s own predicate, loaded by file location.

    Type 3 (a decision list handed back) is that hook's lane. Using its
    predicate rather than a copy keeps the two from double-firing even when
    its vocabulary changes. A failed load answers True — this hook goes silent
    rather than risk firing on the sibling's input; the fixture suite then
    fails every "must fire" case, so the breakage cannot pass unnoticed.
    """
    global _menu_module
    try:
        if _menu_module is None:
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "_praxis_early_stop_prose_menu", _MENU_IMPL
            )
            if spec is None or spec.loader is None:
                return True
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _menu_module = module
        return bool(_menu_module.is_prose_menu(text, turn))
    except Exception:
        return True


def _clip(line: str, limit: int = 160) -> str:
    line = " ".join(line.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def build_message(kind: str, evidence: str) -> str:
    return (
        f"{_PREFIX} This turn ended while requested work still looks open — "
        f"{kind}:\n"
        f"  \"{_clip(evidence)}\"\n"
        "  If nothing blocks the open items, continue with them instead of "
        "stopping. If something does (missing credentials or access, an "
        "approval or a decision only the user can make), state that blocker "
        f"in one line. Bypass: {_BYPASS_ENV}=1"
    )


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip():
        return 0

    payload = read_payload()
    if not isinstance(payload, dict):
        return 0
    if payload.get("stop_hook_active"):
        return 0  # the host's cap on automatic continuations

    turn = load_stop_turn(payload)
    if not turn:
        return 0
    last_text = stop_last_assistant_text(payload, turn)
    if not last_text:
        return 0

    signal = early_stop_signal(last_text)
    if signal is None:
        return 0
    if is_prose_menu(last_text, turn):
        return 0

    transcript_path = payload.get("transcript_path")
    if isinstance(transcript_path, str) and user_wants_the_stop(
        read_last_user_message(transcript_path, human_only=True)
    ):
        return 0

    emit_stop_advisory(build_message(*signal))

    session_id = payload.get("session_id")
    if _fire_ledger.record_session_fire(
        _HOOK_NAME, _ROLE, _fire_ledger.DECISION_ADVISE,
        session_id if isinstance(session_id, str) else "", "Stop",
    ):
        _fire_ledger.suppress_coarse_duplicate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
