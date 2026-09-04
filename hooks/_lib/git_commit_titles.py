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
  text_from_file(path, base_dir=None)         -> str | None
  strip_git_global_flags(argv)                -> tuple[list[str], str | None]
  extract_git_titles(argv, command=None)      -> list[str]
  extract_git_message_texts(argv, command=None) -> list[str]

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
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    _starts_unquoted_comment,
    has_shell_expansion,
    has_unclosed_expansion,
    strip_heredoc_bodies,
    strip_prefix,
)

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

# The two `git commit` short options this module reads a message out of, and
# the kind of value each carries. Both are value-taking, so each ends its
# cluster: the rest of the cluster is the value when there is one, and the next
# token when there is not (`-am"fix: x"` and `-am "fix: x"` are the same
# invocation to git, and `-Fmsg.txt`/`-F-` likewise).
GIT_COMMIT_VALUE_SHORT = {"m": "message", "F": "file"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_git_binary(token: str) -> bool:
    """True iff `token` names the git binary, bare or path-prefixed.

    Mirrors the `_is_git_binary` every sibling commit gate carries
    (`block-rename-sweep-survivors`, `commit-decomposition-advisory`,
    `pre-commit-staged-file-enumeration`, …) and the `_is_gh_binary` in
    `_hook_utils`. An exact `argv[0] == "git"` comparison is bypassed by
    `/usr/bin/git commit -m …`, which is the same path-prefix hole #1099 closed
    on the gh side. The leading strip covers a subshell-wrapped form
    (`(git …)`, `$(git …)`), where the grouping char rides on the token.
    """
    stripped = token.lstrip("(){}$`")
    return stripped == "git" or stripped.endswith("/git")


def _resolve_message_path(path: str, base_dir: str | None) -> str | None:
    """The path to open for a `-F <path>` value, or None when there is none.

    `base_dir` is the working directory git treats as cwd for relative paths
    (the `-C <dir>` global flag value). When absent, paths are opened relative
    to the hook's own cwd. `-` is stdin, which a PreToolUse hook cannot read.
    """
    if path == "-":
        return None  # stdin — acknowledged limitation, silent pass
    if base_dir and not os.path.isabs(path):
        return os.path.join(base_dir, path)
    return path


def title_from_file(path: str, base_dir: str | None = None) -> str | None:
    """Read first line of a file; return None on any error or if stdin placeholder.

    Reads ONE line rather than taking the first line off `text_from_file`. The
    two title gates run on every `git commit` a session makes, and a `-F` file
    is arbitrary — pointing one at a large file made each of them pay for the
    whole read to answer a question about its first 50 characters. Measured on
    a 50 MB file, mean of 20 calls: 47.0 ms per call for the whole-file read
    against 0.023 ms for one line. The body gate still needs the rest and
    calls `text_from_file`.
    """
    resolved = _resolve_message_path(path, base_dir)
    if resolved is None:
        return None
    try:
        with open(resolved, encoding="utf-8") as fh:
            # `.split("\n")[0]` on the whole text drops the newline and keeps a
            # trailing `\r`; `readline()` plus this strip is the same string.
            return fh.readline().rstrip("\n")
    except OSError:
        return None  # unreadable — silent pass


def text_from_file(path: str, base_dir: str | None = None) -> str | None:
    """Whole contents of a `-F <path>` message file; None on any error.

    Same resolution rules as `title_from_file`. The gate that grades the
    message body needs the whole thing; a title gate does not, and pays for
    this read only when it genuinely wants every line.
    """
    resolved = _resolve_message_path(path, base_dir)
    if resolved is None:
        return None
    try:
        with open(resolved, encoding="utf-8") as fh:
            return fh.read()
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

    Two regions the shell never executes are excluded first, so a quote there
    cannot disqualify a literal title that lives in the real command:
    heredoc bodies (blanked via the shared `strip_heredoc_bodies`, same as
    `safe_tokenize` uses) and unquoted `#…` comments, whose word-boundary rule
    is the shared `_starts_unquoted_comment` so this scanner and
    `_quote_open_at_eol` cannot disagree on where a comment starts (issue
    #1036 follow-up).
    """
    command = strip_heredoc_bodies(command)
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
        # `_starts_unquoted_comment`'s word-boundary set has no newline in it
        # because its two callers only ever see one physical line at a time;
        # this scanner walks the whole multi-line command, so a `#` right
        # after a line break is a boundary here too, even though the shared
        # helper alone cannot see it.
        at_line_start = i == 0 or command[i - 1] == "\n"
        if ch == "#" and (at_line_start or _starts_unquoted_comment(command, i)):
            nl = command.find("\n", i)
            i = n if nl == -1 else nl
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
    literal and the double-quoted run belongs to another EXECUTED segment.
    Separating those two needs the source spans the tokenizer discards.
    Silence is the fail-open direction and the one this repo chooses for a
    gate, so that residual is pinned by test rather than closed.

    A narrower case IS closed: `_quoted_runs` excludes comments and heredoc
    bodies before matching, so `git commit -m '$(x)' # "$(x)"` and a `"$(x)"`
    sitting inside an unrelated heredoc body no longer disqualify the literal
    — neither region is code the shell ever runs, so a quoted run found there
    cannot be "the other segment" the mirror-case paragraph above is about
    (issue #1036 follow-up).
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


def _cluster_value(
    tok: str, argv: list[str], i: int
) -> tuple[tuple[str, str] | None, int] | None:
    """`((kind, value) | None, tokens consumed)` for a short-option cluster.

    Returns None when `tok` is not a cluster this module can read, so the
    caller falls through to its long-form branches rather than guessing. A
    cluster is readable while every character before the value-taking one is a
    known no-value `git commit` short flag: that whitelist is what keeps
    `-Smike@example.com` out (`-S` takes an attached key id, and `S`, `@`, `.`
    are not no-value flags), and it is why an unknown character abandons the
    whole cluster instead of scanning past it.

    The value-taking flag ends the cluster either way — with the rest of the
    cluster as its value, or with the next token when the cluster ends there.
    """
    for pos, ch in enumerate(tok[1:], start=1):
        kind = GIT_COMMIT_VALUE_SHORT.get(ch)
        if kind is not None:
            rest = tok[pos + 1:]
            if rest:
                return (kind, rest), 1
            if i + 1 < len(argv):
                return (kind, argv[i + 1]), 2
            return None, 1  # dangling flag — git errors, nothing to read
        if ch not in GIT_COMMIT_NO_VALUE_SHORT:
            return None
    return None, 1  # all no-value flags (`-av`): consumed, contributes nothing


def _scan_commit_message_values(
    argv: list[str],
) -> tuple[list[tuple[str, str]], str | None] | None:
    """Ordered message sources in a `git commit` argv, or None if not one.

    Returns `([(kind, value), …], c_dir)` where `kind` is `"message"` (the
    value is the raw message text) or `"file"` (the value is a path). Order is
    argv order, so a caller can apply git's "first -m is the title" rule
    without re-walking the argv.

    The walk is the single source of truth for git-commit message extraction —
    `extract_git_titles` and `extract_git_message_texts` differ only in what
    they do with the values, which is what keeps the two from drifting the way
    the pre-#594 copies did.

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
    if not argv or not _is_git_binary(argv[0]):
        return None

    # Strip git global flags to find the actual subcommand.
    sub_argv, c_dir = strip_git_global_flags(argv)
    if not sub_argv or sub_argv[0] != "commit":
        return None

    values: list[tuple[str, str]] = []

    i = 1  # sub_argv[0] is "commit"; start scanning from index 1
    while i < len(sub_argv):
        tok = sub_argv[i]

        # Handle --flag=value embedded form. Restricted to long flags: git
        # only honours `=` splitting on `--long=value`; for a short flag
        # `-m=fix: thing` git takes the whole `=fix: thing` (leading `=`
        # included) as the message value, so splitting it here would drop
        # the `=` and mis-parse the title (#1097).
        if "=" in tok and tok.startswith("--"):
            key, _, val = tok.partition("=")
            if key in MESSAGE_FLAGS:
                values.append(("message", val))
                i += 1
                continue
            if key in FILE_FLAGS:
                values.append(("file", val))
                i += 1
                continue

        # Everything after `--` is a pathspec, never an option (git-commit(1),
        # "OPTIONS": `--` separates paths from revisions/options). Scanning on
        # read a path literally named `-m` as a message flag, so its neighbour
        # became message text and could block a valid commit (CodeRabbit,
        # issue #1228 round 2).
        if tok == "--":
            break

        # Handle a short-option cluster — `-m<value>`, `-am <value>`,
        # `-am<value>`, `-Fmsg.txt`, `-F-`, `-vsm`. One walk covers all of
        # them because they are one grammar: leading no-value flags, then at
        # most one value-taking flag that consumes the rest of the cluster or,
        # when the cluster ends with it, the next token. Three separate
        # branches used to cover a subset of that grammar, and the shapes
        # between them (`-am"fix: x"`, `-Fmsg.txt`) yielded no message source
        # at all.
        if tok.startswith("-") and not tok.startswith("--") and len(tok) > 1:
            consumed = _cluster_value(tok, sub_argv, i)
            if consumed is not None:
                value, step = consumed
                if value is not None:
                    values.append(value)
                i += step
                continue

        # Standard separate-token flags.
        if tok in MESSAGE_FLAGS:
            if i + 1 < len(sub_argv):
                values.append(("message", sub_argv[i + 1]))
                i += 2
                continue
            i += 1
            continue

        if tok in FILE_FLAGS:
            if i + 1 < len(sub_argv):
                values.append(("file", sub_argv[i + 1]))
                i += 2
                continue
            i += 1
            continue

        i += 1

    return values, c_dir


def extract_git_titles(argv: list[str], command: str | None = None) -> list[str]:
    """Extract commit title candidates from a git-commit argv.

    Only the FIRST -m / --message flag contributes the title; subsequent -m
    flags are body paragraphs and are ignored (git treats them that way).
    -F / --file reads the file and takes the first line.

    A message value we cannot statically resolve contributes no candidate, but
    it still consumes the "first -m" slot — git took it as the message, we just
    cannot read it, so later -m flags remain body paragraphs.

    `command` is the raw shell command `argv` was tokenized from. It is optional
    only so existing argv-only callers keep working; passing it is what lets a
    single-quoted literal title be graded instead of mistaken for a substitution
    (see `title_is_unresolved_substitution`).
    """
    scanned = _scan_commit_message_values(argv)
    if scanned is None:
        return []
    values, c_dir = scanned

    titles: list[str] = []
    message_seen = False
    for kind, raw in values:
        if kind == "message":
            if message_seen:
                continue
            message_seen = True
            first = raw.split("\n")[0]
            if not title_is_unresolved_substitution(first, command):
                titles.append(first)
            continue
        t = title_from_file(raw, base_dir=c_dir)
        if t is not None:
            titles.append(t)
    return titles


def extract_git_message_texts(
    argv: list[str], command: str | None = None
) -> list[str]:
    """Full commit message text for every statically resolvable source.

    Where `extract_git_titles` keeps only the first line of the first `-m`,
    this keeps everything: git joins successive `-m` paragraphs with a blank
    line, so they are joined that way here and returned as one text, and each
    `-F` file contributes its whole contents as another.

    A gate that reads the *body* needs this because the body is where a shape
    invisible to a title check can live — see
    `hooks/preflight-gate/commit-message-paren-check/spec.md`.

    An unresolvable substitution (`-m "$(cat …)"`) contributes nothing, exactly
    as it contributes no title; the caller recovers that case from the raw
    command's heredoc bodies instead.

    An expansion can also sit on a LATER line of a multi-line value, and only
    the first line decides whether the value is a title candidate. Passing the
    rest through verbatim hands a body gate raw shell text where the shell
    would have put a command's output — `$(git log --oneline` reads as a line
    whose leading word is glued to `(`, so a blocking gate rejects a message
    nobody can predict. Such a line is blanked instead of dropped, keeping the
    line numbers a gate reports pointed at the real message.

    Where the expansion sits on the line does not change that. An earlier
    version blanked only a line that *opened* with one, so `foo($(printf x))`
    — delivered as `foo(x)`, which parses — was graded as raw source and read
    as a nested paren (issue #1228 round 2). The line is unreadable either way,
    so `has_shell_expansion` decides it rather than the opening position.

    A whole value that reached us as one single-quoted run is exempt: the shell
    substitutes nothing inside it, so every line is literal and gradable. That
    also settles the case the line-wise rule cannot — a literal `$(…)` line
    inside a single-quoted message used to be blanked, because the by-value
    match is against a whole quoted run and a line of a run never equals the
    run.
    """
    scanned = _scan_commit_message_values(argv)
    if scanned is None:
        return []
    values, c_dir = scanned

    texts: list[str] = []
    paragraphs: list[str] = []
    for kind, raw in values:
        if kind == "message":
            if command is not None and _title_is_single_quoted_literal(command, raw):
                paragraphs.append(raw)
                continue
            if has_unclosed_expansion(raw):
                # bash never finishes parsing this command, so no commit
                # happens and the text after the opener is the inside of an
                # unfinished substitution rather than message lines.
                continue
            lines = raw.split("\n")
            if title_is_unresolved_substitution(lines[0], command):
                continue
            paragraphs.append("\n".join(
                "" if has_shell_expansion(line) else line for line in lines
            ))
            continue
        body = text_from_file(raw, base_dir=c_dir)
        if body is not None:
            texts.append(body)
    if paragraphs:
        texts.insert(0, "\n\n".join(paragraphs))
    return texts
