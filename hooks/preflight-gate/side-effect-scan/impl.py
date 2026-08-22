#!/usr/bin/env python3
"""PreToolUse(Bash) guard: surface collateral side effects before execution.

Reads Claude Code hook JSON on stdin; if tool_input.command matches a known
side-effect category (git mutation, remote trigger, kubernetes write, known
wrapper CLIs that commit internally), it either emits permissionDecision "ask"
or writes an advisory to stderr, per the matched category's tier. Otherwise
exits 0 (transparent pass-through).

Uses shlex for tokenization so quoted/subshelled fragments can't hide a
matching command from the detector.

Opt-out: embed the marker `# side-effect:ack` anywhere in the command to
signal an intentional invocation — the hook then exits 0 without prompting.

Tiers (issue #874). `git-commit` is ADVISE; every other category is ASK. The
observation behind the demotion: one 2026-07-27 session (`5d46110f`) spent 23
of its 37 total ask prompts on this single hook — 62% — and an approval gate
that becomes habitual stops functioning as a gate. `git-commit` is the one
category that can absorb that reduction:

  • It is the only category whose action is local-only and fully reversible.
    `git push`, `gh pr merge` / `gh pr create` / `gh workflow run` and
    `kubectl apply` all publish to state someone else can already be reading;
    a commit is recoverable with `git reset` / `git commit --amend` from the
    same shell.
  • It is the only category already covered in depth. Seven sibling
    PreToolUse(Bash) hooks gate a `git commit` argv on their own — enumerated
    from `hooks/manifest.json`, each verified to key on the `commit`
    subcommand: block-commit-without-codex-review, block-sciomc-finding-commit,
    commit-title-format-check, commit-title-length-check,
    verify-commit-flag-override, commit-decomposition-advisory,
    pre-commit-staged-file-enumeration. Five of the seven are also the
    checklist `verify-commit-flag-override` prints on its own deny (issue
    #941). No sibling hook gates `kubectl apply` at all.

The wrapper CLIs that used to share the `git-commit` label (`iceberg-schema
migrate|promote`, `omc ralph`) do NOT follow it down; they now carry their own
`wrapper-commit` category at ASK. Both halves of the rationale above fail for
them: the seven sibling gates match a literal `git commit` argv, so a commit
made *inside* a wrapper process is invisible to every one of them, and
`iceberg-schema promote` is a catalog operation, not a local one. Splitting
the category is a deliberate narrowing of issue #874's "demote git-commit" —
recorded here because the label a reason prints changes with it.
"""
from __future__ import annotations

import json
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    GH_MERGE_VALUE_FLAGS,
    compound_cascade_hint,
    is_help_invocation,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)


# Tier names. See the module docstring for why `git-commit` — and only
# `git-commit` — sits at ADVISE (issue #874).
TIER_ASK = "ask"
TIER_ADVISE = "advise"

CATEGORIES = {
    "git-commit": {
        "tier": TIER_ADVISE,
        "patterns": [
            ("git", (1,), {"commit", "merge", "rebase", "cherry-pick", "revert"}),
        ],
        "reason": "local git state mutation — 현재 브랜치/HEAD 확인 필요",
    },
    # Split out of `git-commit` by issue #874: these commit from *inside*
    # another process, so the seven sibling `git commit` argv gates never see
    # them and the coverage half of the demotion rationale does not hold.
    "wrapper-commit": {
        "tier": TIER_ASK,
        "patterns": [
            ("iceberg-schema", (1,), {"migrate", "promote"}),
            ("omc", (1,), {"ralph"}),
        ],
        "reason": "wrapper CLI commits internally — 대상 카탈로그/브랜치와 의도 재확인",
    },
    "git-push": {
        "tier": TIER_ASK,
        "patterns": [
            ("git", (1,), {"push"}),
        ],
        "reason": "remote trigger (git push) — 타겟 브랜치와 upstream 재확인",
    },
    "gh-merge": {
        "tier": TIER_ASK,
        "patterns": [
            ("gh", (1, 2), ("pr", {"merge", "create"})),
            ("gh", (1, 2), ("workflow", {"run"})),
        ],
        "reason": "remote trigger via gh — PR/워크플로 타겟과 의도 재확인",
    },
    "kubectl-apply": {
        "tier": TIER_ASK,
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


# gh global flags that consume one additional argument, skipped so the
# subcommand pair lands at the right position for `gh -R owner/repo pr merge`.
GH_GLOBAL_FLAGS_WITH_ARG = frozenset({"-R", "--repo", "--hostname", "--color"})


def _gh_skip_global_flags(argv: list[str]) -> int:
    """Return the index of the first positional token after gh's leading
    global flags (`gh [--repo X] [-R Y] ...`).

    A leading global flag (e.g. `gh --repo owner/repo pr merge`) shifts the
    subcommand off the fixed `argv[1]`/`argv[2]` positions the old
    fixed-position walk assumed. Skipping the flags first makes the gh
    detector position-independent — issue #514 결함1.
    """
    i = 1
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok == "--":
            return i + 1
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in GH_GLOBAL_FLAGS_WITH_ARG and i < n:
            i += 1
    return i


def _gh_subcommand_pair(argv: list[str]) -> tuple[str, str]:
    """Return the (group, action) subcommand pair for a gh invocation,
    skipping any leading global flags. Empty strings when absent.

    Examples:
      ['gh', 'pr', 'merge', ...]               → ('pr', 'merge')
      ['gh', '--repo', 'o/r', 'pr', 'merge']   → ('pr', 'merge')
      ['gh', 'workflow', 'run', ...]           → ('workflow', 'run')
    """
    if not argv or argv[0] != "gh":
        return ("", "")
    i = _gh_skip_global_flags(argv)
    group = argv[i] if i < len(argv) else ""
    action = argv[i + 1] if i + 1 < len(argv) else ""
    return (group, action)


# Flag-aware gh detector: maps a (group, {actions}) subcommand pair to the
# CATEGORIES key it triggers. Replaces the fixed-position argv_matches walk
# for gh so leading global flags can no longer shift the subcommand out of
# detection range (issue #514 결함1).
_GH_CATEGORY_BY_PAIR: list[tuple[str, frozenset[str], str]] = [
    ("pr", frozenset({"merge", "create"}), "gh-merge"),
    ("workflow", frozenset({"run"}), "gh-merge"),
]


def _detect_gh(argv: list[str]) -> list[str]:
    """Flag-aware detection for gh invocations (issue #514 결함1)."""
    group, action = _gh_subcommand_pair(argv)
    if not group or not action:
        return []
    if is_help_invocation(argv, GH_MERGE_VALUE_FLAGS):
        return []  # `gh pr merge --help` prints usage, triggers nothing (#985)
    matched: list[str] = []
    for want_group, want_actions, category in _GH_CATEGORY_BY_PAIR:
        if group == want_group and action in want_actions:
            if category not in matched:
                matched.append(category)
    return matched


def detect(argv: list[str]) -> list[str]:
    argv = strip_prefix(argv)
    if not argv:
        return []
    cmd = argv[0].rsplit("/", 1)[-1]
    if cmd == "gh":
        # Flag-aware walk — primary gh detector (issue #514 결함1).
        return _detect_gh(argv)
    matched = []
    for category, spec in CATEGORIES.items():
        for cmd_name, positions, expected in spec["patterns"]:
            if cmd_name == "gh":
                continue  # gh handled by the flag-aware _detect_gh walk
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


def build_advisory(categories: list[str], prod: bool) -> str:
    """Return the stderr text for an ADVISE-tier-only match (issue #874).

    Two deliberate differences from `build_reason`:

      • No `compound_cascade_hint` suffix. That advisory exists to warn that a
        *denied* PreToolUse decision aborts every part of a compound command
        atomically (issue #229). Nothing can be denied here — the hook exits 0
        and bash runs the command — so appending it would describe a rejection
        that cannot happen.
      • A `[side-effect-scan]` prefix, matching the sibling advisory-nudge
        convention (`[pipefail-advisory]`, `[praxis:momentum-gate]`). An ask
        reason is already attributed by the permission prompt; a stderr line
        shares the dispatcher's stream with every other member and is not.

    The `# side-effect:ack` pointer is kept: the marker still short-circuits
    the hook, which is now how a caller silences the line rather than a prompt.
    """
    parts = [f"[{c}] {CATEGORIES[c]['reason']}." for c in categories]
    msg = "[side-effect-scan] " + " ".join(parts)
    if prod:
        msg = "[side-effect-scan] ⚠️  PROD scope 감지 — " + " ".join(parts)
    msg += (
        " 의도한 실행이면 command 에 '# side-effect:ack' 주석을 포함해 재호출하세요."
    )
    return msg


def emit_ask(reason: str) -> None:
    emit_decision("ask", reason)


def emit_advisory(text: str) -> None:
    """Write the ADVISE-tier text to stderr (issue #874).

    stderr, not stdout: `_dispatch.run_group` forwards every member's stderr
    unconditionally but discards member stdout that carries no ask/deny marker,
    and `_fire_ledger.classify_decision` reads stderr to record the fire as
    `advise` rather than `pass`. Both are what keeps this demotion visible in
    the ledger the tier decision will be re-scored from.
    """
    sys.stderr.write(text + "\n")


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
    for argv in iter_command_starts(tokens):
        for cat in detect(argv):
            if cat not in matched:
                matched.append(cat)

    if not matched:
        return 0

    prod = has_prod_scope(tokens)

    # Mixed matches stay at ASK and keep EVERY matched category in the reason —
    # `git commit -am x && git push` is unchanged from its pre-#874 text. Only
    # a match consisting solely of ADVISE-tier categories is demoted, so the
    # demotion can never quiet a command that also touches shared state.
    if any(CATEGORIES[c]["tier"] == TIER_ASK for c in matched):
        emit_ask(build_reason(matched, prod, command))
        return 0

    emit_advisory(build_advisory(matched, prod))
    return 0


if __name__ == "__main__":
    sys.exit(main())
