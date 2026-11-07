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

Tiers (issue #874). `git-commit` is ADVISE; every other category is ASK,
except two routine publish shapes on the Hermes host (issue #1635, see
`SELF_CHECK_HOSTS`). The
observation behind the `git-commit` demotion: one 2026-07-27 session (`5d46110f`) spent 23
of its 37 total ask prompts on this single hook — 62% — and an approval gate
that becomes habitual stops functioning as a gate. `git-commit` is the one
category that can absorb that reduction, and the ground is reversibility
alone (issue #1153):

  • Its action is local-only and recoverable from the same shell. `git push`,
    `gh pr merge` / `gh pr create` / `gh workflow run` and `kubectl apply` all
    publish to state someone else can already be reading; a commit is
    recoverable with `git reset` / `git commit --amend` with no second party
    to notify. What is recovered is the ref: merge / rebase / cherry-pick /
    revert also update the index and the tracked working tree, `git reset
    --hard` discards uncommitted changes to tracked files (`git-reset(1)`),
    and a repository carrying its own `pre-commit` / `post-commit` /
    `post-merge` hook can run anything at all. So the guarantee is "no remote
    ref moves, nobody else has seen it", never "nothing outside `.git`
    changes".

The sibling commit gates are context, not ground (issue #1153): coverage is a
property of the installation rather than of the command. Seven sibling
PreToolUse(Bash) hooks gate a `git commit` argv on their own — derived from
the `"gates": ["git-commit"]` field each carries in `hooks/manifest.json`,
each verified to key on the `commit` subcommand:
block-commit-without-codex-review, commit-message-paren-check,
commit-title-format-check, commit-title-length-check,
internal-token-leak-gate, pre-commit-staged-file-enumeration,
verify-commit-flag-override.
Four of the seven siblings are also the checklist
`verify-commit-flag-override` prints on its own deny (issue #941). No
sibling hook gates `kubectl apply` at all. This enumeration is a copy
of the manifest's, kept honest by scripts/check-sibling-commit-gates.py
(issue #1127) — edit the manifest field, never this list alone.

That list is host-scoped. This hook carries no `hosts` key, so the tier ships
everywhere, but several of the names above carry `hosts: ["claude"]` and are
stripped from the other platforms' `hooks.json` — `_dispatch.group_members`
re-applies the same filter at runtime. spec.md's host table (also re-derived
by the checker) carries the per-host counts.

The wrapper CLIs that used to share the `git-commit` label do NOT follow it
down; they carry their own `wrapper-commit` category at ASK. The reversibility
ground fails for them, and the sibling context does too: the seven sibling
gates match a literal
`git commit` argv, so a commit made *inside* a wrapper process is invisible
to every one of them. Splitting the category is a deliberate narrowing of
issue #874's "demote git-commit" — recorded here because the label a reason
prints changes with it.

Which wrapper CLIs exist is installer-specific, so the pattern list comes
from PRAXIS_WRAPPER_COMMIT_CMDS (comma-separated `<cmd> <sub>[|<sub>...]`
entries, e.g. "iceberg-schema migrate|promote, omc ralph") rather than a
shipped table — the shipped default is empty and the category is silent
until configured (issue #1157; the author-toolchain names that used to be
hardcoded here live in the author's env now).
"""
from __future__ import annotations

import os
import re
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _branch_names import base_branch_names, work_branch_regex  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import member_host  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    GH_MERGE_VALUE_FLAGS,
    _is_gh_binary,
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
        "patterns": [],  # populated from PRAXIS_WRAPPER_COMMIT_CMDS below
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

def _wrapper_commit_patterns() -> list:
    """Parse PRAXIS_WRAPPER_COMMIT_CMDS into (cmd, (1,), {subs}) patterns.

    Format: comma-separated entries, each `<cmd> <sub>[|<sub>...]` —
    e.g. "iceberg-schema migrate|promote, omc ralph". Entries that do not
    have exactly a command word plus a subcommand group are dropped
    silently (fail-open: a typo must not turn into a broad match). The
    shipped default is empty — see the module docstring (issue #1157).
    """
    raw = os.environ.get("PRAXIS_WRAPPER_COMMIT_CMDS", "")
    patterns = []
    for entry in raw.split(","):
        parts = entry.split()
        if len(parts) != 2:
            continue
        cmd, subs = parts
        sub_set = {s for s in subs.split("|") if s}
        if cmd and sub_set:
            patterns.append((cmd, (1,), sub_set))
    return patterns


CATEGORIES["wrapper-commit"]["patterns"] = _wrapper_commit_patterns()

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
    if not argv or not _is_gh_binary(argv[0]):
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


# Hosts whose `ask` is a self-check the agent answers, not a prompt the user
# answers: the Hermes adapter turns every ask outside its consent gates into
# "re-check and re-issue" (ARCHITECTURE.md, Hermes adapter, *Asks*). On those
# hosts the routine publish steps of the issue workflow advise instead of
# asking (issue #1635): over 14 days on one Hermes install all 12 of this
# gate's self-checks were re-issued with only the ack marker added, and every
# one was a push of the session's own issue branch or a `gh pr create`. The
# argv already names the target the checklist asks to re-check, so the
# re-check found nothing. Elsewhere the ask is a permission prompt, and this
# change leaves it alone.
SELF_CHECK_HOSTS = frozenset({"hermes"})

# Only these ask categories can be demoted. A wrapper CLI configured under the
# same argv (`PRAXIS_WRAPPER_COMMIT_CMDS`) adds its own category and still asks.
_ROUTINE_PUBLISH_CATEGORIES = frozenset({"git-push", "gh-merge"})

# `git push` options that change neither which ref is written nor how. Any
# other option (`--force*`, `--delete`, `--mirror`, `--all`, `--tags`,
# `--prune`, `-o`, `--no-verify`, bundled short flags, ...) keeps the ask.
_PUSH_PLAIN_FLAGS = frozenset({
    "-u", "--set-upstream",
    "-q", "--quiet",
    "-v", "--verbose",
    "--progress", "--no-progress",
})
_OWN_BRANCH_REMOTE = "origin"

# What a plain branch name can look like before the convention is consulted.
# Refspec syntax (`+`, `:`), expansions (`$`), globs and `refs/...` never
# pass, whatever PRAXIS_BRANCH_NAME_REGEX allows.
_PLAIN_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_SYMBOLIC_REF = re.compile(r"[A-Z_]*HEAD")

# Shell syntax the tokenizer cannot place reliably: a redirection can be
# followed by more arguments (`&>/dev/null --force`); `<(...)`, `>(...)`,
# `$(...)`, backticks and heredoc bodies run commands; a backslash can
# continue the line; a quoted `#` is an argument, not a comment.
_OPAQUE_SHELL = re.compile(r"""[<>`\\]|\$\(|['"]#""")
# A tokenizer word made only of `;`, `|`, `&` is a control operator.
_OPERATOR = re.compile(r"[;|&]+")


def has_opaque_shell_syntax(command: str) -> bool:
    return _OPAQUE_SHELL.search(command) is not None


def _command_args(argv: list[str]) -> list[str]:
    """argv up to a shell comment.

    Only called when `has_opaque_shell_syntax` is false, so every `#` token
    is an unquoted comment start.
    """
    out: list[str] = []
    for tok in argv:
        if tok.startswith("#"):
            break
        out.append(tok)
    return out


def _is_plain_branch_name(ref: str) -> bool:
    return (
        _PLAIN_BRANCH.fullmatch(ref) is not None
        and _SYMBOLIC_REF.fullmatch(ref) is None
        and not ref.startswith("refs/")
        and ".." not in ref
        and not ref.endswith((".", "/", ".lock"))
    )


def is_own_branch_push(args: list[str]) -> bool:
    """`git push [-u|-q|-v...] origin <work branch>` and nothing else.

    `args` is the literal argv: the command word must be `git` itself, so an
    env assignment (`GIT_CONFIG_*=...`) or a wrapper (`env`, `timeout`,
    `xargs`, ...) keeps the ask. The branch must be a plain branch name, match
    the work-branch convention (`_branch_names`, the pattern `branch-name-check`
    gates new branches with) and not be a base branch.
    """
    if len(args) < 2 or args[0] != "git" or args[1] != "push":
        return False
    positionals: list[str] = []
    for tok in args[2:]:
        if tok.startswith("-"):
            if tok not in _PUSH_PLAIN_FLAGS:
                return False
            continue
        positionals.append(tok)
    if len(positionals) != 2:
        return False
    remote, ref = positionals
    if remote != _OWN_BRANCH_REMOTE or ref in base_branch_names() or not _is_plain_branch_name(ref):
        return False
    return work_branch_regex().fullmatch(ref) is not None


def _is_routine_segment(argv: list[str]) -> bool:
    args = _command_args(argv)
    if not args:
        return False
    if args[0] == "git":
        return is_own_branch_push(args)
    if args[0] == "gh":
        return _gh_subcommand_pair(args) == ("pr", "create")
    return False


def is_routine_command(tokens: list[str]) -> bool:
    """The whole command is one routine publish step, optionally after `cd`.

    Accepted: `<step>` or `cd <dir> && <step>`, where `<step>` is an own
    work-branch push or a `gh pr create` written with the bare command word.
    Anything else in the command (another step, a pipe, `;`, `|&`, `&|`, a
    background `&`, a subshell, a wrapper or an env assignment) keeps the ask:
    review rounds 1-3 showed that a merge or push the detector cannot see
    (`timeout ... gh pr merge`, `(git push origin main)`) rode along once the
    visible step stopped asking, so the demotion is an allowlist of whole
    commands rather than a list of dangerous forms. Callers check
    `has_opaque_shell_syntax` first.
    """
    segments: list[list[str]] = [[]]
    operators: list[str] = []
    for tok in tokens:
        if _OPERATOR.fullmatch(tok):
            operators.append(tok)
            segments.append([])
        else:
            segments[-1].append(tok)
    if not operators:
        return _is_routine_segment(segments[0])
    if operators == ["&&"] and len(segments[0]) == 2 and segments[0][0] == "cd":
        return _is_routine_segment(segments[1])
    return False


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
        msg = (
            "[prod] ⚠️  PROD scope detected / PROD scope 감지 — " + msg
            + " Re-confirm the deploy/ops impact. 배포/운영 영향 재확인 필수."
        )
    msg += (
        " If this run is intended, re-invoke with a '# side-effect:ack' comment in the command."
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
        msg = "[side-effect-scan] ⚠️  PROD scope detected / PROD scope 감지 — " + " ".join(parts)
    msg += (
        " If this run is intended, re-invoke with a '# side-effect:ack' comment in the command."
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
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed
    if not command.strip():
        return 0
    if has_opt_out(command):
        return 0

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    demote = (
        member_host() in SELF_CHECK_HOSTS
        and not has_opaque_shell_syntax(command)
        and is_routine_command(tokens)
    )
    matched: list[str] = []
    asks = False
    for argv in iter_command_starts(tokens):
        categories = detect(argv)
        for cat in categories:
            if cat not in matched:
                matched.append(cat)
        ask_categories = {c for c in categories if CATEGORIES[c]["tier"] == TIER_ASK}
        if not ask_categories:
            continue
        if demote and ask_categories <= _ROUTINE_PUBLISH_CATEGORIES:
            continue
        asks = True

    if not matched:
        return 0

    prod = has_prod_scope(tokens)

    # Mixed matches stay at ASK and keep EVERY matched category in the reason —
    # `git commit -am x && git push` is unchanged from its pre-#874 text. Only
    # a match with no ASK-tier category, or a whole command that is one
    # routine publish step on a self-check host, advises, so the demotion can
    # never quiet a command that also touches other shared state.
    if asks:
        emit_ask(build_reason(matched, prod, command))
        return 0

    emit_advisory(build_advisory(matched, prod))
    return 0


if __name__ == "__main__":
    sys.exit(main())
