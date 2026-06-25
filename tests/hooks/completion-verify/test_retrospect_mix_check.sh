#!/bin/bash
# tests/test_retrospect_mix_check.sh — Stop hook coverage for retrospect-mix-check
#
# Synthesizes JSONL transcripts ending in retrospect Stage 3 outputs and runs
# the hook with stop_hook_active=false to assert: T3 double gate (Gate-1
# categorical + Gate-2 5-line rationale schema). Behavior-only sessions with
# proper rationales pass. Tool/workflow/spec-gap labeled findings ending as
# memory-only block. Memory-only without 5-line rationale block.
#
# Run:  ./tests/test_retrospect_mix_check.sh
# Exit: 0 on success, 1 on first failure (after summary).

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/completion-verify/retrospect-mix-check/impl.sh"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Build a JSONL transcript file from $3 and run the hook.
# Args:
#   $1 = case name
#   $2 = expected: "block" | "pass"
#   $3 = transcript JSONL content (each line is one event)
#   $4 = (optional) override for stop_hook_active (default false)
#   $5 = (optional) override for transcript_path ("missing" means non-existent path; "empty" means empty file)
run_case() {
  local name="$1" expected="$2" transcript="$3"
  local stop_active="${4:-false}"
  local path_override="${5:-}"

  local tpath
  if [ "$path_override" = "missing" ]; then
    tpath="$TMPDIR/does_not_exist_$$.jsonl"
  elif [ "$path_override" = "empty" ]; then
    tpath="$TMPDIR/empty_${PASS}_${FAIL}.jsonl"
    : > "$tpath"
  else
    tpath="$TMPDIR/transcript_${PASS}_${FAIL}.jsonl"
    printf '%s\n' "$transcript" > "$tpath"
  fi

  local payload
  payload=$(jq -nc --arg path "$tpath" --argjson sa "$stop_active" \
    '{transcript_path: $path, stop_hook_active: $sa, session_id: "test-session"}')

  local out
  out=$(printf '%s' "$payload" | "$HOOK" 2>/dev/null)
  local rc=$?

  if [ "$rc" -ne 0 ]; then
    echo "FAIL  [$name] hook exited $rc (expected 0)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi

  case "$expected" in
    block)
      if ! echo "$out" | grep -q '"decision": "block"'; then
        echo "FAIL  [$name] expected block, got: ${out:-<empty>}"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      ;;
    pass)
      if [ -n "$out" ]; then
        echo "FAIL  [$name] expected pass (no output), got: $out"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      ;;
    *)
      echo "FAIL  [$name] unknown expected: $expected"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac
  echo "PASS  [$name]"
  PASS=$((PASS + 1))
}

# JSONL builder: emits one assistant message with the given text content.
mk_assistant() {
  local text="$1"
  jq -nc --arg t "$text" '{
    type: "assistant",
    isSidechain: false,
    message: {role: "assistant", content: [{type: "text", text: $t}]}
  }'
}

# Stage 3 retrospect output builders -----------------------------------------

# A minimal valid retrospect Stage 3 output, parameterized.
# Args:
#   $1 = distribution-card body (between fences); newline-separated KEY: VALUE lines
#   $2 = unified-table rows (newline-separated, each row a full markdown row including pipes)
mk_retrospect_stage3() {
  local card="$1" rows="$2"
  cat <<EOF
## Retrospect Report — 2026-04-30

<!-- retrospect:distribution begin -->
$card
<!-- retrospect:distribution end -->

| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|----------|------------|---------|------------|------------|---------|------------------------|-----------|----------|
$rows

<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: synthetic Stop-hook fixture — ledger-presence test only, not a real session | disposition: surface
- self_adversarial: ran | result: synthetic fixture ledger only
- critic_diff: not-run | reason: tier predicate false (synthetic fixture)
<!-- retrospect:suppression_ledger end -->
EOF
}

# A Stage 3 report WITHOUT the suppression_ledger fence (Gate-8 negative cases).
# Same shape as mk_retrospect_stage3 minus the ledger block.
mk_retrospect_stage3_no_ledger() {
  local card="$1" rows="$2"
  cat <<EOF
## Retrospect Report — 2026-04-30

<!-- retrospect:distribution begin -->
$card
<!-- retrospect:distribution end -->

| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|----------|------------|---------|------------|------------|---------|------------------------|-----------|----------|
$rows
EOF
}

# Standard 5-line rationale (valid Gate-2).
RATIONALE_5LINE='not issue: first occurrence, no enforcement target<br>not claude_md_draft: no rule gap, behavior pattern<br>not skill_idea: no recurring trigger<br>not hook_code: <3 repeats, not at enforcement threshold<br>not upstream_feedback: no tool defect involved'

# Test cases ----------------------------------------------------------------

# T1: pass — behavior-only, memory-only with valid 5-line rationale
T1_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: PASS
EOF
)
T1_ROW="| 1 | behavioral | — | hasty conclusion | did not verify | rule absent | No | memory | ${RATIONALE_5LINE} | MED |"
run_case "T1_pass_behavior_only_with_5line_rationale" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")")"

# T2: pass — tool finding escalated to upstream_feedback with backing_repo declared
T2_CARD=$(cat <<EOF
- memory: 0
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 1
- gate_1_verdict: PASS
- gate_2_verdict: NA
EOF
)
T2_ROW="| 1 | tool | cli | gh CLI flag missing | flag undocumented | gap | No | upstream_feedback | tool defect — upstream issue needed<br>backing_repo: devseunggwan/praxis | HIGH |"
run_case "T2_pass_escalated_tool_finding" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T2_CARD" "$T2_ROW")")"

# T3: pass — workflow finding escalated to hook_code
T3_CARD=$(cat <<EOF
- memory: 0
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 1
- upstream_feedback: 0
- gate_1_verdict: PASS
- gate_2_verdict: NA
EOF
)
T3_ROW="| 1 | workflow | — | tests skipped before PR | no enforcement | gap | No | hook_code | workflow gap needs pre-commit gate | HIGH |"
run_case "T3_pass_escalated_workflow_finding" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T3_CARD" "$T3_ROW")")"

# T4: pass — compound action (memory + skill_idea), Gate-2 doesn't apply
T4_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 1
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: NA
EOF
)
T4_ROW="| 1 | behavioral | — | judgment misstep | structural | rule absent | No | memory, skill_idea | compound rationale: memo + future enforcement | MED |"
run_case "T4_pass_compound_action" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T4_CARD" "$T4_ROW")")"

# T5: block — tool label, memory-only (Gate-1 violation)
T5_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: FAIL
- gate_2_verdict: PASS
EOF
)
T5_ROW="| 1 | tool | cli | gh flag missing | tool defect | gap | No | memory | ${RATIONALE_5LINE} | HIGH |"
run_case "T5_block_tool_label_memory_only" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T5_CARD" "$T5_ROW")")"

# T6: block — workflow label, memory-only
T6_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: FAIL
- gate_2_verdict: PASS
EOF
)
T6_ROW="| 1 | workflow | — | tests skipped | no enforcement | gap | No | memory | ${RATIONALE_5LINE} | HIGH |"
run_case "T6_block_workflow_label_memory_only" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T6_CARD" "$T6_ROW")")"

# T7: block — spec-gap label, memory-only
T7_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: FAIL
- gate_2_verdict: PASS
EOF
)
T7_ROW="| 1 | spec-gap | — | rule absent | gap | gap | No | memory | ${RATIONALE_5LINE} | MED |"
run_case "T7_block_spec_gap_memory_only" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T7_CARD" "$T7_ROW")")"

# T8: block — memory-only, Rationale empty (Gate-2)
T8_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: FAIL
EOF
)
T8_ROW="| 1 | behavioral | — | misstep | structural | rule absent | No | memory |  | MED |"
run_case "T8_block_memory_no_rationale" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T8_CARD" "$T8_ROW")")"

# T9: block — memory-only, only 3 'not <action>:' lines
T9_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: FAIL
EOF
)
RATIONALE_3LINE='not issue: x<br>not claude_md_draft: y<br>not skill_idea: z'
T9_ROW="| 1 | behavioral | — | misstep | structural | rule absent | No | memory | ${RATIONALE_3LINE} | MED |"
run_case "T9_block_memory_3line_rationale" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T9_CARD" "$T9_ROW")")"

# T10: block — memory-only, Rationale has invalid action key
T10_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: FAIL
EOF
)
RATIONALE_BAD='not foo: bar<br>not issue: x<br>not claude_md_draft: y<br>not skill_idea: z<br>not hook_code: w'
T10_ROW="| 1 | behavioral | — | misstep | structural | rule absent | No | memory | ${RATIONALE_BAD} | MED |"
run_case "T10_block_memory_wrong_action_keys" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T10_CARD" "$T10_ROW")")"

# T11: block — Gate-1 + Gate-2 both fire (tool label + bad rationale)
T11_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: FAIL
- gate_2_verdict: FAIL
EOF
)
T11_ROW="| 1 | tool | cli | gh flag | defect | gap | No | memory | only one line | HIGH |"
run_case "T11_block_both_violations" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T11_CARD" "$T11_ROW")")"

# T12: pass — non-retrospect message
NONRETRO=$(cat <<EOF
Here is a code review summary.

- File: src/foo.py changed 5 lines
- All checks passed
EOF
)
run_case "T12_passthrough_non_retrospect" "pass" \
  "$(mk_assistant "$NONRETRO")"

# T13: pass — older Stage 4 marker present (post-execution); current message is
# fresh Stage 3 but in the SAME message... edge case: if last assistant message
# contains both '## Retrospect Report' AND '## Actions Executed', we look at
# the most recent '## Retrospect Report' block. Test the simple case where the
# only block also contains '## Actions Executed' — pass-through.
T13_TEXT=$(cat <<EOF
## Retrospect Report — 2026-04-30

<!-- retrospect:distribution begin -->
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: PASS
<!-- retrospect:distribution end -->

| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|----------|------------|---------|------------|------------|---------|------------------------|-----------|----------|
| 1 | behavioral | — | x | y | z | No | memory | ${RATIONALE_5LINE} | MED |

## Actions Executed

| # | Action | Result |
|---|--------|--------|
| 1 | MEMORY.md feedback added | ✅ /tmp/foo.md |
EOF
)
run_case "T13_passthrough_after_actions_executed" "pass" \
  "$(mk_assistant "$T13_TEXT")"

# T14: pass — stop_hook_active=true (re-entry guard)
run_case "T14_failsafe_stop_hook_active" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")")" \
  true

# T15: pass — missing transcript path
run_case "T15_failsafe_missing_transcript" "pass" "" false missing

# T16: pass — empty transcript file
run_case "T16_failsafe_empty_transcript" "pass" "" false empty

# T17: pass — malformed JSONL (broken JSON line)
T17_BROKEN='{not valid json{{'
run_case "T17_failsafe_malformed_jsonl" "pass" "$T17_BROKEN"

# T18: pass — missing jq (mock by stripping PATH; only jq absent, bash still works)
T18_NAME="T18_failsafe_missing_jq"
T18_TPATH="$TMPDIR/transcript_t18.jsonl"
printf '%s\n' "$(mk_assistant "$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")")" > "$T18_TPATH"
T18_PAYLOAD=$(jq -nc --arg path "$T18_TPATH" \
  '{transcript_path: $path, stop_hook_active: false, session_id: "t18"}')
T18_OUT=$(printf '%s' "$T18_PAYLOAD" | env -i PATH=/usr/bin:/bin bash -c "PATH=$(dirname "$(command -v bash)"):/usr/bin:/bin '$HOOK'" 2>/dev/null)
T18_RC=$?
if [ "$T18_RC" -eq 0 ] && [ -z "$T18_OUT" ]; then
  echo "PASS  [$T18_NAME]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [$T18_NAME] rc=$T18_RC out=$T18_OUT"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("$T18_NAME")
fi

# T19: block — same-session rerun. Last assistant message contains a 1st block
# with '## Actions Executed' AND a 2nd '## Retrospect Report' block with
# Gate-1 violation. The most-recent block (2nd) lacks '## Actions Executed' →
# hook MUST gate it (regression test for Critic v2 Major #2).
T19_TEXT=$(cat <<EOF
## Retrospect Report — 2026-04-30 (1st run)

<!-- retrospect:distribution begin -->
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: PASS
<!-- retrospect:distribution end -->

| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|----------|------------|---------|------------|------------|---------|------------------------|-----------|----------|
| 1 | behavioral | — | x | y | z | No | memory | ${RATIONALE_5LINE} | MED |

## Actions Executed

| # | Action | Result |
|---|--------|--------|
| 1 | MEMORY.md feedback added | ✅ /tmp/foo.md |

## Retrospect Report — 2026-04-30 (2nd run)

<!-- retrospect:distribution begin -->
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: FAIL
- gate_2_verdict: PASS
<!-- retrospect:distribution end -->

| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|----------|------------|---------|------------|------------|---------|------------------------|-----------|----------|
| 1 | tool | cli | gh flag | defect | gap | No | memory | ${RATIONALE_5LINE} | HIGH |
EOF
)
run_case "T19_block_rerun_after_actions_executed" "block" \
  "$(mk_assistant "$T19_TEXT")"

# T20: pass — em-dash header form (already used in T1, but test hyphen variant)
T20_CARD="$T1_CARD"
T20_ROW="$T1_ROW"
T20_TEXT="$(printf '%s\n' "$(mk_retrospect_stage3 "$T20_CARD" "$T20_ROW")" | sed 's/## Retrospect Report — 2026-04-30/## Retrospect Report - 2026-04-30/')"
run_case "T20_pass_with_hyphen_header" "pass" \
  "$(mk_assistant "$T20_TEXT")"

# T21: interaction with completion-verify. Last assistant message has BOTH a
# retrospect Stage 3 violation AND a '12 passed' evidence string. This hook
# must block on the retrospect violation independently of completion-verify.
T21_TEXT=$(cat <<EOF
Tests ran: 12 passed in 0.85s.

## Retrospect Report — 2026-04-30

<!-- retrospect:distribution begin -->
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: FAIL
- gate_2_verdict: PASS
<!-- retrospect:distribution end -->

| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|----------|------------|---------|------------|------------|---------|------------------------|-----------|----------|
| 1 | tool | cli | gh flag | defect | gap | No | memory | ${RATIONALE_5LINE} | HIGH |
EOF
)
run_case "T21_block_interaction_with_completion_verify" "block" \
  "$(mk_assistant "$T21_TEXT")"

# Code-review hardening cases (HIGH/MEDIUM bypasses) ------------------------

# T22: block — escaped pipe in cell content. Without sentinel substitution,
# 'a\|b\|c' inside Pattern shifts cell indices and Gate-1 misses tool+memory.
T22_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: FAIL
- gate_2_verdict: PASS
EOF
)
T22_ROW="| 1 | tool | cli | a\\|b\\|c | y | gap | No | memory | ${RATIONALE_5LINE} | HIGH |"
run_case "T22_block_escaped_pipe_in_cell" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T22_CARD" "$T22_ROW")")"

# T23: block — short row (9 cells, missing Priority). Fail-closed.
T23_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: PASS
- gate_2_verdict: PASS
EOF
)
T23_ROW="| 1 | tool | cli | x | y | gap | No | memory | ${RATIONALE_5LINE} |"
run_case "T23_block_short_row_schema_violation" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T23_CARD" "$T23_ROW")")"

# T24: block — degenerate compound 'memory, memory' on tool-categorized row.
# Should be treated as memory-only after dedupe → Gate-1 fires.
T24_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: FAIL
- gate_2_verdict: PASS
EOF
)
T24_ROW="| 1 | tool | cli | x | y | gap | No | memory, memory | ${RATIONALE_5LINE} | HIGH |"
run_case "T24_block_degenerate_memory_compound" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T24_CARD" "$T24_ROW")")"

# T25: block — dual distribution card in same Stage 3 block. Earlier card
# claims PASS, later card has FAIL. Hook must read the LAST occurrence.
T25_TEXT=$(cat <<EOF
## Retrospect Report — 2026-04-30

<!-- retrospect:distribution begin -->
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: PASS
- gate_2_verdict: PASS
<!-- retrospect:distribution end -->

(stale draft above; corrected below)

<!-- retrospect:distribution begin -->
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: FAIL
- gate_2_verdict: PASS
<!-- retrospect:distribution end -->

| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|----------|------------|---------|------------|------------|---------|------------------------|-----------|----------|
| 1 | tool | cli | x | y | gap | No | memory | ${RATIONALE_5LINE} | HIGH |
EOF
)
run_case "T25_block_dual_card_last_wins" "block" \
  "$(mk_assistant "$T25_TEXT")"

# T26: pass — retrospect-shaped output inside a fenced code block (example
# in documentation). Hook MUST strip fences before identifier check, else it
# would block legitimate doc-authoring sessions.
T26_TEXT=$(cat <<'TXTEOF'
Here is what a retrospect output looks like (example only, inside fenced code):

```markdown
## Retrospect Report — 2026-04-30

<!-- retrospect:distribution begin -->
- memory: 1
- gate_1_verdict: FAIL
- gate_2_verdict: FAIL
<!-- retrospect:distribution end -->

| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|----------|------------|---------|------------|------------|---------|------------------------|-----------|----------|
| 1 | tool | cli | x | y | gap | No | memory | empty | HIGH |
```

End of example.
TXTEOF
)
run_case "T26_pass_retrospect_inside_fenced_code" "pass" \
  "$(mk_assistant "$T26_TEXT")"

# Gate-3 (backing_repo) cases ------------------------------------------------

# T27: pass — upstream_feedback row with backing_repo declared
T27_CARD=$(cat <<EOF
- memory: 0
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 1
- gate_1_verdict: PASS
- gate_2_verdict: NA
EOF
)
T27_ROW="| 1 | tool | mcp | slow MCP call | latency | gap | No | upstream_feedback | latency defect in tool<br>backing_repo: owner/some-tool | HIGH |"
run_case "T27_pass_upstream_feedback_with_backing_repo" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T27_CARD" "$T27_ROW")")"

# T28: block — issue row missing backing_repo declaration (Gate-3)
T28_CARD=$(cat <<EOF
- memory: 0
- issue: 1
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: NA
EOF
)
T28_ROW="| 1 | behavioral | — | repeat pattern | structural | gap | Yes | issue | systemic fix needed | HIGH |"
run_case "T28_block_issue_row_missing_backing_repo" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T28_CARD" "$T28_ROW")")"

# T29: pass — row with claude_md_draft (not upstream_feedback/issue), no backing_repo needed
T29_CARD=$(cat <<EOF
- memory: 0
- issue: 0
- claude_md_draft: 1
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: NA
EOF
)
T29_ROW="| 1 | spec-gap | — | rule absent | missing rule | gap | No | claude_md_draft | rule gap detected | MED |"
run_case "T29_pass_non_routed_action_no_backing_repo_needed" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T29_CARD" "$T29_ROW")")"

# Gate-3 verdict (distribution card) cases ------------------------------------

# T30: block — gate_3_verdict: FAIL in distribution card.
# Scenario: Stage 2.5 found a 2-action compound whose evidence was insufficient
# (e.g., both actions derived from a single observation with no independent
# citation for the second action). Stage 2.5 emits FAIL; hook must block.
T30_CARD=$(cat <<EOF
- memory: 1
- issue: 1
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: NA
- gate_3_verdict: FAIL
EOF
)
T30_ROW="| 1 | behavioral | — | repeat judgment error | structural | rule absent | Yes | memory, issue | compound action flagged: issue action lacks independent observation<br>backing_repo: devseunggwan/praxis | HIGH |"
run_case "T30_block_gate3_verdict_fail_in_card" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T30_CARD" "$T30_ROW")")"

# T31: pass — gate_3_verdict: PASS in distribution card.
# Scenario: Stage 2.5 verified a 2-action compound; both actions each backed
# by an independent friction-event observation, not decision-coupled.
T31_CARD=$(cat <<EOF
- memory: 0
- issue: 0
- claude_md_draft: 1
- skill_idea: 1
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: NA
- gate_3_verdict: PASS
EOF
)
T31_ROW="| 1 | behavioral | — | judgment misstep | structural | rule absent | No | claude_md_draft, skill_idea | compound verified: each action backed by independent observation; not decision-coupled | MED |"
run_case "T31_pass_gate3_verdict_pass_in_card" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T31_CARD" "$T31_ROW")")"

# Dimension-tag schema (Schema B) cases — issue #285 --------------------------

# T32: pass — memory-only, 1-line Schema B (dimension-tag)
T32_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: PASS
EOF
)
RATIONALE_DIM1='not-others: repeat=0, rule_exists=yes, gateable=no, tool_defect=no'
T32_ROW="| 1 | behavioral | — | hasty conclusion | did not verify | rule absent | No | memory | ${RATIONALE_DIM1} | MED |"
run_case "T32_pass_schema_b_1line_dim_tag" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T32_CARD" "$T32_ROW")")"

# T33: pass — memory-only, 2-line Schema B (dimension-tag)
T33_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: PASS
EOF
)
RATIONALE_DIM2='not-others: behavioral / first-occurrence / no-gate-surface<br>not-others: enforcement_surface=high-cost'
T33_ROW="| 1 | behavioral | — | misjudged action | structural | rule absent | No | memory | ${RATIONALE_DIM2} | LOW |"
run_case "T33_pass_schema_b_2line_dim_tag" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T33_CARD" "$T33_ROW")")"

# T34: block — memory-only, 3-line Schema B (exceeds 1-2 line limit)
T34_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: FAIL
EOF
)
RATIONALE_DIM3='not-others: repeat=0<br>not-others: rule_exists=yes<br>not-others: gateable=no'
T34_ROW="| 1 | behavioral | — | misjudged action | structural | rule absent | No | memory | ${RATIONALE_DIM3} | LOW |"
run_case "T34_block_schema_b_3line_exceeds_limit" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T34_CARD" "$T34_ROW")")"

# T35: block — memory-only, Schema A and B mixed (not allowed)
T35_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: FAIL
EOF
)
RATIONALE_MIXED='not issue: first occurrence<br>not-others: rule_exists=yes, gateable=no, tool_defect=no'
T35_ROW="| 1 | behavioral | — | misjudged action | structural | rule absent | No | memory | ${RATIONALE_MIXED} | LOW |"
run_case "T35_block_schema_a_and_b_mixed" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T35_CARD" "$T35_ROW")")"

# Gate-4 (External-Repo Authorization) cases ----------------------------------

# T36: pass — external=true + gate_4_verdict: PASS in card.
# Scenario: all upstream_feedback rows are own-org; Gate-4 classified them as
# internal and emitted PASS. Stop hook must pass.
T36_CARD=$(cat <<EOF
- memory: 0
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 1
- gate_1_verdict: PASS
- gate_2_verdict: NA
- gate_3_verdict: NA
- gate_4_verdict: PASS
EOF
)
T36_ROW="| 1 | tool | cli | gh flag missing | tool defect | gap | No | upstream_feedback | tool defect confirmed<br>backing_repo: devseunggwan/praxis | HIGH |"
run_case "T36_pass_gate4_verdict_pass" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T36_CARD" "$T36_ROW")")"

# T37: block — external=true + gate_4_verdict absent but ⚠ EXTERNAL: prefix present.
# Scenario: Stage 2.5 emitted the EXTERNAL marker in the Rationale cell but did
# NOT emit gate_4_verdict in the distribution card (Stage 2.5 was partially skipped
# or the card was hand-edited). Stop hook must block.
T37_CARD=$(cat <<EOF
- memory: 0
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 1
- gate_1_verdict: PASS
- gate_2_verdict: NA
- gate_3_verdict: NA
EOF
)
T37_ROW="| 1 | tool | cli | gh flag missing | tool defect | gap | No | upstream_feedback | ⚠ EXTERNAL: per-action approval required at Stage 4<br>tool defect in third-party CLI<br>backing_repo: third-party/tool | HIGH |"
run_case "T37_block_external_marker_no_gate4_verdict" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T37_CARD" "$T37_ROW")")"

# T38: pass — external=false (no upstream_feedback) + gate_4_verdict: NA.
# Scenario: session had no upstream_feedback findings; Gate-4 emitted NA.
# Stop hook must pass.
T38_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: PASS
- gate_3_verdict: NA
- gate_4_verdict: NA
EOF
)
T38_ROW="| 1 | behavioral | — | hasty conclusion | did not verify | rule absent | No | memory | ${RATIONALE_5LINE} | MED |"
run_case "T38_pass_gate4_verdict_na_no_upstream_feedback" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T38_CARD" "$T38_ROW")")"

# T-NEW1: pass — distribution card includes memory_hygiene: N (Stage 1.5
# category count). Stop hook silently ignores the unknown key per the
# AUTHORITATIVE_SCHEMA carve-out for memory_hygiene/output_quality category
# counts. Hook output must be empty (pass).
T_NEW1_CARD=$(cat <<EOF
- memory: 2
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- memory_hygiene: 2
- gate_1_verdict: NA
- gate_2_verdict: PASS
- gate_3_verdict: NA
- gate_4_verdict: NA
EOF
)
T_NEW1_ROW="| 1 | memory_hygiene | — | merge candidate untriggered | merge-first policy not applied | n/a | No | memory | ${RATIONALE_5LINE} | MED |"
run_case "T-NEW1_pass_memory_hygiene_category_count_ignored" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T_NEW1_CARD" "$T_NEW1_ROW")")"

# T-NEW2: pass — Stage 2.7 0-trigger silent skip path emits the literal
# `<!-- retrospect:audit_skipped: no artifacts -->` trail line above the
# distribution card. The trail line lives outside the distribution fence
# and must not affect hook parsing.
T_NEW2_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- memory_hygiene: 0
- output_quality: 0
- gate_1_verdict: NA
- gate_2_verdict: PASS
- gate_3_verdict: NA
- gate_4_verdict: NA
EOF
)
T_NEW2_ROW="| 1 | behavioral | — | mild confusion | minor friction | n/a | No | memory | ${RATIONALE_5LINE} | LOW |"
T_NEW2_TEXT="$(mk_retrospect_stage3 "$T_NEW2_CARD" "$T_NEW2_ROW")
<!-- retrospect:audit_skipped: no artifacts -->"
run_case "T-NEW2_pass_audit_skipped_trail_line_outside_fence" "pass" \
  "$(mk_assistant "$T_NEW2_TEXT")"

# T-NEW3: pass — Stage 2.7 audit fires (PR mergeability trigger detected).
# Distribution card includes output_quality: 1. Finding row uses
# output_quality category + Tool Layer = cli. Hook must pass (output_quality
# is a category count line, silently ignored by parser).
T_NEW3_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- memory_hygiene: 0
- output_quality: 1
- gate_1_verdict: NA
- gate_2_verdict: PASS
- gate_3_verdict: NA
- gate_4_verdict: NA
EOF
)
T_NEW3_ROW="| 1 | output_quality | cli | PR CHANGES_REQUESTED ≥2 | weak first-pass review quality | n/a | No | memory | ${RATIONALE_5LINE} | MED |"
run_case "T-NEW3_pass_output_quality_category_with_cli_layer" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T_NEW3_CARD" "$T_NEW3_ROW")")"

# Regression (issue #516 defect3): a verdict value with TRAILING whitespace
# (e.g. "FAIL " from a hand-edited card) must still block, now that each verdict
# is trimmed via `xargs`. gate_1 trailing-space variant of T5.
# The trailing space is appended via a variable so it survives editor
# whitespace-stripping and stays visible.
SP=' '
T_DEF3_G1_CARD=$(cat <<EOF
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: FAIL${SP}
- gate_2_verdict: PASS
EOF
)
T_DEF3_G1_ROW="| 1 | tool | cli | gh flag missing | tool defect | gap | No | memory | ${RATIONALE_5LINE} | HIGH |"
run_case "T-DEF3_block_gate1_verdict_fail_trailing_space" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T_DEF3_G1_CARD" "$T_DEF3_G1_ROW")")"

# Gate-7 (Transcript-Enumeration Receipt) cases — issue #600 follow-up --------
# A compaction-summary record in the transcript ('"isCompactSummary":true')
# requires the Stage 3 report to carry a 'retrospect:transcript_receipt' fence,
# else Gate-7 blocks. These transcripts have TWO JSONL lines: the compaction
# marker (a user-role record, filtered out of LAST_TEXT) + the assistant report.

# Emit one compaction-summary JSONL record (role=user so it never shadows the
# assistant message the hook extracts for the report block).
mk_compaction() {
  jq -nc '{
    type: "user",
    isCompactSummary: true,
    uuid: "compact-test-uuid",
    message: {role: "user", content: [{type: "text", text: "(compacted summary)"}]}
  }'
}

# Emit a JSONL record simulating a tool_result with is_error:true.
# Used to add real is_error events so hook's grep -c matches declared count.
mk_is_error() {
  jq -nc --arg msg "$1" '{
    type: "tool_result",
    "is_error": true,
    message: {role: "tool", content: [{type: "text", text: $msg}]}
  }'
}

mk_is_error_spaced_json() {
  mk_is_error "$1" | sed -E 's/"type":/"type": /g; s/"is_error":/"is_error": /g; s/"role":/"role": /g'
}

# Emit a JSONL record simulating an exit-0 tool_result whose CONTENT carries an
# error signal (is_error:false). Used to exercise the issue #670 content-error
# floor: the calibrated regex must catch the signal from tool_result content even
# though the envelope flag is false. The line bears "type":"tool_result" so the
# hook's scoped re-derivation counts it.
mk_content_error() {
  jq -nc --arg msg "$1" '{
    type: "tool_result",
    "is_error": false,
    message: {role: "tool", content: [{type: "text", text: $msg}]}
  }'
}

mk_tool_result_user_role() {
  jq -nc --arg msg "$1" '{
    type: "tool_result",
    "is_error": false,
    message: {role: "user", content: [{type: "text", text: $msg}]}
  }'
}

mk_nested_tool_result() {
  jq -nc --arg msg "$1" --argjson is_error "$2" '{
    type: "user",
    message: {
      role: "user",
      content: [{
        type: "tool_result",
        is_error: $is_error,
        content: [{type: "text", text: $msg}]
      }]
    }
  }'
}

mk_nested_tool_result_pair() {
  jq -nc --arg msg1 "$1" --arg msg2 "$2" --argjson is_error "$3" '{
    type: "user",
    message: {
      role: "user",
      content: [
        {
          type: "tool_result",
          is_error: $is_error,
          content: [{type: "text", text: $msg1}]
        },
        {
          type: "tool_result",
          is_error: $is_error,
          content: [{type: "text", text: $msg2}]
        }
      ]
    }
  }'
}

# Emit a JSONL record for a non-compaction user turn.
# Used to add real user turns so hook's grep -c '"role":"user"' matches declared count.
# Note: mk_compaction() already contributes 1 user turn to the count.
mk_user_turn() {
  jq -nc --arg msg "$1" '{
    type: "human",
    message: {role: "user", content: [{type: "text", text: $msg}]}
  }'
}

mk_user_turn_spaced_json() {
  mk_user_turn "$1" | sed -E 's/"type":/"type": /g; s/"role":/"role": /g'
}

# Receipt fence (real-output form) and the unreachable-skip variant.
# [issue #664] is_error_count > 0 requires a nested retrospect:is_error_enum block
# (count proves a scan ran, not that the errors were read).
G_RECEIPT_FENCE='<!-- retrospect:transcript_receipt begin -->
is_error_count: 3 | user_turn_count: 5 | interrupt_count: 0 | content_error_count: 0
<!-- retrospect:is_error_enum begin -->
- [1] hook advisory fire | disposition: dismiss (negative-list: expected enforcement)
- [2] transient timeout | disposition: note (retried, resolved)
- [3] classifier blocked a fabrication attempt | disposition: promote (finding #1)
<!-- retrospect:is_error_enum end -->
<!-- retrospect:transcript_receipt end -->'
# Count-only receipt (is_error_count > 0 but NO enum block) — the issue #664 gap.
G_RECEIPT_COUNT_ONLY='<!-- retrospect:transcript_receipt begin -->
is_error_count: 3 | user_turn_count: 5 | interrupt_count: 0
<!-- retrospect:transcript_receipt end -->'
# Zero-error receipt — no enum block required (nothing to enumerate).
G_RECEIPT_ZERO='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 5 | interrupt_count: 0 | content_error_count: 0
<!-- retrospect:transcript_receipt end -->'
# Enum bypass #1: only the begin marker (no end, no disposition rows). A bare
# substring check on 'retrospect:is_error_enum' would pass this; the strengthened
# begin+end+row check must block it [issue #664 / Codex F2].
G_RECEIPT_ENUM_MARKER_ONLY='<!-- retrospect:transcript_receipt begin -->
is_error_count: 3 | user_turn_count: 5 | interrupt_count: 0
<!-- retrospect:is_error_enum begin -->
<!-- retrospect:transcript_receipt end -->'
# Enum bypass #2: complete begin/end fence but EMPTY (no disposition row). The
# block enumerates nothing — must still block [issue #664 / Codex F2].
G_RECEIPT_ENUM_EMPTY='<!-- retrospect:transcript_receipt begin -->
is_error_count: 3 | user_turn_count: 5 | interrupt_count: 0
<!-- retrospect:is_error_enum begin -->
<!-- retrospect:is_error_enum end -->
<!-- retrospect:transcript_receipt end -->'
G_RECEIPT_SKIPPED='<!-- retrospect:transcript_receipt_skipped: transcript unreachable -->'
# Enum bypass #3: a count-only receipt fence (is_error_count=3, NO enum inside),
# with a complete is_error_enum block + disposition row planted OUTSIDE the
# receipt fence. Whole-block parsing would pass this; fence-scoped parsing must
# block it [issue #664 / CodeRabbit — scope parse to the receipt fence].
G_RECEIPT_ENUM_OUTSIDE_FENCE='<!-- retrospect:transcript_receipt begin -->
is_error_count: 3 | user_turn_count: 5 | interrupt_count: 0
<!-- retrospect:transcript_receipt end -->
<!-- retrospect:is_error_enum begin -->
- [1] planted outside the receipt fence | disposition: dismiss (bypass attempt)
<!-- retrospect:is_error_enum end -->'
# Enum bypass #4: a count-only receipt fence with a stray transcript_receipt_skipped
# token elsewhere. The old elif short-circuited on the skipped substring anywhere;
# fence-scoped parsing must still block (a real begin fence is present) [CodeRabbit].
G_RECEIPT_SKIPPED_INJECTION='<!-- retrospect:transcript_receipt_skipped: not really -->
<!-- retrospect:transcript_receipt begin -->
is_error_count: 3 | user_turn_count: 5 | interrupt_count: 0
<!-- retrospect:transcript_receipt end -->'
# Malformed fence: receipt fence present but NO is_error_count line inside it.
G_RECEIPT_NO_COUNT='<!-- retrospect:transcript_receipt begin -->
some prose without a parseable count line
<!-- retrospect:transcript_receipt end -->'
# Enum bypass #5: a prior CLOSED fence (is_error_count=0, would pass) followed by
# a new UNTERMINATED begin fence. Marker-count parsing would reuse the stale
# closed fence and pass; state-based unterminated detection must block [Codex r2].
G_RECEIPT_UNTERMINATED_TRAILING='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 5 | interrupt_count: 0
<!-- retrospect:transcript_receipt end -->
<!-- retrospect:transcript_receipt begin -->
is_error_count: 3 | user_turn_count: 5 | interrupt_count: 0'
# Enum bypass #6: a NESTED begin inside an open fence. The outer fence carries
# is_error_count=3 with no enum, but an inner begin/count=0/end is injected before
# the outer close. The extractor would reset its buffer on the inner begin and
# validate only the inner count=0 span; nested-begin detection must block [Codex r3].
G_RECEIPT_NESTED_BEGIN='<!-- retrospect:transcript_receipt begin -->
is_error_count: 3 | user_turn_count: 5 | interrupt_count: 0
<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 5 | interrupt_count: 0
<!-- retrospect:transcript_receipt end -->'
# Enum bypass #7: TWO complete fences. The first (adverse) has is_error_count=3
# with no enum; the second (benign) has is_error_count=0. Validating only the
# last fence would let the benign one mask the adverse one [Codex r4 — exactly
# one receipt per Stage 3 report].
G_RECEIPT_MULTI_FENCE_MASK='<!-- retrospect:transcript_receipt begin -->
is_error_count: 3 | user_turn_count: 5 | interrupt_count: 0
<!-- retrospect:transcript_receipt end -->
<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 5 | interrupt_count: 0
<!-- retrospect:transcript_receipt end -->'
# Single unterminated fence (begin, count, no end) — exercises the malformed
# state check in isolation (not caught by the >1 fence count rule).
G_RECEIPT_SINGLE_UNTERMINATED='<!-- retrospect:transcript_receipt begin -->
is_error_count: 3 | user_turn_count: 5 | interrupt_count: 0'
# Valid single-fence receipt whose enum rows MENTION the marker strings inline
# (both wrapped and bare). Marker matches are anchored to standalone comment
# delimiter lines, so these in-row mentions are NOT counted as fences/markers and
# the report passes — a retrospect OF a session about these markers (e.g. this
# change) must not be false-positive blocked [Codex round 5].
G_RECEIPT_VALID_MARKER_MENTIONS='<!-- retrospect:transcript_receipt begin -->
is_error_count: 2 | user_turn_count: 5 | interrupt_count: 0 | content_error_count: 0
<!-- retrospect:is_error_enum begin -->
- [1] hook discussed <!-- retrospect:transcript_receipt begin --> marker handling | disposition: note (meta-mention)
- [2] error citing retrospect:is_error_enum begin and transcript_receipt end in prose | disposition: dismiss (negative-list)
<!-- retrospect:is_error_enum end -->
<!-- retrospect:transcript_receipt end -->'

# G1: block — post-compaction transcript, Stage 3 report WITHOUT a receipt.
# The card itself is valid (Gates 1-6 pass); only Gate-7 fires.
G1_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")")"
run_case "G1_block_postcompaction_no_receipt" "block" "$G1_TRANSCRIPT"

# G2: pass — post-compaction transcript, report WITH the receipt fence.
# G_RECEIPT_FENCE declares is_error_count: 3 | user_turn_count: 5.
# Transcript must contain 3 is_error events and 5 user turns (compaction=1 + 4 more).
G2_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_FENCE}"
G2_TRANSCRIPT="$(mk_compaction)
$(mk_user_turn "user message 2")
$(mk_user_turn "user message 3")
$(mk_user_turn "user message 4")
$(mk_user_turn "user message 5")
$(mk_is_error "hook advisory fire")
$(mk_is_error "transient timeout")
$(mk_is_error "classifier blocked a fabrication attempt")
$(mk_assistant "$G2_REPORT")"
run_case "G2_pass_postcompaction_with_receipt" "pass" "$G2_TRANSCRIPT"

# G3: pass — NO compaction marker, report WITHOUT a receipt. Gate-7 is dormant
# (regression: the gate must not fire on non-post-compaction sessions).
run_case "G3_pass_no_compaction_no_receipt_required" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")")"

# G4: pass — post-compaction, report carries the skipped-variant line. The
# 'retrospect:transcript_receipt' substring matches '..._skipped', satisfying
# the structural check (SKILL.md governs when the skip variant is legitimate).
G4_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_SKIPPED}"
G4_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$G4_REPORT")"
run_case "G4_pass_postcompaction_receipt_skipped_variant" "pass" "$G4_TRANSCRIPT"

# G5: pass — the ONLY occurrence of the marker is a TEXTUAL mention inside a
# message body. mk_assistant JSON-escapes it to \"isCompactSummary\":true on disk,
# so the compaction grep's (^|[^\\]) prefix excludes it: Gate-7 stays dormant and
# the receipt-less report passes. Regression for the false-trigger CodeRabbit
# flagged on PR #639 — a retrospect session that merely DISCUSSES compaction (this
# very file does) must not be forced to carry a receipt.
G5_MENTION='this session discusses the "isCompactSummary":true compaction marker in prose'
G5_TRANSCRIPT="$(mk_assistant "$G5_MENTION")
$(mk_assistant "$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")")"
run_case "G5_pass_textual_mention_not_false_triggered" "pass" "$G5_TRANSCRIPT"

# G6: block — post-compaction, receipt present with is_error_count=3 > 0 but NO
# nested 'retrospect:is_error_enum' content block [issue #664]. A count-only
# receipt proves a scan ran, not that the errors were read; Gate-7 content check
# fires. This is the exact gap that let a salient-window pass survive the gate.
G6_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_COUNT_ONLY}"
G6_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$G6_REPORT")"
run_case "G6_block_postcompaction_receipt_count_only_no_enum" "block" "$G6_TRANSCRIPT"

# G7: pass — post-compaction, receipt with is_error_count=0. No enum block is
# required (nothing to enumerate); the content check must NOT fire on zero errors.
# G_RECEIPT_ZERO declares is_error_count: 0 | user_turn_count: 5.
# Transcript must contain 0 is_error events and 5 user turns (compaction=1 + 4 more).
G7_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_ZERO}"
G7_TRANSCRIPT="$(mk_compaction)
$(mk_user_turn "user message 2")
$(mk_user_turn "user message 3")
$(mk_user_turn "user message 4")
$(mk_user_turn "user message 5")
$(mk_assistant "$G7_REPORT")"
run_case "G7_pass_postcompaction_receipt_zero_errors_no_enum_needed" "pass" "$G7_TRANSCRIPT"

# G8: block — enum begin marker present but no end marker and no disposition row.
# A bare substring match would let this pass; the begin+end+row check blocks it
# [issue #664 / Codex F2 — marker presence must not substitute for content].
G8_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_ENUM_MARKER_ONLY}"
G8_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$G8_REPORT")"
run_case "G8_block_postcompaction_enum_begin_marker_only" "block" "$G8_TRANSCRIPT"

# G9: block — complete enum begin/end fence but EMPTY (no disposition row). The
# fence enumerates nothing, so the count-not-content gap reappears one layer down;
# the disposition-row count must be >= 1 [issue #664 / Codex F2].
G9_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_ENUM_EMPTY}"
G9_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$G9_REPORT")"
run_case "G9_block_postcompaction_enum_fence_empty_no_rows" "block" "$G9_TRANSCRIPT"

# G10: block — count-only receipt fence with a valid enum block planted OUTSIDE
# the receipt fence. Fence-scoped parsing ignores the out-of-fence enum, so the
# in-fence count=3 with no in-fence enum blocks [issue #664 / CodeRabbit].
G10_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_ENUM_OUTSIDE_FENCE}"
G10_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$G10_REPORT")"
run_case "G10_block_postcompaction_enum_outside_receipt_fence" "block" "$G10_TRANSCRIPT"

# G11: block — count-only receipt fence with a stray transcript_receipt_skipped
# token elsewhere. The skipped short-circuit no longer fires when a real begin
# fence exists, so the count=3 with no enum blocks [CodeRabbit].
G11_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_SKIPPED_INJECTION}"
G11_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$G11_REPORT")"
run_case "G11_block_postcompaction_stray_skipped_token_injection" "block" "$G11_TRANSCRIPT"

# G12: block — receipt fence present but no parseable is_error_count line inside.
# A malformed fence cannot satisfy the gate.
G12_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_NO_COUNT}"
G12_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$G12_REPORT")"
run_case "G12_block_postcompaction_receipt_fence_no_count_line" "block" "$G12_TRANSCRIPT"

# G13: block — a prior closed fence (count=0, would pass) followed by an
# unterminated trailing begin. The latest fence is malformed; a stale closed
# fence must not mask it [Codex round 2 — state-based unterminated detection].
G13_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_UNTERMINATED_TRAILING}"
G13_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$G13_REPORT")"
run_case "G13_block_postcompaction_unterminated_trailing_fence" "block" "$G13_TRANSCRIPT"

# G14: block — a nested begin inside an open fence. The outer count=3 has no enum;
# the inner count=0 span must not be the validation target [Codex round 3].
G14_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_NESTED_BEGIN}"
G14_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$G14_REPORT")"
run_case "G14_block_postcompaction_nested_begin_fence" "block" "$G14_TRANSCRIPT"

# G15: block — two complete fences (adverse count=3 no-enum, then benign count=0).
# Exactly one receipt is allowed; the benign fence must not mask the adverse one
# [Codex round 4].
G15_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_MULTI_FENCE_MASK}"
G15_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$G15_REPORT")"
run_case "G15_block_postcompaction_multi_fence_masking" "block" "$G15_TRANSCRIPT"

# G16: block — a single unterminated fence (begin without end). Exercises the
# malformed state detection in isolation (begin count is 1, so the >1 rule does
# not fire).
G16_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_SINGLE_UNTERMINATED}"
G16_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$G16_REPORT")"
run_case "G16_block_postcompaction_single_unterminated_fence" "block" "$G16_TRANSCRIPT"

# G17: pass — a VALID single-fence receipt whose enum rows mention the marker
# strings inline. Anchored marker matching ignores in-row mentions, so a
# retrospect of a session about these markers is not false-positive blocked
# [Codex round 5 — anchor to standalone comment-delimiter lines].
# G_RECEIPT_VALID_MARKER_MENTIONS declares is_error_count: 2 | user_turn_count: 5.
# Transcript must contain 2 is_error events and 5 user turns (compaction=1 + 4 more).
G17_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_VALID_MARKER_MENTIONS}"
G17_TRANSCRIPT="$(mk_compaction)
$(mk_user_turn "user message 2")
$(mk_user_turn "user message 3")
$(mk_user_turn "user message 4")
$(mk_user_turn "user message 5")
$(mk_is_error "hook discussed marker handling")
$(mk_is_error "error citing is_error_enum marker in prose")
$(mk_assistant "$G17_REPORT")"
run_case "G17_pass_postcompaction_valid_receipt_inline_marker_mentions" "pass" "$G17_TRANSCRIPT"

# Issue #666 — retrospect-active Stage-3 fence-omission gate -----------------
# A session-scoped marker (written by the retrospect-active-marker hook at
# skill-invocation time) is simulated via PRAXIS_RETROSPECT_ACTIVE_FILE. When
# the marker is present, a Stage 3 report that omits the distribution fence (and
# is not a Stage 4 completion) must block — it can no longer no-op the gates by
# evading the format-keyed identifier checks (localized header / dropped fence).
# When the marker is ABSENT, the gate stays dormant (existing behavior).

run_marker_case() {
  # $1 name  $2 expected(block|pass)  $3 transcript  $4 marker-assert(""|cleared|kept)
  local name="$1" expected="$2" transcript="$3" assert_marker="${4:-}"
  local tpath="$TMPDIR/mk_transcript_${PASS}_${FAIL}.jsonl"
  printf '%s\n' "$transcript" > "$tpath"
  local mfile="$TMPDIR/mk_marker_${PASS}_${FAIL}.json"
  printf '%s' '{"retrospect_active": true, "source": "test"}' > "$mfile"
  local payload
  payload=$(jq -nc --arg path "$tpath" \
    '{transcript_path: $path, stop_hook_active: false, session_id: "test-session"}')
  local out rc ok=true
  out=$(printf '%s' "$payload" | env PRAXIS_RETROSPECT_ACTIVE_FILE="$mfile" "$HOOK" 2>/dev/null)
  rc=$?
  if [ "$rc" -ne 0 ]; then ok=false; echo "FAIL  [$name] rc=$rc (expected 0)"; fi
  case "$expected" in
    block) echo "$out" | grep -q '"decision": "block"' || { ok=false; echo "FAIL  [$name] expected block, got: ${out:-<empty>}"; } ;;
    pass)  [ -z "$out" ] || { ok=false; echo "FAIL  [$name] expected pass (no output), got: $out"; } ;;
  esac
  case "$assert_marker" in
    cleared) [ ! -f "$mfile" ] || { ok=false; echo "FAIL  [$name] expected marker cleared, still present"; } ;;
    kept)    [ -f "$mfile" ]   || { ok=false; echo "FAIL  [$name] expected marker kept, was cleared"; } ;;
  esac
  if [ "$ok" = "true" ]; then echo "PASS  [$name]"; PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); fi
}

# F1: block — localized (non-canonical) header + findings table, NO distribution
# fence, no Actions Executed, marker active. THE observed bypass (issue #666).
F666_LOC_BYPASS=$(cat <<EOF
## 회고 보고서 — 2026-04-30

| # | 분류 | 도구 | 패턴 | 근본 원인 | 규칙 | 반복 | 제안 액션 | 근거 | 우선순위 |
|---|------|------|------|----------|------|------|----------|------|---------|
| 1 | behavioral | — | 성급한 결론 | 미검증 | 규칙 없음 | No | memory | 메모만 | MED |
EOF
)
run_marker_case "F666_1_block_localized_header_no_fence" "block" \
  "$(mk_assistant "$F666_LOC_BYPASS")" "kept"

# F2: pass — valid Stage 3 WITH fence (gates pass), marker active. The marker
# does not turn a well-formed report into a block; it stays set awaiting approval.
run_marker_case "F666_2_pass_valid_stage3_with_fence" "pass" \
  "$(mk_assistant "$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")")" "kept"

# F3: pass + marker cleared — Stage 4 (## Actions Executed) reached. Cycle
# complete; the gate clears the marker so subsequent Stops are not gated.
run_marker_case "F666_3_pass_stage4_clears_marker" "pass" \
  "$(mk_assistant "$T13_TEXT")" "cleared"

# F4: pass (regression) — SAME bypass shape as F1 but marker ABSENT. The gate is
# dormant without the marker; pre-#666 pass-through behavior is preserved.
run_case "F666_4_pass_no_marker_gate_dormant" "pass" \
  "$(mk_assistant "$F666_LOC_BYPASS")"

# F5: block — localized header + fence PRESENT but with a Gate-1 violation
# (tool-labeled, memory-only). Proves the gates now run header-INDEPENDENTLY in a
# marker-active session: pre-#666 this passed (no canonical header → exit 0).
F666_LOC_FENCED=$(cat <<EOF
## 회고 — 2026-04-30

<!-- retrospect:distribution begin -->
- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: FAIL
- gate_2_verdict: PASS
<!-- retrospect:distribution end -->

| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|----------|------------|---------|------------|------------|---------|------------------------|-----------|----------|
| 1 | tool | cli | gh flag missing | tool defect | gap | No | memory | ${RATIONALE_5LINE} | HIGH |
EOF
)
run_marker_case "F666_5_block_localized_header_fence_gate1_violation" "block" \
  "$(mk_assistant "$F666_LOC_FENCED")" "kept"

# F6: block — canonical header PRESENT but fence ABSENT (partial bypass: header
# kept, fence dropped), table present, no actions, marker active.
F666_HEADER_NO_FENCE=$(cat <<EOF
## Retrospect Report — 2026-04-30

| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|----------|------------|---------|------------|------------|---------|------------------------|-----------|----------|
| 1 | behavioral | — | x | y | z | No | memory | ${RATIONALE_5LINE} | MED |
EOF
)
run_marker_case "F666_6_block_header_present_fence_absent" "block" \
  "$(mk_assistant "$F666_HEADER_NO_FENCE")" "kept"

# F7: pass — marker active, a legitimate pre-Stage-3 prose clarification stop
# (SKILL.md self-conflict / ambiguous-backing_repo surfaces present PROSE, no
# findings table). The block is narrowed to report-shaped (table-bearing)
# messages, so a prose clarification is NOT a false positive.
run_marker_case "F666_7_pass_pre_stage3_prose_clarification_no_table" "pass" \
  "$(mk_assistant "회고를 진행하기 전에 확인이 필요합니다. carried finding 과 이번 사이클 결론이 충돌합니다: foo.md 가 'absent' vs 'self-managed'. 이 finding 을 유지/반증/수정 중 무엇으로 처리할까요?")" "kept"

# F7b: block — marker active, prose PLUS a localized findings table, no fence,
# no Actions Executed. The table makes it report-shaped → blocked (the bypass).
F666_PROSE_TABLE=$(cat <<EOF
회고 결과를 정리했습니다.

| 번호 | 분류 | 도구 | 패턴 | 근본원인 | 규칙 | 반복 | 액션 | 근거 | 우선순위 |
|------|------|------|------|----------|------|------|------|------|---------|
| 1 | behavioral | — | 성급한 결론 | 미검증 | 없음 | No | memory | 메모 | MED |

승인하시겠습니까?
EOF
)
run_marker_case "F666_7b_block_prose_with_localized_table_no_fence" "block" \
  "$(mk_assistant "$F666_PROSE_TABLE")" "kept"

# Gate-7 VALUE check cases — issue #671 -------------------------------------
# A receipt with counts transcribed from a stale compaction summary rather than
# re-derived this turn passes every STRUCTURAL check but may have counts that
# diverge from a live grep of the same transcript. The value check re-derives
# is_error_count and user_turn_count and blocks when the delta > tolerance (1).
#
# Transcript construction: the fixtures below use real JSONL so the hook's own
# grep yields deterministic values. mk_compaction() yields 1 "role":"user" line;
# mk_assistant() yields 0 "role":"user" lines. mk_is_error() and mk_user_turn()
# are defined near mk_compaction() above.

# V1: block — receipt declares is_error_count=5 but transcript has 0 is_error events.
# This is the core "stale receipt from compaction summary" scenario.
V1_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 5 | user_turn_count: 1 | interrupt_count: 0
<!-- retrospect:is_error_enum begin -->
- [1] hook advisory | disposition: dismiss (expected enforcement)
- [2] timeout | disposition: note (retried)
- [3] classifier block | disposition: promote (finding #1)
- [4] network error | disposition: note (transient)
- [5] parse error | disposition: dismiss (negative-list)
<!-- retrospect:is_error_enum end -->
<!-- retrospect:transcript_receipt end -->'
V1_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${V1_RECEIPT}"
# Transcript: 1 compaction (1 user turn), 1 assistant message, 0 is_error events.
# Live grep: is_error_count=0, user_turn_count=1.
# Declared: is_error_count=5 — delta=5 > tolerance=1 → block.
V1_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$V1_REPORT")"
run_case "V1_block_stale_is_error_count_from_compaction_summary" "block" "$V1_TRANSCRIPT"

# V2: pass — receipt declares counts matching live transcript exactly.
# Transcript: 1 compaction (user turn), 2 is_error events, 1 assistant.
# Live: is_error_count=2, user_turn_count=1.
V2_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 2 | user_turn_count: 1 | interrupt_count: 0 | content_error_count: 0
<!-- retrospect:is_error_enum begin -->
- [1] hook block | disposition: dismiss (expected enforcement)
- [2] timeout | disposition: note (retried, resolved)
<!-- retrospect:is_error_enum end -->
<!-- retrospect:transcript_receipt end -->'
V2_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${V2_RECEIPT}"
V2_TRANSCRIPT="$(mk_compaction)
$(mk_is_error "hook advisory fire")
$(mk_is_error "transient timeout")
$(mk_assistant "$V2_REPORT")"
run_case "V2_pass_receipt_counts_match_live_transcript" "pass" "$V2_TRANSCRIPT"

# V3: pass — receipt with is_error_count off by exactly 1 (within tolerance).
# Transcript: 1 compaction (user turn), 3 is_error events, 1 assistant.
# Live: is_error_count=3, user_turn_count=1.
# Declared: is_error_count=2 — delta=1 == tolerance=1 → pass.
V3_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 2 | user_turn_count: 1 | interrupt_count: 0 | content_error_count: 0
<!-- retrospect:is_error_enum begin -->
- [1] hook block | disposition: dismiss (expected enforcement)
- [2] timeout | disposition: note (retried, resolved)
<!-- retrospect:is_error_enum end -->
<!-- retrospect:transcript_receipt end -->'
V3_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${V3_RECEIPT}"
V3_TRANSCRIPT="$(mk_compaction)
$(mk_is_error "hook advisory fire")
$(mk_is_error "transient timeout")
$(mk_is_error "third error during live-append race")
$(mk_assistant "$V3_REPORT")"
run_case "V3_pass_is_error_count_off_by_one_within_tolerance" "pass" "$V3_TRANSCRIPT"

# V4: block — user_turn_count declared as 7 but transcript has 1 user turn.
# Delta = 6 > tolerance = 1 → block.
# is_error_count=0 so no enum block required.
V4_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 7 | interrupt_count: 0
<!-- retrospect:transcript_receipt end -->'
V4_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${V4_RECEIPT}"
# Transcript: 1 compaction (1 user turn), 0 is_error events, 1 assistant.
# Live: is_error_count=0, user_turn_count=1. Declared: user_turn_count=7 → block.
V4_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$V4_REPORT")"
run_case "V4_block_stale_user_turn_count_from_compaction_summary" "block" "$V4_TRANSCRIPT"

# V5: pass — _skipped variant bypasses value check (no receipt to verify).
# Regression: the _skipped path must not fire the value check.
V5_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${G_RECEIPT_SKIPPED}"
V5_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$V5_REPORT")"
run_case "V5_pass_skipped_variant_no_value_check" "pass" "$V5_TRANSCRIPT"

# V5b: block — receipt has is_error_count=0 but no user_turn_count field at all.
# An absent user_turn_count must be treated as a mismatch, not silently skipped
# (Codex P2: _gate7_mismatch("", N) returns 0 due to concat digit-only check).
V5B_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | interrupt_count: 0
<!-- retrospect:transcript_receipt end -->'
V5B_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${V5B_RECEIPT}"
# Transcript: 1 compaction, 0 is_error events, 1 assistant. is_error_count=0 matches
# live, but user_turn_count is absent from receipt → block.
V5B_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$V5B_REPORT")"
run_case "V5b_block_absent_user_turn_count_field" "block" "$V5B_TRANSCRIPT"

# V6: pass — non-post-compaction session; Gate-7 is dormant.
# No compaction marker in transcript, so value check never runs even if
# receipt counts diverge (though there is no receipt here anyway).
V6_TRANSCRIPT="$(mk_assistant "$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")")"
run_case "V6_pass_no_compaction_gate7_dormant" "pass" "$V6_TRANSCRIPT"

# Content-error-signal enum cases — issue #670 --------------------------------
# The is_error:true flag only fires when the tool CALL itself fails.  Errors
# embedded in exit-0 tool_result CONTENT (non-zero exit text, js_error, FATAL,
# etc.) are missed by the existing is_error scan. Gate-7 now also requires a
# 'content_error_count: N' line in the receipt and, when N > 0, a
# '<!-- retrospect:content_error_enum begin/end -->' block with per-signal
# disposition rows — mirroring the is_error_enum enforcement exactly.
#
# Calibrated regex: 'Exit code [1-9]|command terminated|js_error|FATAL|
#   No such file|denied|BLOCKED|Usage:([[:space:]]|$)'
# This matches error SYNTAX, NOT the bare word "error", which appears in
# benign JSON field names (e.g. "is_error":false) and agent prose.

# CE1: block — receipt declares content_error_count=2 but NO enum block.
# A count alone proves a scan ran, not that the signals were read.
CE1_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 1 | interrupt_count: 0 | content_error_count: 2
<!-- retrospect:transcript_receipt end -->'
CE1_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${CE1_RECEIPT}"
CE1_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$CE1_REPORT")"
run_case "CE1_block_content_error_count_no_enum" "block" "$CE1_TRANSCRIPT"

# CE2: pass — receipt declares content_error_count=2 AND complete enum block
# with two per-signal disposition rows. Real error signals in the transcript
# body (is_error:false envelope containing "Exit code 1" text).
CE2_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 1 | interrupt_count: 0 | content_error_count: 2
<!-- retrospect:content_error_enum begin -->
- [1] bash Exit code 1 (npm test) | disposition: note (retried with fix, resolved)
- [2] FATAL: database connection refused | disposition: promote (finding #2)
<!-- retrospect:content_error_enum end -->
<!-- retrospect:transcript_receipt end -->'
CE2_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${CE2_RECEIPT}"
CE2_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$CE2_REPORT")"
run_case "CE2_pass_content_error_count_with_enum" "pass" "$CE2_TRANSCRIPT"

# CE3: pass — receipt declares content_error_count=0. No enum block required.
CE3_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 1 | interrupt_count: 0 | content_error_count: 0
<!-- retrospect:transcript_receipt end -->'
CE3_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${CE3_RECEIPT}"
CE3_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$CE3_REPORT")"
run_case "CE3_pass_content_error_count_zero_no_enum_needed" "pass" "$CE3_TRANSCRIPT"

# CE4: block — receipt carries NO content_error_count field at all.
# An absent field is a mismatch; the agent must run the calibrated regex and
# paste the real count. Mirrors the is_error_count absent enforcement.
CE4_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 1 | interrupt_count: 0
<!-- retrospect:transcript_receipt end -->'
CE4_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${CE4_RECEIPT}"
CE4_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$CE4_REPORT")"
run_case "CE4_block_absent_content_error_count_field" "block" "$CE4_TRANSCRIPT"

# CE5: block — content_error_count=1 with enum begin/end fence but NO
# disposition rows inside. Empty enum is the same count-not-content gap.
CE5_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 1 | interrupt_count: 0 | content_error_count: 1
<!-- retrospect:content_error_enum begin -->
<!-- retrospect:content_error_enum end -->
<!-- retrospect:transcript_receipt end -->'
CE5_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${CE5_RECEIPT}"
CE5_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$CE5_REPORT")"
run_case "CE5_block_content_error_enum_fence_empty_no_rows" "block" "$CE5_TRANSCRIPT"

# CE6: pass — benign content containing "is_error":false and field names with
# "error" in them does NOT produce a positive content_error_count. The
# calibrated regex matches error SYNTAX, not the bare word "error". The agent
# runs the regex, gets 0, declares content_error_count: 0, and no enum is needed.
# (Functional verification: 'grep -cE ...' on the fixture text returns 0.)
CE6_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 1 | interrupt_count: 0 | content_error_count: 0
<!-- retrospect:transcript_receipt end -->'
CE6_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${CE6_RECEIPT}"
CE6_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$CE6_REPORT")"
# Verify the calibrated regex indeed yields 0 on benign content
_ce6_benign='{"type":"tool_result","is_error":false,"content":[{"type":"text","text":"error_code: 0, is_error: false, status: ok"}]}'
_ce6_live_count=$(printf '%s\n' "$_ce6_benign" | grep -cE 'Exit code [1-9]|command terminated|js_error|FATAL|No such file|denied|BLOCKED|Usage:([[:space:]]|$)' || true)
if [ "${_ce6_live_count:-0}" -ne 0 ]; then
  echo "FAIL  [CE6_calibration] calibrated regex matched benign content (count=$_ce6_live_count) — regex needs recalibration"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("CE6_calibration")
fi
run_case "CE6_pass_benign_error_field_names_no_false_positive" "pass" "$CE6_TRANSCRIPT"

# CE7: calibration check — verify the calibrated regex DOES match real error
# syntax signals that should be caught (exit-0 content-embedded errors).
# Last line is a BARE usage banner ("Usage:" with no trailing space / at EOL) —
# locks the CodeRabbit #693 fix: the Usage: alternation must match end-of-line.
_ce7_errors='Exit code 1\ncommand terminated\njs_error: TypeError\nFATAL: connection refused\nNo such file or directory\nPermission denied\nBLOCKED by hook\nUsage: gh pr create [flags]\nUsage:'
_ce7_live_count=$(printf '%b\n' "$_ce7_errors" | grep -cE 'Exit code [1-9]|command terminated|js_error|FATAL|No such file|denied|BLOCKED|Usage:([[:space:]]|$)' || true)
_ce7_expected=9
if [ "${_ce7_live_count:-0}" -ne "$_ce7_expected" ]; then
  echo "FAIL  [CE7_calibration] calibrated regex matched $_ce7_live_count/${_ce7_expected} expected error signals — regex needs recalibration"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("CE7_calibration")
else
  echo "PASS  [CE7_calibration_regex_matches_all_error_signals]"
  PASS=$((PASS + 1))
fi

# CE_FLOOR tests [issue #670] — independent re-derivation floor. The structural
# block accepts a declared content_error_count: 0 without an enum; the floor
# re-derives from tool_result content and blocks a declared 0 that launders away
# real exit-0 errors (the same gap #671 closed for is_error_count).

# CE_FLOOR1: block — receipt declares content_error_count: 0 but two tool_result
# bodies carry exit-0 error signals (live=2 > tolerance=1).
CE_FLOOR1_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 1 | interrupt_count: 0 | content_error_count: 0
<!-- retrospect:transcript_receipt end -->'
CE_FLOOR1_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${CE_FLOOR1_RECEIPT}"
CE_FLOOR1_TRANSCRIPT="$(mk_compaction)
$(mk_content_error "Exit code 1: build failed")
$(mk_content_error "FATAL: connection refused")
$(mk_assistant "$CE_FLOOR1_REPORT")"
run_case "CE_FLOOR1_block_declared_zero_while_live_content_errors_exist" "block" "$CE_FLOOR1_TRANSCRIPT"

# CE_FLOOR2: pass — the error syntax appears only in an assistant/text line (the
# agent's own prose), NOT in a tool_result line. Scoping excludes it, so the
# floor does not fire on a declared 0.
CE_FLOOR2_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 1 | interrupt_count: 0 | content_error_count: 0
<!-- retrospect:transcript_receipt end -->'
CE_FLOOR2_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
The narrative mentions Exit code 1 and FATAL in prose but no tool actually failed.
${CE_FLOOR2_RECEIPT}"
CE_FLOOR2_TRANSCRIPT="$(mk_compaction)
$(mk_assistant "$CE_FLOOR2_REPORT")"
run_case "CE_FLOOR2_pass_error_syntax_only_in_assistant_prose_scoped_out" "pass" "$CE_FLOOR2_TRANSCRIPT"

# CE_FLOOR3: pass — a single tool_result error signal is within tolerance (1),
# so a declared 0 is not blocked (mirrors the is_error/user_turn off-by-one rule).
CE_FLOOR3_RECEIPT='<!-- retrospect:transcript_receipt begin -->
is_error_count: 0 | user_turn_count: 1 | interrupt_count: 0 | content_error_count: 0
<!-- retrospect:transcript_receipt end -->'
CE_FLOOR3_REPORT="$(mk_retrospect_stage3 "$T1_CARD" "$T1_ROW")
${CE_FLOOR3_RECEIPT}"
CE_FLOOR3_TRANSCRIPT="$(mk_compaction)
$(mk_content_error "Exit code 1: single transient failure")
$(mk_assistant "$CE_FLOOR3_REPORT")"
run_case "CE_FLOOR3_pass_single_content_error_within_tolerance" "pass" "$CE_FLOOR3_TRANSCRIPT"

# Gate-8 (Suppression-Ledger Receipt) cases — issues #699 / #702 -------------
# Every gateable Stage 3 report must carry a 'retrospect:suppression_ledger'
# fence with 'worst_agent_failure:', 'self_adversarial:', and 'critic_diff:'
# lines — the report-level record of the Stage 2 self-incrimination pass and
# conditional externalized critic tier. Its absence means the painful
# agent-caused friction was never surfaced for audit.

SL_LEDGER_VALID_ADVERSE='<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: ran two identical timeouts before probing cost — wasted a user cycle | disposition: surface
- tempted_to_omit: the second blind retry | reason_considered: looked transient | disposition: surface
- self_adversarial: ran | result: surfaced the silent retry the deterministic lanes missed
- critic_diff: none | checked: 0 candidate(s)
<!-- retrospect:suppression_ledger end -->'
SL_LEDGER_CLEAN='<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: synthetic clean ledger — no painful agent failure in this canned report | disposition: none-found
- self_adversarial: ran | result: no real session to scan
- critic_diff: not-run | reason: tier predicate false (synthetic fixture)
<!-- retrospect:suppression_ledger end -->'
SL_LEDGER_CANONICAL_CLEAN='<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: clean path — nothing painful surfaced after full scan | disposition: surface
- self_adversarial: ran | result: concurred — nothing omitted or softened
- critic_diff: none | checked: 0 candidate(s)
<!-- retrospect:suppression_ledger end -->'
SL_LEDGER_SURFACE_CLEAN='<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: synthetic Stop-hook fixture — not a real session | disposition: surface
- self_adversarial: ran | result: concurred — nothing omitted or softened
- critic_diff: not-run | reason: tier predicate false (synthetic fixture)
<!-- retrospect:suppression_ledger end -->'
SL_LEDGER_ADVERSE_SELF_CLEAN='<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: repeated failing tool call after assuming the command would recover | disposition: surface
- self_adversarial: ran | result: concurred — nothing omitted or softened
- critic_diff: none | checked: 1 candidate(s)
<!-- retrospect:suppression_ledger end -->'
SL_LEDGER_ADVERSE_NOT_REAL_SESSION='<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: misclassified a real transcript as not a real session and skipped the scan | disposition: surface
- self_adversarial: ran | result: concurred — nothing omitted or softened
- critic_diff: none | checked: 1 candidate(s)
<!-- retrospect:suppression_ledger end -->'
SL_LEDGER_NO_ADV='<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: minor — nothing painful this session | disposition: surface
- critic_diff: not-run | reason: tier predicate false (synthetic fixture)
<!-- retrospect:suppression_ledger end -->'
SL_LEDGER_NO_WORST='<!-- retrospect:suppression_ledger begin -->
- self_adversarial: ran | result: concurred — nothing omitted
- critic_diff: not-run | reason: tier predicate false (synthetic fixture)
<!-- retrospect:suppression_ledger end -->'
SL_LEDGER_UNTERMINATED='<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: x | disposition: surface
- self_adversarial: ran | result: concurred
- critic_diff: not-run | reason: tier predicate false (synthetic fixture)'
SL_LEDGER_DOUBLE='<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: adverse one | disposition: surface
- self_adversarial: ran | result: surfaced something
- critic_diff: none | checked: 1 candidate(s)
<!-- retrospect:suppression_ledger end -->
<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: benign mask | disposition: none-found
- self_adversarial: ran | result: concurred
- critic_diff: not-run | reason: tier predicate false (synthetic fixture)
<!-- retrospect:suppression_ledger end -->'

# SL1: block — canonical Stage 3 report, valid Gates 1-6, but NO ledger fence.
run_case "SL1_block_no_suppression_ledger_fence" "block" \
  "$(mk_assistant "$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")")"

# SL2: block — ledger fence present but missing the self_adversarial line.
SL2_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_NO_ADV}"
run_case "SL2_block_ledger_missing_self_adversarial" "block" \
  "$(mk_assistant "$SL2_TEXT")"

# SL3: block — ledger fence present but missing the worst_agent_failure line.
SL3_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_NO_WORST}"
run_case "SL3_block_ledger_missing_worst_agent_failure" "block" \
  "$(mk_assistant "$SL3_TEXT")"

# SL4: block — malformed ledger fence (unterminated begin, no end).
SL4_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_UNTERMINATED}"
run_case "SL4_block_ledger_unterminated_fence" "block" \
  "$(mk_assistant "$SL4_TEXT")"

# SL5: block — two complete ledger fences (a benign one could mask an adverse one).
SL5_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_DOUBLE}"
run_case "SL5_block_ledger_double_fence" "block" \
  "$(mk_assistant "$SL5_TEXT")"

# SL6: pass — single well-formed ledger with all required lines (real adverse content).
SL6_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_VALID_ADVERSE}"
run_case "SL6_pass_valid_adverse_ledger" "pass" \
  "$(mk_assistant "$SL6_TEXT")"

# SL7: block — the marker text appears only as an inline PROSE mention, not as a
# standalone comment-delimiter line. Anchored matching ignores it → no real
# fence → Gate-8 blocks (a mention is not a ledger).
SL7_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
I considered emitting a retrospect:suppression_ledger begin marker but left the fence out."
run_case "SL7_block_ledger_inline_mention_not_a_fence" "block" \
  "$(mk_assistant "$SL7_TEXT")"

# SL8: pass — Stage 4 output (## Actions Executed) with no ledger. The hook
# exits at the Actions-Executed guard before Gate-8; a post-execution report is
# not forced to carry a ledger.
SL8_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")

## Actions Executed

| # | Action | Result |
|---|--------|--------|
| 1 | MEMORY.md feedback added | ✅ /tmp/foo.md |"
run_case "SL8_pass_actions_executed_no_ledger_required" "pass" \
  "$(mk_assistant "$SL8_TEXT")"

# SL9: block — a NESTED ledger begin (begin/begin/end). Both sl_begin>1 and
# sl_malformed=1; the malformed branch must win (checked before the duplicate
# count) so the block reason is "malformed", not "multiple fences" [CodeRabbit
# PR #700].
SL_LEDGER_NESTED='<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: outer | disposition: surface
<!-- retrospect:suppression_ledger begin -->
- self_adversarial: ran | result: nested injection
- critic_diff: not-run | reason: tier predicate false (synthetic fixture)
<!-- retrospect:suppression_ledger end -->'
SL9_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_NESTED}"
run_case "SL9_block_ledger_nested_begin" "block" \
  "$(mk_assistant "$SL9_TEXT")"

# SL10: block — ledger claims none-found while live transcript carries multiple
# deterministic adverse signals. Gate-8b re-derives the floor from transcript
# content instead of trusting the ledger text.
SL10_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_CLEAN}"
SL10_TRANSCRIPT="$(mk_is_error "tool failed")
$(mk_is_error "second tool failed")
$(mk_assistant "$SL10_TEXT")"
run_case "SL10_block_clean_ledger_launders_live_is_error_signals" "block" "$SL10_TRANSCRIPT"

# SL11: pass — a single adverse signal is within the live-append tolerance and
# does not block the clean synthetic ledger.
SL11_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_CLEAN}"
SL11_TRANSCRIPT="$(mk_is_error "single tolerated signal")
$(mk_assistant "$SL11_TEXT")"
run_case "SL11_pass_clean_ledger_single_signal_within_tolerance" "pass" "$SL11_TRANSCRIPT"

# SL12: block — user-correction markers count only when they appear on user
# turns. Multiple real user corrections make a clean ledger self-serving.
SL12_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_CLEAN}"
SL12_TRANSCRIPT="$(mk_user_turn "아니 그거 말고")
$(mk_user_turn "that's not what I asked")
$(mk_assistant "$SL12_TEXT")"
run_case "SL12_block_clean_ledger_launders_user_corrections" "block" "$SL12_TRANSCRIPT"

# SL13: pass — marker text in assistant prose is scoped out; only user turns
# contribute to the Gate-8b user-correction floor.
SL13_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
The narrative mentions 아니 and that's not what I asked, but no user turn did.
${SL_LEDGER_CLEAN}"
run_case "SL13_pass_assistant_prose_markers_scoped_out" "pass" \
  "$(mk_assistant "$SL13_TEXT")"

# SL14: block — canonical clean-path wording uses disposition:surface on
# worst_agent_failure and records the clean claim on self_adversarial. Gate-8b
# must treat that self_adversarial line as clean-like too.
SL14_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_CANONICAL_CLEAN}"
SL14_TRANSCRIPT="$(mk_is_error "tool failed")
$(mk_is_error "second tool failed")
$(mk_assistant "$SL14_TEXT")"
run_case "SL14_block_canonical_clean_self_adversarial_laundering" "block" "$SL14_TRANSCRIPT"

# SL15: pass — self_adversarial can legitimately say nothing else was omitted
# when worst_agent_failure already surfaces a concrete adverse event.
SL15_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_ADVERSE_SELF_CLEAN}"
SL15_TRANSCRIPT="$(mk_is_error "tool failed")
$(mk_is_error "second tool failed")
$(mk_assistant "$SL15_TEXT")"
run_case "SL15_pass_adverse_worst_failure_with_clean_self_adversarial" "pass" "$SL15_TRANSCRIPT"

# SL16: pass — a single tool_result line can carry both is_error:true and
# content-error syntax. The floor dedupes by tool event before applying
# tolerance.
SL16_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_CLEAN}"
SL16_TRANSCRIPT="$(mk_is_error "Exit code 1: single failed command")
$(mk_assistant "$SL16_TEXT")"
run_case "SL16_pass_single_tool_result_deduped_across_lanes" "pass" "$SL16_TRANSCRIPT"

# SL17: block — valid JSONL may include spaces after key separators. Gate-8b
# must not rely on compact jq -c formatting for is_error/tool_result detection.
SL17_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_CLEAN}"
SL17_TRANSCRIPT="$(mk_is_error_spaced_json "tool failed")
$(mk_is_error_spaced_json "second tool failed")
$(mk_assistant "$SL17_TEXT")"
run_case "SL17_block_spaced_json_tool_errors" "block" "$SL17_TRANSCRIPT"

# SL18: block — the same whitespace tolerance applies to user-role correction
# scans.
SL18_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_CLEAN}"
SL18_TRANSCRIPT="$(mk_user_turn_spaced_json "아니 그거 말고")
$(mk_user_turn_spaced_json "that's not what I asked")
$(mk_assistant "$SL18_TEXT")"
run_case "SL18_block_spaced_json_user_corrections" "block" "$SL18_TRANSCRIPT"

# SL19: pass — Claude-style tool_result rows can carry message.role:user.
# They must not be scanned as human correction turns.
SL19_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_CLEAN}"
SL19_TRANSCRIPT="$(mk_tool_result_user_role "no failures in lint output")
$(mk_tool_result_user_role "no changes needed")
$(mk_assistant "$SL19_TEXT")"
run_case "SL19_pass_tool_result_user_role_not_user_correction" "pass" "$SL19_TRANSCRIPT"

# SL20: pass — assistant prose can quote JSON-shaped examples or transcript
# receipts. Gate-8b scans top-level JSONL events, not escaped text inside the
# assistant's Stage 3 report.
SL20_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
Example quoted JSON only: {\"type\":\"tool_result\",\"is_error\":true,\"message\":{\"role\":\"user\",\"content\":[{\"text\":\"Exit code 1 and no failures\"}]}}
${SL_LEDGER_CLEAN}"
SL20_TRANSCRIPT="$(mk_is_error "single tolerated signal")
$(mk_assistant "$SL20_TEXT")"
run_case "SL20_pass_quoted_json_in_assistant_prose_not_counted" "pass" "$SL20_TRANSCRIPT"

# SL21: block — Claude Code commonly stores tool results as nested
# message.content[] blocks inside a top-level user event.
SL21_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_CLEAN}"
SL21_TRANSCRIPT="$(mk_nested_tool_result "Exit code 1: build failed" true)
$(mk_nested_tool_result "Exit code 1: test failed" true)
$(mk_assistant "$SL21_TEXT")"
run_case "SL21_block_nested_tool_result_errors" "block" "$SL21_TRANSCRIPT"

# SL22: pass — the same nested tool_result block can carry both is_error:true
# and content-error syntax; it still counts as one tool event.
SL22_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_CLEAN}"
SL22_TRANSCRIPT="$(mk_nested_tool_result "Exit code 1: single failed command" true)
$(mk_assistant "$SL22_TEXT")"
run_case "SL22_pass_single_nested_tool_result_deduped" "pass" "$SL22_TRANSCRIPT"

# SL23: block — top-level tool_result events may carry their output under
# message.content[] even when is_error:false.
SL23_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_CLEAN}"
SL23_TRANSCRIPT="$(mk_content_error "Exit code 1: build failed")
$(mk_content_error "Exit code 1: test failed")
$(mk_assistant "$SL23_TEXT")"
run_case "SL23_block_top_level_message_content_errors" "block" "$SL23_TRANSCRIPT"

# SL24: pass — multiple failing nested tool_result blocks inside one JSONL
# record count as one adverse tool event before tolerance is applied.
SL24_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_CLEAN}"
SL24_TRANSCRIPT="$(mk_nested_tool_result_pair "Exit code 1: build failed" "Exit code 1: test failed" true)
$(mk_assistant "$SL24_TEXT")"
run_case "SL24_pass_multi_nested_tool_results_deduped_per_record" "pass" "$SL24_TRANSCRIPT"

# SL25: block — surface-style clean ledger detection requires both clean
# worst_agent_failure wording and clean self_adversarial wording.
SL25_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_SURFACE_CLEAN}"
SL25_TRANSCRIPT="$(mk_is_error "tool failed")
$(mk_is_error "second tool failed")
$(mk_assistant "$SL25_TEXT")"
run_case "SL25_block_surface_clean_self_adversarial_laundering" "block" "$SL25_TRANSCRIPT"

# SL26: pass — "not a real session" can also describe an actual failure. Do
# not classify it as clean unless it is explicitly fixture/synthetic clean
# wording.
SL26_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_ADVERSE_NOT_REAL_SESSION}"
SL26_TRANSCRIPT="$(mk_is_error "tool failed")
$(mk_is_error "second tool failed")
$(mk_assistant "$SL26_TEXT")"
run_case "SL26_pass_adverse_not_real_session_wording" "pass" "$SL26_TRANSCRIPT"

# SL27: block — issue #702 adds critic_diff as the audit trail for the
# conditional externalized critic tier.
SL_LEDGER_NO_CRITIC='<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: adverse one | disposition: surface
- self_adversarial: ran | result: surfaced something
<!-- retrospect:suppression_ledger end -->'
SL27_TEXT="$(mk_retrospect_stage3_no_ledger "$T1_CARD" "$T1_ROW")
${SL_LEDGER_NO_CRITIC}"
run_case "SL27_block_ledger_missing_critic_diff" "block" \
  "$(mk_assistant "$SL27_TEXT")"

# Synthetic regression fixtures (AC-R1~R4) ----------------------------------
# Each fixture pairs a .jsonl transcript with a .expected.json sidecar:
#   {expected_decision: "pass"|"block", must_contain: [...], must_not_contain: [...]}

FIXTURE_PASS=0
FIXTURE_FAIL=0
FIXTURE_DIR="$REPO_ROOT/tests/fixtures"

for jsonl in "$FIXTURE_DIR"/retrospect-synth-*.jsonl; do
  [ -f "$jsonl" ] || continue
  name=$(basename "$jsonl" .jsonl)
  expected_file="${jsonl%.jsonl}.expected.json"
  if [ ! -f "$expected_file" ]; then
    echo "FAIL  [fixture:$name] missing $expected_file"
    FIXTURE_FAIL=$((FIXTURE_FAIL + 1))
    continue
  fi

  expected_decision=$(jq -r '.expected_decision' "$expected_file")
  must_contain=$(jq -r '.must_contain[]?' "$expected_file")
  must_not_contain=$(jq -r '.must_not_contain[]?' "$expected_file")

  payload=$(jq -nc --arg path "$jsonl" \
    '{transcript_path: $path, stop_hook_active: false, session_id: "fixture-test"}')
  out=$(printf '%s' "$payload" | "$HOOK" 2>/dev/null)
  rc=$?

  if [ "$rc" -ne 0 ]; then
    echo "FAIL  [fixture:$name] hook exited $rc"
    FIXTURE_FAIL=$((FIXTURE_FAIL + 1))
    continue
  fi

  ok=true
  case "$expected_decision" in
    block)
      echo "$out" | grep -q '"decision": "block"' || { ok=false; echo "FAIL  [fixture:$name] expected block, got: ${out:-<empty>}"; }
      ;;
    pass)
      [ -z "$out" ] || { ok=false; echo "FAIL  [fixture:$name] expected pass (no output), got: $out"; }
      ;;
  esac

  # must_contain checks against the assistant message text in the transcript
  # (the 'pass' case has empty hook output, so we check the transcript content).
  transcript_text=$(jq -rs '.[] | select(.message.role=="assistant") | (.message.content // [])[] | select(.type=="text") | .text' "$jsonl" 2>/dev/null)
  if [ -n "$must_contain" ]; then
    while IFS= read -r needle; do
      [ -z "$needle" ] && continue
      printf '%s' "$transcript_text" | grep -qF "$needle" || { ok=false; echo "FAIL  [fixture:$name] must_contain: $needle (not found in transcript)"; }
    done <<< "$must_contain"
  fi
  if [ -n "$must_not_contain" ]; then
    while IFS= read -r needle; do
      [ -z "$needle" ] && continue
      # must_not_contain is checked against hook output (block reasons)
      printf '%s' "$out" | grep -qF "$needle" && { ok=false; echo "FAIL  [fixture:$name] must_not_contain in hook output: $needle"; }
    done <<< "$must_not_contain"
  fi

  if [ "$ok" = "true" ]; then
    echo "PASS  [fixture:$name]"
    FIXTURE_PASS=$((FIXTURE_PASS + 1))
  else
    FIXTURE_FAIL=$((FIXTURE_FAIL + 1))
  fi
done

# Summary -------------------------------------------------------------------

echo
echo "================================"
echo "Cases:    $PASS passed, $FAIL failed"
echo "Fixtures: $FIXTURE_PASS passed, $FIXTURE_FAIL failed"
echo "================================"

if [ "$FAIL" -gt 0 ] || [ "$FIXTURE_FAIL" -gt 0 ]; then
  if [ "$FAIL" -gt 0 ]; then
    echo "Failed cases:"
    for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  fi
  exit 1
fi
exit 0
