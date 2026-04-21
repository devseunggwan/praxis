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

## Reinforcement after reset

- Do not treat reset as absolution — the violations happened. Briefly summarize what went wrong this session (if known from the transcript) and state one concrete behavioral adjustment before continuing with user work.
- Do not request a reset on behalf of the user — only execute when the user explicitly invokes this skill.
