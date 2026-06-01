#!/usr/bin/env python3
"""PostToolUse(Bash) advisory: verify a `git push` actually advanced the remote ref.

Background (issue #539): in remote execution environments the git proxy
endpoint can rotate between calls (e.g. 127.0.0.1:39291 -> :43651). A second
push in the same session may go to a *different* endpoint, so the intended PR
branch never receives the commit even though `git push` prints
`* [new branch]` and exits 0. The squash-merge then lands an incomplete branch
and main goes red. The `* [new branch]` line on an already-pushed branch is the
tell, but it is easy to miss in the moment.

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
import subprocess
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

_BYPASS_ENV = "PRAXIS_PUSH_VERIFY_BYPASS"
_STRICT_ENV = "PRAXIS_PUSH_VERIFY_STRICT"
_GIT_TIMEOUT_SEC = 10

# git push flags we cannot reason about safely -> skip the whole push.
_SKIP_FLAGS = frozenset({
    "--dry-run", "-n", "--delete", "-d", "--tags", "--all",
    "--mirror", "--prune",
})
# git push flags that consume the following token as a value.
_VALUE_FLAGS = frozenset({"-o", "--push-option", "--exec", "--receive-pack", "--repo"})

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


# ---------------------------------------------------------------------------
# git helpers (fail-open)
# ---------------------------------------------------------------------------


def _git(cwd: str, args: list[str], timeout: int = _GIT_TIMEOUT_SEC):
    """Run `git -C <cwd> <args>`. Return (returncode|None, stdout). Never raises."""
    try:
        r = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, (r.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return None, ""


# ---------------------------------------------------------------------------
# command parsing
# ---------------------------------------------------------------------------


def _find_push_argv(command: str):
    """Return (push_args, git_C_dir|None) for the first `git push` segment, else None."""
    try:
        tokens = safe_tokenize(command)
    except Exception:
        return None
    for argv in iter_command_starts(tokens):
        argv = strip_prefix(argv)
        if not argv or argv[0] != "git":
            continue
        # Walk global git options (-C <dir>, -c <kv>, --git-dir=...) to the subcommand.
        i = 1
        git_c = None
        while i < len(argv):
            tok = argv[i]
            if tok == "-C" and i + 1 < len(argv):
                git_c = argv[i + 1]
                i += 2
                continue
            if tok == "-c" and i + 1 < len(argv):
                i += 2
                continue
            if tok.startswith("-"):
                i += 1
                continue
            break
        if i < len(argv) and argv[i] == "push":
            return argv[i + 1:], git_c
    return None


def _parse_push(push_args: list[str]):
    """Extract {remote, refspec} from push argv, or None to skip."""
    positionals: list[str] = []
    i = 0
    while i < len(push_args):
        tok = push_args[i]
        if tok in _SKIP_FLAGS:
            return None
        if tok in ("--force", "-f", "--force-with-lease") or tok.startswith("--force-with-lease="):
            i += 1
            continue
        if tok in _VALUE_FLAGS:
            i += 2  # consume the value token too
            continue
        if tok.startswith("--") and "=" in tok:
            key = tok.split("=", 1)[0]
            if key in _SKIP_FLAGS:
                return None
            if key in _VALUE_FLAGS:
                i += 1  # value already inline after `=`; no next token consumed
                continue
            i += 1
            continue
        if tok.startswith("-"):
            i += 1  # boolean short/long flag (e.g. -u, --set-upstream, --atomic)
            continue
        positionals.append(tok)
        i += 1
    if len(positionals) > 2:
        return None  # multiple refspecs — too ambiguous to verify safely
    remote = positionals[0] if positionals else None
    refspec = positionals[1] if len(positionals) >= 2 else None
    return {"remote": remote, "refspec": refspec}


def _resolve_targets(cwd: str, parsed: dict):
    """Resolve {remote, remote_branch, local_ref} for the push, or None to skip."""
    remote = parsed["remote"]
    refspec = parsed["refspec"]

    def _current_branch():
        rc, cur = _git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"])
        cur = cur.strip()
        if rc != 0 or not cur or cur == "HEAD":
            return None  # detached HEAD
        return cur

    if refspec is None:
        cur = _current_branch()
        if cur is None:
            return None
        if remote is None:
            rc, up = _git(
                cwd, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]
            )
            up = up.strip()
            if rc != 0 or "/" not in up:
                return None  # no upstream configured — cannot know the target
            remote, remote_branch = up.split("/", 1)
        else:
            remote_branch = cur
        local_ref = "HEAD"
    else:
        if refspec.startswith(":"):
            return None  # branch deletion (`git push origin :branch`)
        if refspec.startswith("+"):
            refspec = refspec[1:]
        if remote is None:
            remote = "origin"
        if ":" in refspec:
            local_part, remote_branch = refspec.split(":", 1)
            local_ref = local_part or "HEAD"
        elif refspec == "HEAD":
            cur = _current_branch()
            if cur is None:
                return None
            remote_branch = cur
            local_ref = "HEAD"
        else:
            local_ref = refspec
            remote_branch = refspec
        remote_branch = remote_branch.strip()
        if remote_branch.startswith("refs/heads/"):
            remote_branch = remote_branch[len("refs/heads/"):]

    if not remote or not remote_branch:
        return None
    return {"remote": remote, "remote_branch": remote_branch, "local_ref": local_ref}


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

    found = _find_push_argv(command)
    if not found:
        return 0
    push_args, git_c = found
    parsed = _parse_push(push_args)
    if parsed is None:
        return 0

    cwd = git_c or payload.get("cwd") or os.getcwd()
    targets = _resolve_targets(cwd, parsed)
    if targets is None:
        return 0

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
