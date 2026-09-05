# PreToolUse External API Literal Use-Site Trigger

Supported hosts: all

`hooks/advisory-nudge/external-api-literal-trigger/impl.py` fires on every `PreToolUse` event
for `Write`, `Edit`, and `Bash` tool calls. It scans the content being
written or the command being issued for external API enum / literal patterns
and emits an advisory reminder to verify the value against an authoritative
source before proceeding.

### Why this exists

The *Loaded ≠ Retrieved* rule ([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)) instructs:
> Before using an external API enum / literal / catalog name / configuration
> constant, retrieve a verified source (vendor docs, `SHOW CATALOGS`,
> `--help` output) — never write a value that "looks right" based on naming
> pattern.

Three observed instances (30 days) where the rule was loaded into context
but the retrieval trigger did not fire at use-site:

1. Trino catalog name guessed (`mysql.auth.tb_*`) without prior
   `SHOW CATALOGS` enumeration → 6× `CATALOG_NOT_FOUND` retries
2. Vendor API enum values written from naming-pattern recall → 3 of 5 enum
   values rejected by vendor with `INVALID_VALUE`
3. Date-range literal (`LAST_365_DAYS` style) guessed from a related vendor
   family → `INVALID_DATE_RANGE_FORMAT`

The pattern is consistent: the rule is in context, but the conversational
flow does not re-trigger retrieval at the specific moment the value is
committed to a tool call or file edit. A structural hook moves the gate to
the use-site.

Reference: issue [#202](https://github.com/devseunggwan/praxis/issues/202).

### What is warned

| Tool | Scanned field | Pattern | Advisory emitted |
| ------ | -------------- | --------- | ----------------- |
| `Write` | `tool_input.content` | ALL_CAPS_WITH_UNDERSCORES (≥6 chars, compound) | Yes |
| `Write` | `tool_input.content` | 3-part SQL identifier in SQL context | Yes |
| `Edit` | `tool_input.new_string` | ALL_CAPS_WITH_UNDERSCORES (≥6 chars, compound) | Yes |
| `Edit` | `tool_input.new_string` | 3-part SQL identifier in SQL context | Yes |
| `Bash` | `tool_input.command` | ALL_CAPS_WITH_UNDERSCORES (≥6 chars, compound) | Yes |
| `Bash` | `tool_input.command` | 3-part SQL identifier in SQL context | Yes |
| Any other tool | — | — | Silent pass-through |
| Malformed payload / missing field | — | — | Silent fail-open |

**This hook never blocks.** It is advisory-only: exit 0 in all cases.
Up to 3 findings are reported per invocation to avoid advisory overload.

### Detected patterns

#### ALL_CAPS enum candidates

Tokens matching `\b[A-Z][A-Z0-9_]{5,}(?![A-Z0-9_])` that additionally
contain at least one underscore or digit (compound structure). Pure SQL
keywords (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `WHERE`) are excluded by
the compound-structure requirement. The right boundary is an ASCII
lookahead rather than `\b`: Python's `\b` is Unicode-aware, so a Hangul
character immediately after the token (e.g. `AUTH_TOKEN이`) would otherwise
suppress the match (issue #513).

Examples that fire: `LAST_365_DAYS`, `THIS_WEEK_OF_MONTH`,
`SHOPBY_AUTH_TOKEN`, `PAYMENT_STATUS_APPROVED`, `AUTH_TOKEN이`

#### 3-part SQL identifiers

Tokens matching `<catalog>.<schema>.<table>` that appear after a SQL
context keyword (`FROM`, `JOIN`, `TABLE`, `INTO`, `UPDATE`). The SQL
context gate prevents false positives on Python attribute chains like
`os.environ.get` or `module.sub.attribute`.

Three quoting shapes are recognized; quoted forms are normalized
(delimiters stripped) before reporting:

| Form | Example |
| ------ | --------- |
| Bare | `mysql.auth.tb_user` |
| ANSI double-quoted | `"mysql"."auth"."tb_user"` |
| MySQL / Hive backticked | `` `mysql`.`auth`.`tb_user` `` |

Examples that fire: `mysql.auth.tb_user` (in `FROM mysql.auth.tb_user`),
`hive.warehouse.orders` (in `SELECT * FROM hive.warehouse.orders`),
`"mysql"."auth"."tb_user"` (in `FROM "mysql"."auth"."tb_user"`)

### Stop-words (excluded from ALL_CAPS scan)

The following tokens are excluded to suppress noise on common source-code
constants:

| Category | Excluded tokens |
| ---------- | ---------------- |
| Code markers | `TODO`, `FIXME`, `HACK`, `NOTE`, `WARN`, `WARNING`, `DEBUG` |
| Licenses | `LICENSE`, `README`, `AUTHORS`, `CHANGELOG`, `CONTRIBUTORS`, `MIT`, `BSD`, `GNU`, `GPL`, `APACHE`, `MPL`, `AGPL` |
| Format names | `JSON`, `XML`, `CSV`, `YAML`, `TOML`, `SQL`, `HTML`, `UTF8`, `ASCII`, `UNICODE` |
| Protocol / network | `HTTP`, `HTTPS`, `FTP`, `SSH`, `TCP`, `UDP`, `URL`, `URI`, `UUID`, `GUID` |
| Generic programming | `API`, `SDK`, `CLI`, `GUI`, `EOF`, `NULL`, `NONE`, `TRUE`, `FALSE` |
| Shell / env | `PATH`, `HOME`, `USER`, `SHELL`, `TERM` |
| Python / general | `PYTHONPATH`, `VIRTUAL`, `STDERR`, `STDOUT`, `STDIN` |

### Advisory message

```
⚠ External API literal detected: `<token>` (<kind>)
   Has this value been verified against an authoritative source
   (vendor doc, SHOW CATALOGS, --help) in this session?
   If yes, proceed. If no, retrieve before write.
```

`<kind>` is one of: `ALL_CAPS enum candidate`, `3-part SQL identifier`.

### Fail-open contract

| Condition | Behavior |
| ----------- | ---------- |
| Malformed / missing stdin JSON | exit 0 (silent pass) |
| `tool_name` not in `Write`, `Edit`, `Bash` | exit 0 (silent pass) |
| Missing target field (`content` / `new_string` / `command`) | exit 0 (silent pass) |
| `python3` unavailable | exit 0 (shell shim guards) |
| Hook `.py` file missing | exit 0 (shell shim guards) |
| Any uncaught exception | exit 0 (silent pass) |

### Tests

```bash
bash tests/hooks/advisory-nudge/test_external_api_literal_trigger.sh
```

Covers (20 cases):

- Write `content`: `LAST_365_DAYS` → advisory fire
- Write `content`: `THIS_WEEK_OF_MONTH` → advisory fire
- Edit `new_string`: `mysql.auth.tb_user` in SQL context → advisory fire
- Bash `command`: `SHOPBY_AUTH_TOKEN` → advisory fire
- Write `content`: `PAYMENT_STATUS_APPROVED` → advisory fire
- Edit `new_string`: `hive.warehouse.orders` in SQL context → advisory fire
- `TODO`, `FIXME`, `README`, `LICENSE`, `MIT` stop-words → pass
- Short ALL_CAPS `OS`, `URL` (length < 6) → pass
- Plain lowercase text → pass
- 2-part SQL identifier (not 3-part) → pass
- `Read` tool (not in scope) → pass
- `NotebookEdit` tool (not in scope) → pass
- Pure ALL_CAPS without compound structure (`SELECT`) → pass
- Malformed JSON input → pass
- Empty content field → pass
