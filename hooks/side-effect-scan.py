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
import shlex
import sys


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
SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}
OPT_OUT_MARKER = "# side-effect:ack"


def has_opt_out(raw: str) -> bool:
    return OPT_OUT_MARKER in raw.lower()


def safe_tokenize(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True, comments=False)
    except ValueError:
        return []


def iter_command_starts(tokens: list[str]):
    """Yield (start_index, argv_slice) for each subcommand in a compound line.

    Handles pipelines and `;` / `&&` / `||` chains. Trailing operators that
    shlex leaves glued to a token (`push;`) are stripped and the next index
    is also treated as a new command start.
    """
    i = 0
    n = len(tokens)
    start = 0
    i = 0
    while i < n:
        tok = tokens[i]
        if tok in SHELL_SEPARATORS:
            if start < i:
                yield start, tokens[start:i]
            start = i + 1
        else:
            stripped = tok.rstrip(";&|")
            if stripped != tok and stripped:
                replaced = tokens[start:i] + [stripped]
                yield start, replaced
                start = i + 1
        i += 1
    if start < n:
        yield start, tokens[start:n]


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


def detect(argv: list[str]) -> list[str]:
    if not argv:
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


def build_reason(categories: list[str], prod: bool) -> str:
    parts = [f"[{c}] {CATEGORIES[c]['reason']}." for c in categories]
    msg = " ".join(parts)
    if prod:
        msg = "⚠️  PROD scope 감지 — " + msg + " 배포/운영 영향 재확인 필수."
    msg += (
        " 의도한 실행이면 command 에 '# side-effect:ack' 주석을 포함해 재호출하세요."
    )
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

    matched: list[str] = []
    for _, argv in iter_command_starts(tokens):
        for cat in detect(argv):
            if cat not in matched:
                matched.append(cat)

    if not matched:
        return 0

    reason = build_reason(matched, has_prod_scope(tokens))
    emit_ask(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
