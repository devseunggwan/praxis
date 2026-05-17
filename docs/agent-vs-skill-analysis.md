# Agent vs Skill Split — Tradeoff Analysis

> **Status**: Analysis-only. No migrations proposed for this PR.
> Step 2 (actual split) is tracked as a separate future issue.
>
> **Evidence base**: direct reads of `skills/*/SKILL.md`, `RUNTIME_CONSTRAINTS.md §3`,
> `manifests/platforms/codex.json`, `plugins/praxis/.codex-plugin/plugin.json`, and
> live `gh api` calls to `EveryInc/compound-engineering-plugin` (49 agents, 37 skills,
> as of 2026-05-17).

---

## Section 1 — Definitions

### Skill (invoked via `Skill(skill="praxis:<name>")`)

A skill injects its `SKILL.md` as **context into the current conversation window**. The
model that processes the skill share the parent context: prior turns, loaded files,
in-flight tool outputs, and any other context accumulated in the session. This means:

- **Low isolation**: the skill sees everything the parent saw, and its outputs remain
  visible to the parent after it returns.
- **Low overhead**: no separate model invocation; no context window wipe; no startup
  latency.
- **Context window cost**: skill content adds tokens to the shared window and stays
  there for the session duration.
- **Codex and Claude both support** the `Skill(...)` tool. Praxis already ships skills
  to both platforms via `manifests/platforms/{claude,codex}.json`.

### Agent (invoked via `Agent(subagent_type=...)`)

An agent runs in an **isolated context window**. It receives only what the parent
explicitly passes in, reasons independently, and returns a result. The parent context
is not polluted by the agent's intermediate reasoning.

- **High isolation**: fresh context per invocation; internal chain-of-thought stays
  inside the agent.
- **Higher overhead**: new model invocation; must receive all necessary context
  explicitly; outputs must be serialized back to the parent.
- **Context window savings**: the parent window gains only the result, not the agent's
  full reasoning trace.
- **Codex agent support**: the praxis Codex plugin schema (`plugins/praxis/.codex-plugin/plugin.json`)
  exposes only a `"skills"` key — no `"agents"` key. Adding agents to the Codex
  packaging surface would require a schema extension that is currently unverified.
  The Claude plugin (`plugin.json`) similarly has no `"agents"` key. Both platforms
  support agents at the runtime level, but the praxis manifest build pipeline
  (`scripts/build-plugin-manifests.py`) has no agent output stage.

### The key split criterion (RUNTIME_CONSTRAINTS.md §3)

> `Agent(subagent_type="praxis:<name>")` resolves only against registered subagent
> types. Every praxis entry under `skills/*/SKILL.md` is a *skill*, not a subagent,
> and is reachable only via `Skill(skill="praxis:<name>")`.

This means: **converting a skill to an agent is a breaking change for all callers**.
Any skill a user invokes today as `/praxis:<name>` would need to be reinvoked via
`Agent(...)` syntax — or the agent would need to be registered as a named subagent
type, which is a Claude Code platform concern outside the plugin's control.

---

## Section 2 — praxis Current Status

All 12 praxis entries are skills. None are agents.

| # | Skill | Character (one line) |
|---|-------|---------------------|
| 1 | `cmux-browser` | Thin CLI wrapper — dispatches `cmux browser` commands; minimal reasoning depth |
| 2 | `cmux-delegate` | Orchestrator — builds context packet, spawns independent session; medium reasoning |
| 3 | `cmux-recover-sessions` | Interactive recovery wizard — reads `.jsonl` files, prompts user for layout; medium |
| 4 | `cmux-resume-sessions` | Snapshot restore — reads JSON snapshot, restores workspaces; low reasoning depth |
| 5 | `cmux-save-sessions` | State capture — runs `cmux` list commands, writes JSON; minimal reasoning |
| 6 | `cmux-session-manager` | Lifecycle orchestrator — status dashboard, cleanup, reorganize; medium |
| 7 | `codex-review-wrap` | Deep reasoning wrapper — worktree selection, premise verification, flip detection; **high** |
| 8 | `recover-sessions` | tmux-backend recovery wizard; medium reasoning; sibling to `cmux-recover-sessions` |
| 9 | `reset-strikes` | Mechanical reset + optional reflection gate; minimal reasoning |
| 10 | `retrospect` | Deep causal analysis — delegates to OMC `tracer` + `analyst` agents internally; **high** |
| 11 | `strike` | Mechanical counter — runs `strike-counter.sh` and reports verbatim; minimal |
| 12 | `strikes` | Read-only status — runs `strike-counter.sh status`; minimal |

Source: direct reads of each `skills/*/SKILL.md` (`description:` frontmatter + Iron Law sections), 2026-05-17.

---

## Section 3 — Reference Plugin Comparison: compound-engineering

### Layout (verified via `gh api`, 2026-05-17)

```
gh api repos/EveryInc/compound-engineering-plugin/contents/plugins/compound-engineering/agents \
  --jq '.[].name' | wc -l
# → 49

gh api repos/EveryInc/compound-engineering-plugin/contents/plugins/compound-engineering/skills \
  --jq '.[].name' | wc -l
# → 37
```

37 skills (user-facing slash commands) + 49 agents (internally spawned personas) = 86 total entries.

### How compound-engineering uses agents

From `README.md`:
> "Skills install natively via Codex; for the full experience with specialized review
> and research agents, run the companion Bun converter after install."

From agent file headers (`ce-adversarial-reviewer.agent.md`, `ce-correctness-reviewer.agent.md`,
`ce-architecture-strategist.agent.md` — read directly via `gh api`):

```yaml
# ce-adversarial-reviewer.agent.md
name: ce-adversarial-reviewer
description: Conditional code-review persona, selected when the diff is large (>=50
  changed lines) or touches high-risk domains like auth, payments, data mutations,
  or external APIs.
model: inherit
tools: Read, Grep, Glob, Bash, Write
color: red
```

```yaml
# ce-correctness-reviewer.agent.md
name: ce-correctness-reviewer
description: Always-on code-review persona. Reviews code for logic errors, edge cases,
  state management bugs, error propagation failures, and intent-vs-implementation mismatches.
model: inherit
tools: Read, Grep, Glob, Bash, Write
color: blue
```

### The heuristic compound-engineering uses

Three observable patterns from the 86-entry corpus:

1. **User-invokable entry point → Skill**. Every slash command (`/ce-code-review`,
   `/ce-plan`, `/ce-debug`, etc.) is a skill. Skills are the public API. Users never
   directly invoke agents.

2. **Specialized sub-persona → Agent**. All 49 agents are specialized reviewers,
   researchers, or analyzers that a skill spawns in parallel. The `ce-code-review`
   skill dispatches `ce-adversarial-reviewer`, `ce-correctness-reviewer`,
   `ce-security-reviewer`, etc. as parallel sub-agents, collects JSON results, and
   merges them. Agents have no user-facing invocation path.

3. **Context isolation = context savings at scale**. A code review with 10 parallel
   agents is only viable if each agent gets an isolated window. Running 10 reviewer
   personas sequentially inside the parent window would compound token cost 10×.
   Isolation is the enabling constraint, not a design preference.

The compound-engineering split criterion: **if a task runs in parallel with siblings
of similar reasoning depth, isolate it as an agent; otherwise it is a skill**.

---

## Section 4 — praxis Candidate Split

Evaluation criteria per skill:
- **(a) Reasoning depth**: does this skill require long, iterative internal reasoning?
- **(b) Context window cost**: does the skill's reasoning trace pollute the parent window?
- **(c) Write vs read intent**: does the skill mutate external state (cmux, files, GitHub)?
- **(d) Invocation frequency**: how often is it called in a typical session?
- **(e) Reusability**: is this skill called by other skills internally?

| Skill | (a) Depth | (b) CW cost | (c) Write? | (d) Freq | (e) Reuse | Verdict | Rationale |
|-------|-----------|-------------|-----------|----------|-----------|---------|-----------|
| `cmux-browser` | low | low | yes (browser) | low | no | **keep-as-skill** | Thin CLI wrapper. No reasoning isolation benefit. Mutates browser state — must be sequential anyway. |
| `cmux-delegate` | medium | medium | yes (spawns session) | low | no | **keep-as-skill** | Orchestrator skill that *itself* spawns agents; converting it to an agent adds a layer with no benefit. |
| `cmux-recover-sessions` | medium | medium | yes (cmux) | very low | no | **keep-as-skill** | Interactive wizard; requires user Q&A mid-flow via `AskUserQuestion`. Agents cannot surface `AskUserQuestion` to the parent. |
| `cmux-resume-sessions` | low | low | yes (cmux) | very low | no | **keep-as-skill** | Simple restore; low context cost. No isolation benefit. |
| `cmux-save-sessions` | low | low | yes (file write) | low | no | **keep-as-skill** | Mechanical state capture. Minimal reasoning. No isolation benefit. |
| `cmux-session-manager` | medium | medium | yes (cmux) | low | no | **keep-as-skill** | Interactive (Phase 2/3 require user confirmation mid-flow). `AskUserQuestion` constraint applies. |
| `codex-review-wrap` | **high** | **high** | no (analysis) | medium | no | **split-into-both** | The heavy reasoning phases (premise verification, flip detection, sibling cross-check) generate large internal traces. A `codex-review-wrap` skill that dispatches a `codex-review-analysis` agent for the deep reasoning step would contain the trace cost. However, see Migration Cost note below. |
| `recover-sessions` | medium | medium | yes (tmux) | very low | no | **keep-as-skill** | Same as `cmux-recover-sessions` — interactive wizard with mid-flow `AskUserQuestion`. |
| `reset-strikes` | low | low | yes (state file) | very low | no | **keep-as-skill** | Mechanical. 3-line execution body. No reasoning trace to isolate. |
| `retrospect` | **high** | **high** | yes (memory/hooks) | low | no | **split-into-both** | Explicitly delegates to OMC `tracer` + `analyst` agents already. The skill body is already acting as an orchestrator. Converting the top-level to dispatch a `retrospect-analyzer` agent for Stage 2 (pattern clustering) would reduce parent window pollution during long sessions where retrospect runs at end-of-session. |
| `strike` | low | low | yes (state file) | low | no | **keep-as-skill** | Mechanical. No reasoning. |
| `strikes` | low | low | no (read only) | low | no | **keep-as-skill** | Pure status read. Minimal. |

### The `AskUserQuestion` constraint is the critical blocker

`RUNTIME_CONSTRAINTS.md §1` documents that `AskUserQuestion` has a hard cap of 4
options. More importantly for this analysis: an **agent cannot surface
`AskUserQuestion` to the parent session's user**. Interactive skills — those that
must prompt the user mid-flow — cannot be converted to agents without redesigning
the interaction model (pre-collecting all inputs before dispatch, or returning a
"needs input" sentinel).

This eliminates `cmux-recover-sessions`, `cmux-resume-sessions`, `cmux-session-manager`,
and `recover-sessions` from agent candidacy without redesign.

---

## Section 5 — Trade-off Summary

### Context savings if N skills migrate

| Migration scenario | Skills converted | Estimated CW savings per call |
|-------------------|-----------------|-------------------------------|
| No split (status quo) | 0 | 0 |
| Partial: `retrospect` analysis agent | 1 | Medium — Stage 2 reasoning trace (typically 2k–8k tokens) isolated |
| Partial: `codex-review-wrap` analysis agent | 1 | Medium — premise verification + flip detection trace isolated |
| Both above | 2 | Medium × 2 — most impactful without interactive-flow redesign |
| Full split of all 12 | 8–10 | Marginal — most remaining skills have minimal reasoning traces |

Realistically, 10 of the 12 skills have low-to-medium reasoning depth. The aggregate
context savings from converting them to agents would be small relative to the migration
cost.

### Migration cost

1. **Manifest build pipeline extension**: `scripts/build-plugin-manifests.py` currently
   has no agent output stage. `manifests/platforms/{claude,codex}.json` have no
   `"agents"` key. Adding agent packaging would require new manifest schema design and
   build script changes.

2. **Codex platform uncertainty**: `plugins/praxis/.codex-plugin/plugin.json` exposes
   only `"skills": "./skills/"`. Whether Codex supports an `"agents"` key in its
   plugin schema is **unverified** — the compound-engineering Codex plugin (`gh api
   repos/EveryInc/compound-engineering-plugin/contents/plugins/compound-engineering/.codex-plugin/plugin.json`)
   contains no `"agents"` key either. compound-engineering's README explicitly notes
   that agents require "a companion Bun converter after install" for Codex, suggesting
   agents are not natively packaged in the Codex plugin format.

3. **Breaking caller change**: per `RUNTIME_CONSTRAINTS.md §3`, `Agent(subagent_type=
   "praxis:<name>")` does not resolve praxis skills. A converted entry would require
   callers to switch from `Skill(skill="praxis:<name>")` to `Agent(...)` — a
   breaking API change for any workflow that delegates to praxis skills.

4. **Interactive flow redesign**: 6 of 12 skills use `AskUserQuestion` mid-flow.
   Converting these to agents requires either pre-collecting all inputs (changing the
   UX contract) or implementing a multi-round parent↔agent protocol not currently
   supported by the Claude Code `Agent(...)` tool.

### Codex agent support status (verified)

```json
// plugins/praxis/.codex-plugin/plugin.json
{
  "name": "praxis",
  "skills": "./skills/"
  // No "agents" key
}
```

```json
// manifests/platforms/codex.json
{
  "platform": "codex",
  "outputs": [
    { "kind": "plugin", "path": "plugins/praxis/.codex-plugin/plugin.json",
      "plugin_overrides": { "skills": "./skills/" } },
    { "kind": "marketplace", "path": ".agents/plugins/marketplace.json", ... }
  ]
}
```

No agent output stage exists. The `"skills"` key is the only output in both the
plugin and marketplace manifests.

---

## Section 6 — Recommendation

### Verdict: **Partial split — two candidates only, deferred until manifest pipeline is ready**

#### Do not split now

- 10 of 12 skills are mechanical (low reasoning depth) or interactive (mid-flow
  `AskUserQuestion`). Splitting them yields negligible context savings at non-trivial
  migration cost.
- The manifest pipeline has no agent output stage. Shipping agents before this is
  ready means hand-editing generated files — violating the project's own "do not edit
  generated files directly" rule.
- Codex agent packaging is unverified. compound-engineering's own Codex plugin does
  not expose agents natively, requiring a separate install step. Introducing this gap
  in praxis would degrade the Codex experience.

#### Two future candidates (when manifest pipeline supports agents)

1. **`retrospect`** — Stage 2 pattern clustering already delegates to OMC `tracer` +
   `analyst` agents. Extracting a `retrospect-analyzer` agent for this phase would
   reduce parent window contamination during end-of-session retrospects. Complexity is
   self-contained (no `AskUserQuestion` in Stage 2).

2. **`codex-review-wrap`** — The premise verification + flip detection reasoning is
   the most context-expensive phase in praxis. A `codex-review-analysis` sub-agent
   for Steps 5b–5d would contain the trace. The outer skill retains user interaction
   (Step 2 `AskUserQuestion` for worktree selection).

#### Concrete next steps

1. **Now (Step 2 issue)**: extend `scripts/build-plugin-manifests.py` to support an
   `"agents"` output stage and verify Codex compatibility. This unblocks any future
   agent conversion without breaking the manifest contract.

2. **After manifest support lands**: convert `retrospect` Stage 2 to dispatch a
   `retrospect-analyzer` agent as a proof-of-concept. Measure parent CW delta in
   practice before committing to `codex-review-wrap`.

3. **Do not convert interactive skills** (`cmux-recover-sessions`, `cmux-session-manager`,
   `recover-sessions`) unless the Claude Code platform adds support for agents
   surfacing `AskUserQuestion` to the parent session — a feature that does not exist
   as of 2026-05-17.

#### Why not follow compound-engineering's model directly

compound-engineering uses agents for **parallel specialist sub-personas** spawned by
a single orchestrator skill. praxis skills are each standalone user-facing commands —
none of the 12 currently spawn sibling agents that would benefit from parallel
isolation. The compound-engineering pattern applies to praxis only if praxis grows
multi-agent orchestration inside a single skill (e.g., `retrospect` dispatching three
parallel analysis agents). That is a future design option, not a current requirement.
