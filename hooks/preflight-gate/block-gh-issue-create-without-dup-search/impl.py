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
  - Personal-repo write — target owner listed in PRAXIS_PERSONAL_REPO_OWNERS
    (comma-separated; unset = no exemption, everyone gets the strict path)
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

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from _transcript import TRANSCRIPT_SCAN_LINES, TranscriptReadError, tail_lines  # type: ignore[import-not-found]  # noqa: E402


@fail_open
def main() -> int:
    """Block `gh issue create` unless a duplicate search appears in the tail.

    Reads the last TRANSCRIPT_SCAN_LINES lines by seeking from the end;
    an unreadable transcript fails open, an empty one blocks.
    """
    payload = read_payload()
    if payload is None:
        return 0

    if os.environ.get("CLAUDE_HOOK_BYPASS_DUP_GATE") == "1":
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")

    # Quote-aware tokenization: locate a real `gh issue create` argv among the
    # command segments. A raw regex over the command string matches the
    # literal inside quoted strings / grep patterns (e.g.
    # `grep -rn "gh issue create" hooks/`) → over-block (issue #514 결함2 MED).
    create_argv = _find_gh_issue_create_argv(command)
    if create_argv is None:
        return 0

    # Personal-repo escape hatch (parsed from the create argv only). The
    # owner list comes from PRAXIS_PERSONAL_REPO_OWNERS — the same env var
    # block-personal-asset-leak reads for the same concept; unset means no
    # exemption for anyone (issue #1156).
    repo = _extract_repo(create_argv)
    if repo and "/" in repo:
        owner = repo.split("/", 1)[0].strip().lower()
        if owner and owner in _personal_owners():
            return 0

    title = _extract_title_from_argv(create_argv)
    if _DUP_TOKEN_RE.search(title):
        return 0

    keywords = _extract_keywords(title)
    if not keywords:
        return 0  # no usable keywords → cannot enforce

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0

    # Read the tail from the end (issue #1279): the former bounded reader
    # loaded up to 50 MB into memory to keep 400 lines — 131 ms and 116 MB RSS
    # on a 36 MB session, inside the shared Bash dispatch budget — and failed
    # open past its bound. `tail_lines` seeks backwards, so the cost is the
    # window itself and there is no bound to fall off. `strict` so a missing
    # or unreadable transcript fails open here, while an empty one — a real
    # "no search ran" — still reaches the block below.
    try:
        tail = "\n".join(tail_lines(transcript_path, TRANSCRIPT_SCAN_LINES, strict=True))
    except TranscriptReadError:
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

def _personal_owners() -> frozenset[str]:
    """Lowercased owner handles from PRAXIS_PERSONAL_REPO_OWNERS; empty = no exemption."""
    raw = os.environ.get("PRAXIS_PERSONAL_REPO_OWNERS", "")
    return frozenset(o.strip().lower() for o in raw.split(",") if o.strip())

# gh global flags that consume the following token as their value, so the
# `issue` / `create` subcommand walk skips both (e.g. `gh -R o/r issue create`).
_GH_GLOBAL_VALUE_FLAGS: frozenset[str] = frozenset({
    "-R", "--repo", "--hostname", "--color",
})
# Title flags on `gh issue create` (separate-token value forms).
_TITLE_FLAGS: frozenset[str] = frozenset({"--title", "-t"})
# --repo / -R value forms on the create argv.
_REPO_FLAGS: frozenset[str] = frozenset({"--repo", "-R"})

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


def _find_gh_issue_create_argv(command: str) -> list[str] | None:
    """Return the argv of a real `gh [globals] issue create|new` invocation,
    or None when no such command segment exists.

    Quote-aware: uses safe_tokenize → iter_command_starts so the literal
    `gh issue create` inside a quoted string (`grep -rn "gh issue create"`)
    is NOT a match — those tokens are a single shlex token, not separate
    `gh`/`issue`/`create` words (issue #514 결함2 MED).
    """
    tokens = safe_tokenize(command)
    if not tokens:
        return None
    for raw_argv in iter_command_starts(tokens):
        argv = strip_prefix(list(raw_argv))
        if not argv:
            continue
        if argv[0].rsplit("/", 1)[-1] != "gh":
            continue
        i = _skip_gh_global_flags(argv)
        if i < len(argv) and argv[i] == "issue":
            j = _skip_subcommand_flags(argv, i + 1)
            if j < len(argv) and argv[j] in ("create", "new"):
                return argv
    return None


def _skip_gh_global_flags(argv: list[str]) -> int:
    """Index of the first positional after gh's leading global flags."""
    i = 1
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok == "--":
            return i + 1
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in _GH_GLOBAL_VALUE_FLAGS and i < n:
            i += 1
    return i


def _skip_subcommand_flags(argv: list[str], i: int) -> int:
    """Skip flags between `issue` and the `create`/`new` action token.

    A separate-token value flag (`gh issue -R owner/repo create`) consumes the
    following token as its value, so it must not be mistaken for the action.
    Symmetric with `block-child-repo-issue-create._is_gh_issue_create` — keeps
    the two hooks' `issue [flags] create` parsing aligned (PR #523 review).
    """
    n = len(argv)
    while i < n and argv[i].startswith("-") and argv[i] != "--":
        tok = argv[i]
        i += 1
        if "=" not in tok and tok in _GH_GLOBAL_VALUE_FLAGS and i < n:
            i += 1
    return i


def _flag_value_from_argv(argv: list[str], flags: frozenset[str]) -> str:
    """Return the value of the first matching flag in *argv*.

    Handles separate-token (`--title X`, `-t X`), inline (`--title=X`,
    `-t=X`), and concatenated short-flag (`-tX`, `-Rowner/repo`) forms
    (issue #514 결함2 HIGH).
    """
    short_flags = tuple(
        f for f in flags
        if len(f) == 2 and f.startswith("-") and not f.startswith("--")
    )
    n = len(argv)
    for i, tok in enumerate(argv):
        if "=" in tok:
            name, _, val = tok.partition("=")
            if name in flags:
                return val
            continue
        if tok in flags and i + 1 < n:
            return argv[i + 1]
        for sf in short_flags:
            if tok.startswith(sf) and len(tok) > 2:
                return tok[2:]
    return ""


def _extract_title_from_argv(argv: list[str]) -> str:
    return _flag_value_from_argv(argv, _TITLE_FLAGS)


def _extract_repo(argv: list[str]) -> str:
    return _flag_value_from_argv(argv, _REPO_FLAGS)


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


def _emit_no_search_block(keywords: list[str]) -> None:
    emit_block(
        rule_name="gh issue create dup-search",
        why="no prior `gh search issues` / `gh issue list` in this session — "
            "creating an issue without a duplicate check risks /cancel cycles",
        correct_path=(
            f"run a search FIRST: gh search issues '{' '.join(keywords[:2])}' "
            "--repo <repo> (open AND closed); or add [dup-checked] to --title "
            "if verified outside this session"
        ),
        bypass_env="CLAUDE_HOOK_BYPASS_DUP_GATE",
        reference="CLAUDE.md → GitHub Issue Hygiene; docs/hook/"
            "block-gh-issue-create-without-dup-search.md",
    )
    sys.stderr.write(f"\nTitle keywords: {', '.join(keywords[:6])}\n")


def _emit_no_overlap_block(keywords: list[str], recent_searches: list[str]) -> None:
    emit_block(
        rule_name="gh issue create dup-search",
        why="prior searches exist but none of their keywords overlap with the "
            "new issue's title — an unrelated search does not satisfy the "
            "duplicate-check requirement",
        correct_path=(
            f"run a targeted search using your title's keywords: gh search "
            f"issues '{' '.join(keywords[:2])}' --repo <repo>; or add "
            "[dup-checked] to --title if verified outside this session"
        ),
        bypass_env="CLAUDE_HOOK_BYPASS_DUP_GATE",
        reference="CLAUDE.md → GitHub Issue Hygiene; docs/hook/"
            "block-gh-issue-create-without-dup-search.md",
    )
    extra = [
        "",
        f"Title keywords: {', '.join(keywords[:6])}",
        "Recent search commands found in transcript (no keyword overlap):",
        *[f"  - {s[:120]}" for s in recent_searches],
        "",
    ]
    sys.stderr.write("\n".join(extra) + "\n")


if __name__ == "__main__":
    sys.exit(main())
