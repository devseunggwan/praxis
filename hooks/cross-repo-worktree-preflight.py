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
  • `git -C <dir>` / `--git-dir=` / `--work-tree=` override present (the
    operator explicitly named the repo to target — trust the override)
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
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPT_OUT_MARKER = "# worktree-chain:ack"

# git global flags that take a separate-token argument and bind git to a
# specific repo / working tree. Their presence means the operator is already
# explicit about which repo to operate on, so the hook should not second-guess.
GIT_REPO_OVERRIDE_FLAGS_WITH_ARG = frozenset({"-C", "--git-dir", "--work-tree"})

# Every git global flag that consumes a separate-token argument. Used when
# walking past the global-flag block to reach the subcommand — we must skip
# the value token even for flags that do NOT bind git to a specific repo
# (e.g. `-c name=value`). Otherwise the value token (`name=value`) is read
# as a non-flag and we mis-conclude the subcommand is missing.
# Source: `git --help`. `--exec-path`, `--namespace`, `--super-prefix`,
# `--config-env` all use the `--flag=value` equals form which strip_prefix
# handles via the `"=" in tok` branch; only `-c` and `-C` actually accept
# a separate value token.
GIT_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-C", "-c",
    "--git-dir", "--work-tree",
})

# `git worktree remove` flags that take no argument. Any flag we don't know
# about is consumed generically (single token) — we only need to skip them
# until we hit the path positional.
WORKTREE_REMOVE_FLAGS = frozenset({"-f", "--force"})


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _has_repo_override(argv: list[str]) -> bool:
    """Return True if argv contains a git-level `-C` / `--git-dir` / `--work-tree`.

    Three forms covered:
      • separated:  `git -C /other worktree remove X`
      • equals:     `git --git-dir=/other/.git worktree remove X`
      • shorthand:  `git -C/other worktree remove X` (rare but legal)
    """
    for tok in argv:
        if tok in GIT_REPO_OVERRIDE_FLAGS_WITH_ARG:
            return True
        for prefix in GIT_REPO_OVERRIDE_FLAGS_WITH_ARG:
            if tok.startswith(prefix + "="):
                return True
        # `-C/other` shorthand (only -C supports this; --long= form handled above)
        if tok.startswith("-C") and len(tok) > 2:
            return True
    return False


def _interpret_cd(argv: list[str], current_cwd: str) -> str | None:
    """Return the new cwd after `cd <path>` in this segment, else None.

    Used to thread an `effective_cwd` across compound segments so that
    `cd /owning-repo && git worktree remove /owning-repo-wt` is treated
    as a same-cwd operation (the shell actually runs `cd` first, then
    `worktree remove` against the post-cd cwd). Without this, the hook
    falsely asks because it compares the target against the worktree
    list of the *original* process cwd.

    Resolution rules:
      • argv is not a bare `cd` (after strip_prefix) → None
      • argv is `cd` with no argument → None (cd-to-home; we do not try
        to resolve `$HOME` so we leave effective_cwd as the caller had it)
      • target is `-` or starts with `$` → None (cd-to-prev / variable
        expansion is unresolvable statically — caller retains current cwd)
      • absolute target → normalized path
      • relative target → normalized join against `current_cwd`
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "cd":
        return None
    if len(argv) < 2:
        return None
    target = argv[1]
    if target == "-" or target.startswith("$"):
        return None
    if target.startswith("/"):
        return os.path.normpath(target)
    return os.path.normpath(os.path.join(current_cwd, target))


def _coalesce_subst_runs(tokens: list[str]) -> list[str]:
    """Collapse unquoted `$(...)` command-substitution runs into single tokens.

    `safe_tokenize` uses shlex with whitespace_split=True, which is unaware
    of POSIX command substitution. An unquoted `$(echo k=v)` therefore
    splits into `['$(echo', 'k=v)']`, and any flag-value-skip logic only
    consumes the first token. The remaining token (`k=v)`) then sits where
    a subcommand or positional is expected and breaks parsing.

    This helper walks the token list and merges any run that starts with a
    token containing `$(` and ends with a token containing the matching `)`
    into a single logical token. Quoted substitutions (e.g. `"$(...)"`)
    are already a single token at this layer and need no special handling.

    Mirror of `_coalesce_subst_runs` in `cli-flag-incompat-advisory.py`
    (codex review round 2 sibling fix). The helper will move into
    `_hook_utils.py` at the third consumer per the project DRY-3 rule.
    """
    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if "$(" in tok and ")" not in tok[tok.index("$(") + 2:]:
            j = i + 1
            parts = [tok]
            while j < n:
                parts.append(tokens[j])
                if ")" in tokens[j]:
                    break
                j += 1
            out.append(" ".join(parts))
            i = j + 1
        else:
            out.append(tok)
            i += 1
    return out


def _extract_worktree_remove_path(argv: list[str]) -> str | None:
    """Return the path argument of `git worktree remove <path>`, else None.

    Walks past git global flags, locates the `worktree` `remove` subcommand
    pair, skips any `-f` / `--force` style flags, and returns the first
    positional argument (the path). Returns None when the command is not a
    `git worktree remove` call or the path is missing. Unquoted `$(...)`
    runs are coalesced before walking so a flag-with-arg value that
    contains a multi-token substitution is counted as exactly one token.
    """
    argv = _coalesce_subst_runs(strip_prefix(argv))
    if not argv or argv[0] != "git":
        return None

    # Skip git-level global flags to find the subcommand. Consume value
    # tokens for every known separated-arg global flag (not just repo-override
    # flags), otherwise a benign `git -c name=value worktree remove <path>`
    # bypasses detection because `name=value` is parsed as the subcommand.
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in GIT_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            i += 1  # consume value token for known flag-with-arg

    # Expect: argv[i] == "worktree", argv[i+1] == "remove"
    if i + 1 >= len(argv):
        return None
    if argv[i] != "worktree" or argv[i + 1] != "remove":
        return None
    i += 2

    # Skip remove-level flags to reach the path positional.
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1

    if i >= len(argv):
        return None
    return argv[i]


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

    tokens = safe_tokenize(command.replace("\\\n", " "))
    if not tokens:
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

    for argv in iter_command_starts(tokens):
        argv = list(argv)

        new_cwd = _interpret_cd(argv, effective_cwd)
        if new_cwd is not None:
            effective_cwd = new_cwd
            continue

        target = _extract_worktree_remove_path(argv)
        if target is None:
            continue

        # Skip when the operator already pinned a repo via `-C` / `--git-dir`.
        if _has_repo_override(argv):
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
