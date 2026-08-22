#!/usr/bin/env python3
"""Shared git-commit-argv title extraction — single source of truth (B2, #594).

Previously this parser was duplicated byte-for-byte in two preflight gates:
  - commit-title-format-check/impl.py  (Conventional Commits format gate)
  - commit-title-length-check/impl.py  (50-char title-length gate)

Both now import from here, so the argv parser cannot silently drift across the
two gates (the pre-extraction state had already drifted: one copy carried a
dead `tok.startswith("-") is not False` quirk the other did not).

Public API:
  GIT_GLOBAL_FLAGS_WITH_ARG, GIT_GLOBAL_BARE_FLAGS, GIT_COMMIT_NO_VALUE_SHORT,
  MESSAGE_FLAGS, FILE_FLAGS  — constant sets
  title_from_file(path, base_dir=None)        -> str | None
  strip_git_global_flags(argv)                -> tuple[list[str], str | None]
  extract_git_titles(argv, command=None)      -> list[str]

The two gates differ only in WHAT they do with the extracted titles (length
check vs format check); the extraction itself is identical and lives here.
"""
from __future__ import annotations

import os
import sys as _sys
from pathlib import Path as _Path

# This module lives in hooks/_lib/ alongside _hook_utils. Consumers already
# insert this directory onto sys.path before importing us, but inserting it
# here too keeps the module self-contained (e.g. when imported directly by a
# differential test) without depending on the importer's path setup.
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from _hook_utils import strip_prefix  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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

def title_from_file(path: str, base_dir: str | None = None) -> str | None:
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


def strip_git_global_flags(argv: list[str]) -> tuple[list[str], str | None]:
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


def _quoted_runs(command: str) -> list[tuple[str, str]]:
    """Every quoted run in `command` as `(quote_char, contents)`, in source order.

    A single-quoted run is the one place the shell substitutes nothing at all,
    so its contents reach the tokenizer byte-for-byte. That property is what
    lets the caller below match a token back to its source without tracking
    positions through the tokenizer.

    Double-quoted runs are reported too, and for the opposite reason: their
    contents are what the shell *does* substitute, so a title that also appears
    as one cannot be attributed to the single-quoted run by value alone.

    Each quote style is walked while inside the other, so a quote character
    that is data cannot open or close a run: `"it's"` holds no single-quoted
    run, and neither does `\\'`.
    """
    runs: list[tuple[str, str]] = []
    quote = ""
    start = 0
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if quote == "'":
            if ch == "'":
                runs.append((quote, command[start:i]))
                quote = ""
            i += 1
            continue
        if ch == "\\" and (quote == '"' or not quote):
            i += 2
            continue
        if quote == '"':
            if ch == '"':
                runs.append((quote, command[start:i]))
                quote = ""
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            start = i + 1
            i += 1
            continue
        i += 1
    return runs


def _title_is_single_quoted_literal(command: str, title: str) -> bool:
    """True when `title` reached us as the contents of a single-quoted run.

    Tokenization strips quoting, so `'$(x)'` and `"$(x)"` arrive as the same
    string while the shell treats them oppositely: the first is literal text,
    the second is a substitution. Recovering that distinction needs the raw
    command, which is why the check cannot live on the token alone.

    The match is on the TITLE, not on the opener. An earlier version asked
    whether every occurrence of `$(` in the raw command sat in single quotes,
    which reads far past the segment the title came from: the callers tokenize
    once and hand every segment the same raw command, so `echo $(date); git
    commit -m '$(literal) title'` saw the unquoted `$(` from the `echo` and
    suppressed a title that the shell never touched (CodeRabbit, PR #1014).
    Matching the token against the runs needs no position tracking through the
    tokenizer and is unaffected by anything in another segment.

    Because the match is by value, a command that quotes the SAME text both
    ways — `echo '$(x)'; git commit -m "$(x)"` — offers two source runs that
    tokenize identically, and nothing in the token stream says which one this
    title came from. That is not a literal we failed to recognise; it is a
    title whose value is genuinely unknown at hook time, so the double-quoted
    run disqualifies the match and the gates stay silent (issue #1036). Reading
    it as a literal instead graded a title the shell expands, which blocked a
    legitimate commit.

    The disqualification is by value, so it also silences the mirror case —
    `git commit -m '$(x)' && echo "$(x)"`, where the title really is the
    literal and the double-quoted run belongs to another segment. Separating
    those two needs the source spans the tokenizer discards. Silence is the
    fail-open direction and the one this repo chooses for a gate, so the
    residual is pinned by test rather than closed.
    """
    runs = _quoted_runs(command)
    single = any(q == "'" and body == title for q, body in runs)
    double = any(q == '"' and body == title for q, body in runs)
    return single and not double


def title_is_unresolved_substitution(title: str, command: str | None = None) -> bool:
    """True when a title candidate is a substitution whose value we cannot know.

    `git commit -m "$(cat <<'EOF' … EOF)"` is the standard commit form here, and
    tokenization hands us the literal text `$(cat <<'EOF'` — the shell expands it,
    we cannot. Grading that literal against Conventional Commits rejects every such
    commit with exit 2, so the title gates must stay silent on it instead.

    Narrow on purpose: only a candidate that *opens* a substitution is unknowable.
    `fix: handle $(foo)` is a real title and stays graded.

    `command` is the raw shell command the argv was tokenized from. Without it the
    opening marker alone decides, which cannot tell `'$(literal)'` from
    `"$(expanded)"` — the shell substitutes only the second, so treating both as
    unknowable lets a single-quoted literal title skip the format and length gates
    entirely. Callers that have the raw command must pass it.

    Before the multi-line-quote fix (issue #987) this was rare — the tokenizer
    dropped the whole line, so no title reached here at all. Making the line
    visible turned a latent false positive into a routine one.
    """
    stripped = title.lstrip()
    for opener in ("$(", "`"):
        if not stripped.startswith(opener):
            continue
        if command is not None and _title_is_single_quoted_literal(command, title):
            return False
        return True
    return False


def extract_git_titles(argv: list[str], command: str | None = None) -> list[str]:
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

    `command` is the raw shell command `argv` was tokenized from. It is optional
    only so existing argv-only callers keep working; passing it is what lets a
    single-quoted literal title be graded instead of mistaken for a substitution
    (see `title_is_unresolved_substitution`).
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "git":
        return []

    # Strip git global flags to find the actual subcommand.
    sub_argv, c_dir = strip_git_global_flags(argv)
    if not sub_argv or sub_argv[0] != "commit":
        return []

    titles: list[str] = []

    def add_title(raw: str) -> None:
        """Record the first line of a message value as a title candidate.

        A value we cannot statically resolve contributes no candidate, but the
        caller still marks `message_seen` — git took it as the message, we just
        cannot read it, so later -m flags remain body paragraphs.
        """
        first = raw.split("\n")[0]
        if not title_is_unresolved_substitution(first, command):
            titles.append(first)

    message_seen = False
    i = 1  # sub_argv[0] is "commit"; start scanning from index 1
    while i < len(sub_argv):
        tok = sub_argv[i]

        # Handle --flag=value embedded form.
        if "=" in tok and tok.startswith("-"):
            key, _, val = tok.partition("=")
            if key in MESSAGE_FLAGS and not message_seen:
                add_title(val)
                message_seen = True
                i += 1
                continue
            if key in FILE_FLAGS:
                t = title_from_file(val, base_dir=c_dir)
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
            add_title(tok[2:])
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
                add_title(sub_argv[i + 1])
                message_seen = True
                i += 2
                continue

        # Standard separate-token flags.
        if tok in MESSAGE_FLAGS and not message_seen:
            if i + 1 < len(sub_argv):
                add_title(sub_argv[i + 1])
                message_seen = True
                i += 2
                continue
            i += 1
            continue

        if tok in FILE_FLAGS:
            if i + 1 < len(sub_argv):
                t = title_from_file(sub_argv[i + 1], base_dir=c_dir)
                if t is not None:
                    titles.append(t)
                i += 2
                continue
            i += 1
            continue

        i += 1

    return titles
