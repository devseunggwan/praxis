# PreToolUse Bash Momentum Rule Retrieval Gate

Supported hosts: claude, codex, hermes

Reference: [Autonomy vs Convention — ETHOS.md](../../../ETHOS.md#autonomy-vs-convention)

`hooks/advisory-nudge/momentum-rule-retrieval-gate/impl.py` intercepts `Bash` tool calls at
high-momentum action points and emits a **stderr advisory** surfacing the
relevant CLAUDE.md rules and memory entries that retrieval failure studies
show are most likely to be skipped under session momentum.

The `dispatch` and `force-push` triggers are advisory-only in default mode.
The `gh pr merge` trigger moved to `merge-gate` (issue #1650), which prints the
same surface for a merge and owns the briefing escalation; see
[Merge trigger (moved)](#merge-trigger-moved).

### Why this exists

The 2026-05-18 retrospect identified 3 friction events that all converged on
the root cause "Loaded ≠ Retrieved at execution time": rules and memory entries
are in context but fail retrieval at the exact moment a high-stakes action
(multi-PR merge, agent dispatch, force-push) is executed. The *Pre-Merge
Reporting* and *No Approval Transfer Across Companion PRs* rules
([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)) and the
`feedback_force_history_rewrite_mutation` memory entry have each been violated
in sessions where they were already loaded. This hook fires at exactly those
trigger points so the retrieval gap is filled by the hook infrastructure
rather than relying on in-context retrieval alone.

### Trigger commands (Phase 1)

Phase 1 keeps the momentum signal simple: **any single matching command
triggers the surface**. Multi-mutation detection (e.g., surfacing only when
N merges occur in rapid succession) is deferred to Phase 2 (separate issue).

| Command pattern | trigger id | Static rule cites | Dynamic memory cites |
| ----------------- | ------------ | ------------------- | ---------------------- |
| `cmux new-workspace` (dispatch via cmux) | `dispatch` | Pre-Implementation Surface Enumeration → Multi-PR / multi-worktree shared state + Self-Authored Labels Are Drafts | every memory with `momentum:` containing `dispatch` |
| `git push --force` / `--force-with-lease` / `-f` | `force-push` | trigger header + history-rewrite mutation rule | every memory with `momentum:` containing `force-push` |

### Dynamic memory loading

Memory cites are no longer hardcoded — they are loaded at hook fire time
from the same user-scoped memory directory used by `memory-hint`, resolved
via the shared `hooks/_lib/_memory_dir.py` resolver (#823), and parsed by
`hooks/_lib/_momentum_surface.py`, which `merge-gate` reads too for the `merge`
trigger: `PRAXIS_MEMORY_DIR`
env var when set, otherwise `~/.claude/projects/{slugified-cwd}/memory/` —
slugify replaces every non-alphanumeric character with `-`, per-character
(Claude Code's own project-slug rule). A memory file participates
by adding a flat single-line `momentum:` list to its frontmatter:

```yaml
---
name: my-memory
description: Short rule statement
type: feedback
momentum: [merge]                  # one or more of: merge, dispatch, force-push
---
```

Multi-trigger memories: `momentum: [merge, force-push]`. When a triggered
command matches `merge` AND `force-push` in the same Bash call, the
memory is cited under both surfaces. Memories without a `momentum:` field
(or with an unparseable / empty list) are ignored. The frontmatter parser
is regex-only — multi-line YAML / flow-mapping forms are not supported
and silently skip the memory rather than raise.

### What is emitted

Each triggered surface writes lines to stderr, all prefixed with
`[praxis:momentum-gate]`. The `dispatch` and `force-push` surfaces never block
in default mode.

Example for `git push --force`:

```
[praxis:momentum-gate] ── TRIGGER: git push --force / --force-with-lease / -f ─────
[praxis:momentum-gate]
[praxis:momentum-gate] Rule: History rewrite is a mutation — prior approval is NOT carried over
...
```

### Merge trigger (moved)

The `gh pr merge` trigger, its rule surface and the Pre-Merge Reporting
briefing escalation moved to `merge-gate` (issue #1650); see
`hooks/preflight-gate/merge-gate/spec.md` → *Check: briefing*. The merge
check still reads `PRAXIS_MOMENTUM_BYPASS`, `PRAXIS_MOMENTUM_STRICT` and
`PRAXIS_MOMENTUM_ACK`, so those variables keep covering merges.

### Environment variables

| Variable | Effect |
| ---------- | -------- |
| `PRAXIS_MOMENTUM_BYPASS=1` | Skip all output and exit 0 immediately (for scripted batch operations) |
| `PRAXIS_MOMENTUM_STRICT=1` | Exit 2 (block) instead of exit 0, unless `PRAXIS_MOMENTUM_ACK=1` is also set |
| `PRAXIS_MOMENTUM_ACK=1` | Acknowledge the surface in strict mode; exit 0 after emitting the advisory |

Default mode (no env vars): advisory for `dispatch` / `force-push`.

### Scope (Phase 1 vs Phase 2)

**Phase 1 (this hook):** ANY single matching command triggers the rule surface.
This is intentionally simple — every agent dispatch or `git push -f` emits the
relevant reminder, regardless of how many similar commands have already been
issued in the session.

**Phase 2 (separate issue, not yet implemented):** Add multi-mutation detection
to surface the gate only when a momentum pattern is detected (e.g., N merges
in rapid succession within the same session). Phase 2 will require session-state
tracking per PPID or `session_id`.

### Detection logic

The hook uses the same `safe_tokenize` / `iter_command_starts` / `strip_prefix`
pipeline as sibling hooks (`hooks/preflight-gate/merge-gate/approval.py`,
`hooks/advisory-nudge/bash-worktree-existence-advisory/impl.py`). Each segment in the tokenized command
is inspected independently so that compound commands (`cmux new-workspace &&
git push --force ...`) trigger the appropriate surfaces for each matching segment.

`git push` force detection scans all tokens after the `push` subcommand for
`--force`, `-f`, and `--force-with-lease` (including `--force-with-lease=<ref>`
prefix-matched form).

### Relationship to sibling hooks

| Hook | Overlap |
| ------ | --------- |
| `merge-gate` | Owns the `gh pr merge` trigger this hook used to carry (issue #1650): same surface and memory filter, plus the briefing escalation. No overlap left. |
| `side-effect-scan` | Fires on `git push` collateral side effects. Complementary. |
| `verify-commit-flag-override` | Fires on `git commit --no-verify`. Different trigger, no overlap. |
| `memory-hint` | Surfaces `hookable: true` memory entries by keyword. The momentum gate specifically surfaces the entries most relevant to dispatch / force-push momentum, as a targeted complement to the general memory-hint scan. |

### Co-firing with the sibling gates (PreToolUse Bash)

Per the [Anthropic hooks docs](https://code.claude.com/docs/en/hooks), all
matching hooks run **in parallel**: array position in `hooks/manifest.json` is
registration order, not firing order, and no hook runs before or after another.

What matters here is therefore co-firing, not sequence. This hook writes
stderr rule reminders only; an `ask` or advisory from another hook on the same
command does not suppress them, because there is no chain to short-circuit.

A hook added to this matcher inherits that property automatically. There is no
ordering invariant to preserve, and none can be expressed: a spec that asks
future hooks to register "before" or "after" this one is asking for something
the runtime does not offer.

### Fail-open contract

The hook returns exit 0 on every infrastructure error:

- Malformed JSON stdin
- Non-Bash tool invocation
- Empty or whitespace-only command
- `python3` unavailable (the shell wrapper handles this)
- Any uncaught exception in the inner logic

### Tests

```bash
bash tests/hooks/advisory-nudge/test_momentum_rule_retrieval_gate.sh
```

Cases: cmux new-workspace trigger, force-push triggers
(--force / -f / --force-with-lease), bypass env var, strict mode (block +
ack), fail-open (non-Bash tool, malformed JSON), silent cases (unrelated
commands), compound command multi-trigger.
