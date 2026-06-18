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
run_anchor_check "stage3 reference keeps broad falsification triggers" "$SKILL_DIR/references/stage3-reporting.md" "prefer this"
run_anchor_check "report template includes hygiene trail" "$SKILL_DIR/references/report-template.md" "Hygiene Scan Trail"
run_anchor_check "report template includes memory-scan evidence block" "$SKILL_DIR/references/report-template.md" "<!-- memory_scan finding #<n>:"
run_anchor_check "stage4 reference exists with memory-hint contract" "$SKILL_DIR/references/stage4-execution.md" "Frontmatter contract"
run_anchor_check "stage4 reference reuses repeat scan step 6" "$SKILL_DIR/references/stage4-execution.md" "Stage 2 step 6's repeat scan results"
run_anchor_check "stage4 reference returns backing_repo misses to step 7" "$SKILL_DIR/references/stage4-execution.md" "re-run Stage 2 step 7"
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
