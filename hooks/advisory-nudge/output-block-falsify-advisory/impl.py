#!/usr/bin/env python3
"""PreToolUse advisory + ask-escalation: output-block falsification gate.

Issue #221 (advisory), #290 (ask escalation), #369 (confidence-anchoring
extension). Recurring failure mode: the "Output-Block-Level Falsification
Gate" rule in CLAUDE.md (4+ memory entries accumulated 2026-05-03 through
2026-05-21) fails to fire at output time because rule retrieval is not
structural — the rule is loaded but not re-triggered at the moment the
proposal block is authored.

This hook adds a structural enforcement point at two surfaces:

  1. AskUserQuestion — escalates the question by tier when the body
     lacks a `Falsified:` line (exact prefix at line start):

     T1. Label contains exact `(Recommended)` or `(추천)` (case-sensitive)
         — original literal-marker path. **Emits `permissionDecision: deny`
         (hard block)** — issue #393 upgrade. Rationale: the explicit
         marker is a high-confidence false-positive-free signal; soft
         `ask` permitted acknowledge-and-proceed and within-session
         recurrence was observed (retrospect 2026-05-23, 3 calls in
         a single codex-review-wrap chain). False-positive rate of
         a literal `(Recommended)` label without `Falsified:` is low
         enough to justify hard block.

     T2. Label OR description contains a confidence-anchoring framing
         token (issue #369). EN tokens (case-insensitive, ASCII word
         boundary): `safer`, `safest`, `clearly`, `obvious choice`,
         `natural fit/choice`, `default to/choice`, `prefer this`,
         bare `recommend(ed|s)?`. KO substrings: `안전한`, `가장 안전`,
         `자연스러운`, `당연히`, `분명히`, `추천`, `기본값`.
         **Emits `permissionDecision: ask` (soft gate)** — kept at ask
         for ergonomics: framing-token detection is broader and carries
         higher false-positive risk than the literal T1 marker.

     When `Falsified:` is present in the question body OR the triggering
     option's own `description` field, both tiers silent-pass (issue #828:
     the description-field path lets the evidence stay out of the shared
     `question` string, keeping it short — see `q_texts` construction below).

  2. Bash — bulk-action commands containing patterns like "close all",
     "delete all", "merge all" (+ Korean equivalents). Advisory only.

Fail-open contract (project hook design):
  - Malformed / missing stdin JSON → exit 0
  - Unknown tool_name → exit 0
  - Missing target field (questions / command) → exit 0
  - Any uncaught exception → exit 0
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from ask_option_text import collect_option_texts  # type: ignore[import-not-found]  # noqa: E402
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402

_TELEMETRY_HOOK_NAME = "output-block-falsify-advisory"
_TELEMETRY_HOOK_ROLE = "advisory-nudge"

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

ADVISORY_MSG = (
    "[output-block-falsify-advisory] Surfacing a recommendation/bulk-action "
    "proposal? Run the output-block falsification gate first: is the proposal's "
    "premise already addressed by in-flight work, a merged PR, or a parallel "
    "proposal in this session? If yes — STOP and cite the invalidating link "
    "instead of surfacing the proposal."
)

ASK_MSG = (
    "(Recommended) 라벨이 있으나 question body 또는 해당 옵션의 description "
    "필드 어디에도 'Falsified: <disconfirming test 결과>' 가 없음. "
    "CLAUDE.md Self-Falsify Before Recommendation Lock 룰. "
    # Template-level guidance (issue #682): the problem is not only this call —
    # the AskUserQuestion compose template is missing the field. Bake a
    # column-0 `Falsified:` line into every future (Recommended) compose BEFORE
    # the tool call is formed, not just when the hook fires.
    "[pre-author-template] "
    "이번 호출뿐 아니라 앞으로 (Recommended) 라벨을 붙일 때마다 "
    "AskUserQuestion 작성 직전(도구 호출 전) 에 "
    "첫 칼럼 시작 'Falsified: <검증 결과>' 줄을 템플릿에 포함하라 — "
    "인스턴스 수정이 아닌 템플릿 수정이 필요하다. "
    "'Falsified:' 는 자기 줄 첫 칼럼에서 시작해야 한다 (startswith 검사) — "
    "질문문 중간/불릿/코드펜스 내부 배치는 미검출. "
    # Issue #828: prefer the option's own description field over the
    # question body so long/multiline evidence doesn't accumulate in one
    # shared string.
    "[trigger-reduction] question 은 짧게 유지하고, Falsified: 줄은 해당 옵션 "
    "(Recommended) 의 description 필드에 넣는 것을 권장 — 두 위치 모두 검증됨."
)

ANCHORING_ASK_MSG = (
    "옵션 라벨/설명에 confidence-anchoring framing 토큰 (safer/safest/"
    "natural/obvious/clearly/default/prefer/recommend/안전한/자연스러운/"
    "당연히/분명히/추천/기본값) 이 있으나 question body 또는 해당 옵션의 "
    "description 필드 어디에도 'Falsified: <disconfirming test 결과>' 가 없음. "
    "CLAUDE.md Output-Block-Level Falsification Gate. "
    # Template-level guidance (issue #682): bake the Falsified: line into the
    # AskUserQuestion compose template for every anchoring-framed option, not
    # just the current call.
    "[pre-author-template] "
    "이번 호출뿐 아니라 앞으로 confidence-anchoring 토큰을 옵션 라벨/설명에 쓸 때마다 "
    "AskUserQuestion 작성 직전(도구 호출 전) 에 "
    "첫 칼럼 시작 'Falsified: <검증 결과>' 줄을 템플릿에 포함하라 — "
    "인스턴스 수정이 아닌 템플릿 수정이 필요하다. "
    "'Falsified:' 는 자기 줄 첫 칼럼에서 시작해야 한다 (startswith 검사) — "
    "질문문 중간/불릿/코드펜스 내부 배치는 미검출. "
    # Issue #828: same trigger-reduction guidance as ASK_MSG.
    "[trigger-reduction] question 은 짧게 유지하고, Falsified: 줄은 해당 옵션의 "
    "description 필드에 넣는 것을 권장 — 두 위치 모두 검증됨."
)

# ---------------------------------------------------------------------------
# AskUserQuestion: (Recommended) / (추천) marker detection
# ---------------------------------------------------------------------------

# Exact tokens for ask-escalation (case-sensitive, includes parentheses).
RECOMMENDED_MARKERS_EXACT_EN = ("(Recommended)",)
RECOMMENDED_MARKERS_EXACT_KO = ("(추천)",)

# Substrings for fallback advisory (case-insensitive for English form).
RECOMMENDED_MARKERS_EN = ("(Recommended)",)
RECOMMENDED_MARKERS_KO = ("(추천)",)

# Confidence-anchoring framing tokens (issue #369). Scanned on BOTH option
# label and description, not just label, because in-vivo evidence showed
# anchoring framing placed in description ("가장 안전한 1회 변경") bypassed
# the literal marker check. Same Falsified: satisfaction as the marker tier.
#
# EN: ASCII lookaround instead of `\b`. Python's `\b` is Unicode-aware
# and would misfire when Korean text sits adjacent to ASCII (no
# boundary between Hangul and ASCII word chars). Mirrors the
# _BULK_PATTERN_EN strategy below.
#
# `recommend(?:ed|s)?` is intentionally bare (no parens) to catch
# label/description usage without literal "(Recommended)" markers.
# When `(Recommended)` IS present in a label, T1 (exact marker) fires
# first and short-circuits before this pattern is consulted.
_ANCHORING_EN_PATTERN = re.compile(
    r"(?<![A-Za-z])"
    r"(?:"
    r"safer|safest|clearly|"
    r"natural\s+(?:fit|choice)|"
    r"obvious\s+choice|"
    r"default\s+(?:to|choice)|"
    r"prefer\s+this|"
    r"recommend(?:ed|s)?"
    r")"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)

# KO substrings. Hangul has no ASCII word-boundary issue — these tokens
# don't appear as substrings of unrelated words in option-label / desc
# context. "가장 안전" is listed separately from bare "안전한" to keep
# the recall for the in-vivo "가장 안전한 1회 변경" phrasing explicit,
# even though "안전한" alone is sufficient — second entry is a no-op
# under substring match but documents the canonical trigger phrase.
_ANCHORING_KO_SUBSTRINGS = (
    "안전한",
    "가장 안전",
    "자연스러운",
    "당연히",
    "분명히",
    "추천",
    "기본값",
)


def _has_exact_recommended_marker(labels: list[str]) -> bool:
    """True if any label contains exact (Recommended) or (추천) (case-sensitive)."""
    if not labels:
        return False
    for label in labels:
        for marker in RECOMMENDED_MARKERS_EXACT_EN:
            if marker in label:
                return True
        for marker in RECOMMENDED_MARKERS_EXACT_KO:
            if marker in label:
                return True
    return False


# Issue #787 codex round-2 P1: the ready-to-fill scaffold (see
# _falsified_scaffold below) itself starts a line with "Falsified:", so a
# verbatim copy-paste that never fills in the placeholders would otherwise
# silently satisfy this exact-prefix check with zero probe evidence — the
# gate this hook exists to enforce. A line carrying any of these literal
# placeholder tokens in its evidence region does not count as satisfied.
_SCAFFOLD_PLACEHOLDER_TOKENS = ("<command>", "<observed>", "<...>")

# Issue #787 codex round-5 P2: the scaffold format is
# "Falsified: {label} — probe: <command> → <observed>; ...", and {label} is
# seeded verbatim from the actual option label. If an option's own label
# text happens to contain one of the placeholder tokens (e.g. a label
# literally reading "Run <command> manually"), scanning the WHOLE line
# would flag it as unfilled forever, even after the probe/observed content
# past this marker is genuinely filled in. The evidence a probe must
# supply lives after this marker, so placeholder scanning is scoped to
# that region only — the label prefix is never evidence-bearing.
_PROBE_MARKER = " — probe: "


def _has_falsified_line(texts: list[str], triggering_labels: list[str]) -> bool:
    """True if every label in `triggering_labels` is addressed by its own
    clean 'Falsified:' line, and NO 'Falsified:'-prefixed, scaffold-shaped
    line still carries an unfilled scaffold placeholder in its evidence
    region.

    `triggering_labels` is the deduped list of option labels that triggered
    T1/T2 for this question. When there are 0 or 1 triggering labels, ANY
    clean line satisfies the question (the pre-#787 contract, in place
    since issue #290: a single free-form Falsified line covers the whole
    question regardless of wording — there is no ambiguity about which
    option it addresses when only one is in play). When there are 2+
    triggering labels, each one must be individually covered.

    Issue #787 codex round-9 P1 (count-only verification silent-pass): the
    prior contract counted DISTINCT clean lines against `required_count`
    without checking which triggering option each line actually addressed.
    Two differently-worded lines both about Option A satisfied
    `required_count=2` for a 2-option question while Option B was never
    verified at all. A line is now matched to a specific triggering label
    by checking whether it starts with `"Falsified: {label}"` (the exact
    shape the scaffold seeds verbatim) — longest labels are checked first
    so one label can't shadow a longer sibling label that has it as a
    prefix. Coverage requires every triggering label to have >= 1 matched
    line; unmatched lines still count toward the single-label-mode
    "any clean line" contract but no longer inflate a raw count.

    Issue #787 codex round-9 P2 (evidence-embedded marker bypass): round-8
    anchored the probe-delimiter on `rfind()` (the LAST `— probe:` in the
    line), assuming the scaffold's real delimiter — inserted once, right
    after the label — is always the rightmost occurrence. If the EVIDENCE
    text itself (legitimately placed after the real delimiter) happens to
    contain a second literal `— probe:` substring, `rfind()` picks that
    spurious later occurrence instead, pushing an actual unfilled
    placeholder sitting before it (but still within the real evidence
    region) outside the scanned window — silent pass. Now that a line is
    matched to its label first, the delimiter is found via `find()` seeded
    at the END of the matched label prefix — the FIRST `— probe:` after the
    label, which is structurally always the real, scaffold-inserted one,
    regardless of how many more `— probe:`-shaped substrings the evidence
    text contains afterward. This also still closes round-8's original
    label-embedded-marker case (a label containing `— probe:` no longer
    matters, since the scan starts strictly after the whole label prefix).
    A line that matches no known triggering label (true free-form text, or
    referencing an option outside the current label set) falls back to the
    prior `rfind()` behavior — there is no label boundary to anchor on.

    Issue #787 codex round-3 P1 (retained): any unfilled scaffold
    placeholder anywhere in the evidence region voids satisfaction outright,
    even when other lines are already filled in correctly — fails closed
    rather than returning True on the first clean line found.

    Issue #787 codex round-5 P2 (retained): placeholder tokens are only
    checked in the content after the `— probe:` marker — a line lacking the
    marker is not scaffold-shaped, so it is never scanned for placeholder
    tokens at all (issue #787 codex round-6 P2: scanning marker-less lines
    for bare token substrings falsely perma-blocked genuine free-form
    evidence whose own wording happened to contain one, e.g. an option
    literally named `<command>` referenced without ever using the
    auto-scaffold's `— probe:` phrasing).

    Issue #787 codex round-10 P2 (label-boundary false match): round-9's
    `line.startswith(f"Falsified: {label}")` matched a label as a bare
    substring prefix, not a whole-token prefix. With triggering labels
    `["Run", "Other"]`, a line reading `Falsified: Runtime behavior
    verified — probe: ...` matched the `"Run"` prefix (since `"Runtime"`
    starts with `"Run"`) and credited `"Run"` as covered even though the
    line never actually addresses that option — the real `"Run"` claim
    stayed unverified while the gate silently passed. round-10 required a
    non-alphanumeric character immediately after the label prefix.

    Issue #787 codex round-12 P2 (near-miss label prefix): round-10's
    non-alphanumeric boundary was still too permissive — a genuinely
    DIFFERENT, unrelated option label that happens to share the triggering
    label as its own prefix, separated by a space, also has a non-
    alphanumeric boundary. With triggering labels `["Merge (Recommended)",
    "Run"]`, a line reading `Falsified: Run now — probe: ...` (evidence
    that is really about a different `"Run now"` option, not the
    triggering `"Run"`) matched `"Run"`'s prefix with `" "` as the
    boundary, wrongly crediting `"Run"` as covered. The only boundary that
    reliably means "the label ends here and evidence begins" is the
    scaffold's own delimiter: a label match now counts only when the line
    ends exactly at the label (remainder empty) or the remainder starts
    with the literal `_PROBE_MARKER` string. `"Run now"` no longer
    satisfies `"Run"` because `" now"` does not start with `" — probe: "`.
    """
    labels_by_len = sorted(dict.fromkeys(triggering_labels), key=len, reverse=True)
    single_label_mode = len(labels_by_len) <= 1

    clean_lines: set[str] = set()
    covered_labels: set[str] = set()
    for text in texts:
        for line in text.splitlines():
            if not line.startswith("Falsified:"):
                continue

            matched_label = None
            label_end = 0
            for label in labels_by_len:
                prefix = f"Falsified: {label}"
                if not line.startswith(prefix):
                    continue
                remainder = line[len(prefix) :]
                # Issue #787 codex round-10 P2: startswith() alone matched
                # a label as a bare substring prefix, not a whole-token
                # prefix — labels=["Run", "Other"] let a line reading
                # "Falsified: Runtime behavior verified — probe: ..." match
                # "Run" (since "Runtime" starts with "Run"), crediting the
                # "Run" option as covered even though the line never
                # actually addresses it. round-10 required a non-
                # alphanumeric character after the label, but that is still
                # too weak: issue #787 codex round-12 P2 — a genuinely
                # DIFFERENT option label sharing the triggering label as its
                # own prefix, separated by a space (e.g. triggering "Run"
                # vs. the unrelated text "Run now"), also has a non-
                # alphanumeric boundary (the space) yet is not evidence for
                # the triggering label at all. The boundary must be the
                # scaffold's own delimiter or true end-of-line — nothing
                # else can be trusted to mean "the label ends here and
                # evidence begins": the label match only counts when it is
                # the whole line (remainder empty) or immediately followed
                # by the literal `_PROBE_MARKER` string.
                if remainder and not remainder.startswith(_PROBE_MARKER):
                    continue
                matched_label = label
                label_end = len(prefix)
                break

            if matched_label is not None:
                marker_at = line.find(_PROBE_MARKER, label_end)
            else:
                marker_at = line.rfind(_PROBE_MARKER)

            if marker_at != -1:
                evidence_region = line[marker_at + len(_PROBE_MARKER) :]
                # CodeRabbit PR #796 review: deleting everything after the
                # marker (or moving it to the next physical line) leaves an
                # empty evidence_region, so the placeholder-token scan finds
                # nothing and the line silently passes with zero probe
                # content — the same class of bypass this whole gate exists
                # to close. A marker-terminated line with no evidence at all
                # is treated the same as one still carrying a placeholder.
                if not evidence_region.strip() or any(
                    token in evidence_region for token in _SCAFFOLD_PLACEHOLDER_TOKENS
                ):
                    return False

            clean_lines.add(" ".join(line.split()))
            if matched_label is not None:
                covered_labels.add(matched_label)

    if single_label_mode:
        return len(clean_lines) >= 1
    return all(label in covered_labels for label in labels_by_len)


def _has_recommended_marker(labels: list[str]) -> bool:
    """True if any option label contains a (Recommended) / (추천) marker.

    Reachable only when T1 (exact marker) and T2 (confidence-anchoring,
    including bare ``recommend``) both miss. Since T2 matches any
    case-insensitive `recommend(ed|s)?` form, an option whose label
    contains `(recommended)` (lowercase) is now caught by T2 (ask)
    before this fallback advisory runs — keep this function for
    behavior continuity and clarity of the original three-tier intent,
    but it is effectively dead under the new precedence.
    """
    if not labels:
        return False
    for label in labels:
        lower = label.lower()
        for marker in RECOMMENDED_MARKERS_EN:
            if marker.lower() in lower:
                return True
        for marker in RECOMMENDED_MARKERS_KO:
            if marker in label:
                return True
    return False


def _normalize_label(label: str) -> str:
    """Collapse embedded newlines/whitespace runs to single spaces.

    Issue #787 codex round-11 P2: a raw option label containing a literal
    newline splits `_falsified_scaffold`'s single-physical-line output
    across two printed lines (`f"Falsified: {label} — probe: ..."`
    interpolates the label verbatim). A verbatim copy-paste of that
    unfilled scaffold then has its placeholder tokens land on the SECOND
    physical line, which does not start with `Falsified:` — invisible to
    `_has_falsified_line`'s per-line scan, silently passing an evidence-free
    question. Normalizing at the single point labels enter the triggering
    pipeline (this function's callers) keeps every downstream consumer —
    scaffold generation and `_has_falsified_line` matching alike — on the
    same "one physical line per label" invariant, without needing the fix
    duplicated in each consumer.
    """
    return " ".join(label.split())


def _t1_matching_labels(labels: list[str]) -> list[str]:
    """Option labels carrying the exact (Recommended)/(추천) marker (T1 scope)."""
    matched: list[str] = []
    for label in labels:
        if any(marker in label for marker in RECOMMENDED_MARKERS_EXACT_EN):
            matched.append(_normalize_label(label))
        elif any(marker in label for marker in RECOMMENDED_MARKERS_EXACT_KO):
            matched.append(_normalize_label(label))
    return matched


def _t2_matching_labels(options: list) -> list[str]:
    """Option labels whose own label OR description carries an anchoring token (T2 scope)."""
    matched: list[str] = []
    for o in options:
        if not isinstance(o, dict):
            continue
        label = o.get("label")
        if not isinstance(label, str):
            continue
        if _has_confidence_anchoring(collect_option_texts([o])):
            matched.append(_normalize_label(label))
    return matched


def _t1_triggering_indices(options: list) -> set[int]:
    """Indices (into `options`) of options carrying the exact (Recommended)/(추천) marker.

    Issue #828 codex round-3 P2: evidence-collection must key on option
    IDENTITY (index), not on normalized label text. Two distinct options
    can share the same normalized label (e.g. "Option  A" and "Option A"
    both collapse to "Option A") — matching by label string let a
    non-triggering option's unrelated `Falsified:` description satisfy a
    *different*, triggering option with the same normalized label. Live
    probe: option 0 label="Option  A" description="safer path overall"
    (T2-triggering, no evidence of its own) + option 1 label="Option A"
    description="Falsified: unrelated ... premise survives because n/a"
    (non-triggering) → silent pass (rc=0, empty stdout) under label-string
    matching. Index-based matching closes this because index 0 and index
    1 are never equal.
    """
    indices: set[int] = set()
    for idx, o in enumerate(options):
        if not isinstance(o, dict):
            continue
        label = o.get("label")
        if not isinstance(label, str):
            continue
        if any(marker in label for marker in RECOMMENDED_MARKERS_EXACT_EN):
            indices.add(idx)
        elif any(marker in label for marker in RECOMMENDED_MARKERS_EXACT_KO):
            indices.add(idx)
    return indices


def _t2_triggering_indices(options: list) -> set[int]:
    """Indices (into `options`) of options whose own label OR description
    carries an anchoring token (T2 scope). See `_t1_triggering_indices` for
    why index identity, not label text, is the matching key (issue #828
    codex round-3 P2).

    Unlike T1 (marker lives in `label` only), T2's OR-contract means a
    missing/non-string `label` must not exclude an option whose
    `description` alone carries the anchoring token (PR #829 CodeRabbit
    review) — `collect_option_texts([o])` already tolerates a non-string
    `label` by omitting it from the scanned text, so no separate guard is
    needed here.
    """
    indices: set[int] = set()
    for idx, o in enumerate(options):
        if not isinstance(o, dict):
            continue
        if _has_confidence_anchoring(collect_option_texts([o])):
            indices.add(idx)
    return indices


def _dedupe_labels(labels: list[str]) -> list[str]:
    """Order-preserving de-dup for aggregated scaffold labels (issue #787)."""
    return list(dict.fromkeys(labels))


def _falsified_scaffold(labels: list[str]) -> str:
    """Ready-to-fill `Falsified:` line(s), one per triggering option label.

    Issue #787: the block message previously told the agent to add a
    `Falsified:` line but left the whole line to be reconstructed on retry,
    costing +1 turn every time. The label is the one fact this hook can
    supply with certainty (it is in the blocked tool_input); probe/observed/
    reason stay as placeholders — the probe itself is the intended cost and
    is never fabricated here.
    """
    if not labels:
        return ""
    lines = [
        f"Falsified: {label} — probe: <command> → <observed>; premise survives because <...>"
        for label in labels
    ]
    return "\n".join(lines)


def _build_ask_msg(labels: list[str]) -> str:
    scaffold = _falsified_scaffold(labels)
    if not scaffold:
        return ASK_MSG
    return f"{ASK_MSG} [scaffold]\n{scaffold}"


def _build_anchoring_ask_msg(labels: list[str]) -> str:
    scaffold = _falsified_scaffold(labels)
    if not scaffold:
        return ANCHORING_ASK_MSG
    return f"{ANCHORING_ASK_MSG} [scaffold]\n{scaffold}"


def _record_block_telemetry(session_id: object, decision: str) -> None:
    """Rich fire-ledger record for a T1/T2 block event (issue #787).

    The coarse @fail_open recorder sees rc=0 for every call here (deny/ask
    are signaled via a stdout JSON `permissionDecision`, not exit code 2), so
    it always logs "pass" for this hook — leaving cross-session block-rate
    measurement to transcript grep, which over-counts sessions that merely
    quote the block message in a retrospect report. This records the real
    decision with session attribution so that count can come from the ledger
    instead.

    Always suppresses this process's coarse fallback
    (`_fire_ledger.suppress_coarse_duplicate`) — otherwise `aggregate_fires()`
    sums both the RICH "block"/"ask" record and the COARSE "pass" record for
    the same call, turning one deny into `fires=2, block=1, pass=1` and
    corrupting the exact block-rate count this telemetry exists to provide.

    Coderabbit PR #796 review: a missing `session_id` previously early-returned
    before the RICH write, dropping the block/ask decision from
    `aggregate_fires()`'s `fires`/`block`/`ask` totals entirely — worse than
    the original coarse-pass bug, since the event vanished from the count
    rather than being merely mislabeled. `record_session_fire` already
    coerces a non-str `session_id` to `""`, and `aggregate_fires` only adds a
    session to its per-session set when it is a non-empty string (verified:
    `by_hook[...]["sessions"].add(session)` is guarded by
    `isinstance(session, str) and session`), so emitting the RICH record with
    an empty session_id counts the decision correctly without polluting
    per-session aggregation. The RICH write is now unconditional; only
    per-session attribution is lost when `session_id` is missing, not the
    decision count itself.
    """
    _fire_ledger.suppress_coarse_duplicate()
    _fire_ledger.record_session_fire(
        _TELEMETRY_HOOK_NAME, _TELEMETRY_HOOK_ROLE, decision, session_id, "AskUserQuestion"
    )


def _has_confidence_anchoring(texts: list[str]) -> bool:
    """True if any text contains a confidence-anchoring framing token.

    Scans both EN regex (ASCII word-boundary lookarounds) and KO
    substring patterns. See module docstring for token sets.
    """
    if not texts:
        return False
    for text in texts:
        if not isinstance(text, str):
            continue
        if _ANCHORING_EN_PATTERN.search(text):
            return True
        for kw in _ANCHORING_KO_SUBSTRINGS:
            if kw in text:
                return True
    return False


def _emit_ask(message: str) -> None:
    """T2 path: soft gate. (decision must be "ask"/"deny" — "allow" is never
    emitted; silent-pass is signaled by no JSON output.)"""
    emit_decision("ask", message)


def _emit_deny(message: str) -> None:
    """T1 path: hard block (issue #393 upgrade)."""
    emit_decision("deny", message)


# ---------------------------------------------------------------------------
# Bash: bulk-action keyword detection
# ---------------------------------------------------------------------------

# English bulk-action phrases. Matched as case-insensitive substrings after
# a word-boundary check on the verb part. The "all" / "all " suffix is kept
# as a plain substring to avoid over-matching on common non-bulk commands.
#
# Strategy: search for the verb phrase with "all" nearby. Using a regex with
# ASCII lookaround avoids `\b` issues when Korean text appears nearby.
_BULK_VERBS_EN = ("close", "delete", "merge", "reject", "approve")

# ASCII lookaround instead of `\b`: Python's `\b` is Unicode-aware and would
# misfire when Korean text sits adjacent to ASCII (no boundary between Hangul
# and ASCII word chars). Explicit ASCII boundaries prevent false matches like
# `disclose all` / `enclose all` triggering on the `close all` substring.
_BULK_PATTERN_EN = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(_BULK_VERBS_EN) + r")\s+all(?![A-Za-z])",
    re.IGNORECASE,
)

# Korean bulk-action substrings. Plain substring match is safe because Hangul
# has no ASCII word-boundary issue — these tokens don't appear as substrings
# of unrelated words.
_BULK_SUBSTRINGS_KO = (
    "전부 닫",
    "모두 닫",
    "전부 삭제",
    "모두 삭제",
    "전부 머지",
    "모두 머지",
    "다 머지",
    "전부 클로즈",
    "모두 클로즈",
)

def _is_bulk_action_command(command: str) -> bool:
    """True if the Bash command contains a bulk-action mutation keyword."""
    if not command:
        return False
    # English bulk phrases (close all / delete all / merge all / etc.)
    if _BULK_PATTERN_EN.search(command):
        return True
    # Korean substrings
    for kw in _BULK_SUBSTRINGS_KO:
        if kw in command:
            return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    if not isinstance(payload, dict):
        return 0

    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    if tool_name == "AskUserQuestion":
        questions = tool_input.get("questions") or []
        if not isinstance(questions, list):
            questions = []
        # Issue #787: scan every question — no early break. Stopping at the
        # first violating question left later violating questions
        # unaddressed, so the agent hit a second block on retry even after
        # filling in the first scaffold. any_t1/any_t2 decide the final
        # tier (T1 still wins over T2, same as the pre-#787 precedence);
        # t1_labels/t2_labels aggregate labels from EVERY offending
        # question so one retry can cover all of them.
        any_t1_violation = False
        any_t2_violation = False
        t1_labels: list[str] = []
        t2_labels: list[str] = []
        advisory_needed = False
        for q in questions:
            if not isinstance(q, dict):
                continue
            options = q.get("options") or []
            if not isinstance(options, list):
                continue
            q_labels: list[str] = []
            for o in options:
                if isinstance(o, dict):
                    label = o.get("label")
                    if isinstance(label, str):
                        q_labels.append(label)
            # Per-question check: only this question's text counts.
            q_text = q.get("question")
            q_texts = [q_text] if isinstance(q_text, str) else []

            # Issue #787 codex round-7 P2: T1 and T2 triggering labels are
            # computed independently (not via if/elif) because a single
            # question can carry BOTH an exact (Recommended) option (T1)
            # AND a separate confidence-anchoring option (T2, e.g. a
            # "safer" description with no (Recommended) marker). The prior
            # if/elif skipped the T2 check entirely once T1 matched, so the
            # T2 option's label never entered `required_count` — a retry
            # that only filled in the T1 option's evidence silent-passed
            # with the T2 option's claim never verified. T1 still decides
            # the FINAL deny-vs-ask precedence below (unchanged), but both
            # tiers' labels are now pooled into one evidence requirement
            # for the question, consistent with the existing count-based
            # (not exact-label-matched) `required_count` contract.
            t1_triggering: list[str] = []
            if _has_exact_recommended_marker(q_labels):
                # T1: literal (Recommended) / (추천) marker in label.
                # Issue #393: upgraded from ask to deny (hard block).
                # Dedup WITHIN this question only (defends against a single
                # question listing the same label twice) — NOT across
                # questions (issue #787 codex round-3 P2): each question's
                # own text is checked independently, so 2 different
                # questions sharing the same label text each need their
                # own scaffold line.
                t1_triggering = _dedupe_labels(_t1_matching_labels(q_labels))

            t2_triggering: list[str] = []
            if _has_confidence_anchoring(collect_option_texts(options)):
                # T2 (issue #369): confidence-anchoring framing token in
                # label OR description. Soft gate — false-positive risk
                # higher than T1's literal marker.
                #
                # Exclude labels already in t1_triggering: T2's bare
                # `recommend(?:ed|s)?` pattern matches the literal word
                # inside "(Recommended)" too, so an option carrying the
                # exact T1 marker also, incidentally, matches T2. Without
                # this filter the same label would be appended to BOTH
                # t1_labels and t2_labels below, double-counting it in
                # `required_count` and duplicating its scaffold line in
                # the final `t1_labels + t2_labels` deny message — this is
                # not a second, independent claim to verify, just the
                # same marker matching two patterns.
                t2_triggering = [
                    label
                    for label in _dedupe_labels(_t2_matching_labels(options))
                    if label not in t1_triggering
                ]

            combined_triggering = _dedupe_labels(t1_triggering + t2_triggering)
            if combined_triggering:
                # Issue #828: a `Falsified:` line placed in a TRIGGERING
                # option's own `description` field also counts. A
                # PreToolUse hook cannot see the assistant's own preceding
                # prose (confirmed infeasible — see
                # pre-output-falsification-gate's Lane A "A3 INFEASIBILITY
                # NOTE"), so the evidence cannot be moved out of
                # `tool_input` entirely. Splitting it across each
                # triggering option's own `description` instead of
                # concatenating it into one long `question` string keeps
                # `question` short/single-purpose, reducing the
                # long+multiline+non-ASCII payload shape implicated in the
                # harness's malformed-tool-call-emission bug (upstream
                # anthropics/claude-code #69522/#79339/#74800).
                #
                # Scoped to TRIGGERING options only, and computed here
                # (after combined_triggering exists) rather than
                # unconditionally for every option in the question — two
                # codex-review-caught bypasses required this:
                #
                # 1. label excluded from evidence: pulling in
                #    collect_option_texts() (label + description) let a
                #    single triggering option's own LABEL satisfy T1/T2
                #    with zero real probe evidence (single-label mode
                #    accepts "any clean line anywhere"). `label` already
                #    carries the (Recommended)/anchoring MARKER; it must
                #    never also double as the evidence proving that
                #    marker's own premise.
                # 2. non-triggering options excluded: adding EVERY
                #    option's description unconditionally let an unrelated,
                #    non-triggering option's description (with its own
                #    unrelated `Falsified:` line) satisfy the gate for a
                #    *different* triggering option in single-label mode —
                #    the actual triggering option's premise was never
                #    falsified at all. Live probe: a lone `(Recommended)`
                #    option with no description, paired with a sibling
                #    option whose description read
                #    "Falsified: unrelated ... premise survives because
                #    n/a" → silent pass (rc=0, empty stdout) before this
                #    fix. Only a triggering option's OWN description can
                #    supply its evidence.
                # 3. index identity, not label-string matching (codex
                #    round-3 P2): matching via
                #    `_normalize_label(label) in triggering_norm` conflates
                #    two DIFFERENT options that happen to share the same
                #    normalized label — e.g. "Option  A" (double space,
                #    T2-triggering) and "Option A" (non-triggering). The
                #    non-triggering option's unrelated `Falsified:`
                #    description satisfied the triggering option's gate.
                #    Live probe: options=[{label:"Option  A",
                #    description:"safer path overall"}, {label:"Option A",
                #    description:"Falsified: unrelated ... premise
                #    survives because n/a"}] → silent pass (rc=0, empty
                #    stdout) before this fix. `_t1_triggering_indices` /
                #    `_t2_triggering_indices` key on option position in
                #    `options`, which two distinct options can never share.
                t1_idx = _t1_triggering_indices(options)
                t2_idx = _t2_triggering_indices(options) - t1_idx
                triggering_indices = t1_idx | t2_idx
                falsify_texts = list(q_texts)
                for idx, o in enumerate(options):
                    if idx not in triggering_indices:
                        continue
                    if not isinstance(o, dict):
                        continue
                    desc = o.get("description")
                    if isinstance(desc, str):
                        falsify_texts.append(desc)

                # Each label in combined_triggering (issue #787 codex
                # round-4 P1, extended round-7, round-9 P1) must be
                # individually addressed by its own clean line — a scaffold
                # satisfied by deleting a line, or by pasting distinct-but-
                # off-target evidence for only one option, must still block.
                if not _has_falsified_line(falsify_texts, combined_triggering):
                    if t1_triggering:
                        any_t1_violation = True
                        t1_labels.extend(t1_triggering)
                    if t2_triggering:
                        any_t2_violation = True
                        t2_labels.extend(t2_triggering)
            elif _has_recommended_marker(q_labels):
                # T3 (dead under new precedence — kept for clarity).
                advisory_needed = True

        if any_t1_violation:
            # T1 deny is final — overrides any T2 ask — but the scaffold
            # still aggregates T2 labels too so a question that only
            # violates T2 doesn't reblock on the very next retry. No
            # cross-question dedup here (see comment above).
            _record_block_telemetry(payload.get("session_id"), _fire_ledger.DECISION_BLOCK)
            _emit_deny(_build_ask_msg(t1_labels + t2_labels))
        elif any_t2_violation:
            _record_block_telemetry(payload.get("session_id"), _fire_ledger.DECISION_ASK)
            _emit_ask(_build_anchoring_ask_msg(t2_labels))
        elif advisory_needed:
            sys.stderr.write(ADVISORY_MSG + "\n")

    elif tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str) and _is_bulk_action_command(command):
            sys.stderr.write(ADVISORY_MSG + "\n")

    return 0




if __name__ == "__main__":
    sys.exit(main())
