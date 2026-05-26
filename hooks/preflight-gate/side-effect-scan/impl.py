#!/usr/bin/env python3
"""PreToolUse(Bash) guard: surface collateral side effects before execution.

Reads Claude Code hook JSON on stdin; if tool_input.command matches a known
side-effect category (git mutation, remote trigger, kubernetes write, known
wrapper CLIs that commit internally), emits permissionDecision "ask" with a
reason. Otherwise exits 0 (transparent pass-through).

Uses shlex for tokenization so quoted/subshelled fragments can't hide a
matching command from the detector.

Opt-out: embed the marker `# side-effect:ack` anywhere in the command to
signal an intentional invocation — the hook then exits 0 without prompting.
"""
from __future__ import annotations

import json
import os
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)


CATEGORIES = {
    "git-commit": {
        "patterns": [
            ("git", (1,), {"commit", "merge", "rebase", "cherry-pick", "revert"}),
            ("iceberg-schema", (1,), {"migrate", "promote"}),
            ("omc", (1,), {"ralph"}),
        ],
        "reason": "local git state mutation — 현재 브랜치/HEAD 확인 필요",
    },
    "git-push": {
        "patterns": [
            ("git", (1,), {"push"}),
        ],
        "reason": "remote trigger (git push) — 타겟 브랜치와 upstream 재확인",
    },
    "gh-merge": {
        "patterns": [
            ("gh", (1, 2), ("pr", {"merge", "create"})),
            ("gh", (1, 2), ("workflow", {"run"})),
        ],
        "reason": "remote trigger via gh — PR/워크플로 타겟과 의도 재확인",
    },
    "kubectl-apply": {
        "patterns": [
            ("kubectl", (1,), {"apply", "delete", "replace", "patch"}),
        ],
        "reason": "shared resource write (kubectl) — cluster/namespace 재확인",
    },
}

PROD_LITERAL_TOKENS = {"prod", "production"}
OPT_OUT_MARKER = "# side-effect:ack"


def has_opt_out(raw: str) -> bool:
    return OPT_OUT_MARKER in raw.lower()


def argv_matches(argv: list[str], positions, expected) -> bool:
    """Check argv tokens at `positions` against `expected`.

    `expected` is either a single set (single position) or a tuple aligned
    with multiple positions.
    """
    if not argv:
        return False
    if isinstance(expected, (set, frozenset)):
        pos = positions[0]
        return len(argv) > pos and argv[pos] in expected
    for pos, want in zip(positions, expected):
        if len(argv) <= pos:
            return False
        val = argv[pos]
        if isinstance(want, (set, frozenset)):
            if val not in want:
                return False
        else:
            if val != want:
                return False
    return True


# Codex r3 #187: round 2 bypassed the entire `gh-merge` category under
# CMUX_DELEGATE=1, which also silenced `gh pr create` and `gh workflow run`
# (other entries in the same category) — broader than the sibling
# pre-merge-approval-gate hook's scope. The narrowed bypass below targets
# only `gh pr merge` (with optional gh global flags), leaving sibling
# patterns gated.
GH_GLOBAL_FLAGS_WITH_ARG = frozenset({"-R", "--repo", "--hostname", "--color"})


def _is_gh_pr_merge(argv: list[str]) -> bool:
    """Return True iff argv is `gh [global flags] pr merge ...`."""
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
    return i + 1 < len(argv) and argv[i] == "pr" and argv[i + 1] == "merge"


def detect(argv: list[str], delegate_bypass: bool = False) -> list[str]:
    argv = strip_prefix(argv)
    if not argv:
        return []
    if delegate_bypass and _is_gh_pr_merge(argv):
        return []
    cmd = argv[0].rsplit("/", 1)[-1]
    matched = []
    for category, spec in CATEGORIES.items():
        for cmd_name, positions, expected in spec["patterns"]:
            if cmd != cmd_name:
                continue
            if argv_matches(argv, positions, expected):
                matched.append(category)
                break
    return matched


def has_prod_scope(tokens: list[str]) -> bool:
    for raw in tokens:
        t = raw.lower()
        if t in PROD_LITERAL_TOKENS:
            return True
        if t.startswith("--env=") and t.split("=", 1)[1] in PROD_LITERAL_TOKENS:
            return True
        if t.startswith("--environment=") and t.split("=", 1)[1] in PROD_LITERAL_TOKENS:
            return True
    # also: `--env prod` as two tokens
    for i, raw in enumerate(tokens[:-1]):
        if raw.lower() in {"--env", "--environment", "-e"}:
            if tokens[i + 1].lower() in PROD_LITERAL_TOKENS:
                return True
    return False


def build_reason(categories: list[str], prod: bool, command: str) -> str:
    parts = [f"[{c}] {CATEGORIES[c]['reason']}." for c in categories]
    msg = " ".join(parts)
    if prod:
        msg = "⚠️  PROD scope 감지 — " + msg + " 배포/운영 영향 재확인 필수."
    msg += (
        " 의도한 실행이면 command 에 '# side-effect:ack' 주석을 포함해 재호출하세요."
    )
    msg += compound_cascade_hint(command)
    return msg


def emit_ask(reason: str) -> None:
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


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # never break the session on malformed input

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0
    if has_opt_out(command):
        return 0

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    delegate_bypass = os.environ.get("CMUX_DELEGATE") == "1"

    matched: list[str] = []
    for argv in iter_command_starts(tokens):
        for cat in detect(argv, delegate_bypass=delegate_bypass):
            if cat not in matched:
                matched.append(cat)

    if not matched:
        return 0

    reason = build_reason(matched, has_prod_scope(tokens), command)
    emit_ask(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
