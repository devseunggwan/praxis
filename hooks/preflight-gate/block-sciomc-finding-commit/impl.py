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
      cherry-pick/revert)
  (b) Recent transcript tail (last ~200 lines) contains a sciomc/finding
      marker: "sibling-deviant", "Stage N finding/analysis/complete",
      "sciomc", "[FINDING:", "[STAGE_COMPLETE:", "scientist-agent",
      "deep-dive", "cross-validation", "의미 mismatch"
  (c) No `gh pr view ... --json body` OR `gh issue view ... --json body` OR
      explicit ratification token was emitted AFTER the most recent finding
      marker in the transcript tail

Allow conditions (escape hatches):
  - Commit message (-m / --message value) contains [user-approved] or
    [ratified-by-user] token (scoped to message value only)
  - CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 env var
  - git commit --amend / git merge / git rebase / git revert / git cherry-pick

NOT exempt: --allow-empty / --allow-empty-message. These flags do NOT prevent
staged content from being committed — `git commit --allow-empty -m x` with a
staged file commits that file. Exempting them would let staged changes bypass
the gate. An intentional empty CI-trigger commit uses the [user-approved] token
or the env bypass instead.

Classification is token-level (shlex), not raw-string, so:
  - `git commit-tree` plumbing does not match (tokenizes as one token)
  - `echo "git commit"` does not trip the gate (no `git`+`commit` adjacency)
  - `--amend` inside a `-m` message is a value token, not a flag
  - `[user-approved]` outside the message value (e.g. after &&) does not bypass
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
    commit_args = _commit_invocation_args(command)
    if commit_args is None:
        return 0  # no real `git commit` invocation (or unparseable) → fail-open

    if _is_exempt(commit_args):
        return 0  # --amend (fix-up of an already-gated commit)

    if _has_ratification_token(commit_args):
        return 0

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
# Command classification
#
# Classification operates on shlex tokens, not the raw command string.
# Matching a raw string is unsound for a commit gate:
#   - `--amend` inside a -m message would falsely exempt a content commit
#   - `git commit-tree` tokenizes as one token "commit-tree" (≠ "commit")
#     so plumbing subcommands do not match
#   - `echo "git commit"` has no `git`+`commit` token adjacency → no match
#   - `[user-approved]` outside the -m value (e.g. after &&) does not bypass
#
# Only `--amend` is exempt: its intent is to fix up a prior (already-gated)
# commit. `--allow-empty` / `--allow-empty-message` are deliberately NOT exempt
# — they only *permit* an empty commit / message and do not *prevent* staged
# content from riding along, so exempting them would let
# `git commit --allow-empty -m x` (with staged changes) bypass the gate.
# Verified functionally: both flags commit staged files.
# ---------------------------------------------------------------------------

_SHELL_SEPARATORS = {";", "&", "&&", "|", "||", "\n"}
_GLOBAL_OPTS_WITH_VALUE = {"-c", "-C", "--git-dir", "--work-tree", "--namespace"}
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
    # punctuation_chars=True splits shell operators (`;`, `&&`, `|`, `(`, `)`,
    # redirects) into their own tokens even when not space-delimited, while
    # quotes still protect their contents. Plain shlex.split folds
    # `true;git commit` into a single `true;git` token, letting a content commit
    # ride behind a space-less separator — a bypass the prior raw regex caught.
    # whitespace_split=True keeps normal word splitting; commenters='' preserves
    # the old comments=False behaviour (a bare `#` is not a comment delimiter).
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        return list(lex)
    except ValueError:
        return None  # unbalanced quotes → unparseable → caller fail-opens


# shlex (posix) does not treat shell grouping / command-substitution syntax as
# operators, so `(git commit …)` and `echo $(git commit …)` tokenize the binary
# as `(git` / `$(git` / `` `git ``. Without normalizing these, the parser would
# miss a real content commit wrapped in a subshell/group — a bypass the prior
# raw-regex (`\bgit\s+commit\b`) happened to catch. Strip a leading run of
# grouping/substitution chars before the binary-name check.
_GROUP_PREFIX_CHARS = "(){}$`"


def _is_git_binary(token: str) -> bool:
    stripped = token.lstrip(_GROUP_PREFIX_CHARS)
    return stripped == "git" or stripped.endswith("/git")


def _commit_invocation_args(command: str) -> list[str] | None:
    """Return the argument tokens of the first real `git commit` invocation —
    from the `commit` subcommand token up to the next shell separator or the
    next `git` invocation — or None if the command contains no `git commit`.

    Token-level detection means `git commit` inside a quoted argument
    (`echo "git commit"`, `git log --grep="git commit"`) is not a `git`+`commit`
    adjacency and is ignored, and `git commit-tree` tokenizes as the single
    token `commit-tree` (≠ `commit`) so plumbing subcommands do not match.
    Non-content subcommands (`merge`, `rebase`, `cherry-pick`, `revert`) are
    also not `commit` and return None.
    """
    tokens = _tokenize(command)
    if tokens is None:
        return None
    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]
        if _is_git_binary(tok):
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
                    if k > j and _is_git_binary(t):
                        break
                    args.append(t)
                    k += 1
                return args
            i = j
            continue
        i += 1
    return None


def _is_exempt(commit_args: list[str]) -> bool:
    # Exempt flags must be standalone tokens — `--amend` inside a -m message is
    # part of the message value token, not a flag, so it does not match here.
    return any(arg in _EXEMPT_FLAGS for arg in commit_args)


def _message_values(commit_args: list[str]) -> list[str]:
    """Extract -m / --message values (separate, joined `-mMSG`, and
    `--message=MSG` forms). -F/--file values are file paths, not the message
    text, so they are not inspected."""
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
        # Clustered short options ending in -m, e.g. `-am 'msg'` or `-am'msg'`
        # (-ammsg). git requires -m last in a short cluster; its value is the
        # attached remainder or the next token. Without this, the ratification
        # escape-hatch silently fails for the common `git commit -am` form.
        if (
            arg.startswith("-")
            and not arg.startswith("--")
            and len(arg) > 2
            and arg[1] != "m"            # `-m`/`-mMSG` handled separately
            and "m" in arg
            and arg[1:arg.index("m")].isalpha()
        ):
            rest = arg[arg.index("m") + 1:]
            if rest:
                values.append(rest)
            elif i + 1 < n:
                values.append(commit_args[i + 1])
                i += 1  # consume the value token
            i += 1
            continue
        if arg.startswith("--message="):
            values.append(arg[len("--message="):])
        elif arg.startswith("-m") and len(arg) > 2:
            values.append(arg[2:])
        i += 1
    return values


def _has_ratification_token(commit_args: list[str]) -> bool:
    # Scope the check to -m/--message values so a token elsewhere in a compound
    # command (`git commit -m x; echo '[user-approved]'`) does not bypass the gate.
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
