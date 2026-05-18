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

The hook detects 3-part `<catalog>.<schema>.<table>` references following any
of these SQL context keywords:

| Keyword arm | Covers |
|-------------|--------|
| `FROM` | `SELECT ... FROM`, `DELETE FROM` |
| `JOIN` | all JOIN variants |
| `INTO` | `INSERT INTO`, `MERGE INTO` — **not** bare `SELECT col INTO dest` (SELECT-INTO is not valid Trino SQL; see "Known limitations" below) |
| `UPDATE` | `UPDATE <ref> SET ...` |
| `TABLE` | `CREATE TABLE`, `CREATE OR REPLACE TABLE`, `ALTER TABLE` |
| `VIEW` | `CREATE VIEW`, `CREATE OR REPLACE VIEW` |
| `USING` | `MERGE INTO t USING <catalog>.<schema>.<table>` — source table in MERGE statements (#336 item 7) |
| `LIKE` | `CREATE TABLE x (LIKE <catalog>.<schema>.<table>)` — LIKE clause reference (#336 item 8) |

The context keyword gate avoids false positives on Python attribute chains like
`os.path.sep`.

**`INTO` arm — SELECT-INTO exclusion**: The `INTO` arm is restricted to
`INSERT INTO` and `MERGE INTO` contexts. `SELECT col INTO cat.s.t FROM src`
(PostgreSQL/SQL-Server SELECT-INTO syntax) does NOT trigger the gate because
Trino does not support SELECT-INTO; blocking it would produce a misleading
deny message pointing to a catalog-enumeration problem rather than an
unsupported-syntax error. A separate `MERGE_INSERT_INTO_RE` pattern captures
only the `(INSERT|MERGE) INTO` form (#336 item 6).

**`SHOW SCHEMAS FROM <catalog>` and catalog-existence verification**: A
`SHOW SCHEMAS FROM iceberg` query does NOT count as catalog-existence
verification for the gate. Rationale: `SHOW SCHEMAS FROM <catalog>` itself
references the catalog name inline; if the catalog does not exist, Trino
returns `Catalog '<name>' not found`. The gate marker is created only by
`SHOW CATALOGS` (the query that *enumerates available catalogs*), not by
commands that *assume* a catalog exists. Use `SHOW CATALOGS` first, then
`SHOW SCHEMAS FROM <catalog>` to list schemas in a verified catalog (#336
item 2).

String literals are masked before comment and catalog-reference detection so
that `--` or `/* */` sequences embedded inside string values are not treated
as SQL comment delimiters. SQL comments are then stripped so that
`-- catalog.schema.table` does NOT trigger the gate.

Quoted identifier forms are recognized:

| Form | Example |
|------|---------|
| Bare | `iceberg.foo.bar` |
| ANSI double-quoted | `"iceberg"."foo"."bar"` |
| MySQL / Hive backticked | `` `iceberg`.`foo`.`bar` `` |

### Bypass paths

Two bypass paths are available when the catalog is already known-good:

1. **Inline marker** — place `catalog-enumerated: verified` inside a SQL
   comment in the query body:
   - Line comment: `SELECT ... -- catalog-enumerated: verified`
   - Block comment: `SELECT ... /* catalog-enumerated: verified */`
   The marker must appear in comment syntax. Single-quoted string literals
   are masked before comment extraction, so
   `WHERE x = 'foo -- catalog-enumerated: verified'` is NOT a bypass.
   See "Known limitations" for double-quoted / backtick identifier edge cases.
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

### Known limitations — false negatives (gate silently passes, should block)

The following SQL shapes may produce `CATALOG_NOT_FOUND` but are not yet
detected by this hook (will silently pass through):

- **Double-quoted / backtick bypass** — marker text inside a double-quoted
  (`"-- catalog-enumerated: verified"`) or backtick
  (`` `-- catalog-enumerated: verified` ``) identifier is NOT masked, so the
  `--` sequence is treated as a line-comment delimiter and the gate is bypassed
  unintentionally (tracked in #336 P-redesign).
- Subquery `FROM` clauses where only inner queries reference 3-part catalogs
  but the outer query does not.
- Dynamic SQL via `EXECUTE PREPARE <name> USING ...` (prepared statement
  reference, not the underlying SQL text).

> **Resolved in #336**: `MERGE ... USING <catalog>.<schema>.<table>` (#336 item 7)
> and `CREATE TABLE x (LIKE <catalog>.<schema>.<table>)` (#336 item 8) are now
> detected. `SELECT col INTO <catalog>.<schema>.<table>` (SELECT-INTO) now passes
> correctly with a non-misleading path (#336 item 6).

### Known limitations — false positives (gate blocks, should pass)

- **Paired apostrophes in a line comment** — a line comment containing the
  marker text wrapped in single quotes
  (`-- 'catalog-enumerated: verified'`) causes the apostrophe pair to be
  consumed by the string-literal mask, stripping the marker from the comment.
  The bypass is not recognized and the gate incorrectly blocks.
  **Workaround**: use a block comment (`/* catalog-enumerated: verified */`)
  or the env bypass `PRAXIS_TRINO_CATALOG_GATE=skip` (tracked in #336 P-redesign).

Detection is regex-based, not a full SQL lexer. A proper stateful lexer
(tracking string / comment / identifier state in a single pass) would resolve
all the above delimiter-interaction limitations. See #336 P-redesign.

### Tests

```bash
bash tests/test_trino_catalog_gate.sh
```

Covers cases across M1 (bypass comment-only), M2 (DML/DDL keyword set),
M3 (multi-statement isolation), C1 (string-literal masking), and #336 items:

- (a) `SHOW CATALOGS` passes + creates marker; subsequent 3-part query passes
- (b) 3-part `FROM`/`JOIN` reference without marker → deny
- (c) Inline bypass: line comment and block comment → passes; string literal → deny
- (d) 2-part references (`schema.table`) → passes
- (e) `PRAXIS_TRINO_CATALOG_GATE=skip` → bypasses
- (f) Non-Trino MCP tool → passthrough
- M2: `INSERT INTO`, `MERGE INTO`, `UPDATE`, `CREATE TABLE`, `CREATE VIEW`, `DELETE FROM` → deny
- M3: `SHOW CATALOGS; SELECT ... FROM 3-part` in one payload → deny (no prior marker)
- C1: string literal containing `-- marker text` → deny (literal masking prevents bypass)
- Item 1: PostToolUse `isError=true` → deletes optimistic marker; `isError=false` / missing field → preserves marker
- Item 3: marker file created with `0o600` permissions
- Item 6: `SELECT col INTO cat.s.t` → pass (not INSERT/MERGE INTO); INSERT/MERGE INTO regression guards
- Item 7: `MERGE INTO t USING iceberg.foo.src` → deny; 2-part USING → pass
- Item 8: `CREATE TABLE x (LIKE iceberg.foo.src)` → deny; 2-part LIKE → pass
- Item 9: doc-contract test — keyword arm set in code equals keyword arm table in spec (drift = test failure)
