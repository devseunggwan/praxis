#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `gh issue create` without prior duplicate
search in the same session.

Backs CLAUDE.md "GitHub Issue Hygiene": before creating any issue, run
`gh search issues '<keywords>' --repo <repo>` to detect duplicates. Memory-
only enforcement has failed; this hook intercepts at the create checkpoint.

Concrete retrospect source (2026-05-21, Hub #2242 retrospect):
  AI created Hub #2245 ("shopby_v2 brands_lookup CTE pattern") while Hub
  #2243 (products_src catalog gap) already covered the same root-cause scope.
  User redirect: "기존에 PR이 존재했는데 왜 또 만드나요" → /cancel #2245.

Block conditions (either triggers a block):
  (a) `gh issue create` invoked with no prior `gh search issues` / `gh issue
      list` / `gh issue view` in transcript tail at all
  (b) Prior searches exist but none of their args overlap with any keyword
      extracted from the new issue's --title

Allow conditions (escape hatches):
  - --title contains [dup-checked] or [no-search-needed] token
  - Personal-repo write (--repo devseunggwan/...) — low blast-radius
  - CLAUDE_HOOK_BYPASS_DUP_GATE=1 env var
  - Title has no extractable keywords ≥4 chars (cannot enforce)

Keyword extraction:
  Strip Conventional Commits prefix (`feat(scope):`, `fix:`), lowercase,
  split on word boundaries, drop stop words and tokens <4 chars. Match if
  ANY remaining keyword appears literally in a prior search/list command.
  (Single overlap is a weak gate but catches the most damaging case —
  completely cold issue creation.)
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
        return 0

    if os.environ.get("CLAUDE_HOOK_BYPASS_DUP_GATE") == "1":
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    if not _GH_ISSUE_CREATE_RE.search(command):
        return 0

    # Personal-repo escape hatch.
    repo_match = _REPO_FLAG_RE.search(command)
    if repo_match and _PERSONAL_REPO_RE.match(repo_match.group(1)):
        return 0

    title = _extract_title(command)
    if _DUP_TOKEN_RE.search(title):
        return 0

    keywords = _extract_keywords(title)
    if not keywords:
        return 0  # no usable keywords → cannot enforce

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0

    tail = _read_transcript_tail(transcript_path, max_lines=400, max_bytes=50 * 1024 * 1024)
    if tail is None:
        return 0

    search_cmds = _SEARCH_CMD_RE.findall(tail.lower())

    if not search_cmds:
        _emit_no_search_block(keywords)
        return 2

    overlap = any(
        any(tok in cmd for tok in keywords)
        for cmd in search_cmds
    )
    if overlap:
        return 0

    _emit_no_overlap_block(keywords, search_cmds[-3:])
    return 2


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GH_ISSUE_CREATE_RE = re.compile(r"\bgh\s+issue\s+create\b")
_REPO_FLAG_RE = re.compile(r"--repo\s+(\S+)")
_PERSONAL_REPO_RE = re.compile(r"^devseunggwan/", re.IGNORECASE)

_TITLE_RE = re.compile(
    r"""--title\s+(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S+))""",
)
_DUP_TOKEN_RE = re.compile(
    r"\[(?:dup-checked|no-search-needed|dup-verified)\]",
    re.IGNORECASE,
)
_CC_PREFIX_RE = re.compile(r"^[a-z]+(?:\([^)]+\))?:\s*", re.IGNORECASE)
_TOKEN_SPLIT_RE = re.compile(r"[\s\-_/,.()\[\]'\"#!?]+")

_STOP_WORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "into", "after", "before",
        "this", "that", "those", "these", "have", "has", "had", "been",
        "feat", "fix", "docs", "chore", "refactor", "test", "perf", "ci", "build", "style",
        "add", "remove", "update", "create", "delete", "fixes", "closes",
        "use", "via", "per", "ref",
    }
)

_SEARCH_CMD_RE = re.compile(
    r"\bgh\s+(?:search\s+issues|issue\s+list|issue\s+view)\b[^\n]*",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_title(command: str) -> str:
    m = _TITLE_RE.search(command)
    if not m:
        return ""
    return m.group(1) or m.group(2) or m.group(3) or ""


def _extract_keywords(title: str) -> list[str]:
    body = _CC_PREFIX_RE.sub("", title).lower()
    tokens = [t for t in _TOKEN_SPLIT_RE.split(body) if t]
    return [t for t in tokens if len(t) >= 4 and t not in _STOP_WORDS]


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


def _emit_no_search_block(keywords: list[str]) -> None:
    sys.stderr.write(
        "\n".join(
            [
                "BLOCKED: `gh issue create` without any prior `gh search issues` / `gh issue list`.",
                "",
                f"Title keywords: {', '.join(keywords[:6])}",
                "",
                "Rule: CLAUDE.md → GitHub Issue Hygiene",
                "  Before creating any issue: gh search issues '<keywords>' --repo <repo>",
                "  (open AND closed). Ask user if ambiguous. Never create duplicates.",
                "",
                "Hub #2242 retrospect (2026-05-21):",
                "  AI created Hub #2245 (shopby_v2 brands_lookup CTE) while Hub #2243",
                "  (products_src catalog gap) already covered the same root-cause scope.",
                "  User redirect: '기존에 PR이 존재했는데 왜 또 만드나요' → /cancel.",
                "",
                "Resolve by one of:",
                "  1. Run a search FIRST:",
                "       gh search issues '<keyword>' --repo <repo>",
                "       gh issue list --repo <repo> --search '<keyword>'",
                "  2. If duplicate verified outside this session, add token [dup-checked]",
                "     to --title (e.g. 'feat(x): foo bar [dup-checked]').",
                "  3. One-off bypass: prefix with CLAUDE_HOOK_BYPASS_DUP_GATE=1",
            ]
        )
        + "\n"
    )


def _emit_no_overlap_block(keywords: list[str], recent_searches: list[str]) -> None:
    sys.stderr.write(
        "\n".join(
            [
                "BLOCKED: `gh issue create` — prior searches exist but none overlap with title keywords.",
                "",
                f"Title keywords: {', '.join(keywords[:6])}",
                "Recent search commands found in transcript (no keyword overlap):",
                *[f"  - {s[:120]}" for s in recent_searches],
                "",
                "Rule: CLAUDE.md → GitHub Issue Hygiene",
                "  Searching for unrelated keywords does not satisfy the duplicate-check",
                "  requirement. The search must use keywords semantically overlapping with",
                "  the new issue's scope.",
                "",
                "Resolve by one of:",
                "  1. Run a targeted search using your title's keywords:",
                f"       gh search issues '{' '.join(keywords[:2])}' --repo <repo>",
                "  2. Add [dup-checked] to --title if duplicate verified outside session.",
                "  3. One-off bypass: prefix with CLAUDE_HOOK_BYPASS_DUP_GATE=1",
            ]
        )
        + "\n"
    )


if __name__ == "__main__":
    sys.exit(main())
