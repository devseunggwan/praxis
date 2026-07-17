# PreToolUse Bash Momentum Rule Retrieval Gate

Supported hosts: claude, codex

Reference: [Autonomy vs Convention — ETHOS.md](../../../ETHOS.md#autonomy-vs-convention)

`hooks/advisory-nudge/momentum-rule-retrieval-gate/impl.py` intercepts `Bash` tool calls at
high-momentum action points and emits a **stderr advisory** surfacing the
relevant CLAUDE.md rules and memory entries that retrieval failure studies
show are most likely to be skipped under session momentum.

The `dispatch` and `force-push` triggers are advisory-only in default mode.
The `merge` trigger additionally **blocks** (via `permissionDecision: deny`)
when the assistant text preceding the merge lacks the Pre-Merge Reporting
briefing — see [Merge-briefing escalation](#merge-briefing-escalation-issue-797)
below.

### Why this exists

The 2026-05-18 retrospect identified 3 friction events that all converged on
the root cause "Loaded ≠ Retrieved at execution time": rules and memory entries
are in context but fail retrieval at the exact moment a high-stakes action
(multi-PR merge, agent dispatch, force-push) is executed. CLAUDE.md's
"Pre-Merge Reporting", "No Approval Transfer Across Companion PRs", and the
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
| `gh pr merge` (any flags) | `merge` | Pre-Merge Reporting + No Approval Transfer | every memory with `momentum:` containing `merge` |
| `cmux new-workspace` (dispatch via cmux) | `dispatch` | Pre-Implementation Surface Enumeration → Multi-PR / multi-worktree shared state + Self-Authored Labels Are Drafts | every memory with `momentum:` containing `dispatch` |
| `git push --force` / `--force-with-lease` / `-f` | `force-push` | trigger header + history-rewrite mutation rule | every memory with `momentum:` containing `force-push` |

### Dynamic memory loading

Memory cites are no longer hardcoded — they are loaded at hook fire time
from the same user-scoped memory directory used by `memory-hint`
(`PRAXIS_MEMORY_DIR` env var when set, otherwise
`~/.claude/projects/{slugified-cwd}/memory/`). A memory file participates
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
in default mode. The `merge` surface additionally emits a `permissionDecision:
deny` when the pre-merge briefing is incomplete — see
[Merge-briefing escalation](#merge-briefing-escalation-issue-797).

Example for `gh pr merge --squash`:

```
[praxis:momentum-gate] ── TRIGGER: gh pr merge ──────────────────────────────────
[praxis:momentum-gate]
[praxis:momentum-gate] Rule: Pre-Merge Reporting (CLAUDE.md)
...
```

### Merge-briefing escalation (issue #797)

The `merge` trigger alone is advisory — but a stderr line cannot stop a
`gh pr merge` that skips the Pre-Merge Reporting briefing, and that failure
recurred despite the advisory firing (memory
`feedback_pre_merge_briefing_compound_imperative`, two recurrences; the #795
retrospect confirmed the advisory fired yet the merge ran with 3 of 6 briefing
items and no explicit approve-ask). For the `merge` trigger **only**, the hook
reads the assistant text preceding the merge call and, when fewer than
`MERGE_BRIEFING_MIN_ITEMS` (default **4**) of the 6 Pre-Merge Reporting items
are present, escalates to `permissionDecision: deny` so the merge is blocked
and the agent must produce the briefing before retrying. The stderr advisory
is still emitted alongside the deny.

The 6 items (a single keyword hit marks each present, EN/KO): What changed /
What was verified / What was NOT verified / Risk-blast-radius / Open items /
explicit approve-ask.

**Why `deny`, not `ask`:** the sibling `pre-merge-approval-gate` already emits
an unconditional `ask` on *every* `gh pr merge`. A second `ask` would be
redundant with the exact gate that failed to prevent the #795 recurrence (the
user approved the `ask` blindly while the agent never produced the briefing).
`deny` adds the missing teeth — it blocks and feeds the reason back so the
agent self-corrects, rather than re-surfacing an approval the user will again
approve blindly.

**Escalation is contained** (fail-open and false-positive relief):

- No readable transcript (`transcript_path` absent/unreadable) → no escalation
  (fail open). The advisory still fires.
- `CMUX_DELEGATE=1` (background agent) → no escalation — the delegation intent
  is the approval, mirroring `pre-merge-approval-gate`.
- `PRAXIS_MOMENTUM_MERGE_ADVISORY=1` → demote back to advisory only (keeps the
  stderr reminder, skips the deny). The escape hatch for a mis-scored briefing.
- Trivial-PR markers (`typo`, `comment-only`, `single-line`, `오타`, `주석만`,
  `trivial pr`, `2-line report`, …) in the briefing text → no escalation,
  matching CLAUDE.md's "Trivial PRs: a 2-line report is fine" carve-out.

The `dispatch` and `force-push` triggers never emit a decision — escalation is
merge-only, per the issue scope (only the merge failure was reproduced).

### Environment variables

| Variable | Effect |
| ---------- | -------- |
| `PRAXIS_MOMENTUM_BYPASS=1` | Skip all output and exit 0 immediately (for scripted batch operations) |
| `PRAXIS_MOMENTUM_MERGE_ADVISORY=1` | Demote the merge-briefing escalation to advisory only (stderr reminder still fires, no `deny`) |
| `PRAXIS_MOMENTUM_STRICT=1` | Exit 2 (block) instead of exit 0, unless `PRAXIS_MOMENTUM_ACK=1` is also set |
| `PRAXIS_MOMENTUM_ACK=1` | Acknowledge the surface in strict mode; exit 0 after emitting the advisory |

Default mode (no env vars): advisory for `dispatch` / `force-push`; the `merge`
trigger blocks with `deny` only when the pre-merge briefing is incomplete
(above), otherwise advisory. `CMUX_DELEGATE=1` background sessions never escalate.

### Scope (Phase 1 vs Phase 2)

**Phase 1 (this hook):** ANY single matching command triggers the rule surface.
This is intentionally simple — every `gh pr merge` or `git push -f` emits the
relevant reminder, regardless of how many similar commands have already been
issued in the session.

**Phase 2 (separate issue, not yet implemented):** Add multi-mutation detection
to surface the gate only when a momentum pattern is detected (e.g., N merges
in rapid succession within the same session). Phase 2 will require session-state
tracking per PPID or `session_id`.

### Detection logic

The hook uses the same `safe_tokenize` / `iter_command_starts` / `strip_prefix`
pipeline as sibling hooks (`pre-merge-approval-gate.py`,
`bash-worktree-existence-advisory.py`). Each segment in the tokenized command
is inspected independently so that compound commands (`cmux new-workspace && gh
pr merge ...`) trigger the appropriate surfaces for each matching segment.

gh global flags (`-R/--repo`, `--hostname`, `--color`) are walked past before
checking the subcommand so that `gh -R owner/repo pr merge` is detected
correctly.

`git push` force detection scans all tokens after the `push` subcommand for
`--force`, `-f`, and `--force-with-lease` (including `--force-with-lease=<ref>`
prefix-matched form).

### Relationship to sibling hooks

| Hook | Overlap |
| ------ | --------- |
| `pre-merge-approval-gate` | Both fire on `gh pr merge`. The sibling surfaces an **unconditional** `permissionDecision: ask` (per-PR user approval, content-blind). This hook emits the stderr rule reminder and, when the pre-merge briefing is incomplete, a **content-aware** `permissionDecision: deny` (blocks so the agent produces the briefing). Different checks — the sibling gates *user* approval, this hook gates *briefing existence*. When both fire, `deny` wins (more restrictive), and the agent self-corrects. |
| `side-effect-scan` | Fires on `gh pr merge` / `git push` collateral side effects. Complementary. |
| `verify-commit-flag-override` | Fires on `git commit --no-verify`. Different trigger, no overlap. |
| `memory-hint` | Surfaces `hookable: true` memory entries by keyword. The momentum gate specifically surfaces the entries most relevant to merge / dispatch / force-push momentum, as a targeted complement to the general memory-hint scan. |

### Firing order (PreToolUse Bash chain)

`hooks/hooks.json` registers the PreToolUse(Bash) hooks in this order. Claude
Code fires them sequentially; `momentum-rule-retrieval-gate` is registered
LAST (19th), AFTER `pre-merge-approval-gate` (9th):

| # | Hook |
| --- | ------ |
| 1 | `side-effect-scan` |
| 2 | `block-gh-state-all` |
| 3 | `memory-hint` |
| 4 | `cross-boundary-preflight` |
| 5 | `block-pr-without-caller-evidence` |
| 6 | `pre-gh-pr-create-dedup-gate` |
| 7 | `commit-title-length-check` |
| 8 | `pre-merge-approval-gate` (surfaces `permissionDecision: ask` on `gh pr merge`) |
| 9 | `gh-flag-verify` |
| 10 | `cli-flag-incompat-advisory` |
| 11 | `jq-config-empty-dict-advisory` |
| 12 | `bash-worktree-existence-advisory` |
| 13 | `verify-commit-flag-override` |
| 14 | `session-intent` |
| 15 | `output-block-falsify-advisory` |
| 16 | `count-assertion-verify` |
| 17 | `external-write-path-existence-check` |
| 18 | **`momentum-rule-retrieval-gate`** (this hook — stderr rule reminders) |

When both `pre-merge-approval-gate` and this hook fire on `gh pr merge`, the
user first sees the sibling's `permissionDecision: ask` dialog (hard-gate)
and the stderr reminders from this hook accompany the prompt context. A
`permissionDecision: ask` from an earlier sibling does not short-circuit
subsequent advisory hooks — both the stderr payload and the ask dialog are
surfaced together by Claude Code.

Future hooks added to the same matcher should preserve this invariant:
**hard-gates (deny / ask) before advisories**, so that when an upstream gate
blocks, the downstream advisory output is still produced and visible in the
same surface.

When updating this table, regenerate it from `hooks/hooks.json` directly so
the doc cannot drift from the source-of-truth registration order.

### Fail-open contract

The hook returns exit 0 on every infrastructure error:

- Malformed JSON stdin
- Non-Bash tool invocation
- Empty or whitespace-only command
- `python3` unavailable (the shell wrapper handles this)
- Any uncaught exception in the inner logic

### Tests

```bash
bash tests/test_momentum_rule_retrieval_gate.sh
```

Cases: gh pr merge trigger, cmux new-workspace trigger, force-push triggers
(--force / -f / --force-with-lease), bypass env var, strict mode (block +
ack), fail-open (non-Bash tool, malformed JSON), silent cases (unrelated
commands), compound command multi-trigger.
