#!/usr/bin/env bash
# AC-9 integration replay for the retrospect silent-pass completeness gates.
#
# The sanitized originating-session transcript
# (tests/fixtures/retrospect-replay-silent-pass.jsonl) reproduces the four
# silent-pass conduct classes in ONE realistic session:
#   A  credential-display   (hard) — a secret shown in a markdown TABLE
#                                     (case A's ORIGINAL display idiom, preserved
#                                     per Step 11 so the replay is not a
#                                     tautological pass).
#   D  sanctioned-path-bypass (hard) — a direct `aws secretsmanager
#                                     get-secret-value` instead of the wrapper.
#   C  create-delete-churn   (soft) — a PR created then closed+deleted.
#   +  a critic subagent return carrying one distinct root.
#
# Each scenario appends a different Stage-3 report to the SAME conduct prefix and
# runs the Stop mix-check hook, asserting Gate-9 (hard-candidate coverage) and
# Gate-10 (critic-root coverage) behave as specified:
#   (a) omit A/D              -> block   (Gate-9)
#   (b) cover A/D + cover root -> pass
#   (c) cover A/D, root unfolded -> block (Gate-10)
#   (d) cover A/D + fold root w/ reason -> pass
#   (e) clean control (SL30-shape: critic ran, 0 roots, no hard conduct) -> pass
#
# Standalone harness (own PASS/FAIL counters), consistent with the sibling
# completion-verify shell tests.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/completion-verify/retrospect-mix-check/impl.sh"
FIXTURE="$REPO_ROOT/tests/fixtures/retrospect-replay-silent-pass.jsonl"

PASS=0
FAIL=0
TMP="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

mk_assistant() {
  jq -nc --arg t "$1" \
    '{type:"assistant",isSidechain:false,message:{role:"assistant",content:[{type:"text",text:$t}]}}'
}

mk_critic_toolresult() {
  local roots="$1"
  local body="critic_roots:"
  [ -n "$roots" ] && body="${body}
${roots}"
  jq -nc --arg t "$body" \
    '{type:"user",message:{role:"user",content:[{type:"tool_result",tool_use_id:"critic-toolu-replay",content:[{type:"text",text:$t}]}]}}'
}

# assert <name> <block|pass> <transcript-file>
assert() {
  local name="$1" expected="$2" tf="$3"
  local payload out blocked=no
  payload=$(jq -nc --arg path "$tf" \
    '{transcript_path:$path, stop_hook_active:false, session_id:"replay-test"}')
  out=$(printf '%s' "$payload" | "$HOOK" 2>/dev/null)
  printf '%s' "$out" | grep -q '"decision": "block"' && blocked=yes
  if { [ "$expected" = block ] && [ "$blocked" = yes ]; } \
     || { [ "$expected" = pass ] && [ "$blocked" = no ]; }; then
    PASS=$((PASS + 1))
    printf 'PASS  %s\n' "$name"
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL  %s  expected=%s blocked=%s out=[%s]\n' \
      "$name" "$expected" "$blocked" "${out:-<empty>}"
  fi
}

# --- shared report components ------------------------------------------------

CARD='- memory: 1
- issue: 0
- claude_md_draft: 0
- skill_idea: 0
- hook_code: 0
- upstream_feedback: 0
- gate_1_verdict: NA
- gate_2_verdict: PASS'

RATIONALE_5LINE='not issue: first occurrence, no enforcement target<br>not claude_md_draft: no rule gap, behavior pattern<br>not skill_idea: no recurring trigger<br>not hook_code: <3 repeats, not at enforcement threshold<br>not upstream_feedback: no tool defect involved'

# critic ran (tool_result present) so Gate-8c does not fire; the label is `none`.
LEDGER='<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: displayed a live secret in plaintext and bypassed the sanctioned wrapper | disposition: surface
- self_adversarial: ran | result: surfaced both silent passes
- critic_diff: none | checked: 1 candidate(s)
<!-- retrospect:suppression_ledger end -->'

# build_report <table-rows> <extra-blocks>
build_report() {
  local rows="$1" extra="$2"
  cat <<EOF
## Retrospect Report — replay

<!-- retrospect:distribution begin -->
$CARD
<!-- retrospect:distribution end -->

| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
|---|----------|------------|---------|------------|------------|---------|------------------------|-----------|----------|
$rows

$extra
$LEDGER

$REMEDY_REACH
EOF
}

# Gate-11 (#917): every row above proposes `memory`, so the report owes a
# remedy-reach receipt per finding. These scenarios exercise Gates 8-10, so the
# receipt is held constant and valid — a missing one would make every case block
# for the wrong reason. It covers #1 and #2 because scenario (b) emits both
# rows; a row for a finding the report does not carry is simply unused.
REMEDY_REACH='<!-- retrospect:remedy_reach begin -->
- finding #1: reach=partial | surface: MEMORY.md entry | unreached: prose proposals emit no tool call | worse_axis: yes
- finding #2: reach=partial | surface: MEMORY.md entry | unreached: prose proposals emit no tool call | worse_axis: yes
<!-- retrospect:remedy_reach end -->'

# A findings row that binds BOTH hard candidates via structured covers: tokens.
ROW_COVER_BOTH="| 1 | behavioral | — | displayed a live secret and bypassed the wrapper | plaintext SM value + direct get-secret-value | rule absent | Yes | memory | ${RATIONALE_5LINE}<br>covers: credential-display<br>covers: sanctioned-path-bypass | HIGH |"
# A findings row with NO covers: token (uncovered hard candidates).
ROW_UNCOVERED="| 1 | behavioral | — | hasty conclusion | did not verify | rule absent | No | memory | ${RATIONALE_5LINE} | MED |"
# A second row that genuinely covers the critic root by naming it.
ROW_COVER_ROOT="| 2 | behavioral | — | direct-sm-read-normalization normalized the bypass | treated direct SM read as acceptable | rule absent | Yes | memory | ${RATIONALE_5LINE} | HIGH |"

build_transcript() {
  local report="$1" tf="$2"
  cat "$FIXTURE" > "$tf"
  mk_assistant "$report" >> "$tf"
}

# --- scenario (a): omit A/D -> Gate-9 block ----------------------------------
A_REPORT="$(build_report "$ROW_UNCOVERED" "")"
build_transcript "$A_REPORT" "$TMP/a.jsonl"
assert "replay_a_omit_hard_candidates_blocks" block "$TMP/a.jsonl"

# --- scenario (b): cover A/D + cover critic root in a row -> pass -------------
B_ROWS="$(printf '%s\n%s' "$ROW_COVER_BOTH" "$ROW_COVER_ROOT")"
B_REPORT="$(build_report "$B_ROWS" "")"
build_transcript "$B_REPORT" "$TMP/b.jsonl"
assert "replay_b_cover_hard_and_root_passes" pass "$TMP/b.jsonl"

# --- scenario (c): cover A/D, critic root unfolded -> Gate-10 block -----------
C_REPORT="$(build_report "$ROW_COVER_BOTH" "")"
build_transcript "$C_REPORT" "$TMP/c.jsonl"
assert "replay_c_critic_root_unfolded_blocks" block "$TMP/c.jsonl"

# --- scenario (d): cover A/D + fold root with reason -> pass ------------------
D_EXTRA='folded: direct-sm-read-normalization because it is the same wrapper-bypass enforcement target as finding 1, not a distinct action'
D_REPORT="$(build_report "$ROW_COVER_BOTH" "$D_EXTRA")"
build_transcript "$D_REPORT" "$TMP/d.jsonl"
assert "replay_d_critic_root_folded_with_reason_passes" pass "$TMP/d.jsonl"

# --- scenario (e): clean control (SL30-shape) -> pass ------------------------
# No hard conduct; the critic ran with 0 roots; label `none`. Guards against the
# replay machinery over-blocking a faithful, correction-free session.
E_REPORT="$(build_report "$ROW_UNCOVERED" "")"
E_TF="$TMP/e.jsonl"
{
  mk_assistant "세션은 스킬을 규정대로 실행했고 마찰이 없었습니다."
  mk_critic_toolresult ""
  mk_assistant "$E_REPORT"
} > "$E_TF"
assert "replay_e_clean_control_passes" pass "$E_TF"

printf -- '--- retrospect-replay-silent-pass: %d pass / %d fail ---\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
