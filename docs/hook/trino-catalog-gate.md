# PreToolUse Trino MCP Catalog Gate

Supported hosts: all

`hooks/trino-catalog-gate.py` is a PreToolUse hook that blocks Trino MCP
queries with 3-part catalog references (`<catalog>.<schema>.<table>`) when
the session has not run a `SHOW CATALOGS` query.

### Why this exists

Across multiple sessions, Trino MCP queries failed with `Catalog '<name>' not
found` because catalog names were guessed from naming convention (`mysql`,
`hive`, `postgres`) rather than verified via `SHOW CATALOGS`. Two memory
rules ("enumerate SHOW CATALOGS before assuming catalog name") were loaded into
context but did not prevent the pattern — the habitual inductive guess takes
priority over the explicit verification step. By the 3-generation threshold
defined in this repo's prompt-layer retrieval failure rule, the next mitigation
tier is a mechanical PreToolUse gate.

A hook moves the gate from "Claude tries to remember" to "Claude is structurally
blocked at query construction time".

Reference: issue [#321](https://github.com/devseunggwan/praxis/issues/321).

### Behavior

| Event | Action |
|-------|--------|
| `PreToolUse` on Trino MCP query with `SHOW CATALOGS` | Pass through + create session marker |
| `PreToolUse` on Trino MCP query with 3-part reference, no marker | Deny with guidance |
| `PreToolUse` on Trino MCP query with 3-part reference, marker present | Pass through |
| `PreToolUse` on Trino MCP query without 3-part references | Pass through |
| `-- catalog-enumerated: verified` in query body | Pass through (inline bypass) |
| `PRAXIS_TRINO_CATALOG_GATE=skip` set | Pass through (env bypass) |
| Non-Trino MCP tool | Silent pass-through (matcher-scoped) |
| Malformed JSON / parse failure | Silent fail-open |

### Configuration

| Env var | Default | Effect |
|---------|---------|--------|
| `PRAXIS_TRINO_CATALOG_GATE` | (unset) | Set to `skip` to bypass the gate for one invocation |
| `PRAXIS_TRINO_TOOL_PATTERN` | `^mcp__.*trino.*__(trino_)?query$` | Regex matched against `tool_name`. Override to widen / narrow the gate. |
| `PRAXIS_TRINO_QUERY_ARG` | `query` | Field name inside `tool_input` that carries the SQL text. |
| `PRAXIS_TRINO_CATALOG_GATE_FILE` | (auto-resolved) | Explicit path override for the session marker file. Used by tests for isolation. |

### Session state file resolution

PreToolUse hooks run as independent processes — no shared in-memory state. The
SHOW CATALOGS enumeration marker is persisted to disk. Resolution order
(mirrors `trino-describe-first.py`):

1. `PRAXIS_TRINO_CATALOG_GATE_FILE` env var (explicit override; used by tests
   for isolation).
2. `session_id` from the hook payload →
   `${TMPDIR:-/tmp}/praxis-catalog-gate-<session_id>.enumerated`. This is
   the canonical praxis hook session key — same field consumed by
   `completion-verify.sh`, `retrospect-mix-check.sh`, and
   `strike-counter.sh`.
3. `${TMPDIR:-/tmp}/praxis-catalog-gate-${PPID}.enumerated` — last-resort
   back-compat fallback when the payload does not carry a `session_id`
   (e.g., direct CLI / test invocation). PPID is the hook process's parent.

### 3-part catalog reference detection

The hook detects `FROM <catalog>.<schema>.<table>` and `JOIN <catalog>.<schema>.<table>`
patterns using a regex with a SQL context gate (requires `FROM` or `JOIN`
keyword before the identifier). This avoids false positives on Python attribute
chains like `os.path.sep`.

SQL comments (`-- line`, `/* block */`) are stripped before detection to prevent
`-- catalog.schema.table` false positives.

Quoted identifier forms are recognized:

| Form | Example |
|------|---------|
| Bare | `iceberg.foo.bar` |
| ANSI double-quoted | `"iceberg"."foo"."bar"` |
| MySQL / Hive backticked | `` `iceberg`.`foo`.`bar` `` |

### Bypass paths

Two bypass paths are available when the catalog is already known-good:

1. **Inline marker** — append `-- catalog-enumerated: verified` anywhere in
   the SQL body. The gate checks the raw query text before comment stripping.
2. **Env bypass** — set `PRAXIS_TRINO_CATALOG_GATE=skip`. One-off session
   override; does not persist across invocations.

Running `SHOW CATALOGS` is the canonical path. The gate auto-passes on all
subsequent queries in the same session once the marker file exists.

### Fail-open contract

| Condition | Behavior |
|-----------|----------|
| Malformed / missing stdin JSON | exit 0 (silent pass) |
| `tool_name` not matching Trino pattern | exit 0 (silent pass) |
| Missing or empty `query` field | exit 0 (silent pass) |
| `is_show_catalogs()` parse exception | exit 0 (silent pass) |
| `extract_catalogs()` parse exception | exit 0 (silent pass) |
| Marker file write failure | Silently skipped; gate may block next time |
| `python3` unavailable | exit 0 (shell shim guards) |
| Hook `.py` file missing | exit 0 (shell shim guards) |

### Known limitations

- `INSERT INTO <catalog>.<schema>.<table>`, `MERGE INTO`, and `CREATE TABLE AS
  SELECT` DML catalog references are not detected. Extension candidates for v2.
- Detection is regex-based, not a full SQL parser. Pathological inputs
  (mismatched parens inside string literals, quoted identifiers containing
  `.`) fall back to fail-open.

### Tests

```bash
bash tests/test_trino_catalog_gate.sh
```

Covers 6 cases from the issue verification matrix:

- (a) `SHOW CATALOGS` passes + creates marker
- (b) 3-part reference without marker → blocked with guidance
- (c) 3-part reference + `-- catalog-enumerated: verified` inline → passes
- (d) `SHOW SCHEMAS FROM iceberg` (2-part, not 3-part) → passes
- (e) `PRAXIS_TRINO_CATALOG_GATE=skip` → bypasses
- (f) Non-Trino MCP tool → passthrough
