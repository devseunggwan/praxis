#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `git commit` after sciomc/reviewer finding
without user-design consensus re-fetch.

Backs the rule: user-stated design (PR body / issue body / direct utterance)
is RATIFIED — AI analysis findings (sciomc Stage N, deep-dive, review finds,
scientist agent output) are DRAFTS. Surface findings to the user, never
auto-flip the design via direct commit.

Background:
  Four converging memory rules encode the same root cause family
  (falsify-before-lock, self-authored option scope, recommendation lock,
  consensus re-fetch before lock). Two CLAUDE.md promotions already done
  (Output-Block-Level Falsification Gate / Self-Falsify Before Recommendation
  Lock). Per prompt-layer retrieval failure threshold, 3+ generations require
  hook escalation, not another memo. This hook enforces the gate at the
  commit checkpoint.

Concrete retrospect pattern (praxis issue #374):
  1. User's PR body specifies a literal value or sibling-convention design
     choice — the ratified design, written on a shared surface
  2. A subsequent sciomc Stage N or reviewer analysis surfaces a finding
     that the user's literal/design is "sibling-deviant" or sub-optimal
  3. AI auto-commits a flip of the user's literal without re-reading the
     PR body or asking for approval — treating its own analysis output
     as a ratified directive
  4. User redirects back to the originally-stated design → revert
  5. Cost: extra commits, PR body rewrite, reviewer-timeline noise,
     occasionally a duplicate follow-up issue spawned from the same
     analysis-as-directive pipeline

Block conditions (ALL must hold):
  (a) Tool is Bash with a content `git commit` (not amend/merge/rebase/
      cherry-pick/revert). Classification is shlex token-based with
      `punctuation_chars` so shell operators split reliably, so `git commit`
      inside a quoted arg (`echo "git commit"`) and the `commit-tree` plumbing
      subcommand do not match. EVERY `git commit` in a compound command is
      checked, so a later content commit cannot hide behind an earlier
      exempt/ratified one (`git commit --amend && git commit -m x`).
  (b) Recent transcript tail (last ~200 lines) contains a sciomc/finding
      marker: "sibling-deviant", "Stage N finding/analysis/complete",
      "sciomc", "[FINDING:", "[STAGE_COMPLETE:", "scientist-agent",
      "deep-dive", "cross-validation", "의미 mismatch"
  (c) No `gh pr view ... --json body` OR `gh issue view ... --json body` OR
      explicit ratification token was emitted AFTER the most recent finding
      marker in the transcript tail

Allow conditions (escape hatches):
  - Commit -m/--message message contains a [user-approved] / [ratified-by-user]
    / [user-ratified] token (a -F file / heredoc body is not argv-visible —
    use the env bypass there)
  - CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 env var
  - git commit --amend / git merge / git rebase / git cherry-pick / git revert
  - Unparseable command (unbalanced quotes) → fail-open

NOT exempt: --allow-empty / --allow-empty-message. They permit an empty commit
or message but do not prevent staged content from being committed, so a content
commit using them is still gated (use the env bypass for an intentional empty
commit).

Known limitation: a `git commit` whose subcommand is produced via command
substitution / eval (`git $(echo commit)`, `bash -c '...'`) is not detected.
Defeating arbitrary shell metaprogramming is out of the threat model (the
threat is an inadvertent auto-flip after a finding — issue #374 — not a
deliberately obfuscated dodge of one's own guardrail). Use the env bypass for
any legitimate edge case.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0  # fail-open on malformed payload

    if os.environ.get("CLAUDE_HOOK_BYPASS_SCIOMC_GATE") == "1":
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    invocations = _commit_invocations(command)
    # Block if ANY invocation is a gated content commit — not exempt via --amend
    # and not ratified via a message token. Checking every invocation (not just
    # the first) closes compound-command bypasses such as
    # `git commit --amend --no-edit && git commit -m "feat: x"`, where the first
    # invocation is exempt but the second is an unapproved content commit.
    if not any(
        not _is_exempt(args) and not _has_ratification_token(args)
        for args in (invocations or [])
    ):
        return 0  # no git commit, unparseable, or every commit exempt/ratified

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0  # no transcript → cannot enforce

    tail = _read_transcript_tail(transcript_path, max_lines=200, max_bytes=50 * 1024 * 1024)
    if tail is None:
        return 0

    finding_indices = _find_marker_indices(tail, _FINDING_MARKERS)
    if not finding_indices:
        return 0  # no finding context → allow

    last_finding_idx = max(finding_indices)
    after_finding = tail[last_finding_idx:]

    if _has_consensus_refetch(after_finding):
        return 0

    matched = sorted({m.group(0).strip() for m in _iter_marker_matches(tail, _FINDING_MARKERS)})[:3]
    _emit_block_message(matched)
    return 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Command classification operates on shlex tokens, not the raw command string.
# Raw-string matching is unsound for a commit gate: `--amend` inside a -m
# message would falsely exempt a content commit, `git commit-tree` would falsely
# match `commit` via a `\b` boundary, and `echo "git commit"` would falsely trip
# the gate on a non-commit command.
_SHELL_SEPARATORS = {";", "&", "&&", "|", "||", "(", ")", "<", ">", "\n"}
# git's separate-value global options (every other value-opt uses --opt=value,
# handled generically by the startswith("-") skip). Keep in sync with git.
_GLOBAL_OPTS_WITH_VALUE = {"-c", "-C", "--git-dir", "--work-tree", "--namespace"}
# Only `--amend` is exempt. `--allow-empty` / `--allow-empty-message` are NOT:
# they permit an empty commit/message but do not prevent staged content from
# riding along, so exempting them would let `git commit --allow-empty -m x`
# (with staged changes) bypass the gate.
_EXEMPT_FLAGS = {"--amend"}
_MESSAGE_FLAGS = {"-m", "--message"}
_RATIFICATION_TOKEN_RE = re.compile(
    r"\[(?:user-approved|ratified-by-user|user-ratified)\]",
    re.IGNORECASE,
)

_FINDING_MARKERS = (
    re.compile(r"\bsibling[- ]deviant\b", re.IGNORECASE),
    re.compile(r"\bStage\s*\d\s*(?:분석|finding|analysis|결과|complete)", re.IGNORECASE),
    re.compile(r"\bsciomc\b", re.IGNORECASE),
    re.compile(r"\[FINDING:"),
    re.compile(r"\[STAGE_COMPLETE:"),
    re.compile(r"\bscientist[- ]agent\b", re.IGNORECASE),
    re.compile(r"\breview[- ]finds?\b", re.IGNORECASE),
    re.compile(r"\bdeep[- ]dive\b", re.IGNORECASE),
    re.compile(r"\bcross[- ]validation\b", re.IGNORECASE),
    re.compile(r"\b의미\s*mismatch\b", re.IGNORECASE),
    re.compile(r"\b의미\s*충돌\b"),
)

_CONSENSUS_REFETCH_MARKERS = (
    re.compile(r"\bgh\s+pr\s+view\s+[^\n]*--json\s+[^\n]*body", re.IGNORECASE),
    re.compile(r"\bgh\s+issue\s+view\s+[^\n]*--json\s+[^\n]*body", re.IGNORECASE),
    re.compile(r"\bconsensus\s+re[- ]?fetch", re.IGNORECASE),
    re.compile(r"\bre[- ]?read\s+(?:PR|issue)\s+body", re.IGNORECASE),
    re.compile(r"\buser-stated\s+design\b", re.IGNORECASE),
    re.compile(r"\[ratified-by-user\]", re.IGNORECASE),
    re.compile(r"\[user-approved\]", re.IGNORECASE),
)


def _tokenize(command: str) -> list[str] | None:
    # `punctuation_chars=True` makes shlex emit shell operators (`;`, `&&`,
    # `||`, `|`, `&`, `(`, `)`, `<`, `>`) as standalone tokens even when glued
    # to an adjacent word (`-m "x"; echo` → `… "x" ; echo`). Plain
    # `shlex.split` absorbs the operator into the neighbouring token (`x;`),
    # which would let a later command's `-m` value be misread as this commit's
    # message and bypass the gate.
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None  # unbalanced quotes → unparseable → caller fail-opens


def _commit_invocations(command: str) -> list[list[str]] | None:
    """Return the argument tokens of EVERY real `git commit` invocation in the
    command — each from its `commit` subcommand token up to the next shell
    separator or the next `git` invocation — or None if the command is
    unparseable. An empty list means there is no `git commit`.

    Every invocation is returned (not just the first) so a compound command
    cannot hide an unapproved content commit behind an earlier exempt/ratified
    one. `git commit` inside a quoted argument is not a `git`+`commit` token
    adjacency and is ignored; `git commit-tree` tokenizes as `commit-tree`
    (≠ `commit`); non-content subcommands (`merge`, `rebase`, `cherry-pick`,
    `revert`) are not `commit` and contribute no invocation.
    """
    tokens = _tokenize(command)
    if tokens is None:
        return None
    invocations: list[list[str]] = []
    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]
        if tok == "git" or tok.endswith("/git"):
            j = i + 1
            while j < n:
                t = tokens[j]
                if t in _GLOBAL_OPTS_WITH_VALUE:
                    j += 2  # skip option + its value
                    continue
                if t.startswith("-"):
                    j += 1
                    continue
                break
            if j < n and tokens[j] == "commit":
                args: list[str] = []
                k = j
                while k < n:
                    t = tokens[k]
                    if t in _SHELL_SEPARATORS:
                        break
                    if k > j and (t == "git" or t.endswith("/git")):
                        break
                    args.append(t)
                    k += 1
                invocations.append(args)
                i = k  # resume scanning after this invocation
                continue
            i = j
            continue
        i += 1
    return invocations


def _is_exempt(commit_args: list[str]) -> bool:
    # Exempt flags must be standalone tokens — `--amend` inside a -m message is
    # part of the message value token, not a flag, so it does not match here.
    return any(arg in _EXEMPT_FLAGS for arg in commit_args)


def _message_values(commit_args: list[str]) -> list[str]:
    """Extract -m / --message values (separate, joined `-mMSG`, and
    `--message=MSG` forms). -F/--file values are file paths, not message text."""
    values: list[str] = []
    i = 0
    n = len(commit_args)
    while i < n:
        arg = commit_args[i]
        if arg in _MESSAGE_FLAGS:
            if i + 1 < n:
                values.append(commit_args[i + 1])
            i += 2
            continue
        if arg.startswith("--message="):
            values.append(arg[len("--message="):])
        elif arg.startswith("-m") and len(arg) > 2:
            values.append(arg[2:])
        i += 1
    return values


def _has_ratification_token(commit_args: list[str]) -> bool:
    # Scope the token check to -m/--message values so a token elsewhere in a
    # compound command (`git commit -m x; echo '[user-approved]'`) does not
    # falsely satisfy the ratification escape hatch.
    return any(_RATIFICATION_TOKEN_RE.search(value) for value in _message_values(commit_args))


def _read_transcript_tail(path: str, max_lines: int, max_bytes: int) -> str | None:
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size > max_bytes:
            return None
        text = p.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    lines = text.strip().split("\n")
    return "\n".join(lines[-max_lines:])


def _find_marker_indices(text: str, patterns: tuple[re.Pattern, ...]) -> list[int]:
    return [m.start() for pat in patterns for m in pat.finditer(text)]


def _iter_marker_matches(text: str, patterns: tuple[re.Pattern, ...]):
    for pat in patterns:
        yield from pat.finditer(text)


def _has_consensus_refetch(text: str) -> bool:
    return any(pat.search(text) for pat in _CONSENSUS_REFETCH_MARKERS)


def _emit_block_message(matched_markers: list[str]) -> None:
    sys.stderr.write(
        "\n".join(
            [
                "BLOCKED: `git commit` after sciomc/reviewer finding without user-design consensus re-fetch.",
                "",
                f"Detected finding markers in recent transcript: {', '.join(matched_markers) or '(none)'}",
                "",
                "Rule: User-stated design (PR body / issue body) is RATIFIED.",
                "AI analysis findings (sciomc Stage N, deep-dive, review finds, scientist agent)",
                "are DRAFTS — surface to user, never auto-flip the design via direct commit.",
                "",
                "Pattern (praxis issue #374):",
                "  AI flips a user-stated literal/design based on a sciomc/reviewer finding,",
                "  without re-reading the PR body first. User redirects back → revert + extra",
                "  commits + PR body rewrite + reviewer-timeline noise.",
                "",
                "Resolve by one of:",
                "  1. Re-fetch the user's stated design with:",
                "       gh pr view <N> --json body --jq .body",
                "       gh issue view <N> --json body --jq .body",
                "     Compare against the user's stated design. If conflicting, surface via",
                "     AskUserQuestion BEFORE committing.",
                "  2. If user explicitly approved the change in this session, add token",
                "     [user-approved] or [ratified-by-user] to the commit message.",
                "  3. One-off bypass: prefix with CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1",
                "",
                "CLAUDE.md: Output-Block-Level Falsification Gate / Self-Falsify Before Recommendation Lock",
            ]
        )
        + "\n"
    )


if __name__ == "__main__":
    sys.exit(main())
