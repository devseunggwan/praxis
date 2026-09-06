---
name: strike
description: Declare a rule violation in the current Claude Code session. Escalates — strike 1 warning, strike 2 forced review, strike 3 response block.
when_to_use: Use ONLY when the user says "/strike", "/praxis:strike", "strike 1/2/3", "삼진", or explicitly asks to record a rule violation. Do NOT activate on colloquial uses like "strike a balance" or "strike that".
disable-model-invocation: true
---

# Praxis Strike

Record a single rule violation against the current session's strike counter.

## What to do

1. Treat the user's full argument text as the violation reason (verbatim, do not sanitize or abbreviate).
2. Run the strike counter via the Bash tool, passing the reason as one
   argument:

   ```bash
   "${CLAUDE_PLUGIN_ROOT:?praxis plugin root not set — run via the installed plugin or export CLAUDE_PLUGIN_ROOT}/hooks/strike-counter.sh" strike '<reason>'
   ```

   `<reason>` is **not** a placeholder the host fills in. The host substitutes
   `{{ARGUMENTS}}` in this file's text before you read it (see
   `writing-praxis-skill` → Host Differences), and you then compose the Bash
   call — so the quoting is yours to get right. Two rules:

   - **Single-quote the reason, and write `'\''` for each `'` inside it.**
     Reasons routinely carry backticks (this repo quotes `code` everywhere) and
     may carry `$(...)` or `"`. Inside double quotes the shell would run those
     as command substitution or break the command outright; inside single
     quotes every byte is literal, which is what step 1's "verbatim" requires.
   - **Do not spell the placeholder as a shell variable.** It is unset in a
     skill-body Bash block, so it would expand to empty and record a blank
     reason.

   Example — a reason that itself contains backticks. Keep a real CLI name
   out of the backticks: `check-plugin-manifests.py` reads an inline
   backticked command in an instruction context as this skill *invoking*
   that CLI, which would demand runtime-verification frontmatter the skill
   has no way to earn.

   ```bash
   "${CLAUDE_PLUGIN_ROOT:?praxis plugin root not set — run via the installed plugin or export CLAUDE_PLUGIN_ROOT}/hooks/strike-counter.sh" strike 'claimed `PASS(live)` without running anything'
   ```

3. Report the script's stdout verbatim to the user. Do not paraphrase the level-specific message — the exact wording is part of the discipline signal.

## Reinforcement after the call

- If the script output starts with `⚠️ Strike 1`, internalize the recorded reason and commit to stricter rule adherence for the rest of the session.
- If the script output starts with `🔶 Strike 2`, **stop any in-flight work**, list the cumulative violations, identify the rule that was broken (project `CLAUDE.md` / `AGENTS.md`, `ETHOS.md`, or the user's global `CLAUDE.md`), re-read it, and state explicitly how you will avoid another strike before resuming.
- If the script output starts with `🔴 Strike 3`, recovery is a **two-step trust process**:
  1. **Write the reflection** at the path the script printed — violations summary, root cause per violation tied to a specific rule (project `CLAUDE.md` / `AGENTS.md`, `ETHOS.md`, or the user's global `CLAUDE.md`), and a concrete preventive checklist. The file must be non-empty or `/praxis:reset-strikes` will be refused.
  2. **Persuade the user** before asking for reset: quote or summarize the reflection in-chat (do not just point at the file path), acknowledge the specific harm each violation caused, commit to the preventive checklist in concrete terms, then explicitly ask the user to run `/praxis:reset-strikes` as a trust decision. Do not treat the user's approval as mechanical — it is a judgment call based on your appeal.

## Error Handling

| Situation | Handling |
| --------- | -------- |
| `CLAUDE_PLUGIN_ROOT` unset (skill run outside plugin context) — the `:?` guard aborts with `praxis plugin root not set` instead of silently trying `/hooks/strike-counter.sh` | Resolve the plugin root via the installed-plugins manifest: `jq -r '.plugins["praxis@praxis"][0].installPath // empty' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json"`, export it as `CLAUDE_PLUGIN_ROOT`, and re-run; if still unresolved (manifest missing or no praxis entry), report the failure verbatim and stop — do not fabricate a strike record by hand |

## Non-goals

- Do not interpret, judge, or argue with the reason. The user's assessment is the record.
- Do not attempt to "recover" a previously recorded strike — use `/praxis:reset-strikes` instead.
