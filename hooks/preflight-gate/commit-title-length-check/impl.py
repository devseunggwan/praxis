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

Squash-merge path (issue #843): `gh pr merge --squash` combines the PR title
into the squash commit title on GitHub's side — a shape this hook's `git
commit` matcher cannot see. Detected separately below and reported via
stderr ADVISORY (not `ask`), never blocking the merge itself:

  gh [global flags] pr merge [flags] --squash|-s
    -> `-t`/`--subject <value>` present -> that value IS the title (no
       network call needed)
    -> otherwise -> `gh pr view <id> [-R repo] --json title -q .title`
       (5s timeout, fail-open on any error)
  len(title) > MAX -> stderr advisory (exit 0, command proceeds)

`gh api repos/.../pulls/N/merge` is a DELIBERATE gap, not an oversight —
see "Ordering constraint" in spec.md. The `git commit` path is unchanged
(still `ask`, still blocking); only the new squash-merge path is advisory.
"""
from __future__ import annotations

import re
import os
import subprocess
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    _is_gh_binary,
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from git_commit_titles import (  # type: ignore[import-not-found]  # noqa: E402
    extract_git_titles,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MAX = 50
OPT_OUT_MARKER = "# title-length:ack"

# Prefixes that indicate auto-generated merge/revert commits — skip them.
SKIP_PREFIXES = ("Merge ", "Revert ")

# ---------------------------------------------------------------------------
# Squash-merge path (issue #843)
# ---------------------------------------------------------------------------

_GH_TIMEOUT_SEC = 5

# gh global flags that consume one separate-token argument. Same set
# duplicated across ~11 sibling hooks in this repo (pre-merge-approval-gate,
# gh-merge-worktree-precondition, gh-flag-verify, etc.) — established repo
# convention keeps this local rather than extracted to _lib.
_GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})

# `gh pr merge` flags that consume one separate-token argument (per
# `gh help pr merge`), needed to walk past them when scanning the tail.
_MERGE_FLAGS_WITH_ARG = frozenset({
    "-A", "--author-email",
    "-b", "--body",
    "-F", "--body-file",
    "-t", "--subject",
    "--match-head-commit",
})

_SQUASH_FLAGS = frozenset({"-s", "--squash"})
_SUBJECT_FLAGS = frozenset({"-t", "--subject"})

_TAIL_FLAGS_WITH_ARG = _MERGE_FLAGS_WITH_ARG | _GH_GLOBAL_FLAGS_WITH_ARG
_API_PATH_RE = re.compile(r"^/?repos/([^/]+)/([^/]+)/pulls/([^/]+)/merge$")


def _parse_field_kv(token: str) -> tuple[str, str] | None:
    """Return (key, value) for `-f`/`--field`-style assignments, if valid."""
    if "=" not in token:
        return None
    key, value = token.split("=", 1)
    key = key.lstrip("-")
    return key, value


def _parse_gh_api_merge(tail: list[str]) -> tuple[str | None, str | None, str | None, str | None, bool]:
    """Parse `gh api` merge endpoint.

    Returns:
        (identifier, repo, commit_title, merge_method, has_merge_endpoint)
    """
    identifier: str | None = None
    repo: str | None = None
    commit_title: str | None = None
    merge_method: str | None = None
    has_merge_endpoint = False

    i = 0
    while i < len(tail):
        tok = tail[i]
        if tok == "--":
            if i + 1 < len(tail):
                match = _API_PATH_RE.match(tail[i + 1].lstrip())
                if match:
                    has_merge_endpoint = True
                    identifier = match.group(3)
                    repo = f"{match.group(1)}/{match.group(2)}"
            break

        if tok in _SQUASH_FLAGS:
            # Defensive: ignore unrelated short flags in API payload parsing.
            pass

        if tok in ("-R", "--repo") and i + 1 < len(tail):
            repo = tail[i + 1]
            i += 1
            i += 1
            continue

        if tok.startswith("-R") and tok != "-R" and "=" in tok:
            repo = tok.split("=", 1)[1]

        if tok in ("-f", "--field", "--raw-field"):
            if i + 1 >= len(tail):
                i += 1
                continue
            pair = _parse_field_kv(tail[i + 1])
            if pair is not None:
                key, value = pair
                if key == "commit_title":
                    commit_title = value
                elif key == "merge_method":
                    merge_method = value.lower()
            i += 2
            continue

        if tok.startswith("-f") and not tok.startswith("-f=") and "=" in tok[2:]:
            pair = _parse_field_kv(tok[2:])
            if pair is not None:
                key, value = pair
                if key == "commit_title":
                    commit_title = value
                elif key == "merge_method":
                    merge_method = value.lower()

        if tok.startswith("--field=") or tok.startswith("--raw-field="):
            pair = _parse_field_kv(tok.split("=", 1)[1],)
            if pair is not None:
                key, value = pair
                if key == "commit_title":
                    commit_title = value
                elif key == "merge_method":
                    merge_method = value.lower()

        match = _API_PATH_RE.match(tok)
        if match:
            has_merge_endpoint = True
            identifier = match.group(3)
            repo = repo or f"{match.group(1)}/{match.group(2)}"

        i += 1

    return identifier, repo, commit_title, merge_method, has_merge_endpoint


def _gh_pr_merge_segment(argv: list[str]) -> tuple[list[str], str | None] | None:
    """Return (argv slice after `merge`, repo), or None if not `gh pr merge`.

    Mirrors `gh-merge-worktree-precondition`'s `_merge_segment` — `-R`/
    `--repo` (and the other value-taking gh global flags) are inherited
    cobra flags gh accepts before `pr`, between `pr` and `merge`, and after
    `merge` (the last placement handled by the tail scan below).
    """
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
        return None

    repo: str | None = None
    words: list[str] = []
    i = 1
    while i < len(argv) and len(words) < 2:
        tok = argv[i]
        if tok == "--":
            i += 1
            if i < len(argv):
                words.append(argv[i])
                i += 1
            continue
        if not tok.startswith("-"):
            words.append(tok)
            i += 1
            continue
        i += 1
        base, eq, inline = tok.partition("=")
        if eq and base in ("-R", "--repo"):
            repo = inline or None
        elif not eq and tok in _GH_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            if tok in ("-R", "--repo"):
                repo = argv[i]
            i += 1

    if words != ["pr", "merge"]:
        return None
    return argv[i:], repo


def _scan_merge_tail(tail: list[str]) -> tuple[bool, str | None, str | None, str | None]:
    """Single walk over the post-`merge` argv:
    (has_squash, identifier, repo, subject_override)."""
    has_squash = False
    identifier: str | None = None
    repo: str | None = None
    subject: str | None = None
    i = 0
    while i < len(tail):
        tok = tail[i]
        if tok == "--":
            if identifier is None and i + 1 < len(tail):
                identifier = tail[i + 1]
            break
        if not tok.startswith("-"):
            if identifier is None:
                identifier = tok
            i += 1
            continue
        base, eq, inline = tok.partition("=")
        if base in _SQUASH_FLAGS:
            has_squash = True
        if eq and base in ("-R", "--repo"):
            repo = inline or None
        elif eq and base in _SUBJECT_FLAGS:
            subject = inline
        i += 1
        if not eq and base in _TAIL_FLAGS_WITH_ARG and i < len(tail):
            if base in ("-R", "--repo"):
                repo = tail[i]
            elif base in _SUBJECT_FLAGS:
                subject = tail[i]
            i += 1
    return has_squash, identifier, repo, subject


def _gh_api_segment(argv: list[str]) -> tuple[list[str], str | None] | None:
    """Return `(argv_after_api, repo)` for `gh api` calls, or None."""
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
        return None

    repo: str | None = None
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "api":
            return argv[i + 1 :], repo

        if tok == "--":
            return None

        base, eq, inline = tok.partition("=")
        if eq and base in ("-R", "--repo"):
            repo = inline or None
        elif not eq and base in _GH_GLOBAL_FLAGS_WITH_ARG:
            if base in ("-R", "--repo") and i + 1 < len(argv):
                repo = argv[i + 1]
            i += 1
        i += 1

    return None


def _resolve_pr_title(identifier: str | None, cwd: str | None, repo: str | None) -> str | None:
    """`gh pr view <id> [-R repo] --json title -q .title`, fail-open on error."""
    cmd = ["gh", "pr", "view"]
    if identifier:
        cmd.append(identifier)
    if repo:
        cmd += ["-R", repo]
    cmd += ["--json", "title", "-q", ".title"]
    try:
        result = subprocess.run(
            cmd, cwd=cwd or None, capture_output=True, text=True,
            timeout=_GH_TIMEOUT_SEC, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    title = result.stdout.strip()
    return title or None


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


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _emit_ask(reason: str) -> None:
    emit_decision("ask", reason)


def _emit_squash_advisory(reason: str) -> None:
    """Stderr-only advisory for the squash-merge path (issue #843) — never
    `ask`, so a `gh pr view` network hiccup or an over-eager match cannot
    block a merge. See spec.md 'Severity: advisory, not ask' for rationale."""
    sys.stderr.write(f"[commit-title-length-check] {reason}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed
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
    cwd = payload.get("cwd") or None

    for argv in iter_command_starts(tokens):
        titles = extract_git_titles(argv, command)
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

        merge_segment = _gh_pr_merge_segment(argv)
        merge_api_segment = _gh_api_segment(argv)
        if merge_segment is None:
            if merge_api_segment is None:
                continue

            api_tail, api_repo = merge_api_segment
            identifier, segment_repo, commit_title, merge_method, has_merge_endpoint = _parse_gh_api_merge(api_tail)
            if not has_merge_endpoint or merge_method != "squash":
                continue

            title = commit_title
            if title is None:
                title = _resolve_pr_title(identifier, cwd, segment_repo or api_repo)
            if not title:
                continue

            if any(title.startswith(p) for p in SKIP_PREFIXES):
                continue
            length = len(title)
            if length > max_len:
                _emit_squash_advisory(
                    f"Squash-merge title too long: {length} chars (max {max_len}).\n"
                    f"Title: {title!r}\n"
                    "The PR title becomes the squash commit title on merge. Shorten "
                    "the PR title, pass `-t <title>` (≤50 chars) to override the "
                    "subject, or embed `# title-length:ack` to acknowledge."
                )
                return 0

            # The `gh api` branch is done with this argv — `merge_segment` is
            # None here, so falling through would unpack None.
            continue

        tail, repo = merge_segment
        has_squash, identifier, tail_repo, subject = _scan_merge_tail(tail)
        if not has_squash:
            continue
        repo = tail_repo or repo

        # `-t`/`--subject` overrides the PR title as the squash commit
        # title — no network call needed when present.
        title = subject if subject is not None else _resolve_pr_title(identifier, cwd, repo)
        if not title:
            continue  # unresolvable (fail-open) or genuinely empty
        if any(title.startswith(p) for p in SKIP_PREFIXES):
            continue
        length = len(title)
        if length > max_len:
            _emit_squash_advisory(
                f"Squash-merge title too long: {length} chars (max {max_len}).\n"
                f"Title: {title!r}\n"
                "The PR title becomes the squash commit title on merge. Shorten "
                "the PR title, pass `-t <title>` (≤50 chars) to override the "
                "subject, or embed `# title-length:ack` to acknowledge."
            )
            return 0  # advisory emitted; only report first violation

    return 0


if __name__ == "__main__":
    sys.exit(main())
