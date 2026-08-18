#!/usr/bin/env python3
"""PostToolUse(Bash) advisory: verify a `git push` actually advanced the remote ref.

Background (issue #539): in remote execution environments the git proxy
endpoint can rotate between calls (e.g. 127.0.0.1:39291 -> :43651). A second
push in the same session may go to a *different* endpoint, so the intended PR
branch never receives the commit even though `git push` prints
`* [new branch]` and exits 0. The squash-merge then lands an incomplete branch
and main goes red. The `* [new branch]` line on an already-pushed branch is the
tell, but it is easy to miss in the moment.

The `git push` argv parsing lives in `_lib/_git_push_target.py` (issue #1039)
because a second PostToolUse hook needs the same answer.

This hook runs AFTER a `git push` and cross-checks the remote tip against the
SHA that was supposed to be pushed:

  - Reliable detector: `git ls-remote --heads <remote> <branch>` tip != the
    local SHA of the ref we pushed  -> advisory.
  - Absent branch: ls-remote shows no such ref while the push output claims it
    wrote that branch (`-> <branch>` / `[new branch]`)  -> advisory.
  - When the mismatch coincides with a `* [new branch]` output line, the
    advisory calls out the rotating-endpoint scenario explicitly (AC #2).

Advisory by default (exit 0 + stderr); PRAXIS_PUSH_VERIFY_STRICT=1 escalates to
exit 2. Fully fail-open (any infra error -> silent allow) and bypassable via
PRAXIS_PUSH_VERIFY_BYPASS=1.

Skip conditions (return 0, no advisory):
  - tool_name != Bash, or command without both `git` and `push`
  - the push itself failed / was interrupted (failure is already visible)
  - unparseable / unusual pushes: --dry-run, --delete, --tags, --all,
    --mirror, --prune, branch deletion (`:branch`), >2 positionals, detached
    HEAD, no upstream for a bare `git push`
  - remote unreachable (ls-remote rc != 0) -> cannot verify, fail-open
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _git_push_target import (  # type: ignore[import-not-found]  # noqa: E402
    git as _git,
)
from _git_push_target import resolve_push_target  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402

_BYPASS_ENV = "PRAXIS_PUSH_VERIFY_BYPASS"
_STRICT_ENV = "PRAXIS_PUSH_VERIFY_STRICT"

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


# ---------------------------------------------------------------------------
# push output inspection
# ---------------------------------------------------------------------------


def _push_output(tool_response: object) -> str:
    if not isinstance(tool_response, dict):
        return ""
    parts = []
    for key in ("output", "stdout", "stderr"):
        val = tool_response.get(key)
        if isinstance(val, str):
            parts.append(val)
    return "\n".join(parts)


def _output_wrote_branch(output: str, branch: str) -> bool:
    """True if the push output claims it wrote <branch> (so an absent remote ref
    is a real discrepancy, not just an odd refspec we mis-resolved)."""
    if not output:
        return False
    if re.search(r"->\s*" + re.escape(branch) + r"\b", output):
        return True
    if "[new branch]" in output and re.search(r"\b" + re.escape(branch) + r"\b", output):
        return True
    return False


def _format_advisory(t: dict, expected: str, remote_sha: str, new_branch: bool) -> str:
    remote_disp = remote_sha[:12] if remote_sha else "(absent)"
    lines = [
        f"[push-remote-ref-verify] push to {t['remote']}/{t['remote_branch']} "
        f"may not have landed on the intended remote:",
        f"  expected (local {t['local_ref']}): {expected[:12]}",
        f"  remote tip:                  {remote_disp}",
    ]
    if new_branch:
        lines.append(
            "  push output reported '* [new branch]' — in a rotating-endpoint "
            "environment this often means a different proxy endpoint received "
            "the push, not the intended remote.")
    lines.append(f"  re-verify: git ls-remote {t['remote']} {t['remote_branch']}")
    lines.append(f"  bypass: {_BYPASS_ENV}=1")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip():
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not command or "push" not in command or "git" not in command:
        return 0

    # The push itself failing is already visible — our concern is *silent*
    # divergence after an apparently-successful push.
    tool_response = payload.get("tool_response")
    if isinstance(tool_response, dict):
        exit_code = tool_response.get("exit")
        if exit_code is not None:
            try:
                if int(exit_code) != 0:
                    return 0
            except (TypeError, ValueError):
                pass
        if tool_response.get("interrupted") is True or tool_response.get("isError") is True:
            return 0

    targets = resolve_push_target(command, payload.get("cwd") or os.getcwd())
    if targets is None:
        return 0
    cwd = targets["cwd"]

    expected_rc, expected_sha = _git(cwd, ["rev-parse", "--verify", targets["local_ref"]])
    expected_sha = expected_sha.strip()
    if expected_rc != 0 or not _SHA_RE.match(expected_sha):
        return 0

    remote_rc, remote_out = _git(
        cwd, ["ls-remote", "--heads", targets["remote"], targets["remote_branch"]]
    )
    if remote_rc is None or remote_rc != 0:
        return 0  # remote unreachable — cannot verify, fail-open

    remote_sha = ""
    for line in remote_out.splitlines():
        line = line.strip()
        if line:
            remote_sha = line.split()[0]
            break

    output = _push_output(tool_response)
    if not remote_sha:
        # Branch absent on remote: only a discrepancy if the push claims it wrote it.
        if not _output_wrote_branch(output, targets["remote_branch"]):
            return 0
    elif remote_sha == expected_sha:
        return 0  # verified: remote advanced to the pushed SHA

    sys.stderr.write(_format_advisory(targets, expected_sha, remote_sha, "[new branch]" in output))
    strict = os.environ.get(_STRICT_ENV, "").strip() == "1"
    return 2 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
