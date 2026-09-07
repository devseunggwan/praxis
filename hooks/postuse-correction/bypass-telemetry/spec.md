# bypass-telemetry

Supported hosts: all

**Phase 1** — observe-only PostToolUse(Bash) hook that logs bypass-env
usage to a local JSONL file.  Implements issue #441 Phase 1.
Phase 2 (review CLI) and Phase 3 (HTTP forwarding) are **deferred**.

## Why this exists

Praxis hooks that can be escaped by setting a `CLAUDE_HOOK_BYPASS_*` or
`PRAXIS_*BYPASS*` env var have no visibility into how often those bypasses
are used.  Without a record, recurrent bypass patterns can't be detected or
challenged.

This hook writes a single JSONL line each time a Bash tool call runs with
an active bypass var — creating an auditable local log for future review.
The target has to be a regular file: a FIFO, device or symlink is refused
with the fire ledger's own guard (`_fire_ledger._atomic_append`), so a
misconfigured `PRAXIS_BYPASS_TELEMETRY_FILE` cannot stall the
`PostToolUse(Bash)` dispatch group this hook opens.

## Behavior

| Condition                                     | Action                          |
| --------------------------------------------- | ------------------------------- |
| Bypass env var(s) detected (truthy)           | Append one JSONL record; exit 0 |
| No bypass env detected                        | No output, no write; exit 0     |
| Opt-out (`PRAXIS_BYPASS_TELEMETRY_DISABLE=1`) | No-op; exit 0                   |
| Malformed JSON stdin                          | Silent fail-open; exit 0        |
| Unwritable telemetry dir/file                 | Silent fail-open; exit 0        |

**This hook NEVER blocks.  It always exits 0.**  It is registered as a
`PostToolUse` hook and cannot influence tool execution regardless.

## Bypass var detection

Two naming families are detected (both required):

| Family          | Pattern                                          | Examples                                                                                                                                              |
| --------------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CLAUDE_HOOK_*` | starts with `CLAUDE_HOOK_` AND contains `BYPASS` | `CLAUDE_HOOK_BYPASS_DUP_GATE`, `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE`                                                                                 |
| `PRAXIS_*`      | starts with `PRAXIS_` AND contains `BYPASS`      | `PRAXIS_MOMENTUM_BYPASS`, `PRAXIS_GH_JSON_BYPASS`, `PRAXIS_HOOK_BYPASS_WORKTREE_GATE`, `PRAXIS_HOOK_BYPASS_HUB_ENFORCE`, `PRAXIS_VERSION_BUMP_BYPASS` |

Detection regex: `^(?:CLAUDE_HOOK_|PRAXIS_).*BYPASS`

**Truthiness rule**: a var is only counted if its value is non-empty and not
one of the conventional shell-falsy literals `0` / `false` / `no` / `off`
(matched case-insensitively).  `VAR=0`, `VAR=false`, `VAR=no`, `VAR=off` and
`VAR=` are all treated as inactive.

**Detection sources** (results are unioned):

1. **Inline leading env parse** — `tool_input.command` is scanned for
   `NAME=value` assignments at the start of the Bash command string.
   This is the reliable path for the common `CLAUDE_HOOK_BYPASS_X=1 git ...`
   inline-prefix form.

2. **`os.environ` scan** — the hook process environment is scanned for
   all names matching the bypass pattern.  Claude Code propagates
   inline-prefix env vars into the hook process; this serves as a backstop
   for cases where the inline parse misses whitespace or quoting variants.

### Non-matching examples

| Name                      | Matches? | Reason                      |
| ------------------------- | -------- | --------------------------- |
| `PRAXIS_MD_ESCAPE_SKIP`   | No       | no `BYPASS` substring       |
| `PRAXIS_ASK_END_ADVISORY` | No       | no `BYPASS` substring       |
| `CLAUDE_PROJECT_DIR`      | No       | no `BYPASS` substring       |
| `HOME`                    | No       | neither prefix nor `BYPASS` |

## Log record format

One JSON object per line (JSONL), appended to the daily file.

```json
{
  "timestamp": "2026-05-27T12:34:56.789012+00:00",
  "session_id": "sess-abc123",
  "tool": "Bash",
  "bypass_env_vars": ["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"],
  "tool_input": "CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 git commit -m 'fix: foo'",
  "tool_result_status": "ok"
}
```

Field notes:

| Field                | Type         | Notes                                                                                                         |
| -------------------- | ------------ | ------------------------------------------------------------------------------------------------------------- |
| `timestamp`          | string       | UTC ISO-8601, microsecond precision                                                                           |
| `session_id`         | string       | from hook payload; empty string if absent                                                                     |
| `tool`               | string       | always `"Bash"` for Phase 1 (Bash-only matcher)                                                               |
| `bypass_env_vars`    | list[string] | sorted list of bypass var **names** — values are never stored                                                 |
| `tool_input`         | string       | first 200 chars of `tool_input.command`; never the full command                                               |
| `tool_result_status` | string       | `"ok"` or `"error"` derived from `tool_response.exit` / `tool_response.interrupted` / `tool_response.isError` |

## `tool_result_status` derivation

From the `tool_response` dict in the PostToolUse payload:

| Condition                                           | Status    |
| --------------------------------------------------- | --------- |
| `tool_response.exit == 0`                           | `"ok"`    |
| `tool_response.exit != 0`                           | `"error"` |
| `tool_response.interrupted == True`                 | `"error"` |
| `tool_response.isError == True`                     | `"error"` |
| Non-dict `tool_response` (e.g., string `"success"`) | `"ok"`    |
| None of the above                                   | `"ok"`    |

## Storage

Default path (daily rotation):

```
~/.praxis/telemetry/bypass-events-YYYY-MM-DD.jsonl
```

`~/.praxis` is `PRAXIS_HOME`-relocated (issue #1340), and a development
checkout diverts to `<checkout>/.praxis-dev-telemetry/` (issue #934) — both
via `_fire_ledger.resolve_telemetry_dir()`, which this hook shares with the
fire ledger. Directories are created on first write (`exist_ok=True`).

## Configuration

| Env var                           | Default            | Effect                                                |
| --------------------------------- | ------------------ | ----------------------------------------------------- |
| `PRAXIS_BYPASS_TELEMETRY_DISABLE` | unset              | `1` = full opt-out; hook is a no-op                   |
| `PRAXIS_BYPASS_TELEMETRY_FILE`    | (daily path above) | Override full file path — used by tests for isolation |

## Privacy

- **Bypass var values are never stored** — only the variable names appear in
  `bypass_env_vars`.
- **All leading inline env assignment values are redacted** in `tool_input`.
  A command like `CLAUDE_HOOK_BYPASS_X=1 AWS_SECRET_ACCESS_KEY=token cmd`
  is stored as `CLAUDE_HOOK_BYPASS_X=<redacted> AWS_SECRET_ACCESS_KEY=<redacted> cmd`.
  Only the variable names are preserved.
- `tool_input` is truncated to ≤200 characters.  The full command is never
  stored.
- `tool_response` content (stdout/stderr/output) is never stored — only the
  derived `"ok"` / `"error"` status.

## Deferred: Phase 2 and Phase 3

- **Phase 2** — review CLI (`praxis bypass-telemetry review`) that reads the
  local JSONL log, aggregates by bypass var, and surfaces top patterns.
- **Phase 3** — optional HTTP forwarding of bypass events to a central
  collector for cross-session analytics.

## Known limitations

- **Bash-only matcher (Phase 1)**: the hook is registered with `matcher: Bash`.
  Bypass vars set only in `os.environ` (not inline in a Bash command) would
  also be caught by the env scan for any Bash call, but bypasses that happen
  exclusively on non-Bash tools (Edit, Write, etc.) are not logged in Phase 1.
- **No deduplication across turns**: each Bash call with a bypass active
  produces one record, even if the same bypass is set for many calls in a row.
  Phase 2 review CLI will aggregate by session/var.

## Tests

```bash
bash tests/hooks/postuse-correction/test_bypass_telemetry.sh
```
