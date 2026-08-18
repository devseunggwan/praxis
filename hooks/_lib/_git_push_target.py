"""Resolve which remote branch a `git push` command actually targets.

Extracted from `advisory-nudge/push-remote-ref-verify` (issue #1039) so that a
second PostToolUse(Bash) hook can reuse the same parser instead of copying it.
Both callers need the identical answer to one question — *after this push, which
remote branch is supposed to have moved?* — and the hard part is entirely in the
argv walk: `git -C <dir>`, `-c k=v`, `--force-with-lease=<v>`, value-taking flags
(`-o`, `--repo`, ...), `local:remote` refspecs, bare pushes that resolve through
`@{upstream}`, detached HEAD, and branch deletion.

Every helper is fail-closed in the sense that matters here: anything it cannot
parse confidently returns None, and the caller skips. Silence beats a wrong
branch name.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent))
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

GIT_TIMEOUT_SEC = 10

# git push flags we cannot reason about safely -> skip the whole push.
SKIP_FLAGS = frozenset({
    "--dry-run", "-n", "--delete", "-d", "--tags", "--all",
    "--mirror", "--prune",
})
# git push flags that consume the following token as a value.
VALUE_FLAGS = frozenset({"-o", "--push-option", "--exec", "--receive-pack", "--repo"})


def git(cwd: str, args: list[str], timeout: int = GIT_TIMEOUT_SEC):
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


def find_push_argv(command: str):
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


def parse_push(push_args: list[str]):
    """Extract {remote, refspec} from push argv, or None to skip."""
    positionals: list[str] = []
    i = 0
    while i < len(push_args):
        tok = push_args[i]
        if tok in SKIP_FLAGS:
            return None
        if tok in ("--force", "-f", "--force-with-lease") or tok.startswith("--force-with-lease="):
            i += 1
            continue
        if tok in VALUE_FLAGS:
            i += 2  # consume the value token too
            continue
        if tok.startswith("--") and "=" in tok:
            key = tok.split("=", 1)[0]
            if key in SKIP_FLAGS:
                return None
            if key in VALUE_FLAGS:
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


def resolve_targets(cwd: str, parsed: dict):
    """Resolve {remote, remote_branch, local_ref} for the push, or None to skip."""
    remote = parsed["remote"]
    refspec = parsed["refspec"]

    def _current_branch():
        rc, cur = git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"])
        cur = cur.strip()
        if rc != 0 or not cur or cur == "HEAD":
            return None  # detached HEAD
        return cur

    if refspec is None:
        cur = _current_branch()
        if cur is None:
            return None
        if remote is None:
            rc, up = git(
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


def resolve_push_target(command: str, cwd: str):
    """One-call form: command + fallback cwd -> push target, or None to skip.

    Returns {remote, remote_branch, local_ref, cwd} where `cwd` is the directory
    the push actually ran against (`git -C <dir>` wins over the payload's cwd).
    """
    found = find_push_argv(command)
    if not found:
        return None
    push_args, git_c = found
    parsed = parse_push(push_args)
    if parsed is None:
        return None
    effective_cwd = git_c or cwd
    targets = resolve_targets(effective_cwd, parsed)
    if targets is None:
        return None
    targets["cwd"] = effective_cwd
    return targets
