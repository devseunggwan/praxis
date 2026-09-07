# bypass-telemetry

Observe-only PostToolUse hook that logs bypass-env usage to a local JSONL file.
Implements issue #441 Phase 1.

## Log location

Daily rotation (UTC date):

```
~/.praxis/telemetry/bypass-events-YYYY-MM-DD.jsonl
```

`~/.praxis` is the `PRAXIS_HOME`-relocated root shared with every other
runtime file praxis writes (see `PRIVACY.md`), so with `PRAXIS_HOME` set the
file lives at `$PRAXIS_HOME/telemetry/bypass-events-YYYY-MM-DD.jsonl`; the
`fire-events-*` family and `bypass-review` follow the same rule (issue #1340).
A hook run from a development checkout writes to
`<checkout>/.praxis-dev-telemetry/` instead, whatever `PRAXIS_HOME` says
(issue #934). Directories are created on first write.  The file is
append-only.

On the first write of a new UTC day, files older than
`PRAXIS_TELEMETRY_RETENTION_DAYS` (default 30) are removed and a detached
child gzips every earlier day's file to `bypass-events-YYYY-MM-DD.<token>.jsonl.gz`
(the token keeps a late writer's re-created plain file from ever being appended
into a finished archive). `bypass-review` reads the plain and the compressed
forms of a day together. The same rollover covers the `fire-events-*` family
(issues #1078, #1238).

## Log format

One JSON object per line.  Example:

```json
{"timestamp": "2026-05-27T12:34:56.789012+00:00", "session_id": "sess-abc123", "tool": "Bash", "bypass_env_vars": ["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"], "tool_input": "CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 git commit -m 'fix: foo'", "tool_result_status": "ok"}
```

| Field                | Description                                               |
| -------------------- | --------------------------------------------------------- |
| `timestamp`          | UTC ISO-8601, microsecond precision                       |
| `session_id`         | Claude Code session ID from hook payload                  |
| `tool`               | Tool name (always `"Bash"` in Phase 1)                    |
| `bypass_env_vars`    | Sorted list of bypass var **names** (values never stored) |
| `tool_input`         | First 200 chars of the Bash command                       |
| `tool_result_status` | `"ok"` or `"error"`                                       |

## Env knobs

| Variable                          | Default            | Effect                                                                     |
| --------------------------------- | ------------------ | -------------------------------------------------------------------------- |
| `PRAXIS_BYPASS_TELEMETRY_DISABLE` | unset              | `1` = hook is a no-op for the session                                      |
| `PRAXIS_BYPASS_TELEMETRY_FILE`    | (daily path above) | Override the full target path — useful for tests or custom log aggregation |
| `PRAXIS_HOME`                     | `~/.praxis`        | Relocate the whole runtime tree; the telemetry directory moves with it     |

## Detected bypass var families

Both naming conventions used in praxis are detected:

- `CLAUDE_HOOK_BYPASS_*` — e.g. `CLAUDE_HOOK_BYPASS_SCIOMC_GATE`,
  `CLAUDE_HOOK_BYPASS_DUP_GATE`, `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE`
- `PRAXIS_*BYPASS*` — e.g. `PRAXIS_MOMENTUM_BYPASS`, `PRAXIS_GH_JSON_BYPASS`,
  `PRAXIS_HOOK_BYPASS_WORKTREE_GATE`, `PRAXIS_HOOK_BYPASS_HUB_ENFORCE`,
  `PRAXIS_VERSION_BUMP_BYPASS`

Detection regex: `^(?:CLAUDE_HOOK_|PRAXIS_).*BYPASS`

A var is only recorded if its value is truthy: non-empty and not one of the
shell-falsy literals `0` / `false` / `no` / `off` (case-insensitive).

## Privacy guarantees

- Bypass **values** are never stored — only names.
- `tool_input` is truncated to ≤200 characters.
- `tool_response` content (stdout/stderr) is never stored — only the
  derived `"ok"` / `"error"` status.

## Phase 2 — Review CLI (`bypass-review`)

Implemented in issue #456.  The `bypass-review` binary reads the local JSONL
files and aggregates them for human review.

### Installation

```bash
./scripts/install.sh
```

This symlinks `~/.local/bin/bypass-review` → `skills/bypass-review/bypass-review`.

### Usage

```
bypass-review [OPTIONS]

Options:
  -d, --days N      Query the last N days (default: 7)
  --dir PATH        Override the telemetry directory
                    (default: $PRAXIS_HOME/telemetry, or
                    ~/.praxis/telemetry when PRAXIS_HOME is unset)
  --errors-only     Show only events where tool_result_status == "error"
  -h, --help        Show help and exit
```

### Output

The report has seven sections:

1. **Summary** — total events, date window, error count.
2. **Top bypass vars** — frequency table; vars with error events are flagged
   `⚠ bad-bypass candidate` (bypass followed by a tool failure).
3. **Bypass by hook / rule family** — normalized labels derived from the bypass
   var names (for example `SCIOMC_GATE`, `WORKTREE_GATE`, `GH_JSON`) with
   per-family error counts.
4. **Bypass by command family** — first executable token after inline env
   prefixes, with path-like commands collapsed to `path:<basename>`.
5. **Error-linked bypass signals** — separate summary for vars / hook families /
   command groups that still ended in a tool error.
6. **Bypass by tool** — group by `tool` field.
7. **Error events** — per-event detail for `tool_result_status == "error"`,
   including the normalized family and command-group labels.

Use `--errors-only` to print just the error-event section.

### Privacy

The CLI is read-only and only aggregates what is already in the JSONL.
Bypass var **values** were never stored by the hook; they do not appear in
any output. `tool_input` remains truncated/redacted by the writer, and the
derived command-family view never prints a full path — path-like commands are
collapsed to `path:<basename>`.

### Example output

```
────────────────────────────────────────────────────────────
bypass-review: Bypass Telemetry Report
────────────────────────────────────────────────────────────
  Period : 2026-05-21 → 2026-05-27 (last 7 days)
  Source : ~/.praxis/telemetry
  Total events : 6
  Error events : 1

────────────────────────────────────────────────────────────
Top Bypass Vars (most bypassed rules)
────────────────────────────────────────────────────────────
  Var name                                            Count  Errors  Note
  ──────────────────────────────────────────────────  ─────  ──────  ──────────────────────
  CLAUDE_HOOK_BYPASS_SCIOMC_GATE                          4       1  ⚠ bad-bypass candidate
  CLAUDE_HOOK_BYPASS_DUP_GATE                             1       0
  PRAXIS_MOMENTUM_BYPASS                                  1       0
  CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE                    1       0

  Note: high count = rule may be too strict; Errors = bypass followed by tool failure.
```

## Deferred

- **Phase 3** — Optional HTTP forwarding to a central collector for
  cross-session analytics.
