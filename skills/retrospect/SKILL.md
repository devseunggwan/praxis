---
name: retrospect
description: >
  Session retrospect — analyze current Claude Code session against CLAUDE.md rules,
  identify friction patterns and root causes, propose context-appropriate improvement
  actions, then execute after user approval.
  Triggers on "retrospect", "what went wrong", "session review",
  "session improvement", "what was the issue", "improve".
---

# Retrospect

## Overview

Repeated friction wastes cycles across sessions. Unexamined pain stays unresolved.

**Core principle:** ALWAYS analyze root cause before proposing any action.
Symptom-level fixes (e.g., "remember to do X") miss the underlying pattern.

**Pipeline:** `Load → Analyze → Report/Approve → Execute` (4 stages)

**Delegates to:** OMC `tracer` agent (causal pattern analysis), `analyst` agent (pattern clustering)

## The Iron Law

```
NO ACTION WITHOUT ROOT CAUSE ANALYSIS FIRST.
PATTERN ≠ ROOT CAUSE. SYMPTOM ≠ ROOT CAUSE.
REPEATED PATTERN + MEMORY = FAILED REMEDY. ESCALATE.
TRACER + ANALYST CALLS ARE MANDATORY, NOT OPTIONAL.
```

If you haven't completed Stage 2 (Analyze), you cannot propose actions.
"It happened because X" is a symptom. "X happened because of missing rule Y / unclear trigger Z / absent skill W" is a root cause.

## When to Use

Use at the END of a working session to extract learnings:

- Session had repeated tool retries or direction changes
- User gave corrections mid-session ("no, don't do that")
- A task took significantly longer than expected
- Workflow steps were skipped or out of order
- User expressed frustration or redirected multiple times

**Use this ESPECIALLY when:**
- The same mistake happened more than once in the session
- You feel "I should have done that differently"
- A rule in CLAUDE.md was violated — even once
- A new workflow pattern emerged that isn't captured anywhere

## The Four Stages

You MUST complete each stage before proceeding to the next.

### Stage 1: Load Calibration Standard

**Before scanning the conversation:**

1. **Read CLAUDE.md** — load all rules, behavioral guidelines, and workflow requirements
   - Global: `$CLAUDE_CONFIG_DIR/CLAUDE.md`
   - Project: `CLAUDE.md` in cwd (if exists)
   - Key sections: Mandatory Rules, Behavioral Rules, Workflow rules

2. **Identify rule categories** to scan against:
   - Workflow discipline (Issue-Driven Workflow, Planning Before Implementation)
   - Evidence-Based Delivery (No "Trust Me" completions)
   - Atomic Commits + PR Lifecycle
   - Mandatory Testing (unit + functional)
   - Code Review Before Commit
   - Error Recovery Before Asking
   - Communication conventions

3. **Set the calibration frame**: For each rule category, form a question — e.g.,
   "Did the session violate 'Planning Before Implementation'? Were there 3+ step tasks that skipped plan mode?"

### Stage 2: Analyze Conversation

**Pre-scan: Quick friction event identification** — scan the conversation for up to 5 friction events (user corrections, retries, skipped steps, stalls) BEFORE calling agents. This provides the input for agent calls.

**Early exit**: If pre-scan finds 0 friction events, skip agent calls and exit with "No patterns found. ✅" — do not call agents with empty input.

**MANDATORY AGENT CALLS — when pre-scan finds 1+ friction events, MUST call sequentially (analyst depends on tracer output):**

1. **tracer agent** (causal chain analysis) — call FIRST:
   `Agent(subagent_type="oh-my-claudecode:tracer", model="sonnet")`
   - Input: friction events identified from pre-scan
   - Output: causal chains with confidence scores
   - Do NOT skip this call. "I can analyze this myself" is a Red Flag.

2. **analyst agent** (pattern clustering) — call AFTER tracer completes:
   `Agent(subagent_type="oh-my-claudecode:analyst", model="sonnet")`
   - Input: friction events + tracer causal chains (from step 1)
   - Output: clustered patterns with root causes

**Then refine using agent outputs:**

> **Scope:** Scan the most recent 50 turns, or back to the last session boundary.
> Stop after identifying 5 distinct friction events — clustering (step 6) handles de-duplication.
> If session history is not accessible, use the user's verbal summary as input to steps 3–8.

3. **Refine friction events with agent outputs** — merge pre-scan events with tracer/analyst results:
   - Add any new friction events the agents identified that pre-scan missed
   - Update causal chains using tracer confidence scores
   - Drop false positives that agents ruled out
   - Final list: up to 5 distinct friction events with causal chains attached

4. **Map each event to a CLAUDE.md rule** (or gap):
   - Which rule was applicable?
   - Was it followed, violated, or simply absent?
   - Quote or paraphrase the specific moment

5. **Find root cause** for each pattern:

   ```
   Symptom:   "Claude retried the same tool 3 times"
   Pattern:   "Error recovery loop"
   Root cause: "No diagnostic step between retries — violated Error Recovery Before Asking rule"

   Symptom:   "Implementation started before plan was approved"
   Pattern:   "Premature execution"
   Root cause: "Task had 4 steps but plan mode was not entered — violated Planning Before Implementation"
   ```

6. **Cluster patterns** — are multiple events the same root cause?
   If 3+ events share a root cause → HIGH priority

7. **Scan MEMORY.md for repeat patterns** (2-hop deterministic scan):
   a. Read MEMORY.md index (single file read) — extract all feedback entry titles and file paths
   b. For each finding's root cause, identify candidate matches from index titles (concept-level, not keyword)
   c. Read each candidate feedback file to confirm semantic match (same root cause, not just similar keywords)
   d. Only mark `repeat=true` if root cause is semantically identical
      - Example: "workflow skip" in index + "workflow violation" in finding = match
      - Example: "commit" matching both "atomic commit" and "pre-commit hook" = NOT auto-match, read file to confirm
   e. `repeat_count` = number of distinct feedback files with matching root cause
   f. If match found with existing resolution action (issue/hook already created): mark as `resolved=true`

8. **Auto-assign action type** based on escalation ladder:

   | Condition | Action Type | Rationale |
   |-----------|-------------|-----------|
   | New pattern (structural root cause, likely to recur) | memory | First occurrence — capture for future reference |
   | Repeat (in MEMORY.md, 1-2x) | GitHub issue | Memory alone failed — need systemic fix |
   | Repeat (3x+) | hook or skill | Multiple memory entries = enforcement gap |
   | Missing rule (new) | CLAUDE.md draft | No rule exists for this pattern |
   | Missing rule + Repeat | CLAUDE.md draft + GitHub issue | Missing rule caused repeat — add rule + compliance issue |
   | Tool friction | GitHub issue | Tool improvement needed |
   | One-off mistake (situational cause, unlikely to recur) | note only | No persistent action needed |

   **Distinguishing "New pattern" vs "One-off mistake":**
   - **New pattern**: root cause is structural (missing rule, absent skill, unclear workflow) → likely to recur in future sessions
   - **One-off mistake**: root cause is situational (context loss, typo, unusual edge case) → unlikely to recur under normal conditions
   - When uncertain, default to `memory` (safer to capture than to miss)

   ⚠️ **BLOCKED unless justified**: If `repeat=true`, the action type CANNOT be `memory`.

   **Escape hatch**: If `repeat=true` AND `resolved=true` (existing issue/hook resolution already exists for this feedback), `note only` is allowed. In this case, include a sentence in the report confirming that the existing resolution is still effective.

### Stage 3: Report + Approval

**Present findings in a structured table with escalation context:**

```
## Retrospect Report — {session_date}

| # | Pattern | Root Cause | Rule | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|---------|------------|------|---------|------------------------|-----------|----------|
| 1 | {pattern} | {root_cause} | {rule_ref} | {Yes(Nx)/No} | {action1} [+ {action2}] | {why_composite_or_single} | HIGH/MED/LOW |
...

No patterns found: "This session followed all CLAUDE.md rules. ✅"
```

**Action type baseline comes from Stage 2 escalation ladder**, but Stage 3 MUST explicitly evaluate all five action types per finding and select 1–2 composite actions.

> **Exception — one-off mistakes**: If Stage 2 classified the finding as `note only` (situational root cause, unlikely to recur), skip the evaluation below entirely. No persistent action is created; the finding appears in the report as acknowledged only.

**For each finding (except one-off), evaluate ALL five action types before selecting:**

| Action Type | When to Choose | Skip If |
|-------------|---------------|---------|
| **MEMORY.md feedback** | New pattern (1st occurrence, repeat_count=0), individual learning | repeat=true (memory is BLOCKED) |
| **GitHub issue** | Systemic fix needed (tool/skill implementation), repeat pattern (1–2×) | One-off mistake, purely local insight |
| **CLAUDE.md draft** | Explicit rule gap exists, cross-project scope needed | Existing rule already covers this pattern |
| **Skill idea note** | Repeat pattern needs enforcement mechanism, manual recall is insufficient | Single memo is sufficient, no recurring trigger |
| **Hook code** | Repeat (3x+) requiring automated enforcement; manual recall has repeatedly failed | Fewer than 3 repeats; skill idea or rule is sufficient |

**Selection matrix — three axes to determine compound vs. single action:**

| Axis | Signal → Action |
|------|----------------|
| **Repeat count** | 0× → `memory` (first occurrence); 1–2× → `issue` (memory blocked — repeat=true); 3×+ → `skill` or `hook` (enforcement gap) |
| **Scope** | Cross-project impact → `CLAUDE.md draft`; single-project → `MEMORY.md` |
| **Gap type** | Rule violated → `memory` (reinforce); rule absent → `CLAUDE.md draft` (fill gap); no enforcement → `skill idea` |

> **Axis precedence: Repeat-count is the highest-priority axis.** When `repeat=true`, the Scope and Gap type axes cannot override to `memory` — the repeat-count constraint (issue / skill / hook) always wins. Apply Scope and Gap type only to determine additional actions alongside the repeat-count result.

**Compound action is the default for HIGH-priority findings.** A single `memory` action is acceptable only when the rationale for skipping all other types is explicitly stated in the `Rationale` column.

**Before approval, explain each action's concrete plan:**

For each finding, present:
1. **What will be created** (file path, issue title, hook name, or CLAUDE.md rule text)
2. **Why this action type** (escalation rationale — e.g., "Already recorded 3x in MEMORY.md")
3. **How it will be verified** (what check confirms it works)

Example (single action — repeat pattern):
> Finding #2: Workflow step skipped (4th occurrence)
> - **Proposed Actions**: GitHub issue
> - **Rationale**: Already recorded 3x in MEMORY.md. Memory alone has failed. Structural fix required.
> - **What will be created**: issue — `feat(hook): add external-repo commit guard`
> - **Verify**: issue URL returned + `gh issue view` confirms existence

Example (compound action — rule gap + repeat):
> Finding #1 (HIGH): Hasty interpretation without verification (ambiguous signal → worst-case conclusion, 3 occurrences)
> - **Proposed Actions**: `CLAUDE.md draft` + `GitHub issue`
> - **Rationale**: Rule absent + 3× repeat → fill the rule gap (CLAUDE.md draft) and track enforcement compliance (GitHub issue); matches Stage 2 ladder: "Missing rule + Repeat"
> - **What will be created**:
>   - CLAUDE.md draft: new rule requiring a disconfirmation check before concluding from ambiguous signals
>   - issue — `feat(retrospect): enforce falsify-first check on ambiguous signal interpretation`
> - **Verify**: CLAUDE.md draft shown to user for approval + issue URL returned

**Then ask for approval per item using AskUserQuestion:**

```
For each finding, user selects:
  ✅ Execute now  |  ⏭ Skip  |  🕐 Defer (create note only)
```

Do NOT execute any action until user approves.

### Stage 4: Execute

**"note only" items require no execution** — they appear in the completion report as acknowledged but need no persistent artifact.

For each approved action:

1. **MEMORY.md feedback** → Write to `$CLAUDE_CONFIG_DIR/projects/.../memory/` with proper frontmatter
   - Type: `feedback`
   - Include: rule, why, how to apply
   - Update `MEMORY.md` index

   **⚠️ MANDATORY: Duplicate check before creating any memory file:**

   **Precondition:** This check applies ONLY when the finding's action type is `memory` (new pattern). If Stage 2 already marked `repeat=true` and escalated to issue/hook/CLAUDE.md, skip this check — the escalation ladder takes precedence over merge.

   a. Reuse Stage 2 Step 7's repeat scan results — if a finding matched an existing memory but was NOT escalated (i.e., it's a genuinely new sub-pattern), that file is the merge target
   b. If no Stage 2 match: scan MEMORY.md index for entries with overlapping root cause or topic (concept-level, not keyword)
   c. For each candidate, read the existing memory file and compare:
      - Same root cause / principle → **merge**: append new context (examples, How to apply items) to the existing file. If merge makes this the 2nd+ occurrence, re-evaluate whether action type should escalate per Stage 2 Step 8
      - Related but distinct principle → **create new file** (genuinely different insight)
   d. **Never create a new file when the insight is a specific instance of an existing general rule** — add it as a numbered sub-item instead
   e. After merge or create, update MEMORY.md index (update description if merged, add new line if created)

2. **GitHub issue** → Use project's issue creation skill or `gh issue create`
   - Title: Conventional Commits format (per project convention)
   - Body: per project convention, with background + task list

3. **CLAUDE.md draft** → Write proposed rule addition as a markdown block
   - ⚠️ `$CLAUDE_CONFIG_DIR/CLAUDE.md` is **global scope** — changes affect every project
   - Present the draft to user for review BEFORE any edit
   - Apply only with explicit approval ("yes, add this rule")

4. **Skill idea note** → Write to `{current_project}/.omc/plans/retrospect-skill-idea-{slug}.md`
   - `{current_project}` = `$CLAUDE_PROJECT_DIR` or `git rev-parse --show-toplevel`
   - Include: problem, proposed skill trigger, pipeline sketch

5. **Hook code** → For enforcement-level actions (repeat 3x+):
   a. Write hook script to `.claude/hooks/` or appropriate location
   b. Present the hook code to user for review
   c. Explain how to register in `.claude/settings.json` (show the exact JSON entry)
   d. Use AskUserQuestion: "Hook을 settings.json에 등록할까요?" (✅ 등록 / ⏭ 파일만 유지 / 🕐 나중에)
   e. If approved: Edit `.claude/settings.json` to register the hook
   f. If skipped/deferred: leave the hook file in place and provide manual registration instructions

6. **Verification** — For each executed action, verify the artifact:

   | Artifact | Verification |
   |----------|-------------|
   | MEMORY.md feedback (new) | File exists + MEMORY.md index updated |
   | MEMORY.md feedback (merged) | Existing file updated (diff shown) + MEMORY.md index description updated if needed |
   | GitHub issue | `gh issue view {url}` returns valid data |
   | Hook code | Script file exists + settings.json registration confirmed (dry-run varies by hook type — no generic check) |
   | CLAUDE.md draft | Diff shown to user + explicit approval received |
   | Skill idea note | File exists in `.omc/plans/` |

   Report verification results in the completion table.

7. **Completion report:**

```
## Actions Executed

| # | Action | Result |
|---|--------|--------|
| 1 | MEMORY.md feedback added | ✅ {file_path} |
| 2 | GitHub issue created | ✅ {url} |
...

Session learnings captured. Next session will benefit from these improvements.
```

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "It was a one-off mistake, not worth capturing" | If it happened once, it can happen again. Capture it. |
| "I know the root cause, I'll just note the symptom" | Symptoms recur. Root causes get fixed. Write the root cause. |
| "MEMORY.md is already long, skip this" | Length doesn't matter. Missing the pattern is the cost. |
| "The session was mostly fine, nothing to retrospect" | Even 1 friction event is worth 2 minutes to capture. |
| "I'll do this later" | Later never comes. Do it at session end while context is fresh. |
| "This is a tool issue, not a Claude issue" | Tool + Claude interaction is within scope. Both can be improved. |

## Red Flags — STOP

If you catch yourself:

- Proposing actions before completing Stage 2 analysis
- Writing "root cause: Claude forgot to X" without tracing WHY the forgetting happened
- Adding a MEMORY.md entry that just repeats the CLAUDE.md rule verbatim (no new insight)
- Creating a GitHub issue for every minor friction (low-ROI noise)
- Skipping the approval step and executing actions directly
- Editing `$CLAUDE_CONFIG_DIR/CLAUDE.md` without presenting the draft first — this is global config, affects every project
- Proposing `memory` for a pattern that already exists in MEMORY.md (MUST escalate instead)
- Skipping tracer/analyst agent calls ("I can analyze this myself")
- Generating artifacts without verification ("issue created" without showing URL)
- Creating a new memory file without checking existing entries for overlap (MUST merge into existing when root cause matches)
- **Proposing MEMORY.md feedback as the only action when the same rule was violated 3+ times** — this ignores memo's proven limits; enforcement mechanisms (skill, hook, rule) MUST be evaluated alongside memory
- **Proposing MEMORY.md feedback as the only action when the finding is a rule gap (rule absent)** — gaps are not filled by memos; CLAUDE.md draft or skill idea MUST be considered

**ALL of these mean: STOP. Return to Stage 2.**

## Quick Reference

| Stage | Key Activity | Success Criteria |
|-------|-------------|-----------------|
| **1. Load** | Read CLAUDE.md, form scan questions | Rule categories identified |
| **2. Analyze** | Scan conversation, map to rules, find root cause | Root cause (not symptom) for each pattern |
| **3. Report** | Present table, collect approval per item | User approved at least 1 item (or confirmed 0 findings) |
| **4. Execute** | Run approved actions, verify artifacts | Completion report with links/paths + verification results |

## Error Handling

| Stage | Failure | Action |
|-------|---------|--------|
| Stage 1 (load) | CLAUDE.md not found (project or global) | Proceed with global defaults; flag the missing file in the report |
| Stage 2 (analyze) | Session history not accessible | Fall back to the user's verbal summary as input to steps 3–8 |
| Stage 2 (analyze) | No friction events found | Exit with "No patterns found. ✅" — do not fabricate findings |
| Stage 2 (analyze) | MEMORY.md scan failed (file not accessible) | Treat all findings as new patterns (repeat=false). Flag scan failure in report |
| Stage 2 (analyze) | MEMORY.md is empty | Normal processing — all findings are new patterns |
| Stage 2 (analyze) | tracer/analyst call failed | Fall back to manual analysis. Flag agent failure in report. Warn about reduced root cause quality |
| Stage 3 (report) | User rejects all findings | Capture the rejection itself as a feedback signal for future retrospects |
| Stage 4 (execute) | MEMORY.md write fails | Report the path error; never silently drop the feedback |
| Stage 4 (execute) | GitHub issue creation fails | Fall back to saving a note in `.omc/plans/` for later manual creation |

## Integration

**Entry point:** End of a working session, or after a particularly rough workflow experience
**Exit point:** Completion report shown → optionally chain to next session's `turbo-setup`

**OMC delegation:**
- `tracer` agent: causal chain analysis for complex friction patterns
- `analyst` agent: cluster multiple friction events into root causes
- Project's issue creation skill: GitHub issue creation in Stage 4
