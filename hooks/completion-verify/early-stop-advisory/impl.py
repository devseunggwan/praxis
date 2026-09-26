#!/usr/bin/env python3
"""Stop hook advisory: a turn that ends while the requested work is still owed.

Issue #1498.

The Opus 5.5 prompting guide (§ Unattended agentic runs) gives an example
system-prompt addition, "written for agents that run fully unattended", that
names four ways a model ends its turn while work the user asked for is still
owed:

1. a summary that closes by announcing the next step, with no tool call;
2. an offer to carry on unless the user would prefer otherwise;
3. a list of decisions for the user when none blocks the rest of the work;
4. stopping to report because the turn was long or a milestone is done.

praxis already reacts to (2) routed through `AskUserQuestion`
(`block-manufactured-action-menu`, `block-ask-end-option`) and to (3) written as
prose (`prose-option-menu-advisory`). Types 1, 4, and 2-as-prose reach Stop
with no hook reacting. This hook covers exactly those three.

Scope: the guide says to leave its addition "out of human-in-the-loop
applications, where someone is there to answer". This hook does not tell the
two apart; it fires in every session.

## Decision predicate

Read the turn's last assistant text with fenced code blocks, `>` quote lines
and inline quoted spans (`"…"`, `“…”`, `‘…’`, `'…'`, `「…」`, `『…』`)
removed — a quoted plan or user phrase is not the model's own announcement.
Advise when one of these holds:

- **Offer to continue (type 2, prose).** A closing sentence makes continuing
  conditional on the user's preference (`원하시면`, `할까요`, `…면 될까요`,
  `if you'd like`, `want me to`, `if that works for you`) AND names
  continuing the requested work (`이어서`, `남은`, `나머지`, `continue`,
  `remaining`, `finish the rest`). An offer of something new ("원하시면 PR
  설명도 써 드릴게요", "I can also finish the changelog entry") carries no
  continuation cue and stays silent.
- **Announced next step (type 1).** A closing sentence binds a next-step cue
  to a first-person future: Korean cue before the verb (`이제 …겠습니다`,
  `다음 단계로 …ㄹ게요`) or a verb that is itself the next step (`진행하겠습니다`,
  `진행할게요`); English cue next to the verb (`Next, I'll`, `I'll now`,
  `I'll continue`, `I'll migrate the remaining …`, `I'll … after this`) or a
  cue that opens the sentence (`Next up:`, `Moving on to`). A reporting verb
  after `I'll` (`summarize`, `note`, `report`, `wait`) is not a next step,
  and neither is a deferral out of the turn (`다음에는`, `앞으로`, `나중에`,
  `next time`, `follow-up`, `later`) or a closing (`마치겠습니다`, `wrap up`).
- **Interim report (type 4).** A sentence frames the message as an interim or
  milestone report (`중간 보고`, `진행 상황`, `이 시점에서`, `progress update`,
  `so far`, `at this point`) AND a *different* sentence lists an unfinished
  item (`미착수`, `진행 중`, `not started`, `pending`, `still to do`) that it
  does not negate ("남은 작업은 없습니다", "nothing remaining").

The regexes below are the authority; the words above are examples.

It stays silent — a stop the user wants — when any of these holds:

- the text states a blocker as a need or a lack (`자격 증명이 없어`,
  `비밀번호를 알려주시면`, `needs a password I don't have`,
  `waiting on your approval`, `blocked on`, `once you share`). This is the
  guide's own pair of wanted stops: "where nothing can move without them, or
  where the thing blocking you is deliberately protected from you";
- praxis additions: the human message that opened the turn asked for a
  report, a plan, or a pause, or asked a question (a word-initial
  interrogative plus a question ending, or an English auxiliary-inversion
  question); `prose-option-menu-advisory` would fire on the same text (type
  3: that hook owns decision menus, and loading its predicate keeps the two
  disjoint);
- `stop_hook_active` is set — the host's re-entry flag, true when this Stop
  follows a continuation that a Stop hook's block forced.

The closing lines are the last three prose lines, plus any short
`Key: value` status lines after them. The guide's type 1 is a summary that
*closes* on an announcement; the same verb mid-report usually narrates what
was done in order.

## Delivery: a notice to the user, not a continuation

The output is a Stop `systemMessage`. `_hook_io.py` documents that it is shown
to the user and NOT fed to the model, so the model never reads it and nothing
continues on its own. The guide's pattern instead sends the open items to the
model as the next user message. Here the notice tells the user that work
still looks open and that they can reply "continue".

It is advisory, not a block, because every marker above is also written by a
turn that finished correctly, and a blocker the model did not name reads the
same as no blocker.

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

# How many prose lines from the end a type-1/type-2 sentence may sit and
# still be the message's closing move. `Key: value` status lines after it
# do not count toward the window.
_CLOSING_LINES = 3
# At most this many trailing status lines are passed over.
_MAX_STATUS_LINES = 10

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。？])\s+")

# Inline quoted spans: a quoted plan step or user phrase is not the model's
# own announcement. A straight single quote opens a span only when it does
# not follow a word character, and an apostrophe between two word characters
# stays inside the span, so `I'll` and `users'` are not quote marks.
_INLINE_QUOTE_RE = re.compile(
    r"\"[^\"\n]*\"|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」|『[^』\n]*』"
    r"|(?<!\w)'(?:[^'\n]|(?<=\w)'(?=\w))*'(?!\w)"
)
_QUOTE_OPENERS = "\"“‘「『'"

# A short `Key: value` status line (`Tests: 28 passed`, `- Lint: clean`).
_STATUS_LINE_RE = re.compile(
    r"^(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*)?[^\s:*][^:\n]{0,30}?(?:\*\*)?:\s+\S[^\n]{0,60}$"
)

# Deferrals that point outside this turn — shared by types 1 and 2.
_OUT_OF_TURN_RE = re.compile(
    r"다음에는|다음번|다음 번|다음 기회|다음 세션|다음 PR|다음 이슈|후속|앞으로|나중에|추후|내일"
    r"|\bnext time\b|\bnext session\b|\bfollow-?up\b|\bnext PR\b|\bfrom now on\b"
    r"|\bgoing forward\b|\blater\b|\btomorrow\b",
    re.IGNORECASE,
)

# --- type 2: conditional offer to continue --------------------------------

_OFFER_RE = re.compile(
    r"원하시면|원하신다면|필요하시면|괜찮으시면|괜찮다면|괜찮으시다면"
    r"|(?:할|갈|볼|드릴|진행할|이어갈|계속할)까요|(?:면|도) 될까요"
    r"|\bif you(?:'d| would)? (?:like|want|prefer)\b|\bunless you(?:'d| would)? "
    r"(?:prefer|rather|like)\b|\bwant me to\b|\bshall I\b|\bshould I\b"
    r"|\bwould you like me to\b|\blet me know if you(?:'d| would)? (?:like|want)\b"
    r"|\bif that (?:works|sounds good|is ok(?:ay)?)\b"
    r"|\bif (?:that's|you're) (?:ok|okay|fine)\b"
    r"|\bhappy to (?:continue|keep|carry|proceed|finish)",
    re.IGNORECASE,
)

# Continuing the requested work, not starting a new item.
_CONTINUE_RE = re.compile(
    r"이어서|이어가|계속|남은|나머지|마저"
    r"|\bcontinue\b|\bproceed\b|\bcarry on\b|\bkeep going\b|\bremaining\b"
    r"|\brest of\b|\bthe rest\b|\bthe others\b|\bmove on\b"
    r"|\bfinish (?:the )?(?:rest|remaining|remainder|up|off)\b",
    re.IGNORECASE,
)

# --- type 1: first-person future announcing the next step -----------------

_FUTURE_KO_RE = re.compile(r"겠습니다|겠어요")
# `…ㄹ게요` (할게요, 드릴게요, 옮길게요): the syllable before 게요 ends in ㄹ.
_GEYO_RE = re.compile(r"([가-힣])게요")
_JONGSEONG_RIEUL = 8

_NEXT_CUE_KO_RE = re.compile(
    r"다음 ?단계|다음으로|다음 작업|이제|이어서|이어가|계속|남은|나머지|곧바로|그 ?다음"
)
# A future verb that is itself the next step: 진행하겠습니다, 진행할게요.
_NEXT_VERB_KO_RE = re.compile(r"(?:진행|착수|시작)(?:하겠|할게)")

_EN_FUT = r"(?:I(?:'ll| will| am going to|'m going to|'m about to| am about to))"
# Reporting verbs after `I'll`: the sentence reports, it does not announce work.
_EN_SKIP = (
    r"(?!(?:summari[sz]e|sum up|recap|note|report|mention|point out|wait"
    r"|let you know|conclude|close)\b)"
)
_NEXT_EN_RE = re.compile(
    # cue before the verb: "Next, I'll …", "now let me …"
    r"\b(?:next|now|then)\b,?\s+(?:" + _EN_FUT + r"|let me)\s+" + _EN_SKIP + r"\w"
    # adverb after the verb: "I'll now migrate …", "let me now …"
    r"|\b(?:" + _EN_FUT + r"|let me)\s+(?:now|next|then)\s+" + _EN_SKIP + r"\w"
    # the verb is itself continuing: "I'll continue with …"
    r"|\b" + _EN_FUT + r"\s+(?:also\s+)?(?:continue|proceed|move on|keep going|carry on"
    r"|resume|pick up)\b"
    # the cue names the object: "I'll migrate the remaining …", "… after this"
    r"|\b" + _EN_FUT + r"\s+" + _EN_SKIP + r"\w+(?:\s+[^\s.!?]+){0,5}?\s+"
    r"(?:the (?:remaining|rest)\b|remaining\b|after (?:this|that)\b|next\b)"
    # the cue opens the sentence: "Next up: …", "Moving on to …"
    r"|^(?:next up|up next)\b|^moving on to\b",
    re.IGNORECASE,
)

# Sentences that close the message rather than announce work.
_CLOSING_MOVE_RE = re.compile(
    r"않겠|안 하겠|마치겠|마무리하겠|종료하겠|줄이겠|멈추겠"
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

# --- silence: a blocker stated as a need or a lack ------------------------

_BLOCKER_RE = re.compile(
    # Korean: a missing or needed credential/access, or a handover the user owns
    r"(?:자격 ?증명|크리덴셜|비밀번호|패스워드|토큰|권한|접근 ?권한|API ?키|액세스)"
    r"(?:이|가|을|를)? ?(?:없|필요|주셔야|주시면)"
    r"|(?:알려|공유해|보내|전달해|승인해|제공해|부여해|넣어|설정해|발급해) ?주시면"
    r"|승인(?:이|을)? (?:필요|기다|대기|받아야|받은 ?후|받은 ?뒤|해 주셔야)|승인 대기"
    r"|(?:진행|계속|작업)할 수 없|진행이 불가|불가능합니다|막혀|블로커|차단되"
    r"|결정(?:이|을)? (?:필요|기다)|주셔야|대기하고 있"
    # English (the lack-or-need construction is `_has_en_lack_blocker`)
    r"|\bwaiting (?:on|for) (?:you|your|approval|access|credentials|a decision"
    r"|sign-?off)\b"
    r"|\bblocked (?:on|by)\b|\bcan(?:no|')t (?:proceed|continue)\b"
    r"|\bunable to (?:proceed|continue)\b|\bpermission denied\b"
    r"|\bonce you (?:provide|approve|confirm|grant|share|send|give)\b",
    re.IGNORECASE,
)

# English lack or need, bound to what is missing within the same clause:
# `needs a staging DB password`, `no staging DB credentials`. The noun is
# found first and the trigger looked for in the clause before it, so the
# scan stays linear on long inputs.
_BLOCKER_NOUN_EN_RE = re.compile(
    r"\b(?:credentials?|password|token|api key|access|permissions?|approval"
    r"|sign-?off|decision|input|confirmation)\b",
    re.IGNORECASE,
)
_LACK_TRIGGER_EN_RE = re.compile(
    r"\b(?:no|without|missing|lack(?:ing|s)?|needs?|needed|requires?|don't have"
    r"|do not have|doesn't have|haven't got)\b",
    re.IGNORECASE,
)
_CLAUSE_BREAK_RE = re.compile(r"[.!?\n]")
_LACK_WINDOW = 50


def _has_en_lack_blocker(body: str) -> bool:
    for noun in _BLOCKER_NOUN_EN_RE.finditer(body):
        window = body[max(0, noun.start() - _LACK_WINDOW) : noun.start()]
        clause = _CLAUSE_BREAK_RE.split(window)[-1]
        if _LACK_TRIGGER_EN_RE.search(clause):
            return True
    return False


# --- silence: the user asked for the stop ---------------------------------

_USER_WANTS_STOP_RE = re.compile(
    r"(?:진행 ?(?:상황|현황)|현황|상태)(?:만|을|를)? ?(?:알려|공유|보고|정리)"
    r"|중간 ?보고|보고해|보고만"
    r"|계획(?:만|을 세워|을 짜|부터)|플랜(?:만|을 짜|부터)|멈춰|멈추고|여기까지만"
    r"|하나씩|단계별로 확인|확인받|확인 받"
    r"|\bstatus (?:of|on|update|report)\b"
    r"|\b(?:give|send|show) me (?:a |the |an )?(?:status|progress|update)\b"
    r"|\bwhat(?:'s| is) the (?:status|progress)\b|\bprogress (?:report|update)\b"
    r"|\breport back\b|\bcheck in with me\b"
    r"|\b(?:make|write|draft|give me|propose) (?:a |the )?plan\b|\bplan (?:only|first)\b"
    r"|\b(?:pause|stop) (?:after|here|there|when|before|once)\b"
    r"|\bone (?:at a time|by one)\b|\bstep by step\b",
    re.IGNORECASE,
)

# Korean interrogatives as word-initial tokens; `-든`/`-나`/`-라도` turn them
# into indefinites (`어떻게든`, `언제나`, `뭐라도`), which are not questions.
_INTERROGATIVE_RE = re.compile(
    r"(?<![가-힣])(?:왜|뭐|무엇|무슨|어떻게|어떤|어느|언제|어디|누가|몇)(?!든|나|라도)"
    r"|\b(?:why|what|how|which|when|where|who)\b",
    re.IGNORECASE,
)
_QUESTION_END_RE = re.compile(r"(?:[?？]|까|나요|가요|니|냐|지)\s*$")
# `Are the endpoints done?` — but `Can you migrate …?` is a request.
_AUX_QUESTION_RE = re.compile(
    r"^(?!(?:can|could|will|would) you\b)"
    r"(?:are|is|was|were|did|does|do|has|have|can|could|will|would)\b[^\n]*\?\s*$",
    re.IGNORECASE,
)


def _content_lines(text: str) -> list[str]:
    """Non-empty lines outside fenced code blocks and `>` quotes, with inline
    quoted spans removed."""
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
        if any(q in line for q in _QUOTE_OPENERS):
            line = _INLINE_QUOTE_RE.sub('""', line).strip()
            if not line or line == '""':
                continue
        lines.append(line)
    return lines


def _sentences(line: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(line) if s.strip()]


def _closing_sentences(lines: list[str]) -> list[str]:
    """Sentences of the last `_CLOSING_LINES` prose lines and the status
    lines (up to `_MAX_STATUS_LINES`) that follow the earliest of them."""
    start = len(lines)
    prose = status = 0
    while start > 0 and prose < _CLOSING_LINES:
        start -= 1
        if status < _MAX_STATUS_LINES and _STATUS_LINE_RE.match(lines[start]):
            status += 1
        else:
            prose += 1
    sentences: list[str] = []
    for line in lines[start:]:
        sentences.extend(_sentences(line))
    return sentences


def _is_offer_to_continue(sentence: str) -> bool:
    return bool(
        _OFFER_RE.search(sentence)
        and _CONTINUE_RE.search(sentence)
        and not _OUT_OF_TURN_RE.search(sentence)
    )


def _ko_future_after(sentence: str, pos: int) -> bool:
    if _FUTURE_KO_RE.search(sentence, pos):
        return True
    return any(
        (ord(m.group(1)) - 0xAC00) % 28 == _JONGSEONG_RIEUL
        for m in _GEYO_RE.finditer(sentence, pos)
    )


def _is_announced_next_step(sentence: str) -> bool:
    if _OUT_OF_TURN_RE.search(sentence) or _CLOSING_MOVE_RE.search(sentence):
        return False
    if _NEXT_EN_RE.search(sentence.strip()) or _NEXT_VERB_KO_RE.search(sentence):
        return True
    cue = _NEXT_CUE_KO_RE.search(sentence)
    return cue is not None and _ko_future_after(sentence, cue.end())


def _open_item_sentence(lines: list[str]) -> str | None:
    """An unfinished-item sentence that is not itself the interim framing."""
    for line in lines:
        for sentence in _sentences(line):
            if (
                _OPEN_ITEM_RE.search(sentence)
                and not _OPEN_ITEM_NEGATED_RE.search(sentence)
                and not _INTERIM_RE.search(sentence)
            ):
                return sentence
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
    if _BLOCKER_RE.search(body) or _has_en_lack_blocker(body):
        return None

    closing = _closing_sentences(lines)
    for sentence in closing:
        if _is_offer_to_continue(sentence):
            return "an offer to continue that waits on your preference", sentence
    for sentence in closing:
        if _is_announced_next_step(sentence):
            return "a next step announced but not taken", sentence

    if _INTERIM_RE.search(body):
        open_item = _open_item_sentence(lines)
        if open_item is not None:
            return "an interim report that lists unfinished items", open_item
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
    if _AUX_QUESTION_RE.match(last):
        return True
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
    """A notice for the user: a Stop `systemMessage` is not fed to the model."""
    return (
        f"{_PREFIX} The turn ended with requested work apparently still open — "
        f"{kind}:\n"
        f"  \"{_clip(evidence)}\"\n"
        "  Claude does not see this notice. If nothing blocks the open work, "
        f"reply \"continue\". Bypass: {_BYPASS_ENV}=1"
    )


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip():
        return 0

    payload = read_payload()
    if not isinstance(payload, dict):
        return 0
    if payload.get("stop_hook_active"):
        return 0  # re-entry: this Stop follows a Stop-hook-forced continuation

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
