---
name: writing-praxis-skill
description: >
  Guide for authoring a new praxis SKILL.md — template usage, SRP, trigger
  keyword design, frontmatter conventions, and Claude/Codex host differences.
  Triggers on "new skill", "write skill", "add skill", "skill template",
  "skill spec", "스킬 작성", "새 스킬".
---

# writing-praxis-skill

## Overview

A new praxis skill needs a SKILL.md that the Claude Code plugin runtime can
parse and route correctly. Without a consistent structure, the runtime silently
misroutes or truncates the description, and contributors have to reverse-engineer
the pattern from 13+ existing specs.

**Core principle:** one skill = one responsibility. If a skill needs a second
trigger phrase to describe a second job, split it into two skills.

## When to Use

- Creating a new praxis skill from scratch
- Reviewing an existing SKILL.md for structural compliance
- Onboarding a contributor who will add a skill
- Triggers: "new skill", "write skill", "add skill", "skill template", "skill spec", "스킬 작성", "새 스킬"

## Process

### Step 1: Copy the Template

```bash
cp skills/SKILL.md.tmpl skills/<skill-name>/SKILL.md
```

Open the new file and replace every `<...>` placeholder. Do not leave any
placeholder text in the committed file.

### Step 2: Fill in the Frontmatter

```yaml
---
name: <skill-name>         # kebab-case; must match the directory name
description: >
  <One-line description.>
  Triggers on "<keyword1>", "<keyword2>", "<Korean-keyword>".
---
```

**Rules:**
- `name` must exactly match the directory name under `skills/`.
- `description` must fit within 500 characters — the runtime truncates beyond that.
- Always end `description` with a `Triggers on "..."` clause so the routing
  table in CLAUDE.md can reference exact keywords.
- Use multi-line `>` block for descriptions that include trigger keywords; use
  inline text for short single-line descriptions (see `strike/SKILL.md`).

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
The CLAUDE.md `Skill & Agent Routing` table maps them.

**Principles:**
- **Specific over generic.** "recover cmux" beats "recover" — the generic form
  collides with unrelated skills.
- **Cover the Korean variants.** If users will describe the task in Korean,
  add the Korean form. Example: "크래시 복구", "세션 살려야".
- **Cover the negation gap.** Phrases that look like a trigger but shouldn't be:
  "strike a balance", "strike that" → these must NOT match `strike`.
  Add exclusion notes to the description when collisions are plausible.
- **3–6 keywords is the target range.** Fewer leaves gaps; more creates false
  positives.

### Step 4: Choose Sections

Every SKILL.md must include **Overview**, **When to Use**, and **Process**.
Add the others when relevant:

| Section | Include when |
|---------|-------------|
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

### Step 5: Understand Host Differences

The same SKILL.md runs on both the Claude Code plugin (Claude host) and the
Codex plugin (Codex host). The two hosts differ in how they expose the skill:

| Aspect | Claude host | Codex host |
|--------|-------------|------------|
| Invocation | `Skill("praxis:<name>")` or `/praxis:<name>` | `Skill("praxis:<name>")` |
| `{{ARGUMENTS}}` | Populated from the slash command argument string | Populated from Skill args |
| `Skill(...)` delegation | Supported for most skills | `disable-model-invocation: true` skills fail — use the underlying binary directly |
| `AskUserQuestion` | Supported, max 4 options | Supported, max 4 options |
| `Bash` cwd | Resets between separate Bash calls | Same behavior |
| File `Write` | Supported | Sandbox-restricted; verify with `git status` after |

**Critical Codex constraint:** never call `Skill("codex:review")` from inside
a skill — it declares `disable-model-invocation: true` and always fails with
an error. Invoke the codex CLI binary directly via `Bash` instead, mirroring
the `codex-review-wrap` skill's Step 4 pattern.

**`Bash` cwd reset trap:** a `Bash` call does not persist `cd` across calls.
To change directory and run a command, chain them: `cd <path> && <command>`,
or pass `cwd` as part of the same Bash invocation. Never assume the previous
Bash call's directory is still active.

### Step 6: Respect Runtime Constraints

Read [`RUNTIME_CONSTRAINTS.md`](../../RUNTIME_CONSTRAINTS.md) before finishing
the spec. The three constraints that bite most often:

| Constraint | What to do |
|------------|-----------|
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
5. Include a `verified:` line in the commit body (see CONTRIBUTING.md).

Skills that are pure documentation (no CLI calls, no tool calls) may skip the
live round-trip, but must still have a reviewer read-through.

### Step 8: Register the Skill

1. Add the skill's trigger keywords to the routing table in the project's
   `CLAUDE.md` (or the root `CLAUDE.md` for global skills).
2. Run `./scripts/check-plugin-manifests.py` — new skill directories are
   picked up automatically by the build script, but the check confirms no
   packaging drift.

### Step 9: Open the PR

Follow the standard praxis PR workflow:
- Title: `feat(skill): <what the skill does>` (≤ 50 chars)
- Body in Korean; `Closes #<issue-number>`
- Include the `verified:` line in the commit body if Step 7 applies

## Failure Modes

| Failure | Cause | Fix |
|---------|-------|-----|
| Skill not invoked by routing | Trigger keywords missing from CLAUDE.md routing table | Add keywords to the routing table |
| Description truncated by runtime | `description` > 500 chars | Shorten; move detail into the body |
| `Skill(...)` call fails silently | Target skill uses `disable-model-invocation: true` | Call the underlying binary directly |
| `AskUserQuestion` renders only 2 options | Options array > 4 items | Truncate to 3 + "취소" |
| Codex worker produces empty `git diff` | Sandbox write restriction | Add `git status` check + claude fallback re-dispatch |
| Trigger collides with unrelated skill | Keyword too generic | Make the keyword more specific; add exclusion note |

## Examples

### Minimal skill (no external CLI)

```markdown
---
name: strikes
description: Show the current session's strike count (0-3) and the list of
  recorded violation reasons. Use when the user types "/strikes", "strike status",
  "몇 진", "check strikes".
---

# Praxis Strikes
...
```

### Skill with multi-line description and runtime verification

```markdown
---
name: cmux-recover-sessions
description: >
  Bulk recover Claude Code sessions after a crash, power loss, OOM kill, or reboot
  by scanning the .jsonl files Claude Code persists automatically.
  Triggers on "크래시 복구", "세션 살려야", "crash recovery".
verified-against-runtime: true
runtime-verified-at: 2026-04-10
runtime-verified-note: "cmux 1.2.3 — new-workspace accepted; list-workspaces format confirmed"
---
```
