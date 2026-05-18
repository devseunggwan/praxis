# PreToolUse jq Config Empty/Invalid Advisory

Supported hosts: all

`hooks/jq-config-empty-dict-advisory.sh` watches Bash tool calls that invoke
`jq` against known config file paths. When the target file is empty (size 0)
or contains invalid JSON, it emits a stderr advisory — before `jq` silently
returns `empty` or crashes downstream.

### Why this exists

`jq` applied to an empty config file returns the literal string `empty` on
stdout rather than erroring. Downstream code that pipes or assigns this
output may silently fall back to a wrong default, masking the root cause for
several turns. Invalid JSON produces a jq parse error, but only at runtime
— the agent has already composed and dispatched the full pipeline.

This hook surfaces the problem *before* the command executes (issue #323).

### What is detected

Detection surface — config paths matched:

- `~/.claude/*.json`
- `~/.codex/*.json`
- Repo-root `settings.json` or `hooks.json` (bare filename)
- Any `.json` file under a `.claude/` or `.codex/` directory component
  (absolute or relative)

Four invocation patterns are recognized:

| Pattern | Example |
|---------|---------|
| Direct | `jq '.' ~/.claude/settings.json` |
| Boolean flag | `jq -e '.theme' ~/.claude/settings.json` |
| Stdin redirect | `jq '.' < ~/.claude/settings.json` |
| Pipe from prior segment | `cat ~/.claude/settings.json \| jq '.'` |
| Command substitution | `threshold=$(jq -r '.foo' ~/.claude/settings.json)` |

File-level outcomes:

| File state | Action |
|------------|--------|
| Missing / does not exist | **SILENT** — out of scope; other hooks cover existence |
| Empty (size 0) | **ADVISORY** `[config-empty]` |
| Present, invalid JSON (jq parse fail) | **ADVISORY** `[config-invalid]` |
| Present, valid JSON | **SILENT** |

Command-level examples:

| Command | Action |
|---------|--------|
| `jq '.' ~/.claude/settings.json` (empty) | **ADVISORY** `[config-empty]` |
| `jq -e '.theme' ~/.claude/settings.json` (empty) | **ADVISORY** `[config-empty]` — `-e` is boolean |
| `jq --sort-keys '.foo' ~/.claude/settings.json` (empty) | **ADVISORY** `[config-empty]` — `--sort-keys` is boolean |
| `jq '.hooks' hooks.json` (invalid JSON) | **ADVISORY** `[config-invalid]` |
| `cat ~/.claude/settings.json \| jq '.'` (empty) | **ADVISORY** `[config-empty]` — pipe correlation |
| `jq '.' < ~/.claude/settings.json` (empty) | **ADVISORY** `[config-empty]` — stdin redirect |
| `threshold=$(jq -r '.foo' ~/.claude/settings.json)` (empty) | **ADVISORY** `[config-empty]` — substitution |
| `jq -r '.foo' .claude/settings.json` (valid) | **SILENT** |
| `jq '.' ~/.codex/config.json` (valid) | **SILENT** |
| `jq '.' /tmp/unrelated.json` (any state) | **SILENT** — path not in scope |
| `jq '.' settings.json` (missing) | **SILENT** — existence not in scope |
| `jq -n 'null'` | **SILENT** — no file argument |
| Non-Bash tool | **SILENT** |
| Malformed JSON stdin | **SILENT** (fail-open) |

Not detected (by design):

| Pattern | Reason |
|---------|--------|
| `jq '.' /tmp/other.json` | Non-config path, out of scope |
| `jq -n '{}'` | Null input (`-n`), no file read |
| Multi-hop pipe `cmd1 \| cmd2 \| jq` | Only immediate prior segment correlated |
| `` `jq -r '.foo' ~/.claude/settings.json` `` | Backtick substitution — `safe_tokenize` does not parse backtick blocks; follow-up #338 |
| `$(jq -r .foo cfg.json && jq -r .bar cfg.json)` | Shell separator inside `$(…)` — `_coalesce_subst_runs` joins past `&&` boundary; follow-up #338 |

### Response format

```
stderr: "[config-empty] <path> is empty — jq will return 'empty', downstream may silently fallback"
stderr: "[config-invalid] <path> is not parseable as JSON — jq will fail"
exit 0
```

Advisory-only: the hook **never blocks** and never emits JSON to stdout. The
user sees the stderr text and can inspect / repair the file before rerunning.

### Deduplication

Advisories are deduplicated per `session_id` + canonical path. Only
advisory-emitting checks are recorded — a file that is valid on first access
and later becomes empty will re-trigger the advisory on the next call. State
file:

```
${TMPDIR:-/tmp}/praxis-jq-config-advisory-<session_id>.json
```

PPID is used as a fallback key when `session_id` is absent (direct CLI /
test invocations).

### Parsing guarantees (fail-open)

- malformed JSON stdin → exit 0
- non-Bash tool → exit 0
- empty / whitespace command → exit 0
- `python3` unavailable → shell wrapper exits 0
- `jq` binary absent → skip advisory, exit 0
- jq validation timeout (>5 s) → skip advisory, exit 0
- any uncaught exception → exit 0

### Relationship to sibling hooks

| Hook | Scope | Overlap |
|------|-------|---------|
| `cli-flag-incompat-advisory` | Advisory nudge for mode-incompatible flag combos | None — covers flag errors, not file-content errors |
| `external-api-literal-trigger` | Advisory nudge for unverified ALL_CAPS / 3-part SQL identifiers | None — different detection surface |
| `memory-hint` | Surface hookable memory entries | None — complementary |

### Tests

```bash
bash hooks/test-jq-config-empty-dict-advisory.sh
```

10 cases: empty file, invalid JSON file, valid JSON file (original 3),
plus C1 command substitution, C2a `-e` boolean flag, C2b `--sort-keys`
boolean flag, M3a/M3b dedup state transition (valid→empty), M4a pipe
correlation, M4b stdin redirect.
