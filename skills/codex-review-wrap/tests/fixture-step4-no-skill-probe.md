# Fixture: Step 4 MUST NOT probe Skill("codex:review") (issue #237)

Demonstrates that `/praxis:codex-review-wrap` must route straight to the
`codex-companion.mjs` invocation on every call, including the first round
of every session — without first attempting `Skill("codex:review")` as a
"primary path before fallback".

## Background

`/codex:review` declares `disable-model-invocation: true`. The Skill tool
returns the following constant error on every invocation, in every
session, in every environment:

```
Skill codex:review cannot be used with Skill tool due to disable-model-invocation
```

The failure is not session-dependent, not retry-able, and not
environment-gated. Probing it wastes a turn every call and produces a
visible error in the transcript.

## Expected behaviour

### Round 1 — fresh session

```
user: /praxis:codex-review-wrap

[Step 1] git worktree list --porcelain → 1 worktree
[Step 2] single worktree — skip disambiguation
[Step 3] target: /path/to/repo (branch: feature-xyz)

[Step 4]
  4a: resolve codex-companion.mjs from installed_plugins.json → /path/to/codex/scripts/codex-companion.mjs
  4b: cd /path/to/repo
      node /path/to/codex/scripts/codex-companion.mjs review
```

**NOT expected** (anti-pattern):

```
[Step 4]
  attempt Skill("codex:review") → fails with disable-model-invocation
  fall back to codex-companion.mjs
```

### Round 2+ — same session, repeated invocation

Identical to Round 1. The model must not have "learned" anything between
rounds — the directive in SKILL.md is the only state needed.

### Fallback path (4a — companion not found)

The only `Skill(...)` call legitimately reachable from Step 4 is the
`oh-my-claudecode:code-reviewer` fallback, and only when:

1. `installed_plugins.json` is missing, OR
2. The codex entry is absent from the manifest, OR
3. The resolved path to `codex-companion.mjs` does not exist on disk.

In that case, `AskUserQuestion` surfaces `oh-my-claudecode:code-reviewer`
/ `Manual` / `Cancel`. If the user picks `oh-my-claudecode:code-reviewer`,
`Skill("oh-my-claudecode:code-reviewer")` is invoked. This carveout
assumes the target skill does not also declare `disable-model-invocation`
— verify against the installed version's frontmatter before relying on
it. If that assumption proves false, the user can re-select `Manual` or
`Cancel` from the same `AskUserQuestion` surface; no second probe is
needed.

## Validation checklist

| Requirement | Met? |
| --- | --- |
| Step 4 opens with a hard MUST NOT directive against `Skill("codex:review")` | Yes — SKILL.md line ~128 |
| Failure message reproduced inline so the model sees what would happen | Yes — verbatim block in Step 4 |
| Constant-property framing (not session-dependent) | Yes — "not retry-able, not environment-gated" |
| `oh-my-claudecode:code-reviewer` fallback path remains intact | Yes — Step 4a unchanged |
| `runtime-verified-at` and note reflect the hardening | Yes — updated to 2026-05-16 with issue #237 reference |
