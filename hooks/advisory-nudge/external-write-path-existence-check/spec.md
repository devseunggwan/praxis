# PreToolUse External-Write Phantom-Path Existence Check

Supported hosts: all

`hooks/external-write-path-existence-check.py` is a PreToolUse advisory
that warns when a `gh issue` / `gh pr` body file references repository paths
that do not exist on disk.

### Why this exists

The immediate trigger for this hook (praxis issue #324) was a parent agent
writing phantom `hooks/pre-tool-use/external-write-path-existence-check.sh`
paths into a GitHub issue body.  The flat `hooks/` layout means
`hooks/pre-tool-use/...` can never exist, but the fabricated path appeared
in issue text that downstream readers and review tools (BugBot, Codex) used
as ground truth.  Lexical scanning of body files before posting catches this
class of error at the last checkpoint before shared-state mutation.

### What is warned

| Trigger                                                         | Condition                                                                                              | Advisory                    |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | --------------------------- |
| `gh (issue\|pr) (create\|edit\|comment) ... --body-file <path>` | Body contains markdown link `](./target)` or `](docs/...)` etc. where target is absent under repo root | Phantom-path list to stderr |
| Same trigger                                                    | Body contains inline code span `` `hooks/foo.sh` `` with a repo-relative prefix where target is absent | Phantom-path list to stderr |

Path extraction covers two sources:

- **Markdown link targets** (`](path)`) — Phase 1
- **Inline code spans** (`` `hooks/foo.sh` ``) — Phase 2, repo-relative prefix only

Recognized repo-relative prefixes:
`./`, `docs/`, `hooks/`, `skills/`, `tests/`, `scripts/`, `manifests/`

Leading `./` is stripped to produce a root-relative path using `target[2:]`
(not `lstrip("./")`) so paths like `./../escape.md` are not mangled —
they become `../escape.md` and are correctly flagged as out-of-tree.

### What is NOT warned

| Pattern                                                 | Reason                                                                           |
| ------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `--body "text"` / `--body-file -` (stdin)               | Body content inaccessible at PreToolUse time; fail-open                          |
| Absolute paths (`/usr/...`)                             | Out-of-scope — not repo-relative                                                 |
| HTTP/HTTPS/mailto/anchor links                          | Scheme-prefixed or anchor targets                                                |
| Paths outside recognized prefixes                       | Not in scope                                                                     |
| Binary files (NUL byte in first 512 bytes)              | Sniffed as binary; skipped to prevent false-positive matches inside binary blobs |
| Same session_id + body content SHA-256 already reported | Dedup prevents spam                                                              |

### Response

```text
[phantom-path] 2 referenced path(s) do not exist in repo root (/Users/.../praxis):
  • hooks/pre-tool-use/external-write-path-existence-check.sh
  • docs/hook/nonexistent-spec.md
Verify paths before posting — phantom links confuse readers and review tools.
Set PRAXIS_PHANTOM_PATH_STRICT=1 to convert this advisory into a hard block (exit 2).
```

Default mode emits the advisory to stderr and **exits 0** (advisory only).
Set `PRAXIS_PHANTOM_PATH_STRICT=1` to convert into a hard block (exit 2).

### Repo root resolution

1. `git -C <dir-of-body-file> rev-parse --show-toplevel`
2. `git -C <cwd> rev-parse --show-toplevel`
3. Fallback: `cwd` from payload (fail-open)

This handles worktree layouts where the body file lives in a temp directory
outside the repo tree.

### Session-scoped dedup

Advisory is emitted at most once per `(session_id, body content SHA-256)` pair.
State marker written to `${PRAXIS_STATE_DIR:-~/.praxis/state}/phantom-path/<hash>`
(host-neutral durable root, #527; `PRAXIS_STATE_DIR` still overrides the base).

### Parsing guarantees

- Inherited from `_hook_utils.safe_tokenize` (same primitive as sibling hooks):
  quoted strings, env prefixes, wrapper commands, shell control-flow keywords
  are handled before the hook inspects the argv.
- Subshells (`$(...)`) are opaque to shlex — not decomposed (acknowledged
  limitation shared with all sibling Bash hooks).
- `--body-file -` (stdin) is not inspected — hook passes through silently.
- Body file read failure (missing, permission denied) is fail-open: hook exits 0.

### How to enable

Add an entry to `~/.claude/settings.json` or `.claude/settings.json` under
`hooks.PreToolUse`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/advisory-nudge/external-write-path-existence-check/impl.py" }
        ]
      }
    ]
  }
}
```

For strict mode (hard block on any phantom path detected):

```bash
export PRAXIS_PHANTOM_PATH_STRICT=1
```

Restart Claude Code after adding the entry.

### Tests

```bash
bash tests/test_external_write_path_existence_check.sh
```

Covers 9 cases (Phase 1 scope + M1/M2/M3 regression + P1 refinements):

| Case                         | Input                                                                               | Expected                                              |
| ---------------------------- | ----------------------------------------------------------------------------------- | ----------------------------------------------------- |
| Existing path                | Body links to `docs/hook/INDEX.md` (real file)                                      | Pass — no advisory                                    |
| Missing path                 | Body links to `hooks/pre-tool-use/fake.sh` (phantom)                                | Advisory emitted                                      |
| Mixed paths                  | Body links to one real + one phantom                                                | Advisory lists only the phantom                       |
| Anchor fragment (M1)         | Body links to `docs/hook/INDEX.md#preflight-gate`                                   | Pass — fragment stripped, file exists                 |
| Dedup re-emit (M2)           | Same body content submitted twice in same session                                   | Advisory fires once; second suppressed                |
| Binary body file (M3)        | Body file contains non-UTF-8 bytes                                                  | Fail-open — exit 0, no advisory                       |
| Inline code span (P1-Phase2) | `` `hooks/pre-tool-use/nonexistent.sh` `` phantom + `` `docs/hook/INDEX.md` `` real | Advisory for phantom only                             |
| lstrip quirk (P1-lstrip)     | Link target `./../escape.md`                                                        | Advisory shows `../escape.md`, not `escape.md`        |
| Binary NUL sniff (P1-binary) | Body file with NUL bytes (and embedded phantom path string)                         | Fail-open — NUL sniff skips file, exit 0, no advisory |
