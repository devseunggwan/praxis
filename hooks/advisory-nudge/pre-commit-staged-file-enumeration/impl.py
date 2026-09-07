#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: surface staged file ADDITIONS that were not
created through a Write / Edit / NotebookEdit tool call this session.

Rule (AGENTS.md `Bash Redirect on Existing Path` / `Atomic Commits`; memory
`feedback_pre_commit_staged_file_enumeration`): a file produced by a shell
heredoc, a `> file` redirect, or an external script is staged OUTSIDE the
Write/Edit read-before-write tracking, so it can ride into a `git commit`
unnoticed. Before the commit runs, list the staged additions the agent never
touched through a file tool so it can consciously confirm they are intended.

Precision (why transcript-based, not "list every addition"): surfacing *every*
staged addition would fire on the normal case (files the agent wrote via Write)
and become noise the agent learns to ignore. Instead the hook subtracts the set
of Write/Edit/MultiEdit/NotebookEdit target paths recorded in this session's
transcript (SUCCESSFUL ones only — a failed/rejected file tool did not create
the file) from the staged additions, and surfaces only the remainder — the
files that entered staging through some other path (bash redirect, heredoc,
external script, `git add` of a pre-existing untracked file).

Advisory only (stderr, exit 0 always). A bash-created file is sometimes an
intended artifact, so this nudges rather than blocks.

Silent cases (no output):
  - non-Bash tool, empty command, or no live `git commit` in the command
  - `git commit --dry-run` / `--help` / `-h` (commit nothing); merge / rebase /
    cherry-pick / revert (not the `commit` subcommand). NOTE: `--amend` is NOT
    silent — it folds staged additions into the replacement commit
  - no staged additions (`git diff --cached --diff-filter=A` empty)
  - every staged addition maps to a successful file-tool target path
  - `transcript_path` missing/unreadable/oversized → fail-open (cannot compute
    the seen-set, so surfacing all additions would be noise) — SILENT
  - not inside a git work tree, or any git subprocess fails → fail-open
  - opt-out marker `# [staged-enum-ack]` anywhere in the command
  - PRAXIS_SKIP_STAGED_FILE_ENUM=1 in the environment

Limitations:
  - Root-session transcript only: a file written by a Task/Agent subagent
    (sibling `subagents/agent-*.jsonl`) is not in the root seen-set, so it may
    be surfaced — a conservative false-surface (nudge, never a block).
  - Single-call create+stage+commit is NOT caught: PreToolUse fires BEFORE the
    command runs, so in `printf x > f && git add f && git commit -m y` the file
    is not yet in the index at scan time and the additions list is empty. The
    hook targets the dominant flow where `git commit` is a discrete step after
    the file was created/staged in an earlier call. Parsing `git add` arguments
    to recover the in-call staging set was deliberately rejected as a fragile
    shell-parser (memory `feedback_shell_parser_diminishing_returns`); the
    boundary is documented rather than chased.
  - `git -C <dir> commit` is detected but the diff is read from the hook process
    cwd, not `<dir>` (matches the sibling hooks' cwd-scoped git queries).
  - Shared-tokenizer detection boundaries (identical to the blocking sibling
    `block-rename-sweep-survivors`): `result=$(git commit ...)` is missed;
    partial commits (`--only` / pathspec) over-list all staged additions. The
    heredoc-body false-surface listed here was fixed in `_hook_utils` (#985).
    See spec.md "Detection boundaries" — the rest belong there too, not here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _git import run_git  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _transcript import TranscriptReadError, iter_transcript_bounded  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

# Per-probe cap for each read-only git call. Clamped to the dispatcher's
# remaining member budget at the call site, so the SUM of several calls
# stays inside the member budget (issue #1167).
_GIT_TIMEOUT_SEC = 5


OPT_OUT_MARKER = "# [staged-enum-ack]"
_MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024
# Pre-parse rejects for `_seen_realpaths` — see the loop there.
_TOOL_USE_NEEDLE = b'"tool_use"'
_IS_ERROR_NEEDLE = b'"is_error"'

# git global flags that consume the next token as a value — skipped when
# scanning for the subcommand position.
_GIT_GLOBAL_VALUE_FLAGS: frozenset[str] = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env",
     "--exec-path", "--super-prefix"}
)
# Terminal global options print help/version instead of running the subcommand.
_TERMINAL_GLOBAL_FLAGS: frozenset[str] = frozenset({"--help", "-h", "--version"})
# Flags that make `git commit` commit NOTHING (so no staged addition can ride
# in): --dry-run prints status; --help/-h print the manpage. --amend is NOT
# exempt — it folds any currently-staged additions into the replacement commit,
# so a bash-staged file rides in exactly as with a plain commit. --allow-empty*
# also commit staged content and are likewise not exempt.
_EXEMPT_COMMIT_FLAGS: frozenset[str] = frozenset({"--dry-run", "--help", "-h"})
# File-tool blocks whose target path counts as "seen this session".
_FILE_TOOL_KEYS = {"Write": "file_path", "Edit": "file_path",
                   "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}


def _is_git_binary(token: str) -> bool:
    stripped = token.lstrip("(){}$`")
    return stripped == "git" or stripped.endswith("/git")


def _canonical(path: str) -> str:
    """Canonicalize the PARENT dir but preserve the final path component.

    `os.path.realpath` resolves symlinks in the final component too, so a new
    symlink `alias` -> `target` would canonicalize to the same string as
    `target`; if a Write touched `target`, the symlink addition would be wrongly
    treated as seen. Resolving only the parent keeps `alias` and `target`
    distinct while still neutralizing repo-relative-vs-absolute and
    symlinked-parent-directory differences.
    """
    return os.path.join(
        os.path.realpath(os.path.dirname(path)), os.path.basename(path)
    )


def _has_live_git_commit(command: str) -> bool:
    """True iff the command contains a fresh `git commit`.

    `--amend` counts as live: it folds staged additions into the replacement
    commit. Only `--dry-run` / `--help` / `-h` (commit nothing) are exempt.
    """
    tokens = safe_tokenize(command)
    if not tokens:
        return False
    for argv in iter_command_starts(tokens):
        argv = strip_prefix(argv)
        if not argv or not _is_git_binary(argv[0]):
            continue
        i = 1
        while i < len(argv):
            tok = argv[i]
            if tok in _TERMINAL_GLOBAL_FLAGS:
                i = len(argv)  # `git --help commit` runs no commit
                break
            if tok in _GIT_GLOBAL_VALUE_FLAGS and i + 1 < len(argv):
                i += 2
                continue
            if tok.startswith("-"):
                i += 1
                continue
            break  # first non-flag = subcommand
        if i >= len(argv) or argv[i] != "commit":
            continue
        if any(tok in _EXEMPT_COMMIT_FLAGS for tok in argv[i + 1:]):
            continue
        return True
    return False


def _git(args: list[str]) -> str | None:
    """Run a read-only git command; return stdout or None on any failure.

    Several of these run per invocation. The shared runner (hooks/_lib/_git.py,
    issue #1178) reads the remaining member budget on each call, so their SUM
    stays inside it (issue #1167).
    """
    return run_git(args, timeout=_GIT_TIMEOUT_SEC)


def _staged_addition_realpaths(repo_root: str) -> list[tuple[str, str]] | None:
    """Return (repo_relative, canonical-path) for each staged addition, or None."""
    out = _git(["diff", "--cached", "--diff-filter=A", "--name-only", "-z"])
    if out is None:
        return None
    rels = [p for p in out.split("\0") if p]
    return [(rel, _canonical(os.path.join(repo_root, rel))) for rel in rels]


def _seen_realpaths(transcript_path: str) -> set[str] | None:
    """Canonical paths of every SUCCESSFUL Write/Edit/MultiEdit/NotebookEdit
    target in the transcript.

    A file tool whose `tool_result` carries `is_error: true` (a rejected or
    failed Write) did NOT create the file, so its target must NOT count as seen
    — otherwise a file later created via bash at the same path would be wrongly
    suppressed. tool_use blocks are correlated to their tool_result by
    `id` <-> `tool_use_id`; a use whose id later appears in the failed set is
    dropped.

    Returns None when the transcript is missing/unreadable/oversized — the
    caller treats None as fail-open (cannot compute the seen-set)."""
    pending: dict[str, str] = {}   # tool_use_id -> canonical path
    idless: set[str] = set()       # file-tool targets that carry no id
    failed: set[str] = set()       # tool_use_ids whose result is_error
    # Only two record kinds feed the sets: a `tool_use` block (a file-tool
    # target) and an errored `tool_result`. A line with neither literal is
    # rejected before the parse (issue #1312); the reader streams the file
    # under the byte bound instead of the former read_text() + splitlines(),
    # which held the whole session twice on every `git commit` inside the
    # shared Bash dispatch deadline.
    try:
        for obj in iter_transcript_bounded(
            transcript_path, _MAX_TRANSCRIPT_BYTES, (_TOOL_USE_NEEDLE, _IS_ERROR_NEEDLE)
        ):
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_result":
                    if block.get("is_error"):
                        tid = block.get("tool_use_id")
                        if isinstance(tid, str):
                            failed.add(tid)
                    continue
                if btype != "tool_use":
                    continue
                name = block.get("name")
                key = _FILE_TOOL_KEYS.get(name) if isinstance(name, str) else None
                if key is None:
                    continue
                tool_input = block.get("input")
                if not isinstance(tool_input, dict):
                    continue
                path = tool_input.get(key)
                if not (isinstance(path, str) and path):
                    continue
                canon = _canonical(path)
                use_id = block.get("id")
                if isinstance(use_id, str) and use_id:
                    pending[use_id] = canon
                else:
                    idless.add(canon)

    except TranscriptReadError:  # missing, unreadable, or past the bound
        return None

    seen = {c for uid, c in pending.items() if uid not in failed}
    seen |= idless
    return seen


def _emit(unseen: list[str]) -> None:
    shown = unseen[:10]
    extra = len(unseen) - len(shown)
    listing = "\n".join(f"  {rel}" for rel in shown)
    if extra:
        listing += f"\n  ... +{extra} more"
    # No compound_cascade_hint here: that hint asserts the command was ABORTED
    # (PreToolUse rejection), but this hook only nudges (exit 0) — the commit
    # runs to completion, so the abort-semantics text would misfire.
    sys.stderr.write(
        "[staged-enum] "
        f"{len(unseen)} staged addition(s) were not created via Write/Edit/"
        "NotebookEdit this session:\n"
        f"{listing}\n"
        "Confirm these are intended before committing (heredoc / `> file` / "
        "external-script output can stage unintended files).\n"
        f"Ack with `{OPT_OUT_MARKER}` in the command, or set "
        "PRAXIS_SKIP_STAGED_FILE_ENUM=1 to silence.\n"
    )


@fail_open
def main() -> int:
    if os.environ.get("PRAXIS_SKIP_STAGED_FILE_ENUM") == "1":
        return 0

    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed
    if not command.strip() or OPT_OUT_MARKER in command:
        return 0
    if not _has_live_git_commit(command):
        return 0

    repo_root = _git(["rev-parse", "--show-toplevel"])
    if repo_root is None:
        return 0
    repo_root = repo_root.strip()

    additions = _staged_addition_realpaths(repo_root)
    if not additions:
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0  # cannot compute seen-set → fail-open (silent)
    seen = _seen_realpaths(transcript_path)
    if seen is None:
        return 0  # unreadable transcript → fail-open (silent)

    unseen = [rel for rel, real in additions if real not in seen]
    if unseen:
        _emit(unseen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
