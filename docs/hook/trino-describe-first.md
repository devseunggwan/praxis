# PreToolUse Trino MCP DESCRIBE-First Gate

Supported hosts: all

`hooks/trino-describe-first.py` is a paired PreToolUse + PostToolUse hook
that gates Trino MCP query execution on a session-recorded `DESCRIBE`
having run for every referenced table.

### Why this exists

Across multiple sessions, Trino MCP queries failed repeatedly with
`COLUMN_NOT_FOUND` / `TYPE_MISMATCH` because column names were guessed
from naming convention rather than verified. Two memory rules
(`loaded_not_retrieved`, `external_library_attribute_grep_first`) were
loaded into context but did not prevent the pattern — the habitual
inductive guess takes priority over the explicit verification step. A
hook moves the gate from "Claude tries to remember" to "Claude is
structurally nudged at query construction time".

Reference: issue [#182](https://github.com/devseunggwan/praxis/issues/182).

### Behavior

| Event | Action |
|-------|--------|
| `PreToolUse` on Trino MCP query | Parse the query, extract `FROM` / `JOIN` table identifiers, check each against the session history file. If any table is missing → warn (default) or deny (opt-in). |
| `PostToolUse` on Trino MCP query | If the just-executed query was `DESCRIBE <table>` or `SHOW COLUMNS FROM <table>`, record the table in the history file so subsequent queries pass. |
| Non-Trino MCP tool | Silent pass-through (matcher-scoped). |
| Malformed JSON / parse failure | Silent fail-open. |

### Configuration

| Env var | Default | Effect |
|---------|---------|--------|
| `PRAXIS_DESCRIBE_FIRST_MODE` | `warn` | `block` makes the gate emit `permissionDecision: "deny"` instead of a stderr warning. |
| `PRAXIS_TRINO_TOOL_PATTERN` | `^mcp__.*trino.*__(trino_)?query$` | Regex matched against `tool_name`. Override to widen / narrow the gate. |
| `PRAXIS_TRINO_QUERY_ARG` | `query` | Field name inside `tool_input` that carries the SQL text. |
| `PRAXIS_DESCRIBE_HISTORY_FILE` | (auto-resolved) | Explicit path override. Used by tests for isolation; can also pin a known location for long-running sessions. |

### Session state file resolution

PreToolUse / PostToolUse hooks run as independent processes — no shared
in-memory state. The history must be persisted to disk. Resolution
order:

1. `PRAXIS_DESCRIBE_HISTORY_FILE` env var (explicit override; used by
   tests for isolation).
2. `session_id` from the hook payload →
   `${TMPDIR:-/tmp}/praxis-describe-history-<session_id>.json`. This is
   the canonical praxis hook session key — same field consumed by
   `completion-verify.sh`, `retrospect-mix-check.sh`, and
   `strike-counter.sh`. Primary path: stable across PreToolUse /
   PostToolUse invocations within a single Claude Code session.
3. `${TMPDIR:-/tmp}/praxis-describe-history-${PPID}.json` — last-resort
   back-compat fallback when the payload does not carry a `session_id`
   (e.g., direct CLI / test invocation). PPID is the hook process's
   parent.

The `$CLAUDE_PROJECT_DIR/.praxis/describe-history.json` branch was
intentionally removed (codex R2 P2 on PR #189): project-rooted state
persists across Claude Code sessions in the same workspace and would
silently satisfy a later session's gate with a DESCRIBE recorded by an
earlier session — breaking the "in this session" contract. Same
architectural fix as `session-intent.py` PR #190 R1.

Read failures (missing file, malformed JSON) → empty history,
fail-open. Write failures → silently skip recording.

### Alias / CTE / subquery handling

- `FROM <name>` and `JOIN <name>` extraction matches bare, qualified
  (`schema.table`), and fully-qualified (`catalog.schema.table`)
  identifiers; trailing aliases are dropped (`FROM tbl t` → `tbl`).
- `WITH name AS (...)` CTE headers (including chained `, name AS (...)`)
  are detected via paren-balanced walk. CTE names are treated as known
  aliases (not checked against history). CTE *bodies* remain inline so
  tables referenced inside them are still validated.
- SQL comments (`-- line`, `/* block */`) are stripped before extraction.
- `DESCRIBE` / `SHOW COLUMNS (FROM|IN) <table>` queries are not subject
  to the gate (they are the verification commands themselves).

### Multi-engine

v1 ships Trino as the only seed engine. The engine label is
parameterized in the history file (`{"described": {"trino": [...]}}`),
so BigQuery / Snowflake / ClickHouse extensions can co-exist without
schema churn. To add a new engine: register a new PreToolUse +
PostToolUse matcher in `hooks.json` and update the `engine_for_tool()`
mapping in `trino-describe-first.py`.

### Failed DESCRIBE handling

PostToolUse inspects the `tool_response` field before recording a
DESCRIBE / SHOW COLUMNS as verified. When the response carries
`isError: true` (Trino reported `TABLE_NOT_FOUND` / `SCHEMA_NOT_FOUND` /
etc.), the table is NOT recorded — a subsequent query against that
table still trips the gate. Payloads without a `tool_response` field
(older event shape, non-MCP source) fall back to recording for
back-compat.

### Known limitations

- Dynamic SQL via `EXECUTE PREPARE <name> USING ...` is not parsed (the
  hook only sees the prepared statement reference, not the underlying
  SQL). Falls back to fail-open.
- Parsing is regex-based, not a full SQL parser. Pathological inputs
  (mismatched parens inside string literals, `WITH` keyword inside a
  comment that was already stripped) fall back to fail-open.

### Tests

```bash
bash tests/test_trino_describe_first.sh
```

Covers 30 cases across the warn / silent / deny dimensions: undescribed
table → warn, FROM/JOIN with aliases → base-name extraction, CTE outer
reference (no warn on CTE alias) but CTE body refs still checked,
DESCRIBE/SHOW COLUMNS queries silent, non-Trino tool silent,
post-then-pre flow (DESCRIBE recorded → subsequent query silent),
qualified `catalog.schema.table` round-trip, SQL comment stripping,
custom tool pattern via env, subquery FROM, block-mode deny, mixed
described/undescribed JOIN, failed DESCRIBE (isError) not recorded,
back-compat for payloads lacking `tool_response`, TVF skip (`UNNEST`,
`JSON_TABLE`), CTE column-list alias (`WITH foo(x, y) AS (...)`).
