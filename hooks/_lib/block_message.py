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

import re
import sys
from pathlib import Path as _Path
from typing import Optional, TextIO

# Sibling `_lib` import: a hook reaches this module by putting `_lib` on
# sys.path, but `tests/test_block_message.py` loads the file directly, so the
# directory is added here too rather than relied on from the caller.
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from _hosts import installed_hook_names  # type: ignore[import-not-found]  # noqa: E402


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


# ---------------------------------------------------------------------------
# Verb-keyed gate checklists (issue #873)
#
# Several gates fire on the SAME action verb, each denying on its own missing
# token. An author therefore discovers the requirements one deny at a time,
# spending a retry turn per gate — praxis #873 measured six such blocks in a
# single session, at least four of which had their satisfaction form already
# documented. Documentation was never the gap; retrieval at call time was.
#
# The fix is to make the FIRST deny on a verb teach every token that verb
# owes, so the author can satisfy them in one authoring pass. Issue #824
# introduced this for `gh pr create`; this registry generalises it.
#
# Each entry is verified against the owning hook's own source, not recalled —
# a checklist that names a token the gate does not actually accept is worse
# than no checklist, because it is read as authoritative.
# ---------------------------------------------------------------------------

_VERB_CHECKLISTS: dict[str, str] = {
    "gh pr create": """\

📋 Full PR-body evidence checklist (satisfy ALL in one authoring pass):

  Caller chain verified: <evidence>         ← block-pr-without-caller-evidence
  Pre-commit verified: <command + result>   ← block-pr-without-precommit-evidence

Format rules: each token starts at column 0 of its own line, content follows
the colon on the same line, and the line must sit OUTSIDE fenced code blocks.

Related gate — `git commit`                 ← block-commit-without-codex-review
  Needs a codex review pass this session, or a `[skip-codex-review]` token in
  the commit message.
Related gate — `AskUserQuestion`            ← output-block-falsify-advisory
  A `(Recommended)` label needs a `Falsified:` line.
""",
    "gh pr merge": """\

📋 Every gate on `gh pr merge` (satisfy ALL before re-running):

Unconditional — fire on every `gh pr merge`, whatever the flags:
  6-item briefing in this turn              ← momentum-rule-retrieval-gate
    What changed / verified / NOT verified / Risk / Open items / approve-ask.
    Relief: an unquoted shell comment `# briefing-surfaced: <reason>` on the
    merge command itself. PRAXIS_MOMENTUM_MERGE_ADVISORY=1 also works, but
    ONLY from the session environment — the hook is spawned by the harness,
    so an inline `VAR=1 gh pr merge …` prefix never reaches it.
  Explicit per-PR user approval             ← pre-merge-approval-gate
    Always asks. No agent-attachable bypass exists, by design.
  Intentional-side-effect acknowledgement   ← side-effect-scan
    Opt out in-band: `# side-effect:ack`.

Conditional — fire only in the stated situation:
  Head-branch worktree removed first        ← gh-merge-worktree-precondition
    Only with `--delete-branch` / `-d`.
  PR title ≤ 50 chars                       ← commit-title-length-check
    Only with `--squash` / `-s` (the PR title becomes the squash commit
    title). Advisory.
  Merge piped into another command          ← pipefail-advisory
    `gh pr merge ... | tail -3` reports success even when the merge failed.
  Mutation verb declared for this session   ← session-intent
    A session that opened read-only and never stated a mutation intent: asks,
    and denies under PRAXIS_INTENT_PIVOT_MODE=block.
  Required skill not invoked this session   ← skill-gate-commands
    NO-OP unless PRAXIS_SKILL_GATED_COMMANDS lists `gh pr merge`.

Approving one PR approves only that PR — a companion or follow-up merge needs
its own briefing and its own answer.
""",
    "AskUserQuestion": """\

📋 Every gate on `AskUserQuestion` (satisfy ALL before re-running):

  Falsified: <result>                       ← output-block-falsify-advisory
    Required for a `(Recommended)` label and for confidence-anchoring framing
    (safer / obvious / default / 당연히 / 추천 …). Must begin at column 0 of
    its own line — prose, bullets, and code fences are not detected. Put it
    in the triggering option's own `description`.
  No end-of-session option                  ← block-ask-end-option
    Direct ("세션 종료") and indirect ("잠시 보류", "take a break") forms both
    block without a user stop signal. Opt out by setting
    PRAXIS_ASK_END_ADVISORY=1 in the SESSION environment — the hook reads its
    own process env, so an inline `VAR=1 <cmd>` prefix never reaches it and
    there is no per-call marker.

Conditional — fire only in the stated situation:
  No manufactured action menu               ← block-manufactured-action-menu
    Do not re-ask a question the user's own prior message already answered.
    Advisory by default; blocks under PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT=1.
  Live PR state re-fetched                  ← pr-state-refetch-gate
    A merge-intent question naming a PR number: warns when that PR is already
    MERGED/CLOSED, blocks under PRAXIS_PR_STATE_REFETCH_STRICT=1.
  Merge menu offers a review option         ← merge-menu-review-options-advisory
    A merge-decision menu with no review/inspect option; blocks under
    PRAXIS_MERGE_MENU_REVIEW_STRICT=1.
  Menu offers a non-mutating tier           ← menu-mutation-tier-advisory
    Two or more candidates remain after abandonment options are dropped, at
    least one of them mutates shared state, and none names a non-mutating
    alternative (preview / dev / sandbox / dry-run / review / 보고만 / 조회만).
    An option that merely declines to act ("다음 정기 실행에 맡김", "skip") is
    neither a candidate nor that tier. State `Safe-tier-unavailable: <reason>`
    on its own line in the question body when no safe tier exists. Blocks
    under PRAXIS_MENU_MUTATION_TIER_STRICT=1.

Advisory only — never block, no action needed to proceed:
  pre-output-falsification-gate, memory-hint (stderr reminders).
""",
}


# Row anchor: every checklist row ends its first line in `← <hook-name>`, and
# the indented lines under it are that row's remedy prose. The same anchor
# `verify-commit-flag-override` uses for its own deny checklist (issue #1154),
# so one filter shape serves both surfaces.
_ROW_ANCHOR_RE = re.compile(r"←\s*([a-z0-9][a-z0-9-]*)")


def filter_gate_rows(text: str, installed: set[str]) -> str:
    """`text` with every row naming a hook outside `installed` removed.

    A row runs from its `←` line until the next row or the next section break.
    A section break is a non-indented line that follows a blank one — the
    checklists use those for their headers ("Conditional — fire only in …") and
    their closing prose, and those belong to the checklist rather than to the
    row above them. Without that rule a dropped row would take the next
    section's header with it.

    Returns "" when the text had rows and none survived, so a caller never
    emits a header introducing an empty list.
    """
    kept: list[str] = []
    row: str | None = None
    saw_row = False
    kept_row = False
    prev_blank = True
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\n")
        match = _ROW_ANCHOR_RE.search(line)
        if match:
            row = match.group(1)
            saw_row = True
        elif row is not None and prev_blank and body.strip() and not body[0].isspace():
            row = None
        if row is None or row in installed:
            kept.append(line)
            kept_row = kept_row or row is not None
        prev_blank = not body.strip()
    if saw_row and not kept_row:
        return ""
    return "".join(kept)


def verb_gate_checklist(verb: str, host: str | None = None) -> str:
    """Full gate-token enumeration for every gate that fires on `verb`.

    Args:
      verb: the action verb key — one of the `_VERB_CHECKLISTS` keys, e.g.
        "gh pr create", "gh pr merge", "AskUserQuestion".
      host: the platform this run is executing on (`_hosts.runtime_host()`).
        Rows naming a hook that host does not install are dropped, because
        several of the gates enumerated here carry a `hosts` whitelist while
        the hooks printing the checklist do not — so the unfiltered text asked
        for tokens no installed gate requires and offered bypasses that do not
        exist there (issue #1245, same shape as #1154). None — an unresolvable
        or unknown host — keeps every row: naming an absent gate wastes a
        reader's time, while dropping a present one hides the next hard block.

    Returns:
      The checklist WITH a leading blank line and a trailing newline — callers
      concatenate it after their own newline-terminated block message and
      before any cascade hint. An unregistered verb returns "" so a caller
      cannot inject a stray blank block into its deny message.
    """
    text = _VERB_CHECKLISTS.get(verb, "")
    if not text:
        return ""
    installed = installed_hook_names(host)
    if installed is None:
        return text
    return filter_gate_rows(text, installed)


def pr_body_evidence_checklist(host: str | None = None) -> str:
    """Back-compat alias for `verb_gate_checklist("gh pr create")` (#824)."""
    return verb_gate_checklist("gh pr create", host)


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
