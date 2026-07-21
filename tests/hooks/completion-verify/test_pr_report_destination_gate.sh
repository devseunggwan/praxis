#!/bin/bash
# Tests for completion-verify/pr-report-destination-gate (Stop hook, issue #832).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/pr-report-destination-gate/impl.py"

unset PRAXIS_HOOK_BYPASS_PR_REPORT_DESTINATION_GATE PRAXIS_HOOK_ERROR_STDERR

PASS=0
FAIL=0

# build_transcript <events_json_array> -> writes path to $TRANSCRIPT
build_transcript() {
  local events_json="$1"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$events_json" <<'PY'
import json, sys
path, events_json = sys.argv[1], sys.argv[2]
events = json.loads(events_json)
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# Convenience builders --------------------------------------------------------
bash_use() { printf '{"message":{"role":"assistant","content":[{"type":"tool_use","id":"%s","name":"Bash","input":{"command":%s}}]}}' "$1" "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$2")"; }
write_use() { printf '{"message":{"role":"assistant","content":[{"type":"tool_use","id":"%s","name":"Write","input":{"file_path":"%s"}}]}}' "$1" "$2"; }
result() { printf '{"message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"%s","is_error":%s,"content":"x"}]}}' "$1" "$2"; }

# run_case <advisory|silent> <name> <stop_payload_extra_json> [ENV=v ...]
run_case() {
  local expected="$1" name="$2" extra="$3"
  shift 3
  local payload out err rc ok=1 err_file
  payload=$(python3 -c 'import json,sys
p={"transcript_path":sys.argv[1]}
p.update(json.loads(sys.argv[2]))
print(json.dumps(p))' "$TRANSCRIPT" "$extra")
  err_file=$(mktemp)
  out=$(printf '%s' "$payload" | env "$@" python3 "$HOOK" 2>"$err_file")
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert "[pr-report-destination-gate]" in d["systemMessage"], d
assert "decision" not in d, d
' || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      [ -z "$out" ] || ok=0
      ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc out=<$out> err=<$err>"; FAIL=$((FAIL + 1))
  fi
}

# =====================================================================
# Core — report written, context PR unreported -> advisory
# =====================================================================
build_transcript "[$(bash_use t1 'gh pr view 178 --json mergeable'),$(write_use t2 /tmp/verification-report.md)]"
run_case advisory "context-report-no-post" '{}'

# posted OK to same PR -> silent
build_transcript "[$(bash_use t1 'gh pr view 178'),$(write_use t2 /tmp/review-report.md),$(bash_use t3 'gh pr comment 178 --body-file /tmp/review-report.md'),$(result t3 false)]"
run_case silent "posted-ok-same-pr" '{}'

# =====================================================================
# Correctness guards
# =====================================================================
# GET gh api reviews only (read, not a post) -> advisory
build_transcript "[$(bash_use t1 'gh pr view 178'),$(write_use t2 /tmp/verify-report.md),$(bash_use t3 'gh api repos/o/r/pulls/178/reviews')]"
run_case advisory "get-api-not-a-post" '{}'

# POST gh api reviews -> silent
build_transcript "[$(bash_use t1 'gh pr view 178'),$(write_use t2 /tmp/verify-report.md),$(bash_use t3 'gh api --method POST repos/o/r/pulls/178/reviews --input /tmp/verify-report.md'),$(result t3 false)]"
run_case silent "post-api-counts" '{}'

# failed post (is_error) -> advisory
build_transcript "[$(bash_use t1 'gh pr view 178'),$(write_use t2 /tmp/verify-report.md),$(bash_use t3 'gh pr comment 178 --body x'),$(result t3 true)]"
run_case advisory "failed-post-excluded" '{}'

# posted to a different PR -> advisory (context 178 unreported)
build_transcript "[$(bash_use t1 'gh pr view 178'),$(write_use t2 /tmp/verify-report.md),$(bash_use t3 'gh pr comment 456 --body x'),$(result t3 false)]"
run_case advisory "posted-to-other-pr" '{}'

# explicit --method GET is a read, not a post -> advisory (Codex #6)
build_transcript "[$(bash_use t1 'gh pr view 178'),$(write_use t2 /tmp/verify-report.md),$(bash_use t3 'gh api --method GET repos/o/r/pulls/178/reviews -f page=1'),$(result t3 false)]"
run_case advisory "api-explicit-get-not-post" '{}'

# compound command: both PRs are context; posting only one leaves the other unreported (Codex #5)
build_transcript "[$(bash_use t1 'gh pr view 178 && gh pr view 179'),$(write_use t2 /tmp/verify-report.md),$(bash_use t3 'gh pr comment 178 --body x'),$(result t3 false)]"
run_case advisory "compound-context-partial-post" '{}'

# multi-PR listing in tool output is NOT context -> silent (report, no bound PR) (Codex #4)
build_transcript "[$(write_use t1 /tmp/verify-report.md),{\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"tool_result\",\"tool_use_id\":\"z\",\"content\":\"https://github.com/o/r/pull/900\\nhttps://github.com/o/r/pull/901\"}]}}]"
run_case silent "listing-output-not-context" '{}'

# session dedup: first fire records, repeat is silent (Codex #2)
LEDGER=$(mktemp); rm -f "$LEDGER"
build_transcript "[$(bash_use t1 'gh pr view 178'),$(write_use t2 /tmp/verification-report.md)]"
run_case advisory "session-first-fire" '{"session_id":"sDEDUP1"}' PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER"
run_case silent "session-dedup-repeat" '{"session_id":"sDEDUP1"}' PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER"

# =====================================================================
# Non-triggering
# =====================================================================
# no PR context -> silent
build_transcript "[$(write_use t2 /tmp/verify-report.md)]"
run_case silent "no-pr-context" '{}'

# no report-like file (plain source edit) -> silent
build_transcript "[$(bash_use t1 'gh pr view 178'),$(write_use t2 src/main.py)]"
run_case silent "no-report-file" '{}'

# gh pr checkout establishes context (no post) -> advisory
build_transcript "[$(bash_use t1 'gh pr checkout 178'),$(write_use t2 /tmp/verify-report.md)]"
run_case advisory "checkout-establishes-context" '{}'

# PR URL establishes context (no post) -> advisory
build_transcript "[$(write_use t1 /tmp/impact-report.md),{\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"tool_result\",\"tool_use_id\":\"z\",\"content\":\"https://github.com/o/r/pull/999\"}]}}]"
run_case advisory "pr-url-context" '{}'

# =====================================================================
# Tiers / env
# =====================================================================
build_transcript "[$(bash_use t1 'gh pr view 178'),$(write_use t2 /tmp/verification-report.md)]"
run_case silent "bypass-env" '{}' PRAXIS_HOOK_BYPASS_PR_REPORT_DESTINATION_GATE=1

build_transcript "[$(bash_use t1 'gh pr view 178'),$(write_use t2 /tmp/verification-report.md)]"
run_case silent "stop-hook-active" '{"stop_hook_active": true}'

# =====================================================================
# Fail-open
# =====================================================================
TRANSCRIPT="/nonexistent/transcript.jsonl"
run_case silent "missing-transcript" '{}'

err=$(printf 'not json' | python3 "$HOOK" 2>&1 1>/dev/null)
rc=$?
if [ "$rc" -eq 0 ] && [ -z "$err" ]; then
  echo "PASS  [malformed-json]"; PASS=$((PASS + 1))
else
  echo "FAIL  [malformed-json] rc=$rc err=<$err>"; FAIL=$((FAIL + 1))
fi

err=$(printf '{}' | python3 "$HOOK" 2>&1 1>/dev/null)
rc=$?
out=$(printf '{}' | python3 "$HOOK" 2>/dev/null)
if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -z "$out" ]; then
  echo "PASS  [empty-payload]"; PASS=$((PASS + 1))
else
  echo "FAIL  [empty-payload] rc=$rc out=<$out> err=<$err>"; FAIL=$((FAIL + 1))
fi

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
