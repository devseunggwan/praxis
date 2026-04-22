---
name: reset-strikes
description: Reset the current session's strike counter to 0 and clear the recorded violation list. Required after a 3rd strike block before Claude can respond again. Use when the user types "/reset-strikes", "strike 초기화", "clear strikes".
---

# Praxis Strike Reset

Clear the session strike counter so the discipline signals restart from 0.

## What to do

1. Run the strike counter reset via the Bash tool:
   ```bash
   "${CLAUDE_PLUGIN_ROOT}/hooks/strike-counter.sh" reset
   ```
2. Report the output verbatim. If the session was blocked at 3/3, acknowledge the reset and confirm that future responses will proceed normally until a new strike is declared.

## Reflection gate at 3/3

When the session is blocked at 3/3, reset is **conditional on a written reflection + an explicit user trust decision**.

The strike/stop-hook output names an exact file path (`$STATE_DIR/${SID}.reflection.md`) and the required content structure (violations summary, root cause per violation mapped to a CLAUDE.md rule/section, preventive checklist). Recovery is a two-step process:

1. **Reflection file** — written by Claude, gates the file check. If missing or empty when reset is called, the script prints `❌ Reset refused — reflection missing or empty.` and re-states the path and required contents. The counter is not cleared.
2. **Persuasion turn** — before the user invokes this skill, Claude must present the reflection in-chat (quote or summarize, do not just point at the path), acknowledge each violation's harm, state concrete behavioral commitments, and explicitly ask the user to run `/praxis:reset-strikes`. This is a trust decision, not a mechanical unlock — the user may decline and require more.

Additional behavior:
- On a successful 3/3 reset, the reflection file is removed alongside the state so the next cycle starts clean and cannot reuse a stale document.
- Below 3/3, reset is not gated — the reflection + persuasion requirement only applies at the block threshold.

## Reinforcement after reset

- Do not treat reset as absolution — the violations happened. Briefly summarize what went wrong this session (if known from the transcript) and state one concrete behavioral adjustment before continuing with user work.
- Do not request a reset on behalf of the user — only execute when the user explicitly invokes this skill.
