#!/usr/bin/env python3
"""merge-gate check: deny `gh pr merge` until perf-leak-review ran on the head (issue #1667).

Opt-in. With `PRAXIS_MERGE_PERF_LEAK_REVIEW=1` in the hook environment, a
`gh pr merge` is denied unless a record written by
`skills/perf-leak-review/record_review.py commit` exists for the PR's current
head sha. Unset, the check returns at once.

The check asks whether the review ran on this head, not what it found: the
reviewer only reads, so its findings are candidates and never block. A push
moves the head and leaves the old record behind, so the gate asks again.

The merge target is resolved the way the worktree check does (same argv
walker, `gh pr view` with the merge command's `-R/--repo`). A target that
cannot be resolved is denied while the check is enabled: the gate exists to
make the review structural, and an unresolved head cannot show it ran.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path as _Path
from types import ModuleType

_HERE = _Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    GH_MERGE_VALUE_FLAGS,
    is_help_invocation,
    iter_command_starts,
    safe_tokenize,
)
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _perf_leak_record import ENABLE_ENV, enabled, record_path  # type: ignore[import-not-found]  # noqa: E402
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402

_GH_TIMEOUT_SEC = 5
_RULE = "merge-gate perf-leak-review"
_RECORDER = str(_HERE.parent.parent.parent / "skills" / "perf-leak-review" / "record_review.py")


def _worktree() -> ModuleType:
    """The worktree check, whose argv walker this check shares."""
    name = "_praxis_merge_gate_worktree"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / "worktree.py")
    if spec is None or spec.loader is None:
        raise ImportError("merge-gate worktree check not loadable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _targets(command: str) -> list[tuple[str | None, str | None]]:
    """(identifier, repo) for every `gh pr merge` segment in the command."""
    wt = _worktree()
    found = []
    for argv in iter_command_starts(safe_tokenize(command)):
        seg = wt._merge_segment(argv)
        if seg is None:
            continue
        tail, repo_pre = seg
        if is_help_invocation(tail, GH_MERGE_VALUE_FLAGS):
            continue
        _, identifier, repo_post = wt._scan_tail(tail)
        found.append((identifier, repo_post or repo_pre))
    return found


def _resolve(identifier: str | None, repo: str | None, cwd: str | None) -> tuple[str, str] | None:
    """(PR url, head sha) from `gh pr view`, else None."""
    cmd = ["gh", "pr", "view"]
    if identifier:
        cmd.append(identifier)
    if repo:
        cmd += ["-R", repo]
    cmd += ["--json", "url,headRefOid", "-q", '.url + " " + .headRefOid']
    rc, out = _worktree()._run(cmd, cwd, _GH_TIMEOUT_SEC)
    parts = out.split()
    if rc != 0 or len(parts) != 2:
        return None
    return parts[0], parts[1]


def _deny(why: str, pr: str) -> int:
    emit_block(
        rule_name=_RULE,
        why=why,
        correct_path=(
            f"run `{_RECORDER} prepare --pr {pr}`, review the diff it prints with the "
            f"perf-leak-review skill, then `{_RECORDER} commit --pr {pr} --envelope <file>`; "
            "put the row it prints in the verification anchor and retry the merge"
        ),
        bypass_env=None,
        reference=f"skills/perf-leak-review/SKILL.md → Merge gate; {ENABLE_ENV}=1 turns this check on",
    )
    return 2


@fail_open
def main() -> int:
    if not enabled():
        return 0
    parsed = read_bash_payload()
    if parsed is None:
        return 0
    payload, command = parsed
    if not command.strip():
        return 0
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    for identifier, repo in _targets(command):
        shown = identifier or "<current branch>"
        resolved = _resolve(identifier, repo, cwd)
        if resolved is None:
            return _deny(f"could not resolve the head of PR {shown} to look up its review record", shown)
        url, head = resolved
        path = record_path(url, head)
        if path is None or not os.path.isfile(path):
            return _deny(f"no perf-leak-review record for {url} at head {head[:7]}", url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
