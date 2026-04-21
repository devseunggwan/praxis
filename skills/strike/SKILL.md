---
name: strike
description: Declare a rule violation in the current Claude Code session. Use ONLY when the user says "/strike", "/praxis:strike", "strike 1/2/3", "삼진", or explicitly asks to record a rule violation. Do NOT activate on colloquial uses like "strike a balance" or "strike that". Escalates — 1진 warning, 2진 forced review, 3진 response block.
---

# Praxis Strike

Record a single rule violation against the current session's strike counter.

## What to do

1. Treat the user's full argument text as the violation reason (verbatim, do not sanitize or abbreviate).
2. Run the strike counter via the Bash tool, passing the reason as the argument:
   ```bash
   "${CLAUDE_PLUGIN_ROOT}/hooks/strike-counter.sh" strike "$ARGUMENTS"
   ```
3. Report the script's stdout verbatim to the user. Do not paraphrase the level-specific message — the exact wording is part of the discipline signal.

## Reinforcement after the call

- If the script output starts with `⚠️ 1진`, internalize the recorded reason and commit to stricter rule adherence for the rest of the session.
- If the script output starts with `🔶 2진`, **stop any in-flight work**, list the cumulative violations, identify the matching CLAUDE.md section, re-read it, and state explicitly how you will avoid another strike before resuming.
- If the script output starts with `🔴 3진`, announce that the next response will be blocked by the Stop hook, and ask the user whether to run `/praxis:reset-strikes`.

## Non-goals

- Do not interpret, judge, or argue with the reason. The user's assessment is the record.
- Do not attempt to "recover" a previously recorded strike — use `/praxis:reset-strikes` instead.
