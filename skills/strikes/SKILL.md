---
name: strikes
description: Show the current session's strike count (0-3) and the list of recorded violation reasons. Use when the user types "/strikes", "strike status", "몇 진", "check strikes".
---

# Praxis Strike Status

Report the current strike state for the active session.

## What to do

1. Run the strike counter status via the Bash tool:
   ```bash
   "${CLAUDE_PLUGIN_ROOT}/hooks/strike-counter.sh" status
   ```
2. Present the output verbatim. The header line `Strikes: N/3` and the `Reasons:` list (if any) together are the record — do not summarize or rephrase.

## Non-goals

- Do not interpret or rank the violations.
- Do not suggest fixes here — if the user wants to clear, they will call `/praxis:reset-strikes`.
