# bypass-telemetry

Observe-only PostToolUse hook that logs bypass-env usage to a local JSONL file.
Implements issue #441 Phase 1.

## Log location

Daily rotation (UTC date):

```
~/.praxis/telemetry/bypass-events-YYYY-MM-DD.jsonl
```

Directories are created on first write.  The file is append-only.

## Log format

One JSON object per line.  Example:

```json
{"timestamp": "2026-05-27T12:34:56.789012+00:00", "session_id": "sess-abc123", "tool": "Bash", "bypass_env_vars": ["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"], "tool_input": "CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 git commit -m 'fix: foo'", "tool_result_status": "ok"}
```

| Field | Description |
|-------|-------------|
| `timestamp` | UTC ISO-8601, microsecond precision |
| `session_id` | Claude Code session ID from hook payload |
| `tool` | Tool name (always `"Bash"` in Phase 1) |
| `bypass_env_vars` | Sorted list of bypass var **names** (values never stored) |
| `tool_input` | First 200 chars of the Bash command |
| `tool_result_status` | `"ok"` or `"error"` |

## Env knobs

| Variable | Default | Effect |
|----------|---------|--------|
| `PRAXIS_BYPASS_TELEMETRY_DISABLE` | unset | `1` = hook is a no-op for the session |
| `PRAXIS_BYPASS_TELEMETRY_FILE` | (daily path above) | Override the full target path — useful for tests or custom log aggregation |

## Detected bypass var families

Both naming conventions used in praxis are detected:

- `CLAUDE_HOOK_BYPASS_*` — e.g. `CLAUDE_HOOK_BYPASS_SCIOMC_GATE`,
  `CLAUDE_HOOK_BYPASS_DUP_GATE`, `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE`
- `PRAXIS_*BYPASS*` — e.g. `PRAXIS_MOMENTUM_BYPASS`, `PRAXIS_GH_JSON_BYPASS`,
  `PRAXIS_HOOK_BYPASS_WORKTREE_GATE`, `PRAXIS_HOOK_BYPASS_HUB_ENFORCE`,
  `PRAXIS_VERSION_BUMP_BYPASS`

Detection regex: `^(?:CLAUDE_HOOK_|PRAXIS_).*BYPASS`

A var is only recorded if its value is truthy (non-empty and not `"0"`).

## Privacy guarantees

- Bypass **values** are never stored — only names.
- `tool_input` is truncated to ≤200 characters.
- `tool_response` content (stdout/stderr) is never stored — only the
  derived `"ok"` / `"error"` status.

## Deferred phases

- **Phase 2** — `praxis bypass-telemetry review` CLI to aggregate the local
  JSONL log and surface top bypass patterns by session/var.
- **Phase 3** — Optional HTTP forwarding to a central collector for
  cross-session analytics.
