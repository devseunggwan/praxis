#!/usr/bin/env python3
"""PreToolUse(Bash) guard: enforce Conventional Commits format on commit/PR/issue titles.

Global CLAUDE.md rule: "Git Commit & Title Rules — Use Conventional Commits format".
This hook intercepts AI-authored `git commit`, `gh pr create`, and `gh issue create`
Bash calls before they execute and blocks (exit 2) or warns (exit 0 advisory) when
the title does not match the conventional commit format.

Expected format:
  <type>[(<scope>)]: <description>

Where:
  type        — one of feat|fix|docs|refactor|chore|test|perf|ci|build|style
                (override via PRAXIS_COMMIT_TITLE_ALLOWED_TYPES)
  scope       — optional; lowercase alphanumeric + hyphens in parentheses
  description — must start with a lowercase ASCII letter (not uppercase, not CJK)

Whitelist (always PASS regardless of format):
  Merge branch ...
  Merge pull request ...
  Revert "..."
  fixup! ...
  squash! ...

Config:
  PRAXIS_COMMIT_TITLE_FORMAT_STRICT — default "1" (block, exit 2);
                                      "0" → advisory mode (exit 0, message to stderr)
  PRAXIS_COMMIT_TITLE_ALLOWED_TYPES — comma-separated type override
                                      (default: feat,fix,docs,refactor,chore,test,perf,ci,build,style)

Detection paths:
  git commit [-m|-F|--message|--file] <value>    → check first line (title)
  gh pr create --title <value>                   → check title
  gh issue create --title <value>                → check title
"""
from __future__ import annotations

import json
import os
import re
import sys
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_ALLOWED_TYPES = (
    "feat", "fix", "docs", "refactor", "chore", "test",
    "perf", "ci", "build", "style",
)

# Titles that start with these prefixes are always valid (auto-generated).
WHITELIST_PREFIXES = (
    "Merge branch ",
    "Merge pull request ",
    'Revert "',
    "fixup! ",
    "squash! ",
)

# Flags that carry the commit message (separate-token or embedded form).
MESSAGE_FLAGS = frozenset({"-m", "--message"})
# Flags that carry a file path whose first line is the title.
FILE_FLAGS = frozenset({"-F", "--file"})

# git global flags that consume the next token as their argument.
GIT_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-C", "-c",
    "--git-dir", "--work-tree", "--namespace",
    "--exec-path", "--super-prefix",
    "--config-env", "--attr-source",
    "-L", "--list-cmds",
})
# git global bare flags (no argument consumed).
GIT_GLOBAL_BARE_FLAGS = frozenset({
    "--no-pager", "--paginate", "-p",
    "--bare", "--no-replace-objects",
    "--no-lazy-fetch", "--no-optional-locks",
    "--no-advice", "--literal-pathspecs",
    "--glob-pathspecs", "--noglob-pathspecs",
    "--icase-pathspecs",
    "--help", "--version", "-h", "-v",
})

# git commit short options that take NO value — valid inner chars of a combined
# short cluster (e.g. -am, -vsm). Excludes value-taking short opts like
# -S, -F, -C, -c, -t, -u, -m.
GIT_COMMIT_NO_VALUE_SHORT = frozenset("aesvnqzp")


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _get_allowed_types() -> tuple[str, ...]:
    raw = os.environ.get("PRAXIS_COMMIT_TITLE_ALLOWED_TYPES", "")
    if raw.strip():
        types = [t.strip() for t in raw.split(",") if t.strip()]
        if types:
            return tuple(types)
    return DEFAULT_ALLOWED_TYPES


def _build_pattern(allowed_types: tuple[str, ...]) -> re.Pattern[str]:
    type_group = "|".join(re.escape(t) for t in allowed_types)
    # Match: <type>[(<scope>)]: <lowercase-ascii-letter>
    # Scope: lowercase alphanumeric + hyphens, optional.
    # Description: must START with a lowercase ASCII letter [a-z].
    return re.compile(
        rf"^({type_group})(\([a-z0-9-]+\))?: [a-z]"
    )


def _is_strict() -> bool:
    return os.environ.get("PRAXIS_COMMIT_TITLE_FORMAT_STRICT", "1") != "0"


# ---------------------------------------------------------------------------
# Git commit message extraction (mirrors commit-title-length-check approach)
# ---------------------------------------------------------------------------

import os.path


def _title_from_file(path: str, base_dir: str | None = None) -> str | None:
    """Read first line of a file; return None on any error or if stdin placeholder."""
    if path == "-":
        return None  # stdin — acknowledged limitation, silent pass
    if base_dir and not os.path.isabs(path):
        path = os.path.join(base_dir, path)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readline().rstrip("\n")
    except OSError:
        return None  # unreadable — silent pass


def _strip_git_global_flags(argv: list[str]) -> tuple[list[str], str | None]:
    """Strip git global flags; return (argv_at_subcommand, c_dir)."""
    c_dir: str | None = None
    i = 1  # skip 'git' at argv[0]
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        if "=" in tok:
            i += 1
            continue
        if tok in GIT_GLOBAL_BARE_FLAGS:
            i += 1
            continue
        if tok in GIT_GLOBAL_FLAGS_WITH_ARG:
            if tok == "-C" and i + 1 < len(argv):
                next_dir = argv[i + 1]
                if c_dir and not os.path.isabs(next_dir):
                    c_dir = os.path.join(c_dir, next_dir)
                else:
                    c_dir = next_dir
            i += 2
            continue
        break
    return argv[i:], c_dir


def _extract_git_titles(argv: list[str]) -> list[str]:
    """Extract commit title candidates from a git-commit argv.

    Only the FIRST -m / --message flag contributes the title.
    Handles:
      git commit -m "title"
      git commit --message "title"
      git commit -m="title" / --message="title"
      git commit -mvalue  (attached short-option)
      git commit -am "title"  (combined short flag)
      git commit -F /tmp/msg
      git -C /path commit -m "title"
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "git":
        return []

    sub_argv, c_dir = _strip_git_global_flags(argv)
    if not sub_argv or sub_argv[0] != "commit":
        return []

    titles: list[str] = []
    message_seen = False
    i = 1  # sub_argv[0] is "commit"
    while i < len(sub_argv):
        tok = sub_argv[i]

        # --flag=value embedded form
        if "=" in tok and tok.startswith("-"):
            key, _, val = tok.partition("=")
            if key in MESSAGE_FLAGS and not message_seen:
                titles.append(val.split("\n")[0])
                message_seen = True
                i += 1
                continue
            if key in FILE_FLAGS:
                t = _title_from_file(val, base_dir=c_dir)
                if t is not None:
                    titles.append(t)
                i += 1
                continue

        # Attached short-option form: -m<value>
        if (
            tok.startswith("-m")
            and not tok.startswith("--")
            and len(tok) > 2
            and not message_seen
        ):
            titles.append(tok[2:].split("\n")[0])
            message_seen = True
            i += 1
            continue

        # Combined short flags like -am (e.g. -a -m together)
        if (
            tok.startswith("-")
            and not tok.startswith("--")
            and len(tok) > 2
            and tok[-1] == "m"
            and all(c in GIT_COMMIT_NO_VALUE_SHORT for c in tok[1:-1])
            and not message_seen
        ):
            if i + 1 < len(sub_argv):
                titles.append(sub_argv[i + 1].split("\n")[0])
                message_seen = True
                i += 2
                continue

        # Standard separate-token flags
        if tok in MESSAGE_FLAGS and not message_seen:
            if i + 1 < len(sub_argv):
                titles.append(sub_argv[i + 1].split("\n")[0])
                message_seen = True
                i += 2
                continue
            i += 1
            continue

        if tok in FILE_FLAGS:
            if i + 1 < len(sub_argv):
                t = _title_from_file(sub_argv[i + 1], base_dir=c_dir)
                if t is not None:
                    titles.append(t)
                i += 2
                continue
            i += 1
            continue

        i += 1

    return titles


# ---------------------------------------------------------------------------
# gh pr/issue create --title extraction
# ---------------------------------------------------------------------------

def _extract_gh_title(argv: list[str]) -> str | None:
    """Extract --title value from `gh pr create` or `gh issue create` argv.

    Returns the title string, or None if not a matching gh subcommand or no
    --title flag present.

    Handles:
      gh pr create --title "value"
      gh pr create -t "value"
      gh issue create --title "value"
      gh issue create -t "value"
      gh pr create --title="value"
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "gh":
        return None

    # Expect: gh <subcmd> <subsubcmd> ...
    # We only care about: gh pr create and gh issue create
    if len(argv) < 3:
        return None
    subcmd = argv[1]
    subsubcmd = argv[2]
    if subcmd not in ("pr", "issue") or subsubcmd != "create":
        return None

    i = 3
    while i < len(argv):
        tok = argv[i]
        # --title=value embedded form
        if tok.startswith("--title="):
            return tok[len("--title="):]
        # --title or -t separate-token form
        if tok in ("--title", "-t"):
            if i + 1 < len(argv):
                return argv[i + 1]
            return None
        i += 1

    return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _is_whitelisted(title: str) -> bool:
    return any(title.startswith(p) for p in WHITELIST_PREFIXES)


def _is_valid_format(title: str, pattern: re.Pattern[str]) -> bool:
    return bool(pattern.match(title))


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _emit_block(reason: str) -> None:
    """Write the block message to stderr (exit 2 is the caller's responsibility)."""
    sys.stderr.write(reason + "\n")


def _emit_advisory(reason: str) -> None:
    sys.stderr.write(reason + "\n")


def _build_violation_message(
    title: str,
    allowed_types: tuple[str, ...],
    command: str,
) -> str:
    types_str = "|".join(allowed_types)
    detail = (
        f"Title: {title!r}\n"
        f"Expected: <type>[(<scope>)]: <lowercase-description>\n"
        f"Types: {types_str}\n"
        f"Whitelisted: Merge branch, Merge pull request, Revert \"...\", fixup!, squash!"
    )
    base = format_block(
        rule_name="commit-title-format-check",
        why=(
            f"title does not match Conventional Commits format — {detail}"
        ),
        correct_path=(
            "use: feat(scope): description  or  fix: short description  "
            "(description must start with a lowercase letter)"
        ),
        bypass_env="PRAXIS_COMMIT_TITLE_FORMAT_STRICT",
        bypass_reason_hint="=0 to switch to advisory mode (exit 0, stderr warning only)",
        reference="CLAUDE.md → Git Commit & Title Rules",
    )
    return base + compound_cascade_hint(command)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    # Collapse backslash-newline continuations (mirrors sibling hooks)
    command = command.replace("\\\n", " ")

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    allowed_types = _get_allowed_types()
    pattern = _build_pattern(allowed_types)
    strict = _is_strict()

    violation: str | None = None

    for argv in iter_command_starts(tokens):
        # Check git commit titles
        git_titles = _extract_git_titles(argv)
        for title in git_titles:
            if _is_whitelisted(title):
                continue
            if not _is_valid_format(title, pattern):
                violation = _build_violation_message(title, allowed_types, command)
                break
        if violation:
            break

        # Check gh pr create / gh issue create --title
        gh_title = _extract_gh_title(argv)
        if gh_title is not None:
            if not _is_whitelisted(gh_title) and not _is_valid_format(gh_title, pattern):
                violation = _build_violation_message(gh_title, allowed_types, command)
                break

    if violation is None:
        return 0

    if strict:
        _emit_block(violation)
        return 2
    else:
        _emit_advisory(f"[commit-title-format-check] ADVISORY (STRICT=0):\n{violation}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
