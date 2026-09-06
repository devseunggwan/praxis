# Strike Counter (SessionStart + UserPromptSubmit + Stop)

Supported hosts: all

`hooks/strike-counter.sh` implements praxis's session-scoped three-strike
discipline. A single bash script dispatches across multiple modes — three
of them are registered in `hooks/manifest.json`, the rest are exposed to the
user as slash commands.

```
hooks.json registrations
  SessionStart        → strike-counter.sh session-start
  UserPromptSubmit    → strike-counter.sh preprompt
  Stop                → strike-counter.sh stop

slash commands (via skills/{strike,strikes,reset-strikes})
  strike-counter.sh strike <reason>
  strike-counter.sh status
  strike-counter.sh reset
```

### Why this exists

Workflow-rule violations are the load-bearing signal that the agent has
drifted off-spec. Counting them per session — with escalating
consequences — converts a soft norm into a structural pressure: at 1 the
agent receives a warning, at 2 a forced re-read of the rule that was broken, at 3 the Stop hook hard-blocks the response until the operator
reviews and resets the counter. The reset is itself gated by a reflection
file at count=3 so the trust restoration is a deliberate two-step act,
not a free retry.

### What this enforces

| Mode | Trigger | Behavior |
| ------ | --------- | ---------- |
| `session-start` | SessionStart hook | Reads `session_id` from stdin JSON. Writes `CLAUDE_SESSION_ID=<sid>` to `$CLAUDE_ENV_FILE` (primary) and `<STATE_DIR>/.current-session` latch (backstop). If a prior strike count > 0 exists for this session, emits `additionalContext` so Claude sees the carried state. |
| `preprompt` | UserPromptSubmit hook | Loads count. At count=1 emits a strike-1 warning context; at count=2 emits a strike-2 review-required context with cumulative reason list and a forced rule re-read directive; at count≥3 stays silent (Stop hook handles the block). |
| `stop` | Stop hook | Honors `stop_hook_active=true` to avoid infinite loop. If count≥3, appends to `<STATE_DIR>/last-block.log` and emits `{"decision":"block","reason":"…"}` with the reflection requirement. |
| `strike <reason>` | `/praxis:strike` skill | Increments count, appends reason, prints level-appropriate message (warning / review-required / blocked). Detects unexpected count values (0, non-numeric) as state corruption and surfaces a recovery message rather than mis-announcing the level. |
| `status` | `/praxis:strikes` skill | Prints `Strikes: N/3` and the cumulative reason list. |
| `reset` | `/praxis:reset-strikes` skill | Clears state for the current session. At count≥3, refuses unless `<STATE_DIR>/<sid>.reflection.md` exists and is non-empty (`[ -s file ]`, PR #105). On gated reset, removes the reflection file too so the next cycle cannot reuse a stale doc. |

### State layout

Durable state lives under the host-neutral `~/.praxis/state` by default (#527);
`PRAXIS_STATE_DIR` still overrides the base (back-compat). On first run after the
move, if no override is set and the new location is absent, the pre-#527
`~/.claude/state/praxis/strikes` contents are migrated across once so existing
counters/latches survive.

```
$STATE_DIR = ${PRAXIS_STATE_DIR:-${PRAXIS_HOME:-$HOME/.praxis}/state}/strikes
  <sid>.json              # {"count": N, "reasons": ["...","..."]}
  <sid>.reflection.md     # user-authored reflection (required at count≥3 reset)
  .current-session        # latch — last session_id seen by session-start
  last-block.log          # append-only audit trail of strike-3 blocks
```

The state directory is praxis-owned (not `$CLAUDE_PLUGIN_DATA`, which
points at whichever plugin won the matchcheck in the current scope and
could silently leak praxis state into a sibling plugin's directory —
issue #126). A TTL sweep on entry drops `.json` and `.reflection.md`
files older than 7 days.

### Response shapes

`session-start` (count > 0):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "Praxis strikes carried from prior activity this session: 1/3"
  }
}
```

`preprompt` (count = 1 or 2):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "⚠️ Praxis strike 1/3 — warning. Recorded violation:\n  1. <reason>\nStay extra careful with the rules this session."
  }
}
```

`stop` (count ≥ 3):

```json
{
  "decision": "block",
  "reason": "🔴 Praxis strike 3/3 — response blocked. Violations this session:\n  1. ...\n  2. ...\n  3. ...\n\nRecovery is a two-step trust process — write, then persuade.\n..."
}
```

All hook modes exit `0` regardless of decision — the `decision` JSON
field is the only block signal, never the exit code (per Claude Code Stop
hook semantics).

### Fail-safe posture

The script intentionally runs without `set -euo pipefail`. Every external
call is guarded with `|| true` / `2>/dev/null` / a conditional check so
that a missing `jq`, an unreadable state file, a permission error on the
state directory, or a corrupt JSON state cannot break the Claude Code
session. The hook would rather report nothing than crash mid-prompt.

- `jq` is required; if missing, the hook prints a one-line install hint
  and exits 0 (Claude Code keeps booting).
- A corrupt or non-numeric count is routed through a distinct
  "state file corrupt or write failed" branch so the agent is not told
  "strike 3 — blocked" when the underlying state did not actually advance
  (the Stop hook only blocks at count ≥ 3).
- The `strike` mode writes to a temp file in the same directory and
  `mv`s into place so the write is atomic (`rename(2)`).

### Tests

`tests/hooks/completion-verify/test_strike_counter.sh` covers ~25 acceptance cases, named
`test_ac{N}_{description}` — first/second/third strike level messages,
Stop hook block at 3 and silence under 3, `stop_hook_active`
short-circuit, reset clears state, preprompt context contents,
SessionStart latch + `CLAUDE_ENV_FILE` export, latch fallback, missing
session_id graceful skip, status header, `hooks.json` validity, missing
`jq` guidance, slash-command skill file existence, corrupt state
detection, reflection-gate refuse / accept paths. Run before editing
the hook:

```bash
tests/hooks/completion-verify/test_strike_counter.sh
```
