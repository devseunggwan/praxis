# PreToolUse Markdown Escape-Sensitive Edit Advisory

Supported hosts: all

`hooks/pre-edit-md-escape-advisory.py` is a paired PreToolUse(Edit) +
PostToolUse(Read) hook that nudges the agent to Read a markdown file
before constructing an `old_string` containing escape-sensitive tokens.

### Why this exists

Observed failure mode (issue [#230](https://github.com/devseunggwan/praxis/issues/230),
sub-task of epic [#219](https://github.com/devseunggwan/praxis/issues/219)):
Obsidian-style markdown files use backslash-escaped pipes inside
table-cell wikilinks:

```
| Link                       | Note  |
| -------------------------- | ----- |
| [[01-summary\|01]]         | Index |
```

The agent reconstructs `old_string` from prior-context recall as
`[[01-summary|01]]` (unescaped pipe) → Edit fails the exact-match.
Recovery costs a round trip plus a re-Read. The same risk applies to
HTML entities (`&amp;`, `&lt;`) and to escaped brackets (`\[`, `\]`)
inside table cells.

This hook moves the verification step from "Claude tries to remember
the file's escape style" to a structural nudge at Edit construction
time. Default behavior is advisory (stderr warn). Opt-in block mode
exists for projects where exact-match failures are particularly costly.

### Behavior

| Event | Action |
|-------|--------|
| `PreToolUse` on `Edit` of a `.md` file with escape-sensitive `old_string` and no recorded Read | warn (default) or deny (opt-in) |
| `PreToolUse` on `Edit` of a `.md` file after the file was Read this session | silent pass-through |
| `PreToolUse` on `Edit` of a `.md` file with no escape-sensitive token in `old_string` | silent pass-through |
| `PreToolUse` on `Edit` of a non-`.md` file | silent pass-through (matcher-scoped to `Edit`, but extension gate inside) |
| `PreToolUse` on `Write` / `NotebookEdit` | silent pass-through (no `old_string` to gate) |
| `PostToolUse` on `Read` of a `.md` file | record absolute path in the session history file |
| `PostToolUse` on `Read` of a non-`.md` file | silent (no recording) |
| Malformed JSON / missing fields | silent fail-open |

### Detection patterns

Conservative v1 set, chosen to minimize false positives:

| Label | Pattern | Example |
|-------|---------|---------|
| `\|` | `\\\|` | `[[name\|alias]]` (Obsidian table wikilink) |
| `\[` | `\\\[` | `\[bracket]` (escaped opener) |
| `\]` | `\\\]` | `bracket\]` (escaped closer) |
| HTML entity | `&[A-Za-z]+;` | `&amp;`, `&lt;`, `&nbsp;` |

Other escape forms (backslash-escaped backtick, asterisk, underscore)
are intentionally excluded. They appear far more often in body prose
than the table-cell escapes above, and including them would generate
high-noise advisories. Add to the pattern set in `ESCAPE_PATTERNS` if a
recurrence is observed in production sessions.

### Configuration

| Env var | Default | Effect |
|---------|---------|--------|
| `PRAXIS_MD_ESCAPE_MODE` | `warn` | `block` makes the gate emit `permissionDecision: "deny"` instead of a stderr warning. |
| `PRAXIS_MD_ESCAPE_SKIP` | unset | `1` = full opt-out for the session (silent pass-through regardless of other conditions). |
| `PRAXIS_MD_READ_HISTORY_FILE` | (auto-resolved) | Explicit history-path override. Used by tests for isolation; can also pin a known location for long-running sessions. |

### Session state file resolution

PreToolUse / PostToolUse hooks run as independent processes — no shared
in-memory state. The Read history must be persisted to disk. Resolution
order (standard praxis paired-hook pattern):

1. `PRAXIS_MD_READ_HISTORY_FILE` env var (explicit override; used by
   tests).
2. `session_id` from the hook payload →
   `${TMPDIR:-/tmp}/praxis-md-read-history-<session_id>.json`. This is
   the canonical praxis hook session key.
3. `${TMPDIR:-/tmp}/praxis-md-read-history-${PPID}.json` — last-resort
   back-compat fallback when the payload does not carry a `session_id`
   (direct CLI / test invocation).

State is keyed by `session_id` rather than `$CLAUDE_PROJECT_DIR` for
the standard praxis paired-hook reason: project-rooted state would
silently satisfy a later session's gate with a Read recorded by an
earlier session — breaking the "in this session" contract.

Read failures (missing file, malformed JSON) → empty history,
fail-open. Write failures → silently skip recording.

### Response (warn — default)

stderr only:

```
[pre-edit:md-escape] Edit target /Users/test/vault/_index.md contains
escape-sensitive token(s): `\|`. The file has not been Read in this
session — old_string may not match the file's actual escape format
(Obsidian table wikilinks, HTML entities, etc. differ across files).
Read the exact line range first before constructing old_string.
```

### Response (block — opt-in)

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "pre-edit:md-escape — Edit target ... contains escape-sensitive token(s): `\\|`, and the file has not been Read in this session. Read the exact line range first before constructing old_string. (Block mode active via PRAXIS_MD_ESCAPE_MODE=block.)"
  }
}
```

### Registration in consuming project's `.claude/settings.json`

The hook is registered in the praxis plugin's own `hooks/hooks.json` and
fires automatically when the plugin is loaded. To re-register manually:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/pre-edit-md-escape-advisory-pre.sh",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/pre-edit-md-escape-advisory-post.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

### Parsing guarantees

- `tool_input.file_path` and `tool_input.old_string` are read as raw
  strings — no shell tokenization. Paths with spaces / non-ASCII content
  are handled correctly.
- Escape pattern matching uses `re.search` on raw `old_string`. The
  pattern set is anchored to literal backslash-prefixed characters and
  HTML entity shape; ordinary table pipes (`| col |`) and prose
  ampersands (`rock & roll`) do NOT match.
- Extension matching is case-insensitive; both `.md` and `.markdown`
  count.
- All file I/O uses utf-8 + `errors='replace'`-style fallbacks; binary
  blobs in `old_string` would not crash the hook.

### Known limitations

- **Session-scope, not turn-scope.** A Read at turn 1 satisfies an Edit
  at turn 50 in the same session. Per-turn detection isn't structurally
  cheap (no turn counter in hook payload); session-scope matches the
  praxis paired-hook convention. Tighten if observed FP→FN drift bites.
- **File-level granularity.** Reading lines 1–10 satisfies a later Edit
  targeting line 500. v2 can scope to `(offset, limit)` overlap with the
  Edit target's line.
- **Pattern set is narrow by design.** Backtick-escape, escaped
  asterisks, escaped underscores, and other markdown escape forms are
  not in v1. Add to `ESCAPE_PATTERNS` once FPs from a wider set are
  measured.

### Tests

```bash
bash hooks/test-pre-edit-md-escape-advisory.sh
```

Covers 31 cases:
- **Advisory paths:** wikilink escape, escaped brackets (`\[` / `\]`),
  HTML entities (`&amp;`, `&lt;`), `.MD` / `.markdown` extension
  variants.
- **Silent paths:** Read recorded for the same file, no escape token,
  unescaped pipe / plain ampersand, non-`.md` extensions, non-`Edit`
  tools, `PRAXIS_MD_ESCAPE_SKIP=1`.
- **Deny path:** `PRAXIS_MD_ESCAPE_MODE=block` + escape + no Read.
- **State:** post-hook records `.md` Reads, ignores non-`.md` Reads,
  ignores non-`Read` tools. `session_id`-based history-path resolution.
- **Fail-open:** malformed stdin (pre + post), empty `file_path`, empty
  `old_string`, missing `tool_input`.
