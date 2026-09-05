#!/usr/bin/env python3
"""UserPromptSubmit hook: codex-review worktree / PR-state advisories.

Fires when the user invokes /codex:review (bare) or codex-review-wrap.
Emits additionalContext advisories for:
  1. Multi-worktree detected (>= 2 non-bare worktrees) — bare /codex:review only.
     codex-review-wrap is suppressed here because the skill itself handles
     worktree disambiguation (Step 2).
  2. Current branch's PR is CLOSED or MERGED — stale-state guard for both triggers.
     Fix commits pushed after a closed PR create orphan branches or reopen
     closed PRs unintentionally.

Ported from `impl.sh` (issue #1304, decision (a): shell hooks move to Python,
smallest first). Behaviour is byte-identical on stdout: the same two trigger
regexes with the same word-boundary semantics, the same worktree block
parsing, the same message text, and the same jq-shaped pretty-printed JSON
(2-space indent, raw non-ASCII, trailing newline) — so transcript greps and
downstream consumers see exactly what the shell version wrote.

Fail-open: malformed JSON, no git repo, gh absent, or any unexpected error
all exit 0 silently — `@fail_open` covers the last of those. The hook never
blocks a prompt.

Fire ledger: the advising path writes one RICH `advise` record through
`_fire_ledger.record_session_fire` (the shell version reached the ledger
through `_lib/record_fire.sh`); silent runs are left to `@fail_open`'s
coarse `pass`, the same split every other advisory-nudge Python hook uses.

Subprocesses (`git worktree list`, `git rev-parse`, `gh pr view`) share one
budget derived from the manifest timeout via `shared_probe_deadline`, so
their SUM stays inside the hook's wall-clock allowance (issue #1167).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402
from _git import run_git  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    fail_open,
    shared_probe_deadline,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402

_HOOK_NAME = "codex-review-route"
_ROLE = "advisory-nudge"

# Must match this hook's manifest `timeout`; shared_probe_deadline subtracts
# its own margin for interpreter startup and process spawn.
_MANIFEST_TIMEOUT_SEC = 5

# Per-call ceilings, clamped further by whatever the shared deadline has left.
_GIT_TIMEOUT_SEC = 2.0
_GH_TIMEOUT_SEC = 3.0

# Trigger detection. The shell version used bash `[[ =~ ]]` (POSIX ERE):
# `[[:space:]]` is the ASCII whitespace set, so the class is spelled out
# rather than `\s` (which would also accept Unicode spaces such as U+00A0),
# and `$` in ERE is end-of-string only, which the explicit class already
# covers for a trailing newline.
_WS = r"[ \t\n\r\f\v]"
# Bare invocation: prompt starts with /codex:review or /codex-review.
_BARE_CODEX_REVIEW_RE = re.compile(rf"^/codex(:|-)review({_WS}|$)")
# Wrapper skill invocation: /praxis:codex-review-wrap or codex-review-wrap.
_CODEX_REVIEW_WRAP_RE = re.compile(rf"^(/praxis:)?codex-review-wrap({_WS}|$)")

_PRUNABLE_RE = re.compile(r"^prunable( |$)")
_STALE_PR_STATES = ("CLOSED", "MERGED")


def count_active_worktrees(porcelain: str) -> int:
    """Count non-bare, non-prunable worktree blocks in `--porcelain` output.

    `git worktree list --porcelain` emits one block per entry (blank-line
    separated), each starting with `worktree <path>`. Bare repos have a `bare`
    line; stale worktrees have a `prunable` line. Both must be excluded —
    counting raw `worktree` lines would over-count bare-repo + linked-worktree
    setups and trigger false-positive warnings on single-target reviews.

    Mirrors the shell version's awk block parser exactly, including the
    trailing-block case: the shell captured the output through `$(...)`,
    which strips the final blank line, so the last block was only counted at
    END. Here every block is flushed on its blank line or at end-of-input,
    which yields the same count whether or not the trailing blank survives.
    """
    count = 0
    in_block = False
    is_bare = False
    is_prunable = False
    for line in porcelain.splitlines():
        if line == "":
            if in_block and not is_bare and not is_prunable:
                count += 1
            in_block = False
            is_bare = False
            is_prunable = False
            continue
        if line.startswith("worktree "):
            in_block = True
        if line == "bare":
            is_bare = True
        if _PRUNABLE_RE.match(line):
            is_prunable = True
    if in_block and not is_bare and not is_prunable:
        count += 1
    return count


def _worktree_message(count: int) -> str:
    return (
        f"⚠️ Multi-worktree detected ({count} active worktrees) for a "
        "/codex:review invocation.\n"
        "\n"
        "Bare /codex:review uses the Bash tool's session-default cwd, which "
        "often differs from the worktree the user actually wants to review — "
        "producing an empty or wrong-target diff.\n"
        "\n"
        "Recommended action: instead of dispatching /codex:review directly, "
        "ask the user to run /praxis:codex-review-wrap. The wrapper enumerates "
        "worktrees, prompts for explicit selection, and delegates to "
        "/codex:review with the correct cwd.\n"
        "\n"
        "If the user explicitly confirms the target worktree in this turn, you "
        "may proceed — but state the resolved cwd in your reply so the choice "
        "is visible."
    )


def _pr_state_message(branch: str, state: str) -> str:
    return (
        f"⚠ Current worktree's branch ({branch}) maps to a PR which is "
        f"{state}. Review is unlikely to be actionable — fix commits pushed "
        f"after review will target a {state} branch, which may create an "
        "orphan branch or reopen a closed PR unintentionally."
    )


def _gh_pr_state(branch: str, deadline: float) -> str:
    """Return `gh pr view <branch> --json state --jq .state` stdout, or "".

    Budget-aware like `_git.run_git`: nothing is spawned below the floor, and
    the call is clamped to what the shared deadline has left. Any failure —
    gh missing, non-zero exit (no PR for the branch), timeout — yields "",
    which the caller treats as "no advisory", exactly as the shell version
    treated an empty `$(gh ... 2>/dev/null)`.
    """
    budget = deadline - time.monotonic()
    if budget < MIN_SUBPROC_BUDGET_SEC:
        return ""
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "state", "--jq", ".state"],
            capture_output=True,
            text=True,
            timeout=min(_GH_TIMEOUT_SEC, budget),
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    # `$(...)` strips only trailing newlines; keep that, not a full strip.
    return (proc.stdout or "").rstrip("\n")


def _record_advise(session_id: str) -> None:
    """Record the advisory as a RICH `advise` fire (issue #740 single-event).

    A UserPromptSubmit hook signals on stdout while exiting 0, so
    `@fail_open`'s coarse path would log every engagement as `pass`. Only the
    advising path records: a silent pass is exactly what the coarse record
    already says. Suppression is gated on the rich append landing, so a
    failed write never drops the engagement from both streams.
    """
    if _fire_ledger.record_session_fire(
        _HOOK_NAME, _ROLE, _fire_ledger.DECISION_ADVISE, session_id, "",
    ):
        _fire_ledger.suppress_coarse_duplicate()


def _emit(context: str) -> None:
    # jq -n pretty-print shape (`{hookSpecificOutput: {...}}`): 2-space
    # indent, non-ASCII left raw, one trailing newline. Byte-identical to
    # the shell version's output on purpose.
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": context,
                }
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0  # fail-open on malformed stdin

    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return 0
    session_id = payload.get("session_id")
    if not isinstance(session_id, str):
        session_id = ""

    is_bare_codex_review = bool(_BARE_CODEX_REVIEW_RE.match(prompt))
    is_codex_review_wrap = bool(_CODEX_REVIEW_WRAP_RE.match(prompt))
    if not is_bare_codex_review and not is_codex_review_wrap:
        return 0

    # One deadline for every probe this invocation spawns, so their SUM stays
    # inside the manifest timeout instead of each reading the budget alone.
    deadline = shared_probe_deadline(_MANIFEST_TIMEOUT_SEC)

    # --- Advisory 1: multi-worktree (bare /codex:review only) -----------------
    worktree_msg = ""
    if is_bare_codex_review:
        porcelain = run_git(
            ["worktree", "list", "--porcelain"],
            timeout=_GIT_TIMEOUT_SEC,
            deadline=deadline,
        )
        # No repo / no git → the shell's awk printed 0 → no advisory.
        wt_count = count_active_worktrees(porcelain or "")
        if wt_count >= 2:
            worktree_msg = _worktree_message(wt_count)

    # --- Advisory 2: PR-state guard (all matched triggers) --------------------
    # Requires `gh` CLI; fail-open when absent, when not in a git repo, or
    # when no PR exists for the current branch.
    pr_state_msg = ""
    if shutil.which("gh"):
        head = run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"],
            timeout=_GIT_TIMEOUT_SEC,
            deadline=deadline,
        )
        current_branch = (head or "").rstrip("\n")
        if current_branch and current_branch != "HEAD":
            pr_state = _gh_pr_state(current_branch, deadline)
            if pr_state in _STALE_PR_STATES:
                pr_state_msg = _pr_state_message(current_branch, pr_state)

    # --- Combine and emit -------------------------------------------------------
    combined = "\n\n".join(m for m in (worktree_msg, pr_state_msg) if m)
    if not combined:
        return 0

    _emit(combined)
    _record_advise(session_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
