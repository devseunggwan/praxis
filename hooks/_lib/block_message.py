"""Standard block-message format for praxis preflight-gate hooks (issue #439).

Every preflight-gate hook that rejects a command used to hand-roll its own
stderr/reason text, so the wording, field order, and "how do I bypass this"
guidance drifted hook-to-hook. This module pins a single five-field format so
the agent (and the user reading the transcript) sees the same shape every time:

    ⚠️ <RULE_NAME> blocked

    Why: <one line — which convention is violated>
    Correct path: <specific next action — skill name, command, or pattern>
    Bypass (if truly needed): <env var name> + <reason comment requirement>
    Reference: <CLAUDE.md section, docs/ path, or wiki link>

Field semantics (which fields are mandatory vs informational) are documented in
`docs/hook/block-message-format.md`. In short:

  - rule_name, why, correct_path, reference  → MANDATORY (always rendered)
  - bypass_env                               → informational; omit the Bypass
                                               line entirely when a hook has no
                                               authoritative bypass (e.g. the
                                               pre-merge approval gate, where a
                                               self-bypass would defeat the
                                               gate). Pass `bypass_env=None`.

Two entry points:

  format_block(...)  → returns the formatted string (no I/O). Use this when the
                       hook emits via a JSON `permissionDecisionReason` (the
                       `ask` decision path) rather than raw stderr.
  emit_block(...)    → writes the formatted string to stderr (+ trailing
                       newline). Use this on the stderr/exit-2 block path.

Neither function exits the process or prints the cascade hint — the caller
keeps control of the exit code and may append `compound_cascade_hint(command)`
after the block text, exactly as before.
"""
from __future__ import annotations

import sys
from typing import Optional, TextIO


def format_block(
    rule_name: str,
    why: str,
    correct_path: str,
    bypass_env: Optional[str],
    reference: str,
    bypass_reason_hint: str = "with a one-line reason comment explaining why",
) -> str:
    """Render the standard five-field block message and return it as a string.

    Args:
      rule_name: the hook / rule identifier, e.g. "gh search --state all".
        Rendered uppercased in the header so it reads as a label.
      why: one line naming the violated convention.
      correct_path: the specific next action — a skill name, exact command,
        or pattern the agent should run instead.
      bypass_env: the environment variable that bypasses this gate, e.g.
        "CLAUDE_HOOK_BYPASS_DUP_GATE". Pass None when the gate has no
        authoritative bypass — the Bypass line is then omitted entirely.
      reference: a CLAUDE.md section, docs/ path, or wiki link to read.
      bypass_reason_hint: trailing guidance on the Bypass line describing the
        reason-comment requirement. Ignored when bypass_env is None.

    Returns:
      The multi-line block message WITHOUT a trailing newline (callers that
      write to stderr add their own newline; the cascade hint, if any, is
      appended by the caller).
    """
    lines = [
        f"⚠️ {rule_name.upper()} blocked",
        "",
        f"Why: {why}",
        f"Correct path: {correct_path}",
    ]
    if bypass_env:
        lines.append(f"Bypass (if truly needed): {bypass_env}=1 {bypass_reason_hint}")
    lines.append(f"Reference: {reference}")
    return "\n".join(lines)


def pr_body_evidence_checklist() -> str:
    """Full PR-body evidence-token enumeration for pr-body gate deny messages.

    The two pr-body gates (block-pr-without-caller-evidence,
    block-pr-without-precommit-evidence) each deny on their own missing
    token, so an author discovers the required tokens one deny at a time
    (praxis #824). Appending this checklist to BOTH deny messages teaches
    the full enumeration on the first deny.

    Returns the checklist WITH a trailing newline — callers concatenate it
    after their own newline-terminated block message and before any
    cascade hint.
    """
    return """\

📋 Full PR-body evidence checklist (satisfy ALL in one authoring pass):

  Caller chain verified: <evidence>         ← block-pr-without-caller-evidence
  Pre-commit verified: <command + result>   ← block-pr-without-precommit-evidence

Format rules: each token starts at column 0 of its own line, content follows
the colon on the same line, and the line must sit OUTSIDE fenced code blocks.

Related gates nearby: `git commit` needs a codex review pass or a
`[skip-codex-review]` marker; an AskUserQuestion `(Recommended)` label needs
a `Falsified:` line.
"""


def emit_block(
    rule_name: str,
    why: str,
    correct_path: str,
    bypass_env: Optional[str],
    reference: str,
    bypass_reason_hint: str = "with a one-line reason comment explaining why",
    stream: Optional[TextIO] = None,
) -> None:
    """Write the standard block message to stderr (+ trailing newline).

    Same args as `format_block`. `stream` is injectable for testing; defaults
    to sys.stderr. Does NOT exit — the caller owns the exit code (2 for a
    PreToolUse block).
    """
    out = stream if stream is not None else sys.stderr
    out.write(format_block(rule_name, why, correct_path, bypass_env, reference, bypass_reason_hint))
    out.write("\n")
