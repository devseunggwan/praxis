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

**Cap (mandatory):** scan up to **5 feedback files per invocation**. Persist a cursor at `.omc/state/retrospect-hygiene-cursor.json` recording (a) the timestamp of the last successful pass, (b) the list of file paths scanned (referred to as `scanned_recent_batch` on subsequent reads), (c) the next-batch pointer (`next_batch_pointer` — path of the next un-scanned feedback file in sorted order), (d) the per-signal pairwise-check trail for the batch (`signal_2_contradiction` / `signal_3_merge_candidate`, each `checked: yes|no, matches: N`), and (e) a `note` field holding the previous cycle's signal 1/2/3 findings (one line per finding — keep terse, e.g., `signal 1: feedback_promoted_section.md L5-17 — 12 markdown links to backing files ABSENT`). Field (d) is the home for the zero-match proof — a clean batch emits no finding, so the negative result is recorded here in the scan trail, not in a (non-existent) per-finding block. Field (e) is the carry-forward home — findings that did not complete a Stage 4 resolution in their original cycle live here until they do. Subsequent retrospects resume from the cursor — full corpus coverage amortizes across multiple sessions. When the full corpus has been scanned once, rotate the cursor to restart from the most-recently-modified entries.

**Cursor read mandate (entry — MUST run first).** If `.omc/state/retrospect-hygiene-cursor.json` exists, before scanning ANY feedback file:

- (a) **Use `next_batch_pointer` to choose the batch start** — do NOT make an ad-hoc sort/filter decision (e.g., "scan the 5 most recently modified" / "scan the 5 alphabetically first"). The cursor IS the schedule; overriding it silently re-scans files Stage 1.5 already passed over in the prior cycle and starves the un-scanned tail of the corpus.
- (b) **Exclude files listed in `scanned_recent_batch` from this batch** — avoid re-scan of the immediately prior batch even if `next_batch_pointer` would otherwise fall inside it (defends against a corrupt or stale pointer).
- (c) **Carry forward findings stored in the cursor's `note` field** (signals 1/2/3 from the previous cycle) into Stage 3 input. The carried findings flow through Stage 2.5 gates and Stage 3 emit alongside any new findings produced by this batch's scan; they are NOT silently dropped just because the file containing the finding falls outside the current batch's 5-file window. See Stage 3 "Carry-forward integration" for the emit-side contract.

Silent skip of the cursor read (entry without honoring (a)/(b)/(c)) is a Red Flag — it is the structural enabler of the failure mode where a prior cycle's hygiene finding gets contradicted by the current cycle without falsification.

**Cursor write mandate (exit — read-modify-write union under concurrency, MUST).** The cursor is shared mutable state, and running `/retrospect` from two sessions concurrently or interleaved is a common multi-session workflow. A plain Write at exit clobbers a sibling session's just-persisted `note` (field (e)) and scan trail (field (d)): both sessions read the same cursor at entry, both advance `next_batch_pointer` to the same batch, and the last writer silently drops the first writer's findings and carry-forward. Before persisting the cursor at the end of Stage 1.5, **re-read** the on-disk `.omc/state/retrospect-hygiene-cursor.json` and compare it against the values observed at the entry read (Cursor read mandate above):

Field (a) is re-stamped with a fresh write-time timestamp on *every* persist, so `on-disk (a) != entry-read (a)` is the **primary** concurrent-advance discriminator — it is the one field guaranteed to differ when a sibling persisted, including the headline #568 case where both sessions advanced to the *same* batch and therefore wrote an identical `next_batch_pointer` / `scanned_recent_batch`. Do NOT rely on (c)/(b) alone to detect concurrency: in the same-batch case they match this session's own intended values and would miss the sibling write.

- (a) **No concurrent advance** — the on-disk last-pass timestamp (field (a)) equals the entry-read value AND `next_batch_pointer` is unchanged: write the new cursor normally. Plain overwrite is safe because no other actor touched the file in this window.
- (b) **Concurrent advance detected** — the on-disk last-pass timestamp (field (a)) differs from the entry-read value, OR `next_batch_pointer` / `scanned_recent_batch` differ from it: a sibling session persisted between this session's entry read and exit write. Do NOT overwrite — UNION-merge the on-disk cursor with this cycle's result before writing:
  - **`note` (field (e))**: concatenate the on-disk `note` lines with this cycle's `note` lines, then drop byte-identical duplicate lines. Both sessions' carry-forward findings survive.
  - **scan trail (field (d))**: union the `signal_2_contradiction` / `signal_3_merge_candidate` records keyed by batch — records for *distinct* batches are BOTH retained, because each batch's pairwise-proof (including a zero-match proof) must survive for downstream audit and a different batch's count is not a substitute proof. Only when both sides recorded the same signal for the *same* batch do they conflict; resolve that conflict by keeping the higher `matches` count (a confirmed match is strictly more informative than a zero).
  - **`next_batch_pointer` (field (c))**: advance to the FURTHER of the two pointers (the later position in sorted order) so neither session re-scans a batch the other already passed — never rewind to this session's older value.
  - **`scanned_recent_batch` (field (b))**: union the two file lists.
- (c) **Record the reconciliation** — when (b) fired, add one line to the Stage 3 Hygiene Scan Trail: `Cursor write: concurrent advance detected — union-merged note (<n_self> self + <n_disk> on-disk lines) + advanced pointer to <pointer>`. This makes the merge auditable by the next cycle.

Silent overwrite of a concurrently-advanced cursor (exit without honoring (a)/(b)/(c)) is a Red Flag. This write mandate is the cross-session complement of the single-session "Falsification gate before invalidating a carried finding" below: the falsification gate stops a session from *semantically* dropping a carried finding without a probe; this write mandate stops a session from *mechanically* clobbering a sibling session's findings via a stale-snapshot overwrite. Both protect field (e); neither subsumes the other.

**Five detection signals (each becomes a finding with `category: memory_hygiene`):**

1. **Stale reference** — the entry cites an artifact that no longer matches reality:
   - File path absent: entry references `<path>` that does not exist (verify with `test -e`)
   - CLI flag absent: entry cites `<binary> --<flag>` but `<binary> --help` no longer documents it
   - CLAUDE.md line shifted: entry cites `~/.claude/CLAUDE.md` line `N` whose content no longer matches the quoted excerpt (verify with `grep -n` of the cited excerpt)
   - Skill / hook removed: entry references a skill name or hook path that no longer exists in the plugin manifest

   Detection method: parse the entry's body for `\bgrep -n\b`-able tokens (file paths, CLI flag patterns `--[a-z-]+`, CLAUDE.md line citations); run the verification command; record failures as stale.

   **Probe guideline — multi-path enumerate before concluding stale (MUST):** a single-path not-found result (e.g., `test -e <path>` returns false, `grep -n <excerpt> <file>` returns 0 matches) is insufficient to conclude the entry is stale — the target may have been renamed or moved rather than removed. Before recording a stale finding, try at least one additional verification path:
   - File path absent: also try `find . -name "$(basename <path>)"` to detect **moves** (same filename, new location). This does not catch a true rename (basename changed) — for that, grep a unique symbol / quoted excerpt from the entry across the tree (`grep -rn "<unique token>" .`) before concluding stale.
   - CLI flag absent: also try `<binary> help <subcommand>` or `man <binary>` to catch flag aliases or nested help pages that `--help` alone does not print.
   - CLAUDE.md line shifted: also try `grep -n` with a shorter unique substring of the cited excerpt (the full excerpt may span lines that were reformatted).
   - Skill / hook removed: also try searching the manifest's alias or `name` field, not only the file path.
   Only when all attempted paths fail does the entry qualify as a confirmed stale finding. Record both the primary probe command and the secondary probe(s) in the finding's evidence block.

   **Probe guideline — stored-value oracle match (MUST):** when the stale-looking citation is a **stored measurement / numeric value** (not a file path, CLI flag, or line citation), concluding it stale additionally requires same-oracle confirmation per Stage 2 step 7h — a probe run under a different matching basis / cohort / unit measures a different quantity and cannot mark the stored value stale. If the entry body does not state its originating oracle, DEFER falsification and propose an entry-annotation update instead (route as `memory`, Stage 4 update path). See step 7h for the full rule and `oracle_match` ledger format.

2. **Contradiction** — two entries provide semantically opposed guidance under overlapping triggers:
   - Entry A says "always X under trigger T"; Entry B says "never X under trigger T" — same T, opposite directive.
   - Detection method: concept-level pairwise comparison restricted to entries whose `hookKeywords` (or trigger tokens) overlap. Use LLM judgment for semantic opposition (no regex shortcut). Cap pairwise comparisons to the cursor-batch's 5 files × full-index check.

   **Pairwise-proof requirement (MUST):** record pairwise check metadata in the format `checked: yes, matches: N` where N is the count of contradiction pairs found. "0 across batch" alone is INELIGIBLE — stating a zero count without explicitly confirming that the pairwise comparison was executed is indistinguishable from skipping the check. Placement depends on N: when **N ≥ 1**, record it in each emitted contradiction finding's evidence block; when **N = 0**, no finding is emitted, so record `signal_2_contradiction: checked: yes, matches: 0` in the Stage 1.5 cursor scan trail (field (d) above) — never fabricate a finding just to carry the proof. This requirement exists so downstream retrospects can audit whether Signal 2 ran or was silently elided.

3. **Merge candidate** — two or more entries share a root-cause family but have not been merged:
   - Same principle restated with different examples (the merge-first policy at Stage 4 Action 1d should have collapsed them).
   - Detection method: read each entry's `description` field and root-cause prose; group by concept-level matching (same heuristic as Stage 4 Action 1 duplicate-check). When N ≥ 2 entries share a family AND none has been merged, emit a merge-candidate finding.

   **Pairwise-proof requirement (MUST):** record pairwise check metadata in the format `checked: yes, matches: N` where N is the count of merge-candidate groups found. "0 across batch" alone is INELIGIBLE — stating a zero count without confirming the pairwise comparison was executed is indistinguishable from skipping the check. Placement depends on N: when **N ≥ 1**, record it in each emitted merge-candidate finding's evidence block; when **N = 0**, no finding is emitted, so record `signal_3_merge_candidate: checked: yes, matches: 0` in the Stage 1.5 cursor scan trail (field (d) above) — never fabricate a finding just to carry the proof. Same audit rationale as Signal 2.

4. **Size threshold** — the MEMORY.md **index file itself** has grown past a configurable threshold, increasing the risk that the index is silently truncated at load time and that downstream Stage 1 compaction restoration is incomplete (the upstream structural enabler called out by praxis issue #387). Triggered when EITHER condition fires:
   - Index line count ≥ `PRAXIS_RETROSPECT_INDEX_LINE_THRESHOLD` (default `200`)
   - Index byte size ≥ `PRAXIS_RETROSPECT_INDEX_BYTE_THRESHOLD` (default `30720` — i.e. 30 KB)

   Detection method: single `wc -l` + `stat -c %s` (or equivalent) on the index file. Signal 4 is **index-scoped (one file)** and unrelated to the per-batch `feedback_*.md` cursor — it fires on every Stage 1.5 invocation when the threshold is met, not on a cursor schedule. Threshold env vars that fail to parse as positive integers silently fall back to defaults.

   **Link-detection helper (link-convention-agnostic)** — subsignals 4a and 4b both consume a single shared link scanner. The scanner recognizes a reference from index entry A to artifact B when B appears inside A's body in ANY of three forms:

   - **Wikilink**: `[[B]]` or `[[path/B]]` (Obsidian-style)
   - **Markdown link**: `[text](path)` where `path` resolves to B (relative or absolute), allowed extensions `.md` / `.txt` / `.json` / `.py` / `.sh`
   - **Bare basename mention**: a single-token `<B>.<ext>` (≥4 chars, allowed extensions above) appearing as plain text outside code fences

   This contract MUST hold for ANY user's MEMORY.md regardless of their personal link convention. A scan failure on one form falls through to the next; only when all three forms fail to match does the entry count as backlink-zero. The scanner has no user-environment assumption beyond "MEMORY.md is markdown".

   **Subsignals (each routed via Stage 4):**

   a. **Promoted-pointer compression** — an index entry whose body has been promoted to a parent rule file (`~/.claude/CLAUDE.md`, a SKILL.md, a hook spec) should collapse to a single-line pointer. "Promoted" is recognized when the entry body contains at least one outbound reference (via the helper above) to a file under `~/.claude/`, `<plugin_root>/skills/`, `<plugin_root>/hooks/`, or `<plugin_root>/docs/` that actually exists. Routed as `memory` (Stage 4 Action 1 update path) per matched entry; the rewrite replaces the body with a single-line link to the promotion target.

   b. **Backlink-zero archive** — an index entry that NO other index entry references (via the helper above) after a grace period is a candidate for archive. Backlink counting iterates every other entry's body once per scan; entries with `incoming_refs == 0` AND `age_in_sessions ≥ PRAXIS_RETROSPECT_BACKLINK_ZERO_GRACE_SESSIONS` (default `5`) become candidates. Routed as `issue` (Stage 4 Action 2 — human-confirmed archive move; never auto-delete).

   c. **New-entry length cap** — enforce ≤ `PRAXIS_RETROSPECT_INDEX_LINE_CAP` (default `150`) chars per index line at write time. Routed as a write-time guard recorded in the entry's Proposed Actions; Stage 4 Action 1 applies the cap before the write (truncation with `…` marker + detail body file is the canonical pattern).

   **Self-applicability** — Signal 4 catches the retrospect skill's own monotonic-growth pattern: each retrospect adds memory entries, but without a hygiene loop the index outgrows its load-time budget. Signal 4 closes this loop by surfacing the size threshold as an actionable finding, not just an emitted warning.

5. **Missing oracle annotation** — a memory entry stores a numeric / measurement / comparison value (latency delta, row count, comparison sum, count) whose body does NOT state the **oracle** (matching basis, cohort, unit) that produced the value. Such an entry cannot be falsified later: a future cycle has no way to know which oracle to re-probe with, so any correction risks the multi-oracle mismatch that Stage 2 step 7h / Gate-6 guard against.

   Detection method: parse the entry body for stored numeric tokens (a number paired with a comparison or measurement verb — `is N faster`, `count = N`, `sum = N`, `N rows`, `Nh`, `Nms`); for each, check whether the body also states a matching basis / cohort / unit. Detect-only — no value is re-measured at Stage 1.5.

   Routed as `memory` (Stage 4 Action 1 update path): the rewrite adds an explicit oracle/unit annotation to the entry body so a future cycle can falsify with a known basis. This is the defer-and-annotate fallback referenced by step 7h step (1).

**Output — emit per-defect findings into the Stage 2 friction-event lane.**

Each Stage 1.5 finding has:
- `category[]: [memory_hygiene]` — single category at Stage 1.5; downstream gates may add tool/workflow when defect originates from a tool layer (e.g., manifest staleness from a removed hook).
- `Tool Layer: —` (default for memory_hygiene; the Stage 2 Layer E composition matrix carves out a `—` row for memory_hygiene the same way it does for behavioral).
- `Proposed Actions` (provisional, finalized at Stage 2.5):
  - Stale reference → `memory` (update entry inline) OR `claude_md_draft` (when the stale citation is itself a CLAUDE.md rule line that needs updating)
  - Contradiction → `memory + issue` (surface both entries via an issue for human resolution; merge in Stage 4 only after explicit decision)
  - Merge candidate → `memory` (route through Stage 4 Action 1 merge path; the merge IS the action)
  - Size threshold / 4a promoted-pointer compression → `memory` (Stage 4 Action 1 update path; body collapses to a single-line link to the promotion target)
  - Size threshold / 4b backlink-zero archive → `issue` (Stage 4 Action 2 — human-confirmed archive move; never auto-delete)
  - Size threshold / 4c length cap → `memory` (Stage 4 Action 1 with the cap as a write-time guard; truncate + spill detail body to a separate file)
  - Missing oracle annotation (signal 5) → `memory` (Stage 4 Action 1 update path; add an explicit oracle/unit annotation to the entry body)

**0-friction hygiene-only retrospect path** — when Stage 2 pre-scan finds zero friction events but Stage 1.5 produced ≥1 hygiene finding, the skill does NOT take the "No patterns found ✅" early-exit (Stage 2 line). Instead, proceed to Stage 2.5 → Stage 3 with the hygiene findings as the sole input. Emit a banner in Stage 3 report: `Hygiene-only retrospect — no friction events this session.`

**Stage 3 banner for signal 4 (size_threshold)** — when signal 4 fires (regardless of whether the 0-friction hygiene-only path is also active), emit ONE additional banner line in the Stage 3 report immediately under the section header:

`Index size threshold tripped — {lines} lines / {bytes} bytes (limit: {line_threshold} / {byte_threshold}). Cleanup actions queued: {n_4a}× promoted-pointer compression, {n_4b}× backlink-zero archive, {n_4c}× length-cap write-guard.`

When signal 4 does NOT fire, the line is omitted entirely (no empty banner). This banner is informational and counted under `memory_hygiene` in the distribution card — it does not introduce a new fence parser key.

**Stage 3 emit — Hygiene Scan Trail (carry-forward integration).** The Hygiene Scan Trail block in the Stage 3 report carries TWO lines whenever the cursor's `note` field (field (e)) was non-empty on entry:

```
Hygiene Scan Trail
- Current cycle: signal 1/2/3/4 = <one-line summary per signal that fired this cycle, or `none` if the signal produced no finding>
- Carried from previous cycle (cursor note): signal 1 = <verbatim copy of the carried finding(s)>; signal 2 = ...; signal 3 = ...
```

The two lines are separate by construction — current-cycle findings and carried findings MUST NOT be merged into a single line, even when the same signal number appears on both sides. Reviewers (and the next cycle's Stage 1.5) read the second line to decide whether the carried finding is still outstanding.

**Falsification gate before invalidating a carried finding (MUST).** When the current cycle's conclusion contradicts a carried finding (e.g., current cycle says "Promoted section self-managed" while carried `note` says "12 markdown links to backing files ABSENT"), the contradiction is NOT a license to silently overwrite the carried finding. Before the carried finding may be dropped, marked resolved, or replaced by the new conclusion, the skill MUST run an explicit falsification — re-execute the carried finding's original probe (path existence test, grep, manifest lookup) against the *current* tree and record the probe command + observed output in the Hygiene Scan Trail as a third line:

```
- Falsification of carried finding (signal N): probe=`<command>` output=`<observed>` → carried finding RESOLVED|STILL_OUTSTANDING
```

If the probe shows the carried finding is genuinely resolved (e.g., the previously-absent files now exist), drop it from the next cycle's `note` field. If the probe re-confirms the finding (still absent / still contradictory / still merge-candidate), the carried finding REMAINS in the cursor `note` for the next cycle regardless of any new-cycle conclusion that disagrees with it. Silent overwrite — emitting a current-cycle conclusion that disagrees with the carried finding without running the falsification probe — is a Red Flag (this is the exact failure mode the issue protecting this section was filed against).

When the carried finding is a **stored-value correction** (a numeric measurement / comparison sum / count, not a path / flag / merge-candidate), the re-executed falsification probe MUST satisfy step 7h oracle-match — i.e. it MUST use the SAME oracle (matching basis, cohort, unit) as the stored value — before the carried finding may be marked RESOLVED; a different-oracle probe is not valid falsification and leaves the carried finding STILL_OUTSTANDING.

**Self-conflict detection (optional, advisory).** Before Stage 3 emit, perform a lightweight keyword-overlap check between the cursor `note` text and the current cycle's signal-1/2/3 conclusion text. When ≥1 shared content noun overlaps (file path, section name, or unique identifier) AND the two predicates are opposite (`absent` vs `self-managed`, `stale` vs `fresh`, `contradictory` vs `consistent`, `merge candidate` vs `distinct`), STOP-surface to the user with the trail of both texts and ask whether the carried finding should be retained, falsified, or revised. This advisory check is a defense-in-depth layer above the falsification gate; the gate remains MANDATORY whether or not this check fires.

**Stage 1.5 failure modes:**
- MEMORY.md not accessible → skip Stage 1.5 entirely; emit `<!-- retrospect:hygiene_skipped: index not accessible -->` trail line; proceed to Stage 2.
- Cursor file corrupt → reset cursor to most-recently-modified 5 files; log reset in completion report.
- Per-file scan failure (one of N files unreadable) → continue with remaining files; record per-file failure in the Hygiene Scan Trail section of Stage 3 report.
- Index file unreadable for size measurement → skip signal 4 only (signals 1-3 proceed normally); emit `<!-- retrospect:hygiene_size_threshold_skipped: index unreadable -->` trail line in Stage 3 report.
- Threshold / cap env var malformed (non-integer, negative) → fall back to documented default silently; no Stage 1.5 failure.
- Promotion-target candidate paths unresolvable (plugin root not in scope, custom MEMORY.md layout) → subsignal 4a downgrades to a no-op for that entry (no finding emitted) rather than blocking signal 4.

**Detect-only contract** — Stage 1.5 never writes to MEMORY.md, never deletes a feedback file, never edits the MEMORY.md index. All mutations route through Stage 4: Action 1 merge path (merge candidates), Action 1 update path (stale references / promoted-pointer compression / length-cap rewrites), Action 2 issue creation (contradictions / backlink-zero archive proposals). Inline mutation at Stage 1.5 is a Red Flag.

### Stage 2: Analyze Conversation

**Pre-scan: Symmetric scan (friction + successful + tool usage + user correction)** — scan the conversation BEFORE calling agents. Produces four lanes:

- **friction_events**: up to 5 friction events (user corrections, retries, skipped steps, stalls). This provides the input for agent calls.
- **successful_patterns**: up to 3 patterns that worked well this session. Each entry MUST include:
  - `pattern`: one line (e.g., "deep-dive 3-lane trace identified cross-lane contradiction via direct grep")
  - `evidence`: specific turn or artifact citation (mandatory — abstract reinforcement like "was helpful" or "responded fast" is FORBIDDEN)
  - `reinforce_action`: `memory` (capture as MEMORY.md entry; counted in Stage 2.5 distribution card `memory` total — see Stage 4 Action 1 for the origin-prefix convention) or `visualize_only` (display in Stage 3 Reinforced Patterns section, no persistent action)
- **tool_census**: deterministic inventory of EVERY tool invoked in the scope window (same window as Stage 2 / Stage 2.7: last 50 turns or last session boundary). Unlike `friction_events` (narrative-driven, operator-interpreted), this lane is machine-derived from the transcript so a tool that ran cleanly is still inventoried — closing the blind spot where only tools that *surfaced pain* get examined. It REUSES the Stage 2.7 transcript-scan MECHANISM but broadens its coverage on two axes so the "EVERY tool" promise actually holds: (a) **tool-call types** — Stage 2.7 scanned only Bash invocations + a write-artifact MCP allowlist; the census scans EVERY `tool_use` entry in the transcript: Bash, **all builtin tools** (`Read` / `Edit` / `Write` / `Grep` / `Glob` / `NotebookEdit` / etc.), MCP, and `Agent` dispatches; (b) the write-artifact allowlist is dropped (all tools, not just writes). Axis (a) is load-bearing: without scanning builtin calls, a `builtin`-layer design defect such as Read/Grep output truncation could never be inventoried and the builtin-truncation promotion rule (step 4b Q1) would be permanently dead. The census feeds **step 4b** (which iterates the inventory, not just friction events — see step 4b "Census-driven lane"). The **promotion decision** (which inventory rows clear the step 4b thresholds + dedup + cap) is deterministic and is computed HERE during pre-scan — before the Stage 2 Early-exit decision — so the Early-exit "Census carve-out" can observe the promoted set (see that carve-out's Ordering invariant). step 4b later consumes this same decision; it does not re-decide. Per-tool inventory row:

  ```
  <!-- retrospect:tool_census begin -->
  - tool_name: <e.g. "gh", "Bash", "mcp__<server>__<tool>", "Read", "<skill-name>">
    layer: mcp | cli | builtin | skill
    call_count: N
    error_count: N             # calls that returned an error / non-zero exit / exception
    retry_count: N             # same tool+params RE-ISSUED after a FAILED/errored/unexpected-output prior call. Failure-driven only — intentional polling / repeated status reads / background-output re-reads (e.g. write_stdin polls, airflow_*_progress polling, Read of a growing log) are NOT retries and MUST NOT increment this counter even when consecutive.
    workaround_marker: true|false   # "fallback"/"manual"/"우회"/"workaround" within 2 turns of the call
    surfaced_in_friction: true|false  # dedup flag — true ONLY when the friction-driven lane already emitted a TOOL-DEFECT finding for THIS tool_name (a friction event with `tool` ∈ category[] AND same tool_name). A tool merely appearing in a behavioral/workflow friction event (not itself a tool-defect finding) does NOT set this true — that event is a different finding and must not suppress a distinct census tool-defect signal for the same tool. See step 4b "Census dedup".
    signal: none | <objective-signal summary>   # promotion key; see step 4b thresholds
  <!-- retrospect:tool_census end -->
  ```

  `signal` is the promotion key: a row becomes a `tool` finding ONLY when `signal != none` (step 4b). Rows with `signal: none` stay in the trail (audit), never a finding — a cleanly-used tool is evidence the tool worked, not a defect. This mirrors `dismissed_candidates` (full audit log, selective promotion) and prevents MEMORY.md inflation. The census trail fence is informational — the Stop hook's awk parser (`hooks/completion-verify/retrospect-mix-check/impl.sh`) captures only the distribution-card fence + unified table, so the trail is ignored.

- **user_correction**: deterministic candidate scan of USER turns in the same scope window (last 50 turns or last session boundary) for correction / redirect signals, followed by per-candidate LLM judgment. This lane closes the **user-facing** analogue of the blind spot `tool_census` closed for tools. Today a user correction lands ONLY in `friction_events`, which is *narrative-driven, operator-interpreted* — the very agent that disappointed the user is the sole interpreter of whether an utterance "counts", capped at 5, with NO deterministic enumeration source for user-correction utterances (the Pre-scan Checklist's deterministic sources cover review-bot findings / workflow divergence / retried commands — never user turns). The highest-value retrospect signal (the user directly stating what was wrong) is thus routed through the least-deterministic lane. The marker scan provides a machine-derived candidate floor the agent cannot self-servingly omit; the LLM judge provides precision; dropped candidates are audited.

  **Two-stage detection (marker-gated LLM):**
  1. **Deterministic marker scan** of user turns generates candidates. Marker taxonomy = **Core 3** classes — the recall floor (a correction class with no marker is never even seen as a candidate, so the LLM judge can never recover it):
     - **negation / stop** (ledger `marker_class: negation`): `아니` · `그거 아니야` · `하지 마` · `그거 말고` · `no` · `don't` · `stop`
     - **redirect / re-instruction** (ledger `marker_class: redirect`): `~라니까` · `~하라고 했잖아` · `그게 아니라` · `내 말은` · `다시` · `I said X` · `not that`
     - **mismatch-callout** (did-X-not-Y; ledger `marker_class: mismatch`): `~하라니까 ~하고 있네?` · `왜 X 안 하고 Y?` · `왜 ~했어` · `that's not what I asked`
     - Implementation note (input-surface enumeration — global `~/.claude/CLAUDE.md` "Regex in Korean/English mixed text"): Python `re.\w` / `\b` is Unicode-aware and places NO boundary between Hangul and ASCII; when an ASCII boundary is the intent in mixed KO/EN text, use `(?<![a-z])foo(?![a-z])` and verify on real mixed input before relying on the pattern. repeat-complaint / undo-request / frustration-tone classes are OUT of v1 scope (lower precision).
  2. **Per-candidate LLM judgment**: each marker-matched candidate is judged genuine-correction vs false-positive (a benign "no" answering a yes/no question, a quoted string, a hypothetical, a correction aimed at a third party rather than the agent).

  **Routing of the two outcomes:**
  - **genuine** → emitted as a **behavioral** friction event (`category: [behavioral]`, `origin: user_correction`), flowing through the standard pipeline like any other friction event: step 4 (behavioral correction — what Claude should have done differently), step 5 (root cause), step 6 (cluster), step 7 (MEMORY.md scan), step 8 (action) → Stage 2.5 gates → Stage 3. Because it is a friction event (NOT a separate inventory like census), **step 6 clustering automatically de-dups it** against any narrative-lane friction event of the same root cause — so NO census-style `surfaced_in_friction` dedup flag is needed. The asymmetry with census is principled: census rows bypass clustering (step 4b promotes them straight into the unified table, hence they need the explicit flag), while user_correction rows travel the friction-event clustering path.
    - **Cap priority (MUST)**: `friction_events` is bounded at 5 (lane 1) — the pre-existing limit on how many events feed the agent calls (a scan-stop, per "Stop after identifying 5 distinct friction events" below). A genuine user_correction is the **highest-value** retrospect signal (the user directly stating what was wrong), so when more than 5 friction events exist this session, genuine user_corrections take priority for the retained slots — a narrative event yields, not the user_correction. This lane changes only the *priority* of which events the existing 5-event bound keeps; it does NOT change the bound, and it does NOT claim events beyond the bound are preserved elsewhere (the >5 limitation predates this lane). Without this priority rule the cap would silently re-open the exact recall gap the lane closes — the highest-value signal would be the one crowded out.
  - **false-positive (dropped)** → recorded in the `user_correction` ledger (below) with reason + cite, NEVER silently dropped. The judge's drops are audited so a self-serving omission at the judgment step is structurally visible — the same guarantee the `dismissed_candidates` Red Flag ("Silent dismissal ... is BLOCKED") gives the rule-violation ledger. (This is what keeps the marker-gated-LLM choice from re-opening, at the judgment step, the very self-serving blind spot the lane exists to close.)

  Per-drop ledger row:
  ```
  <!-- retrospect:user_correction begin -->
  - candidate: "<verbatim user-turn excerpt>" | marker_class: negation|redirect|mismatch | reason: <why judged not-a-correction> | cite: <turn ref / quoted context>
  <!-- retrospect:user_correction end -->
  ```
  The ledger fence is informational — it lives OUTSIDE the `retrospect:distribution` fence, so the Stop hook's awk parser (distribution card + unified table only) ignores it, exactly like the `tool_census` trail. Empty body when zero candidates were dropped; when the transcript is unreachable, replace the body with a single `<!-- retrospect:user_correction_skipped: transcript unreachable -->` line.

  **Early-exit interaction (intrinsic, not a separate predicate):** unlike the Census carve-out — which needs an explicit early-exit override because an inventory row is not a friction event — a genuine user_correction IS a friction event, added to `friction_events` during pre-scan. So the existing "1+ friction events → mandatory tracer/analyst calls" path fires and the 0-friction early-exit cannot trigger when a genuine correction exists. The lane's highest-value case (the narrative pre-scan surfaced 0 friction events, but the marker scan + judge catches a correction the agent did not narrate) is handled by the SAME mechanism: the marker scan runs as part of pre-scan and contributes the genuine correction to `friction_events` BEFORE the early-exit decision is taken. No separate carve-out clause is required.

**Pre-scan Categorization (Mandatory)** — every friction event identified in pre-scan MUST be tagged with `category[]: string[]` containing ≥1 of these enumerated values. Stage 2 progression to step 3 is BLOCKED until every event has at least one category label.

| Category | Signal examples (≥2 each) | Required `Tool Layer` (composition with step 4b) |
|----------|---------------------------|--------------------------------------------------|
| `behavioral` | "Claude가 확인 없이 결론 도출" / "한 PR에 여러 concern bundle" / "user가 동일 지적 반복" / "user가 '~하라니까 ~하고 있네?' 로 교정" (user_correction lane origin — genuine corrections enter here) | none (Tool Layer = `—`) |
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

**Pre-scan Checklist (Mandatory)** — after categorization, emit a deterministic per-category checklist that enumerates rule-violation candidates from machine-checkable sources. Pre-scan is otherwise narrative-driven (operator-interpreted), which lets rule-violation events slip through reframings like "downstream caught it, not friction". The checklist forces an explicit per-category accounting before the 0-friction early exit can fire.

For every rule category identified in Stage 1 (Pre-merge Worktree Precondition / Pre-PR rebase MANDATORY / Caller chain verified / Mandatory Testing / etc.), emit ONE of:

```
- {category_name}: violations: none
- {category_name}: violations:
  - {one-line candidate} | spec_cite: {file:line or section name}
```

**Deterministic enumeration sources** (candidates MUST come from these — operator narrative is NOT a source):
- Every codex/review-bot finding produced in this session transcript (Codex, BugBot, Cursor, oh-my-claudecode reviewers)
- Every workflow step in project `AGENTS.md` / global `~/.claude/CLAUDE.md` where the session took a different path (e.g., merge-from-non-base worktree, force-push instead of fixup, omitted caller-chain line caught by a hook)
- Every retried command in the session — same command issued ≥2 times (including diagnostic retries)

**Gate behavior** — the Early exit below is valid ONLY when every category's `violations:` line resolves to `none`. A category with a non-`none` line BLOCKS the 0-friction exit; the blocked candidate MUST be either:
- **Promoted** to a friction event (carries through Stage 2 step 3+ and Stage 2.5 gates), or
- **Demoted** to the `dismissed_candidates` ledger (note-only, see below) with explicit rationale + spec cite. Demotion does NOT silence the audit trail — it records the dismissal reason for follow-up retrospects to surface dismissed-candidate patterns.

**Mandate scope (narrow — rule violations only)** — the checklist gate's capture mandate applies to **rule violations** (workflow steps in `AGENTS.md` / `~/.claude/CLAUDE.md` / hook denials / spec-cited divergence) and NOT to general codex/review-bot findings. A refactor suggestion, style nit, or defensible alternative implementation from a review bot is NOT subject to the dismissal-to-silence ban — those may be acknowledged or ignored without a checklist entry. This narrowing exists to prevent MEMORY.md from inflating into a review-bot mirror over time.

**Negative list (NOT rule violations — do NOT record in `dismissed_candidates`)** — the following signal classes are explicitly out of mandate scope. Recording them in `dismissed_candidates` is over-capture and silently dilutes the leading-signal value of the ledger (cross-session "same candidate dismissed N times" pattern detection):

- **Builtin tool contract violation** (e.g., Edit on un-Read file, Read on non-existent path, Write-before-Read guard firing, NotebookEdit on a non-notebook) — a simple builtin-API constraint resolved by the documented recovery procedure (Read first, then Edit). This is a tool-protocol error, NOT a workflow / AGENTS.md / `~/.claude/CLAUDE.md` rule violation.
- **Expected hook / tool API enforcement firing** (e.g., `--state all` blocked by the praxis `gh`-flag advisory hook, external-write-falsify-check nudging on a hypothesis marker, destructive-bash guard asking on `rm -rf`) — the hook is *doing its job* by design. The fire itself is the enforcement working, not a violation against the operator.
- **Network / transient failure** (timeout, 5xx, MCP server unavailable, `gh` API rate-limit) — environmental noise resolved by retry. Not a rule violation against the operator and not a stable cross-session signal.
- **Signal naturally resolved by time / state mutation** (e.g., a race that another actor cleaned up, a stale lock that cleared on retry, a CI run that flipped from red to green on rerun) — a *signal*, not a violation. The candidate disappeared without operator action because the world changed underneath it.

**Pre-emit self-check (recommended)** — before appending a `dismissed_candidates` line, verify that the candidate's `spec_cite` actually points to a concrete line / section in `AGENTS.md`, `~/.claude/CLAUDE.md`, a hook script under `hooks/`, or a `SKILL.md`. If the `spec_cite` resolves to none of those (or you find yourself writing `spec_cite: n/a` / `spec_cite: builtin tool contract`), the candidate likely belongs to the Negative list above — drop it from the ledger entirely rather than recording a placeholder. The `dismissed_candidates` ledger's value as a cross-session leading signal degrades quickly if it accumulates non-rule-violation entries.

For a rule-violation candidate, the operator's only dismissal path is:
- **Demote to `dismissed_candidates` ledger** (note-only) with explicit rationale + spec cite. Silent dismissal — refusing to record the candidate at all — is BLOCKED by the Red Flag in this skill.
- **Escape hatch via existing resolution** — when `repeat=true AND resolved=true` (a prior session's issue/hook already resolves the same violation pattern; see Stage 2 step 7 escape hatch at the `note only` clause), demotion is permitted with a one-sentence cross-reference to the existing resolution recorded in the dismissal rationale.

**`dismissed_candidates` ledger** — for every checklist candidate that was reviewed and dismissed (i.e., kept off the unified findings table), append one line to the ledger block emitted alongside the checklist:

```
<!-- retrospect:dismissed_candidates begin -->
- {one-line candidate} | reason: {dismissal rationale} | spec_cite: {section name or file:line}
<!-- retrospect:dismissed_candidates end -->
```

This is a pure audit log, not a gate. It exists so a follow-up retrospect can surface patterns like "the same candidate has been dismissed N times across sessions" — a leading signal that the dismissal rationale itself needs scrutiny. Empty ledger (zero dismissals) still emits the fenced block with no inner lines.

Both the checklist block and the dismissed_candidates block live in Stage 3 output between the report header and the distribution card (see Stage 3 Output Schema Contract). They are informational-only — the Stop hook's awk parser silently ignores them.

**Early exit**: If pre-scan finds 0 friction events AND the Pre-scan Checklist has zero unresolved violations (every category's `violations:` line is `none` OR demoted to `dismissed_candidates`), skip agent calls and route to Stage 3's "No patterns found ✅" minimal output path — which still emits the header, the `pre_scan_checklist` fence, the `dismissed_candidates` fence (with any demoted candidates from this session), the `tool_census` trail (full inventory), the `user_correction` ledger (per-drop audit; empty body when zero drops), and the distribution card (counts = 0, verdicts = NA). The audit-trail fences MUST be emitted in this path so demoted candidates, the tool inventory, and dropped correction candidates are not silently lost. Skipping Stage 3 output entirely is a Red Flag — "exit" here means "skip agent calls + Stage 2.5 gates", not "skip Stage 3 output". **Exception (Stage 1.5 carve-out)**: when Stage 1.5 produced ≥1 `memory_hygiene` finding, the early exit does NOT fire — proceed to Stage 2.5 → Stage 3 with the hygiene findings as the sole input, and emit the Stage 1.5 hygiene-only banner (see Stage 1.5 "0-friction hygiene-only retrospect path"). **Exception (Stage 2.7 carve-out)**: when the session transcript contains ≥1 Stage 2.7 audit trigger (PR / issue / Slack / Notion write — see Stage 2.7 trigger list), the early exit does NOT fire — Stage 2.7 must execute its post-hoc artifact audit so silent-pass quality failures (the exact case Stage 2.7 was designed to catch) are not skipped. If Stage 2.7 produces ≥1 `output_quality` finding, proceed to Stage 2.5 → Stage 3 with those findings as input; if Stage 2.7 silently skips (0 triggers detected after the carve-out check), fall through to the normal 0-friction "No patterns found" exit. **Exception (Checklist carve-out)**: when the Pre-scan Checklist has ≥1 unresolved violation (a category line non-`none` AND not yet demoted to `dismissed_candidates`), the early exit does NOT fire — that candidate must be promoted to a friction event or demoted before Stage 3 can present "No patterns found". **Exception (Census carve-out)**: when the `tool_census` lane (pre-scan lane 3) **promoted ≥1 census finding** (a row that survived the step 4b promotion pipeline — objective signal AND post-dedup AND post-cap), the early exit does NOT fire — instead the promoted census findings flow through the **remaining Stage 2 steps like any other finding**: step 4b (emit), step 5 (root cause), step 6 (cluster), step 7 (MEMORY.md 2-hop scan + `memory_scan` recording), step 8 (action assignment via the category-default `tool` row) → Stage 2.5 gates → Stage 3. The friction-only **tracer/analyst agent calls are skipped** when there are no friction events (they take friction events as input); the census path is deterministic and does not need them. Do NOT shortcut census findings straight to Stage 2.5 — that would skip root-cause/MEMORY-scan/action-assignment and emit under-specified findings or miss required gates. **Ordering invariant (MUST):** the census promotion pass is evaluated **during pre-scan, before this early-exit decision is taken** — NOT deferred to step 4b's position later in the document. This is safe because the promotion decision is **deterministic** and needs only inputs already available at pre-scan time: the lane-3 inventory and the `friction_events` (for the `surfaced_in_friction` dedup — the sole dedup input). It does NOT require the tracer/analyst agent calls. step 4b's thresholds **define** the decision; step 4b later **consumes** the same pre-computed result to emit the findings into the unified table — it does not re-decide. Were promotion deferred to step 4b's textual position, a 0-friction session would take this early exit first and skip step 4b entirely, so the carve-out could never observe a promoted finding and the whole census-driven path would be dead — the exact coverage gap this change closes. This is the structural fix for that gap: a 0-friction session with a latent tool defect (used cleanly but with `error_count`/`retry`/`workaround` signal) still produces a finding instead of silently exiting. The trigger keys on **promoted findings**, not the raw `signal != none` row property: a row that has a signal but is deduped (`surfaced_in_friction == true` — the sole suppression rule) or capped out does NOT count toward the carve-out. Note a retry-only row (`retry_count >= 2`) is NOT suppressed by also appearing in the Pre-scan Checklist (that ledger holds rule violations, not tool-defect findings — see step 4b "Census dedup"); such a row promotes and fires this carve-out. If zero census findings promoted, this exception does not fire and the normal 0-friction exit applies — but the census trail fence (full inventory) is still emitted in Stage 3 output (audit completeness). **No separate User-correction carve-out exists by design**: a genuine user_correction (pre-scan lane 4) is emitted as a behavioral friction event during pre-scan, so it makes `friction_events` non-empty and the 0-friction early-exit simply cannot fire — the blocking is intrinsic, not a predicate added here (see pre-scan lane 4 "Early-exit interaction"). The `user_correction` ledger fence (per-drop audit) is still emitted in Stage 3 output even on the 0-friction exit, parallel to the census trail.

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

   **This pass has TWO input lanes (usage-gated, not friction-gated):**
   - **Friction-driven lane** — the `friction_events` with `tool` ∈ `category[]` (the historical input). Each is analyzed per "For each tool friction event, record" below.
   - **Census-driven lane** — iterate the `tool_census` inventory (pre-scan lane 3). The promotion decision (which rows clear the objective-signal thresholds below + dedup + cap) was already computed during pre-scan so the Early-exit Census carve-out could observe it (see pre-scan lane 3 + the carve-out's Ordering invariant); this lane **consumes** that decision and emits each promoted row as a finding — the thresholds below are the definition of that decision, not a second evaluation. This lane is what makes 4b examine tools that ran *without* surfacing friction — the coverage gap this pass was extended to close. The census-driven lane runs EVEN when `friction_events` is empty (see Stage 2 Early-exit "Census carve-out"). A **promoted** census row becomes a finding that enters the standard downstream pipeline like any other finding — step 5 (root cause), step 7 (MEMORY.md 2-hop scan + `memory_scan` recording, which Gate-5 verifies only when the resolved action is a memory action), step 8 (action assignment), and the Stage 2.5 gates.

   **Census promotion thresholds (objective signals — noise suppression):** an inventory row promotes to a `tool` finding when `surfaced_in_friction == false` AND ANY of:
   - `error_count >= 1` AND the error was not resolved by a documented recovery on retry AND the error is **tool-attributable** (see gate below)
   - `retry_count >= PRAXIS_RETROSPECT_CENSUS_RETRY_THRESHOLD` (default `2`) AND the retries are **tool-attributable** (see gate below)
   - `workaround_marker == true` AND the workaround is **tool-attributable** (see gate below) — the marker is a loose text-proximity heuristic (`manual` / `fallback` / `우회` / `workaround` near the call), so a generic "manual" workflow note or an operator workaround unrelated to tool design does NOT promote
   - output truncated/discarded then re-fetched (builtin Read/Grep over-cap pattern)

   **Tool-attribution gate (MUST — applies to the `error_count`, `retry_count`, and `workaround_marker` signals):** a failure / retry / workaround promotes ONLY when it is attributable to the **tool's own design or reliability** — timeout, schema mismatch, malformed tool output, crash, a missing flag/option, an undocumented behavior the tool should have handled. A signal caused by **operator input** (a malformed argument the agent supplied, then corrected on retry), **repo / working-tree state** (merge conflict, a file the agent should have created first), **permission / auth** (401 / 403, sandbox denial), an **expected environment outcome** (a `grep` exit=1 no-match, a probe deliberately expected to fail), or a **workaround unrelated to a tool defect** (a "manual" process note, an operator-chosen alternative path) is NOT a tool defect and MUST NOT promote — even when the numeric threshold or text marker is met. When attribution is ambiguous, default to NOT promoting: a false `tool` finding routes to noisy upstream feedback, whereas a missed one is recoverable next session. The threshold / marker firing is therefore **necessary but not sufficient** — the attribution gate is the second required condition. (Only the truncation-refetch signal is inherently tool-attributable — it is a concrete builtin design defect by construction — and does not need this gate.)

   Rows with no signal (`signal: none`) stay in the census trail only — never a finding.

   **Q1 — builtin-layer promotion scope (Negative-list alignment):** a `builtin`-layer census row promotes ONLY for a **design-defect** signal (e.g., output truncation forcing a re-fetch). A builtin **protocol error** (Edit on un-Read file, Read on a non-existent path, Write-before-Read guard firing) is EXCLUDED from promotion — it is a tool-protocol error resolved by the documented recovery, explicitly carved out by the Stage 2 Pre-scan Checklist Negative list ("Builtin tool contract violation"). Promoting it would contradict that carve-out.

   **Census dedup (the ONLY dedup that suppresses a census finding):**
   - `surfaced_in_friction == true` → the friction-driven lane already emitted the SAME tool-defect finding for this tool; the census-driven lane does NOT double-emit (the census row records the cross-ref only). This is the sole suppression rule.
   - **NOT a dedup — retry_count vs the Pre-scan Checklist:** a `retry_count >= 2` row may ALSO appear as a "retried command" candidate in the Pre-scan Checklist, but that does NOT suppress the census finding. The Checklist is a **rule-violation** ledger (behavioral / workflow — "Claude should not have retried without a diagnostic step"); the census finding is a **tool-defect** finding (the tool *required* N retries — a reliability signal). These are two distinct findings about one event, recorded in BOTH places per the step 4 vs step 4b dedup rule below ("record it in BOTH places"). Suppressing the census tool-defect finding because the Checklist flagged the same retry would delete exactly the latent-tool-defect signal the Census carve-out exists to catch — in a 0-friction session a retry-only census row is often the ONLY promoted finding, so suppressing it re-opens the coverage gap. The Checklist never promotes a tool-defect finding, so there is no genuine double-emit to dedup against.

   **Census finding cap:** promote at most `PRAXIS_RETROSPECT_CENSUS_FINDING_CAP` (default `5`, matching the friction-event cap) findings, ranked by signal severity. When more signal-rows exist, emit the top-N AND record the dropped count in the trail (`<!-- retrospect:census_findings_capped: dropped=K -->`) per the no-silent-cap rule. The full inventory is never capped — only promoted findings are.

   **Env vars** (mirror the `PRAXIS_RETROSPECT_*` convention; malformed non-integer/negative → silent fallback to the documented default):
   - `PRAXIS_RETROSPECT_CENSUS_RETRY_THRESHOLD` (default `2`) — retry_count promotion threshold
   - `PRAXIS_RETROSPECT_CENSUS_FINDING_CAP` (default `5`) — max promoted census findings

   **Tool layers to scan (all 4):**

   | Layer | Examples | Friction signals |
   |-------|----------|-----------------|
   | `mcp` | any custom or third-party MCP server (data warehouse, observability, chat, infra, etc.) | Slow response, missing field, schema mismatch, timeout |
   | `cli` | `gh`, `kubectl`, `git`, plus project-specific CLIs | Missing flag/option, undocumented behavior, workaround needed |
   | `builtin` | Read/Edit/Bash/Grep/Glob, Agent, hooks | Environmental constraint, permission issue, output truncation |
   | `skill` | praxis / OMC / project-specific skills and subagents | Stage boundary unclear, trigger mismatch, prompt defect, wrong routing |

   **For each tool friction event OR promoted census row, record:**
   - `tool_name`: specific tool (e.g., "gh CLI", "<plugin-name> MCP", "<skill-name> skill")
   - `layer`: mcp / cli / builtin / skill
   - `origin`: `friction` (friction-driven lane) | `census` (census-driven lane) — internal lane-routing state distinguishing the two input lanes during step 4b processing (not a surfaced table column; census-origin is already implied by the row's presence in the tool_census trail)
   - `friction_type`: missing feature, design defect, documentation gap, performance issue, integration mismatch
   - `evidence`: the specific moment (quote or paraphrase); for `origin: census`, cite the inventory row's `signal` summary + the call site
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

   h. **Stored-value falsification — multi-oracle completeness (MUST)**. This sub-step applies ONLY to a finding whose probe targets a **stored fact VALUE** held in a memory entry — a numeric measurement, a comparison sum, or a count (e.g., "X is 9h faster than Y", "row count = 4.2M", "criteo sum = N"). It does NOT apply to `DESCRIBE` / `SHOW` enumerate results, where the oracle is unambiguous (the catalog itself) and outside this gate.

      An **oracle** is the specific measurement basis that produced the stored value — the matching basis (e.g., warehouse partition key vs FK rowid), the cohort, and the unit. A probe that re-measures the same fact under a *different* oracle measures a *different* quantity; it cannot falsify the stored value, only surface a separate cohort-shift observation.

      Three-step rule:

      1. **Confirm the originating oracle from the entry body.** Read the entry to identify which oracle produced the stored value (matching basis, cohort, unit). If the entry body does NOT state its oracle → **DEFER falsification**: do not attempt to correct the stored value this cycle. Instead propose an entry-annotation update (route as `memory`, Stage 4 update path) that adds the missing oracle/unit annotation, so a future cycle can falsify with a known basis.
      2. **Probe first with the SAME oracle.** The first falsification probe MUST use the same matching basis, cohort, and unit as the originating oracle. Only a same-oracle probe can confirm or falsify the stored value.
      3. **Different-oracle results are NOT falsification evidence.** A probe run under a different matching basis / cohort / unit does not invalidate the entry. It MAY be emitted as a *separate* cohort-shift finding (its own row), but it MUST NOT drop, overwrite, or mark-resolved the stored value in the original entry.

      **Ledger fields** (mirror the `memory_scan` HTML-comment format above) — emit one comment per stored-value finding, keyed by `finding #N:`:

      ```
      <!-- oracle_match finding #N:
        stored_value_oracle: <matching basis / cohort / unit from entry body, or `absent`>
        falsification_oracle: <matching basis / cohort / unit of the probe run, or `deferred`>
        oracle_match: true|false
      -->
      ```

      **Invariant**: falsification of a stored value applies ONLY when `oracle_match: true`. When `oracle_match: false`, the probe is a cohort-shift observation, not falsification.

      **Oracle-match confirmation is required before any commit that corrects a stored value.** A value-correcting commit whose finding carries `oracle_match: false` (or absent) is a Red Flag (see Red Flags section).

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

**Scope window** — same as Stage 2: scan the most recent 50 turns, or back to the last session boundary (whichever is narrower). The window is intentionally identical so that trigger detection (below) and friction-event analysis operate over the same transcript slice — divergent windows would make `output_quality` findings non-deterministic across invocations and could surface artifacts whose context is no longer visible to Stage 2. If the transcript is unreachable post-compaction, fall through to the `transcript unreachable` failure-mode rule below (skip with `<!-- retrospect:audit_skipped: transcript unreachable -->`).

**Adaptive trigger** — Stage 2.7 fires ONLY when the session transcript (within the Scope window above) contains at least one of these artifact-write signals. With zero triggers, Stage 2.7 silently skips.

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
  - `len([r for r in reviews if r.state == "CHANGES_REQUESTED"]) >= 2` (≥2 CHANGES_REQUESTED submissions — each forces a revision cycle, so ≥2 signals genuine high revision cost). The unfiltered `reviews` array counts individual submissions (APPROVED / COMMENTED / DISMISSED included), not revision rounds — three one-time approvals would meet `len(reviews) >= 3` despite zero rework. Filter on `CHANGES_REQUESTED` to measure what the heuristic intends.
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
1. Env var `PRAXIS_OWN_ORGS` (comma-separated handles) — e.g., `PRAXIS_OWN_ORGS="my-handle,my-team"`
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

**Gate-6 (Oracle-Match Completeness)** — For every finding whose action would CORRECT a stored value in a memory entry (i.e., the memory action was flagged stored-value-falsification at Stage 2 step 7h), verify the `oracle_match` ledger field is present AND, when the action invalidates/overwrites the stored value, `oracle_match: true`.

> **Why Gate-6 exists**: a stored value can be "falsified" by a probe that measured a *different* quantity (different matching basis / cohort / unit), silently overwriting a correct entry with a cohort-shifted number. Step 7h requires same-oracle confirmation; Gate-6 provides the structural enforcement — value-correcting findings without a matching-oracle probe are returned to step 7h.

**Step 1 — Identify stored-value-correcting findings**: collect all findings flagged at step 7h whose action invalidates / overwrites a stored numeric measurement / comparison sum / count.

**Step 2 — Verify `oracle_match`**: for each finding from step 1, check that the `oracle_match` ledger field (step 7h format) is present. If the action invalidates/overwrites the stored value, require `oracle_match: true`.

**Step 3 — Classify violations**: any finding from step 1 with `oracle_match: false` or absent on a value-correcting action → Gate-6 violation → return that finding to Stage 2 step 7h with instruction to (a) re-probe with the same oracle, or (b) convert to a separate cohort-shift finding (its own row, leaving the stored value untouched), or (c) defer + propose an entry-annotation update when the originating oracle is absent from the entry body. Same 2-re-entry cap per finding, shared across all gates for that finding.

**Gate-6 verdict:**
- All stored-value-correcting findings have `oracle_match: true` → `gate_6_verdict: PASS`
- ≥1 stored-value-correcting finding has `oracle_match: false` or absent → `gate_6_verdict: FAIL`
- No stored-value-correcting findings exist → `gate_6_verdict: NA`

`gate_6_verdict` is informational (parallel to `gate_3_verdict` and `gate_5_verdict`). The Stop hook silently ignores it; enforcement is procedural inside Stage 2.5. If Gate-6 violations persist after 2 per-finding re-entries, surface to user with 3-way override prompt:

> "Finding #N의 Gate-6 (oracle-match 미충족 — 다른 oracle 로 stored value 를 덮어쓰려 함)가 통과되지 않습니다. 어떻게 진행할까요? [a] 같은 oracle 로 재측정 / [b] 별도 cohort-shift finding 으로 전환 (stored value 유지) / [c] originating oracle 이 entry 에 없으니 defer + entry-annotation update 제안."

**Output (on pass)** — Stage 2.5 emits the distribution card per the Output Schema Contract defined in Stage 3, plus per-finding Gate-1, Gate-2, Gate-3, Gate-4, Gate-5, and Gate-6 verdicts:

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
- gate_6_verdict: {PASS|FAIL|NA}
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
- `gate_6_verdict: NA` — zero stored-value-correcting findings exist (no finding flagged at step 7h)

The Stop hook `retrospect-mix-check.sh` parses `gate_1_verdict`, `gate_2_verdict`, and `gate_4_verdict`; `gate_3_verdict` is emitted for visibility and audit-trail purposes (the hook silently ignores unknown keys). Gate-3 is enforced procedurally inside Stage 2.5; structural Stop-hook enforcement is reserved for a follow-up tightening if procedural compliance proves insufficient. Gate-4 is enforced both procedurally (Stage 4 Action 4 step 0a per-action approval) and structurally by the Stop hook: a `gate_4_verdict: FAIL` in the card blocks Stage 3 output, and an absent `gate_4_verdict` combined with a `⚠ EXTERNAL:` Rationale prefix also blocks (indicating Stage 2.5 Gate-4 was partially skipped). `gate_5_verdict` is informational-only and INTENTIONALLY EXCLUDED from Stop hook parsing — the hook silently ignores it; Gate-5 enforcement is procedural inside Stage 2.5. (Stop hook wiring for Gate-5 is a deferred follow-up similar to Gate-4's progression in PR #340 — file a follow-up issue when procedural compliance proves insufficient.) `gate_6_verdict` is likewise informational-only and INTENTIONALLY EXCLUDED from Stop hook parsing (parallel to `gate_3_verdict` and `gate_5_verdict`) — the hook silently ignores it; Gate-6 enforcement is procedural inside Stage 2.5.

This card and verdict block become Stage 3's input header.

### Stage 3: Report + Approval

**Output Schema Contract** (normative — Stop hook `retrospect-mix-check.sh` parses this):

Stage 3 output MUST emit, in this order:

1. **Header**: a line matching `^## Retrospect Report` (em-dash or hyphen tail accepted: `## Retrospect Report — {date}` or `## Retrospect Report - {date}`).
2. **Pre-scan Checklist** between HTML comment fences (Stage 2 Pre-scan Checklist origin) — emitted between header and distribution card:

   ```markdown
   <!-- retrospect:pre_scan_checklist begin -->
   - {category_name}: violations: none
   - {category_name}: violations:
     - {one-line candidate} | spec_cite: {section}
   <!-- retrospect:pre_scan_checklist end -->
   ```

3. **`dismissed_candidates` ledger** between HTML comment fences (Stage 2 Pre-scan Checklist origin) — emitted alongside the checklist block; empty body when zero candidates were dismissed:

   ```markdown
   <!-- retrospect:dismissed_candidates begin -->
   - {one-line candidate} | reason: {rationale} | spec_cite: {section}
   <!-- retrospect:dismissed_candidates end -->
   ```

3b. **`tool_census` trail** between HTML comment fences (Stage 2 pre-scan lane 3 origin) — the full tool inventory; one block, all inventory rows (promoted and non-promoted). Empty body only when zero tool calls occurred in the scope window. When transcript is unreachable, replace the body with a single `<!-- retrospect:census_skipped: transcript unreachable -->` line. Promoted rows (those that survived the step 4b promotion pipeline — signal AND post-dedup AND post-cap) ALSO appear as `tool`-category rows in the unified findings table (#5); the trail is the complete audit log:

   ```markdown
   <!-- retrospect:tool_census begin -->
   - tool_name: {tool} | layer: {mcp|cli|builtin|skill} | call_count: N | error_count: N | retry_count: N | workaround_marker: {true|false} | surfaced_in_friction: {true|false} | signal: {none|summary}
   <!-- retrospect:census_findings_capped: dropped=K -->   # only when promoted findings exceeded the cap
   <!-- retrospect:tool_census end -->
   ```

3c. **`user_correction` ledger** between HTML comment fences (Stage 2 pre-scan lane 4 origin) — the per-drop audit log: every marker-matched user-turn candidate the LLM judge ruled a false-positive, one row each, with reason + cite. Empty body when zero candidates were dropped (a session with no user corrections, OR one where every marker-matched candidate was judged genuine and promoted). When the transcript is unreachable, replace the body with a single `<!-- retrospect:user_correction_skipped: transcript unreachable -->` line. Genuine corrections do NOT appear here — they are emitted as `behavioral` friction events in the unified findings table (#5); this ledger records only the drops, so the judge's suppressions stay auditable:

   ```markdown
   <!-- retrospect:user_correction begin -->
   - candidate: "{verbatim user-turn excerpt}" | marker_class: {negation|redirect|mismatch} | reason: {why judged not-a-correction} | cite: {turn ref / quoted context}
   <!-- retrospect:user_correction end -->
   ```

4. **Distribution card** between HTML comment fences. Action keys are canonical snake_case enum; verdict values are `PASS` / `FAIL` / `NA`:

   ```markdown
   <!-- AUTHORITATIVE_SCHEMA — Stop hook depends on this. Co-update hooks/retrospect-mix-check.sh + tests/test_retrospect_mix_check.sh + tests/fixtures/retrospect-synth-*.expected.json on any change to: (1) the fence markers themselves, (2) the action key set (memory/issue/claude_md_draft/skill_idea/hook_code/upstream_feedback), or (3) gate_1_verdict / gate_2_verdict keys.
        Gate-3 carve-out: gate_3_verdict is informational-only and INTENTIONALLY EXCLUDED from this co-update contract. The hook's awk parser keys on gate_1_verdict/gate_2_verdict literals only and silently ignores all other lines (regression-tested by tests T8–T17 + the 4 fixture files which still pass without gate_3_verdict). Adding/removing/renaming gate_3_verdict alone does NOT require hook or test changes.
        Gate-4 enforcement note: gate_4_verdict IS structurally enforced by the Stop hook (unlike gate_3_verdict which remains informational-only). Changes to gate_4_verdict semantics or its FAIL/WARN/NA/PASS values REQUIRE synchronized edits to hooks/retrospect-mix-check.sh and tests/test_retrospect_mix_check.sh (T36/T37/T38). Adding/removing gate_4_verdict from the card alone does NOT require fence-marker or action-key changes.
        Gate-5 carve-out: gate_5_verdict is informational-only and INTENTIONALLY EXCLUDED from this co-update contract (parallel to gate_3_verdict). The hook silently ignores gate_5_verdict. Adding/removing/renaming gate_5_verdict alone does NOT require hook or test changes. (Deferred upgrade trajectory similar to Gate-4 in PR #340 — file a follow-up issue for Stop hook wiring when procedural enforcement proves insufficient.)
        Gate-6 carve-out: gate_6_verdict is informational-only and INTENTIONALLY EXCLUDED from this co-update contract (parallel to gate_3_verdict and gate_5_verdict). The hook silently ignores gate_6_verdict. Adding/removing/renaming gate_6_verdict alone does NOT require hook or test changes.
        Category-count carve-out (memory_hygiene / output_quality): these are CATEGORY counts (count of findings with the respective category in category[]) — NOT action keys. They are emitted as sibling lines to the action-type counts but are informational-only and INTENTIONALLY EXCLUDED from Stop hook parsing. Adding/removing/renaming memory_hygiene OR output_quality alone does NOT require fence-marker or action-key changes; the underlying actions of Stage 1.5 / Stage 2.7 findings still fall under one of the 6 action-type keys above. The Stop hook's awk parser silently ignores both lines (same mechanism as gate_3/gate_5 verdicts).
        Pre-scan-checklist + dismissed_candidates + tool_census + user_correction carve-out: the `retrospect:pre_scan_checklist`, `retrospect:dismissed_candidates`, `retrospect:tool_census`, and `retrospect:user_correction` fenced blocks (Stage 2 origin) are informational-only and INTENTIONALLY EXCLUDED from Stop hook parsing. They live OUTSIDE the `retrospect:distribution` fence so the awk parser keyed on `distribution begin/end` ignores them. Promoted census findings still surface as `tool`-category rows in the unified table and fall under the existing 6 action-type keys (typically `upstream_feedback`); genuine user_correction findings surface as `behavioral`-category rows under the existing action-type keys (typically `claude_md_draft` / `memory`); the trail/ledger fences themselves add no parser key. Adding/removing/renaming the checklist OR dismissed_candidates OR tool_census OR user_correction blocks alone does NOT require hook or test changes. -->
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
   - gate_6_verdict: PASS
   <!-- retrospect:distribution end -->
   ```

5. **Unified findings table** with literal column headers (no abbreviation, no reordering):

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

<!-- retrospect:pre_scan_checklist begin -->
- {category_name}: violations: none
- {category_name}: violations:
  - {one-line candidate} | spec_cite: {section}
<!-- retrospect:pre_scan_checklist end -->

<!-- retrospect:dismissed_candidates begin -->
- {one-line candidate} | reason: {rationale} | spec_cite: {section}
<!-- retrospect:dismissed_candidates end -->

<!-- retrospect:tool_census begin -->
- tool_name: {tool} | layer: {mcp|cli|builtin|skill} | call_count: {n} | error_count: {n} | retry_count: {n} | workaround_marker: {true|false} | surfaced_in_friction: {true|false} | signal: {none|summary}
<!-- retrospect:tool_census end -->

<!-- retrospect:user_correction begin -->
- candidate: "{verbatim user-turn excerpt}" | marker_class: {negation|redirect|mismatch} | reason: {why judged not-a-correction} | cite: {turn ref / quoted context}
<!-- retrospect:user_correction end -->

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
- gate_6_verdict: {PASS|FAIL|NA}
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

No patterns found: emit the **header**, the **`retrospect:pre_scan_checklist` fence**, the **`retrospect:dismissed_candidates` fence** (populated with any dismissals from Stage 2's Pre-scan Checklist; empty body when zero), the **`retrospect:tool_census` fence** (full tool inventory for the session; empty body only when zero tool calls; `census_skipped` line when transcript unreachable), the **`retrospect:user_correction` fence** (per-drop ledger; empty body when zero candidates were dropped; `user_correction_skipped` line when transcript unreachable), and the **distribution card** with all counts = 0 and verdicts = NA, plus literal "This session followed all global `~/.claude/CLAUDE.md` rules. ✅". These audit fences MUST be emitted even in the 0-friction case — otherwise the dismissed-candidate, tool-usage, and dropped-user-correction audit trails are silently lost in exactly the path they are most needed (Stage 2 early exit). Note: the 0-friction "No patterns found" path requires that the user_correction scan promoted **zero genuine corrections** — a single genuine correction is a behavioral friction event, so friction_events is non-empty and this path is not reachable (the populated-table path runs instead). Note: the 0-friction "No patterns found" path is reachable only when the census **promoted zero findings** (no row survived the step 4b pipeline — either no objective signal, or every signal row was deduped / capped out); a single promoted census finding fires the Census carve-out and routes to the populated-table path instead.

**Enumerate-before-empty-fence (MANDATORY in the 0-friction path):** before emitting an empty `dismissed_candidates` fence in the 0-friction exit, MUST run the following deterministic enumeration — an empty fence is permitted only when step (a) surfaces zero **rule-violation** matches (review-bot output that is purely style nits / refactor suggestions does not count as a rule violation and does not block an empty fence):

(a) Scan the session transcript for codex/review-bot finding markers. "Codex/review-bot finding markers" are explicit tool outputs from: Codex CLI reviewer output blocks, BugBot comment bodies, oh-my-claudecode reviewer agent result text, Cursor review annotations. Every match — regardless of whether it constitutes a rule violation — MUST be inspected for rule-violation content. Any match that is a rule violation (workflow step in `AGENTS.md` / `~/.claude/CLAUDE.md` / hook denial / spec-cited divergence) MUST appear as a ledger line in `dismissed_candidates` with rationale (typically the escape-hatch form: `repeat=false AND resolved=true via in-session apply`, or `not a rule violation — style/refactor suggestion only`). Review-bot findings that are purely style nits or refactor suggestions are acknowledged and dismissed inline; they do NOT require a `dismissed_candidates` entry (per the narrow-scope rule at Stage 2 Pre-scan Checklist "Mandate scope").

(b) Every codex/review-bot match that identifies a rule violation MUST appear as a ledger line: `- {one-line candidate} | reason: {dismissal rationale} | spec_cite: {section name or file:line}`. The escape-hatch `repeat=false AND resolved=true via in-session apply` is the standard rationale when the finding was already fixed in the same session.

(c) An empty `dismissed_candidates` fence is ONLY permitted when step (a) enumeration returns zero **rule-violation** matches. The Red Flag is narrow-scoped to rule violations: emitting an empty fence after step (a) surfaced a rule-violation match (instead of recording it as a ledger line) is a Red Flag (see Red Flags section). A transcript whose review-bot output is purely style nits / refactor suggestions — no rule-violation match — correctly permits an empty fence; the mere presence of a review-bot output block does NOT itself force a ledger entry.

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

   In addition to standard fields (`name`, `description`, `type`), every new memory MUST evaluate whether to include the `memory-hint` opt-in fields — `hookable` and `hookKeywords`. The full field spec (parser semantics, matching rules, fail-open contract) lives in `hooks/advisory-nudge/memory-hint/spec.md`; this section defines the authoring-time decision. Use top-level `type:` (consistent with `hooks/advisory-nudge/memory-hint/spec.md` example and existing on-disk memory files); do **not** nest under `metadata:`.

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
   - Whole-token, case-sensitive matching only (per `hooks/advisory-nudge/memory-hint/spec.md`). List multiple casings explicitly if needed (`[Edit, edit]`).
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
   Classification is two-stage:
   1. **Global check uses either the input path OR `realpath` equivalence to the canonical config path**. A dotfiles-symlinked `~/.claude/CLAUDE.md` whose `realpath` resolves outside `~/.claude/` is still the user's own global config — and a finding that directly declares the resolved dotfiles backing path must still route to the Global flow.
   2. **Project vs External uses `realpath`** of the input. This correctly routes both `AGENTS.md` (regular file) and project-local symlinks such as praxis's own `CLAUDE.md → AGENTS.md` (whose `realpath` lands on a file inside cwd) to the Project path.

   | Target | Detection | Execution path |
   |--------|-----------|---------------|
   | **Global `~/.claude/CLAUDE.md`** | EITHER the input path equals `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md` (after `$HOME`/`~` expansion) OR `realpath <input>` equals `realpath ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md` (i.e., the finding declared the resolved dotfiles backing file path directly — still the user's own global config, must take the Global flow). | Staging → AskUserQuestion → apply only on explicit approval (see Global path below) |
   | **Project `AGENTS.md`** | Not Global AND `realpath <path>` resolves inside cwd. Covers `AGENTS.md` directly and project-local symlinks like `CLAUDE.md → AGENTS.md`. | Direct Edit (see Project path below) |
   | **External-repo rule file** | Not Global AND `realpath <path>` resolves outside cwd AND outside `~/.claude/` | Same as external-repo gate — do NOT edit; surface to user with resolved path |

   **Project path** (input is project `AGENTS.md`):
   - Present the draft diff to the user inline
   - Apply with explicit approval ("yes, add this rule") → Direct Edit

   **Global path** (input equals `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md`):
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

   c. **`apply` 선택 시**: Resolve the actual Edit target via `edit_target="$(realpath "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md")"` first — the builtin `Edit` tool refuses to write through a symlink, so the resolved path is the only writable target in symlinked dotfiles environments. Edit `$edit_target` to insert the approved rule at the indicated position. Show the resulting diff as verification.

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
   a. **Write the hook script.** First resolve the target repo (the project's
      `.claude/hooks/`, or a personal-config / dotfiles repo when the hook backs
      `~/.claude/`) and check its current branch: `git -C <repo> branch --show-current`.
      - **On a protected branch (`main` / `dev` / `prod` / `master`)**: an inline
        `Write` here is blocked by `pre-edit-protected-branch-guard` (it guards
        Edit/Write on protected branches — both the dirty-tree and the clean-tree
        PR-workflow-signal paths). Create a dedicated worktree on a new branch and
        write the hook inside it:
        ```
        git -C <repo> worktree add -b retrospect-hook-{slug} \
            <repo-parent>/<repo-name>.retrospect-hook-{slug} <protected-branch>
        ```
        Surface the worktree path to the user for the commit / PR decision —
        retrospect does NOT auto-commit or auto-PR the hook; its contract ends at
        the file write.
      - **Already on a feature branch**: write the hook directly to
        `.claude/hooks/` or the appropriate location.
   b. Present the hook code to user for review
   c. Explain how to register in `.claude/settings.json` (show the exact JSON entry)
   d. Use AskUserQuestion: "Hook을 settings.json에 등록할까요?" (✅ 등록 / ⏭ 파일만 유지 / 🕐 나중에)
   e. If approved: Edit `.claude/settings.json` to register the hook (inside the
      step-(a) worktree when one was created — `settings.json` shares the same
      protected-branch repo as the hook file)
   f. If skipped/deferred: leave the hook file in place and provide manual registration instructions

7. **Verification** — For each executed action, verify the artifact:

   | Artifact | Verification |
   |----------|-------------|
   | MEMORY.md feedback (new) | File exists + MEMORY.md index updated + `hookable`/`hookKeywords` frontmatter decision recorded (true with keywords, OR false with rationale in Actions Executed report) |
   | MEMORY.md feedback (merged) | Existing file updated (diff shown) + MEMORY.md index description updated if needed + if existing entry had `hookable: false` **or the field is missing entirely** and merged context now meets the retrieval-critical default, re-evaluate and add/update frontmatter (most pre-existing memories lack `hookable` — missing field is the dominant case, not false) |
   | GitHub issue | `gh issue view {url}` returns valid data |
   | Upstream feedback | `gh issue view {url}` returns valid data + URL repo matches `verified_backing_repo` from step 0 + label convention is correct for the verified repo (`tool-friction:{layer}` ONLY when verified repo is the praxis distribution; otherwise the repo's own convention label per Action 4's label rule) |
   | Hook code | Script file exists + settings.json registration confirmed (dry-run varies by hook type — no generic check). If Action 6 step (a) created a worktree, report the worktree path and confirm the file exists there. |
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
- **Pre-scan Checklist 가 식별한 rule-violation 후보를 dismissed_candidates ledger 기록 없이 silent 으로 무시** — 운영자의 dismissal-to-silence 권한은 차단됨. 후보는 (a) friction event 로 promote 하거나 (b) dismissed_candidates 에 explicit rationale + spec cite 와 함께 demote 해야 한다. 일반 codex/review-bot finding (refactor 제안 / style nit) 은 본 mandate 적용 대상 아님 — narrow scope 는 rule violation 사실에만 적용된다 (MEMORY.md 가 review-bot mirror 로 inflate 되는 위험 방지).
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
- **Using a different-oracle probe (different matching basis / cohort / unit) to falsify or correct a stored value without same-oracle confirmation** — Stage 2 step 7h / Gate-6 violation. A probe run under a different oracle measures a different quantity; it cannot invalidate the stored value. STOP — re-probe with the originating oracle, or surface the result as a separate cohort-shift finding (leaving the stored value untouched), or defer + propose an entry-annotation update when the entry body does not state its oracle. (e.g., the warehouse-partition-key vs FK-rowid measurement basis yielded a 9h latency shift that was NOT a falsification of the stored value but a cohort-shift artifact.)
- **Stage 3 "Execute now" 선택을 external-repo write 의 per-action 승인으로 ratify** — Stage 3 승인은 action 카테고리 선택이지, 외부 repo 개별 write 승인이 아님. Stage 4 step 0a 게이트를 반드시 별도로 실행.
- **Omitting the `Stage 2 caveats:` line on a finding that has any of: tracer confidence below HIGH, single observation, alternative root cause not ruled out, Gate-3 downgrade, analyst cluster overlap, escape-hatch state** — carry-forward is mandatory whenever ANY of these holds. Silent omission lets Stage 3 rank as if Stage 2 returned clean HIGH-confidence evidence.
- **조사 도구 결과를 completeness 검증 없이 결론에 사용 (premise unverified)** — 다음 두 패턴 모두 "premise falsified" (반증 테스트를 설계한 뒤 통과 → 진행 가능)가 아니라 "premise unverified" (도구 출력 자체의 한계를 검증하지 않은 채 결론에 사용 → STOP) 에 해당한다: (a) `find ... | head -N` 결과로 "파일/모듈 없음" 단정 — **`head` 를 제거한 원 명령 `find ... | wc -l` 을 별도로 실행**해 총 라인 수를 확인하고, cap 초과 시 cap 제거 또는 `grep -rn <token>` narrowing 필요 (`head -N` 파이프 뒤에 `| wc -l` 을 붙이면 cap 으로 잘린 뒤의 라인 수만 세므로 정확히 N개와 cap 초과를 구분할 수 없어 검증이 실패한다); (b) `find <path>` 빈 결과로 "경로/모듈 없음" 단정 — `ls <parent>` 로 path coverage 를 확인하거나 상위 경로로 재시도, 또는 `grep -rn <token>` cross-check 필요.
- **Stage 1.5 hygiene scan을 "MEMORY.md가 작아서" 또는 "이미 다 안다"는 이유로 건너뜀** — Stage 1.5는 unconditional. 코퍼스 크기와 무관하게 cap-and-cursor 메커니즘으로 비용이 amortized 된다. "내가 이미 안다" 는 author-exempt verification trap 의 변형 — Stage 1.5의 detect-only 책임을 Stage 2 friction-scanner 가 대신할 수 없다 (silent recurrence는 마찰 신호 없이 누적).
- **Stage 2.7 audit-skip을 trail 라인 없이 silent으로 처리** — `<!-- retrospect:audit_skipped: no artifacts -->` (또는 `transcript unreadable` variant)은 mandatory. trail 라인 부재 시 "Stage 2.7이 실행되었는가" vs "스킵되었는가" vs "잊혀졌는가" 를 retrospective audit이 구분할 수 없다. 0-trigger silent skip path도 trail 라인은 emit 한다.
- **0-friction exit에서 empty `dismissed_candidates` fence를 emit했으나 step (a) 열거가 rule-violation match를 surface함** — enumerate-before-empty-fence 요건 위반. codex/review-bot output(Codex CLI reviewer, BugBot comment, oh-my-claudecode reviewer result, Cursor annotation) 중 **rule violation에 해당하는** match가 있는데도 `dismissed_candidates` fence가 비어 있으면, 열거 결과를 ledger에 기록하지 않은 증거. STOP, rule-violation match를 모두 ledger 라인으로 기록한 뒤 emit. (style nit / refactor suggestion만 있는 output block은 위반이 아니며 empty fence가 정상 — 단순 output block 존재만으로는 Red Flag 아님.)
- **`tool_census` lane을 건너뛰고 step 4b를 friction_events 만으로 실행** — usage-gated 전환의 핵심을 무효화. census lane은 결정론적(transcript-derived)이므로 "마찰이 없었으니 점검할 도구가 없다"는 스킵 근거로 인정되지 않는다 — 마찰 없이 깨끗하게 쓰인 도구가 바로 census가 잡으려는 사각지대다. transcript가 닿지 않을 때만 `census_skipped` trail 라인과 함께 스킵 허용. trail 라인 없이 census fence를 통째로 생략하는 것은 "실행했는가 vs 스킵했는가 vs 잊었는가"를 구분 불가하게 만드는 Red Flag.
- **promoted census finding(step 4b 승격 파이프라인 통과: signal AND post-dedup AND post-cap)이 있는데 0-friction "No patterns found" 경로로 early exit** — Census carve-out 위반. 깨끗하게 쓰였지만 `error_count`/`retry`/`workaround` 신호를 가진 도구는 finding으로 승격되어야 하며, 그 존재가 early exit을 막는다. STOP, Stage 2.5 → Stage 3 populated-table 경로로 진행.
- **`user_correction` marker scan을 건너뛰고 friction_events를 narrative pre-scan만으로 구성** — usage-gated 탐지의 사용자-대면 축을 무효화. marker scan은 결정론적(user-turn-derived)이므로 "내가 사용자 교정을 다 narrate했다"는 스킵 근거로 인정되지 않는다 — 에이전트가 자발적으로 narrate하지 않은 교정이 바로 이 lane이 잡으려는 사각지대다 (narrative lane은 self-serving). transcript가 닿지 않을 때만 `user_correction_skipped` ledger 라인과 함께 스킵 허용.
- **marker-매칭 user-turn candidate를 LLM judge가 false-positive로 떨어뜨렸으나 `user_correction` ledger에 기록 없이 silent drop** — judge 단계 self-serving 은폐. dismissed_candidates의 "Silent dismissal ... is BLOCKED" Red Flag와 동일 — 모든 drop은 reason + cite와 함께 ledger 라인으로 기록되어야 한다. STOP, 떨어뜨린 candidate를 ledger에 기록하라.

**ALL of these mean: STOP. Return to Stage 2.**

## Quick Reference

| Stage | Key Activity | Success Criteria |
|-------|-------------|-----------------|
| **1. Load** | Read global `~/.claude/CLAUDE.md`, form scan questions | Rule categories identified |
| **1.5 Hygiene** | Detect-only MEMORY.md scan — stale references / contradictions / merge candidates (cap 5 files/invocation + cursor carryover) + size threshold (index-scoped, every invocation; subsignals 4a/4b/4c) + missing oracle annotation (signal 5 — stored numeric values lacking matching basis / cohort / unit) | Stage 1.5 findings emitted with `category: memory_hygiene` (or `hygiene_skipped` trail if MEMORY.md unreachable) |
| **2. Analyze** | Scan conversation (4 pre-scan lanes: friction / successful / tool_census / user_correction), map to rules, find root cause; step 4b runs usage-gated (friction-driven + census-driven lanes) | Root cause (not symptom) for each pattern; every event has `category[]`; tool_census trail emitted (every tool inventoried, promoted only on objective signal); user_correction ledger emitted (every marker-matched drop recorded; genuine corrections promoted to behavioral friction events) |
| **2.7 Audit** | Adaptive post-hoc artifact audit — fires only when session contains `gh pr|issue|comment` / Slack-Notion MCP write / approved external-write events; runs 3 sub-audits (PR mergeability, sub-agent substance, external comment evidence) | Stage 2.7 findings emitted with `category: output_quality` (or `audit_skipped` trail if 0 triggers) |
| **2.5 Audit** | Run Gate-1 (categorical) + Gate-2 (Schema A 5-line or Schema B dimension-tag rationale) + Gate-3 (evidence robustness for 2-action findings) + Gate-4 (external-repo authorization pre-check for upstream_feedback) + Gate-5 (memory-scan completeness for memory-action findings) + Gate-6 (oracle-match completeness for stored-value corrections) | All applicable gates PASS/WARN or per-finding cap reached and surfaced to user |
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
| Stage 2 (census) | Transcript unreachable for tool inventory (post-compaction) | Skip the census lane; emit `<!-- retrospect:census_skipped: transcript unreachable -->` as the tool_census fence body; the Census carve-out does not fire; proceed with friction-driven 4b only |
| Stage 2 (census) | A single tool call's name is unparseable | Skip that inventory row; continue with remaining tools; note the skip in the tool_census trail |
| Stage 2 (census) | Zero tool calls in the scope window (pure-conversation session) | Emit an empty tool_census fence; no findings; Census carve-out does not fire |
| Stage 2 (census) | `PRAXIS_RETROSPECT_CENSUS_*` env var malformed (non-integer / negative) | Silent fallback to documented default (2 / 5); no Stage 2 failure |
| Stage 2 (user_correction) | Transcript unreachable for user-turn scan (post-compaction) | Skip the marker scan; emit `<!-- retrospect:user_correction_skipped: transcript unreachable -->` as the ledger fence body; fall back to narrative friction_events only (the user's verbal summary remains an input per the analyze fallback) |
| Stage 2 (user_correction) | Marker matched but LLM judge cannot classify a candidate (ambiguous) | Default to PROMOTING as a genuine behavioral friction event (a false positive surfaces a benign finding for user dismissal; a false negative silently loses a real correction — the asymmetry favors promotion). Do NOT silently drop. Mark the event `origin: user_correction (ambiguous)` so the disposition stays auditable — promotions are not ledger-recorded (the ledger records drops only), so the origin suffix is the only trace distinguishing a confident-genuine promotion from an ambiguous-default one |
| Stage 2 (user_correction) | Zero user-turn corrections in the scope window | Emit an empty user_correction ledger fence; no findings; early-exit proceeds normally (no genuine correction to block it) |
| Stage 2 (analyze) | No friction events found | Exit with "No patterns found. ✅" — do not fabricate findings (subject to the Census carve-out: a promoted census finding overrides this exit) |
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
| Stage 2.5 (audit) | Gate-6 — stored-value-correcting finding has `oracle_match: false` or absent after 2 re-entries | Surface to user with 3-way override prompt (`[a] 같은 oracle 로 재측정 / [b] 별도 cohort-shift finding 으로 전환 (stored value 유지) / [c] note only 강등`); log selection |
| Stage 2.5 (audit) | Gate-6 — originating oracle un-determinable (entry body lacks oracle/unit annotation) | Defer falsification; propose an entry-annotation update (`memory`, Stage 4 update path) adding the missing oracle/unit so a future cycle can falsify with a known basis; record `oracle_match: false, falsification_oracle: deferred` |
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
