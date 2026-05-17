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

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$REPO_ROOT/hooks/retrospect-mix-check.sh"

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
T18_OUT=$(printf '%s' "$T18_PAYLOAD" | env -i PATH=/usr/bin:/bin bash -c "PATH=$(dirname $(command -v bash)):/usr/bin:/bin '$HOOK'" 2>/dev/null)
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
