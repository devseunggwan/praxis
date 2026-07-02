# PreToolUse gh --json Field Validator

Supported hosts: claude, codex

`hooks/preflight-gate/gh-json-validator/impl.py` intercepts every Bash tool call of the form
`gh <subcommand> ... --json <fields>` and hard-blocks invocations whose
requested field names are not present in the subcommand's valid JSON
projection. The valid field set is probed at runtime via
`gh <subcommand> --json help 2>&1` and cached per subcommand within the
session.

### Why this exists

`gh CLI` accepts `--json <fields>` whose valid field set differs from the
GitHub REST API. A recurring failure mode (6+ sessions) is requesting a
field that exists in the REST API but not in gh's JSON projection — for
example `gh pr view --json merged` fails because `merged` is not a valid
gh pr field (use `state`, `mergedAt`, or `mergeCommit` instead). The
3-generation memory-rule threshold was already exceeded; hook-layer
enforcement is the remaining escalation path.

### What is blocked

The hook uses `safe_tokenize` → `iter_command_starts` → `strip_prefix`
from `_hook_utils.py` (same tokenization pipeline as all sibling hooks),
so chained segments and env-prefixed invocations are covered uniformly.

| Command | Action |
| --------- | -------- |
| `gh pr view 1 --json merged` | **BLOCKED** (`merged` not in gh pr fields; suggests `mergedAt`) |
| `gh pr view 1 --json state,title,url` | **PASS** |
| `gh pr view 1 --json state,merged` | **BLOCKED** (one invalid field) |
| `gh pr view 1 --json help` / `--json=help` | **PASS** (gh's own field introspection — the remediation tells you to run this; issue #514) |
| `gh pr view 1 --json help,merged` | **BLOCKED** (`help` + a real invalid field = data projection) |
| `gh issue view 5 --json merged` | **BLOCKED** (`merged` not in gh issue fields) |
| `gh issue view 5 --json state,number,title` | **PASS** |
| `gh pr list --json state && gh pr view 1 --json merged` | **BLOCKED** (chained segment scanned) |
| `gh issue list --json number,title,state` | **PASS** |
| `gh search issues --json number,title,state` | **PASS** |
| `gh api repos/owner/repo --json name` | **PASS** (gh api excluded — REST schema) |
| `gh release list` (no `--json`) | **PASS** (no --json flag) |
| `gh <unknown-subcmd> --json foo` | **PASS** (fail-open; unknown subcommand) |
| `gh pr view 1 --json state # PRAXIS_GH_JSON_BYPASS=skip` | **PASS** (inline bypass) |
| Non-Bash tool | **PASS** |
| Malformed JSON stdin | **PASS** (fail-open) |

### Field discovery

The hook calls `gh <subcmd> --json help 2>&1` to enumerate valid fields.
For subcommands that require a positional argument (`gh pr view`, `gh
issue view`, `gh release view`), a dummy positional `0` is injected on
retry when the arg-count error is detected in the output.

Parsed field list format:
```
Unknown JSON field: "help"
Available fields:
  fieldOne
  fieldTwo
  ...
```

### Caching

Field sets are cached per subcommand under
`/tmp/praxis-gh-json-<session_id>/` as JSON files (one per subcommand
shape). Cache is session-scoped — a new session starts with a fresh
cache. `PPID` is used as the session_id fallback for direct CLI / test
invocation.

Override `PRAXIS_GH_JSON_CACHE_DIR` is not required; the `/tmp/` path is
sufficient for session-scoped state.

### Closest-match suggestion

When a field is invalid, the hook tries to suggest the closest valid
field name by:
1. Case-insensitive exact match.
2. Valid field contains the requested field as a substring.
3. Requested field contains a valid field as a substring.

Example: `merged` → suggests `mergedAt`.

### Bypass

Two opt-out mechanisms:

1. **Inline comment** (per-command): append
   `# PRAXIS_GH_JSON_BYPASS=skip` anywhere on the command line.
2. **Env var** (per-session): set `PRAXIS_GH_JSON_BYPASS=1`.

### Fail-open paths

The hook never breaks a session on infrastructure failure:

- `gh` not installed
- Network / auth failure on `gh --json help`
- Unknown subcommand (not in gh's help output format)
- Malformed stdin JSON
- Cache file unreadable or unwritable

In every case the hook exits 0 silently; the gh command proceeds and may
fail at execution time with the original error.

### Response

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "BLOCKED: gh pr view --json has invalid field(s): 'merged' (did you mean 'mergedAt'?). Run 'gh pr view --json help' to see all valid fields. Sample valid fields: ..."
  }
}
```

### Relationship to gh-flag-verify and gh-label-verify

- `gh-flag-verify` validates flag *names* accepted by a subcommand.
- `gh-label-verify` validates label *values* passed to `--label`.
- This hook validates the *field names* passed to `--json`.

All three hooks co-fire under the same PreToolUse Bash matcher. Deny
precedence means the user sees the first denial reason; double-blocking
the same command is harmless.

### Tests

```bash
bash tests/test_gh_json_validator.sh
```
