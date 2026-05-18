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

Detection surface — a Bash segment is scanned when it contains `jq` followed
by at least one positional argument matching a known config path:

- `~/.claude/*.json`
- `~/.codex/*.json`
- Repo-root `settings.json` or `hooks.json` (bare filename)
- Any `.json` file under a `.claude/` or `.codex/` directory component
  (absolute or relative)

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
| `jq '.' ~/.claude/settings.json` (file empty) | **ADVISORY** `[config-empty]` |
| `jq '.hooks' hooks.json` (file invalid JSON) | **ADVISORY** `[config-invalid]` |
| `jq -r '.foo' .claude/settings.json` (file valid) | **SILENT** |
| `jq '.' ~/.codex/config.json` (file valid) | **SILENT** |
| `jq '.' /tmp/unrelated.json` (non-config path) | **SILENT** — path not in scope |
| `jq '.' settings.json` (file missing) | **SILENT** — existence not in scope |
| Non-Bash tool | **SILENT** |
| Malformed JSON stdin | **SILENT** (fail-open) |

### Response format

```
stderr: "[config-empty] <path> is empty — jq will return 'empty', downstream may silently fallback"
stderr: "[config-invalid] <path> is not parseable as JSON — jq will fail"
exit 0
```

Advisory-only: the hook **never blocks** and never emits JSON to stdout. The
user sees the stderr text and can inspect / repair the file before rerunning.

### Deduplication

Advisories are deduplicated per `session_id` + canonical path. A given file
triggers at most one advisory per session regardless of how many `jq` calls
target it. State file:

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

3 cases: empty file (`[config-empty]` advisory), invalid JSON file
(`[config-invalid]` advisory), valid JSON file (silent).
