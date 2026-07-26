# Runtime Constraints

Fixed limits of the Claude Code runtime and related CLIs that affect every skill.
Skill spec authors: read this **before** writing a spec that calls external tools
or surfaces options to the user. These are not bugs — they are stable constraints
that will not change without a major Claude Code release.

Each entry follows this structure:
- **Constraint**: one-line summary
- **Why it bites skills**: which pattern silently fails
- **Workaround**: the safe alternative
- **Verified**: verification date / Claude Code version / source

---

## 1. `AskUserQuestion.options` — hard cap of 4 items

**Constraint**: `AskUserQuestion` enforces `maxItems: 4` on the `options` array.
Any spec that surfaces N > 4 options is structurally impossible — the schema
rejects it before the tool runs.

**Why it bites skills**: Skills that enumerate dynamic lists (e.g., all active
worktrees, all open issues, all provider names) and pass them directly as options
will fail when N > 4. The spec looks correct in isolation but breaks in any
realistic session with more than 3 enumerated items.

**Workaround**: Truncate to at most 3 meaningful options, then add a 4th option
that is either:
- `"취소"` — abort the flow; or
- `"Other (직접 입력)"` — fall through to a free-form follow-up question.

For a dynamic list longer than 3 items, surface the top 3 most likely candidates
(e.g., most-recently modified worktrees, most-recently touched issues) and use
the 4th slot for "Other / cancel". Never silently drop items without telling the
user that the list was truncated.

**Verified**: 2026-05-13 / Claude Code (Sonnet 4.6) / Issue #208 — observed
failure: `codex-review-wrap` Step 2 attempted to surface all 8 active worktrees
as options, which is impossible per the `maxItems: 4` JSON schema constraint.

### 1a. `AskUserQuestion.questions` — same hard cap of 4 items

**Constraint**: the `questions` array carries its own `minItems: 1` /
`maxItems: 4`. A step that needs a decision on more than 4 items must issue
consecutive calls of at most 4 questions each, not one large call.

**Verified**: 2026-07-26 / Claude Code (Opus 5) / Issue #861 — a 4-question
call (each with 3 explicit options plus the runtime's automatic `Other` slot)
round-tripped successfully and returned all four answers keyed by question
text; `codex-review-wrap` Step 5i batches its per-finding approval questions
against this cap.

---

## 2. `Skill(...)` cannot invoke a skill that declares `disable-model-invocation: true`

**Constraint**: Claude Code prevents a skill invoked via `Skill(...)` from
internally calling another skill that declares `disable-model-invocation: true`
in its frontmatter (e.g., `/codex:review`).

**Why it bites skills**: A wrapper skill that delegates to `codex:review` via
`Skill("codex:review")` will fail silently or with an opaque error. The wrapper
appears correct in spec — the delegation step is never reached at runtime.

**Workaround**: Instead of `Skill(...)`, invoke the underlying binary directly.
For `codex:review`, that means:
1. Resolve the `codex-companion.mjs` path from `installed_plugins.json`.
2. Call `node "{companion_path}" review {{ARGUMENTS}}` via `Bash`.

This matches what `/codex:review` does in its own foreground flow and is the
canonical pattern already implemented in `codex-review-wrap` Step 4.

If the companion binary is not found, surface alternatives via `AskUserQuestion`
(see `codex-review-wrap` Step 4a for the reference implementation).

**Verified**: 2026-05-13 / Claude Code (Sonnet 4.6) / Issue #208 — `codex-review-wrap`
redesigned to use `node codex-companion.mjs` directly after `Skill("codex:review")`
delegation was confirmed non-viable.

---

## 3. `Agent(subagent_type=...)` cannot invoke a skill — `Skill(...)` is the only path

**Constraint**: The `Agent` and `Skill` tools address disjoint namespaces.
`Agent(subagent_type="praxis:<name>")` resolves only against registered
subagent types (`general-purpose`, `Explore`, `Plan`, OMC `executor`, etc.).
Every praxis entry under `skills/*/SKILL.md` is a *skill*, not a subagent,
and is reachable only via `Skill(skill="praxis:<name>")`.

**Why it bites skills**: Skill names look like subagent IDs (slug-style,
plugin-prefixed), so it is natural to try `Agent(subagent_type="praxis:retrospect")`.
The call fails with `Agent type not found: praxis:retrospect`, which gives
no signal about the correct `Skill(...)` form. Documentation that mixes
"agent" and "skill" interchangeably amplifies the confusion.

**Workaround**: Always invoke praxis entries via `Skill(skill="praxis:<name>")`.
This is true regardless of whether the underlying skill declares
`disable-model-invocation: true` — that flag governs Skill→Skill nesting
(see entry #2), not the Agent-vs-Skill distinction.

| Wrong | Right |
| ------- | ------- |
| `Agent(subagent_type="praxis:codex-review-wrap")` | `Skill(skill="praxis:codex-review-wrap")` |
| `Agent(subagent_type="praxis:retrospect")` | `Skill(skill="praxis:retrospect")` |
| `Agent(subagent_type="praxis:cmux-delegate")` | `Skill(skill="praxis:cmux-delegate")` |

**Verified**: 2026-05-17 / Claude Code (Opus 4.7) / Issue #249 — `Agent()`
returns `Agent type not found` for every praxis skill name. Any praxis
skill listed in the README or AGENTS.md table is reachable only through
the `Skill(...)` tool.

---

## 4. `Bash` tool — cwd resets between invocations

**Constraint**: Each `Bash` tool call starts with the session's original cwd.
A `cd /some/path` in one `Bash` call does **not** persist to the next call.

**Why it bites skills**: Skills that split a multi-step operation across two
`Bash` calls (cd in the first, use the new cwd in the second) will silently
run the second command in the wrong directory. This causes incorrect `git`
operations, wrong file reads, and misrouted CLI invocations — all without an
error message, because the wrong directory is still a valid path.

**Workaround**: Use one of:
- **Single `Bash` call with `&&` chaining**: `cd /path && git status && node script.mjs`
- **Absolute paths throughout**: pass the full path to every command rather than
  relying on cwd — `git -C /path status`, `node /path/script.mjs`.

Never split a cwd-sensitive operation across multiple `Bash` calls. If the
operation is too long for one call, restructure it to use absolute paths.

**Verified**: 2026-05-13 / Claude Code (Sonnet 4.6) / Issue #208 / global
`CLAUDE.md` rule "Bash Redirect on Existing Path Requires Read-First" and
"worktree-context-pre-git-op" memory — the per-call cwd reset is the root
cause of the class of bugs these rules address.

---

## Adding a new entry

1. Observe a constraint that is **fixed by the runtime** (not a project
   convention or a configurable setting).
2. Verify it by hitting the constraint in a live session.
3. Add an entry using the four-field structure above.
4. Open a PR referencing the issue where you observed it.

Pre-commit hook validation for this file: planned for a future PR (tracked in
Issue #208).
