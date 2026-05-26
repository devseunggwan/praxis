#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `git commit` when `praxis:codex-review-wrap`
has not been invoked in the current session.

Backs the rule (devseunggwan/ai-dotfiles#93, AGENTS.md `Deliver` table):
`praxis:codex-review-wrap` is a second mandatory independent review pass before
commit. Prose alone is unreliable (prompt-layer retrieval failure); per the
established escalation pattern, this hook enforces the gate at the commit
checkpoint.

This is the inverse of `block-sciomc-finding-commit`: that hook blocks on the
PRESENCE of a finding marker; this one blocks on the ABSENCE of the required
skill invocation.

Block conditions (ALL must hold):
  (a) Tool is Bash with a content `git commit` (not amend/merge/rebase/
      cherry-pick/revert/--allow-empty)
  (b) The session transcript contains NO `Skill` tool_use with
      input.skill == "praxis:codex-review-wrap" AND no
      `/praxis:codex-review-wrap` slash-command invocation

Allow conditions (escape hatches):
  - The commit -m/--message message contains a [skip-codex-review] token
    (a -F file / heredoc body is not argv-visible — use the env bypass there)
  - CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1 env var
  - git commit --amend / git merge / git rebase / git cherry-pick / git revert
  - --allow-empty
  - Missing / unreadable / oversized transcript → fail-open (cannot enforce)
  - Malformed / unparseable command (unbalanced quotes) → fail-open

Semantics: a whole-transcript scan means one codex-review-wrap invocation in
the session satisfies all subsequent commits — the same coarse session-level
granularity as the other commit/PR review gates.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

_TARGET_SKILL = "praxis:codex-review-wrap"
_MAX_BYTES = 50 * 1024 * 1024


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0  # fail-open on malformed payload

    if os.environ.get("CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE") == "1":
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    commit_args = _commit_invocation_args(command)
    if commit_args is None:
        return 0  # no real `git commit` invocation (or unparseable) → fail-open

    if _is_exempt(commit_args):
        return 0  # --amend / --allow-empty etc.

    if _has_skip_token(commit_args):
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0  # no transcript → cannot enforce

    invoked = _scan_transcript(transcript_path)
    if invoked is None:
        return 0  # unreadable/oversized → fail-open
    if invoked:
        return 0  # codex-review-wrap ran this session → allow

    _emit_block_message()
    return 2


# ---------------------------------------------------------------------------
# Command classification
#
# Classification operates on shlex tokens, not the raw command string. Matching
# a raw string is unsound for a commit gate: `--amend` inside a -m message would
# falsely exempt a content commit, `git commit-tree` would falsely match `commit`
# via the `\b` boundary, and `echo "git commit"` / `git log --grep="git commit"`
# would falsely trip the gate on a non-commit command.
# ---------------------------------------------------------------------------

_SKIP_TOKEN_RE = re.compile(r"\[skip-codex-review\]", re.IGNORECASE)
_SHELL_SEPARATORS = {";", "&", "&&", "|", "||", "\n"}
_GLOBAL_OPTS_WITH_VALUE = {"-c", "-C", "--git-dir", "--work-tree", "--namespace"}
_EXEMPT_FLAGS = {"--amend", "--allow-empty", "--allow-empty-message"}
_MESSAGE_FLAGS = {"-m", "--message"}


def _tokenize(command: str) -> list[str] | None:
    try:
        return shlex.split(command, comments=False, posix=True)
    except ValueError:
        return None  # unbalanced quotes → unparseable → caller fail-opens


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
        if arg.startswith("--message="):
            values.append(arg[len("--message="):])
        elif arg.startswith("-m") and len(arg) > 2:
            values.append(arg[2:])
        i += 1
    return values


def _has_skip_token(commit_args: list[str]) -> bool:
    # The documented escape hatch is a token in the commit message — scope the
    # check to -m/--message values so a token elsewhere in a compound command
    # (`git commit -m x; echo '[skip-codex-review]'`) does not bypass the gate.
    return any(_SKIP_TOKEN_RE.search(value) for value in _message_values(commit_args))


# ---------------------------------------------------------------------------
# Transcript scan
# ---------------------------------------------------------------------------

# The namespace prefix is optional: a slash command may be typed as either
# `/praxis:codex-review-wrap` or `/codex-review-wrap`. Accepting both is the
# permissive (fewer false blocks) choice for this secondary detection channel —
# the primary `_has_skill_tool_use` path still requires the exact qualified
# skill name. Line-anchored so a prose mention ("run /praxis:codex-review-wrap?")
# does not match.
_SLASH_RE = re.compile(r"^\s*/(?:praxis:)?codex-review-wrap(?:\s.*)?$")
_CMDNAME_RE = re.compile(r"^\s*<command-name>/?(?:praxis:)?codex-review-wrap(?:\s|</|$)")


def _scan_transcript(path: str) -> bool | None:
    """Return True if codex-review-wrap was invoked, False if not, None if the
    transcript cannot be read (caller treats None as fail-open)."""
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size > _MAX_BYTES:
            return None
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        if _has_skill_tool_use(obj) or _has_slash_command(obj):
            return True
    return False


def _has_skill_tool_use(obj: dict) -> bool:
    message = obj.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    for item in content:
        if (
            isinstance(item, dict)
            and item.get("type") == "tool_use"
            and item.get("name") == "Skill"
        ):
            tool_input = item.get("input")
            if isinstance(tool_input, dict) and tool_input.get("skill") == _TARGET_SKILL:
                return True
    return False


def _has_slash_command(obj: dict) -> bool:
    message = obj.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
    for text in texts:
        for ln in text.splitlines():
            if _SLASH_RE.match(ln) or _CMDNAME_RE.match(ln):
                return True
    return False


def _emit_block_message() -> None:
    sys.stderr.write(
        "\n".join(
            [
                "BLOCKED: `git commit` without a `praxis:codex-review-wrap` review pass this session.",
                "",
                "Rule (AGENTS.md Deliver table): codex-review-wrap is a second mandatory",
                "independent review pass before commit — an independent Codex pass after",
                "omc:code-reviewer that catches defects a single reviewer misses.",
                "",
                "Resolve by one of:",
                "  1. Run the review (it is a model-invocable skill, not an agent):",
                "       Skill(skill=\"praxis:codex-review-wrap\")",
                "     then re-run the commit (one run satisfies all commits this session).",
                "  2. Skip for this commit: add a [skip-codex-review] token to the",
                "     commit message (e.g. trivial docs/typo change).",
                "  3. One-off bypass: prefix with CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1",
                "",
                "Fail-open: missing/unreadable transcript and non-content commits",
                "(--amend / merge / rebase / cherry-pick / revert / --allow-empty) pass.",
            ]
        )
        + "\n"
    )


if __name__ == "__main__":
    sys.exit(main())
