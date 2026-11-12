#!/usr/bin/env python3
"""merge-gate check: the Pre-Merge Reporting briefing before `gh pr merge`.

Moved from momentum-rule-retrieval-gate's `merge` trigger (issue #1650); the
verdicts and messages are unchanged. On a `gh pr merge` it prints the merge
rule surface to stderr, then denies the merge when the transcript does not
carry the six-part briefing and the user's answer to it (issues #797, #940,
#1214, #1402, #1419, #1433, #1597). `host_merge_decision` and
`bulk_merge_approved` are also read by the approval check.

Env: PRAXIS_MOMENTUM_BYPASS=1 silences it, PRAXIS_MOMENTUM_MERGE_ADVISORY=1
demotes the deny to the advisory, PRAXIS_MOMENTUM_STRICT=1 (without
PRAXIS_MOMENTUM_ACK=1) blocks every merge, as before the move.

Fail-open contract:
  • Malformed JSON / non-Bash payload → exit 0
  • Empty command → exit 0
  • Any uncaught exception in inner logic → swallowed, exit 0
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import deque
from pathlib import Path
from urllib.parse import urlparse
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    GH_MERGE_VALUE_FLAGS,
    _is_gh_binary,
    is_help_invocation,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
import _host_merge_approval as _host_approval  # type: ignore[import-not-found]  # noqa: E402
import _momentum_surface as _surface  # type: ignore[import-not-found]  # noqa: E402
from _memory_dir import resolve_memory_dir  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    TRANSCRIPT_SCAN_LINES,
    block_prose,
)
from block_message import (  # type: ignore[import-not-found]  # noqa: E402
    format_block,
    verb_gate_checklist,
)

# ---------------------------------------------------------------------------
# Prefix used on every stderr line.
# ---------------------------------------------------------------------------

PREFIX = _surface.PREFIX

# Trigger identifier — key for the memory `momentum:` frontmatter filter.
TRIGGER_MERGE = "merge"

_MERGE_STATIC = """\
{p} ── TRIGGER: gh pr merge ──────────────────────────────────
{p}
{p} Rule: Pre-Merge Reporting (CLAUDE.md)
{p}   Before asking for merge approval, report: What changed / What was
{p}   verified / What was NOT verified / Risk / Open items / Explicit ask.
{p}   "완료했습니다, 머지할까요?" without evidence is an anti-pattern.
{p}
{p} Rule: No Approval Transfer Across Companion PRs (CLAUDE.md)
{p}   Approving "merge PR X" approves ONLY X. Companion, chore, regen, or
{p}   hotfix-blocker PRs each need their own explicit approval.""".format(p=PREFIX)

# ---------------------------------------------------------------------------
# Merge-briefing escalation (issue #797).
#
# The `merge` trigger alone is advisory (stderr) — but stderr cannot stop a
# `gh pr merge` that skips the Pre-Merge Reporting briefing, which recurred
# despite the advisory firing (memory feedback_pre_merge_briefing_compound_
# imperative, 2 recurrences). For the `merge` trigger ONLY, this hook inspects
# the assistant text preceding the merge call and, when the 6-item briefing is
# absent, escalates to a `permissionDecision: deny` so the merge is blocked and
# the agent must produce the briefing before retrying.
#
# Why deny (not ask): the sibling `pre-merge-approval-gate` already emits an
# unconditional `ask` on every `gh pr merge`. A second `ask` is redundant with
# the exact gate that failed to prevent the #795 recurrence (the user approved
# blindly while the agent never produced the briefing). `deny` adds the missing
# teeth — it blocks and feeds the reason back so the agent self-corrects.
#
# Fail-open / false-positive containment:
#   • no readable transcript                 → no escalation (fail open)
#   • PRAXIS_MOMENTUM_MERGE_ADVISORY=1        → demote back to advisory only
#   • `# briefing-surfaced` + SOME briefing   → demote back to advisory only
#     (the marker attests completeness, never existence — see issue #940)
#   • trivial-PR markers in the briefing text → no escalation (CLAUDE.md carve-out)
# ---------------------------------------------------------------------------

MERGE_ADVISORY_ENV = "PRAXIS_MOMENTUM_MERGE_ADVISORY"

# In-band bypass marker (issue #826). The env bypass above is read from the hook
# process `os.environ` and is NOT reachable via a Bash inline `VAR=1 cmd` prefix
# — the hook is spawned by the harness, not as a child of the command. In a
# bridge-session harness that ALSO drops assistant text from the transcript, a
# legitimate briefing-surfaced merge is otherwise permanently blocked with no
# in-band way to release it (observed live: #826, and again merging #834). A
# marker embedded in the command string (a shell `#` comment, harmless at exec —
# bash ignores everything after `#`) IS reachable in-band and demotes escalation
# to advisory. This is a conscious self-attestation, appropriate for a
# self-discipline nudge rather than an adversarial boundary: the agent asserts
# the briefing was surfaced, exactly as the env bypass would.
_BRIEFING_MARKER_RE = re.compile(r"#\s*briefing-surfaced\b", re.IGNORECASE)


def _split_shell_comment(command: str) -> tuple[str, str]:
    """Split a command into (code, comment) at each UNQUOTED, word-boundary `#`,
    matching Bash semantics: `#` starts a comment only at the start of a word
    (preceded by whitespace / a command separator / line start) and outside
    quotes; it runs to the end of that physical line. A mid-word `x#`, a quoted
    `'#'`, or a backslash-escaped `\\#` is literal data, not a comment.

    Lexical state (quote + backslash escape) is tracked continuously across
    physical lines so a single-quoted string spanning newlines (e.g.
    `printf 'a\\n# x\\n'`) is not misread as a comment (round-3 P2). Heredoc
    bodies are NOT parsed — a `# briefing-surfaced` inside a `<<EOF` body would
    be treated as a comment; under this gate's non-adversarial threat model
    (self-discipline nudge, not a security boundary) that residual is accepted.

    Returns (code_without_comments, joined_comment_text). Used so the marker is
    detected only in a real comment (round-1/2 P2) AND so segment/repetition
    analysis never sees the comment's reason text (round-2 P2b)."""
    code_chars: list[str] = []
    comment_chars: list[str] = []
    in_single = in_double = escaped = in_comment = False
    prev_is_boundary = True  # command start is a word boundary
    for c in command:
        if in_comment:
            if c == "\n":
                in_comment = False
                prev_is_boundary = True
                code_chars.append(c)
            else:
                comment_chars.append(c)
        elif escaped:  # backslash-escaped char is literal data
            code_chars.append(c)
            escaped = False
            prev_is_boundary = False
        elif in_single:
            code_chars.append(c)
            in_single = c != "'"
            prev_is_boundary = False
        elif in_double:
            code_chars.append(c)
            if c == "\\":
                escaped = True
            else:
                in_double = c != '"'
            prev_is_boundary = False
        elif c == "\\":
            code_chars.append(c)
            escaped = True
            prev_is_boundary = False
        elif c == "'":
            code_chars.append(c)
            in_single = True
            prev_is_boundary = False
        elif c == '"':
            code_chars.append(c)
            in_double = True
            prev_is_boundary = False
        elif c == "#" and prev_is_boundary:
            in_comment = True
            comment_chars.append(c)
        elif c == "\n":
            code_chars.append(c)
            prev_is_boundary = True
        else:
            code_chars.append(c)
            prev_is_boundary = c.isspace() or c in ";|&("
    return "".join(code_chars), "".join(comment_chars)


def _has_briefing_marker(command: object) -> bool:
    """True when the command carries the in-band `# briefing-surfaced` marker in
    an UNQUOTED shell comment (not a quoted argument, not a mid-word `#`).

    A quoted occurrence (`grep '# briefing-surfaced' … && gh pr merge …`) or a
    mid-word `#` (`echo x# briefing-surfaced`) is data, not an attestation, and
    must not bypass the gate (round-1/2 P2)."""
    if not isinstance(command, str):
        return False
    _, comment = _split_shell_comment(command)
    return _BRIEFING_MARKER_RE.search(comment) is not None

# Distinct Pre-Merge Reporting items must be present in the pre-merge assistant
# text. The documented failure surfaced only 3 of 6 (What changed / verified /
# risk — missing NOT-verified, Open-items, and the explicit approve-ask), so a
# floor of 4 separates that case from a complete (or near-complete) briefing
# while keeping the false-positive rate low.
MERGE_BRIEFING_MIN_ITEMS = 4

# Floor for the `# briefing-surfaced` marker to release a merge (issue #940).
# The marker exists because the counter above reads prose and under-counts a
# briefing phrased outside its vocabulary — so it substitutes for the missing
# items, not for the whole briefing. One recognised item is the weakest evidence
# that a briefing is being attested to at all; zero means the attestation has no
# referent, which is the state the marker was releasing on 8 of 11 merges.
MERGE_BRIEFING_MARKER_MIN_ITEMS = 1

# Each tuple is one Pre-Merge Reporting item; a single keyword hit marks the
# item present. Bilingual (EN/KO) since briefings are authored in Korean.
# Korean section labels match only with their colon ("바뀐 것:"): the bare
# phrase is ordinary prose too ("새로 바뀐 것은 없습니다").
_BRIEFING_ITEM_GROUPS: tuple[tuple[str, ...], ...] = (
    # 1. What changed
    ("what changed", "무엇이 변경", "무엇이 바뀌", "바뀌는 것", "변경 사항",
     "변경사항", "변경 내용", "changed:", "changes:", "scope summary",
     "what was changed", "바뀐 것:"),
    # 2. What was verified
    ("verified", "검증", "verification", "tested", "test pass", "테스트 통과",
     "lint clean", "확인 완료", "확인함", "확인한 것:"),
    # 3. What was NOT verified
    ("not verified", "not tested", "미검증", "검증 안 된", "검증되지 않은",
     "미확인", "unverified", "not exercised", "검증하지", "확인하지 못",
     "deferred", "ci pending", "skipped", "못 한 것:", "못한 것:"),
    # 4. Risk / blast radius
    ("risk", "blast radius", "리스크", "위험", "영향 범위", "영향범위",
     "blast", "downstream", "affects"),
    # 5. Open items
    ("open item", "열린 항목", "미해결", "남은 항목", "남은 작업", "남은 것",
     "follow-up", "follow up", "후속", "caveat", "unresolved"),
    # 6. Explicit approve-ask
    ("approve", "승인", "머지할까요", "머지 할까요", "approve merge", "merge?",
     "진행할까요", "머지해도", "머지 진행", "proceed with the merge"),
)

# The 'verified' group shares substrings with the 'not verified' group —
# "not verified" ⊃ "verified", "not tested" ⊃ "tested", "unverified" ⊃
# "verified", "미검증" ⊃ "검증". A negative-phrasing briefing would otherwise
# satisfy BOTH groups from one phrase and inflate the count, letting a
# changed / not-verified / risk briefing reach the 4-item pass threshold with
# only 3 real items. The verified check therefore runs against a haystack with
# the not-verified phrases stripped out first (see _briefing_item_count).
# Named indices keep the strip correct if the group order is ever changed.
_VERIFIED_GROUP_IDX = 1
_NOT_VERIFIED_GROUP_IDX = 2

# Trivial-PR carve-out — CLAUDE.md permits a 2-line report for typo / comment /
# single-line config merges. When the agent has flagged the PR as trivial, the
# full 6-item briefing is not required, so do not escalate.
_TRIVIAL_MERGE_MARKERS: tuple[str, ...] = (
    "trivial pr", "trivial change", "typo", "comment-only", "comment only",
    "single-line", "single line", "오타", "주석만", "주석 수정", "2-line report",
)


def _briefing_item_count(text: str) -> int:
    """Count distinct Pre-Merge Reporting items present in the assistant text.

    The 'verified' group is checked against a haystack with the 'not verified'
    phrases stripped out, so a negative-phrasing briefing cannot satisfy both
    the verified and not-verified items from a single phrase (substring overlap
    documented at _VERIFIED_GROUP_IDX / _NOT_VERIFIED_GROUP_IDX).
    """
    low = text.lower()
    verified_haystack = low
    for kw in _BRIEFING_ITEM_GROUPS[_NOT_VERIFIED_GROUP_IDX]:
        verified_haystack = verified_haystack.replace(kw, " ")
    count = 0
    for idx, group in enumerate(_BRIEFING_ITEM_GROUPS):
        haystack = verified_haystack if idx == _VERIFIED_GROUP_IDX else low
        if any(kw in haystack for kw in group):
            count += 1
    return count


def _is_trivial_merge(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _TRIVIAL_MERGE_MARKERS)


# Short user acknowledgements that count as "approval reply" (EN/KO). Matched
# by exact normalized-string equality, so a substantive short message
# ("go fix the parser") never triggers the prior-turn window extension.
_APPROVAL_TOKENS = frozenset({
    "ok", "okay", "okey", "k", "kk", "yes", "yep", "yup", "y", "go", "go ahead",
    "do it", "proceed", "proceed with the merge", "merge", "merge it", "sure",
    "lgtm", "ship it", "sounds good",
    "approve", "approved", "approve merge", "머지 승인",
    "네", "넵", "응", "ㅇㅋ", "ㅇㅇ", "ㄱㄱ", "고고", "승인", "진행", "진행해",
    "진행해줘", "진행하자", "머지", "머지해", "머지해줘", "머지 진행", "좋아",
    "좋습니다", "그래", "ㄱ",
})

# A user rarely answers a merge ask with one bare word — "ok, merge it" and
# "사용해보고 결정하겠습니다. 머지 진행" are the same consent, and denying them
# blocks the mandated briefing → approval → merge flow outright (issue #1087).
# Only the FINAL clause is matched: an approval token earlier in a message whose
# last clause is a fresh instruction ("머지 진행 상황 알려주고 파서부터 고쳐줘")
# still fails, which is the property exact equality was protecting.
_CLAUSE_TAIL_RE = re.compile(r"[.!?。…\n,;·]+")

# A numbered-list line (`1. …`, `1) …`, `(1) …`, `1: …`, `1번 …`, with an
# optional bullet, heading or bold around the marker). A reply that answers
# numbered questions item by item puts the merge answer at the merge question's
# number, not necessarily last, so the final-clause rule above would read the
# answer to some other question (issue #1630). `1.5` and `10:30` are not markers.
_LIST_ITEM_RE = re.compile(
    r"^(?P<indent>[ \t>]*)(?:[-*+][ \t]+|#{1,6}[ \t]+)?[*_]{0,2}"
    r"(?:\((?P<paren>\d{1,3})\)|(?P<num>\d{1,3})(?:\.(?!\d)|\)|:(?!\d)|[ \t]*번(?:[ \t]*[.):])?))"
    r"[*_]{0,2}[ \t]*(?P<text>.*)$")
# A numbered request list often asks with a noun phrase instead of a question
# mark: `1. #999 머지 승인 여부`, `1. Whether to merge #999`. Only an item's
# first line is read this way, and it still has to pass `_is_merge_ask` with
# the `?` it leaves out — so `머지 완료 여부` stays a status question.
_ITEM_ASK_RE = re.compile(r"(?:여부|할지요?)$|^whether(?![A-Za-z])", re.IGNORECASE)
# Words that make a question about merging ask something else: when or under
# what condition (`before merging #999?`, `#999 머지 전 CI 재실행 여부`), a hold
# or delay, a step around the merge (rebase, rerun, delete the branch), or a
# choice (`merge #999 or close it?`). `_is_merge_ask` accepts these, which an
# unnumbered `yes` already relies on; a numbered reply is read only against a
# plain ask. A match here only costs one more ask.
_ITEM_ASK_QUALIFIER_RE = re.compile(
    r"(?<![A-Za-z])(?:before|after|once|until|when|then|first|or|if|unless|wait|hold"
    r"|defer|delay|postpone|later|rebase|re-?run|delete|close|instead)(?![A-Za-z])"
    r"|(?:머지|병합)\s*(?:전|후|뒤)|전에|후에|뒤에|먼저|미루|미룰|대기|기다|보류|나중"
    r"|리베이스|재실행|삭제|닫|아니면|또는|대신|말고|조건",
    re.IGNORECASE)
# A line this long is not a question someone answers by number, and
# `_ASK_SENTENCE_RE` is quadratic on a long line with no sentence end.
_ITEM_LINE_MAX = 400
_URL_RE = re.compile(r"https?://[^\s?]+")
# An option bullet under a numbered ask (`   - (추천) 승인합니다`).
_BULLET_RE = re.compile(r"^\s*[-*+•]\s")

# A menu written as prose (or in a host's question tool that keeps no
# `"q"="a"` row) lists its choices as lettered lines, and the user answers
# with the letter alone. The reply is one letter A-D, any case, with an
# optional trailing period.
_LETTER_REPLY_RE = re.compile(r"^([a-d])\.?$")
# A lettered choice line of the turn the letter answers: `A: 머지합니다`,
# `- A: merge`, `A (추천): 머지`, `**A**: 머지`, `**A:** merge`. The letter is
# uppercase and followed by a colon, so prose (`a: …`, `A. Smith`) is no menu.
_OPTION_LINE_RE = re.compile(
    r"^[ \t>]*(?:[-*+•][ \t]+)?(?:\*\*|__)?(?P<letter>[A-D])(?:\*\*|__)?[ \t]*"
    r"(?:\((?P<note>[^()\n]{0,30})\)[ \t]*)?(?:\*\*|__)?[:：](?:\*\*|__)?[ \t]*(?P<label>.*)$")
# A choice whose lead is merging said plainly (`merge`, `Merge PR #999`,
# `머지합니다`, `머지 진행`) is the merge choice. A closed set, not a prefix:
# `머지 진행 상황 알려줘` and `머지 전에 리베이스` lead with the word and ask for
# something else. `merged` is a past state, as in `_MERGE_WORD_RE`.
_MERGE_LEAD_RE = re.compile(
    r"(?:squash\s+)?merge(?:\s+(?:it|now|this|this\s+pr|the\s+pr|pr))?"
    r"|(?:스쿼시\s*)?(?:머지|병합)\s*(?:합니다|할게요|하기|해|해요|해줘|해\s*주세요|하겠습니다"
    r"|진행|진행합니다|진행해|진행해\s*주세요|승인)?", re.IGNORECASE)
# The lead's first sentence: `머지합니다. 정리까지 합니다.` leads with `머지합니다`.
_LEAD_SENTENCE_RE = re.compile(r"[.。!](?:\s|$)")

# The harness writes the interrupt as a `role: user` entry with this exact text
# and no flag distinguishing it from typed input. Anchored at both ends: the
# same phrase inside a longer message is the user quoting it, not an interrupt.
# Both spellings occur in this machine's transcripts (146 and 100 of 246 bare
# markers); the corpus holds no third.
_INTERRUPT_ONLY_RE = re.compile(
    r"^\[request interrupted by user(?: for tool use)?\]$", re.IGNORECASE)

# Each `"question"="answer"` pair in an AskUserQuestion tool_result; the answer
# is the option label (or the typed "Other" text) the user picked.
_ASK_ANSWER_RE = re.compile(r'"((?:[^"\\]|\\.)*)"=\s*"((?:[^"\\]|\\.)*)"')

# A picked AskUserQuestion label usually leads with the approval and qualifies
# it after a separator ("승인 — 머지", "머지, `Carried: none`"). Only that leading
# segment is read: a keyword anywhere in the label cannot tell asking from
# agreeing, as "PR #999 머지 상태만 알려줘" shows.
_LABEL_LEAD_RE = re.compile(r"\s*[—–:,(-]\s*")

# …except that the qualifier can REVERSE the lead instead of narrowing it
# ("승인 — 머지하지 않기", "Approve — do not merge"). Reading only the lead turns
# that label into consent, so a negation anywhere in the label disqualifies the
# whole thing. The reversal takes several shapes — the negator can precede the
# verb ("안 머지"), follow it ("머지하지"), sit in a consequence ("머지하면 안
# 됨"), or be a bare English determiner ("no merge"). A label that declines is
# not an approval under any reading, so the cost of matching too widely here is
# one more ask.
_LABEL_NEGATION_RE = re.compile(
    r"하지\s*(?:않|말)|취소|보류|중단|나중에|안\s*(?:함|하기|할|돼|되|됨|됩)"
    r"|(?:안|말)\s*(?:머지|병합)|(?:머지|병합)\s*(?:하지|안\b)"
    r"|(?<![A-Za-z])(?:not|don't|dont|never|cancel|hold|skip|abort)(?![A-Za-z])"
    r"|(?<![A-Za-z])no\s+merg(?:e|ing)(?![A-Za-z])",
    re.IGNORECASE)

# A merge ask is one sentence that both names merging and asks ("Approve
# merge?", "PR #999를 머지할까요?"). The briefing counter's approve-ask words
# cannot stand in for it: "approve" also matches "PR #999 was approved", and
# "승인" matches "테스트 승인할까요?", and neither of those asks to merge.
# The merge word is `merge` or `merging` bounded by non-letters, so "emergency"
# and "merge-base" stay out. `merged` stays out on purpose: it is the past
# participle, and every question built on it asks whether the merge happened
# ("Was #999 merged?") rather than asking to do it — which is the same reason a
# question that negates merging or asks after its state ("머지하지 말까요?",
# "머지됐나요?", "머지했나요?", "Did we merge #999?") is not an ask to merge.
# A completion question is the same kind ("Is the merge of #999 done?", "머지
# 완료됐어?"). The English form stops at a conditional so "Merge #999 once CI is
# done?" stays an ask; the Korean form needs a state ending, so "머지 완료할까요?"
# (asking to finish the merge) stays one too. Excluding an ask only denies more.
_MERGE_WORD_RE = re.compile(r"(?<![A-Za-z])merg(?:e|ing)(?![A-Za-z-])|머지|병합",
                            re.IGNORECASE)
_NOT_MERGE_ASK_RE = re.compile(
    r"(?<![A-Za-z])(?:not|don't|dont|never)(?![A-Za-z])"
    r"|(?:머지|병합)\s*(?:됐|되었|된|되어|되나|했|하셨|하였|하지|안\b|말|상태|충돌|결과)"
    r"|(?:머지|병합)\s*(?:이|가|은|는)?\s*(?:(?:완료|끝|성공)\s*(?:됐|되었|된|되어|되나|되|했|하였|났|나|인|이|여부|\?)|진행\s*중)"
    r"|(?:안|말)\s*(?:머지|병합)|말까요"
    r"|(?<![A-Za-z])(?:did|have|has)\s+(?:we|you|i|they|it)\s+(?:already\s+)?merg"
    r"|(?<![A-Za-z])merg(?:e|ing)\s+(?:status|state|conflicts?|results?)(?![A-Za-z])"
    # "Is GitHub merging #999?" asks what is happening; "Is merging #999 OK?"
    # has no subject before `merging` and "Is it fine merging" no bare one.
    r"|(?<![A-Za-z])(?:is|are|was|were)\s+(?:the\s+)?(?!merging(?![A-Za-z]))[A-Za-z]+"
    r"(?:\s+(?:still|now|already|currently))?\s+merging(?![A-Za-z])"
    r"|(?<![A-Za-z])merg(?:e|ing)"
    r"(?:(?!(?<![A-Za-z])(?:once|when|after|if|until|before)(?![A-Za-z]))[^.?!\n]){0,40}?"
    r"(?<![A-Za-z])(?:complete[ds]?|done|finish(?:ed|es)?|succeed(?:ed|s)?"
    r"|go(?:ne)?\s+through|went\s+through|in\s+progress)(?![A-Za-z])",
    re.IGNORECASE)
_ASK_SENTENCE_RE = re.compile(r"[^.!?。\n]*(?:\?|할까요|될까요|하시겠|해도 되)")
# An explicit PR reference (`#N`, `PR N`, `…/pull/N`). A bare number is not one:
# "CI 10/10" must not read as a second PR in the turn.
_PR_REF_RE = re.compile(r"(?:#|/pull/|(?<![A-Za-z])pr\s*#?\s*)(\d+)(?![A-Za-z0-9_])", re.IGNORECASE)

# The PRs a merge verb takes as its object: a list of references right before
# 머지/병합 ("#1023, #1024 를 머지") or right after "merge" ("Approve merge #833").
# `·` separates a list in Korean prose ("#1525 · #1526"), like `,`.
_REF_LIST = (r"(?:#|(?<![A-Za-z])pr\s*#?\s*)\d+(?![A-Za-z0-9_])"
             r"(?:\s*(?:,|·|과|와|및|and|&)\s*(?:#|pr\s*#?\s*)?\d+(?![A-Za-z0-9_]))*")
# A count restating the list before the particle ("#1 · #2 · #3 세 건을 머지").
_REF_COUNT = r"(?:\s*(?:두|세|네|다섯|\d+)\s*(?:건|개)(?:\s*(?:모두|전부))?)?"
# A merge method between the particle and the verb ("두 건을 squash 머지",
# "#5 를 스쿼시로 병합") names how to merge, not a different object.
_MERGE_METHOD = (r"(?:(?<![A-Za-z])(?:squash|rebase)(?![A-Za-z])|스쿼시|리베이스)"
                 r"(?:\s*(?:으로|로|방식으로))?\s*")
_MERGE_OBJECT_RE = re.compile(
    rf"({_REF_LIST}){_REF_COUNT}\s*(?:을|를|은|는|도)?\s*(?:{_MERGE_METHOD})?(?:머지|병합)"
    rf"|(?<![A-Za-z])merg(?:e|ing)(?:\s*:\s*|\s+)(?:pr\s*)?({_REF_LIST})", re.IGNORECASE)

# A single positional token that is a bare PR number or a …/pull/N URL.
_PULL_TOKEN_RE = re.compile(r"^(?:\S*/pull/(\d+)|(\d+))$")

# A PR number alone does not identify a PR: every repo has a #999 (issue #1419).
# The repo a `gh pr` segment names comes from `-R`/`--repo` or from a …/pull/N
# URL positional; nothing else in a transcript resolves one, since an approval
# is prose and names no repo. `gh -R` accepts `[HOST/]OWNER/REPO`, so two and
# three segments both parse and the host is dropped — the same repo reached
# with and without an explicit host is one target. The cost is that one
# OWNER/REPO on two hosts (github.com and an enterprise server) also reads as
# one target; keeping the host would instead split the far commoner
# with-and-without-host pair into a false conflict.
_REPO_FLAGS = frozenset({"-R", "--repo"})
_REPO_SLUG_RE = re.compile(r"^(?:[A-Za-z0-9._-]+/)?([A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?$")
_PULL_URL_REPO_RE = re.compile(r"([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/\d+$")

# `gh pr merge` flags that consume a following value token — skipped when
# scanning for the first positional (the merge target) so a flag value like
# `--subject 833` is never mistaken for the PR number (issue #826, P2#6). The
# gh *global* value flags (`-R`/`--repo`, …) are included because they may trail
# the subcommand (`gh pr merge -R org/repo 999`); without skipping their value,
# `org/repo` would be read as the positional and 999 misclassified (P1d).
_MERGE_VALUE_FLAGS = GH_MERGE_VALUE_FLAGS

# Read-only `gh pr` verbs whose positional PR argument identifies the PR the
# session is operating on. A numberless `gh pr merge` runs on the current
# branch, so its real target is derived from the most recent such command in
# the window — the mandated Pre-Merge probe (`gh pr checks/view N`).
_CONTEXT_VERBS = frozenset({
    "view", "checks", "diff", "ready", "edit", "status", "comment", "review",
})
_CONTEXT_VALUE_FLAGS = frozenset({
    "-b", "--body", "-F", "--body-file", "-t", "--title", "--subject",
    "--json", "--jq", "--template", "--repo", "-R",
})
# `_segment_repo` reads both merge and context segments, so it skips the values
# of either verb family's flags.
_SEGMENT_VALUE_FLAGS = _MERGE_VALUE_FLAGS | _CONTEXT_VALUE_FLAGS

# Shell constructs that repeat a single textual merge segment at runtime
# (`for pr in 833 999; do gh pr merge "$pr"; done`, `xargs gh pr merge`). One
# approval must not ride a loop, so their presence disables the single-approval
# window extension. Matched as whole tokens (not a substring regex) so a
# hyphenated branch name like `issue-1-fix-for-x` is never mistaken for a loop.
_LOOP_KEYWORDS = frozenset({"for", "while", "until", "xargs"})


def _load_turn_entries(transcript_path: str) -> list[dict] | None:
    """Parse a bounded tail of the transcript into entry dicts, or None if unreadable."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            # Bounded tail read — hold at most TRANSCRIPT_SCAN_LINES lines in
            # memory instead of loading the whole (potentially huge) transcript.
            lines = deque(fh, maxlen=TRANSCRIPT_SCAN_LINES)
    except OSError:
        return None
    entries: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries


def _is_injected(ev: dict) -> bool:
    """True when a user-role entry was injected by the host, not written by the
    human: `isMeta`, or an `origin.kind` other than human (see below)."""
    if ev.get("isMeta"):
        return True
    origin = ev.get("origin")
    return isinstance(origin, dict) and origin.get("kind") not in (None, "human")


def _human_user_indices(entries: list[dict]) -> list[int]:
    """Indices of human-authored user messages (skip tool_result-only bridges).

    `isMeta` entries are injected, not typed (issue #940): a skill body loaded
    mid-turn and the expansion of a slash command both arrive as `role: user`
    with prose content, and the harness stamps both `isMeta: true` while a
    genuinely typed message carries no such flag. Counting them starts a new
    "since the last user message" window, which discards a briefing the user
    actually saw — a skill invoked between the briefing and the merge was enough
    to make a compliant flow look unbriefed.

    Host notifications carry no `isMeta` but are tagged by `origin.kind`
    (issue #1410): a background task finishing between the approval and the
    merge otherwise becomes the last user message and closes the prior-turn
    extension. An entry with no `origin` stays human, since transcripts written
    before the field existed carry none.

    An interrupt marker carries neither flag (issue #1459) and is not typed
    content either: it records that the user stopped the reply, so a briefing
    answered with `ok` right after one was still answered in that turn. Only a
    message whose whole text IS the marker is skipped — the phrase quoted inside
    a longer message is something the user wrote, and it still closes the window.
    """
    idxs: list[int] = []
    for i, ev in enumerate(entries):
        msg = ev.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "user" or ev.get("isSidechain"):
            continue
        if _is_injected(ev):
            continue
        content = msg.get("content", [])
        if _is_interrupt_only(content):
            continue
        if isinstance(content, str):
            if content.strip():
                idxs.append(i)
        elif isinstance(content, list):
            if any(isinstance(b, dict) and b.get("type") != "tool_result" for b in content):
                idxs.append(i)
    return idxs


def _assistant_text(entries: list[dict], lo: int, hi: int, pr: str | None = None) -> str:
    """Concatenate what the assistant said in entries[lo:hi]: its prose blocks,
    plus — when `pr` is given — each AskUserQuestion question that asks to merge
    `pr`.

    The user reads a question before answering it, so a briefing written inside
    the merge question is a briefing the user saw. Hosts whose approval tool
    carries the whole briefing in the question (Hermes `clarify`) left the prose
    empty, and every merge after the first was denied. Only the question asking
    to merge THIS PR counts: unrelated questions in the same window would add
    keywords no briefing of this merge carried."""
    texts: list[str] = []
    for ev in entries[lo:hi]:
        msg = ev.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant" or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for b in content:
                prose = block_prose(b)
                if prose is not None:
                    texts.append(prose)
                elif pr is not None:
                    texts.extend(_merge_ask_questions(b, pr, entries))
    return "\n".join(texts)


def _merge_ask_questions(block: object, pr: str, entries: list[dict]) -> list[str]:
    """The questions of an AskUserQuestion tool_use that ask to merge `pr`.

    A question that names PRs but not `pr` briefs another merge, even when it
    ends in an unnamed "Approve merge?". A question whose answer holds the
    merge (`보류`) or that the user declined (`is_error`) is not a briefing the
    user accepted: its tool_result is not a human message, so it does not close
    the window the way a typed `보류` does.
    """
    if not (isinstance(block, dict) and block.get("type") == "tool_use"
            and block.get("name") == "AskUserQuestion"):
        return []
    inp = block.get("input")
    questions = inp.get("questions") if isinstance(inp, dict) else None
    if not isinstance(questions, list):
        return []
    result = _tool_result_block(entries, block.get("id"))
    if result is not None and (
            result.get("is_error") or _question_answer_holds(result.get("content"), pr)):
        return []
    return [q["question"] for q in questions
            if isinstance(q, dict) and isinstance(q.get("question"), str)
            and _is_merge_ask(q["question"], pr)
            and pr in (set(_PR_REF_RE.findall(q["question"])) or {pr})]


def _question_answer_holds(content: object, pr: str) -> bool:
    """True when an AskUserQuestion result picks a non-approval for a question
    about `pr` or about merging. A label listing PRs (`#1 · #2 모두 머지`) is a
    bulk approval, which `_ask_label_approves` alone does not read."""
    return any((_mentions_pr(q, pr) or _MERGE_WORD_RE.search(q))
               and not _ask_label_approves(a, pr)
               and _bulk_reply_prs(a) is None
               for q, a in _ASK_ANSWER_RE.findall(_user_message_text(content)))


def _tool_result_block(entries: list[dict], tool_use_id: object) -> dict | None:
    """The tool_result block answering `tool_use_id`, else None."""
    if not isinstance(tool_use_id, str):
        return None
    for ev in entries:
        msg = ev.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for b in content:
            if (isinstance(b, dict) and b.get("type") == "tool_result"
                    and b.get("tool_use_id") == tool_use_id):
                return b
    return None


def _is_interrupt_only(content: object) -> bool:
    """True when a user message is the interrupt marker and nothing else.

    `_user_message_text` keeps text blocks only, so a marker sent with an image
    would flatten to the bare marker; the image is content of the user's own
    and must still close the window.
    """
    if isinstance(content, list) and not all(
        isinstance(b, dict) and b.get("type") == "text" for b in content
    ):
        return False
    return bool(_INTERRUPT_ONLY_RE.match(_user_message_text(content).strip()))


def _user_message_text(content: object) -> str:
    """Flatten a user message's content (str or block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b["text"] for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        )
    return ""


def _is_bare_approval(raw: str) -> bool:
    """True when lowercased `raw` is an approval token whole, or by its final clause."""
    # A reply that ends in a question asks rather than agrees: "Approve merge?"
    # typed back is the ask itself, and "ok?" is checking, not consent.
    if raw.endswith(("?", "？")):
        return False
    if re.sub(r"\s+", " ", raw).strip(" .!~,·") in _APPROVAL_TOKENS:
        return True
    # Split the RAW text: normalizing whitespace first turns a newline into a
    # space, which would collapse "briefing\napprove" into one clause and lose
    # the boundary the split is looking for. Whitespace inside each clause is
    # normalized after the split instead.
    clauses = [re.sub(r"\s+", " ", c).strip(" .!~,·") for c in _CLAUSE_TAIL_RE.split(raw)]
    clauses = [c for c in clauses if c]
    return bool(clauses) and clauses[-1] in _APPROVAL_TOKENS


def _list_items(text: str) -> tuple[list[tuple[int, list[str], list[str]]], bool]:
    """Top-level numbered items of `text` as `(number, body, after)`, and
    whether a non-blank line precedes the first item.

    Top level is the shallowest marker indent; a deeper marker (a nested
    sub-list) and an indented line are body of the item above. An
    unindented, unnumbered line after an item is not its body but goes to
    `after`: prose the writer put between or after the items."""
    lines = text.splitlines()
    marks = [_LIST_ITEM_RE.match(line) for line in lines]
    indents = [len(m.group("indent").expandtabs(4)) for m in marks if m]
    items: list[tuple[int, list[str], list[str]]] = []
    lead = False
    if not indents:
        return items, lead
    base = min(indents)
    for line, m in zip(lines, marks):
        if m and len(m.group("indent").expandtabs(4)) == base:
            items.append((int(m.group("paren") or m.group("num")), [m.group("text")], []))
        elif items:
            if line.strip() and not line[:1].isspace() and not m:
                items[-1][2].append(line)
            else:
                items[-1][1].append(line)
        elif line.strip():
            lead = True
    return items, lead


def _numbered_items(text: str) -> list[tuple[int, str]] | None:
    """`(number, text)` per top-level item of a numbered reply; `[]` when the
    reply is not a list, None when text outside the items breaks it — before
    the first item, or unindented after one — since that text answers
    nothing by number."""
    items, lead = _list_items(text)
    if not items:
        return []
    if lead or any(after for _, _, after in items):
        return None
    return [(n, "\n".join(body)) for n, body, _ in items]


def _strip_fences(text: str) -> str:
    """`text` without its fenced blocks (closed the CommonMark way, as
    `_pr_sections` closes them); an unclosed fence runs to the end."""
    kept: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        f = _FENCE_LINE_RE.match(line)
        if f is not None:
            run = f.group("fence")
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not f.group("rest").strip():
                fence = None
            continue
        if fence is None:
            kept.append(line)
    return "\n".join(kept)


def _picked_option(raw: str, asked: str) -> tuple[str, str] | None:
    """`(label, note)` of the choice line in `asked` that a one-letter reply
    `raw` picks; None when `raw` is not one letter A-D, or when `asked` has
    no line for that letter or more than one outside fenced blocks: two
    menus in one turn leave the letter ambiguous."""
    m = _LETTER_REPLY_RE.match(raw.strip().lower())
    if m is None:
        return None
    letter = m.group(1).upper()
    picked = [o for o in map(_OPTION_LINE_RE.match, _strip_fences(asked).splitlines())
              if o is not None and o.group("letter") == letter]
    if len(picked) != 1:
        return None
    return picked[0].group("label").strip(" \t*_"), picked[0].group("note") or ""


def _option_label_approves(label: str, note: str, pr: str) -> bool:
    """True when a picked choice line approves merging `pr`.

    The line carries no negation (`A: 머지하지 않기`, `A: 보류`) and names no
    other PR, its note included (`A (#1001): merge`). Only the lead is read,
    never the whole label: `A: Check merge status, ok` ends in an approval
    token but asks for something else. The lead approves when it is an
    approval token (`A: 승인, 머지`) or merging said plainly
    (`_MERGE_LEAD_RE`: `A: 머지합니다`, `A: merge: squash`). A wrong refusal
    costs one more ask."""
    line = f"{note} {label}"
    if _LABEL_NEGATION_RE.search(line) or set(_PR_REF_RE.findall(line)) - {pr}:
        return False
    lead = _LABEL_LEAD_RE.split(label.lower(), maxsplit=1)[0]
    lead = _LEAD_SENTENCE_RE.split(_strip_pr_ref(lead, pr), maxsplit=1)[0]
    lead = re.sub(r"\s+", " ", lead).strip(" .!~,·")
    return lead in _APPROVAL_TOKENS or bool(_MERGE_LEAD_RE.fullmatch(lead))


def _merge_question_numbers(asked: str, pr: str) -> set[int] | None:
    """Numbers of the items in `asked`'s LAST numbered list that ask to merge
    `pr`; None when `asked` has no numbered list at all.

    A list restarts where a number does not exceed the one before it, so a
    numbered six-item briefing followed by numbered questions yields the
    questions: the ones the reply answers. Fenced blocks are skipped — log
    output there is not a question."""
    text = _strip_fences(asked)
    items, _ = _list_items(text)
    if not items:
        return None
    start = 0
    for k in range(1, len(items)):
        if items[k][0] <= items[k - 1][0]:
            start = k
    only_target = set(_PR_REF_RE.findall(text)) <= {pr}
    return {n for n, body, after in items[start:]
            if _is_item_merge_ask(body, after, pr, only_target)}


def _is_item_merge_ask(body: list[str], after: list[str], pr: str, only_target: bool) -> bool:
    """True when a numbered item plainly asks to merge `pr`.

    The item's LAST ask sentence is read — a `yes` answers the question asked
    last (`Approve merge of #999? Or wait for the freeze?`). With no ask
    sentence, a first line phrased as a request (`_ITEM_ASK_RE`) is one. The
    ask, and what follows it — the rest of its line, the item's later lines
    that are not option bullets, and prose after the item (`after`) — must
    carry no qualifier (`_ITEM_ASK_QUALIFIER_RE`) or negation: `Approve merge
    of #999? Only after the release.` asks for a conditional yes. Option
    bullets under the ask are choices the answer picks between, not
    conditions on it. An ask naming no PR counts only when the turn names no
    PR but `pr`: `1. Approve merge?` in another PR's briefing is that PR's
    question."""
    if any(len(line) > _ITEM_LINE_MAX for line in body + after):
        return False
    # A URL's dots are not sentence ends: `Approve merge of
    # https://github.com/o/r/pull/999?` is one ask.
    lines = [_URL_RE.sub(lambda u: u.group().replace(".", "_"), line) for line in body]
    asks = [(i, m.end(), m.group()) for i, line in enumerate(lines)
            for m in _ASK_SENTENCE_RE.finditer(line)]
    head = (lines[0] if lines else "").strip().rstrip(" .。:：")
    if asks:
        i, end, ask = asks[-1]
        rest = [lines[i][end:], *lines[i + 1:]]
    elif _ITEM_ASK_RE.search(head):
        ask, rest = head + "?", lines[1:]
    else:
        return False
    condition = "\n".join([ask, *(line for line in rest if not _BULLET_RE.match(line)), *after])
    if _ITEM_ASK_QUALIFIER_RE.search(condition) or _LABEL_NEGATION_RE.search(condition):
        return False
    if not _PR_REF_RE.findall(ask) and not only_target:
        return False
    return _is_merge_ask(ask, pr)


def _is_approval_reply(content: object, asked: str | None = None,
                       pr: str | None = None) -> bool:
    """True when a user message is a short bare approval token (ok / 진행 / 승인 …).

    With `asked` (the assistant turn the message answers) and `pr`, a reply
    written as a numbered list is read at the number of the item in that turn
    asking to merge `pr` (issue #1630): `1. approve` / `2. go with your
    recommendation` approves question 1 whichever line comes last. It fails
    when the turn's last numbered list holds no single plain merge ask for
    `pr`; when the reply has no item at that number, has it twice, starts
    with text, or has an unindented line outside its items; or when another
    item mentions the PR or merging without approving — the latest word on
    the merge wins inside one message too. A turn with no numbered list
    leaves the reply to the final-clause rule, as does an unnumbered reply.
    A reference to `pr` in the answer is set aside (`1. #999 머지`), as
    `_is_typed_approval` does for a whole reply. A one-letter reply (`A`)
    is the choice line it picks in that turn, read by
    `_option_label_approves` (issue #1652); with no such line it fails as before.
    """
    raw = _user_message_text(content).strip().lower()
    if asked is None or pr is None:
        return _is_bare_approval(raw)
    # A one-letter reply is read as the choice line it picks (`A: 머지합니다`).
    picked = _picked_option(raw, asked)
    if picked is not None:
        return _option_label_approves(*picked, pr)
    items = _numbered_items(raw)
    if items == []:
        return _is_bare_approval(raw)
    asked_numbers = _merge_question_numbers(asked, pr)
    if asked_numbers is None:
        return _is_bare_approval(raw)
    if items is None or len(asked_numbers) != 1:
        return False
    number = next(iter(asked_numbers))
    answers = [t for n, t in items if n == number]
    # The answer may name the PR it approves; naming another one, `#` or bare
    # (`1. For #1001, approve`, `1. 1001 approve`), is not this merge's approval.
    if len(answers) != 1 or set(_REPLY_PR_RE.findall(answers[0])) - {pr}:
        return False
    # A negation anywhere in an answer about the merge outweighs a trailing
    # token: `1. don't merge #999, ok` holds the merge.
    if any(_LABEL_NEGATION_RE.search(t) for n, t in items
           if n == number or _mentions_pr(t, pr) or _MERGE_WORD_RE.search(t)):
        return False
    if not _is_bare_approval(_strip_pr_ref(answers[0], pr).strip()):
        return False
    return not any((_mentions_pr(t, pr) or _MERGE_WORD_RE.search(t))
                   and not _is_bare_approval(t.strip())
                   for n, t in items if n != number)


def _first_positional(tokens: list[str], value_flags: frozenset[str]) -> str | None:
    """First positional token, skipping option flags and each value-flag's
    following value — so `--subject 833` never yields `833` as a positional."""
    j = 0
    n = len(tokens)
    while j < n:
        tok = tokens[j]
        if tok == "--":
            return tokens[j + 1] if j + 1 < n else None
        if tok.startswith("-"):
            if "=" not in tok and tok in value_flags and j + 1 < n:
                j += 2
            else:
                j += 1
            continue
        return tok
    return None


def _gh_pr_positional(argv: list[str], verbs: frozenset[str],
                      value_flags: frozenset[str]) -> str | None:
    """First positional token of a `gh pr <verb>` segment — the RAW token, which
    may be a PR number, a …/pull/N URL, or a branch name — or None when the
    segment has no positional at all (a bare `gh pr merge --squash`).

    Global flags before the subcommand are walked exactly as `_is_gh_pr_merge`
    does, so a numeric global-flag value cannot be mistaken for the target."""
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
        return None
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in GH_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            i += 1
    if i + 1 >= len(argv) or argv[i] != "pr" or argv[i + 1] not in verbs:
        return None
    return _first_positional(argv[i + 2:], value_flags)


def _segment_repo(argv: list[str]) -> str | None:
    """`owner/repo` a `gh pr` segment names, or None when it names none.

    Reads `-R`/`--repo` in all three forms the sibling gates accept (separate
    token, `--repo=X`, concatenated `-RX`) and a `…/pull/N` URL positional. The
    host is dropped, so `gh -R github.com/o/r` and `gh -R o/r` are one repo.

    Only a real positional is read as a URL: a flag's value can hold any text,
    and a `--subject` quoting another repo's PR link does not retarget the merge.
    """
    argv = strip_prefix(argv)
    skip_next = False
    for i, tok in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        cand = ""
        if "=" in tok:
            name, _, val = tok.partition("=")
            if name in _REPO_FLAGS:
                cand = val
            elif name.startswith("-"):
                continue
        elif tok in _REPO_FLAGS and i + 1 < len(argv):
            cand = argv[i + 1]
        elif tok.startswith("-R") and len(tok) > 2 and not tok.startswith("--"):
            cand = tok[2:]
        elif tok in _SEGMENT_VALUE_FLAGS:
            skip_next = True
            continue
        elif tok.startswith("-"):
            continue
        if cand:
            m = _REPO_SLUG_RE.match(cand.strip("'\""))
            if m:
                return m.group(1).lower()
            continue
        m = _PULL_URL_REPO_RE.search(tok)
        if m:
            return m.group(1).lower()
    return None


def _merge_target_repo(command: object) -> str | None:
    """Repo the single `gh pr merge` segment names, or None (no repo, or not a
    single segment — a compound command is already refused upstream)."""
    if not isinstance(command, str):
        return None
    repos = [_segment_repo(argv)
             for argv in iter_command_starts(safe_tokenize(command))
             if _is_gh_pr_merge(strip_prefix(argv))]
    return repos[0] if len(repos) == 1 else None


def _context_repo_from_window(entries: list[dict], lo: int, hi: int) -> str | None:
    """Repo named by the most recent read-only `gh pr <verb>` in the window, or
    None. Mirrors `_context_pr_from_window`'s last-wins scan so the repo and the
    number come from the same probe: a later probe that names no repo resets it
    to None rather than leaving an earlier probe's repo paired with its number."""
    found: str | None = None
    for ev in entries[lo:hi]:
        msg = ev.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant" or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"
                    and b.get("name") == "Bash"):
                continue
            cmd = (b.get("input") or {}).get("command")
            if not isinstance(cmd, str):
                continue
            for argv in iter_command_starts(safe_tokenize(cmd)):
                if _gh_pr_target(argv, _CONTEXT_VERBS, _CONTEXT_VALUE_FLAGS) is None:
                    continue
                found = _segment_repo(argv)
    return found


def _repo_conflict(merge_repo: str | None, window_repo: str | None) -> bool:
    """True when both sides resolve a repo and they differ — the one case where a
    PR number is provably not this session's PR (issue #1419).

    Deliberately NOT a mismatch test against `None`. Measured over the local
    corpus: of 393 `gh pr merge` calls naming a repo, 377 agree with the window's
    probe and 14 differ; a further 123 name no repo, and 96 of those sit in a
    window that does name one. Treating `None` as a mismatch would break
    correlation for those 96 — the ordinary `gh pr checks 1234` →
    `gh pr merge --repo o/r 1234` shape — to reach the same 14.
    """
    return bool(merge_repo) and bool(window_repo) and merge_repo != window_repo


def _gh_pr_target(argv: list[str], verbs: frozenset[str],
                  value_flags: frozenset[str]) -> str | None:
    """PR *number* the first positional resolves to (bare number or …/pull/N),
    or None when the segment has no positional OR the positional is a non-numeric
    branch name."""
    pos = _gh_pr_positional(argv, verbs, value_flags)
    if pos is None:
        return None
    m = _PULL_TOKEN_RE.match(pos)
    return (m.group(1) or m.group(2)) if m else None


def _merge_segments(command: object) -> list[str | None]:
    """One entry per `gh pr merge` segment in a (possibly compound) command: its
    first positional token (PR number OR branch name), or None when the segment
    has no positional (`gh pr merge --squash`, a current-branch merge)."""
    if not isinstance(command, str):
        return []
    out: list[str | None] = []
    for argv in iter_command_starts(safe_tokenize(command)):
        stripped = strip_prefix(argv)
        if _is_gh_pr_merge(stripped):
            out.append(_gh_pr_positional(stripped, frozenset({"merge"}), _MERGE_VALUE_FLAGS))
    return out


def _mentions_pr(text: str, pr: str) -> bool:
    """True when the text references the target PR (`#N` or the bare number).

    Bounded by ASCII identifier characters, not `\\b`: Hangul is a word
    character, so `#1102를` or `1102번` has no word boundary after the number,
    while `v1102` must still not read as PR 1102.
    """
    return re.search(rf"(?<![A-Za-z0-9_]){pr}(?![A-Za-z0-9_])", text) is not None


def _context_pr_from_window(entries: list[dict], lo: int, hi: int) -> str | None:
    """PR number named by the most recent read-only `gh pr <verb> N` (the
    mandated Pre-Merge probe) in the window's assistant tool_use Bash commands,
    or None when no such command resolves a target."""
    found: str | None = None
    for ev in entries[lo:hi]:
        msg = ev.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant" or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"
                    and b.get("name") == "Bash"):
                continue
            cmd = (b.get("input") or {}).get("command")
            if not isinstance(cmd, str):
                continue
            for argv in iter_command_starts(safe_tokenize(cmd)):
                pr = _gh_pr_target(argv, _CONTEXT_VERBS, _CONTEXT_VALUE_FLAGS)
                if pr is not None:
                    found = pr  # last (most recent) resolved target wins
    return found


# Shell constructs that can skip a textually present segment while the call as a
# whole still exits clean, so a `gh pr merge` under one of them may never have
# run. Whole-token matched, like `_LOOP_KEYWORDS`, so a branch named
# `if-else-fix` is never mistaken for one.
_SKIPPABLE_KEYWORDS = frozenset({"||", "if", "elif", "case"})

# `&&` needs a positional test rather than a flat ban. `A && gh pr merge N`
# skips the merge only when A fails — and when the merge is the LAST command,
# that failure IS the call's exit status, so `is_error` already drops it. Put
# anything after the merge and the status stops being the merge's:
# `false && gh pr merge 833; true` exits 0 having merged nothing, which would
# credit a phantom and over-block the next merge. Requiring the merge to be
# terminal buys the invariant "a credited merge really ran" without a shell
# parser. Measured over the local transcripts, 587 of 834 `gh pr merge` calls
# stay creditable (70%), against 826 (99%) with `&&` unrestricted and 431 (52%)
# with `&&` banned outright.
_AND_TOKEN = "&&"


def _executed_merge_indices(entries: list[dict]) -> list[int]:
    """Entry indices of `gh pr merge` calls that actually RAN, oldest first."""
    return [i for i, _ in _executed_merges(entries)]


def _executed_merges(entries: list[dict], *,
                     include_skippable: bool = False) -> list[tuple[int, str]]:
    """`(entry index, command)` of `gh pr merge` calls that actually RAN, oldest first.

    A merge whose tool_result is `is_error` never executed — the gate itself, a
    sibling gate, or a declined approval stopped it — so counting it would make
    a legitimate retry unmergeable. Only a result that came back clean marks the
    briefing as spent.

    A merge the shell could have jumped over is dropped for the mirror-image
    reason. `true || gh pr merge 833` and `false && gh pr merge 833; true` both
    exit clean having merged nothing, and crediting either would advance the cut
    past a briefing nothing consumed — denying the next marked merge whose
    briefing was real. Both directions therefore fail open: the guard declines to
    fire rather than spend a window it cannot confirm.

    `include_skippable=True` keeps those calls: the bulk release must fail
    closed, so a clean `gh pr merge 1001 && echo done` counts as a merge that
    may have run.
    """
    pending: dict[str, tuple[int, str]] = {}
    executed: list[tuple[int, str]] = []
    for i, ev in enumerate(entries):
        msg = ev.get("message")
        if not isinstance(msg, dict) or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if (b.get("type") == "tool_use" and b.get("name") == "Bash"
                    and msg.get("role") == "assistant"):
                cmd = (b.get("input") or {}).get("command")
                tid = b.get("id")
                if not (isinstance(cmd, str) and isinstance(tid, str)):
                    continue
                tokens = safe_tokenize(cmd)
                if not include_skippable and any(tok in _SKIPPABLE_KEYWORDS for tok in tokens):
                    continue
                starts = list(iter_command_starts(tokens))
                if not any(_is_gh_pr_merge(argv) for argv in starts):
                    continue
                if (not include_skippable and _AND_TOKEN in tokens
                        and not _is_gh_pr_merge(starts[-1])):
                    continue
                pending[tid] = (i, cmd)
            elif b.get("type") == "tool_result":
                tid = b.get("tool_use_id")
                hit = pending.pop(tid, None) if isinstance(tid, str) else None
                if hit is not None and not b.get("is_error"):
                    executed.append(hit)
    return sorted(executed)


def _last_executed_merge(entries: list[dict]) -> int | None:
    """Index of the most recent executed `gh pr merge`, or None."""
    executed = _executed_merge_indices(entries)
    return executed[-1] if executed else None


def _strip_pr_ref(text: str, pr: str) -> str:
    """`text` with each reference to `pr` (`PR #999`, `#999`, `999`) blanked out."""
    return re.sub(rf"(?:(?<![A-Za-z])pr\s*)?#?(?<![A-Za-z0-9_]){pr}(?![A-Za-z0-9_])", " ",
                  text, flags=re.IGNORECASE)


def _ask_label_approves(label: str, pr: str) -> bool:
    """True when a picked label is an approval: the whole label passes
    `_is_approval_reply`, or its leading segment is an approval token once a
    reference to `pr` is removed (`PR #999 머지`, `Merge PR #999`) — and no
    part of it negates the action (`승인 — 머지하지 않기`)."""
    if _LABEL_NEGATION_RE.search(label):
        return False
    if _is_approval_reply(label):
        return True
    lead = _LABEL_LEAD_RE.split(label.strip().lower(), maxsplit=1)[0]
    lead = _strip_pr_ref(lead, pr)
    return re.sub(r"\s+", " ", lead).strip(" .!~,·") in _APPROVAL_TOKENS


def _is_merge_ask(text: str, pr: str) -> bool:
    """True when some sentence of `text` asks to merge `pr`: the sentence names
    it, or names no PR at all ("PR #999 브리핑 … Approve merge?"). A sentence
    that names another PR ("Approve merge #833?") asks about that PR. A briefing
    cites issues and sibling PRs, so an unnamed ask is not discounted for them."""
    for s in _ASK_SENTENCE_RE.findall(text):
        if not _MERGE_WORD_RE.search(s) or _NOT_MERGE_ASK_RE.search(s):
            continue
        objects = {n for m in _MERGE_OBJECT_RE.finditer(s)
                   for n in re.findall(r"\d+", m.group(1) or m.group(2))}
        if pr in objects if objects else set(_PR_REF_RE.findall(s)) <= {pr}:
            return True
    return False


def _ask_answer_approves(content: object, pr: str) -> bool:
    """True when an AskUserQuestion result approves merging a question that names
    `pr` — the question asks to merge, or the picked approval names merging
    itself ("승인 — 그대로 머지"); a bare approval picked for some other ask is
    not this merge's answer."""
    return any(_mentions_pr(q, pr)
               and (_is_merge_ask(q, pr) or _MERGE_WORD_RE.search(a))
               and _ask_label_approves(a, pr)
               for q, a in _ASK_ANSWER_RE.findall(_user_message_text(content)))


def _ask_answer_holds(content: object, pr: str) -> bool:
    """True when an AskUserQuestion result answers a question about `pr` or about
    merging with something that is not an approval (`보류`)."""
    return any((_mentions_pr(q, pr) or _MERGE_WORD_RE.search(q))
               and not _ask_label_approves(a, pr)
               for q, a in _ASK_ANSWER_RE.findall(_user_message_text(content)))


def _ask_question_text(block: dict) -> str:
    """The question texts of an AskUserQuestion tool_use, joined.

    A declined question comes back `is_error` with no `"q"="a"` pair, so the
    only place its subject survives is the call that raised it."""
    inp = block.get("input")
    questions = inp.get("questions") if isinstance(inp, dict) else None
    if not isinstance(questions, list):
        return ""
    return " ".join(q["question"] for q in questions
                    if isinstance(q, dict) and isinstance(q.get("question"), str))


def _ask_held_after(entries: list[dict], index: int, pr: str) -> bool:
    """True when an AskUserQuestion answer after `index` holds or declines this
    merge — a `보류` pick, or a declined question about the PR or about merging.

    The typed-message scan cannot see either: an answer arrives as a tool_result,
    which `_human_user_indices` skips by design (issue #1418 F2). Without this,
    a typed `ok` survived the very question that took it back.
    """
    asks: dict[str, str] = {}
    for i, ev in enumerate(entries):
        msg = ev.get("message")
        if not isinstance(msg, dict) or ev.get("isSidechain"):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                if isinstance(b.get("id"), str):
                    asks[b["id"]] = _ask_question_text(b)
            elif (i > index and b.get("type") == "tool_result"
                  and b.get("tool_use_id") in asks):
                question = asks[b["tool_use_id"]]
                if b.get("is_error"):
                    if _mentions_pr(question, pr) or _MERGE_WORD_RE.search(question):
                        return True
                elif _ask_answer_holds(b.get("content"), pr):
                    return True
    return False


def _merge_target_pr(entries: list[dict], command: object, floor: int) -> str | None:
    """PR number a single `gh pr merge` targets, resolved the way
    `_correlated_prior_turn_text` resolves it; None when it cannot be named.

    A numberless merge reads its probe from `floor` on: a probe before the last
    executed merge named THAT merge's PR, not the current branch's."""
    segments = _merge_segments(command)
    if len(segments) != 1:
        return None
    pos = segments[0]
    if pos is None:
        return _context_pr_from_window(entries, floor, len(entries))
    m = _PULL_TOKEN_RE.match(pos)
    return (m.group(1) or m.group(2)) if m else None


def _is_typed_approval(content: object, pr: str, asked: str | None = None) -> bool:
    reply = _user_message_text(content)
    # A hold or refusal anywhere outweighs a trailing token: `#999 머지 보류. ok`
    # neither approves nor leaves an earlier approval standing.
    if _LABEL_NEGATION_RE.search(reply):
        return False
    if _is_approval_reply(content, asked, pr):
        return True
    # A numbered reply paired with a numbered turn sets its PR references aside
    # per item; blanking them here would also blank a list marker equal to the
    # PR number (`2.` for #2). Paired only then: otherwise the reply keeps the
    # final-clause rule, strip included.
    if (asked is not None and _numbered_items(reply.strip().lower()) != []
            and _merge_question_numbers(asked, pr) is not None):
        return False
    return _mentions_pr(reply, pr) and _is_approval_reply(_strip_pr_ref(reply, pr), asked, pr)


def _held_after(entries: list[dict], idxs: list[int], index: int, pr: str) -> bool:
    """True when a later user message about this PR or merging is not an approval
    ("PR #999 머지 보류"): the latest decision replaces an earlier approval. A
    picked or declined AskUserQuestion answer withdraws it the same way."""
    if _ask_held_after(entries, index, pr):
        return True
    for k, j in enumerate(idxs):
        if j <= index:
            continue
        content = entries[j].get("message", {}).get("content")
        text = _user_message_text(content)
        asked = _assistant_text(entries, idxs[k - 1] + 1 if k else 0, j)
        # A numbered answer to a numbered merge ask is about this merge even
        # when it names neither the PR nor merging (`1. hold`); so is a letter
        # picking a choice line of a turn about the PR (`B` for `B: 보류`),
        # including an unnamed merge re-ask in a turn naming no other PR.
        about = (_mentions_pr(text, pr) or _MERGE_WORD_RE.search(text)
                 or (_numbered_items(text.strip().lower()) != []
                     and bool(_merge_question_numbers(asked, pr)))
                 or (_picked_option(text, asked) is not None
                     and (_mentions_pr(asked, pr)
                          or (set(_PR_REF_RE.findall(asked)) <= {pr}
                              and bool(_MERGE_WORD_RE.search(asked))))))
        if about and not _is_typed_approval(content, pr, asked):
            return True
    return False


def _answered_after(entries: list[dict], idxs: list[int], index: int,
                    pr: str | None) -> bool:
    """True when the user approved after entry `index` — typed or via AskUserQuestion.

    Only approval of THIS merge counts, tied to `pr` — a typed
    approval must name the PR or answer a turn that named it, where either that
    turn asked to merge or the approval names merging itself ("머지 진행"),
    since an "ok" to a status line or to another approval ask agrees to
    nothing; a picked one is held to the same rule against its question. With no resolvable target
    nothing counts. An AskUserQuestion answer arrives as
    a tool_result, which `_human_user_indices` skips by design, so it is matched
    to its question here; a declined question comes back `is_error`.
    """
    if pr is None:
        return False
    for k, i in enumerate(idxs):
        content = entries[i].get("message", {}).get("content")
        if i <= index:
            continue
        reply = _user_message_text(content)
        replied_to = _assistant_text(entries, max(index + 1, idxs[k - 1] + 1 if k else 0), i)
        if not _is_typed_approval(content, pr, replied_to):
            continue
        merge_named = _is_merge_ask(replied_to, pr) or _MERGE_WORD_RE.search(reply)
        if not merge_named:
            picked = _picked_option(reply, replied_to)
            merge_named = picked is not None and bool(_MERGE_WORD_RE.search(picked[0]))
        if ((_mentions_pr(reply, pr) or (merge_named and _mentions_pr(replied_to, pr)))
                and not _held_after(entries, idxs, i, pr)):
            return True
    asks: set[str] = set()
    for i, ev in enumerate(entries):
        msg = ev.get("message")
        if not isinstance(msg, dict) or ev.get("isSidechain"):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                if isinstance(b.get("id"), str):
                    asks.add(b["id"])
            elif (i > index and b.get("type") == "tool_result"
                  and b.get("tool_use_id") in asks and not b.get("is_error")
                  and not _is_injected(ev)
                  and _ask_answer_approves(b.get("content"), pr)
                  and not _held_after(entries, idxs, i, pr)):
                return True
    return False


def _has_repetition(command: object) -> bool:
    """True when the command wraps the merge in a shell loop / xargs — a single
    approval must not authorize a repeated merge (No Approval Transfer)."""
    if not isinstance(command, str):
        return False
    return any(tok in _LOOP_KEYWORDS for tok in safe_tokenize(command))


def _last_reply_approves(entries: list[dict], idxs: list[int], command: object,
                         floor: int = 0) -> bool:
    """True when the last user message approves, read against the turn it answers.

    That turn is the one `_correlated_prior_turn_text` scores, and the merge
    target is resolved the way it resolves it, so a numbered reply is paired
    with the numbered merge ask the user actually saw (issue #1630)."""
    prev_lo = max(idxs[-2] + 1 if len(idxs) >= 2 else 0, floor)
    asked = _assistant_text(entries, prev_lo, idxs[-1])
    return _is_approval_reply(entries[idxs[-1]].get("message", {}).get("content"),
                              asked, _merge_target_pr(entries, command, prev_lo))


def _correlated_prior_turn_text(entries: list[dict], idxs: list[int],
                                command: object, floor: int = 0) -> str | None:
    """The prior-turn assistant text the gate may score, else None (issue #826).

    The mandated flow is briefing → user approval → merge, which places the
    briefing in the turn BEFORE the approving user message — outside the
    since-last-user window. When the last user message is a short approval reply,
    the immediately preceding assistant turn is scored ALONE (so text authored
    AFTER the approval cannot supplement a briefing the user never saw), and only
    when the merge is correlated to the briefed PR.

    Returning the text rather than a verdict keeps ONE definition of "which
    window may be scored": the caller decides which threshold applies to it,
    so the marker's lower floor sees the same window the full-briefing check
    does instead of scoring an empty current turn (issue #940 review).

    `floor` is the serial-merge cut (issue #1214): text at or before an already
    executed merge belongs to THAT merge, so the prior turn is read from `floor`
    onward. A briefing authored after the earlier merge still counts.
    """
    segments = _merge_segments(command)
    # A single approval authorizes ONE merge. A compound `merge A && merge B` (≥2
    # segments) or a shell loop repeating one segment (`for pr in …; do merge`)
    # must not ride a single approval (No Approval Transfer) → no extension.
    if len(segments) != 1 or _has_repetition(command) or not idxs:
        return None
    if not _last_reply_approves(entries, idxs, command, floor):
        return None

    # On a long turn the briefing turn's opening message scrolls past the bounded
    # tail. Every entry before the only visible human message still belongs to
    # that one turn, so the tail head is its lower bound (issue #1410).
    prev_start = idxs[-2] + 1 if len(idxs) >= 2 else 0
    prev_lo = max(prev_start, floor)
    if prev_lo >= idxs[-1]:
        return None
    # A briefing about another repo's #999 is not this merge's briefing, however
    # well the number matches (issue #1419). Only a resolvable disagreement
    # counts — see `_repo_conflict` for why `None` is not a mismatch.
    if _repo_conflict(_merge_target_repo(command),
                      _context_repo_from_window(entries, prev_lo, len(entries))):
        return None
    prev_text = _assistant_text(entries, prev_lo, idxs[-1])
    pos = segments[0]
    if pos is None:
        # Truly numberless (`gh pr merge --squash`) → runs on the CURRENT branch.
        # Derive the real target from the window's mandated Pre-Merge probe
        # (`gh pr checks/view N`) and correlate against THAT. Fail closed when no
        # probe resolves a target (No Approval Transfer, P1#2).
        ctx_pr = _context_pr_from_window(entries, prev_lo, len(entries))
        correlated = ctx_pr is not None and _mentions_pr(prev_text, ctx_pr)
    else:
        m = _PULL_TOKEN_RE.match(pos)
        if m:
            correlated = _mentions_pr(prev_text, m.group(1) or m.group(2))
        else:
            # A NAMED branch (`gh pr merge feature-x`) targets that branch's PR —
            # which cannot be mapped to a number without a live `gh` call, and is
            # NOT the current branch, so the window probe does not identify it.
            # Fail closed (round-3 P1b): a prior probe of a different PR must not
            # be treated as this branch's target.
            correlated = False

    # An uncorrelated prior turn is not this merge's window at all: returning
    # None keeps the trivial-PR carve-out, the briefing count, and the marker
    # floor from ever reading a briefing about a different PR (P1#1).
    if not correlated:
        return None
    return prev_text


def _last_spoken_turn(entries: list[dict], idxs: list[int]) -> tuple[int, int] | None:
    """Bounds of the latest turn before the last human message with assistant text.

    Turns with no assistant text are skipped: an interrupt marker arrives as a
    human message, so the turn between it and the next reply is empty and the
    briefing the user actually read sits one turn further back.
    """
    for k in range(len(idxs) - 1, -1, -1):
        lo = idxs[k - 1] + 1 if k >= 1 else 0
        if _assistant_text(entries, lo, idxs[k]).strip():
            return lo, idxs[k]
    return None


def _excerpt(entries: list[dict], index: int) -> str:
    text = _surface.sanitize(_user_message_text(entries[index].get("message", {}).get("content")))
    text = re.sub(r"\s+", " ", text).strip().replace("`", "'")
    return text if len(text) <= 30 else text[:30] + "…"


def _excluded_briefing_reason(entries: list[dict], idxs: list[int], command: object,
                              threshold: int, floor: int = 0) -> str | None:
    """Why a briefing the user was shown fell outside the scored window, else None.

    Message-only (issue #1433): every check below is one the decision already
    made, asked again in the same order so the named reason is the one that
    actually excluded the briefing. None when no such briefing exists, so a merge
    that was never briefed keeps its message unchanged.
    """
    if not idxs:
        return None
    spoken = _last_spoken_turn(entries, idxs)
    if spoken is None:
        return None
    lo, hi = spoken
    if _briefing_item_count(_assistant_text(entries, lo, hi)) < threshold:
        return None
    if len(_merge_segments(command)) != 1 or _has_repetition(command):
        return "one approval releases one merge, and this command runs more than one"
    if not _last_reply_approves(entries, idxs, command, floor):
        return (f"the last user message (`{_excerpt(entries, idxs[-1])}`) is not an "
                "approval reply, so no earlier turn was scored")
    if len(idxs) >= 2 and lo < idxs[-2] + 1:
        return (f"a user message (`{_excerpt(entries, hi)}`) came between the briefing "
                "and the approval, and only the turn right before the approval is scored")
    if _correlated_prior_turn_text(entries, idxs, command) is None:
        return "the briefed turn does not name the PR this merge targets"
    if floor > lo:
        return ("it precedes a merge that already ran in this session, and one "
                "briefing releases one merge")
    return None


def _with_exclusion(why: str, reason: str | None) -> str:
    if reason is None:
        return why
    return f"{why}. A briefing was shown earlier but not scored: {reason}"


# ---------------------------------------------------------------------------
# Bulk approval: one briefing turn, one answer, several merges.
# ---------------------------------------------------------------------------

# Words that make one approval cover a listed set ("모두 머지", "approve both").
# They are dropped before the remainder is matched against the approval tokens.
_BULK_QUANTIFIERS = frozenset({
    "모두", "전부", "다", "둘다", "셋다", "넷다", "다섯다", "둘", "셋", "넷", "다섯",
    "all", "both", "each", "every", "everything", "them", "of",
})
# Polite or sentence-final approvals the bare-token set does not carry; read
# only on the bulk path, where the reply must also name or answer a listed set.
_BULK_EXTRA_TOKENS = frozenset({
    "머지하세요", "머지해주세요", "머지 해주세요", "머지 해줘", "머지합시다",
    "승인합니다", "진행하세요", "진행해주세요", "merge them", "merge all",
})
# Single words that each approve, so "승인 — 모두 머지" reads as approve + merge.
_BULK_APPROVAL_WORDS = frozenset(
    t for t in _APPROVAL_TOKENS | _BULK_EXTRA_TOKENS if " " not in t)
# Particles and list joiners left behind once the PR references are removed.
_BULK_FILLERS = frozenset({"만", "은", "는", "을", "를", "도", "과", "와", "및", "and", "&", "pr"})
# Sentence boundaries of a reply; commas stay inside a sentence because they
# join a PR list ("#833, #999 머지").
_SENTENCE_TAIL_RE = re.compile(r"[.!?。…\n;·]+")
# In a short approval reply a bare number is a PR: users type "98, 1118 머지".
_REPLY_PR_RE = re.compile(r"(?:#|/pull/|(?<![A-Za-z])pr\s*(?:#\s*)?)?(?<![A-Za-z0-9_])(\d+)(?![A-Za-z0-9_])",
                          re.IGNORECASE)
# A heading line: a markdown heading, or a line that is bold from end to end.
# A bold lead inside a bullet (`- **리스크**: …`) is an item, not a heading.
_HEADING_RE = re.compile(r"^\s{0,3}(?:(?P<hashes>#{1,6})\s+\S|\*\*[^*\n]+\*\*\s*:?\s*$)")
_FENCE_LINE_RE = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})(?P<rest>.*)$")
# A whole-line bold heading ranks below every markdown heading.
_BOLD_HEADING_LEVEL = 7
# A reply longer than this is not a bare approval; capping it also bounds the
# regex work a pasted wall of text could cost.
_BULK_REPLY_MAX_CHARS = 300
# Commands that move the working directory for every later Bash call.
_CHDIR_COMMANDS = frozenset({"cd", "pushd", "popd"})
# A listed merge ask: the PR list is the object of the merge verb, and the
# verb is the sentence's predicate — the ask ending follows it directly
# ("#833, #999 를 머지할까요?", "Approve merging #833 and #999?"). A list
# before "머지 전에 … 돌릴까요?" or "머지 큐에 올려둘까요?" asks something else.
_LISTED_ASK_RE = re.compile(
    rf"(?P<ko>{_REF_LIST}){_REF_COUNT}\s*(?:을|를|은|는|도)?\s*(?:모두\s*|전부\s*|다\s*)?(?:{_MERGE_METHOD})?(?:머지|병합)\s*"
    r"(?:할까요|해도\s*될까요|해도\s*되나요|진행할까요|하시겠(?:어요|습니까)?|해\s*드릴까요|해드릴까요)\s*\??\s*$"
    rf"|^\s*(?:(?:shall|should|can|may)\s+i\s+|approve\s+|ok\s+to\s+)?(?:(?:squash|rebase)[\s-]+)?merg(?:e|ing)\s*:?\s*(?:pr\s*)?"
    rf"(?P<en>{_REF_LIST})\s*\?\s*$",
    re.IGNORECASE)
# Env assignments that move a `gh` call to another repo without a flag.
_REPO_ENV_RE = re.compile(r"^(?:GH_REPO|GH_HOST)=")
_COUNT_PHRASE_RE = re.compile(r"(두|세|네|다섯|\d+)\s*(?:건|개)")
_COUNT_WORDS = {"두": 2, "세": 3, "네": 4, "다섯": 5}


def _bulk_reply_prs(text: str) -> set[str] | None:
    """PRs a bulk approval names (empty when it names none), or None when the
    text is not an approval at all.

    Conservative on purpose — a miss costs one ask, a false read releases a
    merge. Every sentence must be PR references plus approval words only, so
    a deferral ("두 번째 건 내일 다시 보자. 모두 머지"), a precondition ("CI
    한번 더 돌려보고. ok"), or a condition after a comma voids the reply, as
    do a refusal word anywhere and a trailing `?`.
    """
    raw = text.strip()
    if (not raw or len(raw) > _BULK_REPLY_MAX_CHARS or raw.endswith(("?", "？"))
            or _LABEL_NEGATION_RE.search(raw)):
        return None
    named: set[str] = set()
    # "98, 1118 머지" names PRs by bare number; once any `#N`/`PR N` appears,
    # a bare number is something else ("1 — #833, #999 머지" is an ordinal),
    # so it stays in the text and voids the reply instead of naming PR 1.
    ref_re = _PR_REF_RE if _PR_REF_RE.search(raw) else _REPLY_PR_RE
    approved = False
    counts: list[int] = []
    for sentence in _SENTENCE_TAIL_RE.split(raw):
        if not sentence.strip():
            continue
        flat = re.sub(r"\s*[—–:]\s*", " ", sentence.lower())
        refs = set(ref_re.findall(flat))
        named |= refs
        # A count phrase restates the list ("#1 · #2 두 건 모두 머지"), as the
        # listed ask allows (`_REF_COUNT`); it approves nothing on its own.
        rest = ref_re.sub(" ", flat)
        counts += [_COUNT_WORDS.get(m.group(1)) or int(m.group(1))
                   for m in _COUNT_PHRASE_RE.finditer(rest)]
        rest = _COUNT_PHRASE_RE.sub(" ", rest)
        words = [w for w in re.split(r"[\s,()]+", rest)
                 if w and w not in _BULK_FILLERS and w not in _BULK_QUANTIFIERS]
        remainder = " ".join(words).strip(" .!~,·")
        # `·` lists PRs but also splits clauses, so "#1 · #2 · #3 머지" yields a
        # piece that is only a reference; it names a PR and approves nothing.
        if refs and not remainder:
            continue
        if not (remainder in _APPROVAL_TOKENS or remainder in _BULK_EXTRA_TOKENS
                or (words and all(w in _BULK_APPROVAL_WORDS for w in words))):
            return None
        approved = True
    # A count must restate the PRs the reply names: "두 건만 머지" against a
    # three-PR ask names none and picks a subset, and "두 건 모두" over three
    # references disagrees with itself — either way the reply is not read.
    if any(n != len(named) for n in counts):
        return None
    return named if approved else None


def _listed_merge_ask(text: str) -> tuple[set[str], str]:
    """(PRs, sentence) of the LAST listed merge ask in `text` naming two or
    more PRs, else (empty set, "")."""
    listed: tuple[set[str], str] = (set(), "")
    for s in _ASK_SENTENCE_RE.findall(text):
        if _NOT_MERGE_ASK_RE.search(s):
            continue
        m = _LISTED_ASK_RE.search(s.strip())
        if m is None:
            continue
        objects = set(re.findall(r"\d+", m.group("ko") or m.group("en")))
        if len(objects) >= 2:
            listed = (objects, s)
    return listed


def _pr_sections(text: str, prs: set[str]) -> dict[str, str]:
    """Per-PR briefing sections.

    The first heading that names exactly one PR of `prs` fixes the section
    level. A section runs from such a heading to the next heading at that
    level or above; a deeper heading (`#### #999 와의 관계`) is content of the
    section it sits in, and so is a `#` comment inside a code fence. A heading
    naming two PRs belongs to neither, a shared block after the last section
    is nobody's, and only a PR's first section counts — a second heading
    naming it is not a second briefing.
    """
    sections: dict[str, str] = {}
    seen: set[str] = set()
    level: int | None = None
    owner: str | None = None
    fence: str | None = None
    for line in text.splitlines():
        f = _FENCE_LINE_RE.match(line)
        if f is not None:
            run = f.group("fence")
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not f.group("rest").strip():
                # CommonMark: only a bare run of the opener's character, at
                # least as long, closes the block; "```bash" inside it does not.
                fence = None
        m = None if fence is not None else _HEADING_RE.match(line)
        if m is not None:
            this = len(m.group("hashes")) if m.group("hashes") else _BOLD_HEADING_LEVEL
            # Every PR the heading names counts, not only the listed ones:
            # "### PR #833 / PR #1001" is a joint heading, nobody's section.
            named = set(_PR_REF_RE.findall(line))
            refs = named if len(named) == 1 and named <= prs else set()
            if level is None and refs:
                level = this
            if level is not None and this <= level:
                pr = refs.pop() if refs else None
                owner = pr if pr is not None and pr not in seen else None
                if owner is not None:
                    seen.add(owner)
        if owner is not None:
            sections[owner] = sections.get(owner, "") + line + "\n"
    return sections


# A PR named with its repo: `owner/repo#N`, or a …/owner/repo/pull/N URL.
_SECTION_SLUG_REF_RE = re.compile(
    r"(?<![A-Za-z0-9._/-])([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)#(\d+)(?![A-Za-z0-9_])")
_SECTION_URL_REF_RE = re.compile(r"/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/(\d+)(?![A-Za-z0-9_])")


def _section_repo(section: str, pr: str) -> str | None:
    """The one repo a briefing section's heading names `pr` with, else None.

    Only the heading line counts: the body may cite a related PR with the same
    number in another repo (`the upstream fix is c/d#999`). Two repos for the
    number name no single target, so the section decides nothing and the
    window probe stays the reference. Lowercased like `_segment_repo`, since
    GitHub treats owner and repo names case-insensitively."""
    heading = section.split("\n", 1)[0]
    repos = {m.group(1).lower() for rx in (_SECTION_SLUG_REF_RE, _SECTION_URL_REF_RE)
             for m in rx.finditer(heading) if m.group(2) == pr}
    return repos.pop() if len(repos) == 1 else None


def _merge_pr_number(command: str) -> str | None:
    """Explicit PR number of a command that is one merge and nothing else.

    Anything chained before or after it (`cd other && …`, `git fetch; …`) or an
    env prefix that retargets the repo (`GH_REPO=x/y …`) is refused: either can
    move the merge somewhere the briefing never looked."""
    tokens = safe_tokenize(command)
    starts = list(iter_command_starts(tokens))
    if len(starts) != 1 or _has_repetition(command):
        return None
    if any(_REPO_ENV_RE.match(tok) for tok in starts[0]):
        return None
    segments = _merge_segments(command)
    if len(segments) != 1 or segments[0] is None:
        return None
    m = _PULL_TOKEN_RE.match(segments[0])
    return (m.group(1) or m.group(2)) if m else None


def _latest_ask_answer(entries: list[dict], after: int) -> tuple[int, list[tuple[str, str]]] | None:
    """(index, every question/label pair) of the last answered AskUserQuestion
    result after entry `after`, or None. A declined question carries no pair.

    Only a result whose `tool_use_id` belongs to an AskUserQuestion call counts:
    a Bash or Read output that happens to print `"q"="a"` is not the user."""
    found: tuple[int, list[tuple[str, str]]] | None = None
    asks: set[str] = set()
    for i, ev in enumerate(entries):
        msg = ev.get("message")
        if not isinstance(msg, dict) or ev.get("isSidechain"):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                if isinstance(b.get("id"), str):
                    asks.add(b["id"])
                continue
            if (i > after and b.get("type") == "tool_result"
                    and b.get("tool_use_id") in asks and not b.get("is_error")):
                pairs = _ASK_ANSWER_RE.findall(_user_message_text(b.get("content")))
                if pairs:
                    found = (i, pairs)
    return found


def _ask_decision(pairs: list[tuple[str, str]]) -> tuple[set[str], set[str], str] | None:
    """(named, listed, ask sentence) from one AskUserQuestion result, else None.

    Every pair is read: a refusal picked on any question voids the result. The
    deciding pair is the one whose question is a listed merge ask, or whose
    label names the PRs; a 승인 picked for an unrelated question approves no
    merge, whatever the prose around it asked."""
    if any(_LABEL_NEGATION_RE.search(a) for _, a in pairs):
        return None
    for question, label in pairs:
        named = _bulk_reply_prs(label)
        if named is None:
            continue
        listed, ask_text = _listed_merge_ask(question)
        if listed or len(named) >= 2:
            return named, listed, ask_text
    return None


def _changed_directory_after(entries: list[dict], index: int) -> bool:
    """True when a Bash call after entry `index` changed directory or exported
    GH_REPO/GH_HOST — state that outlives the call and retargets a later merge."""
    for ev in entries[index + 1:]:
        msg = ev.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant" or ev.get("isSidechain"):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"
                    and b.get("name") == "Bash"):
                continue
            cmd = (b.get("input") or {}).get("command")
            if not isinstance(cmd, str):
                continue
            for argv in iter_command_starts(safe_tokenize(cmd)):
                if not argv:
                    continue
                if argv[0] in _CHDIR_COMMANDS:
                    return True
                if argv[0] == "export" and any(_REPO_ENV_RE.match(t) for t in argv[1:]):
                    return True
    return False


def _bulk_released(entries: list[dict], idxs: list[int], command: object) -> bool:
    """True when this merge is one of a set the user approved in one answer.

    The flow: one assistant turn briefs every PR in its own section (a heading
    naming that PR, 4 of 6 items inside it) and asks once, listing the PRs; the
    user's next answer approves the set — naming the PRs (`#833 #999 머지`) or
    answering the listed ask (`모두 머지`, `ok`). Each merge then has to be one
    of that set, run as a command that is that merge alone, and the set is spent
    in order: any merge since the answer that targets a PR outside the set, or
    repeats one, ends it. Numberless merges never ride this path.
    """
    if not isinstance(command, str) or not idxs:
        return False
    pr = _merge_pr_number(command)
    if pr is None:
        return False
    last_human = idxs[-1]
    ask = _latest_ask_answer(entries, last_human)
    if ask is not None:
        answer_idx, pairs = ask
        decision = _ask_decision(pairs)
        if decision is None:
            return False
        named, listed, ask_text = decision
        brief_lo, brief_hi = last_human + 1, answer_idx
    else:
        answer_idx = last_human
        reply = _bulk_reply_prs(_user_message_text(
            entries[last_human].get("message", {}).get("content")))
        if reply is None:
            return False
        named = reply
        brief_lo = idxs[-2] + 1 if len(idxs) >= 2 else 0
        brief_hi = last_human
        briefing_text = _assistant_text(entries, brief_lo, brief_hi)
        listed, ask_text = _listed_merge_ask(briefing_text)
        # A bare "ok" answers the turn's LAST question; a later "Or just #833?"
        # or "그 전에 리베이스해 둘까요?" is what it may be answering.
        asks = _ASK_SENTENCE_RE.findall(briefing_text)
        if not named and (not asks or asks[-1] != ask_text):
            return False
    approved = named or listed
    if pr not in approved or len(approved) < 2:
        return False
    briefing = _assistant_text(entries, brief_lo, brief_hi, pr)
    section = _pr_sections(briefing, approved).get(pr)
    if section is None:
        return False
    # The one shared ask is every section's approve-ask item.
    if _briefing_item_count(section + "\n" + ask_text) < MERGE_BRIEFING_MIN_ITEMS:
        return False
    merge_repo = _merge_target_repo(command)
    # A section that names its PR with the repo (`owner/repo#N`, a …/pull/N
    # URL) says which repo the user approved for that PR. It outranks the
    # window's last-wins probe, which in a bulk set across repos names
    # whichever PR was probed last.
    briefed_repo = (_section_repo(section, pr)
                    or _context_repo_from_window(entries, brief_lo, len(entries)))
    if _repo_conflict(merge_repo, briefed_repo):
        return False
    # After a `cd` the shell may sit in another checkout, where a bare number
    # names a different PR; only a merge naming the briefed repo is safe then.
    # The window opens at the briefing, not the answer: a cd between the repo
    # probe and the answer moves the shell just the same.
    if _changed_directory_after(entries, brief_lo - 1) and (
            merge_repo is None or merge_repo != briefed_repo):
        return False
    spent: set[str] = set()
    for i, cmd in _executed_merges(entries, include_skippable=True):
        if i <= answer_idx:
            continue
        # A chained or conditional merge has no single PR number: it ends the set.
        done = _merge_pr_number(cmd)
        if done is None or done not in approved or done in spent:
            return False
        spent.add(done)
    if pr in spent:
        return False
    return not _ask_held_after(entries, answer_idx, pr)


def bulk_merge_approved(payload: dict) -> bool:
    """Public entry for `pre-merge-approval-gate`: True when the merge in
    `payload` is released by a bulk approval. Fails closed (False) on any
    unreadable input, so the sibling gate keeps asking."""
    try:
        command = (payload.get("tool_input") or {}).get("command")
        if not isinstance(command, str):
            return False
        code = _split_shell_comment(command)[0]
        entries = _load_turn_entries(payload.get("transcript_path", "") or "")
        if entries is None:
            return False
        return _bulk_released(entries, _human_user_indices(entries), code)
    except Exception:
        return False


# `gh` subcommands that act on one repository; a call to one of them that
# names no repo acts on whatever the shell's checkout is.
_REPO_SCOPED_GH = frozenset({
    "pr", "issue", "run", "workflow", "release", "label", "cache", "secret",
    "variable", "browse", "repo", "api",
})
_API_REPO_RE = re.compile(r"^/?repos/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)(?:/|$)")
_ISSUE_URL_RE = re.compile(r"^https?://[^/\s]+/[^/\s]+/[^/\s]+/(?:pull|issues)/\d+")


def _gh_call_repos(argv: list[str]) -> set[tuple[str, str]] | None:
    """`(host, owner/name)` of every repo one `gh` call names: `-R`/`--repo`
    values, pull or issue URLs, a `gh repo <verb> o/r` slug and a
    `gh api repos/o/r/…` endpoint. An empty set when the call is repo-scoped
    and names none, None when it is not repo-scoped (`gh auth status`) or
    names something that is not a repo."""
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
        return None
    words: list[str] = []
    found: set[tuple[str, str]] = set()
    host = _GITHUB_HOST
    skip_next = False
    for i, tok in enumerate(argv[1:], start=1):
        if skip_next:
            skip_next = False
            continue
        value = None
        if "=" in tok and tok.partition("=")[0] in _REPO_FLAGS:
            value = tok.partition("=")[2]
        elif tok in _REPO_FLAGS and i + 1 < len(argv):
            value, skip_next = argv[i + 1], True
        elif tok.startswith("-R") and len(tok) > 2 and not tok.startswith("--"):
            value = tok[2:]
        elif tok == "--hostname" and i + 1 < len(argv):
            host, skip_next = argv[i + 1].lower(), True
        elif tok in _SEGMENT_VALUE_FLAGS or tok in GH_GLOBAL_FLAGS_WITH_ARG:
            skip_next = True
        elif _ISSUE_URL_RE.match(tok.strip("'\"")):
            url = tok.strip("'\"").replace("/issues/", "/pull/", 1)
            hit = _pull_url_repo(url)
            if hit is None:
                return None
            found.add(hit)
        elif not tok.startswith("-"):
            words.append(tok)
        if value is not None:
            hit = _repo_spec(value)
            if hit is None:
                return None
            found.add(hit)
    if words[:1] == ["repo"] and len(words) >= 3 and "/" in words[2]:
        hit = _repo_spec(words[2])
        if hit is None:
            return None
        found.add(hit)
    if words[:1] == ["api"] and len(words) >= 2:
        m = _API_REPO_RE.match(words[1])
        if m:
            found.add((host, f"{m.group(1).lower()}/{m.group(2).lower().removesuffix('.git')}"))
    if found or (words[:1] and words[0] in _REPO_SCOPED_GH):
        return found
    return None


def _window_names_only(entries: list[dict], lo: int, repo: tuple[str, str]) -> bool:
    """True when every repo-scoped `gh` call from entry `lo` on names `repo` and
    nothing else, and at least one does. A call naming no repo, or another
    repo, may be what the user's answer was about, so it keeps the prompt."""
    seen = False
    for ev in entries[lo:]:
        msg = ev.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant" or ev.get("isSidechain"):
            continue
        content = msg.get("content")
        for b in content if isinstance(content, list) else []:
            if not (isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash"):
                continue
            cmd = (b.get("input") or {}).get("command")
            if not isinstance(cmd, str):
                continue
            for argv in iter_command_starts(safe_tokenize(cmd)):
                repos = _gh_call_repos(argv)
                if repos is None:
                    continue
                if repos != {repo}:
                    return False
                seen = True
    return seen


def _chat_released(entries: list[dict], idxs: list[int], command: object) -> bool:
    """True when the user approved this one merge in chat since the last merge.

    The approval is the one `_answered_after` reads for the briefing check: a
    typed reply to a turn that asked to merge the PR (`merge`, `머지 승인`), the
    user's own message naming the PR and merging (`#999 머지해`), or an
    AskUserQuestion pick. The answer names a number, never a repo, so the
    repo is bound by the window instead: the merge names exactly one repo,
    every repo-scoped `gh` call since the last merge that may have run names
    that same repo (host included), and no `GH_HOST` was exported. The command
    must be one numbered merge and nothing else.
    """
    if not isinstance(command, str) or not idxs:
        return False
    pr = _merge_pr_number(command)
    repos = _merge_repos(command)
    if pr is None or not repos or len(repos) != 1 or _exported_gh_host(entries):
        return False
    executed = _executed_merges(entries, include_skippable=True)
    last_merge = executed[-1][0] if executed else -1
    if not _window_names_only(entries, last_merge + 1, next(iter(repos))):
        return False
    return _answered_after(entries, idxs, last_merge, pr)


def chat_merge_approved(payload: dict) -> bool:
    """Public entry for the approval check: True when the user already
    approved the merge in `payload` in chat, so the host prompt would ask a
    second time. Fails closed (False) on any unreadable input."""
    try:
        command = (payload.get("tool_input") or {}).get("command")
        if not isinstance(command, str):
            return False
        code = _split_shell_comment(command)[0]
        entries = _load_turn_entries(payload.get("transcript_path", "") or "")
        if entries is None:
            return False
        return _chat_released(entries, _human_user_indices(entries), code)
    except Exception:
        return False


def _call_failed(entries: list[dict], tool_use_id: str) -> bool:
    """True when the transcript holds a failed tool_result for `tool_use_id`."""
    for ev in entries:
        msg = ev.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list) or ev.get("isSidechain"):
            continue
        for b in content:
            if (isinstance(b, dict) and b.get("type") == "tool_result"
                    and b.get("tool_use_id") == tool_use_id and b.get("is_error")):
                return True
    return False


_GITHUB_HOST = "github.com"


def _repo_spec(spec: str) -> tuple[str, str] | None:
    """`(host, owner/name)` of an `-R` value (`o/r` or `host/o/r`), lowercased,
    or None when it is neither."""
    parts = spec.strip("'\"").split("/")
    if len(parts) == 2:
        parts = [_GITHUB_HOST, *parts]
    if len(parts) != 3 or not all(re.fullmatch(r"[A-Za-z0-9._-]+", p) for p in parts):
        return None
    host, owner, name = (p.lower() for p in parts)
    return host, f"{owner}/{name.removesuffix('.git')}"


def _pull_url_repo(url: str) -> tuple[str, str] | None:
    """`(host, owner/name)` of a pull request URL, read the way `gh` reads one
    (host, then the first two path segments), or None when it is not one."""
    parsed = urlparse(url.strip("'\""))
    segs = [s for s in parsed.path.split("/") if s]
    if parsed.scheme not in ("http", "https") or not parsed.hostname or len(segs) < 4 \
            or segs[2] != "pull":
        return None
    return parsed.hostname.lower(), f"{segs[0].lower()}/{segs[1].lower()}"


def _merge_repos(code: str) -> set[tuple[str, str]] | None:
    """Every `(host, owner/name)` the single merge segment names: each `-R` /
    `--repo` value (gh keeps the last one) and a pull request URL positional
    (gh prefers it to `-R`). None when one of them is not a repo."""
    found: set[tuple[str, str]] = set()
    for argv in iter_command_starts(safe_tokenize(code)):
        argv = strip_prefix(argv)
        if not _is_gh_pr_merge(argv):
            continue
        positional = _gh_pr_positional(argv, frozenset({"merge"}), _MERGE_VALUE_FLAGS)
        if positional and "/" in positional:
            hit = _pull_url_repo(positional)
            if hit is None:
                return None
            found.add(hit)
        skip_next = False
        for i, tok in enumerate(argv):
            if skip_next:
                skip_next = False
                continue
            value = None
            if "=" in tok and tok.partition("=")[0] in _REPO_FLAGS:
                value = tok.partition("=")[2]
            elif tok in _REPO_FLAGS and i + 1 < len(argv):
                value = argv[i + 1]
                skip_next = True
            elif tok.startswith("-R") and len(tok) > 2 and not tok.startswith("--"):
                value = tok[2:]
            elif tok in _SEGMENT_VALUE_FLAGS:
                skip_next = True
            if value is not None:
                hit = _repo_spec(value)
                if hit is None:
                    return None
                found.add(hit)
    return found


def _exported_gh_host(entries: list[dict]) -> bool:
    """True when an earlier Bash call exported GH_HOST, which retargets an
    `-R owner/name` merge to another host for the rest of the shell."""
    for ev in entries:
        msg = ev.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant" or ev.get("isSidechain"):
            continue
        content = msg.get("content")
        for b in content if isinstance(content, list) else []:
            if not (isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash"):
                continue
            cmd = (b.get("input") or {}).get("command")
            if not isinstance(cmd, str):
                continue
            for argv in iter_command_starts(safe_tokenize(cmd)):
                if argv and argv[0] == "export" and any(t.startswith("GH_HOST=") for t in argv[1:]):
                    return True
    return False


def _provider_target(code: str, entries: list[dict]) -> tuple[str | None, str | None, str | None]:
    """`(repo, pr, refusal)` for the provider: one merge, one PR number, and
    exactly one repo on github.com that every repo reference in the command
    agrees on, so the repo the provider vouches for is the one `gh` merges."""
    pr = _merge_pr_number(code) if code else None
    if pr is None:
        return None, None, "the command is not one merge naming its PR number"
    repos = _merge_repos(code)
    if repos is None:
        return None, None, "the merge names a repo or URL that cannot be read"
    if not repos:
        return None, None, "the merge does not name its repo (--repo owner/name)"
    if len(repos) > 1:
        return None, None, "the merge names more than one repo"
    host, repo = next(iter(repos))
    if host != _GITHUB_HOST:
        return None, None, f"the merge targets host {host}, not {_GITHUB_HOST}"
    if _exported_gh_host(entries):
        return None, None, "GH_HOST was exported earlier in this session"
    return repo, pr, None


def host_merge_decision(payload: dict) -> _host_approval.Decision:
    """Public entry for both merge gates: the host approval provider's verdict
    on the merge in `payload` (see `_lib/_host_merge_approval.py`).

    Only a single merge naming its PR number and exactly one repo (`--repo`,
    `-R`, or a pull request URL, all agreeing) is sent to the provider. A merge
    with no repo would run against whatever checkout the shell sits in, which
    a `cd` or the host's working directory can change between the approval
    and the call; one naming two repos runs against the one `gh` prefers (the
    URL, else the last `-R`). Never raises: any error is a refusal, so neither
    gate releases on a crash.
    """
    try:
        command = (payload.get("tool_input") or {}).get("command")
        code = _split_shell_comment(command)[0] if isinstance(command, str) else ""
        entries = _load_turn_entries(payload.get("transcript_path", "") or "") or []
        repo, pr, refusal = _provider_target(code, entries)
        return _host_approval.decide(
            session_id=str(payload.get("session_id") or ""),
            tool_use_id=str(payload.get("tool_use_id") or ""),
            repo=repo, pr=pr, refusal=refusal,
            count_items=_briefing_item_count, min_items=MERGE_BRIEFING_MIN_ITEMS,
            call_failed=lambda tid: _call_failed(entries, tid))
    except Exception as exc:  # noqa: BLE001 - a crash must refuse, not release
        return _host_approval.Decision(False, f"the provider check failed ({type(exc).__name__})")


def _merge_escalation_reason(payload: dict) -> str | None:
    """Return a deny reason when the merge is not approved, else None.

    The transcript decides first; a host approval provider, when the host
    configured one, is asked only about a merge the transcript would stop.
    """
    reason = _transcript_merge_reason(payload)
    if reason is None or not _host_approval.configured():
        return reason
    decision = host_merge_decision(payload)
    if decision.released:
        return None
    return f"{reason}\n{decision.note()}"


def _transcript_merge_reason(payload: dict) -> str | None:
    """Return a deny reason when the pre-merge briefing is incomplete, else None."""
    # Demote-to-advisory escape hatch (false-positive relief without full bypass).
    if os.environ.get(MERGE_ADVISORY_ENV) == "1":
        return None

    command = (payload.get("tool_input") or {}).get("command")
    # Analyze only the EXECUTABLE portion (unquoted shell comment stripped) for
    # segments/repetition, so a `# briefing-surfaced: … for …` reason text never
    # trips the loop guard (round-2 P2b).
    code = _split_shell_comment(command)[0] if isinstance(command, str) else command
    # In-band bypass (issue #826): a `# briefing-surfaced` marker in an UNQUOTED
    # shell comment demotes to advisory where the env bypass cannot reach the
    # hook process — but only for a SINGLE merge with no loop. One
    # self-attestation must not release a compound `merge A && merge B` or a
    # looped merge (round-1 P1: same No-Approval-Transfer guard the prior-turn
    # extension applies). Whether it releases *this* merge is decided below,
    # after the transcript is read: issue #940 measured the marker riding along
    # on 8 of 11 merges with no preceding block at all, one of them 627 events
    # after the last briefing, because it short-circuited before any evidence
    # was consulted.
    marker_ok = (
        _has_briefing_marker(command)
        and len(_merge_segments(code)) == 1
        and not _has_repetition(code)
    )

    entries = _load_turn_entries(payload.get("transcript_path", "") or "")
    if entries is None:
        return None  # transcript unreadable → fail open, do not block

    idxs = _human_user_indices(entries)
    lo = (idxs[-1] + 1) if idxs else 0
    last_merge = _last_executed_merge(entries)
    # The PR this merge targets, so the question asking to merge it is read as
    # part of the briefing (issue #1597).
    target = _merge_target_pr(entries, code, 0 if last_merge is None else last_merge + 1)
    current_text = _assistant_text(entries, lo, len(entries), target)
    if _is_trivial_merge(current_text):
        return None
    if _bulk_released(entries, idxs, code):
        return None

    # A complete briefing is half of the flow; the user's answer is the other
    # half (issue #1402). Once a merge has run, a briefing written after it with
    # no user message since can only be citing an answer the earlier merge
    # already consumed, so it no longer releases the next merge on text alone.
    # One answer releases one merge: a loop or chained merge is never answered.
    # An answer also does not cross repos — the prose names a number, never a
    # repo, so a `--repo`-bearing merge whose window probed a DIFFERENT repo is
    # reusing the earlier PR's approval (issue #1419).
    answered = last_merge is None or (
        len(_merge_segments(code)) == 1 and not _has_repetition(code)
        and not _repo_conflict(
            _merge_target_repo(code),
            _context_repo_from_window(entries, last_merge + 1, len(entries)))
        and _answered_after(entries, idxs, last_merge, target))
    items = _briefing_item_count(current_text)
    full_briefing = items >= MERGE_BRIEFING_MIN_ITEMS
    if full_briefing and answered:
        return None

    prev_text = _correlated_prior_turn_text(entries, idxs, code)
    if prev_text is not None:
        if _is_trivial_merge(prev_text):
            return None
        if _briefing_item_count(prev_text) >= MERGE_BRIEFING_MIN_ITEMS:
            if answered:
                return None
            full_briefing = True

    # The marker attests that a briefing was surfaced, so it can stand in for
    # *completeness* — the item counter reads prose and under-counts a briefing
    # phrased outside its vocabulary. It cannot stand in for *existence*: with
    # zero briefing items in the window there is nothing for the attestation to
    # be about, which is exactly the state the marker kept releasing (#940).
    #
    # "The window" is whichever one the gate was allowed to score. In the
    # mandated briefing → approval → merge flow the briefing sits in the prior
    # turn and the current turn is empty, so scoring `items` alone would deny a
    # marked merge whose briefing the user did see — the failure the marker
    # exists to absorb.
    # Serial-merge cut (issue #1214). The window the MARKER may stand on is cut
    # at the last merge that actually ran: prose written before it is that
    # merge's evidence, already spent. Without the cut one briefing releases
    # every later merge in the same window — the cross-turn twin of the
    # compound/loop guard, which only ever saw repetition inside ONE command.
    # It is a window calculation, so no new persistent state; a merge that never
    # ran (`is_error` — this gate, a sibling gate, or a declined approval) moves
    # nothing, leaving the legitimate retry exactly where it was.
    #
    # Scoped to the marker floor deliberately. Applying the same cut to the
    # 4-of-6 full-briefing path above changes 35 historical decisions, 32 of
    # them merges that DID carry a complete briefing — that only raises the
    # cost of the honest path, the ground on which #1214 discarded its own
    # original proposal. Cut here, 3 change and all 3 are marker-only merges.
    floor = 0 if last_merge is None else last_merge + 1
    marker_prev = _correlated_prior_turn_text(entries, idxs, code, floor)
    marker_items = max(
        _briefing_item_count(_assistant_text(entries, max(lo, floor), len(entries), target)),
        _briefing_item_count(marker_prev) if marker_prev is not None else 0,
    )
    if marker_ok and marker_items >= MERGE_BRIEFING_MARKER_MIN_ITEMS:
        return None

    if marker_ok:
        return format_block(
            rule_name="Pre-Merge Reporting briefing",
            why=_with_exclusion(
                "this gh pr merge carries `# briefing-surfaced` but no briefing "
                "is present in the window — the marker attests that a briefing "
                "was complete, not that one exists, and none was found",
                _excluded_briefing_reason(entries, idxs, code,
                                          MERGE_BRIEFING_MARKER_MIN_ITEMS, floor)),
            correct_path="surface the 6-item briefing and an explicit 'Approve "
                "merge?' question in this turn, then re-run the merge",
            bypass_env=MERGE_ADVISORY_ENV,
            bypass_reason_hint="with a one-line reason — set in the session "
                "environment, since an inline `VAR=1 gh pr merge …` prefix "
                "never reaches this hook",
            reference="CLAUDE.md → Pre-Merge Reporting; "
                "hooks/advisory-nudge/momentum-rule-retrieval-gate/spec.md",
        )

    if full_briefing:
        replied = last_merge is not None and any(i > last_merge for i in idxs)
        return format_block(
            rule_name="Pre-Merge Reporting briefing",
            why="a gh pr merge already ran in this session and "
                + ("nothing the user said since approves this merge"
                   if replied else "no user message has arrived since")
                + " — the briefing is complete, but approving one PR approves "
                "only that PR, so this merge needs its own answer",
            correct_path="ask 'Approve merge?' for this PR, wait for the user's "
                "answer, then re-run the merge",
            bypass_env=MERGE_ADVISORY_ENV,
            bypass_reason_hint="with a one-line reason — set in the session "
                "environment, since an inline `VAR=1 gh pr merge …` prefix "
                "never reaches this hook",
            reference="CLAUDE.md → Pre-Merge Reporting; "
                "hooks/advisory-nudge/momentum-rule-retrieval-gate/spec.md",
        )

    return format_block(
        rule_name="Pre-Merge Reporting briefing",
        why=_with_exclusion(
            "this gh pr merge is not preceded by the Pre-Merge Reporting "
            "briefing — fewer than "
            f"{MERGE_BRIEFING_MIN_ITEMS} of 6 items present (What changed / "
            "verified / NOT verified / Risk / Open items / explicit approve-ask)",
            _excluded_briefing_reason(entries, idxs, code, MERGE_BRIEFING_MIN_ITEMS)),
        correct_path="surface the 6-item briefing and an explicit 'Approve "
            "merge?' question, then re-run the merge",
        bypass_env=MERGE_ADVISORY_ENV,
        bypass_reason_hint="with a one-line reason, set in the session "
            "environment (an inline `VAR=1 gh pr merge …` prefix never reaches "
            "this hook) — or append `# briefing-surfaced: <reason>` to the "
            "merge command, only when the 6-item briefing is already in this "
            "turn, or in the turn that named this PR right before the user's "
            "approval: the marker attests the briefing was complete, not that "
            "one exists",
        reference="CLAUDE.md → Pre-Merge Reporting; "
            "hooks/advisory-nudge/momentum-rule-retrieval-gate/spec.md",
    )


# ---------------------------------------------------------------------------
# GH global flags that consume one additional argument.
# ---------------------------------------------------------------------------

GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})


# ---------------------------------------------------------------------------
# Trigger detection helpers.
# ---------------------------------------------------------------------------

def _is_gh_pr_merge(argv: list[str]) -> bool:
    """Return True iff the argv segment is `gh pr merge`."""
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
        return False
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in GH_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            i += 1
    if i + 1 >= len(argv):
        return False
    if argv[i] != "pr" or argv[i + 1] != "merge":
        return False
    # `gh pr merge --help` prints usage and merges nothing (issue #985).
    return not is_help_invocation(argv[i + 2:], _MERGE_VALUE_FLAGS)


def _is_merge(command: str) -> bool:
    tokens = safe_tokenize(command)
    return bool(tokens) and any(_is_gh_pr_merge(argv) for argv in iter_command_starts(tokens))


@fail_open
def main() -> int:
    # Bypass: scripted batch operations may set this to silence the gate.
    if os.environ.get("PRAXIS_MOMENTUM_BYPASS") == "1":
        return 0

    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed
    if not command.strip():
        return 0
    if not _is_merge(command):
        return 0

    sys.stderr.write(
        _surface.emit_surface(_MERGE_STATIC, TRIGGER_MERGE, resolve_memory_dir()) + "\n")

    # Merge-briefing escalation (issue #797): block the merge when the
    # preceding assistant text lacks the Pre-Merge Reporting briefing. The
    # stderr surface above is still emitted so the agent sees the rule text too.
    reason = _merge_escalation_reason(payload)
    if reason is not None:
        # The verb checklist goes out on BOTH channels, because neither one
        # alone reaches the model in every case (issue #932): only the first
        # deny's stdout survives aggregation, and a PreToolUse hook's stderr
        # reaches the model only on the deny path. Deny is always exit 2, so
        # stderr suffices here — keep the two channels identical anyway.
        checklist = verb_gate_checklist("gh pr merge")
        sys.stderr.write(checklist)
        emit_decision("deny", f"{reason}\n{checklist}")
        return 0

    # Strict mode: block unless PRAXIS_MOMENTUM_ACK=1 is also present.
    if os.environ.get("PRAXIS_MOMENTUM_STRICT") == "1":
        if os.environ.get("PRAXIS_MOMENTUM_ACK") != "1":
            sys.stderr.write(
                f"{PREFIX} STRICT MODE — set PRAXIS_MOMENTUM_ACK=1 to proceed\n"
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
