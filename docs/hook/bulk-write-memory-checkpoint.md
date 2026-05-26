# PreToolUse Bulk-Write Memory Checkpoint

Supported hosts: claude, codex

`hooks/bulk-write-memory-checkpoint.sh` intercepts `Write`, `Edit`, and
`NotebookEdit` tool calls and emits a **stderr advisory** when the target
path is inside a flagged source-of-truth (SOT) directory — vault, wiki,
skills, AGENTS.md, CLAUDE.md companions. The advisory reminds the agent
that bulk-authoring loops collapse N decisions into 1, so per-task memory
retrieval at loop entry does NOT propagate to per-file writes.

### Why this exists

During a retrospect session (praxis-driven retrospect on 2026-05-26), three
recurrences of the same Loaded≠Retrieved pattern were documented across a
5-day window — all in bulk-authoring loops touching a team-shared knowledge
vault:

| Date | Failure | Memory entry that existed pre-session |
|------|---------|----------------------------------------|
| 2026-05-21 (Hub #2250) | Vault SOT direct-read protocol bypassed during summary stub authoring | `feedback_external_sot_direct_read_before_authoring_protocol.md` |
| 2026-05-23 | SB→LW migration cycle 1 — 21/55 files used invalid `source_type='external'` enum value | (same as above) + `user_laplace_wiki_team_shared_vs_personal.md` |
| 2026-05-26 | Same enum violation recurred in same session despite both memory entries loaded | (same as above) |

Root cause (from the retrospect tracer pass):

- Memory entries existed in `MEMORY.md` USER tier (highest salience) at
  session start
- Bulk-write loop collapsed N file decisions into 1 task decision
- Retrieval gate fired once at task entry, did not re-fire per file
- Result: enum-value drift and source-attribution leak across N files

This hook is the structural enforcement layer for the global behavioral
contract "Loaded ≠ Retrieved (Self-Discipline)" applied to bulk authoring
on SOT-flagged paths. Per `feedback_prompt_layer_retrieval_failure_threshold.md`,
3-gen recurrence threshold mandates escalation beyond memory — this hook
fills that gap at the per-file write boundary.

### What is emitted

The hook writes advisory text to stderr and exits 0. Tool execution is
never blocked.

| Condition | Behavior |
|-----------|----------|
| Target inside a flagged SOT pattern | Advisory to stderr (exit 0) |
| Target outside flagged patterns | Silent (exit 0) |
| Non-target tool (e.g., `Bash`, `Read`) | Silent |
| `Write` / `Edit` payload uses `file_path` | Path read from `tool_input.file_path` |
| `NotebookEdit` payload uses `notebook_path` | Path read from `tool_input.notebook_path` |
| Malformed JSON stdin | Silent (fail-open) |
| Missing `tool_input` / empty path | Silent (fail-open) |
| `python3` unavailable | Silent (Bash shim fail-open) |

### Flagged path patterns

Substring match (case-sensitive) on the target file path:

```
/laplace-wiki/
/second-brain/
/wiki/wiki/
/wiki/entities/
/wiki/concepts/
/wiki/summaries/
/wiki/analyses/
/wiki/recipes/
/wiki/playbooks/
/skills/
/SKILL.md
/AGENTS.md
/.claude/CLAUDE.md
```

The list is opinionated toward two concrete failure cases (the SB→LW
migration and laplace-wiki vault SOT protocol). Extending the pattern set
is a configuration follow-up — the list is intentionally narrow in v1 to
keep the false-positive rate observable.

### Advisory message

```
[advisory] Bulk-write checkpoint — SOT-flagged path detected.
  Path: <file_path>
  Reminder: if this is part of an N-file authoring loop, ensure relevant
  memory entries are retrieved per file, not just at task entry.
  Patterns to consider:
    feedback_external_sot_direct_read_*
    user_*_team_shared_vs_personal*
    feedback_inline_rules_*
  This is a soft advisory — write proceeds normally.
```

### v1 deliberately stateless

- No per-session write counter
- No N≥3 threshold gating
- No suppression on repeat patterns
- Fires on **every** SOT-flagged write

Stateful per-session counting + N-threshold gating are tracked as v2 once
the noise profile is observed in v1 production.

### Env vars

(None in v1.)

### Smoke tests

The hook is exercised with four fixture payloads at install time:

| Payload | Expected behavior |
|---------|-------------------|
| `Write` to `/laplace-wiki/wiki/summaries/test.md` | Advisory emitted, exit 0 |
| `NotebookEdit` with `notebook_path` to flagged path | Advisory emitted, exit 0 |
| `Write` to `/tmp/random.txt` (non-flagged) | Silent, exit 0 |
| `Bash` to `ls /laplace-wiki/` | Silent, exit 0 |
| `not json` stdin | Silent (fail-open), exit 0 |

### Files

- `hooks/bulk-write-memory-checkpoint.py` — Python implementation
- `hooks/bulk-write-memory-checkpoint.sh` — Bash shim delegating to Python
- Registered under PreToolUse matcher `Edit|Write|NotebookEdit` in `hooks/hooks.json`

### Related

- `path-probe-gate` — sibling advisory hook on the same matcher surface; complementary (path-depth probe vs SOT-flag probe)
- `pre-edit-protected-branch-guard` — also on `Edit|Write|NotebookEdit`; blocks on protected branches
- `memory-hint` — surfaces memory entries by keyword at decision-construction time (this hook adds the per-file retrieval surface that memory-hint cannot enforce)
