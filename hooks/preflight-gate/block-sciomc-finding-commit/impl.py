#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `git commit` after sciomc/reviewer finding
without user-design consensus re-fetch.

Backs the rule: user-stated design (PR body / issue body / direct utterance)
is RATIFIED — AI analysis findings (sciomc Stage N, deep-dive, review finds,
scientist agent output) are DRAFTS. Surface findings to the user, never
auto-flip the design via direct commit.

Background:
  Four converging memory rules encode the same root cause family
  (falsify-before-lock, self-authored option scope, recommendation lock,
  consensus re-fetch before lock). Two CLAUDE.md promotions already done
  (Output-Block-Level Falsification Gate / Self-Falsify Before Recommendation
  Lock). Per prompt-layer retrieval failure threshold, 3+ generations require
  hook escalation, not another memo. This hook enforces the gate at the
  commit checkpoint.

Concrete retrospect pattern (praxis issue #374):
  1. User's PR body specifies a literal value or sibling-convention design
     choice — the ratified design, written on a shared surface
  2. A subsequent sciomc Stage N or reviewer analysis surfaces a finding
     that the user's literal/design is "sibling-deviant" or sub-optimal
  3. AI auto-commits a flip of the user's literal without re-reading the
     PR body or asking for approval — treating its own analysis output
     as a ratified directive
  4. User redirects back to the originally-stated design → revert
  5. Cost: extra commits, PR body rewrite, reviewer-timeline noise,
     occasionally a duplicate follow-up issue spawned from the same
     analysis-as-directive pipeline

Block conditions (ALL must hold):
  (a) Tool is Bash with `git commit` (not amend/merge/revert/cherry-pick)
  (b) Recent transcript tail (last ~200 lines) contains a sciomc/finding
      marker: "sibling-deviant", "Stage N finding/analysis/complete",
      "sciomc", "[FINDING:", "[STAGE_COMPLETE:", "scientist-agent",
      "deep-dive", "cross-validation", "의미 mismatch"
  (c) No `gh pr view ... --json body` OR `gh issue view ... --json body` OR
      explicit ratification token was emitted AFTER the most recent finding
      marker in the transcript tail

Allow conditions (escape hatches):
  - Commit message contains [user-approved] or [ratified-by-user] token
  - CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 env var
  - git commit --amend / git merge / git revert / git cherry-pick
  - --allow-empty
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0  # fail-open on malformed payload

    if os.environ.get("CLAUDE_HOOK_BYPASS_SCIOMC_GATE") == "1":
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    if not _is_content_git_commit(command):
        return 0

    if _has_ratification_token(command):
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0  # no transcript → cannot enforce

    tail = _read_transcript_tail(transcript_path, max_lines=200, max_bytes=50 * 1024 * 1024)
    if tail is None:
        return 0

    finding_indices = _find_marker_indices(tail, _FINDING_MARKERS)
    if not finding_indices:
        return 0  # no finding context → allow

    last_finding_idx = max(finding_indices)
    after_finding = tail[last_finding_idx:]

    if _has_consensus_refetch(after_finding):
        return 0

    matched = sorted({m.group(0).strip() for m in _iter_marker_matches(tail, _FINDING_MARKERS)})[:3]
    _emit_block_message(matched)
    return 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GIT_COMMIT_RE = re.compile(r"\bgit\s+commit\b")
_GIT_NON_CONTENT_RE = re.compile(r"\bgit\s+(merge|rebase|cherry-pick|revert)\b")
_AMEND_RE = re.compile(r"--amend\b")
_ALLOW_EMPTY_RE = re.compile(r"--allow-empty\b")
_RATIFICATION_TOKEN_RE = re.compile(
    r"\[(?:user-approved|ratified-by-user|user-ratified)\]",
    re.IGNORECASE,
)
_COMMIT_MSG_RE = re.compile(r"""-m\s+(?:"((?:[^"\\]|\\.)*)"|'([^']*)')""")

_FINDING_MARKERS = (
    re.compile(r"\bsibling[- ]deviant\b", re.IGNORECASE),
    re.compile(r"\bStage\s*\d\s*(?:분석|finding|analysis|결과|complete)", re.IGNORECASE),
    re.compile(r"\bsciomc\b", re.IGNORECASE),
    re.compile(r"\[FINDING:"),
    re.compile(r"\[STAGE_COMPLETE:"),
    re.compile(r"\bscientist[- ]agent\b", re.IGNORECASE),
    re.compile(r"\breview[- ]finds?\b", re.IGNORECASE),
    re.compile(r"\bdeep[- ]dive\b", re.IGNORECASE),
    re.compile(r"\bcross[- ]validation\b", re.IGNORECASE),
    re.compile(r"\b의미\s*mismatch\b", re.IGNORECASE),
    re.compile(r"\b의미\s*충돌\b"),
)

_CONSENSUS_REFETCH_MARKERS = (
    re.compile(r"\bgh\s+pr\s+view\s+[^\n]*--json\s+[^\n]*body", re.IGNORECASE),
    re.compile(r"\bgh\s+issue\s+view\s+[^\n]*--json\s+[^\n]*body", re.IGNORECASE),
    re.compile(r"\bconsensus\s+re[- ]?fetch", re.IGNORECASE),
    re.compile(r"\bre[- ]?read\s+(?:PR|issue)\s+body", re.IGNORECASE),
    re.compile(r"\buser-stated\s+design\b", re.IGNORECASE),
    re.compile(r"\[ratified-by-user\]", re.IGNORECASE),
    re.compile(r"\[user-approved\]", re.IGNORECASE),
)


def _is_content_git_commit(command: str) -> bool:
    if not _GIT_COMMIT_RE.search(command):
        return False
    if _AMEND_RE.search(command):
        return False
    if _GIT_NON_CONTENT_RE.search(command):
        return False
    if _ALLOW_EMPTY_RE.search(command):
        return False
    return True


def _has_ratification_token(command: str) -> bool:
    msg_match = _COMMIT_MSG_RE.search(command)
    if not msg_match:
        return False
    msg = msg_match.group(1) or msg_match.group(2) or ""
    return bool(_RATIFICATION_TOKEN_RE.search(msg))


def _read_transcript_tail(path: str, max_lines: int, max_bytes: int) -> str | None:
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size > max_bytes:
            return None
        text = p.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    lines = text.strip().split("\n")
    return "\n".join(lines[-max_lines:])


def _find_marker_indices(text: str, patterns: tuple[re.Pattern, ...]) -> list[int]:
    return [m.start() for pat in patterns for m in pat.finditer(text)]


def _iter_marker_matches(text: str, patterns: tuple[re.Pattern, ...]):
    for pat in patterns:
        yield from pat.finditer(text)


def _has_consensus_refetch(text: str) -> bool:
    return any(pat.search(text) for pat in _CONSENSUS_REFETCH_MARKERS)


def _emit_block_message(matched_markers: list[str]) -> None:
    sys.stderr.write(
        "\n".join(
            [
                "BLOCKED: `git commit` after sciomc/reviewer finding without user-design consensus re-fetch.",
                "",
                f"Detected finding markers in recent transcript: {', '.join(matched_markers) or '(none)'}",
                "",
                "Rule: User-stated design (PR body / issue body) is RATIFIED.",
                "AI analysis findings (sciomc Stage N, deep-dive, review finds, scientist agent)",
                "are DRAFTS — surface to user, never auto-flip the design via direct commit.",
                "",
                "Pattern (praxis issue #374):",
                "  AI flips a user-stated literal/design based on a sciomc/reviewer finding,",
                "  without re-reading the PR body first. User redirects back → revert + extra",
                "  commits + PR body rewrite + reviewer-timeline noise.",
                "",
                "Resolve by one of:",
                "  1. Re-fetch the user's stated design with:",
                "       gh pr view <N> --json body --jq .body",
                "       gh issue view <N> --json body --jq .body",
                "     Compare against the user's stated design. If conflicting, surface via",
                "     AskUserQuestion BEFORE committing.",
                "  2. If user explicitly approved the change in this session, add token",
                "     [user-approved] or [ratified-by-user] to the commit message.",
                "  3. One-off bypass: prefix with CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1",
                "",
                "CLAUDE.md: Output-Block-Level Falsification Gate / Self-Falsify Before Recommendation Lock",
            ]
        )
        + "\n"
    )


if __name__ == "__main__":
    sys.exit(main())
