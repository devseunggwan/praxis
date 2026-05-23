#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `gh issue create` without prior duplicate
search in the same session.

Backs CLAUDE.md "GitHub Issue Hygiene": before creating any issue, run
`gh search issues '<keywords>' --repo <repo>` to detect duplicates. Memory-
only enforcement has failed; this hook intercepts at the create checkpoint.

Concrete retrospect pattern (praxis issue #374):
  AI creates a follow-up issue from a fresh analysis finding without
  running a duplicate search first; an existing open issue already covers
  the same root-cause scope (often surfaced by a sibling sciomc Stage or
  PR-body "후속 검토" item). User redirects to the existing issue → /cancel.

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
  the title-keyword set has non-empty intersection with the prior search
  command's topic-token set (positionals + `--search` value), where
  flag-arg tokens (`--repo VAL`, `--limit N`, ...) are skipped. Substring
  match against the full command line is INCORRECT because flag values
  (e.g. `--repo acme/widget`) leak keyword fragments into the topic
  corpus — see issue #384.
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

    kw_set = set(keywords)
    overlap = any(kw_set & _extract_search_topic(cmd) for cmd in search_cmds)
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

# Value-taking flags relevant to gh search issues / gh issue list / gh issue
# view. Each consumes the next whitespace-separated token (or `=VALUE`
# inline). Lowercased — _SEARCH_CMD_RE input is `tail.lower()`. Lowercase
# short-flag collisions (-l for both --limit and --label, -a for both
# --assignee and --author, -s for both --state and --search) are all
# value-taking for the relevant subcommands, so collision is harmless.
_VALUE_FLAG_NAMES: frozenset[str] = frozenset({
    "--repo", "-r",
    "--limit", "-l",
    "--state", "-s",
    "--search",
    "--label",
    "--owner",
    "--author", "-a",
    "--assignee",
    "--milestone", "-m",
    "--mention",
    "--language",
    "--app",
    "--match",
    "--sort",
    "--order",
    "--visibility",
    "--commenter",
    "--comments",
    "--created",
    "--updated",
    "--closed",
    "--interactions",
    "--involves",
    "--jq", "-q",
    "--json",
    "--template", "-t",
    "--project", "-p",
    "--reactions",
    "--team-mentions",
})

# Flags whose value is itself topic text (not metadata). Currently only the
# `--search` family on `gh issue list` / `gh pr list`.
_TOPIC_VALUE_FLAGS: frozenset[str] = frozenset({"--search", "-s"})

# Trailing chars to strip from a token. Raw transcript text often glues JSON
# closure chars to the last argv token (e.g. `acme/repo"}`).
_TRAILING_STRIP = '"}],'


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


def _topic_tokens_from(s: str) -> set[str]:
    """Split a topic string into ≥4-char tokens minus stop words.

    Mirrors `_extract_keywords` so the title-keyword and prior-search-topic
    token domains are symmetric for set intersection.
    """
    tokens = [t for t in _TOKEN_SPLIT_RE.split(s.lower()) if t]
    return {t for t in tokens if len(t) >= 4 and t not in _STOP_WORDS}


def _extract_search_topic(cmd: str) -> set[str]:
    """Extract the topic-token set from a prior `gh search issues|issue
    list|issue view` command line.

    Whitespace-splits the command, strips JSON-closure chars glued to the
    last token by raw-transcript matching (`acme/repo"}`), and walks argv
    as a small state machine:

      - `--flag=value` inline: keep `value` only when flag ∈ _TOPIC_VALUE_FLAGS.
      - `--flag value`: consume both tokens; keep `value` only when flag ∈
        _TOPIC_VALUE_FLAGS.
      - Bare `--bool` or `-x` (not value-taking): skip self.
      - Positional: emit its tokens.

    Returns the set of ≥4-char non-stop-word tokens — same domain as
    `_extract_keywords`, so set intersection with title keywords is a
    valid overlap test.
    """
    tokens = cmd.strip().split()
    if len(tokens) < 3 or tokens[0] != "gh":
        return set()
    args = tokens[3:]
    topic: set[str] = set()
    i, n = 0, len(args)
    while i < n:
        tok = args[i].rstrip(_TRAILING_STRIP)
        if not tok:
            i += 1
            continue
        if tok.startswith("--") and "=" in tok:
            flag, _, val = tok.partition("=")
            if flag in _TOPIC_VALUE_FLAGS:
                topic.update(_topic_tokens_from(val))
            i += 1
            continue
        if tok in _VALUE_FLAG_NAMES:
            if tok in _TOPIC_VALUE_FLAGS and i + 1 < n:
                next_val = args[i + 1].rstrip(_TRAILING_STRIP)
                topic.update(_topic_tokens_from(next_val))
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        topic.update(_topic_tokens_from(tok))
        i += 1
    return topic


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
                "Pattern (praxis issue #374):",
                "  AI spawns a new issue from a fresh analysis finding without searching;",
                "  an existing open issue covers the same root cause → /cancel cycle.",
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
