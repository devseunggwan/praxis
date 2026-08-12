#!/bin/bash
# test_n1_quantitative_claim_advisory.sh — coverage for
# hooks/advisory-nudge/n1-quantitative-claim-advisory/impl.py (issue #949)
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory:<marker>  — exit 0, stderr contains <marker>
#   silent             — exit 0, stderr empty
#
# Usage: bash tests/hooks/advisory-nudge/test_n1_quantitative_claim_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/n1-quantitative-claim-advisory/impl.py"
SPEC="$ROOT_DIR/hooks/advisory-nudge/n1-quantitative-claim-advisory/spec.md"
MARKER="n1-quantitative-claim-advisory"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()
TMPDIR_TEST=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$TMPDIR_TEST"' EXIT

# run_case <name> <expectation> <command> [cwd]
run_case() {
  local name="$1" expectation="$2" command="$3" cwd="${4:-$ROOT_DIR}"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))
' "$command")

  run_payload_case "$name" "$expectation" "$payload" "$cwd"
}

# run_payload_case <name> <expectation> <raw-payload> [cwd]
run_payload_case() {
  local name="$1" expectation="$2" payload="$3" cwd="${4:-$ROOT_DIR}"

  local err_file
  err_file=$(mktemp)
  (cd "$cwd" && printf '%s' "$payload" | python3 "$HOOK" >/dev/null 2>"$err_file")
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    advisory:*)
      local expected_marker="${expectation#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$expected_marker"*) ;;
        *) ok=0 ;;
      esac
      ;;
    *)
      echo "FAIL  [$name] unknown expectation: $expectation"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# === REQUIRED FIXTURES: the three 2026-08-05 instances + the n=15 follow-up
# (issue #949 "Verification plan"). Instance 1 must fire; instances 2 and 3
# must NOT — the coverage ceiling is documented, not papered over.
# ---------------------------------------------------------------------------

I1='### Verification — abc1234 (rev 1)

| # | Claim | Result |
| --- | --- | --- |
| 1 | 목록 엔드포인트 응답 지연이 해소된다 | PASS(실환경) |

실환경 호출 결과 응답 시간 91ms 를 확인했다.'

run_case "I1: instance 1 — PASS row with an n=1 latency figure" \
  "advisory:$MARKER" \
  "gh pr comment 949 --body '$I1'"

I2='### 지연 원인 분석

목록 화면의 체감 지연은 상품 조회 경로의 N+1 질의에서 발생한다.
해당 경로를 수정하면 사용자가 보고한 증상은 사라진다.'

run_case "I2: instance 2 — prose root-cause report (coverage ceiling)" \
  silent \
  "gh issue comment 949 --body '$I2'"

I3='### 위임 브리핑

담당 범위는 상품 조회 경로 한 곳이다. 해당 경로의 수정과 회귀 테스트를
포함해 진행하면 된다. 나머지 화면은 이번 범위에 포함하지 않는다.'

run_case "I3: instance 3 — prose delegation briefing (coverage ceiling)" \
  silent \
  "gh issue comment 949 --body '$I3'"

I4='재측정 결과 n=15 에서 p50 2,920ms → 91ms 로, 두 분포는 겹치지 않는다.'

run_case "I4: n=15 follow-up — sample size stated" \
  silent \
  "gh pr comment 949 --body '$I4'"

# ---------------------------------------------------------------------------
# === SELF-APPLICATION (2x2 factorial).
# This hook's own spec.md documents its trigger vocabulary — percentiles,
# `PASS`, `n=`. S1 asserts it does not self-trip. S2 is the NEGATIVE CONTROL:
# with BOTH documented guards removed, the same text must fire. Without S2,
# S1's silence is indistinguishable from a dead detector — the FP<->FN
# offsetting failure this repo has already paid for once.
# S3 and S4 isolate each guard as independently sufficient.
# ---------------------------------------------------------------------------

# strip_spec <keep-optout:0|1> <keep-sample-sizes:0|1> -> path
# Sample sizes in the spec are all written as `15`; rewriting them to a
# non-numeric token removes every `n>=2` marker in one pass.
strip_spec() {
  local keep_optout="$1" keep_samples="$2"
  local out="$TMPDIR_TEST/spec-$keep_optout-$keep_samples.md"
  python3 - "$SPEC" "$out" "$keep_optout" "$keep_samples" <<'PY'
import sys
src, dst, keep_optout, keep_samples = sys.argv[1:5]
text = open(src, encoding="utf-8").read()
if keep_optout == "0":
    text = text.replace("sample-size-noted", "OPTOUT-MARKER-REMOVED")
if keep_samples == "0":
    text = text.replace("15", "one")
open(dst, "w", encoding="utf-8").write(text)
PY
  printf '%s' "$out"
}

run_case "S1: own spec.md verbatim does not self-trip" \
  silent \
  "gh pr comment 949 --body-file $(strip_spec 1 1)"

run_case "S2: NEGATIVE CONTROL — spec.md with both guards removed fires" \
  "advisory:$MARKER" \
  "gh pr comment 949 --body-file $(strip_spec 0 0)"

run_case "S3: opt-out marker alone is sufficient" \
  silent \
  "gh pr comment 949 --body-file $(strip_spec 1 0)"

run_case "S4: stated sample size alone is sufficient" \
  silent \
  "gh pr comment 949 --body-file $(strip_spec 0 1)"

# ---------------------------------------------------------------------------
# === FORM A: summary statistic + number
# ---------------------------------------------------------------------------

run_case "A1: p50 with a measurement" "advisory:$MARKER" \
  "gh pr comment 1 --body 'p50 91ms 로 개선되었다'"
run_case "A2: P95 uppercase" "advisory:$MARKER" \
  "gh pr comment 1 --body 'P95 320ms 를 기록했다'"
run_case "A3: p99.9 decimal percentile" "advisory:$MARKER" \
  "gh pr comment 1 --body 'p99.9 는 410ms 였다'"
run_case "A4: english median" "advisory:$MARKER" \
  "gh pr comment 1 --body 'median latency was 91ms'"
run_case "A5: korean 평균" "advisory:$MARKER" \
  "gh pr comment 1 --body '평균 응답 91ms 확인'"
run_case "A6: thousands separator in the figure" "advisory:$MARKER" \
  "gh pr comment 1 --body 'p50 2,920ms 로 측정되었다'"

# ---------------------------------------------------------------------------
# === FORM B: verdict token near a measurement
# ---------------------------------------------------------------------------

run_case "B1: PASS(live) with a latency figure" "advisory:$MARKER" \
  "gh pr comment 1 --body '| 1 | 응답 | PASS(live) | 91ms 확인 |'"
run_case "B2: PASS followed by a Korean particle" "advisory:$MARKER" \
  "gh pr comment 1 --body 'PASS를 91ms 로 확인했다'"
run_case "B3: FAIL with a measurement" "advisory:$MARKER" \
  "gh pr comment 1 --body 'FAIL — 응답이 4,100ms 로 회귀했다'"
run_case "B4: english verified" "advisory:$MARKER" \
  "gh pr comment 1 --body 'verified against prod: 91ms'"
run_case "B5: 검증 완료 with a measurement" "advisory:$MARKER" \
  "gh pr comment 1 --body '검증 완료 — 응답 91ms'"
run_case "B6: throughput unit qps" "advisory:$MARKER" \
  "gh pr comment 1 --body 'PASS(live) — 1,200 qps 를 처리했다'"

# ---------------------------------------------------------------------------
# === FORM C: verdict-attached bare count with no run condition.
# Live instance (this session): a count reported as "48건" was actually 43 —
# the number was not wrong so much as unverifiable, because the command, the
# collection scope, and where it ran were all unstated. Any ONE of the three
# fields silences this; the trigger is deliberately the intersection with a
# verdict token, not every count in every body.
# ---------------------------------------------------------------------------

run_case "C1: verdict-attached count with no run condition" "advisory:$MARKER" \
  "gh pr comment 1 --body '검증 완료 — 48건 확인했다'"
run_case "C2: field 1 (cited command) silences" silent \
  "gh pr comment 1 --body '검증 완료 — 48건 (\`PYTHONPATH=./x pytest ./tests\`)'"
run_case "C3: field 2 (collection scope) silences" silent \
  "gh pr comment 1 --body '검증 완료 — 48건, 수집 범위는 CI 와 동일'"
run_case "C4: field 3 (where it ran) silences" silent \
  "gh pr comment 1 --body 'PASS — 41 failed, 로컬 재현'"
run_case "C5: english failure count with no field" "advisory:$MARKER" \
  "gh pr comment 1 --body 'PASS — 41 failed'"
run_case "C6: rows count attached to a verdict" "advisory:$MARKER" \
  "gh pr comment 1 --body 'PASS(live) — 12 rows 반환'"
run_case "C7: count with no verdict token is not a claim" silent \
  "gh pr comment 1 --body '총 48건의 이슈를 정리했다'"
run_case "C8: CI run id is a location field" silent \
  "gh pr comment 1 --body 'PASS — 41 failed (CI run 123)'"

# ---------------------------------------------------------------------------
# === KNOWN MISSES (spec "Known misses"). Two failures produced during this
# hook's own authoring session, both in the family it targets, NEITHER caught.
# Asserted as silent so the coverage ceiling is executable rather than prose:
# if someone later widens the trigger to catch these, these cases fail and the
# noise-budget decision gets re-opened deliberately instead of by accident.
# ---------------------------------------------------------------------------

run_case "K1: absence claim with unstated grep scope (not a count claim)" silent \
  "gh pr comment 1 --body '하드코딩된 카운트 단언은 없다. 검증 완료 — grep 결과 해당 없음.'"
run_case "K2: unitless counts stated as fact (no unit token to anchor on)" silent \
  "gh pr comment 1 --body '검증 완료 — 둘 다 들어가면 48/22/48/43 이 된다.'"

# K3 pins the suppressor-scope leak (spec "Suppressor scope is the whole body").
# Row 1 rests on one reading; row 2's unrelated n=15 silences the whole scan.
# Asserted silent so that narrowing the suppressors to the claim's neighbourhood
# breaks this case and reopens the noise-budget decision deliberately.
K3='| 1 | 목록 응답 | PASS(live) | 91ms (1회 측정) |
| 2 | 배치 처리량 | PASS(live) | 별도 벤치 n=15 |'

run_case "K3: unrelated row's sample size suppresses an n=1 row" silent \
  "gh pr comment 1 --body '$K3'"

# ---------------------------------------------------------------------------
# === REVIEW REGRESSIONS (codex F1, CodeRabbit CR-B) — bind each body flag to
# its own gh invocation. A regex over raw command text can do neither of the
# first two; see the PR anchor for the pre-fix measurement.
# ---------------------------------------------------------------------------

run_case "R1: gh words inside a quoted string post nothing" silent \
  "echo 'gh pr comment 1' --body 'p50 91ms'"
run_case "R2: second chained invocation is scanned on its own body" "advisory:$MARKER" \
  "gh pr comment 1 --body 'n=15 p50 91ms' && gh pr comment 2 --body 'PASS(live) 91ms'"
run_case "R3: global flag between gh and its subcommand" "advisory:$MARKER" \
  "gh --repo owner/name pr comment 1 --body 'p50 91ms'"
run_case "R4: path-invoked gh binary" "advisory:$MARKER" \
  "/usr/bin/gh pr comment 1 --body 'p50 91ms'"

# F2 — Korean particles attach directly to the unit.
run_case "R5: count unit followed by a particle" "advisory:$MARKER" \
  "gh pr comment 1 --body '검증 완료 — 48건을 확인했다'"
run_case "R6: measurement unit followed by a particle" "advisory:$MARKER" \
  "gh pr comment 1 --body 'PASS — 91초로 완료됐다'"
run_case "R7: particle guard does not resurrect the msg false positive" silent \
  "gh pr comment 1 --body 'PASS(live) — 2920msg 를 큐에 넣었다'"

# ---------------------------------------------------------------------------
# === FALSE-POSITIVE GUARDS (word boundary, Unicode, noise vocabulary)
# ---------------------------------------------------------------------------

run_case "FP1: bare PASS row with no measurement" silent \
  "gh pr comment 1 --body '| 1 | 라우팅 동작 | PASS(live) |'"
run_case "FP2: Codex [P1] severity tag is not a percentile" silent \
  "gh pr comment 1 --body '[P1] 파서 경계 문제 — 3개 파일에서 확인'"
run_case "FP3: bypass/password do not match PASS" silent \
  "gh pr comment 1 --body 'bypass 토큰과 password 를 12s 안에 회전한다'"
run_case "FP4: bare 검증 is ordinary prose" silent \
  "gh pr comment 1 --body '검증 앵커를 5s 안에 작성한다'"
# The body carries no count: `3건을` is now a legitimate Form C trigger
# (verdict-attached count, no run condition), which would mask what this case
# actually asserts — that `top50` is not read as a percentile.
run_case "FP5: top50 is not a percentile" silent \
  "gh pr comment 1 --body 'top50 그룹을 PASS 처리했다'"
run_case "FP6: minute-scale build duration" silent \
  "gh pr comment 1 --body '전체 스위트 12분 소요, PASS(live)'"
run_case "FP7: 2920msg is not a measurement" silent \
  "gh pr comment 1 --body 'PASS(live) — 2920msg 를 큐에 넣었다'"
run_case "FP8: multiplier notation is the perf hook's territory" silent \
  "gh pr comment 1 --body '3.06x 개선되었다'"

# ---------------------------------------------------------------------------
# === SAMPLE-SIZE PASS FORMS (condition 2)
# ---------------------------------------------------------------------------

run_case "N1: n=15 notation" silent \
  "gh pr comment 1 --body 'n=15, p50 91ms'"
run_case "N2: N = 15 with spaces" silent \
  "gh pr comment 1 --body 'N = 15 반복, median 91ms'"
run_case "N3: korean 15회 측정" silent \
  "gh pr comment 1 --body '15회 측정 결과 평균 91ms'"
run_case "N4: korean 표본 크기" silent \
  "gh pr comment 1 --body '표본 크기 15, PASS(live) 91ms'"
run_case "N5: english 15 runs" silent \
  "gh pr comment 1 --body 'across 15 runs, median 91ms'"
run_case "N6: explicit n=1 still fires" "advisory:$MARKER" \
  "gh pr comment 1 --body 'n=1 표본이지만 PASS(live) 91ms'"
run_case "N7: min=15 is not a sample size" "advisory:$MARKER" \
  "gh pr comment 1 --body 'min=15 설정에서 PASS(live) 91ms'"
run_case "N8: opt-out marker silences" silent \
  "gh pr comment 1 --body 'p50 91ms (sample-size-noted: 단일 관측 의도)'"

# ---------------------------------------------------------------------------
# === SURFACE / FLAG FORMS
# ---------------------------------------------------------------------------

run_case "F1: -b short flag" "advisory:$MARKER" \
  "gh issue comment 1 -b 'p50 91ms'"
run_case "F2: --body= inline form" "advisory:$MARKER" \
  "gh pr create --title x --body='p50 91ms'"
run_case "F3: gh pr view is not a deliverable write" silent \
  "gh pr view 1 --json body"
run_case "F4: gh issue list is not a deliverable write" silent \
  "gh issue list --search 'p50 91ms'"
run_case "F5: fires inside a compound command" "advisory:$MARKER" \
  "git status && gh pr comment 1 --body 'p50 91ms'"
run_case "F6: non-gh command" silent \
  "echo 'p50 91ms PASS(live)'"

# ---------------------------------------------------------------------------
# === FAIL-OPEN / EDGE
# ---------------------------------------------------------------------------

run_payload_case "E1: malformed JSON stdin" silent "not json at all"
run_payload_case "E2: non-Bash tool name" silent \
  '{"tool_name":"Write","tool_input":{"command":"gh pr comment 1 --body \"p50 91ms\""}}'
run_payload_case "E3: missing command field" silent \
  '{"tool_name":"Bash","tool_input":{}}'
run_payload_case "E4: empty command" silent \
  '{"tool_name":"Bash","tool_input":{"command":"   "}}'
run_payload_case "E5: payload is a JSON list" silent '[]'
run_case "E6: unbalanced quote (shlex failure) fails open" silent \
  "gh pr comment 1 --body 'p50 91ms"
run_case "E7: --body-file target missing on disk" silent \
  "gh pr comment 1 --body-file $TMPDIR_TEST/does-not-exist.md"
run_case "E8: gh deliverable with no body flag" silent \
  "gh pr create --fill"

# ---------------------------------------------------------------------------

echo
echo "----------------------------------------"
echo "PASS: $PASS  FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
