#!/usr/bin/env python3
"""PreToolUse advisory: warn before posting hypothesis-form claims to external surfaces.

Public/shared-state writes — PR comments, issue bodies, Slack messages, Notion
pages — train downstream readers (review bots, teammates) on the published
facts. Posting hypothesis-stage thinking to these surfaces creates retraction
and noise cost when the hypothesis turns out to be false.

This hook detects:
  - Bash calls invoking `gh issue/pr comment`, `gh issue/pr create` with a body
    flag (`--body`, `-b`, `--body-file`, `-F`)
  - MCP tool calls writing to chat / docs surfaces (slack send/post,
    notion create_page / update_page)
  - Write tool calls to staging paths (/tmp/*-issue-*.md, /tmp/*-pr-*.md,
    .omc/plans/*.md) when cluster-approval language is found in recent user
    messages (issue #276)

When the body contains hypothesis markers (might / could / potentially / appears
to / is failing / 가설 / 추정), it emits a stderr advisory reminding the user
to verify each factual claim with executed evidence before posting.

The hypothesis scan catches the UNDER-claiming polarity (too uncertain to
publish). The opposite polarity — a confident but false COMPLETION claim
(반영했습니다 / "I've updated the PR") — carries zero hedging tokens and would
pass the hypothesis scan by construction. A separate over-claiming scan
(issue #802) detects first-person modification-completion phrases and reminds
the author to cross-check each claim against a tool call that actually ran.

Additionally, when the body contains author-exempt claim shapes (mapping table
rows or bash code blocks with unverified identifiers) and no verification call
(gh label list / DESCRIBE / <binary> --help) is found in the recent transcript,
it emits a separate advisory (issue #183).

Exits 0 by default — this is an advisory, not a block. Set
`PRAXIS_EXTERNAL_WRITE_STRICT=1` to convert hypothesis-marker detection into a
hard block (exit 2). Set `PRAXIS_OVERCLAIM_STRICT=1` to convert over-claiming
detection into a hard block (exit 2). Set `PRAXIS_AUTHOR_EXEMPT_STRICT=1` to
convert author-exempt detection into a hard block (exit 2). Set
`PRAXIS_CLUSTER_APPROVAL_STRICT=1` to convert cluster-approval staging detection
into a hard block (exit 2).

Uses shlex tokenization (same approach as block-gh-state-all.py / side-effect-scan.py)
so that pattern references inside quoted strings, echo arguments, or comments
are not mistakenly flagged.
"""
from __future__ import annotations

import json
import os
import re
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import TRANSCRIPT_SCAN_LINES, tail_lines  # type: ignore[import-not-found]  # noqa: E402


# ---------------------------------------------------------------------------
# Detection — heuristic markers
# ---------------------------------------------------------------------------

# English hypothesis markers — conservative list to reduce false positives.
HYPOTHESIS_MARKERS_EN = (
    "might ", "could be", "could fail", "could break",
    "potentially", "potential ",
    "appears to", "seems to",
    "likely ", "suspected", "hypothesis",
    "is failing", "is broken",
    "may have", "may be ",
)
HYPOTHESIS_MARKERS_KO = (
    "가설", "추정", "추측", "가능성", "의심됨", "의심된다",
)


# Shared surface detection + body extraction now lives in
# `_lib/_external_write_body.py` (extracted at the 3rd consumer per repo
# convention — see that module's docstring). Aliased to the previous
# private names so call sites and tests stay unchanged.
from _external_write_body import (  # type: ignore[import-not-found]  # noqa: E402
    extract_gh_body as _extract_gh_body,
    extract_mcp_body as _extract_mcp_body,
    is_gh_external_write as _is_gh_external_write,
    is_mcp_external_write as _is_mcp_external_write,
)


# ---------------------------------------------------------------------------
# Hypothesis marker scan
# ---------------------------------------------------------------------------

def _has_hypothesis_marker(body: str) -> bool:
    if not body:
        return False
    lower = body.lower()
    if any(marker in lower for marker in HYPOTHESIS_MARKERS_EN):
        return True
    return any(marker in body for marker in HYPOTHESIS_MARKERS_KO)


# ---------------------------------------------------------------------------
# Over-claiming (false-completion) marker scan (issue #802)
# ---------------------------------------------------------------------------
# The opposite polarity of the hypothesis scan. A confident but false completion
# claim ("§3 제안도 반영했습니다" when §3 was never executed) has no hedging
# token, so the hypothesis scan passes it by construction. Retrospect
# 2026-07-17 observed exactly this: a review reply asserted two review items
# were incorporated when only one ran; the false sentences mapped 1:1 onto the
# un-executed items (structural projection of the review ask onto the draft).
#
# Full claim↔tool-call verification is an NL-matching problem and out of scope
# (the issue is explicit about this). The realistic gate is an advisory that
# fires on first-person modification-completion phrases and asks the author to
# self-cross-check each claim against a tool call that actually ran.
#
# Scope is the review-reply projection verb family (reflected / organized /
# fixed / changed / replaced / updated / completed). The Korean markers require
# an active completion inflection (했/함) so that verification/status nouns
# ("검증 완료: 819 rows") and the passive 됐 form ("prod에 반영됐습니다" — a
# factual observation, and the applied-on-branch check's domain) do not surface.
_OVERCLAIM_MARKERS_KO = (
    "반영했", "반영함",
    "정리했", "정리함",
    "바꿨",
    "변경했",
    "수정했",
    "완료했", "완료함",
    "교체했",
    "추가했",
    "제거했",
    "삭제했",
    "업데이트했",
    "갱신했",
)

# English first-person completion claims. Anchored to "I've / I have + verb" or
# "verb + the <artifact>" so bare ambiguous past tense ("the value changed")
# does not false-positive.
_OVERCLAIM_MARKERS_EN = (
    re.compile(
        r"\bi(?:'ve|\s+have)\s+"
        r"(?:updated|fixed|changed|rewrote|rewritten|reorganized|replaced|"
        r"addressed|incorporated|revised|adjusted|reflected|removed|added|edited)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:updated|rewrote|rewritten|reorganized|revised|edited|replaced)\s+"
        r"the\s+(?:pr|body|description|comment|doc|docstring)\b",
        re.IGNORECASE,
    ),
)


def _has_overclaim_marker(body: str) -> bool:
    if not body:
        return False
    if any(marker in body for marker in _OVERCLAIM_MARKERS_KO):
        return True
    return any(p.search(body) for p in _OVERCLAIM_MARKERS_EN)


# ---------------------------------------------------------------------------
# Author-exempt claim-shape detection (issue #183)
# ---------------------------------------------------------------------------
# Detects unverified identifiers the agent authored itself: mapping table rows
# with CLI flags or label names, and bash code blocks with column/table names.
# No hypothesis hedging language is required — the pattern is structural.
#
# Verification is category-specific: a gh label list only clears label claims;
# a DESCRIBE only clears schema/column claims; --help only clears flag claims.
# Mixed bodies require matching evidence per category (Codex P2 fix).

# Identifier categories — used to match identifiers against verification commands.
_CAT_FLAG   = "flag"    # e.g. --cli-flag
_CAT_LABEL  = "label"   # e.g. type:docs
_CAT_SCHEMA = "schema"  # e.g. snake_col, schema.table, `backtick-id`

# Markdown table data row: at least two pipe-delimited cells.
_MD_TABLE_ROW_RE = re.compile(r"^\|(.+\|)+", re.MULTILINE)

# Identifier patterns checked inside table cells (high specificity).
_CELL_FLAG_RE    = re.compile(r"--[a-z][a-z0-9-]{1,30}")
_CELL_LABEL_RE   = re.compile(r"\b[a-z][a-z0-9-]+:[a-z][a-z0-9][a-z0-9-]*\b")
_CELL_BACKTICK_RE = re.compile(r"`[a-z][a-z0-9_-]{2,}`")

# Bash / SQL / any language code block.
_CODE_BLOCK_RE = re.compile(r"```\w*\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# Inside code blocks: add snake_case column names and schema-qualified tables.
_CODE_SNAKE_RE    = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*){1,}\b")
_CODE_QUALIFIED_RE = re.compile(r"\b[a-z][a-z0-9_]+\.[a-z][a-z0-9_]+\b")

# Verification regex patterns per category.
_VERIF_BY_CAT: dict[str, tuple[re.Pattern, ...]] = {
    _CAT_FLAG: (
        re.compile(r"\bgh\s+\w[\w-]*\s+(?:--help|-h)\b", re.IGNORECASE),
        re.compile(r"\b\w[\w.-]{1,}\s+--help\b", re.IGNORECASE),
    ),
    _CAT_LABEL: (
        re.compile(r"\bgh\s+label\s+list\b", re.IGNORECASE),
    ),
    _CAT_SCHEMA: (
        re.compile(r"\bDESCRIBE\s+\w", re.IGNORECASE),
        re.compile(r"\bSHOW\s+COLUMNS\b", re.IGNORECASE),
    ),
}


def _is_separator_row(row: str) -> bool:
    """True for pure separator rows like |---|:---:|---| (no data)."""
    return bool(re.fullmatch(r"[\|\s\-:=+]+", row.strip()))


def _extract_categorized_identifiers(body: str) -> dict[str, list[str]]:
    """Return {category: [identifiers]} from mapping table cells and code blocks."""
    result: dict[str, list[str]] = {_CAT_FLAG: [], _CAT_LABEL: [], _CAT_SCHEMA: []}

    # Markdown table data rows
    for m in _MD_TABLE_ROW_RE.finditer(body):
        row = m.group(0)
        if _is_separator_row(row):
            continue
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        for cell in cells:
            if not cell:
                continue
            result[_CAT_FLAG].extend(_CELL_FLAG_RE.findall(cell))
            result[_CAT_LABEL].extend(_CELL_LABEL_RE.findall(cell))
            result[_CAT_SCHEMA].extend(_CELL_BACKTICK_RE.findall(cell))

    # Code blocks (any language tag)
    for m in _CODE_BLOCK_RE.finditer(body):
        block = m.group(1)
        result[_CAT_FLAG].extend(_CELL_FLAG_RE.findall(block))
        result[_CAT_LABEL].extend(_CELL_LABEL_RE.findall(block))
        result[_CAT_SCHEMA].extend(_CELL_BACKTICK_RE.findall(block))
        result[_CAT_SCHEMA].extend(_CODE_SNAKE_RE.findall(block))
        result[_CAT_SCHEMA].extend(_CODE_QUALIFIED_RE.findall(block))

    return {k: v for k, v in result.items() if v}


def _recent_bash_commands(transcript_path: str) -> list[str]:
    """Return recent Bash command strings from the last N transcript JSONL lines."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return []
    cmds: list[str] = []
    for line in tail_lines(transcript_path, TRANSCRIPT_SCAN_LINES):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message") or {}
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for block in (msg.get("content") or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                inp = block.get("input") or {}
                cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                if isinstance(cmd, str) and cmd.strip():
                    cmds.append(cmd)
    return cmds


def _unverified_identifiers(
    categorized: dict[str, list[str]], commands: list[str]
) -> list[str]:
    """Return a sample of identifiers whose category has no matching verification.

    Each category is checked independently — a gh label list only clears label
    claims; a DESCRIBE only clears schema/column claims; --help only clears flag
    claims. Returns up to 2 identifiers per unverified category.
    """
    unverified: list[str] = []
    for cat, ids in categorized.items():
        patterns = _VERIF_BY_CAT.get(cat, ())
        cat_verified = any(
            pat.search(cmd)
            for cmd in commands
            for pat in patterns
        )
        if not cat_verified:
            unverified.extend(list(dict.fromkeys(ids))[:2])
    return unverified


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

ADVISORY_MESSAGE = (
    "REMINDER (External-Surface Write Falsification): hypothesis markers "
    "detected in body.\n"
    "Before posting, verify:\n"
    "  • Has each factual claim been verified by executed evidence "
    "(query output, test pass, log inspection)?\n"
    "  • Is your verification's own premise (key, filter, schema, "
    "dimensional layout) falsified?\n"
    "  • If the verification loop has not closed, write to /tmp/ or "
    ".omc/plans/ instead.\n"
    "Set PRAXIS_EXTERNAL_WRITE_STRICT=1 to convert this advisory into a "
    "hard block (exit 2).\n"
)

OVERCLAIM_ADVISORY = (
    "REMINDER (External-Surface Write / Over-Claiming): body contains "
    "first-person completion claims (반영했 / 정리했 / 수정했 · \"I've "
    "updated\") asserting work is done.\n"
    "The falsification gate scans for UNDER-claiming (hypothesis hedging); "
    "an over-claim — a confident but false 'done' claim — carries no hedging "
    "token and passes by construction (issue #802).\n"
    "Cross-check EACH completion claim against a tool call that actually ran "
    "this session. Remove or correct any claim whose action was not executed — "
    "execution scope silently narrows while the draft still matches the full "
    "ask.\n"
    "Set PRAXIS_OVERCLAIM_STRICT=1 to convert this advisory into a hard block "
    "(exit 2).\n"
)

AUTHOR_EXEMPT_ADVISORY = (
    "REMINDER (External-Surface Write / Author-Exempt): body contains "
    "mapping table or code-block identifiers ({identifiers}) with no "
    "verification call found in recent transcript.\n"
    "Own-authored labels, columns, and flags are in scope — run "
    "gh label list / DESCRIBE / <binary> --help before publishing.\n"
    "Set PRAXIS_AUTHOR_EXEMPT_STRICT=1 to convert this advisory into a "
    "hard block (exit 2).\n"
)


# ---------------------------------------------------------------------------
# Applied-on-branch claim detection (issue #656)
# ---------------------------------------------------------------------------
# "X applied/deployed/blocked on branch Y" published to an external surface is
# a reference-frame claim: existence on ref A is not application on ref B
# (stacked-PR base, dev-commit-date != prod-apply-date). Such a claim requires
# reachability evidence in the recent transcript — a generic PR state query is
# NOT sufficient (the 2026-05-15 incident ran `gh pr view --json state` and
# still mis-released 3 changes).

# Lookarounds (not \b) so Korean particles ("dev에", "prod에서") match.
_BRANCH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(dev|prod|main|master|release)(?![A-Za-z0-9_])"
    r"|브랜치|(?<![A-Za-z0-9_])branch(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
# `released` is deliberately absent — it false-positives on lock/memory
# release prose ("released the lock on the main thread") and double-counts
# with the `release` branch token; applied/deployed/landed + 배포/반영 cover
# the deploy sense.
_APPLIED_TOKEN_RE = re.compile(
    r"\b(applied|deployed|landed)\b|\bblocked\s+since\b"
    r"|적용\s*(됐|했|됨|되었|완료)|배포\s*(됐|됨|되었|완료)|반영\s*(됐|됨|되었|완료)|차단\s*(됐|됨|되었)",
    re.IGNORECASE,
)
# `\bwithout\b` is deliberately absent — genuine deploy claims routinely carry
# it ("Deployed to prod without incident") and would be silently suppressed.
# `fail` stays: "prod 배포 실패" is a failure report, not an applied claim;
# the cost (an applied claim co-occurring with "failing" prose is missed) is
# accepted and documented.
_APPLIED_NEGATION_RE = re.compile(
    r"\bnot\b|n't\b|\byet\b|아직|않|못\s|안\s|없|실패|fail",
    re.IGNORECASE,
)
# Mirrored in hooks/completion-verify/merge-state-claim-gate (2 copies — DRY
# extraction deferred to a 3rd consumer per repo convention). The baseRefName
# arm requires the `--json` query context AND a `state` field in the same
# command (either order): a bare-token match would let `grep baseRefName
# impl.py` silently clear a genuine claim, and a baseRefName-only query never
# confirms the PR actually merged — the canonical probe is
# `--json state,baseRefName`. `--contains` tolerates both short (-r) and long
# (--merged) intervening flags.
_REACHABILITY_VERIF_RE = re.compile(
    r"merge-base\s+--is-ancestor"
    r"|--json[^|;&\n]*(?:state[^|;&\n]*baseRefName|baseRefName[^|;&\n]*state)"
    r"|\bbranch\b\s+(?:--?\w[\w-]*\s+)*--contains",
    re.IGNORECASE,
)

APPLIED_CLAIM_ADVISORY = (
    "REMINDER (External-Surface Write / Applied-on-Branch): body asserts a "
    "change is applied/deployed/blocked on a branch, but no reachability "
    "probe (`git merge-base --is-ancestor`, `gh pr view --json "
    "state,baseRefName`, `git branch --contains`) is found in the recent "
    "transcript.\n"
    "Existence on ref A is not application on ref B — PR state=MERGED does "
    "not prove the change reached the target branch (issue #656).\n"
    "Run the reachability probe and cite its output inline "
    "(`Probe: <command> -> <output>`) before publishing.\n"
    "Set PRAXIS_APPLIED_CLAIM_STRICT=1 to convert this advisory into a "
    "hard block (exit 2).\n"
)


def _has_applied_on_branch_claim(body: str) -> bool:
    """True if any body line pairs a branch token with an applied-state token
    and is not negated (line-localized, same approach as the merge-state gate)."""
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or _APPLIED_NEGATION_RE.search(line):
            continue
        if _BRANCH_TOKEN_RE.search(line) and _APPLIED_TOKEN_RE.search(line):
            return True
    return False


# ---------------------------------------------------------------------------
# Cluster-approval language detection (issue #276)
# ---------------------------------------------------------------------------
# Detects the pattern: user approves a cluster of tasks in bulk ("all 4 together",
# "1+3 같이"), then the agent writes directly to a staging path without
# per-action surfacing. CLAUDE.md rule: "No Approval Transfer Across Companion PRs".

_USER_MSG_SCAN_COUNT = 5

# Patterns that indicate a cluster/bulk approval by the user.
CLUSTER_APPROVAL_PATTERNS = (
    # English — exact phrases from the issue spec
    re.compile(r"\b\d+\s+buckets?\s+together\b", re.IGNORECASE),
    re.compile(r"\ball\s+\d+\s+as\s+separate\b", re.IGNORECASE),
    re.compile(r"\bas\s+approved\s+above\b", re.IGNORECASE),
    re.compile(r"\bcluster\s+(?:i|we)\s+approved\b", re.IGNORECASE),
    # "all N ... approv*" within 50 chars (e.g. "all 4 approved", "all 4 go ahead")
    re.compile(r"\ball\s+\d+\b.{0,50}\bapprov\w*", re.IGNORECASE),
    # Korean — from issue spec
    re.compile(r"\d+개\s*모두"),
    re.compile(r"\d+\s*\+\s*\d+\s*같이"),
    re.compile(r"모두\s*승인"),
)

# Staging paths: intermediate draft files, not final external-surface targets.
STAGING_PATH_PATTERNS = (
    re.compile(r"/tmp/[^/\s]*-issue-[^/\s]*\.md$"),
    re.compile(r"/tmp/[^/\s]*-pr-[^/\s]*\.md$"),
    re.compile(r"\.omc/plans/[^/\s]*\.md$"),
)

CLUSTER_APPROVAL_ADVISORY = (
    "REMINDER (External-Surface Write / Cluster-Approval): cluster-approval "
    "language detected in a recent user message.\n"
    "Cluster approvals (\"all N together\", \"1+3 같이\", \"as approved above\") "
    "do NOT auto-transfer to per-action staging writes.\n"
    "Each child mutation (issue draft, PR body) requires its own explicit "
    "per-action surfacing via AskUserQuestion.\n"
    "Surface a dedicated AskUserQuestion for this specific action before "
    "writing to a staging path.\n"
    "Set PRAXIS_CLUSTER_APPROVAL_STRICT=1 to convert this advisory into a "
    "hard block (exit 2).\n"
)


def _recent_user_messages(transcript_path: str, count: int) -> list[str]:
    """Return text from the last `count` user messages in the transcript.

    Scans the last TRANSCRIPT_SCAN_LINES JSONL entries in reverse so that
    the most-recent user messages are returned first (index 0 = most recent).
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return []
    msgs: list[str] = []
    for line in reversed(tail_lines(transcript_path, TRANSCRIPT_SCAN_LINES)):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message") or {}
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            text = "\n".join(parts)
        else:
            continue
        if text.strip():
            msgs.append(text)
        if len(msgs) >= count:
            break
    return msgs


def _has_cluster_approval(user_messages: list[str]) -> bool:
    """Return True if any recent user message contains cluster-approval language."""
    for msg in user_messages:
        for pat in CLUSTER_APPROVAL_PATTERNS:
            if pat.search(msg):
                return True
    return False


def _is_staging_path(file_path: str) -> bool:
    """Return True if file_path matches a recognized staging path pattern."""
    if not file_path:
        return False
    return any(pat.search(file_path) for pat in STAGING_PATH_PATTERNS)


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0  # fail-open on malformed stdin

    tool_name = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input", {}) or {}
    transcript_path = payload.get("transcript_path", "") or ""

    # --- Collect all bodies from external write surfaces ---
    all_bodies: list[str] = []

    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
        if not command.strip():
            return 0
        command = command.replace("\\\n", " ")
        tokens = safe_tokenize(command)
        if not tokens:
            return 0
        for argv in iter_command_starts(tokens):
            if _is_gh_external_write(argv):
                candidate = _extract_gh_body(argv)
                if candidate is not None:
                    all_bodies.append(candidate)
    elif _is_mcp_external_write(tool_name):
        mcp_body = _extract_mcp_body(tool_input)
        if mcp_body:
            all_bodies.append(mcp_body)
    elif tool_name == "Write":
        # --- Check 3: cluster-approval + staging path (issue #276) ---
        file_path = tool_input.get("file_path", "") or ""
        if _is_staging_path(file_path):
            user_msgs = _recent_user_messages(transcript_path, _USER_MSG_SCAN_COUNT)
            if _has_cluster_approval(user_msgs):
                sys.stderr.write(CLUSTER_APPROVAL_ADVISORY)
                if os.environ.get("PRAXIS_CLUSTER_APPROVAL_STRICT") == "1":
                    return 2
        return 0
    else:
        return 0

    if not all_bodies:
        return 0

    exit_code = 0

    # --- Check 1: hypothesis markers (existing behavior) ---
    for b in all_bodies:
        if _has_hypothesis_marker(b):
            sys.stderr.write(ADVISORY_MESSAGE)
            if os.environ.get("PRAXIS_EXTERNAL_WRITE_STRICT") == "1":
                exit_code = 2
            break

    # --- Check 5: over-claiming completion phrases (issue #802) ---
    # Opposite polarity of Check 1 — runs independently so a body can trip both.
    for b in all_bodies:
        if _has_overclaim_marker(b):
            sys.stderr.write(OVERCLAIM_ADVISORY)
            if os.environ.get("PRAXIS_OVERCLAIM_STRICT") == "1":
                exit_code = 2
            break

    # --- Check 2: author-exempt claim-shape (issue #183) ---
    combined = "\n".join(all_bodies)
    categorized = _extract_categorized_identifiers(combined)
    commands: list[str] | None = None
    if categorized:
        commands = _recent_bash_commands(transcript_path)
        unverified = _unverified_identifiers(categorized, commands)
        if unverified:
            sample = ", ".join(list(dict.fromkeys(unverified))[:3])
            sys.stderr.write(AUTHOR_EXEMPT_ADVISORY.format(identifiers=sample))
            if os.environ.get("PRAXIS_AUTHOR_EXEMPT_STRICT") == "1":
                exit_code = 2

    # --- Check 4: applied-on-branch claim without reachability probe (#656) ---
    if _has_applied_on_branch_claim(combined):
        if commands is None:
            commands = _recent_bash_commands(transcript_path)
        if not any(_REACHABILITY_VERIF_RE.search(cmd) for cmd in commands):
            sys.stderr.write(APPLIED_CLAIM_ADVISORY)
            if os.environ.get("PRAXIS_APPLIED_CLAIM_STRICT") == "1":
                exit_code = 2

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
