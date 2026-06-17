# Stage 3 report template + worked examples (retrospect)

Reference material for [`../SKILL.md`](../SKILL.md) Stage 3. The **normative
contract now lives in [`stage3-reporting.md`](stage3-reporting.md)**. Read that
file first for the canonical emit order, schema rules, and approval gate; use
this file for the consolidated template and worked examples.

## Consolidated report template

Emit order and fence markers per the Output Schema Contract.

The `retrospect:transcript_receipt` fence (§3e) is **post-compaction sessions
only** — omit the whole block when the transcript has no compaction marker.
Unlike the other Stage 2 fences it **IS parsed by the Stop hook (Gate-7)**:
its absence in a post-compaction session blocks Stage 3. The body must be
real command output run against the live `transcript_path`, never
hand-written. If the transcript is genuinely unreachable, replace the fence
with the single line
`<!-- retrospect:transcript_receipt_skipped: transcript unreachable -->`.
When `is_error_count > 0` the nested `retrospect:is_error_enum` block is
**also required** (Gate-7 blocks Stage 3 without it) — each `is_error`
event enumerated with a `promote`/`note`/`dismiss` disposition [issue #664].
Omit the nested enum block only when `is_error_count == 0` (nothing to
enumerate).

```markdown
## Retrospect Report — {session_date}

<!-- retrospect:pre_scan_checklist begin -->
- {category_name}: violations: none
- {category_name}: violations:
  - {one-line candidate} | spec_cite: {section}
<!-- retrospect:pre_scan_checklist end -->

<!-- retrospect:dismissed_candidates begin -->
- {one-line candidate} | reason: {rationale} | spec_cite: {section}
<!-- retrospect:dismissed_candidates end -->

Hygiene Scan Trail
- Current cycle: signal 1/2/3/4/5 = {one-line summary per signal, or none}
- Carried from previous cycle (cursor note): signal N = {verbatim carried finding}
- Falsification of carried finding (signal N): probe=`{command}` output=`{observed}` -> RESOLVED|STILL_OUTSTANDING|UNRUNNABLE
- Cursor write: concurrent advance detected — union-merged note ({n_self} self + {n_disk} on-disk lines) + advanced pointer to {pointer}

<!-- retrospect:tool_census begin -->
- tool_name: {tool} | layer: {mcp|cli|builtin|skill} | call_count: {n} | error_count: {n} | retry_count: {n} | workaround_marker: {true|false} | surfaced_in_friction: {true|false} | signal: {none|summary}
<!-- retrospect:tool_census end -->

<!-- retrospect:user_correction begin -->
- candidate: "{verbatim user-turn excerpt}" | marker_class: {negation|redirect|mismatch} | reason: {why judged not-a-correction} | cite: {turn ref / quoted context}
<!-- retrospect:user_correction end -->

<!-- retrospect:self_correction begin -->
- corrective: "{verbatim corrective-call excerpt}" | prior: "{verbatim prior-call excerpt}" | signature: {oracle-mismatch|wrong-target|basis-change} | reason: {why judged not-a-self-correction} | cite: {turn ref}
<!-- retrospect:self_correction end -->

<!-- retrospect:transcript_receipt begin -->
$ grep -c '"is_error":true' {transcript_path}
{real N}
$ grep -c '"role":"user"' {transcript_path}
{real N}
is_error_count: {N} | user_turn_count: {N} | interrupt_count: {N}
$ python3 -c "...emit each tool_result is_error body..." {transcript_path}
<!-- retrospect:is_error_enum begin -->
- [1] {error content excerpt} | disposition: dismiss (negative-list: expected hook fire)
- [2] {error content excerpt} | disposition: note (transient timeout, retried)
- [k] {error content excerpt} | disposition: promote (finding #N)
<!-- retrospect:is_error_enum end -->
<!-- retrospect:transcript_receipt end -->

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

<!-- memory_scan finding #<n>:
  scanned: true
  candidates_reviewed: <concrete paths or unreachable marker>
  repeat: true|false
  repeat_count: <integer>
-->

```

## Worked examples (per-finding plan calibration)

Example (single action — repeat pattern):
> Finding #2: Workflow step skipped (4th occurrence)
>
> - **Proposed Actions**: GitHub issue
> - **Rationale**: Already recorded 3x in MEMORY.md. Memory alone has failed. Structural fix required.
> - **What will be created**: issue — `feat(hook): add external-repo commit guard`
> - **Verify**: issue URL returned + `gh issue view` confirms existence
> - **Stage 2 caveats**: (none — tracer confidence HIGH, repeat=true with 4 observations)
> - **Falsification**: premise survived — disconfirming observation would be "memory rule prevented step skip in ≥1 prior session"; Stage 2 MEMORY.md scan found no such evidence in any of the 3 prior memory entries

Example (compound action — rule gap + repeat):
> Finding #1 (HIGH): Hasty interpretation without verification (ambiguous signal → worst-case conclusion, 3 occurrences)
>
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
>
> - **Proposed Actions**: `upstream_feedback`
> - **Rationale**: Tool defect surfaced at step 4b — performance issue.<br>backing_repo: `<resolved_backing_repo>`
> - **What will be created**: issue in the resolved backing repo — `perf(<plugin>): reduce MCP timeout on <op>`
> - **Verify**: `gh issue view {url}` succeeds + URL repo matches resolved backing repo
> - **Stage 2 caveats**: `single observation`; `tracer confidence: MED`; `alternative root cause not ruled out: transient network blip`
> - **Falsification**: premise failed — `(Recommended)` label suppressed. Disconfirming observation IS present (single timeout indistinguishable from transient blip without a second sample). Option surfaced unranked; AskUserQuestion includes premise-confirmation line: "이 timeout이 패턴인지 단발 blip인지 추가 관찰 전에 upstream 이슈를 열까요?"
