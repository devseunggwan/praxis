#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: cwd-dependent relative execution while 2+
worktrees are registered.

Issue #852. The session cwd resetting to the default worktree (usually
`main`) between Bash calls is a documented failure mode (memory
`verify worktree context before git-state CLI invocation`) — but that memory
only fires on git-state CLI keywords (`gh`, `git worktree`, `/codex:review`),
not on an arbitrary relative-path script execution. 2026-07-24: `./scripts/
run-tests.sh` was run without a preceding `cd`, the session cwd had reset to
`main`, and tests silently ran against the wrong worktree — 5 recurrences in
one session, none matched by the memory's hookKeywords.

This hook closes that specific gap: it fires when a Bash command executes a
relative path (a `./`, `../`, or bare `dir/file` command, or a relative
`--body-file`/`-F` value) with NO preceding `cd`/`pushd` segment in the SAME
command, while `git worktree list` shows 2+ registered worktrees (single-
worktree sessions have no cwd ambiguity to warn about).

Silent cases (by design — see spec.md for the full enumerated surface):
  - Absolute path execution (`/abs/script.sh`) — cwd-independent, always
    resolves to the same file regardless of which worktree the shell is in.
  - `~` / `~user` tilde expansion — same reasoning as the sibling
    `bash-worktree-existence-advisory` (hook-process $HOME may differ).
  - `$VAR`-interpolated paths — unresolvable at hook time, no ambiguity claim
    can be made either way.
  - A `cd <path>` (or `pushd`) segment anywhere BEFORE the relative-exec
    segment in the same command — the cwd is explicit in-command.
  - Fewer than 2 registered worktrees — no ambiguity to flag.
  - Opt-out marker `# cwd-advisory:ack` anywhere in the command.

Channel — this is a PreToolUse `ask` decision (not a stderr advisory). The
issue's own root-cause analysis is that `memory-hint`'s stderr channel never
reaches the model (canary #841); an advisory that repeats that channel would
reproduce the exact gap it is meant to close. `ask` is a soft gate — the
model sees the reason and can proceed after acknowledging, mirroring
`output-block-falsify-advisory`'s T2 tier.

Fail-open contract:
  - Malformed JSON / non-Bash tool / empty command → exit 0, silent
  - `git worktree list` failure / timeout / non-git cwd → exit 0, silent
    (cannot confirm ambiguity, so no claim is made either way)
  - Any uncaught exception → exit 0 (`@fail_open`)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    Token,
    TokenRole,
    filter_argv,
    tokenize_with_roles,
)

OPT_OUT_MARKER = "# cwd-advisory:ack"

# Flags whose value is a path that changes meaning with cwd. Scoped to the
# issue's concrete example (`gh pr/issue create|comment --body-file`) rather
# than a generic file-flag heuristic, which would misfire on unrelated `-f`
# usages across arbitrary CLIs.
_PATH_FLAG_SPEC: dict[str, set[str]] = {
    "gh": {"--body-file", "-F"},
}

_CD_LIKE = ("cd", "pushd")


def _is_unresolvable_or_absolute(target: str) -> bool:
    """True for targets this hook makes no ambiguity claim about."""
    if not target:
        return True
    if target.startswith("/"):
        return True  # absolute — cwd-independent
    if target.startswith("~"):
        return True  # tilde expansion — hook $HOME may differ from agent's
    if target.startswith("$"):
        return True  # env-var interpolation — unresolvable at hook time
    return False


def _segment_is_cd(seg: list[Token]) -> bool:
    argv = filter_argv(seg)
    if not argv:
        return False
    cmd = argv[0].text
    if cmd.startswith("("):
        cmd = cmd[1:]
    if cmd == "(" and len(argv) >= 2:
        cmd = argv[1].text
    return cmd in _CD_LIKE


def _relative_command_target(seg: list[Token]) -> str | None:
    """Return the relative script path if this segment's COMMAND token is one."""
    for tok in seg:
        if tok.role != TokenRole.COMMAND:
            continue
        text = tok.text
        if text.startswith("("):
            text = text[1:]
        if "/" not in text:
            return None  # bare command name (PATH-searched), not a path
        if _is_unresolvable_or_absolute(text):
            return None
        return text
    return None


def _relative_flag_target(seg: list[Token]) -> str | None:
    """Return the relative path flag value if this segment carries a
    registered path-flag (`--body-file` / `-F`, ...) with a relative value."""
    for tok in seg:
        if tok.role != TokenRole.FLAG_VALUE:
            continue
        flag_name = getattr(tok, "flag_name", None)
        if flag_name is None:
            continue
        matched = any(flag_name in flags for flags in _PATH_FLAG_SPEC.values())
        if not matched:
            continue
        if _is_unresolvable_or_absolute(tok.text):
            continue
        return tok.text
    return None


def _worktree_count() -> int:
    """Count registered worktrees via `git worktree list --porcelain`.

    Returns 0 on any failure (non-git cwd, timeout, missing binary) — the
    caller treats 0 as "cannot confirm ambiguity", i.e. stays silent.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return 0
    if result.returncode != 0:
        return 0
    return sum(1 for line in result.stdout.splitlines() if line.startswith("worktree "))


def _find_finding(command: str) -> str | None:
    """Return the offending relative-path text, or None if silent."""
    segments = tokenize_with_roles(command.replace("\\\n", " "), _PATH_FLAG_SPEC)
    if not segments:
        return None

    cd_seen = False
    for seg in segments:
        if _segment_is_cd(seg):
            cd_seen = True
            continue
        if cd_seen:
            continue
        target = _relative_command_target(seg) or _relative_flag_target(seg)
        if target is not None:
            return target
    return None


_MESSAGE_TMPL = (
    "[cwd-relative-exec-advisory] relative path `{target}` executed with no "
    "preceding `cd` in this command, and {count} worktrees are registered — "
    "the actual cwd this Bash call runs in may not be the intended worktree "
    "(session cwd can reset to the default worktree between calls).\n"
    "[cwd-relative-exec-advisory] Verify the effective cwd first (`pwd`, "
    "`git worktree list`) or chain `cd <worktree> && {target}` in one call. "
    "Ack and proceed if the cwd is already correct."
)


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "") or ""
    if not isinstance(command, str) or not command.strip():
        return 0

    if OPT_OUT_MARKER in command:
        return 0

    count = _worktree_count()
    if count < 2:
        return 0

    target = _find_finding(command)
    if target is None:
        return 0

    emit_decision("ask", _MESSAGE_TMPL.format(target=target, count=count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
