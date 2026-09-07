#!/bin/bash
# test_caller_probe_gate.sh — coverage for hooks/advisory-nudge/caller-probe-gate/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   warn   → exit 0 + stderr contains "REMINDER"
#   silent → exit 0 + stderr empty
#   block  → exit 2 + stderr contains "REMINDER" (when PRAXIS_CALLER_PROBE_STRICT=1)
#
# Usage: bash tests/hooks/advisory-nudge/test_caller_probe_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/caller-probe-gate/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

# Throwaway fire-telemetry sink so test runs never pollute the real ledger.
TEL_FILE=$(mktemp)
export PRAXIS_FIRE_TELEMETRY_FILE="$TEL_FILE"

PASS=0
FAIL=0
FAILED_NAMES=()

run_case() {
  local name="$1" expectation="$2" strict="$3" payload="$4"

  local err_file
  err_file=$(mktemp)
  if [ "$strict" = "strict" ]; then
    echo "$payload" | env PRAXIS_CALLER_PROBE_STRICT=1 python3 "$HOOK" >/dev/null 2>"$err_file"
  else
    echo "$payload" | env -u PRAXIS_CALLER_PROBE_STRICT python3 "$HOOK" >/dev/null 2>"$err_file"
  fi
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
    warn)
      [ "$rc" -eq 0 ] || ok=0
      echo "$err" | grep -q "REMINDER" || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q "REMINDER" || ok=0
      ;;
    *)
      echo "  internal: unknown expectation '$expectation'" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    PASS=$((PASS + 1))
    echo "  PASS  $name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    echo "  FAIL  $name (rc=$rc, expected=$expectation)"
    [ -n "$err" ] && echo "        stderr: $err" | head -c 400
  fi
}

echo "test_caller_probe_gate"

# --- Regression: the motivating case (issue #906) ----------------------------
# A Read of the cited file must NOT clear the gate. This is the exact evidence
# the retracted issue already had, and admitting it would reproduce
# source-citation-probe-gate's oracle instead of complementing it.

READ_ONLY_TRANSCRIPT=$(mktemp /tmp/cpg-readonly-XXXXXX.jsonl)
cat > "$READ_ONLY_TRANSCRIPT" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Read","input":{"file_path":"/repo/dags/account_deletion.py"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"sed -n '700,780p' dags/account_deletion.py"}}]}}
EOF

run_case "MOTIVATING: Read of cited file does NOT clear (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title 'fix(dag): guard drops shared data' --body 'dags/account_deletion.py 의 가드가 project_membership 을 보지 않습니다'\"},\"transcript_path\":\"$READ_ONLY_TRANSCRIPT\"}"

run_case "MOTIVATING: sed/cat single-file open does NOT clear (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title 'fix(dag): shared guard' --body 'the guard in worker.py fails to check membership'\"},\"transcript_path\":\"$READ_ONLY_TRANSCRIPT\"}"

# The same claim, after a call-site search, clears.
SEARCH_TRANSCRIPT=$(mktemp /tmp/cpg-search-XXXXXX.jsonl)
cat > "$SEARCH_TRANSCRIPT" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"grep -rn account_deletion analytics_backend/"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Grep","input":{"pattern":"invalidate_owner_project_governance_sync"}}]}}
EOF

run_case "cleared by transcript grep of cited path stem (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title 'fix(dag): guard drops shared data' --body 'dags/account_deletion.py 의 가드가 잘못 판정합니다'\"},\"transcript_path\":\"$SEARCH_TRANSCRIPT\"}"

run_case "cleared by Grep tool pattern of cited symbol (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title 'fix(api): stale membership' --body 'the helper \`invalidate_owner_project_governance_sync\` fails to run'\"},\"transcript_path\":\"$SEARCH_TRANSCRIPT\"}"

run_case "grep of a DIFFERENT symbol does not clear (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title 'fix(etl): bad join' --body 'unrelated_module.py incorrectly joins the table'\"},\"transcript_path\":\"$SEARCH_TRANSCRIPT\"}"

# --- Axis A — call-level signals ---------------------------------------------

run_case "fix( title + cited path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title \"fix(auth): reject stale token\" --body \"see handler.py for the flow\""}}'

run_case "--label bug + cited path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title \"investigate token flow\" --label type:bug --body \"see handler.py for the flow\""}}'

run_case "feat( title + cited path, no defect token (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title \"feat(auth): add refresh\" --body \"new logic goes in handler.py\""}}'

run_case "--label enhancement + cited path (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title \"add refresh\" --label enhancement --body \"new logic goes in handler.py\""}}'

# --- Axis A — body-level defect tokens ---------------------------------------

run_case "EN 'fails to' + cited path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"parser.py fails to reset the offset\""}}'

run_case "EN 'incorrectly' + backtick symbol (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"`resolve_body` incorrectly returns empty\""}}'

run_case "KO 결함 + cited path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"worker.py 에 결함이 있습니다\""}}'

run_case "KO 누락 + backtick symbol (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"`membership_guard` 검사가 누락되었습니다\""}}'

# --- Axis B absent — no code cited -------------------------------------------

run_case "defect token but no code cited (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"the export fails to finish before the timeout\""}}'

run_case "fix( title but no code cited (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title \"fix(ops): restart the worker nightly\" --body \"운영 절차만 바꿉니다\""}}'

run_case "short backtick span is not a symbol (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"the `id` value incorrectly resets\""}}'

# --- Block window (body-level axis A only) -----------------------------------

WINDOW_NEAR=$(mktemp /tmp/cpg-near-XXXXXX.md)
printf 'Some context line.\nThe guard fails to check membership.\nSee worker.py for the code.\n' > "$WINDOW_NEAR"
run_case "code within +-3 lines of defect token (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $WINDOW_NEAR\"}}"
rm -f "$WINDOW_NEAR"

WINDOW_FAR=$(mktemp /tmp/cpg-far-XXXXXX.md)
printf 'The guard fails to check membership.\nfiller\nfiller\nfiller\nfiller\nUnrelated appendix: worker.py is the entry point.\n' > "$WINDOW_FAR"
run_case "code outside +-3 lines of defect token (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $WINDOW_FAR\"}}"
rm -f "$WINDOW_FAR"

# --- Arm A — in-body Caller-probe line ---------------------------------------

PROBE_OK=$(mktemp /tmp/cpg-probe-XXXXXX.md)
printf 'worker.py fails to check membership.\nCaller-probe: grep -rn worker app/ → app/main.py:12 calls worker.run()\n' > "$PROBE_OK"
run_case "Arm A: filled Caller-probe line (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $PROBE_OK\"}}"
rm -f "$PROBE_OK"

PROBE_KO=$(mktemp /tmp/cpg-probeko-XXXXXX.md)
printf 'worker.py 에 결함이 있습니다.\n반증 프로브: grep -rn worker app/ → app/main.py:12 에서 호출\n' > "$PROBE_KO"
run_case "Arm A: Korean 반증 프로브 prefix (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $PROBE_KO\"}}"
rm -f "$PROBE_KO"

PROBE_SCAFFOLD=$(mktemp /tmp/cpg-scaffold-XXXXXX.md)
printf 'worker.py fails to check membership.\nCaller-probe: <command> → <output>\n' > "$PROBE_SCAFFOLD"
run_case "Arm A: unfilled scaffold does not clear (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $PROBE_SCAFFOLD\"}}"
rm -f "$PROBE_SCAFFOLD"

PROBE_EMPTY=$(mktemp /tmp/cpg-empty-XXXXXX.md)
printf 'worker.py fails to check membership.\nCaller-probe: grep -rn worker app/ →\n' > "$PROBE_EMPTY"
run_case "Arm A: empty evidence after arrow does not clear (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $PROBE_EMPTY\"}}"
rm -f "$PROBE_EMPTY"

PROBE_NOARROW=$(mktemp /tmp/cpg-noarrow-XXXXXX.md)
printf 'worker.py fails to check membership.\nCaller-probe: I checked the callers already\n' > "$PROBE_NOARROW"
run_case "Arm A: no arrow does not clear (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $PROBE_NOARROW\"}}"
rm -f "$PROBE_NOARROW"

PROBE_NOCMD=$(mktemp /tmp/cpg-nocmd-XXXXXX.md)
printf 'worker.py fails to check membership.\nCaller-probe: → observed the caller\n' > "$PROBE_NOCMD"
run_case "Arm A: arrow with no command before it does not clear (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $PROBE_NOCMD\"}}"
rm -f "$PROBE_NOCMD"

# --- Per-write isolation (Codex round 1, P2) ---------------------------------
# A fix( title on one write must not make a sibling write's incidental file
# mention read as a defect citation.

run_case "two writes: fix( title on the one WITHOUT code (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title \"fix(a): b\" --body \"no code cited here\" && gh pr comment 9 --body \"looks good, see helper.py for context\""}}'

run_case "two writes: fix( title on the one WITH code (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 9 --body \"looks good, thanks\" && gh issue create --title \"fix(a): b\" --body \"helper.py is the culprit\""}}'

ISO_PROBE=$(mktemp /tmp/cpg-iso-XXXXXX.md)
printf 'helper.py fails to reset.\nCaller-probe: grep -rn helper app/ → app/main.py:3 calls helper\n' > "$ISO_PROBE"
run_case "two writes: probe in write A does not clear write B (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $ISO_PROBE && gh pr comment 6 --body \\\"other.py fails to close the pool\\\"\"}}"
rm -f "$ISO_PROBE"

# --- Surface variants --------------------------------------------------------

run_case "-b inline body (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 -b \"walker.py incorrectly skips the last row\""}}'

run_case "--body=value form (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body=\"walker.py incorrectly skips the last row\""}}'

run_case "gh pr review (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr review 12 --comment --body \"runner.py fails to close the pool\""}}'

run_case "chained echo && gh pr comment (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"echo staged && gh pr comment 7 --body \"runner.py fails to close the pool\""}}'

run_case "env-prefix FOO=1 gh issue comment (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"FOO=1 gh issue comment 3 --body \"pool.py fails to release the handle\""}}'

# The Slack/Notion MCP registrations were dropped (#1359); these hooks are
# PreToolUse(Bash) only now, so an MCP payload falls through the `else` arm
# and is silent. Kept as the regression pin for the removal — the payloads
# still carry the shapes that used to warn.
run_case "MCP slack send is out of scope (silent)" \
  "silent" "advisory" \
  '{"tool_name":"mcp__laplace-slack__slack_send_message","tool_input":{"text":"heads up: pool.py fails to release the handle"}}'

run_case "MCP notion create page is out of scope (silent)" \
  "silent" "advisory" \
  '{"tool_name":"mcp__notion__notion-create-pages","tool_input":{"children":[{"paragraph":{"rich_text":[{"text":{"content":"pool.py fails to release the handle"}}]}}]}}'

run_case "--body-file missing path (silent fail-open)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title \"fix(x): y\" --body-file does-not-exist-906.md"}}'

run_case "gh issue list is not a write (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list --label type:bug"}}'

# --- Strict mode / fail-open -------------------------------------------------

run_case "strict mode converts warn to block" \
  "block" "strict" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"parser.py fails to reset the offset\""}}'

run_case "strict mode still silent when cleared" \
  "silent" "strict" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title 'fix(dag): guard' --body 'dags/account_deletion.py 가드가 잘못 판정합니다'\"},\"transcript_path\":\"$SEARCH_TRANSCRIPT\"}"

run_case "no transcript_path — Arm B fails open, Arm A absent (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"parser.py fails to reset the offset\""}}'

run_case "unreadable transcript path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"parser.py fails to reset the offset\""},"transcript_path":"/tmp/does-not-exist-906.jsonl"}'

run_case "malformed stdin JSON (silent fail-open)" \
  "silent" "advisory" \
  'this is not json'

run_case "non-write non-MCP tool (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Read","tool_input":{"file_path":"/tmp/impl.py"}}'

rm -f "$READ_ONLY_TRANSCRIPT" "$SEARCH_TRANSCRIPT"

# --- Summary -----------------------------------------------------------------

rm -f "$TEL_FILE"

echo ""
echo "passed: $PASS, failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
