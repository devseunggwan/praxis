---
name: strike
description: Declare a rule violation in the current Claude Code session. Use ONLY when the user says "/strike", "/praxis:strike", "strike 1/2/3", "삼진", or explicitly asks to record a rule violation. Do NOT activate on colloquial uses like "strike a balance" or "strike that". Escalates — strike 1 warning, strike 2 forced review, strike 3 response block.
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

- If the script output starts with `⚠️ Strike 1`, internalize the recorded reason and commit to stricter rule adherence for the rest of the session.
- If the script output starts with `🔶 Strike 2`, **stop any in-flight work**, list the cumulative violations, identify the matching CLAUDE.md section, re-read it, and state explicitly how you will avoid another strike before resuming.
- If the script output starts with `🔴 Strike 3`, recovery is a **two-step trust process**:
  1. **Write the reflection** at the path the script printed — violations summary, root cause per violation tied to a specific CLAUDE.md rule/section, and a concrete preventive checklist. The file must be non-empty or `/praxis:reset-strikes` will be refused.
  2. **Persuade the user** before asking for reset: quote or summarize the reflection in-chat (do not just point at the file path), acknowledge the specific harm each violation caused, commit to the preventive checklist in concrete terms, then explicitly ask the user to run `/praxis:reset-strikes` as a trust decision. Do not treat the user's approval as mechanical — it is a judgment call based on your appeal.

## Non-goals

- Do not interpret, judge, or argue with the reason. The user's assessment is the record.
- Do not attempt to "recover" a previously recorded strike — use `/praxis:reset-strikes` instead.
