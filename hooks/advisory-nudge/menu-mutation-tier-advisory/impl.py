#!/usr/bin/env python3
"""PreToolUse(AskUserQuestion) nudge: approval menu offers no non-mutating tier.

When the thing being approved changes shared state, a menu whose candidate
options all sit in the same blast-radius tier (all prod) asks the user "how
much", never "whether". The menu fixes the axis and the user only picks a point
on it — so if no safe tier is among the candidates, the consent itself is not
intact.

This hook reads each option's label + description, classifies it into one of
three tiers, and nudges when the candidate set carries a mutation tier and no
low-blast tier at all.

Background (2026-08-11, issue #963):
  A prod DAG-trigger approval menu offered `두 소스 다 트리거 (Recommended)` /
  `한 건만 먼저` / `다음 정기 실행에 맡김`. All three targeted the live customer
  tenant; preview / dev / sandbox were never enumerated even though the
  project's own rules said e2e verification runs on preview. The approved run
  made a prod Iceberg table permanently unqueryable.

  `block-manufactured-action-menu` inspects whether an option is *manufactured*
  (redundant re-confirmation). `merge-menu-review-options-advisory` inspects
  whether a merge gate offers a *review lever*. Neither looks at whether the
  options span more than one blast-radius tier — this hook is the third axis on
  the same event.

Abandonment is not a tier (the issue's open question):
  `다음 정기 실행에 맡김` / `하지 않음` / `대기` do not mutate, but they are not
  low-blast *alternatives* either — they are the choice to give up on verifying.
  Counting them as a safe tier would silence this hook on the exact incident it
  exists for, so they are classified as ABANDON: they neither suppress the
  advisory nor count as a real candidate.

Default mode: advisory (exit 0 + stderr nudge).
Strict mode (PRAXIS_MENU_MUTATION_TIER_STRICT=1): block (exit 2).

Allow conditions (no nudge/block emitted):
  1. tool_name != "AskUserQuestion"
  2. Fewer than 2 non-abandonment candidate options in every question
  3. No candidate carries a mutation signal (menu is not tier-relevant)
  4. Any option carries a low-blast signal (a safe tier IS on the menu)
  5. Malformed / partial payload (fail-open per project hook design contract)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Tier signal tokens
# ---------------------------------------------------------------------------
#
# English tokens use ASCII-letter lookaround — the repo's whole-token mechanism
# (`\b` would not split `prod실행`, because Python's Unicode-aware `\w` treats
# Hangul as a word character). Korean tokens are substring-matched, the
# convention both siblings on this event already use: CJK has no ASCII word
# boundary, and collision risk is controlled by preferring multi-character
# phrases over single verbs (`실행` is deliberately absent — it matches the
# abandonment label `다음 정기 실행에 맡김`).

# Tier 1a — high-blast TARGETS. Not verbs: the shared surface itself. Split out
# from the verbs below because a low-blast token loses its meaning next to one
# of these. `deploy to dev` is a mutating verb aimed at a safe surface and is a
# genuine low-blast alternative; `dry-run then deploy to prod` names the safe
# step and then the prod one, and ends on the shared surface either way. Only
# the presence of a high-blast target separates the two.
HIGH_BLAST_TARGETS_EN = (
    "prod",
    "production",
)
HIGH_BLAST_TARGETS_KO = (
    "프로덕션",
    "실 고객",
    "실고객",
    "실제 고객",
    "운영 환경",
    "운영환경",
)

# Tier 1b — mutation VERBS. The option changes shared state, wherever it points.
# The external-write verbs (`send`, `notify`, …) are here because a Slack post
# or an email is a shared-state mutation with no rollback — a menu offering only
# `Send Slack now` / `Send email instead` is the same defect wearing different
# vocabulary. They are kept verb-anchored: the nouns (`slack`, `email`) also
# occur in read-only options ("read the Slack thread") and would misfire.
MUTATION_VERBS_EN = (
    "deploy",
    "trigger",
    "merge",
    "delete",
    "drop",
    "truncate",
    "apply",
    "backfill",
    "overwrite",
    "publish",
    "push",
    "send",
    "notify",
    "broadcast",
    "announce",
    "submit",
)
MUTATION_VERBS_KO = (
    "트리거",
    "머지",
    "삭제",
    "배포",
    "적용",
    "덮어쓰",
    "백필",
    "전송",
    "발송",
    "공지",
    "게시",
)

MUTATION_TOKENS_EN = HIGH_BLAST_TARGETS_EN + MUTATION_VERBS_EN
MUTATION_TOKENS_KO = HIGH_BLAST_TARGETS_KO + MUTATION_VERBS_KO

# Tier 2 — non-mutating / low blast radius. The option is a real verification
# alternative that does not touch the shared surface. Presence of ANY of these
# suppresses the advisory: a safe tier is on the menu, which is all this hook
# asks for. A false positive here only *silences* the nudge, which is the safe
# direction — the same rationale `merge-menu-review-options-advisory` records
# for its own suppression set.
#
# Review / inspection levers are in this tier on purpose: re-running a review
# verifies without touching the shared surface, which is exactly the cheaper
# alternative this hook asks the menu to carry.
LOW_BLAST_TOKENS_EN = (
    "preview",
    "dev",
    "development",
    "staging",
    "sandbox",
    "dry-run",
    "dryrun",
    "dry run",
    "local",
    "mock",
    "simulate",
    "simulation",
    "read-only",
    "readonly",
    "report-only",
    "plan-only",
    "no-op",
    "noop",
    # `reviewer` / `codex` / `critic` are listed alongside `review` because the
    # lookaround is whole-token: `review` does not match inside `code-reviewer`.
    "review",
    "reviewer",
    "codex",
    "critic",
    "audit",
    "inspect",
)
LOW_BLAST_TOKENS_KO = (
    "프리뷰",
    "미리보기",
    "개발 환경",
    "개발환경",
    "개발 서버",
    "개발계",
    "샌드박스",
    "스테이징",
    "드라이런",
    "보고만",
    "조회만",
    "읽기 전용",
    "읽기전용",
    "시뮬레이션",
    "로컬",
    "리뷰",
    "검토",
    "점검",
)

# Tier 0 — abandonment. Not a tier: the option declines to act rather than
# offering a cheaper way to verify. Excluded from the candidate count so a
# binary go/no-go menu (`머지` / `대기`) does not fire, and — critically — NOT
# treated as a low-blast tier, so the 2026-08-11 menu (whose third option was
# `다음 정기 실행에 맡김`) fires rather than being suppressed by it.
ABANDON_TOKENS_EN = (
    "later",
    "skip",
    "defer",
    "postpone",
    "do nothing",
    "nothing",
    "cancel",
    "hold",
    "wait",
    "none",
)
ABANDON_TOKENS_KO = (
    "다음 정기",
    "다음 주기",
    "나중에",
    "하지 않",
    "안 함",
    "안함",
    "대기",
    "보류",
    "취소",
    "건너뛰",
    "미루",
    "아무것도",
    "그대로 둬",
    "그대로 둔",
)


def _en_token_present(token: str, lower_text: str) -> bool:
    """ASCII-letter lookaround match: token not flanked by ASCII letters.

    Rejects `merged` / `deployment` for tokens `merge` / `deploy`, while still
    matching mixed-script text like `prod 트리거` and hyphenated `dry-run`.
    """
    pattern = r"(?<![a-z])" + re.escape(token.lower()) + r"(?![a-z])"
    return re.search(pattern, lower_text) is not None


def _matches(text: str, ko_tokens: tuple[str, ...], en_tokens: tuple[str, ...]) -> bool:
    lower = text.lower()
    if any(token in text for token in ko_tokens):
        return True
    return any(_en_token_present(token, lower) for token in en_tokens)


def _is_mutation(text: str) -> bool:
    return _matches(text, MUTATION_TOKENS_KO, MUTATION_TOKENS_EN)


def _names_high_blast_target(text: str) -> bool:
    return _matches(text, HIGH_BLAST_TARGETS_KO, HIGH_BLAST_TARGETS_EN)


def _is_low_blast(text: str) -> bool:
    """True if this option is a genuine non-mutating alternative.

    A low-blast token alone is not enough: a compound option
    (`dry-run then deploy to prod`) names the safe step and then the prod one,
    and still ends on the shared surface. Naming a high-blast target disqualifies
    the option no matter which safe words sit beside it — otherwise one such
    option silently suppresses the advisory for the whole menu, which is the
    hook's central failure mode rather than a corner case.

    A mutation VERB does not disqualify: `deploy to dev first` is a mutating
    verb aimed at a safe surface, which is exactly the alternative this hook
    asks the menu to carry.
    """
    if _names_high_blast_target(text):
        return False
    return _matches(text, LOW_BLAST_TOKENS_KO, LOW_BLAST_TOKENS_EN)


def _is_abandon(text: str) -> bool:
    return _matches(text, ABANDON_TOKENS_KO, ABANDON_TOKENS_EN)


# ---------------------------------------------------------------------------
# Payload walking
# ---------------------------------------------------------------------------


# In-band marker letting an author state that no safe tier exists. The advisory
# asks for one of two things — add a low-blast option, or say why one is
# impossible — so without a way to record the second the hook has no satisfying
# path, and strict mode becomes a gate nothing can pass but an extra option.
#
# Shape mirrors the sibling `output-block-falsify-advisory`'s `Falsified:`
# marker exactly: the literal prefix at column 0 of its own line in the question
# body, checked with `startswith`, with non-empty text after it. Prose, bullets,
# and fenced blocks do not count — an author writing the reason must place it as
# its own line, which is also what makes the reason visible to the reader the
# menu is for.
_REASON_MARKER = "Safe-tier-unavailable:"

_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def _prose_lines(question_text: str) -> "list[str]":
    """The lines a reader reads as prose — fenced blocks and HTML comments out.

    A fenced example is the reason this exists: its content sits at column 0
    like a real marker does, so documenting the marker would otherwise satisfy
    it. An unterminated fence swallows the rest of the body on purpose — the
    author who opened it is showing an example, not stating a reason.
    """
    lines = []
    fence = None
    in_comment = False
    for line in question_text.splitlines():
        if fence is not None:
            close = _FENCE_RE.match(line)
            # CommonMark: the closer is the same character, at least as long,
            # and carries no info string.
            if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= len(fence):
                if not line[close.end():].strip():
                    fence = None
            continue
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        opener = _FENCE_RE.match(line)
        if opener:
            fence = opener.group(1)
            continue
        if line.lstrip().startswith("<!--"):
            if "-->" not in line:
                in_comment = True
            continue
        lines.append(line)
    return lines


def _has_reason_marker(question_text: str) -> bool:
    """True if the question body states why no non-mutating tier is available."""
    for line in _prose_lines(question_text):
        if line.startswith(_REASON_MARKER):
            if line[len(_REASON_MARKER):].strip():
                return True
    return False


def _question_body(q: dict) -> str:
    """The question-level prose the reason marker may live in."""
    parts = [q.get("question"), q.get("header")]
    return "\n".join(p for p in parts if isinstance(p, str))


def _collect_option_texts(tool_input: dict) -> list[tuple[list[str], str]]:
    """Return one `(option texts, question body)` pair per question.

    Each option contributes `label` + `description` joined — the issue's SOT is
    explicit that the tier signal may live in either ("옵션 라벨/설명에서 mutation
    티어 신호를 추출"), and an elliptical label (`한 건만 먼저`) routinely carries
    its target only in the description.

    Tolerant of partial schemas: any missing field yields fewer entries rather
    than an exception. The hook must never crash on a malformed payload.
    """
    per_question: list[tuple[list[str], str]] = []
    questions = tool_input.get("questions") or []
    if not isinstance(questions, list):
        return per_question
    for q in questions:
        if not isinstance(q, dict):
            continue
        options = q.get("options") or []
        if not isinstance(options, list):
            continue
        texts: list[str] = []
        for o in options:
            if not isinstance(o, dict):
                continue
            parts = [o.get("label"), o.get("description")]
            joined = " ".join(p for p in parts if isinstance(p, str))
            if joined.strip():
                texts.append(joined)
        if texts:
            per_question.append((texts, _question_body(q)))
    return per_question


def _question_triggers(texts: list[str]) -> bool:
    """True if this question's option set is single-tier (all-mutating).

    Predicate, in the order the early-returns run:
      1. abandonment options are removed first  (they are neither a candidate
                                                 nor a safe tier, so they must
                                                 not be read for either)
      2. no candidate carries a low-blast signal (a safe tier would suppress)
      3. >= 2 candidates remain                 (a binary go/no-go is a genuine
                                                 "whether", not a "how much")
      4. >= 1 candidate carries a mutation signal

    Step 1 runs before step 2 because the two classes overlap: `skip this run
    and review later` carries an abandonment token AND the low-blast `review`.
    Reading low-blast first let that single option suppress an otherwise
    all-prod menu — the spec says abandonment is neither class, so it has to be
    removed before either question is asked of it.

    Divergence from the issue's draft wording, stated deliberately: the issue
    says *every* option must carry a mutation signal. That is not decidable
    lexically — a scope-reduction option (`한 건만 먼저`) inherits its verb from a
    sibling option and carries no token of its own, so an all-must-match
    predicate is silent on the very menu in the issue's incident. The
    implemented predicate requires the mutation signal from at least one
    candidate and takes the absence of any low-blast option as the defect.
    """
    candidates = [t for t in texts if not _is_abandon(t)]
    if any(_is_low_blast(t) for t in candidates):
        return False
    if len(candidates) < 2:
        return False
    return any(_is_mutation(t) for t in candidates)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

_BODY = """\
Every candidate option in this menu sits in the same blast-radius tier — the
mutating one. No option names a non-mutating alternative (preview / dev /
sandbox / dry-run / 보고만 / 조회만).

A menu fixes the axis and the user picks a point on it. When the whole axis
mutates shared state, the user is choosing HOW MUCH, never WHETHER — the
consent is not intact, because the safe tier was never a candidate.

Do one of two things before surfacing this menu:
  - add one non-mutating option (run the same verification on preview / dev /
    a sandbox, or a report-only pass), or
  - if prod really is the only path, say so in the question body on its own
    line, starting at column 0:

        Safe-tier-unavailable: <what makes a safe tier impossible here>

    That line suppresses this advisory for that question, and it is what the
    reader of the menu sees when deciding.

Note: an option that simply declines to act (`다음 정기 실행에 맡김`, `하지 않음`,
`대기`, `skip`) does NOT count as the non-mutating tier. It abandons the
verification rather than offering a cheaper way to do it.
"""

ADVISORY_MSG = (
    "[advisory] AskUserQuestion approval menu offers no non-mutating tier.\n"
    "\n"
    f"{_BODY}"
    "\n"
    "Strict mode disabled. Set PRAXIS_MENU_MUTATION_TIER_STRICT=1 to block.\n"
)

BLOCK_MSG = (
    "BLOCKED: AskUserQuestion approval menu offers no non-mutating tier.\n"
    "\n"
    f"{_BODY}"
    "\n"
    "To opt out: unset PRAXIS_MENU_MUTATION_TIER_STRICT (default is advisory).\n"
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") != "AskUserQuestion":
        return 0

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    per_question = _collect_option_texts(tool_input)
    # Evaluated per question, not across the whole payload: two independent
    # questions have independent option sets, and pooling them would let one
    # question's preview option suppress another question's all-prod menu. The
    # reason marker is read per question for the same reason.
    fires = any(
        _question_triggers(texts) and not _has_reason_marker(body)
        for texts, body in per_question
    )
    if not fires:
        return 0

    # Strict only on the documented `=1` value, matching the dominant codebase
    # convention (destructive-bash-guard, protected-paths-guard,
    # merge-menu-review-options-advisory).
    strict_set = os.environ.get("PRAXIS_MENU_MUTATION_TIER_STRICT", "").strip() == "1"

    if strict_set:
        sys.stderr.write(BLOCK_MSG)
        return 2

    # Soft gate, not stderr: this advisory asks the *composing agent* to add a
    # tier or state a reason, and stderr never reaches it (CONTRIBUTING.md,
    # "Advisory output is not visible to the model"). The sibling
    # `output-block-falsify-advisory` gates the same event/matcher the same way.
    sys.stderr.write(ADVISORY_MSG)
    emit_decision("ask", ADVISORY_MSG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
