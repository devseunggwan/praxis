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

# Tier 1c — CONDITIONAL mutation verbs (issue #974, codex round-1 gap 1 on
# PR #966). `create` / `update` do change shared state, but unlike every Tier 1b
# verb they are also ordinary English for making a file, a heading, or a
# sentence. They are therefore counted as a mutation signal only when the same
# option also names a shared surface (below).
#
# Measured, not assumed: a prototype that added them to MUTATION_VERBS_EN
# unconditionally fired on `Create a new test file` / `Update the existing test`,
# `Create a README section` / `Update the changelog`, `문서 생성` / `문서 갱신`,
# and `Create a shorter title` / `Update the wording` — four ordinary menus that
# touch no shared surface. That cost is not a stderr line: since PR #966 rev 5
# the default path is `emit_decision("ask", …)`, so a false fire is a human
# confirmation prompt. The gate keeps the issue's own repro (`Create the
# customer record` / `Update the customer record`) firing while leaving all four
# silent.
CONDITIONAL_MUTATION_VERBS_EN = (
    "create",
    "update",
)
CONDITIONAL_MUTATION_VERBS_KO = (
    "생성",
    "갱신",
)

# The shared surfaces that promote a Tier 1c verb into a mutation signal. Data
# and infrastructure nouns only — deliberately not `file`, `doc`, `section`,
# `title`, `test`, which is what separates the issue's repro from the four
# false-fire menus above. Plurals are listed explicitly because the English
# lookaround is whole-token (the same reason `development` and `reviewer` are
# listed beside `dev` and `review`).
#
# `schema`, `index`, `namespace`, `migration`, `dag`, `policy` were drafted here
# and removed after probing: each fired on an ordinary authoring menu
# (`Create a migration file` / `Update the migration file`,
# `Create a retry policy in the client` / `Update the retry policy`,
# `Create a new namespace` / `Update the namespace`, …) because their everyday
# meaning is a repo artifact, not a shared surface. Creating a migration is
# authoring a file; it is `apply` — already Tier 1b — that mutates.
SHARED_SURFACE_NOUNS_EN = (
    "record",
    "records",
    "row",
    "rows",
    "table",
    "tables",
    "database",
    "dataset",
    "bucket",
    "cluster",
    "tenant",
    "tenants",
    "customer",
    "customers",
    "account",
    "accounts",
    "subscription",
    "subscriptions",
    "secret",
    "secrets",
    "credential",
    "credentials",
    "pipeline",
    "webhook",
)
SHARED_SURFACE_NOUNS_KO = (
    "레코드",
    "테이블",
    "데이터베이스",
    "데이터셋",
    "버킷",
    "클러스터",
    "테넌트",
    "고객",
    "계정",
    "구독",
    "시크릿",
    "자격 증명",
    "자격증명",
    "파이프라인",
)

# …and the veto: a shared-surface noun sitting inside an authoring artifact is
# not the shared surface. `Create a fixture record for the test` and
# `Create an index variable` both name a Tier 1c verb next to a kept noun, and
# both fired before this set existed.
#
# Consulted ONLY from the Tier 1c branch, so no option without `create` /
# `update` / `생성` / `갱신` changes classification because of it. It is a
# false-negative surface of its own — a genuine shared-surface menu that
# happens to say `spec` or `test` goes silent — which is the same direction as
# the defect being fixed and is recorded under Known limitations in spec.md.
LOCAL_ARTIFACT_NOUNS_EN = (
    "file",
    "files",
    "doc",
    "docs",
    "readme",
    "changelog",
    "spec",
    "comment",
    "comments",
    "variable",
    "fixture",
    "fixtures",
    "test",
    "tests",
    "draft",
    "template",
    "snippet",
)
LOCAL_ARTIFACT_NOUNS_KO = (
    "파일",
    "문서",
    "주석",
    "변수",
    "테스트",
    "픽스처",
    "초안",
    "템플릿",
)

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
    "건너뛰",
    "미루",
    "아무것도",
    "그대로 둬",
    "그대로 둔",
)

# Tier 0b — CONDITIONAL abandonment (issue #974, codex round-1 gap 2 on
# PR #966). `cancel` / `취소` mean two different things depending on whether the
# option carries an object. Bare `Cancel` declines to act. `Cancel the prod
# deployment` cancels something already in flight — that aborts a running change
# to the shared surface, which is itself a mutation, and a menu of
# `Cancel the prod deployment` / `Proceed with the prod deployment` has no
# non-mutating tier at all.
#
# Before the fix that menu was silent, and not because of a vocabulary hole:
# `Cancel the prod deployment` already classified as a mutation via `prod`, but
# `_question_triggers` strips abandonment options before it ever asks the
# mutation question, so the option was dropped and the single survivor fell
# below the two-candidate floor.
#
# The discriminator is the mutation evidence the hook already computes, not
# syntactic object-detection: a regex for `cancel` + determiner + noun would
# also demote `Cancel the deployment` and `Skip the deployment`, which are
# genuine do-nothing tiers. The unconditional sets above are checked first, so
# `do not` / `하지 않` phrasings keep their abandonment status even when they
# name a prod object.
CONDITIONAL_ABANDON_EN = ("cancel",)
CONDITIONAL_ABANDON_KO = ("취소",)

# The rule above has its own false-fire surface, measured: a genuine no-go
# option that spells out what it is declining (`Cancel — do not deploy to
# prod`) carries the mutation token too, so it would be promoted to a candidate
# and fire the advisory on a menu that already offers "whether". An explicit
# negation settles it in the abandonment direction.
#
# Read ONLY inside the Tier 0b branch, never as a Tier 0 token of its own. A
# global `do not` was tried and rejected: it silenced
# `Deploy to prod and notify` / `Deploy to prod but do not notify`, where the
# negation attaches to a rider rather than to the act, and that menu fires
# today. Scoping it to options that also say `cancel` keeps the blast radius of
# this whole issue at exactly one word — no option without `cancel` / `취소`
# changes classification. `하지 않` is already unconditional Tier 0, which is
# why the KO phrasing never had the reverse false fire; it is listed here too
# so the pair is readable as one rule.
NEGATION_TOKENS_EN = (
    "do not",
    "don't",
)
NEGATION_TOKENS_KO = (
    "하지 않",
    "안 함",
    "안함",
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


def _names_shared_surface(text: str) -> bool:
    """True if the option names a data / infrastructure surface (Tier 1c gate).

    An authoring artifact in the same option vetoes it: `a fixture record for
    the test` names `record`, but the record is a repo artifact, not the shared
    surface this hook is about.
    """
    if _matches(text, LOCAL_ARTIFACT_NOUNS_KO, LOCAL_ARTIFACT_NOUNS_EN):
        return False
    return _matches(text, SHARED_SURFACE_NOUNS_KO, SHARED_SURFACE_NOUNS_EN)


def _is_mutation(text: str) -> bool:
    """True if this option changes shared state.

    Tier 1a/1b tokens count on their own. Tier 1c verbs (`create` / `update` /
    `생성` / `갱신`) count only when the option also names a shared surface —
    they are too common in ordinary file, doc, and prompt wording to carry the
    signal alone. See the Tier 1c block above for the measured false fires.
    """
    if _matches(text, MUTATION_TOKENS_KO, MUTATION_TOKENS_EN):
        return True
    if _matches(text, CONDITIONAL_MUTATION_VERBS_KO, CONDITIONAL_MUTATION_VERBS_EN):
        return _names_shared_surface(text)
    return False


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
    """True if this option declines to act rather than offering a cheaper path.

    Tier 0 tokens are unconditional. Tier 0b (`cancel` / `취소`) abandons only
    when nothing else in the option is mutating: a bare `Cancel` is the do-
    nothing tier, while `Cancel the prod deployment` aborts an in-flight change
    to the shared surface and stays a candidate (issue #974). An explicit
    negation inside the same option overrides that — it is a no-go naming its
    own object, not a cancellation of something already running.
    """
    if _matches(text, ABANDON_TOKENS_KO, ABANDON_TOKENS_EN):
        return True
    if _matches(text, CONDITIONAL_ABANDON_KO, CONDITIONAL_ABANDON_EN):
        if _matches(text, NEGATION_TOKENS_KO, NEGATION_TOKENS_EN):
            return True
        return not _is_mutation(text)
    return False


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

    That ordering is also why `cancel` had to become conditional rather than
    gain a sibling token (issue #974): step 1 removes the option before step 4
    can notice it is mutating, so `Cancel the prod deployment` /
    `Proceed with the prod deployment` fell to one candidate and went silent
    even though both options change prod.

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
