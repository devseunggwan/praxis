#!/usr/bin/env python3
"""PreToolUse(Bash) guard: enforce 50-character limit on git commit titles.

Global CLAUDE.md rule: "Git Commit & Title Rules — Title: max 50 characters".
This hook intercepts AI-authored `git commit` Bash calls before they execute
and emits permissionDecision "ask" when the first line of the commit message
exceeds the configured maximum (default 50, override via CLAUDE_COMMIT_TITLE_MAX).

Why a PreToolUse hook instead of a git commit-msg hook:
  The praxis distribution model ships Claude Code hooks (loaded via hooks.json).
  A git commit-msg hook would require installation into every repo's .git/hooks/
  directory — an out-of-band setup step that is easy to miss, not portable across
  worktrees, and breaks when a repo is freshly cloned. A PreToolUse hook fires
  centrally for every AI-authored Bash call in any repo/worktree, with no per-repo
  setup required. Trade-off: it only catches AI-authored commits (not manual shell
  commits), which is exactly the population that produced the silent violations.

Detection path:
  git commit [-m|-F|--message|--file] <value>  →  extract title (first line)
  len(title) > MAX                              →  emit permissionDecision "ask"

Opt-out: embed `# title-length:ack` anywhere in the command to bypass.
Skip: Merge / Revert commits, -F - (stdin body), unreadable -F files.
Config: CLAUDE_COMMIT_TITLE_MAX=<n> (integer ≥ 1) overrides default 50.
"""
from __future__ import annotations

import json
import os
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

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MAX = 50
OPT_OUT_MARKER = "# title-length:ack"

# Prefixes that indicate auto-generated merge/revert commits — skip them.
SKIP_PREFIXES = ("Merge ", "Revert ")

# Flags that carry the commit message as the next token (or in --flag=value form).
MESSAGE_FLAGS = frozenset({"-m", "--message"})
# Flags that carry a file path whose first line is the title.
FILE_FLAGS = frozenset({"-F", "--file"})

# Git global flags that appear between `git` and the subcommand.
# These must be stripped before checking argv[1] == "commit".
# Flags that consume the next token as their argument.
GIT_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-C", "-c",
    "--git-dir", "--work-tree", "--namespace",
    "--exec-path", "--super-prefix",
    "--config-env", "--attr-source",
    "-L", "--list-cmds",
})
# Bare flags (no argument consumed).
GIT_GLOBAL_BARE_FLAGS = frozenset({
    "--no-pager", "--paginate", "-p",
    "--bare", "--no-replace-objects",
    "--no-lazy-fetch", "--no-optional-locks",
    "--no-advice", "--literal-pathspecs",
    "--glob-pathspecs", "--noglob-pathspecs",
    "--icase-pathspecs",
    "--help", "--version", "-h", "-v",
})

# `git commit` short options that take NO value — valid as inner chars of a
# POSIX combined-short cluster (e.g. -am, -vsm). Excludes value-taking short
# options like -S (signing key id, optional attached), -F (file), -C (commit
# ref), -c (commit), -t (template), -u (untracked mode), -m (message).
GIT_COMMIT_NO_VALUE_SHORT = frozenset("aesvnqzp")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_max() -> int:
    """Read CLAUDE_COMMIT_TITLE_MAX; fall back to DEFAULT_MAX on invalid value."""
    raw = os.environ.get("CLAUDE_COMMIT_TITLE_MAX", "")
    if raw.strip():
        try:
            val = int(raw.strip())
            if val >= 1:
                return val
        except ValueError:
            pass
    return DEFAULT_MAX


import os.path  # for joining -C base with relative -F file path


def _title_from_file(path: str, base_dir: str | None = None) -> str | None:
    """Read first line of a file; return None on any error or if stdin placeholder.

    `base_dir` is the working directory git treats as cwd for relative paths
    (the `-C <dir>` global flag value). When absent, paths are opened
    relative to the hook's own cwd.
    """
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
    """Strip git global flags between 'git' and the subcommand.

    Handles flags-with-arg (-C, -c, --git-dir, etc.) and bare flags
    (--no-pager, -p, etc.), plus '='-embedded long-form (--git-dir=/path).
    Returns (argv_at_subcommand, c_dir). The c_dir is the value passed to
    `-C <dir>` / `-C=<dir>` if present — the working directory git uses for
    resolving relative paths (notably `-F <file>`). When multiple `-C` flags
    appear (git supports stacking — each is relative to the previous), they
    are joined left-to-right, matching git's own behavior.
    """
    c_dir: str | None = None
    i = 1  # skip 'git' at argv[0]
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        # Long flag with embedded '=' (e.g. --git-dir=/path) — bare, no next token.
        if "=" in tok:
            i += 1
            continue
        if tok in GIT_GLOBAL_BARE_FLAGS:
            i += 1
            continue
        if tok in GIT_GLOBAL_FLAGS_WITH_ARG:
            if tok == "-C" and i + 1 < len(argv):
                next_dir = argv[i + 1]
                # git -C stacking: each subsequent -C is relative to prior
                if c_dir and not os.path.isabs(next_dir):
                    c_dir = os.path.join(c_dir, next_dir)
                else:
                    c_dir = next_dir
            i += 2  # consume flag + its argument
            continue
        # Unknown flag — stop stripping to avoid over-consuming.
        break
    return argv[i:], c_dir


def _extract_titles(argv: list[str]) -> list[str]:
    """Extract commit title candidates from a git-commit argv.

    Only the FIRST -m / --message flag contributes the title; subsequent -m
    flags are body paragraphs and are ignored (git treats them that way).
    -F / --file reads the file and takes the first line.

    Handles:
      git commit -m "title"
      git commit --message "title"
      git commit -m="title" / --message="title"
      git commit -mvalue  (attached short-option, POSIX style)
      git commit -F /tmp/msg
      git commit --file /tmp/msg
      git commit -am "title"   (combined short flag, e.g. -a -m together)
      git commit --amend -m "title"
      git -C /path commit -m "title"   (git global flags stripped)
      git -c key=val commit -m "title" (git global flags stripped)
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "git":
        return []

    # Strip git global flags to find the actual subcommand.
    sub_argv, c_dir = _strip_git_global_flags(argv)
    if not sub_argv or sub_argv[0] != "commit":
        return []

    titles: list[str] = []
    message_seen = False
    i = 1  # sub_argv[0] is "commit"; start scanning from index 1
    while i < len(sub_argv):
        tok = sub_argv[i]

        # Handle --flag=value embedded form.
        if "=" in tok and not tok.startswith("-") is False:
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

        # Handle attached short-option form: -m<value> parsed as single token.
        # shlex strips quotes, so `git commit -m"long title"` becomes ['-mlong title'].
        # This must be checked BEFORE the combined-flag branch to avoid misrouting.
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

        # Handle combined short flags like -am / -vsm (git allows clustered
        # short options where -m terminates with the next token as value).
        # Strict whitelist: every preceding char must be a known no-value short
        # flag from git commit. This excludes `-Smike@example.com` (-S accepts
        # attached key id; even though `com` ends in `m`, `S/@/.` etc. are not
        # in the no-value set, so the cluster is rejected). Round-1 heuristic
        # used "m anywhere in tok[1:]" and was unsafe; round-4 narrows it.
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

        # Standard separate-token flags.
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
# Output
# ---------------------------------------------------------------------------

def _emit_ask(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


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

    if OPT_OUT_MARKER in command:
        return 0

    # Bash line continuation: collapse `\<newline>` to a single space so
    # multi-line invocations like `git commit \\\n  -m "..."` parse as one
    # command. Mirrors the same pre-tokenize step in
    # `external-write-falsify-check.py` and `block-gh-state-all.py`.
    command = command.replace("\\\n", " ")

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    max_len = _get_max()

    for argv in iter_command_starts(tokens):
        titles = _extract_titles(argv)
        for title in titles:
            if any(title.startswith(p) for p in SKIP_PREFIXES):
                continue
            length = len(title)
            if length > max_len:
                _emit_ask(
                    f"Commit title too long: {length} chars (max {max_len}).\n"
                    f"Title: {title!r}\n"
                    "Shorten to ≤50 chars, or embed `# title-length:ack` to bypass."
                    + compound_cascade_hint(command)
                )
                return 0  # ask emitted; only report first violation

    return 0


if __name__ == "__main__":
    sys.exit(main())
