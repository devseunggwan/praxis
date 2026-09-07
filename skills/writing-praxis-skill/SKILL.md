---
name: writing-praxis-skill
description: >
  Guide for authoring a new praxis SKILL.md — template usage, SRP, trigger
  keyword design, frontmatter conventions, and Claude/Codex host differences.
when_to_use: >
  Triggers on "new praxis skill", "write praxis skill", "add praxis skill",
  "skill template", "praxis skill spec", "스킬 작성", "새 스킬".
  Do NOT activate on "add skill section", "skill up", "skill set".
verified-against-runtime: true
runtime-verified-at: 2026-06-16
runtime-verified-note: "claude 1.x --help + current praxis verified-skill survey — the guide's `--model`/`--resume` examples and AskUserQuestion/Skill runtime constraints still match the live Claude CLI surface."
---

# writing-praxis-skill

## Overview

A new praxis skill needs a SKILL.md that the Claude Code plugin runtime can
parse and route correctly. Without a consistent structure, the runtime silently
misroutes or truncates the description, and contributors have to reverse-engineer
the pattern from the existing specs.

**Core principle:** one skill = one responsibility. If a skill needs a second
trigger phrase to describe a second job, split it into two skills.

## When to Use

- Creating a new praxis skill from scratch
- Reviewing an existing SKILL.md for structural compliance
- Onboarding a contributor who will add a skill

## Process

### Step 1: Copy the Template

```bash
cp skills/SKILL.md.tmpl skills/<skill-name>/SKILL.md
```

### Step 2: Fill in the Frontmatter

Replace every `<...>` placeholder. Do not leave any placeholder text in the
committed file.

```yaml
---
name: <skill-name>         # kebab-case; must match the directory name
description: >
  <One-line description of what the skill does.>
when_to_use: >
  Triggers on "<keyword1>", "<keyword2>", "<Korean-keyword>".
---
```

**Rules:**

- `name` must exactly match the directory name under `skills/`.
- `description` says what the skill does — concise, scannable, and with no
  trigger phrases in it.
- `when_to_use` holds the `Triggers on "..."` clause, plus any
  `Do NOT activate on "..."` exclusion. The runtime appends it to
  `description` in the skill listing
  (<https://code.claude.com/docs/en/skills.md>), so routing — the runtime's
  own listing match, any routing table a user keeps in their `CLAUDE.md`, and
  the roster in `docs/skills.md` — matches exact keywords from here;
  `check-plugin-manifests.py` Rule 13e fails on any drift between
  `when_to_use` and the roster row.
- **Hard budget: the folded `description` must stay ≤ 1,024 characters** (see
  [`RUNTIME_CONSTRAINTS.md` §5](../../RUNTIME_CONSTRAINTS.md)). The runtime
  truncates longer descriptions; `when_to_use` shares the listing budget as
  its tail, so trimming description prose is still what protects the
  triggers. Trim prose, never triggers.
- The frontmatter `Triggers on "..."` clause is the **sole** source of trigger
  keywords. Do NOT duplicate them in an in-body `- Triggers:` bullet — that
  bullet was retired (#591) because the two copies drifted; frontmatter is the
  single source of truth.
- Use a multi-line `>` block for a description or clause longer than one
  line; use inline text for a short one (see `strikes/SKILL.md`).

**Optional runtime fields** (each documented at
<https://code.claude.com/docs/en/skills.md>; add one only when its condition
holds):

- `disable-model-invocation: true` — the skill runs only when the user types
  `/praxis:<name>`: the model never auto-loads it from `when_to_use`, and
  `Skill(...)` cannot reach it
  ([`RUNTIME_CONSTRAINTS.md` §2](../../RUNTIME_CONSTRAINTS.md)). Use it for
  user-only commands — `strike`, `strikes`, and `reset-strikes` declare it.
  Never add it to a skill another skill chains into via `Skill(...)`
  (`worktree-merge-cleanup`, `codex-review-wrap`; issue #163).
- `allowed-tools` — tools the skill's steps may run without a permission
  prompt during the invoking turn. List only what the steps actually run, as
  specific `Bash(cmd *)` prefixes rather than bare `Bash`, and never a
  mutation (`gh pr merge`, `git push`, `gh issue create`) — those must keep
  prompting. `retrospect`, `codex-review-wrap`, and `merge-briefing` are the
  instances.
- `user-invocable: false` and `disallowed-tools` exist as well; no praxis
  skill uses them.

**If the skill wraps an external CLI, calls `AskUserQuestion`, or delegates to
another skill via `Skill(...)`,** add the runtime-verification fields after the
initial description fields:

```yaml
verified-against-runtime: true
runtime-verified-at: YYYY-MM-DD
runtime-verified-note: "<cli-name> <version> — one-line observed behavior"
```

### Step 3: Design Trigger Keywords

Trigger keywords are the phrases users type (or say) that route to this skill.
The runtime matches them against the skill listing — `description` with
`when_to_use` appended (see
[`RUNTIME_CONSTRAINTS.md` §5](../../RUNTIME_CONSTRAINTS.md)); a user may
additionally keep a routing table in their own `CLAUDE.md`.

**Principles:**
- **Specific over generic.** "recover cmux" beats "recover" — the generic form
  collides with unrelated skills.
- **Cover the Korean variants.** If users will describe the task in Korean,
  add the Korean form. Example: "크래시 복구", "세션 살려야".
- **Cover the negation gap.** Phrases that look like a trigger but shouldn't be:
  "strike a balance", "strike that" → these must NOT match `strike`.
  Add exclusion notes to `when_to_use` when collisions are plausible.
- **3–6 keywords is the target range.** Fewer leaves gaps; more creates false
  positives.

**Language policy — body vs literals.** Body prose (Overview, When to Use,
Process steps, tables) is written in English across the praxis corpus. Korean
belongs in exactly two places: trigger keywords in the frontmatter
`when_to_use` (the "Cover the Korean variants" principle above), and literal
strings the skill must match or emit verbatim — quoted user utterances, CLI/UI
output, and `AskUserQuestion` option labels. `merge-briefing` quoting the
progress signals `"계속"` / `"진행"` and `codex-review-wrap`'s `"취소"` option
label are the pattern: the literal stays in its source language because
translating it would change what the skill matches or displays. Do not write
section prose in Korean, and do not translate a literal into English.

### Step 4: Choose Sections

Every SKILL.md must include **Overview**, **When to Use**, and **Process**.
Add the others when relevant:

| Section | Include when |
| --------- | ------------- |
| `## Overview` | Always |
| `## When to Use` | Always |
| `## The Iron Law` | Skill has non-negotiable invariants (recovery, destructive ops) |
| `## Process` | Always — numbered steps, one logical op per step |
| `## Error Handling` | Skill calls external CLIs, APIs, or cmux |
| `## Limitations` | Known gaps the user might hit |
| `## Architecture` | Multiple execution paths (modes, distribute variants) |

**Step granularity:** each step is one logical operation. Avoid "do A and also
B" in one step — split. Every step should leave the system in a coherent,
inspectable state.

#### Progressive disclosure: split large bodies into `references/`

When a skill's body grows past **~15KB**, stop growing the single file and
split it:

- **`SKILL.md` stays the spine** — frontmatter, Overview, When to Use, the
  decision flow (step list, execution order, gates that route between steps),
  and a one-paragraph summary per moved step.
- **`references/*.md` hold the per-step detail** — procedures, tables, worked
  examples, prompt templates. One file per coherent group of steps (2–6
  files), not one per heading.
- **Link at the point of use.** Every summary in the spine links the reference
  file that holds its full procedure, and the Overview carries a reference
  map, so the reader loads detail only when a step actually runs.
- **Move text, don't rewrite it.** A split is a refactor: normative rules keep
  their wording; fix only what the move breaks (heading levels, relative
  links). Every rule in the old body must survive in the spine or a reference.

Precedents: `retrospect` (#687) — an ~18KB spine over six reference files
(four stage references plus appendices and a report template) — and
`codex-review-wrap` (#1181), whose Step 5 detail lives in three
`references/step5-*.md` files. Note that
`scripts/check-plugin-manifests.py`'s runtime-metadata gate scans
`references/*.md` alongside `SKILL.md`, so moving an `AskUserQuestion` or
external-CLI surface into a reference file does not exempt the skill from the
verification-metadata requirement.

### Step 5: Understand Host Differences

The same SKILL.md ships to every platform under `manifests/platforms/` that
lists `skills/` — Claude Code, Codex, and Cursor today. The table covers the
two hosts the `runtime-verified-note` entries in this repo were recorded on;
no skill carries a Cursor verification, so treat Cursor as unverified rather
than identical to either column:

| Aspect | Claude host | Codex host |
| -------- | ------------- | ------------ |
| Invocation | `Skill("praxis:<name>")` or `/praxis:<name>` | `Skill("praxis:<name>")` |
| `{{ARGUMENTS}}` | Populated from the slash command argument string | Populated from Skill args |
| `Skill(...)` delegation | Supported for most skills | Supported for most skills |
| `AskUserQuestion` | Supported, max 4 options | Supported, max 4 options |
| `Bash` cwd | Resets between separate Bash calls | Same behavior |
| File `Write` | Supported | Sandbox-restricted; verify with `git status` after |

**Critical cross-host constraint:** never call `Skill("codex:review")` from
inside any skill on either host — it declares `disable-model-invocation: true`
and always fails. Invoke the codex CLI binary directly via `Bash` instead,
mirroring the `codex-review-wrap` skill's Step 4 pattern.

**`Bash` cwd reset trap:** a `Bash` call does not persist `cd` across calls.
To change directory and run a command, chain them: `cd <path> && <command>`,
or pass `cwd` as part of the same Bash invocation. Never assume the previous
Bash call's directory is still active.

### Step 6: Respect Runtime Constraints

Read [`RUNTIME_CONSTRAINTS.md`](../../RUNTIME_CONSTRAINTS.md) before finishing
the spec. The three constraints that bite most often:

| Constraint | What to do |
| ------------ | ----------- |
| `AskUserQuestion.options` max 4 items | Surface top 3 + "취소"; put the full list in the question body text |
| `Skill(...)` rejects `disable-model-invocation: true` skills | Use the binary directly (see Step 5) |
| `Bash` cwd resets between calls | Chain with `&&` or use absolute paths |

### Step 7: Verify Before Merging

Any skill that wraps an external CLI, calls `AskUserQuestion`, or delegates via
`Skill(...)` **must** complete a live round-trip before the PR is merged.

1. Invoke the skill in a real Claude Code session.
2. Confirm the trigger keywords route correctly.
3. Confirm `AskUserQuestion` renders with the expected options.
4. Add `verified-against-runtime: true` + `runtime-verified-at` to frontmatter.
5. Include a `verified:` line in the commit body (see CONTRIBUTING.md) —
   required for any commit that introduces or significantly revises a skill spec.

Skills that do not match any of the three conditions in CONTRIBUTING.md
"Rule: verify before publishing" may skip the live round-trip, but must still
have a reviewer read-through.

### Step 8: Register the Skill

1. Add the skill to the tables in `AGENTS.md` and `docs/skills.md`
   (`check-plugin-manifests.py` Rule 13 fails until both list it).
2. Run `./scripts/check-plugin-manifests.py` — new skill directories are
   picked up automatically by the build script, but the check confirms no
   packaging drift.

### Step 9: Open the PR

Follow the standard praxis PR workflow:
- Title: `feat(skill): <what the skill does>` (≤ 50 chars)
- Body in Korean (praxis repo convention; verify the target repo's CONTRIBUTING.md for cross-repo work); `Closes #<issue-number>`
- Include the `verified:` line in the commit body if Step 7 applies

## Failure Modes

| Failure | Cause | Fix |
| --------- | ------- | ----- |
| Skill not invoked by routing | Trigger keywords absent from the `when_to_use` `Triggers on` clause, or pushed past the listing budget by a long `description` | Put them in `when_to_use` and keep `description` to what the skill does |
| `Skill(...)` call fails silently | Target skill uses `disable-model-invocation: true` (both hosts) | Call the underlying binary directly |
| `AskUserQuestion` call rejected before tool runs | Options array > 4 items — JSON schema rejects the call | Truncate to 3 + "취소" |
| Codex worker produces empty `git diff` | Sandbox write restriction | Add `git status` check + claude fallback re-dispatch |
| Trigger collides with unrelated skill | Keyword too generic | Make the keyword more specific; add exclusion note |

## Examples

### Minimal skill (no external CLI)

Verbatim from [`skills/strikes/SKILL.md`](../strikes/SKILL.md) — the
canonical short-inline-description instance. If the two drift, that file
wins; update this excerpt.

```markdown
---
name: strikes
description: Show the current session's strike count (0-3) and the list of recorded violation reasons.
when_to_use: Use when the user types "/strikes", "strike status", "몇 진", "check strikes".
disable-model-invocation: true
---

# Praxis Strike Status
...
```

### Skill with multi-line description and runtime verification (schematic)

The shape below is **schematic** — every `<...>` is a placeholder, and the
field values are not copied from any real skill. For a real instance of the
folded description plus the three runtime-verification fields, read
[`skills/cmux-recover-sessions/SKILL.md`](../cmux-recover-sessions/SKILL.md)
directly rather than trusting a transcription that can drift.

```markdown
---
name: <skill-name>
description: >
  <Multi-line description folded with `>` — what the skill does and any
  priority notes.>
when_to_use: >
  Triggers on "<keyword1>", "<keyword2>", "<Korean-keyword>".
  Do NOT activate on "<colliding-phrase>".
verified-against-runtime: true
runtime-verified-at: <YYYY-MM-DD>
runtime-verified-note: "<cli-name> <version> — one-line observed behavior"
---
```
