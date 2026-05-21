---
name: retrospect
description: >
  Session retrospect — analyze current Claude Code session against global `~/.claude/CLAUDE.md` rules,
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
- A rule in global `~/.claude/CLAUDE.md` was violated — even once
- A new workflow pattern emerged that isn't captured anywhere

## The Four Stages

You MUST complete each stage before proceeding to the next.

### Stage 1: Load Calibration Standard

**Before scanning the conversation:**

1. **Read global and project rule files** — load all rules, behavioral guidelines, and workflow requirements
   - Global: `$CLAUDE_CONFIG_DIR/CLAUDE.md` (i.e., `~/.claude/CLAUDE.md`)
   - Project: `AGENTS.md` in cwd (if exists; in praxis, `AGENTS.md` is canonical — the project symlink in cwd points to it)
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

### Stage 1.5: MEMORY.md Hygiene Pass (Detect-only)

Stage 1.5 runs unconditionally between Stage 1 (Load) and Stage 2 (Analyze). Its purpose is to surface latent defects in MEMORY.md itself — defects that the session-scoped friction scanner (Stage 2) cannot see because they accumulate across sessions. Findings produced here flow into the same downstream pipeline (Stage 2 categorization → Stage 2.5 gates → Stage 3 approval → Stage 4 execution). Stage 1.5 itself does NOT mutate MEMORY.md.

**Scope:** scan MEMORY.md index + a bounded subset of `feedback_*.md` files.

**Cap (mandatory):** scan up to **5 feedback files per invocation**. Persist a cursor at `.omc/state/retrospect-hygiene-cursor.json` recording (a) the timestamp of the last successful pass, (b) the list of file paths scanned, and (c) the next-batch pointer (path of the next un-scanned feedback file in sorted order). Subsequent retrospects resume from the cursor — full corpus coverage amortizes across multiple sessions. When the full corpus has been scanned once, rotate the cursor to restart from the most-recently-modified entries.

**Three detection signals (each becomes a finding with `category: memory_hygiene`):**

1. **Stale reference** — the entry cites an artifact that no longer matches reality:
   - File path absent: entry references `<path>` that does not exist (verify with `test -e`)
   - CLI flag absent: entry cites `<binary> --<flag>` but `<binary> --help` no longer documents it
   - CLAUDE.md line shifted: entry cites `~/.claude/CLAUDE.md` line `N` whose content no longer matches the quoted excerpt (verify with `grep -n` of the cited excerpt)
   - Skill / hook removed: entry references a skill name or hook path that no longer exists in the plugin manifest

   Detection method: parse the entry's body for `\bgrep -n\b`-able tokens (file paths, CLI flag patterns `--[a-z-]+`, CLAUDE.md line citations); run the verification command; record failures as stale.

2. **Contradiction** — two entries provide semantically opposed guidance under overlapping triggers:
   - Entry A says "always X under trigger T"; Entry B says "never X under trigger T" — same T, opposite directive.
   - Detection method: concept-level pairwise comparison restricted to entries whose `hookKeywords` (or trigger tokens) overlap. Use LLM judgment for semantic opposition (no regex shortcut). Cap pairwise comparisons to the cursor-batch's 5 files × full-index check.

3. **Merge candidate** — two or more entries share a root-cause family but have not been merged:
   - Same principle restated with different examples (the merge-first policy at Stage 4 Action 1d should have collapsed them).
   - Detection method: read each entry's `description` field and root-cause prose; group by concept-level matching (same heuristic as Stage 4 Action 1 duplicate-check). When N ≥ 2 entries share a family AND none has been merged, emit a merge-candidate finding.

**Output — emit per-defect findings into the Stage 2 friction-event lane.**

Each Stage 1.5 finding has:
- `category[]: [memory_hygiene]` — single category at Stage 1.5; downstream gates may add tool/workflow when defect originates from a tool layer (e.g., manifest staleness from a removed hook).
- `Tool Layer: —` (default for memory_hygiene; the Stage 2 Layer E composition matrix carves out a `—` row for memory_hygiene the same way it does for behavioral).
- `Proposed Actions` (provisional, finalized at Stage 2.5):
  - Stale reference → `memory` (update entry inline) OR `claude_md_draft` (when the stale citation is itself a CLAUDE.md rule line that needs updating)
  - Contradiction → `memory + issue` (surface both entries via an issue for human resolution; merge in Stage 4 only after explicit decision)
  - Merge candidate → `memory` (route through Stage 4 Action 1 merge path; the merge IS the action)

**0-friction hygiene-only retrospect path** — when Stage 2 pre-scan finds zero friction events but Stage 1.5 produced ≥1 hygiene finding, the skill does NOT take the "No patterns found ✅" early-exit (Stage 2 line). Instead, proceed to Stage 2.5 → Stage 3 with the hygiene findings as the sole input. Emit a banner in Stage 3 report: `Hygiene-only retrospect — no friction events this session.`

**Stage 1.5 failure modes:**
- MEMORY.md not accessible → skip Stage 1.5 entirely; emit `<!-- retrospect:hygiene_skipped: index not accessible -->` trail line; proceed to Stage 2.
- Cursor file corrupt → reset cursor to most-recently-modified 5 files; log reset in completion report.
- Per-file scan failure (one of N files unreadable) → continue with remaining files; record per-file failure in the Hygiene Scan Trail section of Stage 3 report.

**Detect-only contract** — Stage 1.5 never writes to MEMORY.md, never deletes a feedback file, never edits the MEMORY.md index. All mutations route through Stage 4 (Action 1 merge path for merge candidates; Action 1 update path for stale references; Action 2 issue creation for contradictions). Inline mutation at Stage 1.5 is a Red Flag.

### Stage 2: Analyze Conversation

**Pre-scan: Symmetric scan (friction + successful patterns)** — scan the conversation BEFORE calling agents. Produces two lanes:

- **friction_events**: up to 5 friction events (user corrections, retries, skipped steps, stalls). This provides the input for agent calls.
- **successful_patterns**: up to 3 patterns that worked well this session. Each entry MUST include:
  - `pattern`: one line (e.g., "deep-dive 3-lane trace identified cross-lane contradiction via direct grep")
  - `evidence`: specific turn or artifact citation (mandatory — abstract reinforcement like "was helpful" or "responded fast" is FORBIDDEN)
  - `reinforce_action`: `memory` (capture as MEMORY.md entry; counted in Stage 2.5 distribution card `memory` total — see Stage 4 Action 1 for the origin-prefix convention) or `visualize_only` (display in Stage 3 Reinforced Patterns section, no persistent action)

**Pre-scan Categorization (Mandatory)** — every friction event identified in pre-scan MUST be tagged with `category[]: string[]` containing ≥1 of these enumerated values. Stage 2 progression to step 3 is BLOCKED until every event has at least one category label.

| Category | Signal examples (≥2 each) | Required `Tool Layer` (composition with step 4b) |
|----------|---------------------------|--------------------------------------------------|
| `behavioral` | "Claude가 확인 없이 결론 도출" / "한 PR에 여러 concern bundle" / "user가 동일 지적 반복" | none (Tool Layer = `—`) |
| `tool` | "gh CLI `--state all` 부재" / "MCP 응답 지연" / "Read 출력 truncation" / "kubectl flag 부재" | **mandatory**: one of `mcp` / `cli` / `builtin` / `skill` |
| `workflow` | "test 안 돌리고 PR" / "verify 단계 건너뜀" / "issue 안 만들고 브랜치" / "code review 생략" | optional: `skill` (when defect originates inside a skill's stage flow) |
| `spec-gap` | "이 상황을 다루는 규칙 부재" / "SKILL.md trigger 모호" / "global `~/.claude/CLAUDE.md`에 명시되지 않은 행동" | optional: `skill` (when rule gap is in a SKILL.md) |
| `memory_hygiene` | "stale 파일경로 / CLI flag / CLAUDE.md 라인 인용" / "두 entry가 동일 trigger 에서 contradict" / "merge candidates 미수렴" (Stage 1.5 origin) | none (Tool Layer = `—` by default; `skill` only when the stale citation references a specific skill/hook artifact) |
| `output_quality` | "PR review-rounds ≥3" / "sub-agent stream-killed + output<100ch used downstream" / "external comment with hypothesis markers, no falsification trace" (Stage 2.7 origin) | **mandatory** when PR/external-write artifact present: `cli` (gh-related) or `skill` (sub-agent); `—` only when MCP-via-behavioral path applies |

**Layer E ↔ step 4b composition matrix** (normative — referenced by step 4b and Stage 3 unified table):

- A friction event MAY carry multiple categories (e.g., `[workflow, tool]` when a workflow step skip was caused by a tool flag bug).
- When `tool` ∈ `category[]`, the event MUST also be classified into one of the 4 step-4b layers (`mcp` / `cli` / `builtin` / `skill`); the `Tool Layer` cell of the unified table cannot remain `—`.
- For `behavioral` only events, `Tool Layer` = `—`.
- For `workflow` or `spec-gap` events without a tool root cause, `Tool Layer` = `—`; if the workflow defect or rule gap originates within a skill, `Tool Layer` MAY be set to `skill` to enable step 4b downstream routing.
- For `memory_hygiene` events (Stage 1.5 origin), `Tool Layer` = `—` by default. When the stale citation points to a specific skill/hook artifact (e.g., a renamed hook script), `Tool Layer` MAY be set to `skill` so the finding compounds with a `skill` step-4b lane. `memory_hygiene` events do NOT require the mandatory `mcp/cli/builtin/skill` classification that `tool` events do.
- For `output_quality` events (Stage 2.7 origin), `Tool Layer` follows the audited surface: `cli` for `gh pr|issue` audits, `skill` for sub-agent substance audits, `—` for MCP-via-behavioral path (e.g., Slack `*_send_message` audit where the surface is the action discipline, not the MCP itself). Unlike the strict `tool` category, `output_quality` does not require the layer to be `mcp/cli/builtin/skill` when the audit surface is behavioral (evidence discipline in a comment body).

**Early exit**: If pre-scan finds 0 friction events, skip agent calls and exit with "No patterns found. ✅" — do not call agents with empty input. **Exception (Stage 1.5 carve-out)**: when Stage 1.5 produced ≥1 `memory_hygiene` finding, the early exit does NOT fire — proceed to Stage 2.5 → Stage 3 with the hygiene findings as the sole input, and emit the Stage 1.5 hygiene-only banner (see Stage 1.5 "0-friction hygiene-only retrospect path"). **Exception (Stage 2.7 carve-out)**: when the session transcript contains ≥1 Stage 2.7 audit trigger (PR / issue / Slack / Notion write — see Stage 2.7 trigger list), the early exit does NOT fire — Stage 2.7 must execute its post-hoc artifact audit so silent-pass quality failures (the exact case Stage 2.7 was designed to catch) are not skipped. If Stage 2.7 produces ≥1 `output_quality` finding, proceed to Stage 2.5 → Stage 3 with those findings as input; if Stage 2.7 silently skips (0 triggers detected after the carve-out check), fall through to the normal 0-friction "No patterns found" exit.

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

4. **Map each event to a global `~/.claude/CLAUDE.md` rule** (or gap):
   - Read the event's `category[]` from pre-scan and feed it into rule-mapping
   - Which rule was applicable?
   - Was it followed, violated, or simply absent?
   - Quote or paraphrase the specific moment
   - If `category[]` includes `spec-gap`, this map step often resolves as "rule absent" — fold that signal into step 5 root cause

4b. **Tool Friction Pass** — independently analyze tool/feature-level friction (cross-referenced by Layer E composition matrix above):

   This pass runs SEPARATELY from step 4. A friction event may match a rule violation (step 4) AND a tool defect (step 4b) — both are recorded. Per the Layer E composition matrix, every event with `tool` ∈ `category[]` MUST be classified into one of the 4 layers below; the unified-table `Tool Layer` cell of such an event cannot remain `—`.

   **Tool layers to scan (all 4):**

   | Layer | Examples | Friction signals |
   |-------|----------|-----------------|
   | `mcp` | any custom or third-party MCP server (data warehouse, observability, chat, infra, etc.) | Slow response, missing field, schema mismatch, timeout |
   | `cli` | `gh`, `kubectl`, `git`, plus project-specific CLIs | Missing flag/option, undocumented behavior, workaround needed |
   | `builtin` | Read/Edit/Bash/Grep/Glob, Agent, hooks | Environmental constraint, permission issue, output truncation |
   | `skill` | praxis / OMC / project-specific skills and subagents | Stage boundary unclear, trigger mismatch, prompt defect, wrong routing |

   **For each tool friction event, record:**
   - `tool_name`: specific tool (e.g., "gh CLI", "<plugin-name> MCP", "<skill-name> skill")
   - `layer`: mcp / cli / builtin / skill
   - `friction_type`: missing feature, design defect, documentation gap, performance issue, integration mismatch
   - `evidence`: the specific moment (quote or paraphrase)
   - `expected_behavior`: what should have happened
   - `proposed_fix_direction`: brief suggestion for upstream improvement

   **Representative friction examples (for calibration):**
   1. "gh CLI의 `--state all` 플래그가 없어서 open/closed를 각각 호출해야 했다" → layer: `cli`, friction_type: missing feature
   2. "MCP 응답 지연으로 3회 재시도 후 fallback 전략을 수동 구성했다" → layer: `mcp`, friction_type: performance issue
   3. "skill의 Stage 경계가 불명확해서 step을 건너뛰고 다음 stage로 넘어갔다" → layer: `skill`, friction_type: design defect
   4. "codex exec의 permission mode가 달라 파일 쓰기에 실패했다" → layer: `cli`, friction_type: integration mismatch
   5. "Read 도구의 출력 truncation으로 파일 끝부분을 놓쳤다" → layer: `builtin`, friction_type: design defect

   **Dedup rule (step 4 vs step 4b):**
   - If a friction event has BOTH a rule violation (step 4) AND a tool defect (step 4b), record it in BOTH places
   - Step 4 finding addresses the behavioral correction (what Claude should have done differently)
   - Step 4b finding addresses the tool improvement (what the tool should do differently)
   - The two findings may have different action types (e.g., step 4 → memory, step 4b → upstream feedback)

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

6b. **Cluster fold-back** — propagate cluster-level repeat signal to member events.

   Execute this pass AFTER step 7 completes (step 7 sets `event.repeat_count`; this step then computes `event.effective_repeat`):

   ```
   for each cluster (from step 6):
     cluster_event_count  = number of events in this cluster
     cluster_repeat_count = max(event.repeat_count for event in cluster) + (cluster_event_count - 1)
     # max MEMORY.md hit across cluster members + extra in-session occurrences beyond the first
   for each event in cluster:
     event.effective_repeat = max(event.repeat_count, cluster_repeat_count)
   ```

   For events that belong to no cluster (singleton): `event.effective_repeat = event.repeat_count`.

   **Why this matters**: step 7's MEMORY.md scan is event-level and independent per event. An event with no direct MEMORY.md match (repeat_count=0) that is clustered with repeat events inherits the cluster's accumulated signal — ensuring that all members of the same root-cause family receive a consistent action tier in step 8.

   **Example** (from the session that motivated this step): Cluster with 3 events; 2 have repeat_count=1, 1 has repeat_count=0.
   - `cluster_repeat_count = max(1, 1, 0) + (3-1) = 3`
   - Event with repeat_count=0 → `effective_repeat = max(0, 3) = 3` → escalates to "hook or skill", not "memory"

7. **Scan MEMORY.md for repeat patterns** (2-hop deterministic scan — MANDATORY, not skippable):

   This step is required for EVERY finding. Skipping it (e.g., "probably not in MEMORY.md", "I'd remember if I saw this before") is a Red Flag. Each finding MUST record a `memory_scan` field capturing the scan evidence before the finding can advance to step 8.

   a. Read MEMORY.md index (single file read) — extract all feedback entry titles and file paths
   b. For each finding's root cause, identify candidate matches from index titles (concept-level, not keyword)
   c. Read each candidate feedback file to confirm semantic match (same root cause, not just similar keywords)
   d. Only mark `repeat=true` if root cause is semantically identical
      - Example: "workflow skip" in index + "workflow violation" in finding = match
      - Example: "commit" matching both "atomic commit" and "pre-commit hook" = NOT auto-match, read file to confirm
   e. `repeat_count` = number of distinct feedback files with matching root cause
   f. If match found with existing resolution action (issue/hook already created): mark as `resolved=true`
   g. **Record `memory_scan` on every finding** — this field is load-bearing for Gate-5 (below).

      **Recording location**: emit the `memory_scan` block as an HTML comment immediately after the unified findings table in the Stage 3 report (one comment per finding, keyed by `finding #N:`). This pin keeps the field auditable by reviewers and parseable by downstream tooling without polluting the human-readable table.

      **Format**:

      ```
      <!-- memory_scan finding #1:
        scanned: true
        candidates_reviewed: [<file1.md>, <file2.md>, ...]   # empty list if index is empty
        matched: [<matched_file.md>]                          # empty list if no match
        repeat: true|false
        repeat_count: N
        resolved: true|false                                  # optional; omit if no resolution action found
      -->
      ```

      **Field requirements** — Gate-5 (Stage 2.5 Step 2) verifies only the **load-bearing** fields below; the rest are documentation-only metadata for human reviewers:
      - `scanned: true` — **Gate-5 verified** (proves step was executed, not skipped)
      - `candidates_reviewed` — **Gate-5 verified** (presence required; empty list = index was empty or 0 candidates after concept-level filter)
      - `repeat` and `repeat_count` — **Gate-5 verified** (presence required; values reflect step (e) results)
      - `matched` — documentation-only; SHOULD list every file confirmed in step (d) for human review, but Gate-5 does NOT verify its presence (the `repeat`/`repeat_count` pair already encodes this signal at the type level)
      - `resolved` — **optional** and NOT verified by Gate-5; emit only when `repeat=true` AND existing resolution action found in step (f)
      - When proposed action ∈ {`memory_create`, `memory_update`}: `memory_scan` MUST be populated — Gate-5 (Stage 2.5) will block Stage 3 if absent

8. **Auto-assign action type** based on escalation ladder. Use `event.effective_repeat` (from step 6b; equals `event.repeat_count` for singleton events) as the repeat signal for the Repeat-based rows below. Apply Repeat-based rows first; then apply Category-default rows below to override or compound when the event's `category[]` (from pre-scan, step 4) makes memory-only inappropriate even on first occurrence:

   | Condition | Action Type | Rationale |
   |-----------|-------------|-----------|
   | New pattern (structural root cause, likely to recur) | memory | First occurrence — capture for future reference |
   | Repeat (in MEMORY.md, 1-2x) | GitHub issue | Memory alone failed — need systemic fix |
   | Repeat (3x+) | hook or skill | Multiple memory entries = enforcement gap |
   | Missing rule (new) | global `~/.claude/CLAUDE.md` draft | No rule exists for this pattern |
   | Missing rule + Repeat | global `~/.claude/CLAUDE.md` draft + GitHub issue | Missing rule caused repeat — add rule + compliance issue |
   | Tool friction (step 4b finding) | upstream feedback | Tool improvement needed — issue in the tool's **backing repo** (resolve via Stage 4 Action 4 routing table; not always praxis) |
   | One-off mistake (situational cause, unlikely to recur) | note only | No persistent action needed |
   | **Category default — `tool`** | upstream feedback (compound with memory only when behavioral co-label exists) | step 4b backing-repo resolution; tool defect is not a Claude behavior issue, memory alone insufficient |
   | **Category default — `workflow`** | hook code OR skill idea (memory-alone NOT allowed even on first occurrence) | enforcement gap detected — workflow steps that get skipped need structural enforcement, not memo |
   | **Category default — `spec-gap`** | global `~/.claude/CLAUDE.md` draft OR skill idea (memory-alone NOT allowed) | rule absent — gaps are filled with rules, not memos |
   | **Category default — `behavioral`** | memory (default; compound with skill_idea or global `~/.claude/CLAUDE.md` draft when structural) | only category where memory-alone is acceptable; still subject to Gate-2 rationale schema in Stage 2.5 |

   **Distinguishing "New pattern" vs "One-off mistake":**
   - **New pattern**: root cause is structural (missing rule, absent skill, unclear workflow) → likely to recur in future sessions
   - **One-off mistake**: root cause is situational (context loss, typo, unusual edge case) → unlikely to recur under normal conditions
   - When uncertain, default to `memory` (safer to capture than to miss)

   ⚠️ **BLOCKED unless justified**: If `repeat=true`, the action type CANNOT be `memory`.

   **Escape hatch**: If `repeat=true` AND `resolved=true` (existing issue/hook resolution already exists for this feedback), `note only` is allowed. In this case, include a sentence in the report confirming that the existing resolution is still effective.

   ⚠️ **MUST — backing_repo declaration for upstream_feedback rows**: When `Proposed Actions` contains `upstream_feedback` (single or compound), the row's `Rationale` cell MUST include a `backing_repo: <owner/repo>` line resolved per Stage 4 Action 4's resolution table. The declaration is **load-bearing** — Stage 4 re-reads it as the routing decision and aborts on divergence.

   - Resolution source-of-truth (in priority order): plugin manifest `repository` field → MCP server git remote → dotfiles backing repo via symlink chain → project `AGENTS.md` feature-to-repo mapping.
   - Format: literal line `backing_repo: <owner>/<repo>` embedded in the Rationale cell via `<br>` separators. Example: `Rationale: tool defect in praxis distribution<br>backing_repo: <resolved-praxis-repo>`.
   - Unresolvable layer (`builtin`, or no upstream reachable per the Action 4 resolution table's `builtin` row): **remove `upstream_feedback` from the row's `Proposed Actions` set entirely**. If the row had compound actions (e.g., `memory, upstream_feedback`), retain the remaining ones (e.g., keep `memory` alone). If `upstream_feedback` was the sole action, re-derive the action via Stage 2 step 8's category-default rows (typically `skill_idea` for `tool` category, or `memory` if behavioral co-label exists). The escape-hatch state `note only` (from `repeat=true AND resolved=true`) is a separate construct and is NOT used here. Do NOT emit a placeholder `backing_repo`.
   - Ambiguous layer (resolution table's `Other / ambiguous` row): keep `upstream_feedback` but surface to user immediately at Stage 2; the user-supplied repo becomes the declared `backing_repo`. Stage 4 step 0 then re-resolves and may still divergence-prompt if the live re-resolution differs.
   - Hook-parsing safety: this is not a memory-only row, so the Stage 2.5 Gate-2 5-line schema does not apply. The `backing_repo:` line lives alongside the human rationale text without conflicting with the Gate-2 regex.

### Stage 2.7: Adaptive Post-hoc Artifact Audit

Stage 2.7 runs between Stage 2 (Analyze) and Stage 2.5 (Distribution Audit). Its purpose is to surface output-quality defects in artifacts the session produced — defects that the friction-event scanner (Stage 2) cannot see because they are *silent-pass* failures (no observable session friction).

**Adaptive trigger** — Stage 2.7 fires ONLY when the session transcript contains at least one of these artifact-write signals. With zero triggers, Stage 2.7 silently skips.

Trigger detection (scan the session's Bash invocations + MCP tool calls):
- `gh pr create | edit | merge | comment` Bash invocations
- `gh issue create | edit | comment | close | reopen` Bash invocations
- Slack MCP `*_send_message` / `*_update_message` tool calls
- Notion MCP `*_create_*` / `*_update_*` tool calls
- `external-write-falsify-check.mjs` PreToolUse hook fired AND the user approved the write (i.e., the write actually proceeded)

**0-trigger silent skip** — when scan finds zero triggers, Stage 2.7 emits a single trail line and exits:

```
<!-- retrospect:audit_skipped: no artifacts -->
```

The trail line is mandatory: it documents that Stage 2.7 ran and chose to skip, distinguishing "skipped intentionally" from "stage forgotten." Distribution card emits `output_quality: 0`.

**≥1-trigger audit fires** — execute three sub-audits in parallel; each may produce 0–N findings.

**Sub-audit 1 — PR mergeability** (Tool Layer: `cli`):

For each PR touched by `gh pr {create,edit,merge,comment}` in this session:
- Run `gh pr view <number-or-url> --json reviewRequests,reviews,state,mergeable,mergedAt`
- Emit a finding when ANY of:
  - `state == "CLOSED"` AND `mergedAt == null` (closed without merge — likely abandoned or rejected)
  - `len(reviews) >= 3` (≥3 review-rounds — high revision cost signals weak first-pass quality)
  - `mergeable == "CONFLICTING"` (stale branch left in conflict state)

Finding template:
- `category[]: [output_quality]` — single category; the `cli` surface is captured by `Tool Layer` below. Compounding with `tool` would trigger Gate-1 (Stage 2.5) which forbids `memory`-single for `tool` findings; output_quality first-occurrence is appropriately memory-single, so the `tool` co-category is omitted here. (Tool Layer still carries the surface signal.)
- `Tool Layer: cli`
- `Proposed Actions`: `memory` (single occurrence) or `memory, claude_md_draft` (when repeat ≥2; rule-gap on review-quality bar)

**Sub-audit 2 — Sub-agent output substance** (Tool Layer: `skill`):

For each sub-agent dispatch in the session (Agent tool calls):
- Scan the parent's continuation context for stream-killed indicators (`agentId`, `internal ID`, abrupt-output truncation marker, "status=completed" without a result body)
- Compute output length from the agent's final reported result
- Flag when ALL of:
  - Stream-killed indicator present OR output length < 100 characters
  - Parent agent invoked downstream tooling that referenced the sub-agent's output (i.e., the sub-output was actually *used*, not just discarded)

Finding template:
- `category[]: [output_quality]` — single category; the `skill` surface is captured by `Tool Layer`. Same rationale as Sub-audit 1: Gate-1 would reject `memory`-single for a `tool`-co-labeled finding, but first-occurrence sub-agent substance is correctly memory-single (a downstream-reliability lesson). Tool Layer below preserves the surface signal.
- `Tool Layer: skill`
- `Proposed Actions`: `memory` (capture as a downstream-reliability lesson) or `memory, skill_idea` (when repeat ≥2; sketch a verifier helper)

This sub-audit cross-references `feedback_agent_completion_verify_substance.md` — if a finding here matches that memory entry's family, the action MUST escalate per Stage 2.5 Gate-1 (repeat=true blocks memory-single).

**Sub-audit 3 — External comment evidence** (Tool Layer: `cli`):

For each external comment write — covers both create and update variants (`gh issue comment`, `gh pr comment`, Slack `*_send_message` / `*_update_message`, Notion `*_create_comment` / `*_update_*`). Update variants are included because edited comments can still introduce or retain hypothesis-language markers; auditing only creates would silently miss the same defect class in trigger sessions that only update prior content:
- Re-use the regex from `hooks/external-write-falsify-check.mjs` to scan the comment body POST-write
- Hypothesis-language markers without falsification trace: `might`, `could be`, `potential`, `is failing`, `아마`, `~인 듯` etc.
- Emit a finding when the comment body contains a hypothesis marker AND lacks any of:
  - `Falsification:` line
  - `Probe:` line with command + output
  - Direct evidence citation (file path + line number + quoted excerpt)

Finding template:
- `category[]: [output_quality, behavioral]` (behavioral because evidence discipline)
- `Tool Layer: cli` (gh-related) or `—` (Slack/Notion via MCP — Layer E composition matrix's `behavioral` row applies)
- `Proposed Actions`: `memory` (first occurrence) or `memory, hook_code` (when repeat ≥2; hook-level enforcement consideration)

**Distribution card field** — Stage 3 emits `- output_quality: N` where N counts findings whose `category[]` includes `output_quality`. Like `memory_hygiene`, this is a **category count**, not an action key; the underlying actions still fall under the 6 action-type slots.

**Stage 2.7 failure modes:**
- Transcript not accessible (e.g., session-resume after compaction) → Stage 2.7 emits `<!-- retrospect:audit_skipped: transcript unreadable -->` and skips
- `gh pr view` API failure for a specific PR → log per-PR error in Stage 3 report Audit Trail section; continue with remaining PRs
- Hypothesis-marker regex import failure (hook file missing or unreadable) → fallback to a built-in minimal regex (`(might|could|potential|아마)`) AND log the fallback in the report

**Detect-only contract** — Stage 2.7 never re-edits PRs, never deletes comments, never re-runs the offending tool. All mutations route through Stage 4 (Action 1 for memory entries, Action 2 for issues, Action 6 for hook code). Inline mutation at Stage 2.7 is a Red Flag.

### Stage 2.5: Action Distribution Audit

After Stage 2 completes (all findings have `category[]` labels and provisional `Proposed Actions`) and BEFORE Stage 3 begins, run the three gate checks below. Each finding has its own per-finding gate counter, reset at Stage 2.5 entry.

**Gate-1 (Categorical)** — for each finding whose `category[]` intersects {`tool`, `workflow`, `spec-gap`}, verify `Proposed Actions` ≠ `memory` (single, not compound).

If `memory` is the *only* action for such a finding → return that finding to Stage 2 step 4 (re-evaluate label correctness — Gate-1 violations are most often *mislabeling*: the event was actually behavioral but got tagged tool/workflow/spec-gap, or vice versa) AND step 8 (re-derive action with category-default rows applied).

**Gate-2 (Procedural)** — for each finding with `Proposed Actions = memory` (single, not compound, regardless of category), verify the `Rationale` cell matches **one** of the two accepted schemas (mixing is not allowed):

- **Schema A** (verbose): EXACTLY 5 lines, each matching `^not (issue|claude_md_draft|skill_idea|hook_code|upstream_feedback): .+$`. The 5 lines MUST cover all 5 non-memory action types (no duplicates, no missing keys).
- **Schema B** (dimension-tag, issue #285): 1–2 lines, each matching `^not-others: .+$`. The line(s) MUST encode why all non-memory actions were skipped via dimension tags (e.g., `not-others: repeat=0, rule_exists=yes, gateable=no, tool_defect=no`). No Schema A lines may appear alongside Schema B lines.

If absent or incomplete → return that finding to Stage 2 step 8 (re-evaluate with explicit per-action rationale enforcement).

**Gate-3 (Evidence Robustness)** — for each finding with `Proposed Actions` count = 2 (compound), verify three sub-conditions. Gate-1 and Gate-2 check whether the *form* of action assignment is correct; Gate-3 checks whether the *evidence* underneath each action is robust enough to justify it. Without this gate, category-default form-filling can produce a second action whose only purpose is to ask a question the first action already answered — pure structural redundancy that costs an upstream issue or a global `~/.claude/CLAUDE.md` draft.

Sub-conditions (all three must hold):

(a) **Per-action evidence pointer** — each of the 2 actions cites ≥1 explicit friction-event observation that supports it. Citations live as a sub-bullet inside the Rationale cell or as an inline `(observed: <event-id or one-line>)` reference. Form-filling actions with no concrete observation behind them are not justifiable on their own.

(b) **Sibling decision-coupling check** — if action B's outcome decides a question that action A's existence already presupposes (i.e., A applied option X, and B's purpose is to ask which of {X, Y} is correct), the actions are decision-coupled. The two actions cannot both be justified at the same time — applying A while B is still "asking" produces a contradiction. Resolution: keep the stronger-evidence action; demote the weaker to a **trigger condition** line in Stage 3 ("file new issue if observation Z appears"). Do not execute both.

(c) **Single-observation downgrade** — for each action backed by exactly 1 observation AND `repeat=false` (Stage 2 step 7 result), downgrade one tier per this table:

| Original action (in compound row) | Downgraded to | Rationale |
|-----------------------------------|---------------|-----------|
| `upstream_feedback` | `memory` | Single observation against an external party doesn't justify upstream-write cost; capture locally first |
| `issue` | `memory` | Single observation without repeat doesn't justify systemic-fix tracking; capture pattern first |
| `hook_code` | `skill_idea` | Single observation doesn't justify enforcement-code investment; sketch as a skill idea first |
| `claude_md_draft` (single-observation in this row) | `memory` | Single-observation rule additions risk over-fitting; capture as memory first, escalate if the pattern recurs. Note: a *compound* `memory, claude_md_draft` row where `claude_md_draft` is the action being evaluated still downgrades to `memory`, collapsing the row to single `memory`. |
| `skill_idea` | `memory` | Single observation doesn't justify a skill artifact; capture pattern first |
| `memory` | (no downgrade) | Already the lowest tier |

If sub-condition (a) fails → return finding to Stage 2 step 8 with prompt to add observation citations.
If sub-condition (b) fails → keep the stronger-evidence action, mark the weaker for trigger-condition emission in Stage 3, log the coupling reason in Actions Executed report.
If sub-condition (c) fires → apply the downgrade; if the resulting action set is now memory-only on a `tool`/`workflow`/`spec-gap`-labeled finding, this re-triggers Gate-1 (per-finding loop cap applies). Cap accounting: the downgrade itself does NOT consume a re-entry — only the resulting Gate-1 re-evaluation does. A single Gate-3 (c) → Gate-1 cascade counts as one re-entry, not two.

**Out of scope** — each sub-condition (a) / (b) / (c) has its own skip rule. A single condition does NOT necessarily disable the whole gate; sub-conditions are evaluated independently per finding:

| Condition on the finding | (a) per-action evidence | (b) decision-coupling | (c) single-observation downgrade |
|--------------------------|:-----------------------:|:---------------------:|:--------------------------------:|
| `Proposed Actions` count = 1 (single-action; includes behavioral-only defaults and `note only` rows) | SKIP (gate inapplicable — no sibling) | SKIP | SKIP |
| Two actions affect different artifacts on different surfaces (e.g., `claude_md_draft` for global `~/.claude/CLAUDE.md` + `skill_idea` for tool-side enhancement) | apply | SKIP — not decision-coupled by construction | apply |
| `repeat=true` finding | apply (per-action evidence still required) | apply (coupling can still occur even with repeat history) | SKIP — repeat history is multi-observation evidence |

The previous all-or-nothing "Gate-3 does NOT fire" framing produced a bypass: a `claude_md_draft + skill_idea` pair backed by a single non-repeat observation would silently skip (a) and (c) along with (b), defeating the core evidence-robustness intent. This table restores sub-condition granularity.

**Decision-coupling examples for sub-condition (b):**

- **Coupled** (Gate-3 (b) fires): `claude_md_draft` rewrites a rule to assert "behavior X is intentional" + `upstream_feedback` opens an upstream issue asking "is X intentional or oversight?". The first action presupposes the answer the second action exists to ask. Keep the stronger-evidence action; demote the other to a trigger-condition line.
- **Not coupled** (Gate-3 (b) skips): `claude_md_draft` adds a project-side rule + `skill_idea` sketches a tool-side enhancement on a different surface. Both actions can be executed simultaneously without contradiction; their outcomes are independent.

**Per-finding loop cap** — maximum 2 re-entries per finding (across all three gates combined; not per-gate). Worked example of a worst-case path that hits the cap: Gate-3 (a) fail → step 8 re-derive (re-entry 1) → Gate-3 (c) downgrades action → Gate-1 fail → step 8 re-derive (re-entry 2) → cap reached, surface to user. On the 3rd violation for the same finding, surface to user with explicit prompt:

> "Finding #N의 Gate-1/Gate-2/Gate-3 중 하나가 통과되지 않습니다. 어떻게 진행할까요? [a] rationale/observation을 직접 입력해 우회 / [b] action을 직접 지정 / [c] 이 finding은 note only로 강등."

User-supplied resolution is logged but bypasses the gate(s) for that single finding.

**Behavioral-only safeguard** — if ALL findings ended up labeled `behavioral` only (no tool/workflow/spec-gap anywhere), run a final keyword sanity check on the original pre-scan signal text. If any signal contains `gh ` / `kubectl` / `MCP` / `--state` / `permission denied` / `timeout` / `--help` / `flag`, surface to user:

> "모든 finding이 behavioral로 분류되었으나 pre-scan 신호 텍스트에 도구 키워드(`gh`, `MCP`, ...)가 발견됨. 라벨이 정확한지 재검토 필요."

User confirmation required to proceed; if user confirms, log the keyword set found.

**Gate-4 (External-Repo Authorization Pre-check)** — For each finding whose `Proposed Actions` includes `upstream_feedback`, classify the `backing_repo` owner against the own-org allowlist and mark external findings before Stage 3 renders them.

**Step 1 — Extract owner.** Parse `backing_repo: <owner/repo>` from the finding's Rationale cell. If the declaration is absent → Gate-4 skips this finding (Stage 4 Action 4 step 0's missing-declaration abort is the downstream enforcement).

**Step 2 — Resolve own-org allowlist** (in priority order):
1. Env var `PRAXIS_OWN_ORGS` (comma-separated handles) — e.g., `PRAXIS_OWN_ORGS="devseunggwan,laplace-tech-team"`
2. Fallback: `gh api user --jq .login` → treat the single returned handle as the only own org
3. Both absent → treat all `backing_repo` owners as external (conservative default — all `upstream_feedback` rows require per-action approval at Stage 4)

**Step 3 — Classify.** Extract `owner` from `backing_repo: <owner/repo>` (case-insensitive comparison). If `owner` ∉ resolved allowlist → mark the finding `external=true`.

**Step 4 — Mark external findings.** For each `external=true` finding, prepend `⚠ EXTERNAL: per-action approval required at Stage 4` to the Rationale cell (separated from existing text by `<br>`). This prefix is load-bearing: Stage 4 Action 4 step 0a scans the Rationale cell for this exact string to trigger its per-action approval gate.

**Gate-4 verdict:**
- Zero `external=true` findings → `gate_4_verdict: PASS`
- ≥1 `external=true` finding → `gate_4_verdict: WARN` (the action is still permitted; per-action approval at Stage 4 Action 4 step 0a is the enforcement control)
- No `upstream_feedback` findings at all → `gate_4_verdict: NA`

`gate_4_verdict` is informational (parallel to `gate_3_verdict`). The Stop hook silently ignores it; enforcement is procedural inside Stage 4 Action 4 step 0a.

**Gate-5 (Memory-Scan Completeness)** — For every finding whose `Proposed Actions` includes `memory` (i.e., the finding is headed toward a MEMORY.md write at Stage 4 Action 1), verify that the finding carries a populated `memory_scan` field (set by Stage 2 step 7g).

> **Why Gate-5 exists**: Stage 2 step 7 was previously procedural-only and effectively skippable. Without a mandatory call site (unlike tracer/analyst agent invocations), "MEMORY.md scan" was easily elided by claiming knowledge from context. Gate-5 provides the structural enforcement — findings that propose memory writes without showing scan evidence are returned to Stage 2 step 7 to complete it.

**Detection**: For each finding, check whether `memory` ∈ `Proposed Actions`. If yes → verify `memory_scan.scanned == true` is present in the finding's internal record from Stage 2 step 7g.

> **Note on `memory_create` vs `memory_update`**: "memory" in the Proposed Actions column covers both creating a new file and merging into an existing one (Stage 4 Action 1's duplicate-check step distinguishes them). Gate-5 applies to both sub-cases. Findings with `memory` as a compound second action (e.g., `memory, issue`) are subject to Gate-5 equally.

**Step 1 — Identify memory-action findings**: collect all findings where `memory` ∈ `unique_actions`.

**Step 2 — Verify `memory_scan` field**: for each finding from step 1, check that Stage 2 step 7g recorded the load-bearing fields (per step 7g's field requirements):
- `memory_scan.scanned == true` (proves step was executed, not skipped)
- `candidates_reviewed` is present (even if empty list — proves index was consulted)
- `repeat` and `repeat_count` fields are present

`matched` and `resolved` are NOT verified by Gate-5 (`matched` is documentation-only; `resolved` is optional per step 7g).

**Step 3 — Classify violations**: any finding from step 1 that fails step 2 → Gate-5 violation → return that finding to Stage 2 step 7 with instruction to complete the 2-hop scan and record `memory_scan` per step 7g. Cap counter applies (same 2-re-entry cap per finding, shared across all gates for that finding).

**Gate-5 verdict:**
- All memory-action findings have valid `memory_scan` → `gate_5_verdict: PASS`
- ≥1 memory-action finding missing `memory_scan` → `gate_5_verdict: FAIL`
- No memory-action findings exist → `gate_5_verdict: NA`

`gate_5_verdict` is informational (parallel to `gate_3_verdict` and `gate_4_verdict`). The Stop hook silently ignores it; enforcement is procedural inside Stage 2.5. If Gate-5 violations persist after 2 per-finding re-entries, surface to user with 3-way override prompt:

> "Finding #N의 Gate-5 (memory_scan 필드 누락)가 통과되지 않습니다. 어떻게 진행할까요? [a] MEMORY.md index를 직접 읽고 memory_scan 필드를 입력 / [b] action을 직접 지정 / [c] 이 finding은 note only로 강등."

**Output (on pass)** — Stage 2.5 emits the distribution card per the Output Schema Contract defined in Stage 3, plus per-finding Gate-1, Gate-2, Gate-3, Gate-4, and Gate-5 verdicts:

```
<!-- retrospect:distribution begin -->
- memory: {n}
- issue: {n}
- claude_md_draft: {n}
- skill_idea: {n}
- hook_code: {n}
- upstream_feedback: {n}
- memory_hygiene: {n}
- output_quality: {n}
- gate_1_verdict: {PASS|FAIL|NA}
- gate_2_verdict: {PASS|FAIL|NA}
- gate_3_verdict: {PASS|FAIL|NA}
- gate_4_verdict: {PASS|WARN|NA}
- gate_5_verdict: {PASS|FAIL|NA}
<!-- retrospect:distribution end -->
```

`memory` count = (friction-event findings with Proposed Actions = `memory`, single or compound) + (successful_patterns where `reinforce_action = memory`). Stage 4 Action 1 reads the same total but routes per-entry by origin tag.

`memory_hygiene` count = findings produced by Stage 1.5 (i.e., findings whose `category[]` includes `memory_hygiene`). This is a **category count**, not an action key — these findings still emit their underlying action under one of the existing 6 action-type slots (memory / issue / claude_md_draft / ...). The `memory_hygiene` line is informational: it surfaces how much of the report originated from Stage 1.5 vs Stage 2 friction-scan.

`NA` = no findings of the relevant type. Per-gate `NA` semantics:
- `gate_1_verdict: NA` — zero `tool`/`workflow`/`spec-gap` labeled findings exist
- `gate_2_verdict: NA` — zero memory-only findings exist
- `gate_3_verdict: NA` — zero findings have `Proposed Actions` count = 2 (every finding is single-action)
- `gate_4_verdict: NA` — zero `upstream_feedback` findings exist
- `gate_5_verdict: NA` — zero `memory`-action findings exist

The Stop hook `retrospect-mix-check.sh` parses `gate_1_verdict`, `gate_2_verdict`, and `gate_4_verdict`; `gate_3_verdict` is emitted for visibility and audit-trail purposes (the hook silently ignores unknown keys). Gate-3 is enforced procedurally inside Stage 2.5; structural Stop-hook enforcement is reserved for a follow-up tightening if procedural compliance proves insufficient. Gate-4 is enforced both procedurally (Stage 4 Action 4 step 0a per-action approval) and structurally by the Stop hook: a `gate_4_verdict: FAIL` in the card blocks Stage 3 output, and an absent `gate_4_verdict` combined with a `⚠ EXTERNAL:` Rationale prefix also blocks (indicating Stage 2.5 Gate-4 was partially skipped). `gate_5_verdict` is informational-only and INTENTIONALLY EXCLUDED from Stop hook parsing — the hook silently ignores it; Gate-5 enforcement is procedural inside Stage 2.5. (Stop hook wiring for Gate-5 is a deferred follow-up similar to Gate-4's progression in PR #340 — file a follow-up issue when procedural compliance proves insufficient.)

This card and verdict block become Stage 3's input header.

### Stage 3: Report + Approval

**Output Schema Contract** (normative — Stop hook `retrospect-mix-check.sh` parses this):

Stage 3 output MUST emit, in this order:

1. **Header**: a line matching `^## Retrospect Report` (em-dash or hyphen tail accepted: `## Retrospect Report — {date}` or `## Retrospect Report - {date}`).
2. **Distribution card** between HTML comment fences. Action keys are canonical snake_case enum; verdict values are `PASS` / `FAIL` / `NA`:

   ```markdown
   <!-- AUTHORITATIVE_SCHEMA — Stop hook depends on this. Co-update hooks/retrospect-mix-check.sh + tests/test_retrospect_mix_check.sh + tests/fixtures/retrospect-synth-*.expected.json on any change to: (1) the fence markers themselves, (2) the action key set (memory/issue/claude_md_draft/skill_idea/hook_code/upstream_feedback), or (3) gate_1_verdict / gate_2_verdict keys.
        Gate-3 carve-out: gate_3_verdict is informational-only and INTENTIONALLY EXCLUDED from this co-update contract. The hook's awk parser keys on gate_1_verdict/gate_2_verdict literals only and silently ignores all other lines (regression-tested by tests T8–T17 + the 4 fixture files which still pass without gate_3_verdict). Adding/removing/renaming gate_3_verdict alone does NOT require hook or test changes.
        Gate-4 enforcement note: gate_4_verdict IS structurally enforced by the Stop hook (unlike gate_3_verdict which remains informational-only). Changes to gate_4_verdict semantics or its FAIL/WARN/NA/PASS values REQUIRE synchronized edits to hooks/retrospect-mix-check.sh and tests/test_retrospect_mix_check.sh (T36/T37/T38). Adding/removing gate_4_verdict from the card alone does NOT require fence-marker or action-key changes.
        Gate-5 carve-out: gate_5_verdict is informational-only and INTENTIONALLY EXCLUDED from this co-update contract (parallel to gate_3_verdict). The hook silently ignores gate_5_verdict. Adding/removing/renaming gate_5_verdict alone does NOT require hook or test changes. (Deferred upgrade trajectory similar to Gate-4 in PR #340 — file a follow-up issue for Stop hook wiring when procedural enforcement proves insufficient.)
        Category-count carve-out (memory_hygiene / output_quality): these are CATEGORY counts (count of findings with the respective category in category[]) — NOT action keys. They are emitted as sibling lines to the action-type counts but are informational-only and INTENTIONALLY EXCLUDED from Stop hook parsing. Adding/removing/renaming memory_hygiene OR output_quality alone does NOT require fence-marker or action-key changes; the underlying actions of Stage 1.5 / Stage 2.7 findings still fall under one of the 6 action-type keys above. The Stop hook's awk parser silently ignores both lines (same mechanism as gate_3/gate_5 verdicts). -->
   <!-- retrospect:distribution begin -->
   - memory: 1
   - issue: 0
   - claude_md_draft: 0
   - skill_idea: 0
   - hook_code: 0
   - upstream_feedback: 0
   - memory_hygiene: 0
   - output_quality: 0
   - gate_1_verdict: PASS
   - gate_2_verdict: PASS
   - gate_3_verdict: PASS
   - gate_4_verdict: PASS
   - gate_5_verdict: PASS
   <!-- retrospect:distribution end -->
   ```

3. **Unified findings table** with literal column headers (no abbreviation, no reordering):

   ```
   | # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
   ```

   Column semantics:
   - `Category`: comma-separated subset of `behavioral`, `tool`, `workflow`, `spec-gap`, `memory_hygiene`, `output_quality` (≥1, see Stage 2 pre-scan categorization; `memory_hygiene` originates from Stage 1.5, `output_quality` originates from Stage 2.7)
   - `Tool Layer`: one of `mcp`, `cli`, `builtin`, `skill`, or `—` (mandatory non-`—` when `tool` ∈ Category, mandatory non-`—` when `output_quality` ∈ Category AND audit surface is gh-CLI or sub-agent, optional `skill` for `workflow` / `spec-gap` / `memory_hygiene`, `—` for `behavioral` / `memory_hygiene` (default) / `output_quality` (MCP-via-behavioral path))
   - `Proposed Actions (1~2)`: comma-separated subset of `memory`, `issue`, `claude_md_draft`, `skill_idea`, `hook_code`, `upstream_feedback`; for findings marked `external=true` by Stage 2.5 Gate-4, append ` (external)` suffix to `upstream_feedback` (e.g., `memory, upstream_feedback (external)`)
   - `Rationale`: free-form one-line for compound or non-memory rows; for **memory-only** rows (single `memory`, not compound), the cell MUST match Schema A or Schema B (see Gate-2 in Stage 2.5): Schema A — exactly 5 lines `^not (issue|claude_md_draft|skill_idea|hook_code|upstream_feedback): .+$`, one per non-memory action type; Schema B — 1-2 lines `^not-others: .+$` with dimension tags (e.g., `not-others: repeat=0, rule_exists=yes, gateable=no, tool_defect=no`). Generic single-sentence rationales are NOT acceptable for memory-only findings. **For rows whose actions include `upstream_feedback`** (single or compound), the cell MUST also contain a literal line `backing_repo: <owner>/<repo>` (embedded via `<br>` for single-line markdown form) — Stage 4 Action 4 step 0 reads this as the routing decision. **Compound case `memory, upstream_feedback`**: the row is NOT memory-only (contains a non-memory action), so the Schema A/B requirement does NOT apply — instead use free-form prose for the human rationale + the `backing_repo:` line. Compound combinations are *additive*: each action-specific Rationale convention applies independently to the row, joined with `<br>`.

The Stop hook parses the distribution-card fence (deterministic) and the table (anchored on these literal column headers). Drift in this contract requires synchronized edits to `hooks/retrospect-mix-check.sh`, `tests/test_retrospect_mix_check.sh`, and `tests/fixtures/retrospect-synth-*.expected.json`.

**Spec AC-A3 deviation note** — earlier draft asked for "memory-only justification 한 줄" inside the distribution card. v2 relocates that justification into the unified-table `Rationale` column as the structured 5-line `not <action>: <reason>` block: strictly more informative than a single line, single source of truth, eliminates card↔table inconsistency risk.

---

**Present findings as a single unified table per the Output Schema Contract above:**

```
## Retrospect Report — {session_date}

<!-- retrospect:distribution begin -->
- memory: {n}
- issue: {n}
- claude_md_draft: {n}
- skill_idea: {n}
- hook_code: {n}
- upstream_feedback: {n}
- memory_hygiene: {n}
- output_quality: {n}
- gate_1_verdict: {PASS|FAIL|NA}
- gate_2_verdict: {PASS|FAIL|NA}
- gate_3_verdict: {PASS|FAIL|NA}
- gate_4_verdict: {PASS|WARN|NA}
- gate_5_verdict: {PASS|FAIL|NA}
<!-- retrospect:distribution end -->

| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|----------|------------|---------|------------|------------|---------|------------------------|-----------|----------|
| 1 | {behavioral|tool|workflow|spec-gap, ...} | {mcp|cli|builtin|skill|—} | {pattern} | {root_cause} | {rule_ref or "gap"} | {Yes(Nx)/No} | {action1[, action2]} | {rationale: 5 `not <action>:` lines for memory-only, or one-line for compound/non-memory} | HIGH/MED/LOW |
...

### Trigger Conditions (Gate-3 (b) demotions)

(Non-machine-parsed; emitted for human review only — not parsed by the Stop hook.)

If Stage 2.5 Gate-3 detected sibling decision-coupling and demoted the weaker action,
emit one bullet per demoted action below. Format: literal `^- Finding #N: file <action>
when <observation predicate>$`.

- Finding #N: file `<demoted_action>` when `<observation predicate>` (originally proposed alongside `<kept_action>`; demoted because <coupling reason>)

If no Gate-3 (b) demotions occurred, omit this section entirely (do not emit "None.").

No patterns found: emit the distribution card with all counts = 0 and verdicts = NA, plus literal "This session followed all global `~/.claude/CLAUDE.md` rules. ✅"

### Reinforced Patterns (this session)

(Emitted only when pre-scan found ≥1 successful_patterns with `reinforce_action: visualize_only`. Omit this section entirely if none — do not emit "None." Rows with `reinforce_action: memory` are NOT listed here — they appear in Stage 4 Actions Executed report alongside friction-origin memory entries, distinguishable by the `Reinforced — ` description prefix.)

- {pattern_1} (evidence: {turn or artifact})
- {pattern_2} ...
```

The unified table folds the previous dual-table layout (Pattern + Tool/Feature Findings) into one. Tool-layer information that previously lived in a separate "Tool/Feature Findings" table is now carried in the `Tool Layer` column of every row tagged with `tool` in `Category`. Reviewers see all findings in priority order without cross-referencing two tables.

**Sorting**: rows SHOULD be sorted by `Priority` (HIGH → MED → LOW). Within the same priority, prefer non-memory `Proposed Actions` first so escalations surface above behavioral memos.

**Action type baseline comes from Stage 2 escalation ladder**, but Stage 3 MUST explicitly evaluate all six action types per finding and select 1–2 composite actions.

> **Exception — one-off mistakes**: If Stage 2 classified the finding as `note only` (situational root cause, unlikely to recur), skip the evaluation below entirely. No persistent action is created; the finding appears in the report as acknowledged only.

**For each finding (except one-off), evaluate ALL six action types before selecting:**

| Action Type | When to Choose | Skip If |
|-------------|---------------|---------|
| **MEMORY.md feedback** | New pattern (1st occurrence, repeat_count=0), individual learning | repeat=true (memory is BLOCKED) |
| **GitHub issue** | Systemic fix needed (tool/skill implementation), repeat pattern (1–2×) | One-off mistake, purely local insight |
| **Global `~/.claude/CLAUDE.md` draft** | Explicit rule gap exists, cross-project scope needed | Existing rule already covers this pattern |
| **Skill idea note** | Repeat pattern needs enforcement mechanism, manual recall is insufficient | Single memo is sufficient, no recurring trigger |
| **Hook code** | Repeat (3x+) requiring automated enforcement; manual recall has repeatedly failed | Fewer than 3 repeats; skill idea or rule is sufficient |
| **Upstream feedback** | Tool/feature-level defect identified in step 4b; improvement needed in the tool itself, not in Claude's behavior | Finding is purely a rule violation with no tool-level root cause |

**Selection matrix — three axes to determine compound vs. single action:**

| Axis | Signal → Action |
|------|----------------|
| **Repeat count** | 0× → `memory` (first occurrence); 1–2× → `issue` (memory blocked — repeat=true); 3×+ → `skill` or `hook` (enforcement gap) |
| **Scope** | Cross-project impact → global `~/.claude/CLAUDE.md` draft; single-project → `MEMORY.md` |
| **Gap type** | Rule violated → `memory` (reinforce); rule absent → global `~/.claude/CLAUDE.md` draft (fill gap); no enforcement → `skill idea` |

> **Axis precedence: Repeat-count is the highest-priority axis.** When `repeat=true`, the Scope and Gap type axes cannot override to `memory` — the repeat-count constraint (issue / skill / hook) always wins. Apply Scope and Gap type only to determine additional actions alongside the repeat-count result.

**Compound action is the default for HIGH-priority findings.** A single `memory` action is acceptable only when the rationale for skipping all other types is explicitly stated in the `Rationale` column.

**Before approval, explain each action's concrete plan:**

For each finding, present:
1. **What will be created** (file path, issue title, hook name, or global `~/.claude/CLAUDE.md` rule text)
2. **Why this action type** (escalation rationale — e.g., "Already recorded 3x in MEMORY.md")
3. **How it will be verified** (what check confirms it works)
4. **Stage 2 caveats carried forward** (MANDATORY when any of the following hold) — emit a literal `Stage 2 caveats:` line listing each item that applies to this finding. Omit the entire line only when none of the items apply.
   - `tracer confidence: LOW|MED` — tracer agent (Stage 2 step 1) returned anything below HIGH on this finding's causal chain
   - `single observation` — only 1 friction-event citation supports this finding (Stage 2.5 Gate-3 (c) precondition)
   - `alternative root cause not ruled out: <description>` — tracer/analyst surfaced ≥1 competing root cause that was not falsified
   - `Gate-3 (c) downgrade applied: <original> → <new>` — Stage 2.5 downgraded the action tier
   - `Gate-3 (b) sibling demoted to trigger: <demoted_action>` — Stage 2.5 split a decision-coupled pair into kept + trigger
   - `repeat=true, resolved=true (escape hatch)` — existing resolution covers this; verify before re-acting
   - `analyst clustered with #N` — analyst agent (Stage 2 step 2) clustered this with another finding whose Proposed Actions differ
  Stage 3 ranking (including `(Recommended)` selection in the next sub-section) MUST read these caveats. A `(Recommended)` label cannot be applied to an action whose Stage 2 caveats include `tracer confidence: LOW`, `single observation` alone, or `alternative root cause not ruled out` — those caveats must be discharged via the Pre-Output Falsification Gate below before ranking.

#### Pre-Output Falsification Gate (AskUserQuestion)

This gate fires immediately before each `AskUserQuestion` call emitted by Stage 3 for any finding. It is normative — skipping it for the same finding more than once per Stage 3 turn is a Red Flag.

**Trigger detection (scan every option label and surrounding framing):**

- Literal `(Recommended)` suffix on any option label
- Confidence-anchoring framing in option label or description: `safer`, `safest`, `natural fit`, `natural choice`, `obvious choice`, `clearly`, `안전한`, `자연스러운`, `당연히`, `분명히`
- Synonymous ranking signals: `default to`, `default choice`, `prefer this`, `recommend`, `추천`, `기본값`

If ANY trigger matches → the gate fires. The first option's mere position is NOT a trigger; only labels and framing are.

**Mandatory pre-output question (internal to the skill, not surfaced to user):**

> "If this proposal's premise is wrong, what observation should be *missing* from the current evidence? Is that observation actually missing?"

Concretely, for each `(Recommended)`-labeled option, derive:

1. The proposal's premise in one sentence (e.g., "this friction's root cause is in Claude's behavior, addressable via memory entry")
2. The disconfirming observation that would make the premise false (e.g., "if upstream tool defect is the real root cause, the friction recurs even when Claude follows the proposed memory rule perfectly")
3. Whether that disconfirming observation is actually missing in Stage 2 evidence — read the finding's `category[]`, `Tool Layer`, root cause text, and Stage 2 caveats (above) to check

**Outcome rules:**

| Falsification outcome | Action |
|---|---|
| Premise survives — disconfirming observation IS missing in Stage 2 evidence | `(Recommended)` label allowed; emit one-line falsification trace in the per-finding plan: `Falsification: premise survived — <disconfirming observation> was not present in Stage 2 evidence (category=<...>, Tool Layer=<...>, root cause=<one-line>)` |
| Premise fails — disconfirming observation IS present | `(Recommended)` label DISALLOWED; surface the option as unranked alongside siblings; record `Falsification: premise failed — <disconfirming observation> present, surfacing unranked` |
| Falsification step not run (e.g., skill skipped this gate) | `(Recommended)` label DISALLOWED; ESCALATE to user with open premise: `AskUserQuestion` MUST include a question line asking the user to confirm the premise before any ranked option is offered |

**The gate composes with Stage 2 caveats (above):**

- `tracer confidence: LOW` → falsification cannot mark "premise survives" without explicit disconfirming-observation check (LOW confidence IS itself a present disconfirming signal until shown otherwise)
- `single observation` + `repeat=false` → falsification must explicitly state whether the single observation is dispositive; if dispositive evidence absent, downgrade to unranked
- `alternative root cause not ruled out` → premise fails by construction; surface unranked

**Skip conditions (gate does NOT fire):**

- Zero `(Recommended)` labels AND zero confidence-anchoring phrases in the entire Stage 3 output (the trigger scan finds nothing)
- Stage 3 emits the "No patterns found. ✅" early exit per Stage 2 step 7
- All findings are `note only` (one-off mistakes — no ranking needed)

The gate's outcome MUST appear as a `Falsification:` line in the per-finding plan immediately under the `Stage 2 caveats:` line (or replace it when `Stage 2 caveats:` is omitted). Reviewers and downstream parsers anchor on this exact prefix.

Example (single action — repeat pattern):
> Finding #2: Workflow step skipped (4th occurrence)
> - **Proposed Actions**: GitHub issue
> - **Rationale**: Already recorded 3x in MEMORY.md. Memory alone has failed. Structural fix required.
> - **What will be created**: issue — `feat(hook): add external-repo commit guard`
> - **Verify**: issue URL returned + `gh issue view` confirms existence
> - **Stage 2 caveats**: (none — tracer confidence HIGH, repeat=true with 4 observations)
> - **Falsification**: premise survived — disconfirming observation would be "memory rule prevented step skip in ≥1 prior session"; Stage 2 MEMORY.md scan found no such evidence in any of the 3 prior memory entries

Example (compound action — rule gap + repeat):
> Finding #1 (HIGH): Hasty interpretation without verification (ambiguous signal → worst-case conclusion, 3 occurrences)
> - **Proposed Actions**: global `~/.claude/CLAUDE.md` draft + `GitHub issue`
> - **Rationale**: Rule absent + 3× repeat → fill the rule gap (global `~/.claude/CLAUDE.md` draft) and track enforcement compliance (GitHub issue); matches Stage 2 ladder: "Missing rule + Repeat"
> - **What will be created**:
>   - global `~/.claude/CLAUDE.md` draft: new rule requiring a disconfirmation check before concluding from ambiguous signals
>   - issue — `feat(retrospect): enforce falsify-first check on ambiguous signal interpretation`
> - **Verify**: global `~/.claude/CLAUDE.md` draft shown to user for approval + issue URL returned
> - **Stage 2 caveats**: analyst clustered with Finding #4 (same root cause family); evaluate combined fix scope before executing
> - **Falsification**: premise survived for `global ~/.claude/CLAUDE.md draft` — if the rule already existed in another section, the 3× repeat would show citations to that rule rather than rule-absent rationale; Stage 2 step 4 confirmed no applicable rule (category=spec-gap)

Example (gate-suppressed `(Recommended)`):
> Finding #3 (MED): MCP timeout caused 3 retries (single occurrence in this session)
> - **Proposed Actions**: `upstream_feedback`
> - **Rationale**: Tool defect surfaced at step 4b — performance issue.<br>backing_repo: `<resolved_backing_repo>`
> - **What will be created**: issue in the resolved backing repo — `perf(<plugin>): reduce MCP timeout on <op>`
> - **Verify**: `gh issue view {url}` succeeds + URL repo matches resolved backing repo
> - **Stage 2 caveats**: `single observation`; `tracer confidence: MED`; `alternative root cause not ruled out: transient network blip`
> - **Falsification**: premise failed — `(Recommended)` label suppressed. Disconfirming observation IS present (single timeout indistinguishable from transient blip without a second sample). Option surfaced unranked; AskUserQuestion includes premise-confirmation line: "이 timeout이 패턴인지 단발 blip인지 추가 관찰 전에 upstream 이슈를 열까요?"

**Then ask for approval per item using AskUserQuestion:**

```
For each finding, user selects:
  ✅ Execute now  |  ⏭ Skip  |  🕐 Defer (create note only)
```

Do NOT execute any action until user approves.

### Stage 4: Execute

**"note only" items require no execution** — they appear in the completion report as acknowledged but need no persistent artifact.

For each approved action:

1. **MEMORY.md feedback** → Write to `$CLAUDE_CONFIG_DIR/projects/.../memory/` with proper frontmatter. This action processes two input streams:

   **Friction-event origin** (existing behavior): finding row with `memory` in Proposed Actions. Write MEMORY.md entry per existing convention (Type: `feedback`, include: rule, why, how to apply). Description uses the standard feedback format.

   **Success-pattern origin** (new): `successful_patterns` row where `reinforce_action: memory`. Write MEMORY.md entry with:
   - Type: `feedback`
   - Description MUST start with `Reinforced — <pattern>` (the `Reinforced — ` prefix distinguishes positive reinforcement entries from friction entries in future retrospect scans)
   - Evidence: from the `successful_patterns.evidence` field
   - How to apply: describe how to intentionally replicate the successful pattern

   Per-entry workflow:
   1. For each finding with `memory` action → write MEMORY.md entry per existing convention.
   2. For each `successful_patterns` row with `reinforce_action: memory` → write MEMORY.md entry with description starting `Reinforced — <pattern>`, evidence link from the `successful_patterns.evidence` field.

   - Update `MEMORY.md` index (for both origins)

   **Frontmatter contract — `memory-hint` opt-in (mandatory consideration)**

   In addition to standard fields (`name`, `description`, `type`), every new memory MUST evaluate whether to include the `memory-hint` opt-in fields — `hookable` and `hookKeywords`. The full field spec (parser semantics, matching rules, fail-open contract) lives in `docs/hook/memory-hint.md`; this section defines the authoring-time decision. Use top-level `type:` (consistent with `docs/hook/memory-hint.md` example and existing on-disk memory files); do **not** nest under `metadata:`.

   ```yaml
   ---
   name: my-memory
   description: Short rule statement
   type: feedback
   hookable: true                           # opt into PreToolUse surface
   hookKeywords: [keyword1, keyword2]       # whole-token match (case-sensitive)
   ---
   ```

   **Category-based default (apply unless rationale documented):**

   | Category | `hookable` default | Rationale |
   |---|---|---|
   | behavioral retrieval-critical (silent-recurrence likely; failure mode is "Loaded ≠ Retrieved") | `true` | hook is the only structural enforcement; skill/memory alone fails retrieval at action time |
   | success-pattern reinforcement (`Reinforced — ` prefix) where intentional replication needs same retrieval surface | `true` | same "retrieve at action time" need applies in reverse |
   | abstract / meta / cross-cutting principle (no concrete action signal) | `false` | keyword match would be noisy across unrelated commands |
   | author-generated rule (belongs in `~/.claude/CLAUDE.md` draft) or upstream-feedback note | `false` | not action-gateable; memory is a holding pen, not the enforcement surface |

   When uncertain → default `hookable: false` (safer to omit than to add noise) AND record the uncertainty in the Actions Executed report so a future retrospect can re-evaluate.

   **`hookKeywords` selection rules:**
   - Choose tokens that appear in the *action* the memory is meant to gate — CLI subcommand (`merge`, `close`), tool name (`Edit`, `cmux-delegate`), distinctive flag (`--force`), or domain identifier (`Closes`, `Recommended`).
   - Whole-token, case-sensitive matching only (per `docs/hook/memory-hint.md`). List multiple casings explicitly if needed (`[Edit, edit]`).
   - 1–4 keywords typical; >5 raises false-positive risk linearly.
   - **Avoid generic English words** (`add`, `run`, `test`, `update`) — they fire on unrelated commands and erode the hint signal.
   - When the memory targets a non-Bash event (Edit, Write, AskUserQuestion, etc.), the current `memory-hint.py` only fires on Bash — record the intent in the description so a future event-coverage expansion can surface it.

   **⚠️ MANDATORY: Duplicate check before creating any memory file:**

   **Precondition:** This check applies ONLY when the finding's action type is `memory` (new pattern). If Stage 2 already marked `repeat=true` and escalated to issue/hook/global `~/.claude/CLAUDE.md` draft, skip this check — the escalation ladder takes precedence over merge.

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

3. **Global `~/.claude/CLAUDE.md` draft** → Write proposed rule addition as a markdown block, routed by target

   **Step 0 — Target detection (MUST run first):**
   Resolve the target path via `realpath` and classify:

   | Target | Detection | Execution path |
   |--------|-----------|---------------|
   | **Project `AGENTS.md`** | `realpath <path>` does NOT match `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md` AND resolves inside cwd | Direct Edit (see Project path below) |
   | **Global `~/.claude/CLAUDE.md`** | `realpath <path>` matches `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md` | Staging → AskUserQuestion → apply only on explicit approval (see Global path below) |
   | **External-repo rule file** | `realpath <path>` resolves outside cwd AND outside `~/.claude/` | Same as external-repo gate — do NOT edit; surface to user with resolved path |

   **Project path** (`realpath` does NOT match global):
   - Present the draft diff to the user inline
   - Apply with explicit approval ("yes, add this rule") → Direct Edit

   **Global path** (`realpath` matches `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md`):
   ⚠️ Global scope — changes affect every project. Claude Code's self-modification classifier blocks direct Edit/Write without explicit approval.

   a. **Stage draft**: write the proposed rule block to `/tmp/claude-md-draft-{slug}.md` (use `.omc/plans/claude-md-draft-{slug}.md` as fallback when `/tmp/` is not writable). Present the full draft content inline before showing the prompt.

   b. **AskUserQuestion — 3-option prompt**:
      ```
      options:
        apply  — 승인. 지금 바로 적용합니다.
        수정   — 변경할 내용을 free-text로 입력하면 재작성 후 다시 이 단계로 돌아옵니다.
        보류   — 이번 세션은 적용하지 않습니다. 스테이징 파일을 남겨둡니다.
      ```
      - `수정` 선택 시: "무엇을 바꿀까요?" 입력 받음 → re-draft → 다시 (b) 단계로 복귀.
        Cap: 최대 3 라운드. 3 라운드 초과 시: "3회 재작성을 초과했습니다. 수동 편집을 권장합니다: `{staging_path}`" 후 보류 처리.
      - `보류` 선택 시: Edit 호출 없이 staging 파일 경로를 completion report에 기록하고 종료.

   c. **`apply` 선택 시**: Edit on global `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md` to insert the approved rule at the indicated position. Show the resulting diff as verification.

4. **Upstream feedback** → Resolve the tool's **backing repo first** (do NOT hardcode any specific repo), then create a labeled issue there. Hardcoding misroutes plugin defects, custom MCP defects, dotfiles defects across user environments.

   ### Step 0 — Backing-repo verification gate (MUST run before any mutation)

   This gate is the first procedure step for every `upstream_feedback` row, executed **before** any of the resolution-table lookups below. Skipping it means the most salient file path in the executor's local context (often the working project repo) wins the routing decision — which is the exact failure mode this gate prevents.

   1. **Read the declaration.** Parse `backing_repo: <owner/repo>` from the finding's Rationale cell (Stage 2 step 8 makes this MANDATORY for upstream_feedback rows; Stage 3 surfaces it). If the declaration is absent → ABORT this action and return the finding to Stage 2 step 8 with prompt: `"Finding #N upstream_feedback row missing backing_repo declaration — re-run Stage 2 step 8."`

   2. **Re-resolve from source-of-truth.** Independently of the declaration, re-resolve the backing repo using the resolution table below. Do NOT use the declared value as the lookup input — use the tool/layer signal from Stage 2 step 4b to derive the repo from scratch. Capture the re-resolved value as `live_backing_repo`. If the resolution table's `Other / ambiguous` row matches the layer (no concrete repo derivable), treat `live_backing_repo = AMBIGUOUS` and skip to step 0.4 with a 2-way prompt instead of 3-way.

   3. **Compare.** If `live_backing_repo == declared backing_repo` → proceed to the rest of Action 4. Normalization rules for equality (apply both sides):
      - Strip leading/trailing whitespace
      - Strip trailing `.git`
      - Treat all of these as equivalent forms of the same repo: `owner/repo`, `https://github.com/owner/repo`, `git@github.com:owner/repo`, `ssh://git@github.com/owner/repo`
      - Case-insensitive on `owner` and `repo` (GitHub treats them case-insensitively for routing)

   4. **Divergence / ambiguity handling.** If `live_backing_repo != declared backing_repo` (after normalization) → ABORT and surface to user via `AskUserQuestion`. Two prompt variants:

      **(i) Both sides concrete repos — 3-way prompt:**
      ```
      ⚠ Backing-repo divergence on Finding #N:
         Stage 2/3 declared:    {declared}
         Stage 4 re-resolved:   {live}

      어느 쪽이 정확합니까?
      [a] declared ({declared}) 으로 진행
      [b] re-resolved ({live}) 으로 진행 (Stage 2 declaration 정정)
      [c] 이 finding 은 skip — upstream_feedback 액션 제거
      ```

      **(ii) Re-resolution returned `AMBIGUOUS` (declared is concrete) — 2-way prompt:**
      ```
      ⚠ Backing-repo re-resolution ambiguous on Finding #N:
         Stage 2/3 declared:    {declared}
         Stage 4 re-resolved:   AMBIGUOUS (resolution table's `Other / ambiguous` row)

      어느 쪽으로 진행할까요?
      [a] declared ({declared}) 으로 진행 (사용자가 Stage 2에서 결정한 값을 신뢰)
      [b] 이 finding 은 skip — upstream_feedback 액션 제거
      ```

      **(iii) Declared was `AMBIGUOUS` but re-resolution found a concrete value — 2-way prompt:** mirror of (ii) with `[a]` = use re-resolved, `[b]` = skip.

      Do NOT proceed without an explicit pick. `[b]` (in variant i) requires updating the declared `backing_repo` line — record the corrected value in the Actions Executed report's verification trail rather than re-emitting the entire Stage 3 report (the report is append-only post-Stage-3; corrections live in step 0.5's trail). The skip path removes `upstream_feedback` from the row's action set and logs the divergence reason in the Actions Executed section.

   5. **Verification trail.** Record both values + the chosen path in the Actions Executed report (e.g., `Finding #N: backing_repo verified (declared=live=<resolved-praxis-repo>)` or `Finding #N: divergence resolved via [b] — switched declared <X> → re-resolved <Y>`). This trail is the defense against silent misrouting in retrospective analysis.

   ### Backing repo resolution (used by step 0.2 and as reference)

   | Tool name / layer pattern | Backing repo resolution |
   |---|---|
   | `mcp__<plugin>__*` from a Claude Code plugin | Read `repository` field from that plugin's `.claude-plugin/plugin.json` (or equivalent manifest) |
   | `mcp__<service>-*` from a custom/team MCP server | The MCP server's source repo — `git remote -v` of the server's directory, or read its package manifest |
   | Skill within the praxis distribution itself | The praxis source repo this skill was installed from — read `repository` field in praxis's own plugin manifest |
   | Hook in `~/.claude/hooks/` or a globally symlinked `~/.claude/CLAUDE.md`/`AGENTS.md` | The user's dotfiles backing repo — resolve via `ls -la` symlink chain, then `git remote -v` of the target dir |
   | CLI tool (e.g., `gh`, `kubectl`) | The CLI's open-source upstream if accessible; otherwise `note only` |
   | Builtin tool (Read/Edit/Bash/Grep) | Typically not actionable — `note only` |
   | Other / ambiguous | Ask the user; do NOT fall back to a hardcoded repo |

   If the active project's `AGENTS.md` provides a feature-to-repo mapping, consult it before deciding a repo.

   ### Step 0a — External-repo authorization gate (MUST run before `gh issue create` for external findings)

   This gate fires for every `upstream_feedback` row where the finding's Rationale cell contains `⚠ EXTERNAL: per-action approval required at Stage 4` (set by Stage 2.5 Gate-4). It fires **even when** the user selected "✅ Execute now" in Stage 3 — Stage 3 approval authorizes the action category, not the specific external-org write.

   **Detection:** scan the Rationale cell (already verified in step 0) for the literal prefix `⚠ EXTERNAL: per-action approval required at Stage 4`. If present → this gate fires. If absent → skip to "Then create the issue."

   **Mandatory `AskUserQuestion` prompt (do NOT proceed without explicit `[a]` pick):**

   ```
   ⚠ External-repo write authorization required — Finding #N

   Proposed: create GitHub issue in {backing_repo} (classified external by Stage 2.5 Gate-4)
   Title: {proposed_issue_title}
   Evidence: {one-line friction event summary}

   Per global `~/.claude/CLAUDE.md` "External / third-party repo content isolation (MUST)", this requires
   explicit per-action approval. Stage 3 "Execute now" does not satisfy this gate.
   Auto-mode override, batch approval, and "prior selection ratifies this" inferences
   are all invalid — only an explicit [a] pick here allows proceeding.

   어느 쪽으로 진행할까요?
   [a] 승인 — {backing_repo}에 이슈 생성 진행
   [b] Skip — upstream_feedback 액션 제거 (이 finding의 action set에서 제외)
   [c] Issue draft를 먼저 검토한 뒤 결정 (draft를 보여준 뒤 재질문)
   ```

   - If `[b]` → remove `upstream_feedback` from this finding's action set; log reason in Actions Executed report
   - If `[c]` → show the draft issue body (title, labels, full body text) and re-issue this AskUserQuestion
   - Only `[a]` → proceed to `gh issue create`

   **Then create the issue (using the verified backing_repo from step 0):**
   - Title: `{type}({tool_layer}): {friction description}` (Conventional Commits format)
   - Label: `tool-friction:{layer}` is praxis's own convention. Apply it ONLY when the verified backing repo is the praxis distribution itself. For any other backing repo, use that repo's existing label conventions (e.g., `bug`, `enhancement`); do NOT auto-create praxis-style labels in unrelated repos.
   - If `tool-friction:*` is needed and missing in the praxis repo: `gh label create "tool-friction:{layer}" --repo <verified-praxis-repo>`
   - Body: include evidence, expected behavior, proposed fix direction from step 4b finding
   - Command: `gh issue create --repo <verified_backing_repo> --title "$TITLE" --label "$LABEL" --body "$BODY"` — substitute the verified repo, never hardcode
   - **Verification (mandatory):** issue URL is returned, `gh issue view {url}` succeeds, AND the URL's repo matches the verified backing repo (catches misrouting)

5. **Skill idea note** → Write to `{current_project}/.omc/plans/retrospect-skill-idea-{slug}.md`
   - `{current_project}` = `$CLAUDE_PROJECT_DIR` or `git rev-parse --show-toplevel`
   - Include: problem, proposed skill trigger, pipeline sketch

6. **Hook code** → For enforcement-level actions (repeat 3x+):
   a. Write hook script to `.claude/hooks/` or appropriate location
   b. Present the hook code to user for review
   c. Explain how to register in `.claude/settings.json` (show the exact JSON entry)
   d. Use AskUserQuestion: "Hook을 settings.json에 등록할까요?" (✅ 등록 / ⏭ 파일만 유지 / 🕐 나중에)
   e. If approved: Edit `.claude/settings.json` to register the hook
   f. If skipped/deferred: leave the hook file in place and provide manual registration instructions

7. **Verification** — For each executed action, verify the artifact:

   | Artifact | Verification |
   |----------|-------------|
   | MEMORY.md feedback (new) | File exists + MEMORY.md index updated + `hookable`/`hookKeywords` frontmatter decision recorded (true with keywords, OR false with rationale in Actions Executed report) |
   | MEMORY.md feedback (merged) | Existing file updated (diff shown) + MEMORY.md index description updated if needed + if existing entry had `hookable: false` **or the field is missing entirely** and merged context now meets the retrieval-critical default, re-evaluate and add/update frontmatter (most pre-existing memories lack `hookable` — missing field is the dominant case, not false) |
   | GitHub issue | `gh issue view {url}` returns valid data |
   | Upstream feedback | `gh issue view {url}` returns valid data + URL repo matches `verified_backing_repo` from step 0 + label convention is correct for the verified repo (`tool-friction:{layer}` ONLY when verified repo is the praxis distribution; otherwise the repo's own convention label per Action 4's label rule) |
   | Hook code | Script file exists + settings.json registration confirmed (dry-run varies by hook type — no generic check) |
   | Global `~/.claude/CLAUDE.md` draft | **Project target (`AGENTS.md`)**: Diff shown + explicit approval received + Edit applied. **Global target (`~/.claude/CLAUDE.md`)**: Staging file created at `/tmp/claude-md-draft-{slug}.md` → AskUserQuestion 3-option presented → `apply`: Edit applied + diff shown; `보류`: staging file path logged in completion report. |
   | Skill idea note | File exists in `.omc/plans/` |

   Report verification results in the completion table.

8. **Completion report:**

```
## Actions Executed

| # | Action | Result |
|---|--------|--------|
| 1 | MEMORY.md feedback added | ✅ {file_path} |
| 2 | GitHub issue created | ✅ {url} |
| 3 | Upstream feedback (Finding #N) | ✅ {url} (backing_repo verified: declared=live={owner/repo}) |
| 4 | Upstream feedback (Finding #M) | ⚠ aborted at step 0 — declared {X} ≠ re-resolved {Y}; user picked [b], re-issued at {url} |
| 5 | Upstream feedback (Finding #P) | ⊘ skipped at step 0 — divergence; user picked [c], action removed; reason: declared {X} not reachable, re-resolved {Y} unfamiliar to user |
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
| "Tool issue라서 이번 retrospect scope 밖이다" | Scope 안이다. Step 4b에서 분석하고 `upstream feedback`으로 도구의 backing repo (Stage 4 Action 4 의 routing 표 참고)에 이슈를 남겨야 한다. |
| "도구 결함이지 내 행동 문제가 아니다" | 둘 다일 수 있다. Step 4 (행동 교정)와 step 4b (도구 개선)에 각각 기록하라. 하나만 선택하지 마라. |

## Red Flags — STOP

If you catch yourself:

- Proposing actions before completing Stage 2 analysis
- Writing "root cause: Claude forgot to X" without tracing WHY the forgetting happened
- Adding a MEMORY.md entry that just repeats the global `~/.claude/CLAUDE.md` rule verbatim (no new insight)
- Creating a GitHub issue for every minor friction (low-ROI noise)
- Skipping the approval step and executing actions directly
- Editing `$CLAUDE_CONFIG_DIR/CLAUDE.md` directly (without the staging → AskUserQuestion 3-option path) — global `~/.claude/CLAUDE.md` requires staging + explicit `apply` approval; direct Edit/Write is blocked by the self-modification classifier and skips user review. Use the Global path flow in Action 3 above.
- Proposing `memory` for a pattern that already exists in MEMORY.md (MUST escalate instead)
- Skipping tracer/analyst agent calls ("I can analyze this myself")
- Generating artifacts without verification ("issue created" without showing URL)
- Creating a new memory file without checking existing entries for overlap (MUST merge into existing when root cause matches)
- **Proposing MEMORY.md feedback as the only action when the same rule was violated 3+ times** — this ignores memo's proven limits; enforcement mechanisms (skill, hook, rule) MUST be evaluated alongside memory
- **Proposing MEMORY.md feedback as the only action when the finding is a rule gap (rule absent)** — gaps are not filled by memos; global `~/.claude/CLAUDE.md` draft or skill idea MUST be considered
- **Forcing tool friction into only a rule-violation frame** — tool-layer defects from step 4b MUST be carried in the unified findings table with `Tool Layer` set to a non-`—` value and evaluated for `upstream feedback`, not collapsed into rule-violation-only findings
- **Skipping step 4b entirely** ("no tool issues this session") — step 4b is mandatory. If no tool friction is found, the distribution card MUST emit `upstream_feedback: 0` and the report MUST state "No tool/feature friction detected. ✅" explicitly
- **Pre-scan에서 friction event에 `category[]` 라벨링을 누락한 채 Stage 2 step 3 이상 진행** — Layer E 강제. 누락은 Stage 2 진입 전 차단되어야 한다.
- **Memory-only finding의 `Rationale`이 Gate-2 schema 를 만족하지 않음** — Gate-2 위반. 허용 schema: (a) 5줄 `not <action>: <reason>` 형식 (Schema A) 또는 (b) 1-2줄 `not-others: <dim-tags>` 형식 (Schema B). 일반 한 줄 진술, 3-line `not-others:`, Schema A/B 혼용은 모두 부적격.
- **Stage 2.5 분포 감사를 명시적으로 건너뛰고 Stage 3로 직행** — distribution card와 Gate-1/Gate-2/Gate-3 verdict 출력은 Stage 3 입력의 mandatory 전제.
- **`tool` 라벨 finding의 `Tool Layer` 컬럼이 `—`로 비어 있음** — Layer E ↔ step 4b composition matrix 위반. tool 카테고리는 4b layer 중 하나(mcp/cli/builtin/skill)를 반드시 가져야 한다.
- **`upstream_feedback` 행에 `backing_repo: <owner/repo>` 선언이 없음** — Stage 2 step 8 위반. 선언은 Stage 4 Action 4 step 0의 라우팅 결정 입력이며, 누락 시 Stage 4가 abort 한다.
- **Stage 4 Action 4에서 step 0 (declared vs re-resolved 비교)을 건너뛰고 바로 `gh issue create` 실행** — 이슈가 잘못된 레포로 라우팅되는 정확한 실패 경로. 선언과 재계산 값을 모두 기록하지 않은 채 진행하면 retrospect 자체가 검증 불가.
- **`Proposed Actions` count = 2 인데 두 action 이 decision-coupled (한 쪽이 다른 쪽이 묻는 질문을 이미 결정)** — Gate-3 (b) 위반. 둘 다 실행하면 상호 모순 상태가 만들어지며, `upstream_feedback` 측이 외부 레포에 빈 질문을 남기는 노이즈가 발생한다. 강한 evidence 쪽을 유지하고 약한 쪽은 trigger condition으로 강등.
- **2-action finding 의 각 action 이 ≥1 friction-event observation 을 인용하지 않음** — Gate-3 (a) 위반. Category-default form-filling만으로 만들어진 action 은 evidence-based delivery 원칙과 충돌하며, 첫 번째 action 이 이미 수행한 결정을 두 번째가 반복-질문하는 redundancy 의 전형적 신호.
- **Surfacing an option as `(Recommended)` or default without running a disconfirming test on the recommendation's own premise** — premise verification at HIGH-confidence lock is mandatory. The user's push-back is a trailing signal; pre-emptive self-falsification is the correct path. (Pairs with the upstream `Falsify Before Fix` global rule.)
- **Emitting `AskUserQuestion` with a `(Recommended)` label or confidence-anchoring framing (`safer` / `natural fit` / `안전한` / `자연스러운`) without an accompanying `Falsification:` trace line in the per-finding plan** — Stage 3 Pre-Output Falsification Gate violation. The label asserts ranking confidence that wasn't earned; downstream readers (user, hooks, retrospect parsers) cannot tell whether the premise was tested. If the gate didn't run, drop the label and surface the option unranked.
- **Stage 3 ranking that contradicts Stage 2 caveats** — e.g., `(Recommended)` applied to an action whose Stage 2 row carries `tracer confidence: LOW`, `single observation` alone, or `alternative root cause not ruled out`. Stage 2's caveat is the leading signal; Stage 3 must carry it forward (`Stage 2 caveats:` line) and either discharge it via falsification or suppress the recommendation.
- **`upstream_feedback` 행의 `backing_repo` owner가 own-org 밖인데 Stage 2.5 Gate-4 마킹(`⚠ EXTERNAL: per-action approval required at Stage 4` prefix) 없이 Stage 3 진입** — Gate-4 가 실행되지 않은 것. Stage 2.5로 돌아가 Gate-4 재실행.
- **external-marked finding 의 Stage 4 `upstream_feedback` 실행에서 AskUserQuestion per-action 승인(step 0a) 없이 `gh issue create` 직행** — global `~/.claude/CLAUDE.md` zero-exception 룰 위반. STOP, step 0a AskUserQuestion 실행 먼저.
- **`memory` action 이 있는 finding 에서 `memory_scan` 필드 없이 Stage 2.5 Gate-5 진입** — Stage 2 step 7 스킵 증거. Stage 2 step 7로 돌아가 2-hop 스캔을 완료하고 `memory_scan` 필드를 기록하라. "MEMORY.md를 이미 알고 있어서 스캔이 필요 없다"는 Gate-5 우회 근거로 인정되지 않는다.
- **Stage 3 "Execute now" 선택을 external-repo write 의 per-action 승인으로 ratify** — Stage 3 승인은 action 카테고리 선택이지, 외부 repo 개별 write 승인이 아님. Stage 4 step 0a 게이트를 반드시 별도로 실행.
- **Omitting the `Stage 2 caveats:` line on a finding that has any of: tracer confidence below HIGH, single observation, alternative root cause not ruled out, Gate-3 downgrade, analyst cluster overlap, escape-hatch state** — carry-forward is mandatory whenever ANY of these holds. Silent omission lets Stage 3 rank as if Stage 2 returned clean HIGH-confidence evidence.
- **조사 도구 결과를 completeness 검증 없이 결론에 사용 (premise unverified)** — 다음 두 패턴 모두 "premise falsified" (반증 테스트를 설계한 뒤 통과 → 진행 가능)가 아니라 "premise unverified" (도구 출력 자체의 한계를 검증하지 않은 채 결론에 사용 → STOP) 에 해당한다: (a) `find ... | head -N` 결과로 "파일/모듈 없음" 단정 — **`head` 를 제거한 원 명령 `find ... | wc -l` 을 별도로 실행**해 총 라인 수를 확인하고, cap 초과 시 cap 제거 또는 `grep -rn <token>` narrowing 필요 (`head -N` 파이프 뒤에 `| wc -l` 을 붙이면 cap 으로 잘린 뒤의 라인 수만 세므로 정확히 N개와 cap 초과를 구분할 수 없어 검증이 실패한다); (b) `find <path>` 빈 결과로 "경로/모듈 없음" 단정 — `ls <parent>` 로 path coverage 를 확인하거나 상위 경로로 재시도, 또는 `grep -rn <token>` cross-check 필요.
- **Stage 1.5 hygiene scan을 "MEMORY.md가 작아서" 또는 "이미 다 안다"는 이유로 건너뜀** — Stage 1.5는 unconditional. 코퍼스 크기와 무관하게 cap-and-cursor 메커니즘으로 비용이 amortized 된다. "내가 이미 안다" 는 author-exempt verification trap 의 변형 — Stage 1.5의 detect-only 책임을 Stage 2 friction-scanner 가 대신할 수 없다 (silent recurrence는 마찰 신호 없이 누적).
- **Stage 2.7 audit-skip을 trail 라인 없이 silent으로 처리** — `<!-- retrospect:audit_skipped: no artifacts -->` (또는 `transcript unreadable` variant)은 mandatory. trail 라인 부재 시 "Stage 2.7이 실행되었는가" vs "스킵되었는가" vs "잊혀졌는가" 를 retrospective audit이 구분할 수 없다. 0-trigger silent skip path도 trail 라인은 emit 한다.

**ALL of these mean: STOP. Return to Stage 2.**

## Quick Reference

| Stage | Key Activity | Success Criteria |
|-------|-------------|-----------------|
| **1. Load** | Read global `~/.claude/CLAUDE.md`, form scan questions | Rule categories identified |
| **1.5 Hygiene** | Detect-only MEMORY.md scan — stale references / contradictions / merge candidates (cap 5 files/invocation + cursor carryover) | Stage 1.5 findings emitted with `category: memory_hygiene` (or `hygiene_skipped` trail if MEMORY.md unreachable) |
| **2. Analyze** | Scan conversation, map to rules, find root cause | Root cause (not symptom) for each pattern; every event has `category[]` |
| **2.7 Audit** | Adaptive post-hoc artifact audit — fires only when session contains `gh pr|issue|comment` / Slack-Notion MCP write / approved external-write events; runs 3 sub-audits (PR mergeability, sub-agent substance, external comment evidence) | Stage 2.7 findings emitted with `category: output_quality` (or `audit_skipped` trail if 0 triggers) |
| **2.5 Audit** | Run Gate-1 (categorical) + Gate-2 (Schema A 5-line or Schema B dimension-tag rationale) + Gate-3 (evidence robustness for 2-action findings) + Gate-4 (external-repo authorization pre-check for upstream_feedback) + Gate-5 (memory-scan completeness for memory-action findings) | All applicable gates PASS/WARN or per-finding cap reached and surfaced to user |
| **3. Report** | Present unified table + distribution card, carry Stage 2 caveats forward, run Pre-Output Falsification Gate before each `AskUserQuestion`, collect approval per item | User approved at least 1 item (or confirmed 0 findings); every `(Recommended)` label has a `Falsification:` trace |
| **4. Execute** | Run approved actions, verify artifacts | Completion report with links/paths + verification results |

## Error Handling

| Stage | Failure | Action |
|-------|---------|--------|
| Stage 1 (load) | `~/.claude/CLAUDE.md` not found (global) or `AGENTS.md` not found (project) | Proceed with global defaults; flag the missing file in the report |
| Stage 1.5 (hygiene) | MEMORY.md index not accessible | Skip Stage 1.5 entirely; emit `<!-- retrospect:hygiene_skipped: index not accessible -->` trail line; proceed to Stage 2 |
| Stage 1.5 (hygiene) | Cursor file (`.omc/state/retrospect-hygiene-cursor.json`) corrupt | Reset cursor to most-recently-modified 5 files; log reset in completion report |
| Stage 1.5 (hygiene) | Per-file scan failure (one of N files unreadable) | Continue with remaining files; record per-file failure in Stage 3 report Hygiene Scan Trail section |
| Stage 2 (analyze) | Session history not accessible | Fall back to the user's verbal summary as input to steps 3–8 |
| Stage 2.7 (audit) | Trigger detection scan failed (e.g., transcript unreadable) | Emit `<!-- retrospect:audit_skipped: transcript unreadable -->` trail line; skip Stage 2.7; proceed to Stage 2.5 |
| Stage 2.7 (audit) | 0 triggers detected (no PR/external-write artifacts in session) | Silent skip with mandatory trail line `<!-- retrospect:audit_skipped: no artifacts -->` AND `output_quality: 0` in distribution card |
| Stage 2.7 (audit) | `gh pr view` API failure for a specific PR | Log per-PR error in Stage 3 report Audit Trail section; continue with remaining PRs |
| Stage 2.7 (audit) | Mid-session artifact deletion (e.g., PR was created then closed by user before retrospect) | Treat as audit signal — closed-without-merge IS a finding (per Sub-audit 1 trigger). If artifact entirely removed (issue/PR deleted), log "artifact no longer accessible" in Audit Trail; do not emit finding for that artifact |
| Stage 2 (analyze) | No friction events found | Exit with "No patterns found. ✅" — do not fabricate findings |
| Stage 2 (analyze) | MEMORY.md scan failed (file not accessible) | Treat all findings as new patterns (repeat=false). Flag scan failure in report |
| Stage 2 (analyze) | MEMORY.md is empty | Normal processing — all findings are new patterns |
| Stage 2 (analyze) | tracer/analyst call failed | Fall back to manual analysis. Flag agent failure in report. Warn about reduced root cause quality |
| Stage 2 (analyze) | Pre-scan event missing `category[]` label | Block Stage 2 progression to step 3; instruct LLM to backfill labels per Layer E enumerated values |
| Stage 2.5 (audit) | Gate-1 violation persists after 2 per-finding re-entries | Surface to user with override prompt; log user-supplied rationale |
| Stage 2.5 (audit) | Gate-2 violation persists after 2 per-finding re-entries | Surface to user with override prompt; log user-supplied rationale |
| Stage 2.5 (audit) | Gate-3 violation persists after 2 per-finding re-entries | Surface to user with 3-way override prompt (`[a] rationale 직접 입력 / [b] action 직접 지정 / [c] note only 강등`); log selection |
| Stage 2.5 (audit) | Gate-3 (c) downgrade collapses action set to memory-only on a tool/workflow/spec-gap finding | Re-trigger Gate-1; counts toward the per-finding loop cap of 2 |
| Stage 2.5 (audit) | Behavioral-only safeguard triggered (tool keywords detected in pre-scan signals) | Surface to user; require explicit confirmation before proceeding to Stage 3 |
| Stage 2.5 (audit) | Gate-4 — `backing_repo` declaration absent (cannot extract owner) | Skip this finding in Gate-4; Stage 4 Action 4 step 0's missing-declaration abort handles it downstream |
| Stage 2.5 (audit) | Gate-4 — `gh api user --jq .login` fails and `PRAXIS_OWN_ORGS` is unset | Conservative default: treat all `upstream_feedback` findings as external; emit `gate_4_verdict: WARN` |
| Stage 2.5 (audit) | Gate-5 — memory-action finding missing `memory_scan` field after 2 re-entries | Surface to user with 3-way override prompt (`[a] 직접 MEMORY.md 읽고 memory_scan 입력 / [b] action 직접 지정 / [c] note only 강등`); log selection |
| Stage 2.5 (audit) | Gate-5 — MEMORY.md index not accessible during step 7 scan | Record `memory_scan: {scanned: true, candidates_reviewed: [], error: "index not accessible"}` and treat all findings as new patterns (repeat=false) |
| Stage 3 (report) | Pre-Output Falsification Gate triggered but premise cannot be falsified or survives only ambiguously | Drop `(Recommended)` label; surface option unranked; emit explicit premise-confirmation line in `AskUserQuestion` per the gate's "Falsification step not run" row |
| Stage 3 (report) | Finding lacks Stage 2 caveats line despite tracer confidence below HIGH or single-observation flag | Block Stage 3 emission for that finding; return to Stage 2 step 5 (root cause refinement) or Stage 2.5 Gate-3 (c) (single-observation downgrade) and re-derive |
| Stage 3 (report) | User rejects all findings | Capture the rejection itself as a feedback signal for future retrospects |
| Stage 4 (execute) | MEMORY.md write fails | Report the path error; never silently drop the feedback |
| Stage 4 (execute) | GitHub issue creation fails | Fall back to saving a note in `.omc/plans/` for later manual creation |
| Stage 4 (execute) | Upstream feedback issue creation fails | Fall back to saving a note in `.omc/plans/tool-friction-{slug}.md` with intended `tool-friction:{layer}` label and issue draft |
| Stage 4 (execute) | `tool-friction:*` label doesn't exist (and the verified backing repo is the praxis distribution) | Auto-create with `gh label create "tool-friction:{layer}" --repo <verified-praxis-repo>` and retry |
| Stage 4 (execute) | Action 4 step 0 — `backing_repo` declaration missing from finding row | ABORT this action; return finding to Stage 2 step 8 with prompt to emit declaration; do NOT fall back to project repo |
| Stage 4 (execute) | Action 4 step 0 — declared vs re-resolved `backing_repo` divergence | ABORT this action; surface `AskUserQuestion` per step 0.4 prompt variants (3-way `[a] declared / [b] re-resolved / [c] skip-upstream_feedback-action`, or 2-way for AMBIGUOUS cases); do NOT auto-pick |
| Stage 4 (execute) | Action 4 step 0a — external authorization `AskUserQuestion` declined (`[b]` picked) | Remove `upstream_feedback` from finding's action set; log in Actions Executed as skipped with reason |
| Stage 4 (execute) | Action 4 step 0a — external authorization gate skipped without `AskUserQuestion` | STOP; return to step 0a; never auto-proceed on external-repo writes |

## Integration

**Entry point:** End of a working session, or after a particularly rough workflow experience
**Exit point:** Completion report shown — improvements applied to the next working session

**OMC delegation:**
- `tracer` agent: causal chain analysis for complex friction patterns
- `analyst` agent: cluster multiple friction events into root causes
- Project's issue creation skill: GitHub issue creation in Stage 4
