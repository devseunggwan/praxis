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
- a cancel option — abort the flow; or
- an "other / type it in" option — fall through to a free-form follow-up
  question.

The praxis skills spell these as `"취소"` and `"Other (직접 입력)"`; any
label works, as long as the fourth slot is the escape hatch.

For a dynamic list longer than 3 items, surface the top 3 most likely candidates
(e.g., most-recently modified worktrees, most-recently touched issues) and use
the 4th slot for "Other / cancel". Never silently drop items without telling the
user that the list was truncated.

**Verified**: 2026-05-13 / Claude Code (Sonnet 4.6) / Issue #208 — a skill
step that passed 8 enumerated worktrees as options was rejected by the
`maxItems: 4` schema.

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

## 4. `Bash` tool — cwd resets between invocations (host-dependent)

**Constraint**: Each `Bash` tool call starts with the session's original cwd.
A `cd /some/path` in one `Bash` call does **not** persist to the next call.
This holds on the remote (web) harness, which resets the shell explicitly;
the local CLI's own tool description says the working directory persists
between calls, so the behaviour is host-dependent and a skill must not rely
on either — write every step so it is correct under both.

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

**Verified**: 2026-05-13 / Claude Code (Sonnet 4.6) / Issue #208 — the per-call
cwd reset was the root cause of a class of wrong-directory git operations the
author had previously papered over with prompt-layer rules.

**Re-verified**: 2026-09-05 / Claude Code remote (web) session / Issue #1286 —
`cd /tmp && pwd` printed `/tmp` and the harness appended `Shell cwd was reset
to /home/user/praxis` to the result; the next call's `pwd` printed
`/home/user/praxis`. That appended line is the same harness note
`second-failure-advisory` strips as noise (issue #1042), so the reset is a
documented property of this host. The local CLI's Bash tool description
states the opposite ("Working directory persists between calls"); no local
measurement has been recorded here, so treat persistence as unverified and
keep the workaround.

---

## 5. Skill `description` frontmatter — truncated past ~1,024 characters

**Constraint**: The runtime exposes each skill's `description` to the model in
a bounded slot of 1,024 characters; text past the budget is not shown. The
YAML file itself accepts any length — nothing fails at parse time, the tail is
just silently absent from the routing surface.

**Why it bites skills**: The praxis convention puts the `Triggers on "..."`
clause at the **end** of the description, so an over-budget description loses
exactly its trigger keywords — the part routing depends on. The skill then
stops matching some (or all) of its documented triggers with no error
anywhere: the spec looks complete, `check-plugin-manifests.py` passes, and
the drift only surfaces as "the skill didn't fire".

**Workaround**: Keep the folded description ≤ 1,024 characters, and keep the
`Triggers on "..."` clause inside the first **500** — 500 is the older,
equally unmeasured figure `CONTRIBUTING.md` used to carry, so a description
that clears it routes correctly whichever bound is real. Trim prose,
never triggers. When a body is too rich to summarize under the budget, move
detail into the body or `references/` (see `writing-praxis-skill` →
*Progressive disclosure*) — the description is a routing surface, not
documentation.

**Workaround (2026-09-06, issue #1331)**: the trigger phrases now live in the
`when_to_use:` frontmatter field, outside `description`. The runtime documents
`when_to_use` as "appended to `description` in the skill listing" under a
combined cap of 1,536 characters
(<https://code.claude.com/docs/en/skills.md>, read 2026-09-06) — so the
phrases are no longer inside the prose the rule above measures, but they still
sit at the tail of the listing budget. The practical effect: `description`
holds only what the skill does (largest in this repo ≈ 650 folded characters),
the combined text stays well inside both figures (largest ≈ 800), and each
field's length is measurable on its own instead of by counting back from the
end of one string. The constraint text above is kept as written — neither
figure has been measured live here. `scripts/check-plugin-manifests.py` Rule
13e mirrors `docs/skills.md` against `when_to_use`, and still reads
`description` so an unmigrated clause is checked rather than exempt.

**Verified**: 2026-08-30 / Issue #1181 — status: **documented-behavior-based,
not yet measured live in this repo**. The observed half: `codex-review-wrap`'s
description had grown to 1,405 folded characters with the trigger clause in
the truncated tail region, and #1181 compressed it to 917. The limit value
itself (1,024) comes from the documented Claude Code skill-description budget,
not from a live truncation observation here. What would verify it: publish a
throwaway skill whose description carries a sentinel trigger keyword starting
past character 1,024, then check whether the loaded skill listing shows the
sentinel and whether the keyword still routes. Until then, treat 1,024 as the
ceiling and leave headroom.

---

## 6. `PreCompact` and `PostCompact` carry no context-injection channel — use `SessionStart` with matcher `compact`

**Constraint**: Neither compaction event can hand text back to the model.
`PreCompact` accepts only the top-level `decision` / `reason` / `continue` /
`stopReason` / `suppressOutput` / `systemMessage` fields and no
`hookSpecificOutput.additionalContext`. `PostCompact` "hooks have no decision
control … Claude Code discards a PostCompact hook's `systemMessage` and
`continue` fields", and it has no `additionalContext` channel either. The
context channel around a compaction is `SessionStart`: its matcher values are
`startup`, `resume`, `clear`, `compact`, `fork`, its input carries `source`,
its decision control supports `hookSpecificOutput.additionalContext`
(`hookEventName: "SessionStart"`), and plain stdout is added to context as
well. The hooks guide's own recipe for this is titled "Re-inject context after
compaction" and registers `SessionStart` with `"matcher": "compact"`.

**Why it bites skills**: A hook that wants the post-compaction turn to know
something (worktree, branch, open PR, strike count) and registers it on
`PreCompact` or `PostCompact` runs, exits 0, and delivers nothing — there is
no error, the output is simply not a channel. The praxis `postcompact-context`
hook spent three designs on this: `PreCompact` (#466, no channel), then
`UserPromptSubmit` with a transcript-tail scan for the `isCompactSummary`
record plus a per-session dedup file and a lock so the marker still in the
tail on later prompts would not re-inject (#472, #1034, #1155). All of it was
infrastructure for detecting an event the runtime raises directly.

**Workaround**: Register on `SessionStart` with `"matcher": "compact"`, guard
on `source == "compact"` in the body, and emit
`{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": …}}`.
The event fires once per compaction, so no dedup state is needed. Reference
implementation: `hooks/advisory-nudge/postcompact-context/impl.py` (#1339).

**Coverage**: the `SessionStart` matcher table lists `compact` as "Auto or
manual compaction" (read 2026-09-06), so the registration covers both. No
minimum Claude Code version is stated for the matcher; none is assumed here.

**Documented**: 2026-09-06 / <https://code.claude.com/docs/en/hooks> and
<https://code.claude.com/docs/en/hooks-guide> / Issue #1339 — status:
**documented-behavior-based, not yet measured live in this repo**. The
`PreCompact` half was measured in #472 (Wave 0 probe); the `PostCompact`
quote and the `SessionStart` matcher list are read from the docs on that
date. What would verify it: run `/compact` in a session on a release that
carries the `SessionStart(compact)` registration and check the fire ledger
for a `postcompact-context` record plus the injected block in the next turn.

---

## 7. `SubagentStop` carries two transcripts — `transcript_path` is the parent's, not the subagent's

**Constraint**: `SubagentStop` fires when a subagent finishes and "use[s] the
same decision control format as Stop hooks". Its payload carries `agent_id`,
`agent_type`, `agent_transcript_path`, `last_assistant_message` — and
`transcript_path`. The last one is **the main session's transcript**, not the
subagent's: "The `transcript_path` is the main session's transcript, while
`agent_transcript_path` is the subagent's own transcript stored in a nested
`subagents/` folder."

**Why it bites hooks**: a Stop gate registered on `SubagentStop` without
changing anything reads `transcript_path` — as every Stop gate does — and so
grades the **parent session's** last turn against the **subagent's**
completion claim. It does not error; it silently answers about the wrong
conversation. The `last_assistant_message` field has the same shape of trap in
reverse: it is the reliable source for the final text on both events, because
the transcript "is written asynchronously and may lag the in-memory
conversation", so a gate that only reads the transcript can miss a claim that
was already made.

**Workaround**: on a subagent payload read `agent_transcript_path` **or
nothing** — never `transcript_path`. A fallback looks harmless and is not: the
turn then comes from the parent while the claim comes from the subagent's
`last_assistant_message`, so a subagent that ran nothing and repeated a number
from the parent's output clears an evidence check against evidence it never
produced. A plain `Stop` payload carries neither `hook_event_name:
SubagentStop` nor the key, and reads `transcript_path` as before. Prefer
`last_assistant_message` for the final text, keeping the transcript read as
the gate on whether any evidence was seen at all. Reference implementation:
`hooks/_lib/_transcript.py` `resolve_stop_transcript` / `load_stop_turn` /
`stop_last_assistant_text` (#1337).

**Sidechain markers**: the shared readers drop an event whose `isSidechain` is
true, which is how a subagent's events are kept out of the MAIN transcript's
turn. Every event in a per-agent transcript belongs to that agent, so that
filter must not be applied to it — left on, it would empty the turn and every
gate would pass each subagent silently. `load_recent_events(drop_sidechain=…)`
removes the marker as the agent transcript is parsed; whether the field is
even written there is **not measured** (see below), and dropping it is a
no-op if it is absent.

**Documented**: 2026-09-06 / <https://code.claude.com/docs/en/hooks> /
Issue #1337 — status: **documented-behavior-based, not yet measured live
in this repo**. What would verify it: run a subagent to completion on a release
carrying the `SubagentStop` registrations and check the fire ledger for a
`completion-verify` record plus which transcript the gate read.

---

## 8. Hook payload field names diverge from the published reference

**Constraint**: For three events the field names in
<https://code.claude.com/docs/en/hooks> do not match what the runtime sends.
Measured 2026-09-07 on Claude Code 2.1.263 by dumping the raw payload from a
canary hook.

| Event | Reference says | Runtime sends |
| ------- | ---------------- | --------------- |
| `PostToolBatch` | array `tools`; entries carry `tool_output` / `error` | array **`tool_calls`**; entries carry `tool_input` / `tool_name` / **`tool_response`** / `tool_use_id` |
| `ConfigChange` | `config_source`, `changes` | **`source`** (observed value `project_settings`), `file_path`; no `changes` key |
| `SubagentStart` | includes `task` | **no `task`** — `agent_id`, `agent_type`, `cwd`, `hook_event_name`, `prompt_id`, `session_id`, `transcript_path` |

**Why it bites hooks**: the failure is silent in the worst direction. A hook
that reads the documented `tools` gets an empty list, finds nothing to object
to, and exits 0 — indistinguishable from a clean batch. Nothing errors, no
schema check fires, and the gate looks installed and healthy while enforcing
nothing. The `SubagentStart` case removes a capability rather than a field: no
`task` means the delegation prompt is **not** inspectable, so any design of the
form "read the prompt and warn when it lacks X" cannot be built on this event —
only unconditional `additionalContext` injection can.

**Workaround**: before adopting an event, dump its real payload and write the
hook against that. The dump needs no change to user settings — an isolated
headless session takes its own settings file:

```bash
claude -p "<something that triggers the event>" --settings /path/to/canary.json
```

where `canary.json` registers a hook whose command writes stdin to a file.
Pin the observed field name in the hook with the divergence recorded beside
it, and add a test case that feeds the *documented* name and asserts the gate
does **not** fire on it — paired with a control case under the real name that
does. Without that pair, a later "fix" that switches to the documented name
looks green.

**Documented**: 2026-09-07 / <https://code.claude.com/docs/en/hooks> vs.
measured runtime / Issue #1370 — status: **measured live**, canary dumps for
`PostToolBatch`, `ConfigChange`, `SubagentStart`, plus the negative result
below.

**Adjacent measurement**: `PreCompact` firing is not evidence that a compaction
happened. In the canary session it fired once and `PostCompact` — registered in
the same settings file — fired zero times; the session ended three seconds
later. A hook that treats `PreCompact` as "a compaction is underway" will act
on compactions that never occur.

**Adjacent measurement**: `WorktreeCreate` fires for the built-in worktree tool
and **not** for a Bash `git worktree add`. Positive control: in the same
session `SessionStart`, `PreToolUse` and `SessionEnd` all fired, and the
worktree directory the command created exists on disk — so the absence is the
event's scope, not a dead canary. A gate meant to guard this repo's
worktree workflow, which uses the Bash form, cannot be built on it.

---

## Adding a new entry

1. Observe a constraint that is **fixed by the runtime** (not a project
   convention or a configurable setting).
2. Verify it by hitting the constraint in a live session.
3. Add an entry using the four-field structure above.
4. Open a PR referencing the issue where you observed it.

The skill-side half of this convention — `verified-against-runtime` and its
two companion fields on every runtime-sensitive `SKILL.md` — is enforced by
`scripts/check-plugin-manifests.py` Rule 11. Entries in this file are added
by hand.
