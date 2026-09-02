# PreToolUse Bulk-Write Memory Checkpoint Advisory

Supported hosts: all

`hooks/advisory-nudge/bulk-write-memory-checkpoint/impl.py` (run in-process by
the `Edit|NotebookEdit|Write` dispatch group's `hooks/_dispatch.sh`, #1168)
fires on PreToolUse for `Edit`,
`Write`, and `NotebookEdit` tool calls that target a Source-of-Truth (SOT)
directory, and emits a **stderr advisory** (never a block) reminding the
agent to checkpoint memory before continuing a bulk-write loop.

The hook **never blocks**, **never asks**, **always exits 0**.

### Why this exists

Three "Loaded ≠ Retrieved" recurrences traced to the CLAUDE.md
"Bulk-authoring loop pre-checklist" rule: in each case a write loop over
SOT paths (`vault/`, `wiki/`, `.claude/`, `skills/`, `AGENTS.md` /
`CLAUDE.md` companions) completed without the agent re-reading MEMORY.md or
the target directory's SOT contract at loop-entry time.  Memory-addition
alone was insufficient enforcement — hook-level structural enforcement fires
the retrieval gap at detection time.

This is an advisory re-implementation of the intent from the closed PR #428,
now targeting the Phase 3 (ADR-0001§5.3) role-dir layout.

Reference: issue [#443](https://github.com/devseunggwan/praxis/issues/443).

### SOT path set

| Path pattern  | Bucket key  | Classification rule                      |
| ------------- | ----------- | ---------------------------------------- |
| `…/vault/…`   | `vault`     | Any component in the path equals `vault` |
| `…/wiki/…`    | `wiki`      | Any component equals `wiki`              |
| `…/.claude/…` | `.claude`   | Any component equals `.claude`           |
| `…/skills/…`  | `skills`    | Any component equals `skills`            |
| `…/AGENTS.md` | `AGENTS.md` | Exact basename (case-sensitive)          |
| `…/CLAUDE.md` | `CLAUDE.md` | Exact basename (case-sensitive)          |

Boundary rule: a path component must equal the bucket key **exactly** as an
independent path segment.  This prevents false positives on paths like
`/my-claude-project/file.md` (`.claude` is not a standalone component) or
`CLAUDE.md.bak` (basename does not equal `CLAUDE.md`).

### "Bulk" definition

"Bulk" = 2 or more SOT-flagged writes targeting the **same bucket** within
the same session.  The advisory fires at the 2nd write (the first moment a
"loop" is detectable) and is deduplicated per `(session_id, bucket)` — so
the agent receives at most one advisory per SOT bucket per session, regardless
of how many files are written in that loop.

Session state is keyed on `session_id` from the stdin payload (the canonical
Claude Code session identity), stored under
`${TMPDIR:-/tmp}/praxis-bulk-write-checkpoint/<session_id_hash>/`.
This mirrors the `path-probe-gate` `(session_id, parent_hash)` dedup pattern.

### Advisory content

Each triggered surface writes lines to stderr, all prefixed with
`[praxis:bulk-write-checkpoint]`.  Tool execution is never blocked.

Example for a 2nd write to `vault/my-note.md`:

```
[praxis:bulk-write-checkpoint] ── SOT-FLAGGED WRITE: vault/my-note.md ───
[praxis:bulk-write-checkpoint]
[praxis:bulk-write-checkpoint] Rule: Bulk-authoring loop pre-checklist (CLAUDE.md)
[praxis:bulk-write-checkpoint]   Writing to a Source-of-Truth directory ...
...
```

### Environment variables

| Variable                     | Effect                                 |
| ---------------------------- | -------------------------------------- |
| `PRAXIS_BULK_WRITE_BYPASS=1` | Skip all output and exit 0 immediately |

Default mode (no env vars): advisory only — writes to stderr, exits 0.

### Session state

State files live under `${TMPDIR:-/tmp}/praxis-bulk-write-checkpoint/` and
are cleaned up by OS tmp purge policy.  No persistent side effects.

### Fail-open contract

The hook returns exit 0 on every infrastructure error:

- Malformed JSON stdin
- Non-target tool invocation (`Bash`, `Read`, etc.)
- Empty or missing `file_path` / `notebook_path`
- `python3` unavailable (the shell wrapper handles this)
- Any uncaught exception in the inner logic

When `session_id` is absent from the payload (edge case), the hook uses a
fallback key — advisory may fire more eagerly in that case, which is the
conservative direction (advisory rather than silence).

### Relationship to sibling hooks

| Hook              | Overlap                                                                                                                                                                                                 |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `memory-hint`     | Surfaces hookable memory entries by keyword. The memory-checkpoint advisory specifically targets the SOT-write loop pattern as structural enforcement of the CLAUDE.md rule; the two are complementary. |
| `path-probe-gate` | Fires on nested worktree path writes. Complementary — different trigger condition, same event.                                                                                                          |

### Tests

```bash
bash tests/hooks/advisory-nudge/test_bulk-write-memory-checkpoint.sh
```

Cases: SOT-flagged paths for each bucket (vault/, wiki/, .claude/, skills/,
AGENTS.md, CLAUDE.md), first-write-silent (below threshold), second-write
advisory, dedup (third write silent), non-SOT path silent, boundary
collision (my-claude-project), bypass env var, fail-open (non-target tool,
malformed JSON).
