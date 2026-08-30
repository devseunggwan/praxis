#!/bin/bash
# tests/test_retrospect_routing.sh — assert retrospect skill docs are repo-agnostic
#
# Regression test for the routing rule: user-specific GitHub org/repo names
# MUST NOT appear in the retrospect skill. The skill is distributed across
# users; any hardcoded name misroutes tool-friction issues for users on
# different fork/company environments.
#
# Run:  ./tests/test_retrospect_routing.sh
# Exit: 0 on success, 1 on first failure (after summary).

set +e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_DIR="$REPO_ROOT/skills/retrospect"
SKILL="$SKILL_DIR/SKILL.md"

if [ ! -f "$SKILL" ]; then
  echo "FAIL: SKILL.md not found at $SKILL" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# Forbidden patterns — any match is a regression toward user-specific
# hardcoding. Extend via PRAXIS_RETROSPECT_FORBIDDEN_PATTERNS env var
# (newline-separated) when bringing this test into a downstream fork that
# has additional org/handle names to guard against.
FORBIDDEN_PATTERNS=(
  "devseunggwan/"
  "Yeachan-Heo/"
  "ai-dotfiles"
)
if [ -n "${PRAXIS_RETROSPECT_FORBIDDEN_PATTERNS:-}" ]; then
  while IFS= read -r extra; do
    [ -n "$extra" ] && FORBIDDEN_PATTERNS+=("$extra")
  done <<< "$PRAXIS_RETROSPECT_FORBIDDEN_PATTERNS"
fi

run_forbidden_check() {
  local name="$1"
  local pattern="$2"
  local hits
  hits=$(grep -R -c -- "$pattern" "$SKILL_DIR" 2>/dev/null | awk -F: '{sum += $2} END {print sum + 0}')
  hits=${hits:-0}
  if [ "$hits" -eq 0 ]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name — found $hits occurrence(s) of '$pattern':"
    grep -R -n -- "$pattern" "$SKILL_DIR" | sed 's/^/    /'
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# Required placeholder presence — ensure routing uses placeholder convention
# rather than concrete repo names.
run_placeholder_check() {
  local name="$1"
  local placeholder="$2"
  local hits
  hits=$(grep -R -c -- "$placeholder" "$SKILL_DIR" 2>/dev/null | awk -F: '{sum += $2} END {print sum + 0}')
  hits=${hits:-0}
  if [ "$hits" -gt 0 ]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name — placeholder '$placeholder' missing in retrospect docs"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# Section-scoped anchor check: the pattern must appear inside the block that
# starts at $start_re and ends at the line matching $end_re (exclusive). A
# plain document-wide grep cannot tell "Action 2 routes through the gate" from
# "the gate text exists somewhere else in the file" — issue #1138 was exactly
# that shape (gate present, `issue` action not wired to it).
run_section_check() {
  local name="$1"
  local path="$2"
  local start_re="$3"
  local end_re="$4"
  local pattern="$5"
  local section
  if [ ! -f "$path" ]; then
    echo "FAIL: $name — missing file $path"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    return
  fi
  # The section bounds are passed through the environment, not `awk -v`.
  # `-v` runs escape processing over the value, so gawk turns `^2\. \*\*GitHub`
  # into `^2. **GitHub` (warning: "escape sequence `\*' treated as plain `*'")
  # and the `**` is then a broken quantifier that matches nothing — the section
  # comes back empty and every check against it fails. mawk keeps the
  # backslashes, so this splits by awk implementation: mawk locally, gawk on the
  # CI runner. ENVIRON values are not escape-processed, so both agree.
  # Both bounds are reported, not just the text between them. Without the end
  # marker the helper scans to EOF when the end heading is renamed or removed,
  # and a pattern living in a LATER section then satisfies a check scoped to
  # this one — the same silent over-match the `awk -v` bug produced, arriving
  # by a different route.
  section=$(
    PRAXIS_SECTION_START="$start_re" PRAXIS_SECTION_END="$end_re" awk '
      BEGIN { s = ENVIRON["PRAXIS_SECTION_START"]; e = ENVIRON["PRAXIS_SECTION_END"] }
      !inside && !seen_start && $0 ~ s { inside = 1; seen_start = 1; print; next }
      inside && $0 ~ e { inside = 0; seen_end = 1 }
      inside { print }
      END { print "###BOUNDS### " (seen_start ? 1 : 0) " " (seen_end ? 1 : 0) }
    ' "$path"
  )
  local bounds
  bounds=$(printf '%s\n' "$section" | sed -n 's/^###BOUNDS### //p' | tail -1)
  section=$(printf '%s\n' "$section" | grep -v '^###BOUNDS### ')
  if [ "${bounds% *}" != "1" ]; then
    echo "FAIL: $name — section start '$start_re' never matched in $path"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi
  if [ "${bounds#* }" != "1" ]; then
    echo "FAIL: $name — section end '$end_re' never matched in $path; the scan ran to EOF, so a later section could satisfy this check"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi
  # An empty section means the start bound never matched. Without this arm the
  # helper reports "pattern missing", which reads as a defect in the document
  # under test rather than in the extraction — the exact misdiagnosis this
  # helper produced on its first CI run.
  if [ -z "$section" ]; then
    echo "FAIL: $name — section '$start_re' extracted nothing from $path (bounds did not match)"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    return
  fi
  if printf '%s\n' "$section" | grep -q -- "$pattern"; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name — pattern '$pattern' missing from the $start_re section of $path"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

run_anchor_check() {
  local name="$1"
  local path="$2"
  local pattern="$3"
  if [ ! -f "$path" ]; then
    echo "FAIL: $name — missing file $path"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    return
  fi
  if grep -q -- "$pattern" "$path" 2>/dev/null; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name — pattern '$pattern' missing in $path"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

echo "=== retrospect docs repo-agnosticism checks ==="
echo ""

for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
  run_forbidden_check "no hardcoded '$pattern'" "$pattern"
done

echo ""
run_placeholder_check "uses <resolved_backing_repo> placeholder" "<resolved_backing_repo>"
run_placeholder_check "uses <resolved-praxis-repo> placeholder" "<resolved-praxis-repo>"

echo ""
echo "=== retrospect reference split smoke checks ==="
run_anchor_check "stage1-2 reference exists with cursor write mandate" "$SKILL_DIR/references/stage1-2-analysis.md" "Cursor write mandate"
run_anchor_check "stage1-2 reference keeps CLAUDE_CONFIG_DIR fallback" "$SKILL_DIR/references/stage1-2-analysis.md" '${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md'
run_anchor_check "stage1-2 reference keeps timestamp concurrency discriminator" "$SKILL_DIR/references/stage1-2-analysis.md" "write-time timestamp is the primary concurrent-advance discriminator"
run_anchor_check "stage1-2 reference keeps size threshold env defaults" "$SKILL_DIR/references/stage1-2-analysis.md" "PRAXIS_RETROSPECT_INDEX_LINE_THRESHOLD"
run_anchor_check "stage1-2 reference keeps size line cap default" "$SKILL_DIR/references/stage1-2-analysis.md" "PRAXIS_RETROSPECT_INDEX_LINE_CAP"
run_anchor_check "stage1-2 reference keeps user-correction markers" "$SKILL_DIR/references/stage1-2-analysis.md" "that's not what I asked"
run_anchor_check "stage1-2 reference keeps mixed regex warning" "$SKILL_DIR/references/stage1-2-analysis.md" "explicit ASCII guards"
run_anchor_check "stage1-2 reference keeps pre-scan scope window" "$SKILL_DIR/references/stage1-2-analysis.md" "last 50 turns"
run_anchor_check "stage1-2 reference keeps retry_count polling exclusion" "$SKILL_DIR/references/stage1-2-analysis.md" "background-output re-reads"
run_anchor_check "stage1-2 reference keeps user-correction cap priority" "$SKILL_DIR/references/stage1-2-analysis.md" "genuine \`user_correction\` events rank first"
run_anchor_check "stage1-2 reference keeps self-correction criteria" "$SKILL_DIR/references/stage1-2-analysis.md" "prior result was wrong or superseded"
run_anchor_check "stage1-2 reference keeps self-correction cap priority" "$SKILL_DIR/references/stage1-2-analysis.md" "genuine self-corrections rank after"
run_anchor_check "stage1-2 reference keeps externalized critic tier" "$SKILL_DIR/references/stage1-2-analysis.md" "Externalized critic re-scan tier"
run_anchor_check "stage1-2 reference keeps critic opt-in env" "$SKILL_DIR/references/stage1-2-analysis.md" "PRAXIS_RETROSPECT_CRITIC=1"
run_anchor_check "stage1-2 reference keeps critic not-suppressed disposition" "$SKILL_DIR/references/stage1-2-analysis.md" "not-suppressed: <reason>"
run_anchor_check "stage1-2 reference keeps action assignment ladder" "$SKILL_DIR/references/stage1-2-analysis.md" "Action assignment (step 7)"
run_anchor_check "stage1-2 reference keeps hygiene action mapping" "$SKILL_DIR/references/stage1-2-analysis.md" "stale reference -> \`memory\`"
run_anchor_check "stage1-2 reference keeps bounded-drop cycle rule" "$SKILL_DIR/references/stage1-2-analysis.md" "PRAXIS_RETROSPECT_UNRUNNABLE_DROP_CYCLES"
run_anchor_check "stage1-2 reference keeps census retry promotion threshold" "$SKILL_DIR/references/stage1-2-analysis.md" "PRAXIS_RETROSPECT_CENSUS_RETRY_THRESHOLD"
run_anchor_check "stage1-2 reference keeps access-blocked retention" "$SKILL_DIR/references/stage1-2-analysis.md" "access-blocked-only findings are never auto-dropped"
run_anchor_check "stage1-2 reference keeps cluster repeat propagation" "$SKILL_DIR/references/stage1-2-analysis.md" "cluster_repeat_count = max(repeat_count) + (cluster_event_count - 1)"
run_anchor_check "stage1-2 reference keeps link scanner wikilink support" "$SKILL_DIR/references/stage1-2-analysis.md" "[[wikilink]]"
run_anchor_check "stage1-2 reference keeps link scanner bare basename support" "$SKILL_DIR/references/stage1-2-analysis.md" "Bare basename mention"
run_anchor_check "stage1-2 reference keeps memory-scan fallback passable" "$SKILL_DIR/references/stage1-2-analysis.md" "memory_scan.scanned=true"
run_anchor_check "stage1-2 reference keeps backing_repo declaration rule" "$SKILL_DIR/references/stage1-2-analysis.md" "backing_repo: <owner>/<repo>"
run_anchor_check "stage1-2 reference keeps tool-friction step 4b" "$SKILL_DIR/references/stage1-2-analysis.md" "Tool-friction promotion pass"
run_anchor_check "stage1-2 reference keeps pre-tracer artifact probe" "$SKILL_DIR/references/stage1-2-analysis.md" "probed artifact:"
run_anchor_check "stage1-2 reference keeps PR mergeability audit" "$SKILL_DIR/references/stage1-2-analysis.md" "Sub-audit 1: PR mergeability"
run_anchor_check "stage1-2 reference keeps external-comment audit" "$SKILL_DIR/references/stage1-2-analysis.md" "external-comment evidence"
run_anchor_check "stage1-2 reference keeps audit action templates" "$SKILL_DIR/references/stage1-2-analysis.md" "PR mergeability -> \`memory\`"
run_anchor_check "stage1-2 reference keeps post-compaction transcript enumeration" "$SKILL_DIR/references/stage1-2-analysis.md" "is_error_count"
run_anchor_check "stage1-2 reference keeps skipped transcript marker" "$SKILL_DIR/references/stage1-2-analysis.md" "retrospect:transcript_receipt_skipped: transcript unreachable"
run_anchor_check "stage2.5 reference exists with Gate-1" "$SKILL_DIR/references/stage2.5-audit.md" "Gate-1"
run_anchor_check "stage2.5 reference blocks non-behavioral memory-only" "$SKILL_DIR/references/stage2.5-audit.md" "Proposed Actions = memory"
run_anchor_check "stage2.5 reference keeps all-findings behavioral safeguard" "$SKILL_DIR/references/stage2.5-audit.md" "Behavioral-only safeguard (all findings)"
run_anchor_check "stage2.5 reference keeps behavioral label falsification" "$SKILL_DIR/references/stage2.5-audit.md" "behavioral-label-justify"
run_anchor_check "stage2.5 reference points Gate-3 repair to step 7" "$SKILL_DIR/references/stage2.5-audit.md" "Stage 2 step 7"
run_anchor_check "stage2.5 reference keeps Gate-3 downgrade table" "$SKILL_DIR/references/stage2.5-audit.md" "single-observation downgrade"
run_anchor_check "stage2.5 reference keeps override prompt body" "$SKILL_DIR/references/stage2.5-audit.md" "rationale 직접 입력"
run_anchor_check "stage2.5 reference keeps Gate-6 override prompt body" "$SKILL_DIR/references/stage2.5-audit.md" "같은 oracle로 재측정"
run_anchor_check "stage2.5 reference keeps own-org allowlist resolution" "$SKILL_DIR/references/stage2.5-audit.md" "PRAXIS_OWN_ORGS"
run_anchor_check "stage2.5 reference keeps external WARN verdict" "$SKILL_DIR/references/stage2.5-audit.md" "at least one external finding exists"
run_anchor_check "stage2.5 reference keeps same-oracle producer" "$SKILL_DIR/references/stage2.5-audit.md" "same matching basis / cohort / unit"
run_anchor_check "stage3 reference exists with distribution fence" "$SKILL_DIR/references/stage3-reporting.md" "retrospect:distribution begin"
run_anchor_check "stage3 reference keeps hygiene trail slot" "$SKILL_DIR/references/stage3-reporting.md" "Stage 1.5 Hygiene Scan Trail"
run_anchor_check "stage3 reference keeps Stage 2.7 audit skipped slot" "$SKILL_DIR/references/stage3-reporting.md" "retrospect:audit_skipped: no artifacts"
run_anchor_check "stage3 reference counts success-pattern memory" "$SKILL_DIR/references/stage3-reporting.md" "successful_patterns\` rows whose"
run_anchor_check "stage3 reference gates success-pattern memory approval" "$SKILL_DIR/references/stage3-reporting.md" "explicitly approved in Stage 3"
run_anchor_check "stage3 reference keeps memory-scan evidence block" "$SKILL_DIR/references/stage3-reporting.md" "memory_scan finding #<n>"
run_anchor_check "stage3 reference keeps memory-scan HTML comment" "$SKILL_DIR/references/stage3-reporting.md" "<!-- memory_scan finding #<n>:"
run_anchor_check "stage3 reference scopes backing_repo to routed actions" "$SKILL_DIR/references/stage3-reporting.md" "any row with \`upstream_feedback\` or \`issue\`"
run_anchor_check "stage3 reference keeps critic_diff ledger line" "$SKILL_DIR/references/stage3-reporting.md" "critic_diff:"
run_anchor_check "stage3 reference keeps critic skip reason" "$SKILL_DIR/references/stage3-reporting.md" "tier predicate false"
run_anchor_check "stage3 reference keeps broad falsification triggers" "$SKILL_DIR/references/stage3-reporting.md" "prefer this"
run_anchor_check "report template includes hygiene trail" "$SKILL_DIR/references/report-template.md" "Hygiene Scan Trail"
run_anchor_check "report template includes memory-scan evidence block" "$SKILL_DIR/references/report-template.md" "<!-- memory_scan finding #<n>:"
run_anchor_check "report template includes suppression ledger" "$SKILL_DIR/references/report-template.md" "retrospect:suppression_ledger begin"
run_anchor_check "report template includes critic_diff ledger line" "$SKILL_DIR/references/report-template.md" "critic_diff:"
run_anchor_check "stage4 reference exists with memory-hint contract" "$SKILL_DIR/references/stage4-execution.md" "Frontmatter contract"
run_anchor_check "stage4 reference reuses repeat scan step 6" "$SKILL_DIR/references/stage4-execution.md" "Stage 2 step 6's repeat scan results"
run_anchor_check "stage4 reference returns backing_repo misses to step 7" "$SKILL_DIR/references/stage4-execution.md" "re-run Stage 2 step 7"

# ---------------------------------------------------------------------------
# NOTE: run_anchor_check / run_forbidden_check grep with a BASIC regex, so the
# patterns below deliberately avoid `[`, `]` and `*` — a literal `[a]` in the
# document is a bracket expression to grep and would never match.
#
# Step 0's success branch must land in Step 0a. Routing it into the action's
# own bullets is what let an `⚠ EXTERNAL`-marked issue row reach
# `gh issue create` with no per-action approval (code review of PR #1144).
# ---------------------------------------------------------------------------

run_anchor_check "stage4 Step 0 success branch routes into Step 0a (#1144)" \
  "$SKILL_DIR/references/stage4-execution.md" \
  ", never straight to the action's own procedure"

run_forbidden_check "stage4 Step 0 no longer routes past the gate (#1144)" \
  "proceed to the rest of the gated action's procedure"

run_anchor_check "stage4 Step 0a logs the [a] approval (#1144)" \
  "$SKILL_DIR/references/stage4-execution.md" \
  "approved <verified_backing_repo> for finding #N"

run_anchor_check "stage4 upstream_feedback row also requires the approval log (#1144)" \
  "$SKILL_DIR/references/stage4-execution.md" \
  "from step 0 + if Step 0a fired"

run_section_check "stage4 Action 2 drafts before running the gate (#1144)" \
  "$SKILL_DIR/references/stage4-execution.md" \
  "^2\. \*\*GitHub issue" "^3\. \*\*" \
  "draft the title and body FIRST, then run the gate"

# The global anchor for `{gated_action} = issue` is satisfied by the shared gate
# section itself, so it cannot see Action 2 calling the gate with the WRONG
# action. Scope each binding to the action that owns it.
#
# Action 4's start bound is `^4\. \*\*Upstream`, not `^4\. \*\*`: the shared
# gate's own step 4 ("Divergence / ambiguity handling") also opens with
# `4. **`, and the helper takes the first start match.
run_section_check "stage4 Action 2 binds the gate to the issue action (#1144)" \
  "$SKILL_DIR/references/stage4-execution.md" \
  "^2\. \*\*GitHub issue" "^3\. \*\*" \
  "{gated_action} = issue"

run_section_check "stage4 Action 4 binds the gate to upstream_feedback (#1144)" \
  "$SKILL_DIR/references/stage4-execution.md" \
  "^4\. \*\*Upstream" "^5\. \*\*" \
  "{gated_action} = upstream_feedback"

# The verified value, not the declared one, is what `--repo` targets, so it is
# what the approval prompt must name (CodeRabbit CWE-863 on this PR).
run_anchor_check "stage4 Step 0a prompt names the verified repo (#1144)" \
  "$SKILL_DIR/references/stage4-execution.md" \
  "승인 — {verified_backing_repo}에 이슈 생성 진행"

run_forbidden_check "stage4 Step 0a prompt no longer names the declared repo (#1144)" \
  "승인 — {backing_repo}에 이슈 생성 진행"

# The marker is computed from the DECLARED repo, so a Step 0 divergence leaves
# it stale; the verified repo's visibility is an independent trigger.
run_anchor_check "stage4 Step 0a rechecks visibility of the verified repo (#1144)" \
  "$SKILL_DIR/references/stage4-execution.md" \
  "gh repo view <verified_backing_repo> --json visibility"

run_anchor_check "stage4 Step 0a fails closed on an unanswerable visibility query (#1144)" \
  "$SKILL_DIR/references/stage4-execution.md" \
  "an unanswerable question is not permission"

run_section_check "stage4 Action 2 routes hub-mediated orgs to the hub skill (#1144)" \
  "$SKILL_DIR/references/stage4-execution.md" \
  "^2\. \*\*GitHub issue" "^3\. \*\*" \
  "block-child-repo-issue-create"

run_anchor_check "stage2 step 7 can resolve the working project repo (#1144)" \
  "$SKILL_DIR/references/stage1-2-analysis.md" \
  "The working project repo itself"
run_anchor_check "stage4 reference declares memory-lint CI split intended (issue #975)" "$SKILL_DIR/references/stage4-execution.md" "the intended design, not a residual gap"
run_anchor_check "stage4 reference shares one cross-boundary write gate (issue #1138)" "$SKILL_DIR/references/stage4-execution.md" "## Cross-boundary write gate (shared by Action 2 and Action 4)"
run_anchor_check "stage4 Step 0 covers issue rows (issue #1138)" "$SKILL_DIR/references/stage4-execution.md" "first procedure step for every \`issue\` row and every \`upstream_feedback\` row"
run_anchor_check "stage4 Step 0a covers issue rows (issue #1138)" "$SKILL_DIR/references/stage4-execution.md" "This gate fires for every \`issue\` row and every \`upstream_feedback\` row"
run_anchor_check "stage4 gate skip prompts name the gated action (issue #1138)" "$SKILL_DIR/references/stage4-execution.md" "{gated_action} 액션 제거"
run_anchor_check "stage4 binds the gate to the issue action (issue #1138)" "$SKILL_DIR/references/stage4-execution.md" "{gated_action} = issue"
run_anchor_check "stage4 binds the gate to the upstream_feedback action (issue #1138)" "$SKILL_DIR/references/stage4-execution.md" "{gated_action} = upstream_feedback"
run_section_check "stage4 Action 2 routes through the shared gate (issue #1138)" "$SKILL_DIR/references/stage4-execution.md" "^2\\. \\*\\*GitHub issue" "^3\\. \\*\\*" "Cross-boundary write gate"
run_section_check "stage4 Action 2 pins gh issue create to --repo (issue #1138)" "$SKILL_DIR/references/stage4-execution.md" "^2\\. \\*\\*GitHub issue" "^3\\. \\*\\*" "gh issue create --repo <verified_backing_repo>"
run_forbidden_check "stage4 gate prompts no longer hardcode the upstream_feedback action (issue #1138)" "upstream_feedback 액션 제거"
run_anchor_check "SKILL.md action map wires the issue action to the gate (issue #1138)" "$SKILL" "| 2 | GitHub issue | cross-boundary write gate"
run_forbidden_check "stage4 no longer frames the memory-lint path as an open gap (issue #975)" "still incomplete"
run_anchor_check "appendices reference exists with Red Flags" "$SKILL_DIR/references/appendices.md" "Red Flags"

echo ""
echo "=== summary ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi

exit 0
