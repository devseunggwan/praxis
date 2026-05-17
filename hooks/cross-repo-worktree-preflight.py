#!/usr/bin/env python3
"""PreToolUse(Bash) guard: cross-repo worktree pre-flight for `git worktree remove`.

`git worktree remove <abs-path>` resolves `<abs-path>` against the cwd repo's
`.git/worktrees` registry. When the cwd belongs to repo A but the path lives
under repo B, git aborts with `fatal: '<path>' is not a working tree`. In a
chained command (`git worktree remove X && gh pr merge ...`) the chain halts
mid-flight, leaving a partial post-merge cleanup state.

Memory-only enforcement keeps failing — the rule "worktree-context-pre-git-op"
exists in CLAUDE.md but is not retrieved at command-composition time. This
hook moves enforcement to the Bash boundary (praxis issue #246, incident
laplacetec/laplace-dev-hub#2060).

Pattern detected:
  WORKTREE_REMOVE_CROSS_REPO — `git worktree remove <abs-path>` where the
  absolute path is NOT present in the cwd repo's `git worktree list`.

Sibling hooks that cover adjacent scenarios:
  cross-boundary-preflight.sh  → gh write subcommands across --repo boundary
  side-effect-scan.sh          → generic collateral side-effect ask
  pre-merge-approval-gate.sh   → gh pr merge per-PR approval

Skips (no-op pass-through):
  • `git -C <dir>` or `git --git-dir=<path>` override present (the operator
    explicitly repinned git's view of the repo — trust the override).
    `--work-tree` ALONE is NOT a repo override — it only relocates the
    worktree pointer while git still resolves `.git` from cwd, so the
    cross-repo registry mismatch still happens and the hook still fires.
  • a preceding `cd <path>` segment in the same compound command has
    already moved the effective cwd into the worktree's owning repo
    (the post-cd cwd's worktree list contains the target)
  • effective cwd is not a git repo (fail-open)
  • `git worktree list` invocation fails or times out (fail-open)
  • payload is malformed or tool is not Bash (fail-open)

Relative remove targets (`../sibling-wt`, `wt/`) are NO LONGER skipped
unconditionally. They are normalized against the effective cwd before
the worktree-list comparison so that `cd somewhere && git worktree
remove ../sibling-wt` is correctly classified by where it actually
lands on disk.

Role-aware tokenization (issue #263): the local `_coalesce_subst_runs`
mirror was removed; `tokenize_with_roles` now handles `$()` coalesce,
flag-value attribution (`-c $(echo k=v)`), and the `--` argv boundary
centrally. The caller iterates typed Token segments and only needs to
recognize `worktree remove` structure and `-C` / `--git-dir` overrides.

Opt-out: embed `# worktree-chain:ack` anywhere in the shell command portion
after manually confirming the target path. Marker placement inside heredoc
bodies is irrelevant here — `git worktree remove` does not accept heredoc
bodies as input.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    Token,
    TokenRole,
    compound_cascade_hint,
    filter_argv,
    tokenize_with_roles,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPT_OUT_MARKER = "# worktree-chain:ack"

# git global flags whose presence means git is pinned to a different repo's
# gitdir / working-cwd than the hook process — the operator has already
# named the target repo so the cross-repo guard should yield.
#
# `--work-tree` is intentionally NOT in this set. Without a paired
# `--git-dir`, `--work-tree` only relocates the worktree pointer; git still
# resolves `.git` from the hook process cwd, so a `worktree remove` call
# with bare `--work-tree=/other-repo` still consults the cwd repo's
# worktree registry and fails with `not a working tree` — exactly the
# scenario this hook is built to surface.
GIT_REPO_OVERRIDE_FLAGS_WITH_ARG = frozenset({"-C", "--git-dir"})

# Role-aware tokenizer spec. Every git-level value-taking global flag is
# included so the role API attributes their value tokens as FLAG_VALUE
# (otherwise the next token slips into the subcommand slot and the walker
# bails). Subcommand level for `git worktree` has no separate-value flags
# we care about — `-f` / `--force` are valueless and fall through generically.
# Source: `git --help`. `--exec-path`, `--namespace`, `--super-prefix`,
# `--config-env` all use the `--flag=value` equals form which is a single
# token; only `-c` / `-C` / `--git-dir` / `--work-tree` / `--namespace`
# accept a separate value token.
_FLAG_VALUE_SPEC: dict[str, set[str]] = {
    "git": {"-C", "-c", "--git-dir", "--work-tree", "--namespace"},
    "git worktree": set(),
}


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _has_repo_override(seg: list[Token]) -> bool:
    """Return True if seg contains a git-level `-C` / `--git-dir` override.

    Three forms covered:
      • separated:  `git -C /other worktree remove X`
      • equals:     `git --git-dir=/other/.git worktree remove X`
      • shorthand:  `git -C/other worktree remove X` (rare but legal)

    `--work-tree` alone does NOT qualify (see GIT_REPO_OVERRIDE_FLAGS_WITH_ARG
    docstring). The role API has already split FLAG / FLAG_VALUE for the
    separated form; we iterate FLAG tokens and inspect their text.
    """
    argv = filter_argv(seg)
    for tok in argv:
        if tok.role != TokenRole.FLAG:
            continue
        text = tok.text
        if text in GIT_REPO_OVERRIDE_FLAGS_WITH_ARG:
            return True
        for prefix in GIT_REPO_OVERRIDE_FLAGS_WITH_ARG:
            if text.startswith(prefix + "="):
                return True
        # `-C/other` shorthand (only -C supports this; --long= form handled above)
        if text.startswith("-C") and len(text) > 2:
            return True
    return False


def _interpret_cd(seg: list[Token], current_cwd: str) -> str | None:
    """Return the new cwd after `cd <path>` in this segment, else None.

    Used to thread an `effective_cwd` across compound segments so that
    `cd /owning-repo && git worktree remove /owning-repo-wt` is treated
    as a same-cwd operation (the shell actually runs `cd` first, then
    `worktree remove` against the post-cd cwd). Without this, the hook
    falsely asks because it compares the target against the worktree
    list of the *original* process cwd.

    Resolution rules:
      • COMMAND is not `cd` → None
      • no positional argument → None (cd-to-home; we do not try
        to resolve `$HOME` so we leave effective_cwd as the caller had it)
      • target is `-` or starts with `$` → None (cd-to-prev / variable
        expansion is unresolvable statically — caller retains current cwd)
      • absolute target → normalized path
      • relative target → normalized join against `current_cwd`
    """
    argv = filter_argv(seg)
    if not argv or argv[0].text != "cd":
        return None
    # Find the first positional / subst_run / post-DD token after COMMAND.
    target: str | None = None
    for tok in argv[1:]:
        if tok.role in (TokenRole.FLAG, TokenRole.FLAG_VALUE, TokenRole.SEPARATOR_DD):
            continue
        target = tok.text
        break
    if not target:
        return None
    if target == "-" or target.startswith("$"):
        return None
    if target.startswith("/"):
        return os.path.normpath(target)
    return os.path.normpath(os.path.join(current_cwd, target))


def _extract_worktree_remove_path(seg: list[Token]) -> str | None:
    """Return the path argument of `git worktree remove <path>`, else None.

    Walks the typed Token list:
      1. argv[0] must be COMMAND `git`.
      2. Skip FLAG / FLAG_VALUE tokens (git globals — value tokens have
         already been attributed via _FLAG_VALUE_SPEC).
      3. The next POSITIONAL must be `worktree`.
      4. Skip more FLAG / FLAG_VALUE tokens.
      5. The next POSITIONAL must be `remove`.
      6. Skip remove-level FLAG / FLAG_VALUE tokens (`-f`, `--force`).
      7. The next POSITIONAL / SUBST_RUN / POST_DD text is the path.

    Returns None for any other shape (non-git, non-worktree-remove).
    """
    argv = filter_argv(seg)
    if not argv or argv[0].text != "git":
        return None

    n = len(argv)
    i = 1

    # Step 2-3: find `worktree`.
    while i < n:
        tok = argv[i]
        if tok.role == TokenRole.POSITIONAL:
            if tok.text == "worktree":
                break
            return None  # different git subcommand
        if tok.role in (TokenRole.SEPARATOR_DD, TokenRole.POST_DD, TokenRole.SUBST_RUN):
            return None
        # FLAG / FLAG_VALUE — skip
        i += 1
    if i >= n:
        return None
    i += 1  # past 'worktree'

    # Step 4-5: find `remove`.
    while i < n:
        tok = argv[i]
        if tok.role == TokenRole.POSITIONAL:
            if tok.text == "remove":
                break
            return None  # different worktree subcommand
        if tok.role in (TokenRole.SEPARATOR_DD, TokenRole.POST_DD, TokenRole.SUBST_RUN):
            return None
        i += 1
    if i >= n:
        return None
    i += 1  # past 'remove'

    # Step 6-7: find the path positional.
    while i < n:
        tok = argv[i]
        if tok.role == TokenRole.SEPARATOR_DD:
            i += 1
            continue
        if tok.role in (TokenRole.FLAG, TokenRole.FLAG_VALUE):
            i += 1
            continue
        if tok.role in (TokenRole.POSITIONAL, TokenRole.SUBST_RUN, TokenRole.POST_DD):
            return tok.text
        i += 1
    return None


def _normalize_path(path: str) -> str:
    """Canonicalize a path for string comparison against `git worktree list`.

    `git worktree list --porcelain` always emits the realpath of each
    worktree (symlinks resolved, no trailing slash). On macOS `/var` is a
    symlink to `/private/var`, so a user-supplied path under `/tmp` resolves
    to `/private/tmp/...` in the porcelain output. Run the same resolution
    on both sides so paths that point to the same on-disk location compare
    equal. Realpath is purely a string-equality preparation step here, not
    a permission probe.
    """
    return os.path.realpath(path.rstrip("/"))


def _list_cwd_worktrees(cwd: str) -> list[str] | None:
    """Return absolute paths of every worktree registered in cwd's git repo.

    Uses `git -C <cwd> worktree list --porcelain` to get a stable parse
    surface. Returns None on any failure (non-git cwd, timeout, malformed
    output) — caller treats None as fail-open.
    """
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    paths: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.append(_normalize_path(line[len("worktree "):]))
    return paths


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _build_ask_reason(target_path: str, known: list[str]) -> str:
    known_block = "\n".join(f"    {p}" for p in known) if known else "    (none — cwd may not be a git repo)"
    return (
        "⚠️  Cross-repo worktree pre-flight: "
        f"`git worktree remove {target_path}`\n"
        "\n"
        f"Target path is NOT registered in the cwd repo's worktree list:\n"
        f"{known_block}\n"
        "\n"
        "`git worktree remove` resolves the path against the cwd repo's\n"
        "`.git/worktrees` registry. If the path belongs to a different repo,\n"
        "git aborts with `fatal: '<path>' is not a working tree` and any\n"
        "chained command (`&& gh pr merge ...`) halts mid-flight.\n"
        "\n"
        "Recommended:\n"
        "  • cd into the worktree's owning repo first, then run remove there\n"
        "  • or pin git to the owning repo: `git -C <owning-repo> worktree remove <path>`\n"
        "  • or append `# worktree-chain:ack` after confirming the target\n"
    )


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
        return 0  # fail-open on malformed input

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    if OPT_OUT_MARKER in command:
        return 0

    segments = tokenize_with_roles(command.replace("\\\n", " "), _FLAG_VALUE_SPEC)
    if not segments:
        return 0

    # `effective_cwd` is updated by each preceding `cd <path>` segment so
    # that a compound command like `cd /B && git worktree remove /B-wt`
    # is evaluated against /B's worktree list, not the hook process's
    # original cwd. Relative remove targets are also resolved against
    # this effective_cwd (matching the shell's behavior at the time `git
    # worktree remove` actually runs).
    effective_cwd = os.getcwd()

    known: list[str] | None = None
    known_for_cwd: str | None = None

    for seg in segments:
        new_cwd = _interpret_cd(seg, effective_cwd)
        if new_cwd is not None:
            effective_cwd = new_cwd
            continue

        target = _extract_worktree_remove_path(seg)
        if target is None:
            continue

        # Skip when the operator already pinned a repo via `-C` / `--git-dir`.
        if _has_repo_override(seg):
            continue

        # Resolve relative remove targets against effective_cwd. git
        # itself does this before consulting its worktree registry, so
        # `git worktree remove ../sibling-wt` from repo A's worktree
        # can absolutely be cross-repo and must NOT be skipped silently.
        if not target.startswith("/"):
            target = os.path.normpath(os.path.join(effective_cwd, target))

        target_norm = _normalize_path(target)

        # Lazy enumerate; re-enumerate when effective_cwd changes between
        # segments so we always compare against the same-cwd repo's list.
        if known is None or known_for_cwd != effective_cwd:
            known = _list_cwd_worktrees(effective_cwd)
            known_for_cwd = effective_cwd
            if known is None:
                return 0  # fail-open: effective cwd not a git repo / git failed

        if target_norm in known:
            continue  # target is owned by effective-cwd repo — safe pass-through

        reason = _build_ask_reason(target_norm, known) + compound_cascade_hint(command)
        _emit_ask(reason)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
