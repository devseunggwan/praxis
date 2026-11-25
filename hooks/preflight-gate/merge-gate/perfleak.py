#!/usr/bin/env python3
"""merge-gate check: deny `gh pr merge` until perf-leak-review ran on the head (issue #1667).

Opt-in. With `PRAXIS_MERGE_PERF_LEAK_REVIEW=1` in the hook environment, a
`gh pr merge` is denied unless a record written by
`skills/perf-leak-review/record_review.py commit` exists for the head that
merge will take. Unset, the check returns at once.

The check asks whether the review ran on this head, not what it found: the
reviewer only reads, so its findings are candidates and never block. A push
moves the head and leaves the old record behind, so the gate asks again.

What the gate checks has to be what the merge targets, so:

- The repository is named in the command: a PR URL, or `-R/--repo`. The
  hook's cwd, a `cd` earlier in the command and a `GH_REPO` prefix can each
  point the lookup and the merge at different repositories. `GH_HOST`
  anywhere in the command needs a PR URL, which carries its host.
- The head is the `--match-head-commit` value when given, since gh then
  refuses any other head. Without it, the merge must be the only command:
  an earlier step (a push) could move the head between this check and the
  merge. A lone merge is checked against the current head, leaving only a
  concurrent push from elsewhere in the milliseconds before gh runs.
- `--disable-auto` turns auto-merge off and merges nothing, so it passes.

A target that cannot be resolved is denied while the check is enabled; an
unresolved head cannot show the review ran.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path as _Path
from types import ModuleType
from typing import Optional

_HERE = _Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _perf_leak_record import ENABLE_ENV, enabled, parse_pr_url, record_path  # type: ignore[import-not-found]  # noqa: E402
from _shell_tokenize import (  # type: ignore[import-not-found]  # noqa: E402
    GH_MERGE_VALUE_FLAGS,
    is_help_invocation,
    iter_command_starts,
    safe_tokenize,
)
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402

_GH_TIMEOUT_SEC = 5
_RULE = "merge-gate perf-leak-review"
_RECORDER = str(_HERE.parent.parent.parent / "skills" / "perf-leak-review" / "record_review.py")
_PIN_FLAG = "--match-head-commit"


@dataclass(frozen=True)
class Target:
    identifier: Optional[str]
    repo: Optional[str]
    pin: Optional[str]


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


def _flag_value(tail: list[str], flag: str) -> Optional[str]:
    for i, tok in enumerate(tail):
        if tok == "--":
            break
        if tok == flag and i + 1 < len(tail):
            return tail[i + 1]
        if tok.startswith(flag + "="):
            return tok.split("=", 1)[1]
    return None


def _targets(segments: list[list[str]]) -> list[Target]:
    """One Target per `gh pr merge` segment that merges (help and --disable-auto skipped)."""
    wt = _worktree()
    found = []
    for argv in segments:
        seg = wt._merge_segment(argv)
        if seg is None:
            continue
        tail, repo_pre = seg
        if is_help_invocation(tail, GH_MERGE_VALUE_FLAGS) or "--disable-auto" in tail:
            continue
        _, identifier, repo_post = wt._scan_tail(tail)
        found.append(Target(identifier, repo_post or repo_pre, _flag_value(tail, _PIN_FLAG)))
    return found


def _resolve(target: Target, cwd: Optional[str]) -> Optional[tuple[str, str]]:
    """(PR url, current head sha) from `gh pr view`, else None."""
    cmd = ["gh", "pr", "view"]
    if target.identifier:
        cmd.append(target.identifier)
    if target.repo:
        cmd += ["-R", target.repo]
    cmd += ["--json", "url,headRefOid", "-q", '.url + " " + .headRefOid']
    rc, out = _worktree()._run(cmd, cwd, _GH_TIMEOUT_SEC)
    parts = out.split()
    if rc != 0 or len(parts) != 2:
        return None
    return parts[0], parts[1]


def _deny(why: str, correct_path: str) -> int:
    emit_block(
        rule_name=_RULE,
        why=why,
        correct_path=correct_path,
        bypass_env=None,
        reference=f"skills/perf-leak-review/SKILL.md → Merge gate; {ENABLE_ENV}=1 turns this check on",
    )
    return 2


def _review_path(pr: str) -> str:
    return (f"run `{_RECORDER} prepare --pr {pr}`, review the diff it prints with the "
            f"perf-leak-review skill, then `{_RECORDER} commit --pr {pr} --envelope <file>`; "
            "put the row it prints in the verification anchor and retry the merge")


def _context_problem(target: Target, command: str, alone: bool) -> Optional[str]:
    """Why the merge's repository or head cannot be pinned from the command, else None."""
    is_url = bool(target.identifier and parse_pr_url(target.identifier))
    if not is_url and not target.repo:
        return "the merge names no repository (`--repo` or a PR URL), so the lookup could check another PR"
    if not is_url and "GH_HOST" in command:
        return "`GH_HOST` is set in the command, so only a PR URL names the host the merge targets"
    if target.pin is None and not alone:
        return (f"the merge runs after another command without `{_PIN_FLAG}`, so the head it takes "
                "can differ from the head checked here")
    return None


def _check(target: Target, command: str, alone: bool, cwd: Optional[str]) -> int:
    shown = target.identifier or "<current branch>"
    problem = _context_problem(target, command, alone)
    if problem:
        return _deny(problem, f"run the merge alone as `gh pr merge <N> --repo <owner/name> --squash "
                              f"{_PIN_FLAG} <40-char head>` (or with the PR URL)")
    resolved = _resolve(target, cwd)
    if resolved is None:
        return _deny(f"could not resolve the head of PR {shown} to look up its review record",
                     "check that `gh pr view` resolves this PR, then retry")
    url, head = resolved
    head = target.pin or head
    path = record_path(url, head)
    if path is None or not _record_holds(path, head):
        return _deny(f"no perf-leak-review record for {url} at head {head[:7]}", _review_path(url))
    return 0


def _record_holds(path: str, head: str) -> bool:
    """True when `path` is a complete record for `head` (a truncated or foreign file is not)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("head") == head and isinstance(data.get("envelope"), dict)


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
    segments = list(iter_command_starts(safe_tokenize(command)))
    for target in _targets(segments):
        rc = _check(target, command, len(segments) == 1, cwd)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
