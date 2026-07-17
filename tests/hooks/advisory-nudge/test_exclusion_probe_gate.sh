#!/bin/bash
# test_exclusion_probe_gate.sh — coverage for hooks/advisory-nudge/exclusion-probe-gate
#
# Enumerates the exclusion-directive x verification-claim surface (issue #807),
# the probe-citation pass, the false-positive exclusions, strict/skip modes,
# and fail-open cases.
#
# Usage: bash tests/hooks/advisory-nudge/test_exclusion_probe_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/exclusion-probe-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# ---------------------------------------------------------------------------
# run_case name expectation tool_name file_path content [ENV=VAL ...]
#   expectation:
#     "advisory" — exit 0, stderr contains [exclusion-probe-gate]
#     "silent"   — exit 0, stderr empty
#     "deny"     — exit 2, stdout contains [exclusion-probe-gate]
# ---------------------------------------------------------------------------
run_case() {
  local name="$1" expectation="$2" tool_name="$3" file_path="$4" content="$5"
  shift 5 || true
  local extra_env=("$@")

  local key="content"
  [ "$tool_name" = "Edit" ] && key="new_string"

  local payload
  payload=$(python3 -c '
import json, sys
d = {"tool_name": sys.argv[1], "tool_input": {"file_path": sys.argv[2], sys.argv[4]: sys.argv[3]}}
print(json.dumps(d))
' "$tool_name" "$file_path" "$content" "$key")

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  printf '%s' "$payload" | env "${extra_env[@]}" "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local marker="[exclusion-probe-gate]"
  local ok=1
  case "$expectation" in
    silent)   [ "$rc" -eq 0 ] || ok=0; [ -z "$err" ] || ok=0 ;;
    advisory) [ "$rc" -eq 0 ] || ok=0; case "$err" in *"$marker"*) ;; *) ok=0 ;; esac ;;
    deny)     [ "$rc" -eq 2 ] || ok=0; case "$out" in *"$marker"*) ;; *) ok=0 ;; esac ;;
    *) echo "FAIL  [$name] unknown expectation: $expectation"; FAIL=$((FAIL+1)); FAILED_NAMES+=("$name"); return ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS+1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>} stdout=${out:-<empty>}"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# ===========================================================================
# === FIRE: exclusion directive ∧ verification claim, no probe cited ===
# ===========================================================================

# Cause instance (issue #807).
run_case "cause: DELIBERATELY EXCLUDED (verified via HMS)" advisory \
  "Write" "/tmp/migration.md" "DELIBERATELY EXCLUDED (verified via HMS -- do NOT add these)"

# --- Axis A directive-imperative variants (EN) x Axis B ---
run_case "A: do NOT add + confirmed via" advisory \
  "Write" "/tmp/a.md" "do NOT add columns X, Y — confirmed via prod query"
run_case "A: do not include + verified with" advisory \
  "Write" "/tmp/a.md" "We do not include the beta rows, verified with the HMS dump"
run_case "A: don't add + checked with" advisory \
  "Write" "/tmp/a.md" "don't add the legacy keys, checked with the source table"
run_case "A: must not add + validated against" advisory \
  "Write" "/tmp/a.md" "these must not add up in the total — validated against ledger"
run_case "A: never re-add + verified by" advisory \
  "Write" "/tmp/a.md" "never re-add the dropped shards, verified by the audit script"

# --- Axis A intentful-declarative variants (EN) x Axis B ---
run_case "A: deliberately excluded + cross-checked with" advisory \
  "Write" "/tmp/a.md" "deliberately excluded the demo tenants, cross-checked with billing"
run_case "A: intentionally omitted + tested against" advisory \
  "Write" "/tmp/a.md" "intentionally omitted the sandbox rows, tested against prod"
run_case "A: excluded on purpose + verified using" advisory \
  "Write" "/tmp/a.md" "these were excluded on purpose, verified using the reconciliation job"

# --- Axis A + B in Korean ---
run_case "KO: 의도적 제외 + 대조 완료" advisory \
  "Edit" "/tmp/plan.md" "이 항목들은 의도적 제외 (HMS로 대조 완료)"
run_case "KO: 추가하지 말 것 + 확인함" advisory \
  "Write" "/tmp/plan.md" "이 키는 추가하지 말 것. 소스에서 확인함."
run_case "KO: 포함하지 않음 + 검증됨" advisory \
  "Write" "/tmp/plan.md" "beta 로우는 포함하지 않음, 프로덕션에서 검증됨"
run_case "KO: 일부러 제외 + 검증 완료" advisory \
  "Write" "/tmp/plan.md" "샌드박스는 일부러 제외했고 검증 완료"

# Edit surface (new_string) fires.
run_case "Edit new_string fires" advisory \
  "Edit" "/tmp/e.md" "DELIBERATELY EXCLUDED — verified by the migration audit"

# Same-block proximity (directive and claim on adjacent lines).
run_case "proximity: adjacent lines fire" advisory \
  "Write" "/tmp/p.md" "The demo tenants were intentionally omitted.
Their absence was confirmed via the prod reconciliation query."

# ===========================================================================
# === PASS: probe cited near directive ===
# ===========================================================================

run_case "probe cited (Step 5g arrow) passes" silent \
  "Write" "/tmp/ok.md" "DELIBERATELY EXCLUDED (verified via HMS)
Probe: hms query 'select count(*)' -> 0 rows"
run_case "probe cited on same line passes" silent \
  "Write" "/tmp/ok.md" "excluded the demo tenants, verified via HMS. Probe: describe hms.demo -> empty"

# ===========================================================================
# === PASS: false-positive surfaces (issue #807 table) ===
# ===========================================================================

# Exclusion directive ALONE (no verification claim) — legitimate.
run_case "FP: API-usage comment (directive alone)" silent \
  "Write" "/tmp/code.py" "# do NOT add retries here — it breaks the backoff invariant"
run_case "FP: rule prohibition (directive alone)" silent \
  "Write" "/tmp/notes.md" "Contributors must not add new global singletons."

# Normative document paths — skipped wholesale.
run_case "FP: CLAUDE.md path skipped" silent \
  "Write" "/tmp/proj/CLAUDE.md" "do NOT add X, confirmed via audit"
run_case "FP: SKILL.md path skipped" silent \
  "Write" "/tmp/proj/SKILL.md" "deliberately excluded Y, verified with grep"
run_case "FP: spec.md path skipped" silent \
  "Write" "/tmp/hooks/foo/spec.md" "intentionally omitted Z, checked with the source"

# Test / fixture paths — skipped.
run_case "FP: tests/ path skipped" silent \
  "Write" "/tmp/repo/tests/test_x.sh" "DELIBERATELY EXCLUDED (verified via HMS)"
run_case "FP: fixtures/ path skipped" silent \
  "Write" "/tmp/repo/fixtures/data.md" "deliberately omitted, confirmed via prod"

# Fenced code / inline code / blockquote — masked, illustrative only.
run_case "FP: fenced code example masked" silent \
  "Write" "/tmp/doc.md" "Example of the bad pattern:
\`\`\`
DELIBERATELY EXCLUDED (verified via HMS)
\`\`\`"
run_case "FP: blockquote example masked" silent \
  "Write" "/tmp/doc.md" "> DELIBERATELY EXCLUDED (verified via HMS)"

# Negated verification claim — not a claim.
run_case "FP: negated claim (not verified)" silent \
  "Write" "/tmp/pr.md" "do not include Y (not verified via the prod path)"
run_case "FP: KO negated claim (검증하지 않음)" silent \
  "Write" "/tmp/pr.md" "beta 로우는 포함하지 않음, 아직 검증하지 않았음"

# "What was NOT verified" PR section — recommended pattern, must pass.
run_case "FP: PR 'not verified' section" silent \
  "Write" "/tmp/pr.md" "## What was NOT verified
- prod-only path not exercised
- do not include the sandbox rows in the count"

# ===========================================================================
# === codex round-1 regressions (P2) ===
# ===========================================================================

# P2-2: negation separated from claim by an auxiliary/adverb must still pass.
run_case "P2-2: 'has not been verified via' (aux between neg and claim)" silent \
  "Write" "/tmp/pr.md" "do not include Y; it has not been verified via the prod path"
run_case "P2-2: 'was never actually confirmed with'" silent \
  "Write" "/tmp/pr.md" "these were deliberately omitted but never actually confirmed with prod"
# ...but a genuinely unnegated claim in the same shape still fires.
run_case "P2-2: unnegated 'was verified via' still fires" advisory \
  "Write" "/tmp/pr.md" "do not include Y; it was verified via the prod path"

# P2-3: multi-backtick inline-code span must be masked (illustrative).
run_case "P2-3: double-backtick inline code masked" silent \
  "Write" "/tmp/doc.md" "See the marker \`\`DELIBERATELY EXCLUDED (verified via HMS)\`\` in old code."

# P2-4: a closing fence shorter than the opening run does not close it.
run_case "P2-4: 4-backtick fence with inner 3-backtick masked" silent \
  "Write" "/tmp/doc.md" "\`\`\`\`
\`\`\`
DELIBERATELY EXCLUDED (verified via HMS)
\`\`\`
\`\`\`\`"

# P2-1: Edit scans the POST-EDIT full file, not new_string alone.
#   A pre-existing directive + an Edit that adds only the claim (adjacent line)
#   must fire; the same Edit on a non-existent file (claim fragment only,
#   directive absent) must stay silent.
P2_DIR=$(mktemp -d)
printf '# notes\nDELIBERATELY EXCLUDED these two tables\n- table_a\n' > "$P2_DIR/notes.md"
printf '%s' "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$P2_DIR/notes.md\",\"old_string\":\"- table_a\",\"new_string\":\"- table_a (this exclusion confirmed via prod)\"}}" \
  | "$HOOK" >/dev/null 2>/tmp/p2e
if grep -q '\[exclusion-probe-gate\]' /tmp/p2e; then
  echo "PASS  [P2-1: Edit fires on directive-in-file + claim-only new_string]"; PASS=$((PASS+1))
else
  echo "FAIL  [P2-1: Edit fires on directive-in-file + claim-only new_string]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("P2-1 post-edit fire")
fi
printf '%s' '{"tool_name":"Edit","tool_input":{"file_path":"/tmp/nonexistent_807_xyz.md","old_string":"a","new_string":"this exclusion confirmed via prod"}}' \
  | "$HOOK" >/dev/null 2>/tmp/p2e2
if [ -s /tmp/p2e2 ]; then
  echo "FAIL  [P2-1: absent directive on new file stays silent]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("P2-1 new-file silent")
else
  echo "PASS  [P2-1: absent directive on new file stays silent]"; PASS=$((PASS+1))
fi
rm -rf "$P2_DIR" /tmp/p2e /tmp/p2e2

# ===========================================================================
# === STRICT / SKIP modes ===
# ===========================================================================

run_case "strict: escalates to deny (exit 2)" deny \
  "Write" "/tmp/m.md" "deliberately omitted these, confirmed with prod query" \
  "PRAXIS_EXCLUSION_PROBE_STRICT=1"
run_case "skip: PRAXIS_EXCLUSION_PROBE_SKIP=1 disables gate" silent \
  "Write" "/tmp/m.md" "DELIBERATELY EXCLUDED verified via HMS" \
  "PRAXIS_EXCLUSION_PROBE_SKIP=1"

# ===========================================================================
# === FAIL-OPEN / non-target ===
# ===========================================================================

run_case "non-target tool (Bash) is silent" silent \
  "Bash" "/tmp/m.md" "DELIBERATELY EXCLUDED verified via HMS"
run_case "empty content is silent" silent \
  "Write" "/tmp/m.md" ""

# malformed JSON stdin
printf 'not-json' | "$HOOK" >/dev/null 2>/dev/null
if [ "$?" -eq 0 ]; then
  echo "PASS  [malformed JSON stdin is silent (exit 0)]"; PASS=$((PASS+1))
else
  echo "FAIL  [malformed JSON stdin is silent (exit 0)]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("malformed JSON stdin")
fi

# Bash: strict env must NOT block a non-target tool (Bash out of scope this PR).
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"gh issue create --body \"DELIBERATELY EXCLUDED verified via HMS\""}}' \
  | env PRAXIS_EXCLUSION_PROBE_STRICT=1 "$HOOK" >/dev/null 2>/dev/null
if [ "$?" -eq 0 ]; then
  echo "PASS  [Bash surface out of scope: not blocked even in strict]"; PASS=$((PASS+1))
else
  echo "FAIL  [Bash surface out of scope: not blocked even in strict]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("Bash out of scope")
fi

# ---------------------------------------------------------------------------
# @fail_open structural assertion
# ---------------------------------------------------------------------------
_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if getattr(mod.main, "__wrapped__", None) is None:
    sys.stderr.write("main not wrapped by @fail_open\n"); sys.exit(1)
PYEOF
)
if [ "$?" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "PASS  [main() wrapped by @fail_open]"; PASS=$((PASS+1))
else
  echo "FAIL  [main() wrapped by @fail_open] out=$(echo "$_uncaught_out" | head -c 200)"
  FAIL=$((FAIL+1)); FAILED_NAMES+=("main() wrapped by @fail_open")
fi

# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
