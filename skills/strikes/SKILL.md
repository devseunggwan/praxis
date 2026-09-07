---
name: strikes
description: Show the current session's strike count (0-3) and the list of recorded violation reasons.
when_to_use: Use when the user types "/strikes", "strike status", "몇 진", "check strikes".
disable-model-invocation: true
---

# Praxis Strike Status

Report the current strike state for the active session.

## What to do

1. Run the strike counter status via the Bash tool:

   ```bash
   "${CLAUDE_PLUGIN_ROOT:?praxis plugin root not set — run via the installed plugin or export CLAUDE_PLUGIN_ROOT}/hooks/strike-counter.sh" status
   ```

2. Present the output verbatim. The header line `Strikes: N/3` and the `Reasons:` list (if any) together are the record — do not summarize or rephrase.

## Error Handling

| Situation | Handling |
| --------- | -------- |
| `CLAUDE_PLUGIN_ROOT` unset (skill run outside plugin context) — the `:?` guard aborts with `praxis plugin root not set` instead of silently trying `/hooks/strike-counter.sh` | Resolve the plugin root via the installed-plugins manifest: `jq -r '.plugins["praxis@praxis"][0].installPath // empty' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json"`, export it as `CLAUDE_PLUGIN_ROOT`, and re-run; if still unresolved (manifest missing or no praxis entry), report the failure verbatim and stop — do not guess a strike count |

## Non-goals

- Do not interpret or rank the violations.
- Do not suggest fixes here — if the user wants to clear, they will call `/praxis:reset-strikes`.
