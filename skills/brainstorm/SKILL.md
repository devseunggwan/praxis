---
name: brainstorm
description: >
  Diamond Model brainstorming — diverge ideas without judgment, then converge with quantified evaluation.
  Wraps deep-interview for requirements clarification.
  Triggers on "brainstorm", "브레인스토밍", "아이디어", "ideate", "what if", "explore options".
---

# Brainstorm

## Overview

Brainstorming expands possibilities before narrowing them down. Unlike planning (HOW) or deep-interview (WHAT exactly), brainstorming explores the WHAT IF space.

**Core principle:** Diverge fully before converging. Never judge during idea generation.

**Delegates to:** OMC `architect` agents (multi-lens ideation), `analyst` agent (clustering), `critic` agent (evaluation)

## The Iron Law

```
NO JUDGMENT DURING DIVERGENT PHASE. NO SELECTION WITHOUT EVALUATION.
```

If you haven't completed Phase 1 (Diverge), you cannot evaluate or select ideas.
If you haven't completed Phase 3 (Evaluate), you cannot recommend an idea.

## When to Use

- Direction is unclear — don't know what approach to take
- Exploring possibilities for a new feature, architecture, or tool
- Stuck on a problem and need fresh perspectives
- Multiple valid approaches exist and need structured comparison
- User says "brainstorm", "아이디어", "what if", "explore options"

**Use this ESPECIALLY when:**
- "Just go with the obvious approach" feels tempting
- Only one option seems to exist (usually means insufficient exploration)
- The problem domain is unfamiliar

## The Diamond Model

```
Phase 1: DIVERGE              Phase 2-3: CONVERGE
   ╱  ideas expand    ╲           ╱  evaluate  ╲
  ╱   no judgment      ╲         ╱   rank       ╲
 ╱    quantity first    ╲       ╱    select      ╲
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        open wide              narrow to best
```

## Process

### Phase 0: Context Gathering (Automatic)

Before any ideation, gather relevant context silently:

1. Run `explore` agent (haiku): scan codebase for related patterns, constraints, prior art
2. Identify technology stack, existing conventions, and hard constraints
3. If user provided a topic, search for similar solved problems in the codebase

**Rule:** Gather codebase facts BEFORE generating ideas. Ideas grounded in reality are better than ideas from vacuum.

### Phase 1: Diverge — "Yes, And"

Generate as many ideas as possible. **NO judgment. NO filtering. NO evaluation.**

**Multi-Lens Technique** (run 3-5 `architect` agents in parallel):

| Lens | Perspective | Guiding Question |
|------|------------|-----------------|
| **Analyst** | Data/logic-driven | "What approaches are logically possible given our constraints?" |
| **Contrarian** | Opposite viewpoint | "What if we did the exact opposite? What if we intentionally made it worse?" |
| **Analogist** | Cross-domain | "How do other domains solve similar problems?" |
| **Minimalist** | Remove complexity | "What if there were no constraints? What's the simplest possible version?" |
| **User** | End-user perspective | "What does the actual user want? What would delight them?" |

Each lens generates 3-5 ideas → 15-25 candidates total.

**Rules during Phase 1:**
- Every idea is valid (no "that won't work")
- Build on others' ideas ("yes, and...")
- Wild ideas welcome (they often seed practical solutions)
- Quantity over quality at this stage

### Phase 2: Cluster & Enrich

1. Group similar ideas into clusters (3-7 clusters typical)
2. Name each cluster with a clear theme
3. Identify combinable ideas across clusters (SCAMPER: Combine)
4. Fill gaps: "What perspective is missing?"
5. Synthesize hybrid ideas from promising combinations

### Phase 3: Evaluate & Rank

Score each candidate on a 4-axis matrix:

| Axis | Weight | Description |
|------|--------|-------------|
| **Impact** | 35% | How much value does it create or how well does it solve the problem? |
| **Feasibility** | 30% | Can we build this with current tech, resources, and time? |
| **Novelty** | 20% | How different is this from existing approaches? |
| **Risk** | 15% | What's the downside if it fails? (inverse: lower risk = higher score) |

Scoring: 1-5 per axis → weighted average → rank.

Delegate evaluation to OMC `critic` agent (opus) for rigorous, unbiased scoring.

### Phase 4: Output

Present results in structured format:

```
## Brainstorm Results: {topic}

### Context
{codebase findings, constraints identified in Phase 0}

### Top Ideas (ranked)
1. **[Idea Name]** — Score: X.X/5
   - What: one-sentence description
   - Why it scores high: key strengths
   - Sketch: rough implementation approach
   - Risk: main concern

2. ...

### Honorable Mentions
- Ideas that scored well but narrowly missed top ranks

### Discarded (with reasons)
- Ideas that were evaluated and rejected, with brief rationale

### Recommended Next Step
```

### Phase 5: Execution Bridge

After presenting results, offer next steps:

1. **Deep Interview** → Clarify requirements for the top idea
2. **Plan** → Create implementation plan for a selected idea
3. **Refine** → Run another brainstorm round with narrower scope
4. **Compare** → Deep-dive trade-off analysis of top 2-3 ideas

## Modes

| Mode | Flag | Behavior |
|------|------|----------|
| **Interactive** (default) | none | User participates in each phase, adds their own ideas |
| **Solo** | `--solo` | Agent runs all phases autonomously, presents final results |
| **Workshop** | `--workshop` | 5 parallel architect agents for maximum idea diversity |

## OMC Agent Delegation

| Phase | Agent | Model | Role |
|-------|-------|-------|------|
| Phase 0 | `explore` | haiku | Codebase context gathering |
| Phase 1 | `architect` x 3-5 | sonnet | Multi-lens idea generation (parallel) |
| Phase 2 | `analyst` | sonnet | Clustering and synthesis |
| Phase 3 | `critic` | opus | Quantified evaluation |

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "I already know the best approach" | That's convergent thinking. Diverge first, then verify. |
| "Only one option makes sense" | Insufficient exploration. Run more lenses. |
| "Too simple for brainstorming" | Simple problems often have non-obvious better solutions. |
| "No time to brainstorm" | Bad direction costs more time than 15 minutes of ideation. |
| "Let me evaluate as I generate" | Mixing divergence and convergence kills creativity. Separate them. |
| "The first idea is good enough" | First idea = most obvious idea. Obvious ≠ best. |

## Red Flags — STOP

If you catch yourself:
- Dismissing ideas during Phase 1 ("that won't work")
- Jumping to implementation before completing evaluation
- Evaluating only 1-2 ideas instead of the full set
- Skipping lenses because "we already have enough ideas"
- Selecting an idea without scoring it against the matrix
- Combining Phase 1 and Phase 3 (generating and judging simultaneously)

**ALL of these mean: STOP. Return to the current phase's rules.**

## Integration

**Pipeline position:**
```
[brainstorm] → [deep-interview] → [plan] → [autopilot/ralph]
  WHAT IF       WHAT exactly       HOW        DO
```

**Previous step:** None (entry point for open-ended exploration)
**Next step:** `deep-interview` (for requirements clarification) or `omc-plan` (if requirements are already clear)

**OMC delegation:**
- `architect` agents: multi-lens ideation (Phase 1)
- `analyst` agent: clustering and synthesis (Phase 2)
- `critic` agent: quantified evaluation (Phase 3)
- `explore` agent: codebase context (Phase 0)
