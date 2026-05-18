#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: surface relevant CLAUDE.md rules and memory
entries at high-momentum action points to prevent "Loaded ≠ Retrieved" failures.

Background (issue #326):
  2026-05-18 retrospect identified 3 friction events converging on the root
  cause "Loaded ≠ Retrieved at execution time" — rules and memory entries were
  loaded in context but failed retrieval at high-momentum moments such as
  multi-PR merge, dispatch, and force-push. This hook fires at exactly those
  moments and emits a structured reminder to stderr.

Trigger commands (Phase 1 — ANY single matching call triggers the surface;
  multi-mutation detection is deferred to Phase 2):
  • gh pr merge (any flags)
  • cmux new-workspace --command "claude -p ..." (dispatch via cmux)
  • git push --force-with-lease / git push -f / git push --force

Behavior:
  • Emits structured reminder to stderr ([praxis:momentum-gate] prefix per line)
  • Content is scoped per trigger type:
      gh pr merge        → Pre-Merge Reporting, No Approval Transfer Across
                           Companion PRs, memory feedback_pre_merge_briefing_compound_imperative
      cmux new-workspace → Pre-Implementation Surface Enumeration → Multi-PR /
                           multi-worktree shared state, Self-Authored Labels Are
                           Drafts, Not Ratified Scope
      force-push         → memory feedback_force_history_rewrite_mutation
  • Default mode: advisory (exit 0). Strict mode (PRAXIS_MOMENTUM_STRICT=1)
    exits 2 to block unless PRAXIS_MOMENTUM_ACK=1 is also set.
  • Bypass: PRAXIS_MOMENTUM_BYPASS=1 silences all output and exits 0.

Fail-open contract:
  • Malformed JSON / non-Bash payload → exit 0
  • Empty command → exit 0
  • Any uncaught exception in inner logic → swallowed, exit 0
  • python3 unavailable → shell wrapper exits 0
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

# ---------------------------------------------------------------------------
# Prefix used on every stderr line.
# ---------------------------------------------------------------------------

PREFIX = "[praxis:momentum-gate]"

# ---------------------------------------------------------------------------
# Rule surfaces per trigger type.
# ---------------------------------------------------------------------------

_MERGE_RULES = """\
{p} ── TRIGGER: gh pr merge ──────────────────────────────────
{p}
{p} Rule: Pre-Merge Reporting (CLAUDE.md)
{p}   Before asking for merge approval, report: What changed / What was
{p}   verified / What was NOT verified / Risk / Open items / Explicit ask.
{p}   "완료했습니다, 머지할까요?" without evidence is an anti-pattern.
{p}
{p} Rule: No Approval Transfer Across Companion PRs (CLAUDE.md)
{p}   Approving "merge PR X" approves ONLY X. Companion, chore, regen, or
{p}   hotfix-blocker PRs each need their own explicit approval.
{p}
{p} Memory: feedback_pre_merge_briefing_compound_imperative
{p}   Compound imperatives ("merge and clean both PR") do NOT lower the
{p}   Pre-Merge Reporting depth bar. Irreversible actions require 6-item
{p}   briefing regardless of phrasing.
{p}
{p} ─────────────────────────────────────────────────────────────""".format(p=PREFIX)

_DISPATCH_RULES = """\
{p} ── TRIGGER: cmux new-workspace (dispatch) ───────────────────
{p}
{p} Rule: Pre-Implementation Surface Enumeration → Multi-PR / multi-worktree
{p}   shared state (CLAUDE.md)
{p}   When dispatching independent PRs in parallel, enumerate every file each
{p}   PR touches that a sibling also touches. hooks.json, AGENTS.md/CLAUDE.md,
{p}   marketplace.json, plugin.json, hook-index tables — all shared. First PR
{p}   to merge wins; subsequent PRs need rebase.
{p}
{p} Rule: Self-Authored Labels Are Drafts, Not Ratified Scope (CLAUDE.md)
{p}   AI-written task labels during planning = drafts. Ambiguous verbs
{p}   (define / review / verify / clean up / 정의 / 검토 / 확인) in self-authored
{p}   labels → surface one-time disambiguation BEFORE execution.
{p}
{p} ─────────────────────────────────────────────────────────────""".format(p=PREFIX)

_FORCE_PUSH_RULES = """\
{p} ── TRIGGER: git push --force / --force-with-lease / -f ─────
{p}
{p} Memory: feedback_force_history_rewrite_mutation
{p}   force-with-lease / reset --hard / rebase --skip (worker commit drop)
{p}   are mutations — prior approval is NOT carried over, fixup commit is the
{p}   safe alternative. History rewrite requires fresh per-action consent.
{p}
{p} ─────────────────────────────────────────────────────────────""".format(p=PREFIX)

# ---------------------------------------------------------------------------
# GH global flags that consume one additional argument.
# ---------------------------------------------------------------------------

GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})

# git global flags that consume one additional SEPARATE-TOKEN argument
# (value). Verified against `man git` / `git -h`. When these appear between
# `git` and the subcommand AND are NOT in fused `--flag=value` form, both the
# flag AND its value token must be skipped so the subcommand check lands at
# the correct argv position.
#
# IMPORTANT — boolean flags MUST NOT be in this set. Round-3 fix: prior
# inclusion of `--literal-pathspecs` and `--super-prefix` (both boolean per
# `man git` / `git -h`) caused the walker to consume the NEXT token as a
# value, masking `push` and bypassing the force-push gate entirely. Example:
# `git --literal-pathspecs push --force` previously slipped through.
#
# `--exec-path` retained: bare `--exec-path` is a QUERY form that exits
# immediately (never reaches `push`), so treating it as value-consuming does
# not introduce a real bypass — only over-skips in a code path that does not
# reach runtime.
GIT_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-c", "-C",
    "--git-dir", "--work-tree", "--namespace",
    "--exec-path", "--config-env",
})


# ---------------------------------------------------------------------------
# Trigger detection helpers.
# ---------------------------------------------------------------------------

def _is_gh_pr_merge(argv: list[str]) -> bool:
    """Return True iff the argv segment is `gh pr merge`."""
    argv = strip_prefix(argv)
    if not argv or argv[0] != "gh":
        return False
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in GH_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            i += 1
    if i + 1 >= len(argv):
        return False
    return argv[i] == "pr" and argv[i + 1] == "merge"


def _is_cmux_dispatch(argv: list[str]) -> bool:
    """Return True iff the argv is `cmux new-workspace ... --command "<ai> ..."`.

    Round-2 fix (MAJOR 1): require `--command` value to contain an AI provider
    token (`claude` / `codex` / `gemini`) to avoid false positives on:
      • `cmux new-workspace test-foo` (plain workspace, no command)
      • `cmux new-workspace --command "echo hello"` (non-AI command)
    False positives train users to ignore the surface, defeating the gate.

    Detection contract:
      1. argv[0] == "cmux"
      2. argv contains "new-workspace" as a token
      3. argv contains `--command <value>` OR `--command=<value>` where the
         value contains a known AI provider name token.
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "cmux":
        return False
    if "new-workspace" not in argv[1:]:
        return False

    # Scan for --command and inspect its value.
    ai_provider_tokens = ("claude", "codex", "gemini")
    n = len(argv)
    for i, tok in enumerate(argv):
        value: str | None = None
        if tok == "--command" and i + 1 < n:
            value = argv[i + 1]
        elif tok.startswith("--command="):
            value = tok.split("=", 1)[1]
        if value is None:
            continue
        lowered = value.lower()
        if any(p in lowered for p in ai_provider_tokens):
            return True
    return False


def _is_force_push(argv: list[str]) -> bool:
    """Return True iff the argv is `git push` with a force flag.

    Round-2 fix (MAJOR 2): the prior bare-flag walker treated `-c key=val`
    as `(flag)(positional)` and landed the subcommand check on `key=val`,
    silently bypassing the gate for `git -c user.name=x push --force ...`.
    Mirror the gh global-flags-with-arg pattern: skip both the flag and its
    value token. `--flag=value` fused form needs only single-token skip.
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "git":
        return False
    # Walk past git global flags + their value tokens to find the subcommand.
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        # If the flag is a known value-taker AND in bare form (not --flag=value),
        # the next token is the value — skip it too.
        if "=" not in tok and tok in GIT_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            i += 1
    if i >= len(argv) or argv[i] != "push":
        return False
    # Scan remaining tokens for force flags.
    rest = argv[i + 1:]
    force_flags = {"--force", "-f", "--force-with-lease"}
    for tok in rest:
        bare = tok.split("=", 1)[0]
        if bare in force_flags or tok in force_flags:
            return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _detect_triggers(command: str) -> list[str]:
    """Return list of rule surfaces to emit for the given Bash command."""
    tokens = safe_tokenize(command)
    if not tokens:
        return []

    triggered: list[str] = []
    for argv in iter_command_starts(tokens):
        if _is_gh_pr_merge(argv):
            if _MERGE_RULES not in triggered:
                triggered.append(_MERGE_RULES)
        if _is_cmux_dispatch(argv):
            if _DISPATCH_RULES not in triggered:
                triggered.append(_DISPATCH_RULES)
        if _is_force_push(argv):
            if _FORCE_PUSH_RULES not in triggered:
                triggered.append(_FORCE_PUSH_RULES)
    return triggered


def _main_inner() -> int:
    # Bypass: scripted batch operations may set this to silence the gate.
    if os.environ.get("PRAXIS_MOMENTUM_BYPASS") == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed input

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    surfaces = _detect_triggers(command)
    if not surfaces:
        return 0

    # Emit each surface to stderr.
    for surface in surfaces:
        sys.stderr.write(surface + "\n")

    # Strict mode: block unless PRAXIS_MOMENTUM_ACK=1 is also present.
    if os.environ.get("PRAXIS_MOMENTUM_STRICT") == "1":
        if os.environ.get("PRAXIS_MOMENTUM_ACK") != "1":
            sys.stderr.write(
                f"{PREFIX} STRICT MODE — set PRAXIS_MOMENTUM_ACK=1 to proceed\n"
            )
            return 2

    return 0


def main() -> int:
    """Advisory hook — fail-open on infrastructure errors."""
    try:
        return _main_inner()
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
