#!/bin/bash
# test_perf_multiplier_evidence_advisory.sh — coverage for
# hooks/advisory-nudge/perf-multiplier-evidence-advisory/impl.py (issue #850)
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory:<marker>  — exit 0, stderr contains <marker>
#   silent              — exit 0, stderr empty
#
# Usage: bash tests/hooks/advisory-nudge/test_perf_multiplier_evidence_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/perf-multiplier-evidence-advisory/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case <name> <expectation> <command> [cwd]
#   expectation:
#     "advisory:<marker>" — exit 0, stderr contains <marker>
#     "silent"            — exit 0, stderr empty
run_case() {
  local name="$1" expectation="$2" command="$3" cwd="${4:-$ROOT_DIR}"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))
' "$command")

  local err_file
  err_file=$(mktemp)
  (cd "$cwd" && echo "$payload" | python3 "$HOOK" >/dev/null 2>"$err_file")
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
      local marker="${expectation#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$marker"*) ;;
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
# === CORE: multiplier/lever with no timing artifact -> advisory ===
# ---------------------------------------------------------------------------

run_case "3x multiplier, no timing artifact" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh issue create --title "perf" --body "dim encoding gives a 3x speedup"'

run_case "decimal multiplier with x, no timing artifact" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh issue comment 123 --body "measured lever: 3.06x improvement expected"'

run_case "Korean 배 multiplier, no timing artifact" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh issue create --title "perf" --body "dim 인코딩 적용 시 1.76배 개선"'

run_case "timing pair A -> B, no timing artifact" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh pr create --title "perf fix" --body "ordercycle precompute: 49m -> 12m"'

run_case "from-to prose timing pair" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh pr comment 45 --body "runtime improves from 49m to 12m after the change"'

run_case "percent with direction word, no timing artifact" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh issue create --title "perf" --body "grid change is 73% faster in theory"'

run_case "lever verdict token (채택), no timing artifact" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh issue comment 9 --body "NOT MATERIALIZED lever 채택 제안"'

run_case "lever verdict token (역효과), no timing artifact" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh issue create --title "perf" --body "이 lever 는 역효과로 보인다"'

run_case "-b short flag, inline value" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh issue create --title "perf" -b "3x speedup expected"'

run_case "--body= inline equals form" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh issue comment 1 --body="3x speedup expected"'

# ---------------------------------------------------------------------------
# === SILENT: adjacent controlled-timing artifact ===
# ---------------------------------------------------------------------------

run_case "3x multiplier WITH cited command->output line is silent" \
  silent \
  'gh issue create --title "perf" --body "dim encoding: 3x speedup. $ hyperfine ./bench -> wall-clock: 12.4s"'

run_case "timing pair WITH wall-clock marker is silent" \
  silent \
  'gh pr create --title "perf" --body "49m -> 12m; wall-clock: measured via DuckDB CLI"'

run_case "lever verdict WITH elapsed marker is silent" \
  silent \
  'gh issue comment 9 --body "lever 채택; elapsed 12.4s measured"'

run_case "multiplier WITH real Nm marker is silent" \
  silent \
  'gh pr create --title "perf" --body "3x faster; real 0m12.400s"'

# ---------------------------------------------------------------------------
# === SILENT: no multiplier / lever token at all ===
# ---------------------------------------------------------------------------

run_case "ordinary body with no perf claim is silent" \
  silent \
  'gh issue create --title "docs" --body "update the README with new install steps"'

run_case "bare percent with no direction word is silent" \
  silent \
  'gh issue comment 3 --body "disk usage sits at 73% currently"'

run_case "lever word absent, only generic prose is silent" \
  silent \
  'gh issue create --title "note" --body "this change touches the grid config"'

# ---------------------------------------------------------------------------
# === SILENT: not a deliverable-write gh invocation ===
# ---------------------------------------------------------------------------

run_case "gh issue list is silent (not create/comment)" \
  silent \
  'gh issue list --search "perf 3x"'

run_case "gh pr view is silent (not create/comment)" \
  silent \
  'gh pr view 42'

run_case "non-gh command with 3x is silent" \
  silent \
  'echo "this run was 3x faster"'

# ---------------------------------------------------------------------------
# === --body-file handling ===
# ---------------------------------------------------------------------------

BODY_FILE_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
echo '3x speedup expected, no measurement yet' > "$BODY_FILE_DIR/body.md"
run_case "--body-file relative, unreadable timing -> advisory" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh issue create --title "perf" --body-file body.md' \
  "$BODY_FILE_DIR"

echo '3x speedup; $ hyperfine ./bench -> wall-clock: 5.1s' > "$BODY_FILE_DIR/body2.md"
run_case "--body-file with timing artifact inside is silent" \
  silent \
  'gh issue create --title "perf" --body-file body2.md' \
  "$BODY_FILE_DIR"

run_case "--body-file missing target is silent (no body text extractable)" \
  silent \
  'gh issue create --title "perf" --body-file does-not-exist.md' \
  "$BODY_FILE_DIR"

rm -rf "$BODY_FILE_DIR"

# ---------------------------------------------------------------------------
# === CHAINED INVOCATIONS (issue #973) — each body is bound to its OWN gh call.
# The pre-fix shape read only the first --body in the flat argv and matched the
# trigger as a regex over the whole command string; neither R1..R4 nor R7 can be
# answered that way. See the PR anchor for the pre-fix measurement.
# ---------------------------------------------------------------------------

# R1 is the issue's exact scenario: evidence in body 1 must NOT silence the
# bare claim in body 2 — a timing artifact posted by one invocation measures
# nothing about a multiplier posted by another.
run_case "R1: evidence in body 1 does not silence a claim in body 2 (&&)" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh issue comment 1 --body "wall-clock: elapsed 12.0s baseline" && gh pr comment 2 --body "so the new path is 5x faster"'

run_case "R2: same chain with a ';' separator" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh issue comment 1 --body "elapsed 12.0s" ; gh pr create --title t --body "5x faster"'

# R3: a non-gh command's --body used to be donated to the gh scan, hiding the
# real claim behind curl's harmless body.
run_case "R3: a non-gh --body ahead of the gh claim is not the gh body" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'curl -X POST --body "harmless" https://x && gh issue create --title t --body "5x faster"'

# R4: the false POSITIVE in the same root cause — there is no deliverable here
# at all, only the words inside a quoted echo argument.
run_case "R4: 'gh issue create' inside quoted text posts nothing" \
  silent \
  'echo "run gh issue create later" > /tmp/note.txt && printf %s --body "5x faster"'

# R5/R6 pin the control direction: a chain must stay silent when EVERY body
# carries its own timing artifact, so the per-body loop cannot be "fixed" by
# firing on any chain that contains a multiplier anywhere.
run_case "R5: both chained bodies carry their own timing artifact is silent" \
  silent \
  'gh issue comment 1 --body "3x faster; elapsed 12.0s" && gh pr comment 2 --body "49m -> 12m; wall-clock: measured"'

run_case "R6: unrelated command chained before a silenced claim stays silent" \
  silent \
  'git status && gh pr comment 1 --body "3x faster; real 0m12.400s"'

# R7 pins the newline path. Bash separates commands on a newline, but
# shlex.split consumes it as whitespace — without the unquoted-newline rewrite
# this flattens to one segment whose argv[0] is `git`, and the gh body is never
# scanned.
run_case "R7: newline-separated chain is split at the command boundary" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'git status
gh pr comment 1 --body "5x faster"'

# R8 is R7's control: the newline rewrite must NOT cut a multi-line body, which
# is this hook's primary input (`### Verification` anchors are multi-line).
run_case "R8: multi-line quoted body keeps its timing artifact attached" \
  silent \
  'gh pr comment 1 --body "### Verification
3x speedup
$ hyperfine ./bench -> wall-clock: 12.4s"'

run_case "R9: multi-line quoted body with no artifact still fires" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh pr comment 1 --body "### Verification
3x speedup expected"'

# R10/R11 mirror the sibling's invocation-shape cases: structural tokenization
# must find the deliverable through a path-invoked binary and through gh's own
# global flags sitting between `gh` and its subcommand.
run_case "R10: path-invoked gh binary" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  '/usr/bin/gh pr comment 1 --body "5x faster"'

run_case "R11: global flag between gh and its subcommand" \
  "advisory:[perf-multiplier-evidence-advisory]" \
  'gh --repo owner/name pr comment 1 --body "5x faster"'

# ---------------------------------------------------------------------------
# === PRECISION (#1544) — #1538's replay found 0 true positives in 11 fires.
# A lever verdict names a lever and gives it a verdict; an x/배 multiplier
# sits on one line with its number and near a performance word.
# ---------------------------------------------------------------------------

MARKER="advisory:[perf-multiplier-evidence-advisory]"

run_case "P1: a verdict word with no lever is not a lever verdict (silent)" silent \
  'gh pr comment 1 --body "번호 지목은 라벨을 그대로 채택합니다"'
run_case "P2: the word lever with no verdict is not a lever verdict (silent)" silent \
  'gh pr comment 1 --body "names this lever the completion criterion"'
run_case "P3: 레버 with a verdict word is a lever verdict (warn)" "$MARKER" \
  'gh pr comment 1 --body "이 레버는 역효과입니다"'
run_case "P4: an uppercase Lever with a verdict word still reads (warn)" "$MARKER" \
  'gh pr comment 1 --body "Lever: 채택"'
run_case "P5: a count and a header on the next line are not a multiplier (silent)" silent \
  'gh pr comment 1 --body "headers: 50
X-XSRF-TOKEN speed"'
run_case "P6: a number and 배 split by a newline are not a multiplier (silent)" silent \
  'gh pr comment 1 --body "속도 2
배 차이"'
run_case "P7: a space or tab between the number and x still reads (warn)" "$MARKER" \
  "gh pr comment 1 --body \"3 x faster, then 2$(printf '\t')x faster\""
# The issue's own control; the number follows ×, so it was silent before too.
run_case "P8: retry ×2 with no performance word is silent" silent \
  'gh pr comment 1 --body "retry ×2 before giving up"'
run_case "P8b: a call count 200 ×2 is not a speed factor (silent)" silent \
  'gh pr comment 1 --body "PUT 200 ×2 then GET 200"'
run_case "P8c: 3x faster still fires" "$MARKER" \
  'gh pr comment 1 --body "the new path is 3x faster"'
run_case "P9: 2배 with no performance word is a count (silent)" silent \
  'gh pr comment 1 --body "사용량 표가 약 2배 과대 집계됩니다"'
run_case "P10: a performance word before the multiplier (warn)" "$MARKER" \
  'gh pr comment 1 --body "speedup of 3x on the grid"'
run_case "P11: a Korean performance word next to 배 (warn)" "$MARKER" \
  'gh pr comment 1 --body "속도가 2배 빨라집니다"'
run_case "P12: a performance word past 40 characters does not count (silent)" silent \
  'gh pr comment 1 --body "3x the calls were issued across every retry in this run, and the speed was fine"'
run_case "P13: a timing pair still fires with no performance word (warn)" "$MARKER" \
  'gh pr comment 1 --body "precompute: 49m -> 12m"'
run_case "P14: a ratio of two timings reads as performance context (warn)" "$MARKER" \
  'gh pr comment 1 --body "기동이 80~110 ms 로 본문의 8 ms 와 10배 차이입니다"'
# A timing near a count also reads as context; spec records this trade-off.
run_case "P15: a retry count next to its interval still fires (warn)" "$MARKER" \
  'gh pr comment 1 --body "retry 3x every 5s"'

# Replay of #1538's sample, pseudonymized (spec "Replay fixtures"). 06 and 08
# still warn (a configuration change written as a timing pair, the #850
# shape); the other 9 were false positives.
REPLAY_DIR="$ROOT_DIR/tests/fixtures/perf-multiplier-evidence-advisory/replay-1544"
for d in "$REPLAY_DIR"/*/; do
  n=$(basename "$d")
  case "$n" in
    06|08) expect="$MARKER"; label=warn ;;
    *) expect=silent; label=silent ;;
  esac
  run_case "replay-1544 case $n ($label)" "$expect" \
    "gh pr comment 1 --body-file ${d}body.txt"
done

# ---------------------------------------------------------------------------
# === SILENT: non-Bash tool / malformed payload ===
# ---------------------------------------------------------------------------

err=$(echo '{"tool_name": "Read", "tool_input": {"file_path": "body.md"}}' | python3 "$HOOK" 2>&1 >/dev/null)
rc=$?
if [ "$rc" -eq 0 ] && [ -z "$err" ]; then
  echo "PASS  [non-Bash tool is silent]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [non-Bash tool is silent] rc=$rc err=${err:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-Bash tool is silent")
fi

err=$(echo 'not json' | python3 "$HOOK" 2>&1 >/dev/null)
rc=$?
if [ "$rc" -eq 0 ] && [ -z "$err" ]; then
  echo "PASS  [malformed JSON fails open]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [malformed JSON fails open] rc=$rc err=${err:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed JSON fails open")
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "== $PASS passed, $FAIL failed =="
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
