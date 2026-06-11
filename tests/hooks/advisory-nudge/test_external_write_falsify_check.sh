#!/bin/bash
# test_external_write_falsify_check.sh — coverage for hooks/advisory-nudge/external-write-falsify-check/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   warn   → exit 0 + stderr contains "REMINDER"
#   silent → exit 0 + stderr empty
#   block  → exit 2 + stderr contains "REMINDER" (when PRAXIS_EXTERNAL_WRITE_STRICT=1)
#
# Usage: bash tests/test_external_write_falsify_check.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/external-write-falsify-check/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation strict_env payload_json
#   expectation:
#     "warn"   — stderr contains REMINDER, rc=0
#     "silent" — stderr empty, rc=0
#     "block"  — stderr contains REMINDER, rc=2
run_case() {
  local name="$1" expectation="$2" strict="$3" payload="$4"

  local err_file
  err_file=$(mktemp)
  if [ "$strict" = "strict" ]; then
    echo "$payload" | env -u PRAXIS_CLUSTER_APPROVAL_STRICT -u PRAXIS_APPLIED_CLAIM_STRICT PRAXIS_EXTERNAL_WRITE_STRICT=1 python3 "$HOOK" >/dev/null 2>"$err_file"
  elif [ "$strict" = "cluster_strict" ]; then
    echo "$payload" | env -u PRAXIS_EXTERNAL_WRITE_STRICT -u PRAXIS_APPLIED_CLAIM_STRICT PRAXIS_CLUSTER_APPROVAL_STRICT=1 python3 "$HOOK" >/dev/null 2>"$err_file"
  elif [ "$strict" = "applied_strict" ]; then
    echo "$payload" | env -u PRAXIS_EXTERNAL_WRITE_STRICT -u PRAXIS_CLUSTER_APPROVAL_STRICT PRAXIS_APPLIED_CLAIM_STRICT=1 python3 "$HOOK" >/dev/null 2>"$err_file"
  else
    echo "$payload" | env -u PRAXIS_EXTERNAL_WRITE_STRICT -u PRAXIS_CLUSTER_APPROVAL_STRICT -u PRAXIS_APPLIED_CLAIM_STRICT python3 "$HOOK" >/dev/null 2>"$err_file"
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

echo "test_external_write_falsify_check"

# --- Bash gh detection
run_case "gh issue comment + hypothesis marker (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"This might fail because the regex appears to mismatch.\""}}'

run_case "gh issue comment + verified claim (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"Confirmed: 819 rows verified.\""}}'

run_case "gh pr comment + marker (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 50 --body \"This is potentially broken under concurrent writes.\""}}'

run_case "gh issue create + marker via -b (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title foo -b \"hypothesis: the cache could be stale\""}}'

run_case "gh issue create with --body=value (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title foo --body=\"the loader might be failing\""}}'

# --- non-write gh subcommands → silent
run_case "gh issue list (not a write)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list --search foo"}}'

run_case "gh search issues (not a write)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh search issues might-fail"}}'

# --- non-Bash, non-MCP → silent
run_case "Read tool (no body) silent" \
  "silent" "advisory" \
  '{"tool_name":"Read","tool_input":{"file_path":"/tmp/foo"}}'

# --- MCP detection
run_case "MCP slack send + KO marker (warn)" \
  "warn" "advisory" \
  '{"tool_name":"mcp__slack__slack_send_message","tool_input":{"text":"이건 가설인데 prod 적재가 실패했을 가능성이 있습니다."}}'

run_case "MCP slack send + verified content (silent)" \
  "silent" "advisory" \
  '{"tool_name":"mcp__slack__slack_send_message","tool_input":{"text":"검증 완료: 819 rows."}}'

run_case "MCP notion update_page + marker (warn)" \
  "warn" "advisory" \
  '{"tool_name":"mcp__notion__notion_update_page","tool_input":{"content":"This appears to be the cause."}}'

# --- strict mode → block
run_case "strict mode + marker (block)" \
  "block" "strict" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"This might fail.\""}}'

run_case "strict mode + no marker (silent)" \
  "silent" "strict" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"Verified.\""}}'

# --- gh pr review (codex P2 round 3): catches body in pr review subcommand
run_case "gh pr review --body + marker (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr review --comment --body \"This might break under concurrency.\""}}'

run_case "gh pr review --approve + verified body (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr review --approve --body \"Tests pass, logic verified.\""}}'

# --- chained Bash commands: scan beyond the first body
run_case "chained gh writes — marker in 2nd write (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 1 --body \"Verified by tests.\" && gh issue comment 2 --body \"This might fail.\""}}'

run_case "chained gh writes — all bodies verified (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 1 --body \"Verified.\"; gh issue comment 2 --body \"Confirmed by query.\""}}'

# --- P2: MCP nested-body recursive extraction (issue #174)
run_case "MCP notion_append_blocks nested rich_text + marker (warn)" \
  "warn" "advisory" \
  '{"tool_name":"mcp__notion__notion_append_blocks","tool_input":{"block_id":"abc","children":[{"paragraph":{"rich_text":[{"text":{"content":"This might fail under load."}}]}}]}}'

run_case "MCP notion_append_blocks nested verified content (silent)" \
  "silent" "advisory" \
  '{"tool_name":"mcp__notion__notion_append_blocks","tool_input":{"block_id":"abc","children":[{"paragraph":{"rich_text":[{"text":{"content":"Confirmed: 819 rows."}}]}}]}}'

run_case "MCP slack send_message blocks nested text + marker (warn)" \
  "warn" "advisory" \
  '{"tool_name":"mcp__slack__slack_send_message","tool_input":{"channel":"C123","blocks":[{"type":"section","text":{"type":"mrkdwn","text":"이건 가설인데 prod 지연 가능성."}}]}}'

run_case "MCP slack blocks nested verified text (silent)" \
  "silent" "advisory" \
  '{"tool_name":"mcp__slack__slack_send_message","tool_input":{"channel":"C123","blocks":[{"type":"section","text":{"type":"mrkdwn","text":"Verified 100 percent."}}]}}'

run_case "MCP notion non-body top-level fields ignored (silent)" \
  "silent" "advisory" \
  '{"tool_name":"mcp__notion__notion_create_page","tool_input":{"parent_id":"likely-channel-id","title":"Potential customers list"}}'

# Codex F2 regression: Notion page property titles live under
# `properties.{name}.title[].text.content` (NOT body content). A naive
# recursive walker would collect the title text and trip "potential " marker.
run_case "MCP notion property title not collected as body (silent)" \
  "silent" "advisory" \
  '{"tool_name":"mcp__notion__notion_create_page","tool_input":{"parent":{"database_id":"abc"},"properties":{"Name":{"title":[{"text":{"content":"Potential customers list"}}]}}}}'

# Notion page with property title (marker-ish string) AND children body (real
# marker) → only children body should be collected → warn.
run_case "MCP notion property title silent + children body warn" \
  "warn" "advisory" \
  '{"tool_name":"mcp__notion__notion_create_page","tool_input":{"properties":{"Name":{"title":[{"text":{"content":"safe title"}}]}},"children":[{"paragraph":{"rich_text":[{"text":{"content":"This might fail."}}]}}]}}'

# --- malformed input → fail-open silent
run_case "malformed JSON → silent" \
  "silent" "advisory" \
  'not-json'

# --- author-exempt: claim-shape detection (issue #183) ---

# Mapping table with label name, no transcript → author-exempt advisory fires.
# Single-line body avoids the documented safe_tokenize newline-split limitation.
run_case "author-exempt: mapping table label, no verification (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"| type:docs | documentation |\""}}'

# Mapping table with prior gh label list in transcript → advisory silent.
AE_TRANSCRIPT=$(mktemp)
printf '%s\n' '{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","id":"t1","input":{"command":"gh label list --repo owner/repo"}}]}}' > "$AE_TRANSCRIPT"
run_case "author-exempt: mapping table + gh label list transcript (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue comment 100 --body \\\"| type:docs | documentation |\\\"\"},\"transcript_path\":\"$AE_TRANSCRIPT\"}"
rm -f "$AE_TRANSCRIPT"

# Bash code block with snake_case column name via --body-file → advisory fires.
# Uses --body-file to bypass the safe_tokenize newline-in-quoted-string limitation.
AE_BODY_FILE=$(mktemp)
printf 'Schema:\n\n```sql\nSELECT user_id FROM orders\n```\n' > "$AE_BODY_FILE"
run_case "author-exempt: bash code block column name (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr create --body-file $AE_BODY_FILE\"}}"
rm -f "$AE_BODY_FILE"

# --- cluster-approval: staging path detection (issue #276) ---

# Cluster approval in transcript + staging path Write → advisory fires.
CA_TRANSCRIPT=$(mktemp)
printf '%s\n' \
  '{"message":{"role":"user","content":[{"type":"text","text":"all 4 as separate issues, go ahead and approve them all"}]}}' \
  > "$CA_TRANSCRIPT"
run_case "cluster-approval: EN pattern + staging /tmp/-issue-.md Write (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/tmp/praxis-issue-b2.md\",\"content\":\"draft\"},\"transcript_path\":\"$CA_TRANSCRIPT\"}"
rm -f "$CA_TRANSCRIPT"

# Korean cluster approval ("1+3 같이") + staging path → advisory fires.
CA_TRANSCRIPT_KO=$(mktemp)
printf '%s\n' \
  '{"message":{"role":"user","content":[{"type":"text","text":"1+3 같이 진행해줘"}]}}' \
  > "$CA_TRANSCRIPT_KO"
run_case "cluster-approval: KO pattern + staging .omc/plans/ Write (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/Users/dev/repo/.omc/plans/batch-issue.md\",\"content\":\"draft\"},\"transcript_path\":\"$CA_TRANSCRIPT_KO\"}"
rm -f "$CA_TRANSCRIPT_KO"

# Cluster approval in transcript but Write to non-staging path → no advisory.
CA_TRANSCRIPT2=$(mktemp)
printf '%s\n' \
  '{"message":{"role":"user","content":[{"type":"text","text":"all 4 together approved"}]}}' \
  > "$CA_TRANSCRIPT2"
run_case "cluster-approval: EN pattern + non-staging path (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/Users/dev/repo/README.md\",\"content\":\"docs\"},\"transcript_path\":\"$CA_TRANSCRIPT2\"}"
rm -f "$CA_TRANSCRIPT2"

# Staging path but no cluster-approval language in recent user messages → no advisory.
EMPTY_TRANSCRIPT=$(mktemp)
printf '%s\n' \
  '{"message":{"role":"user","content":[{"type":"text","text":"please write the draft for issue 42"}]}}' \
  > "$EMPTY_TRANSCRIPT"
run_case "cluster-approval: staging path but no cluster-approval language (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/tmp/task-issue-42.md\",\"content\":\"draft\"},\"transcript_path\":\"$EMPTY_TRANSCRIPT\"}"
rm -f "$EMPTY_TRANSCRIPT"

# Write tool, staging path, cluster approval, strict mode → block.
CA_TRANSCRIPT_STRICT=$(mktemp)
printf '%s\n' \
  '{"message":{"role":"user","content":[{"type":"text","text":"4 buckets together, all approved"}]}}' \
  > "$CA_TRANSCRIPT_STRICT"
run_case "cluster-approval: strict mode + staging path (block)" \
  "block" "cluster_strict" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/tmp/praxis-pr-fix.md\",\"content\":\"draft\"},\"transcript_path\":\"$CA_TRANSCRIPT_STRICT\"}"
rm -f "$CA_TRANSCRIPT_STRICT"

# Write tool with no transcript_path → fail-open (no advisory).
run_case "cluster-approval: Write + staging path + no transcript (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Write","tool_input":{"file_path":"/tmp/praxis-issue-x.md","content":"draft"}}'

# --- applied-on-branch claim detection (issue #656) ---

# EN applied claim in gh comment body, no reachability in transcript → warn.
run_case "applied: EN claim to prod, no reachability (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"The fix is deployed to prod.\""}}'

# KO applied claim ("dev에 적용됨") → warn.
run_case "applied: KO claim dev에 적용됨 (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 200 --body \"수정이 dev에 적용됐습니다.\""}}'

# State-only gh query in transcript must NOT clear the claim (incident shape).
AP_TRANSCRIPT_STATE=$(mktemp)
printf '%s\n' '{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","id":"t1","input":{"command":"gh pr view 543 --json state"}}]}}' > "$AP_TRANSCRIPT_STATE"
run_case "applied: state-only query does NOT clear (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue comment 100 --body \\\"Change applied to dev.\\\"\"},\"transcript_path\":\"$AP_TRANSCRIPT_STATE\"}"
rm -f "$AP_TRANSCRIPT_STATE"

# merge-base --is-ancestor in transcript clears the claim → silent.
AP_TRANSCRIPT_MB=$(mktemp)
printf '%s\n' '{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","id":"t1","input":{"command":"git merge-base --is-ancestor abc123 origin/prod"}}]}}' > "$AP_TRANSCRIPT_MB"
run_case "applied: merge-base evidence clears (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue comment 100 --body \\\"The fix is deployed to prod.\\\"\"},\"transcript_path\":\"$AP_TRANSCRIPT_MB\"}"
rm -f "$AP_TRANSCRIPT_MB"

# baseRefName query in transcript clears the claim → silent.
AP_TRANSCRIPT_BRN=$(mktemp)
printf '%s\n' '{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","id":"t1","input":{"command":"gh pr view 543 --json state,baseRefName"}}]}}' > "$AP_TRANSCRIPT_BRN"
run_case "applied: baseRefName evidence clears (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 200 --body \\\"변경이 prod 브랜치에 반영됐습니다.\\\"\"},\"transcript_path\":\"$AP_TRANSCRIPT_BRN\"}"
rm -f "$AP_TRANSCRIPT_BRN"

# Negated claim line → silent.
run_case "applied: negated claim (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"아직 prod에 적용되지 않았습니다.\""}}'

# Branch token without applied token → silent.
run_case "applied: branch token only (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"Checked out the dev branch for testing.\""}}'

# Applied token without branch token → silent.
run_case "applied: applied token only (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"Formatting applied across all files.\""}}'

# Strict mode → block.
run_case "applied: strict mode (block)" \
  "block" "applied_strict" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"Hotfix landed on main.\""}}'

# Review fix: grep of the baseRefName literal must NOT clear the claim.
AP_TRANSCRIPT_GREP=$(mktemp)
printf '%s\n' '{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","id":"t1","input":{"command":"grep -n baseRefName hooks/advisory-nudge/external-write-falsify-check/impl.py"}}]}}' > "$AP_TRANSCRIPT_GREP"
run_case "applied: grep baseRefName does NOT clear (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue comment 100 --body \\\"The fix is applied to prod.\\\"\"},\"transcript_path\":\"$AP_TRANSCRIPT_GREP\"}"
rm -f "$AP_TRANSCRIPT_GREP"

# Review fix: long-flag `git branch --merged --contains` clears → silent.
AP_TRANSCRIPT_LONG=$(mktemp)
printf '%s\n' '{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","id":"t1","input":{"command":"git branch --merged --contains abc123"}}]}}' > "$AP_TRANSCRIPT_LONG"
run_case "applied: branch long-flag --contains clears (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue comment 100 --body \\\"The fix is applied to prod.\\\"\"},\"transcript_path\":\"$AP_TRANSCRIPT_LONG\"}"
rm -f "$AP_TRANSCRIPT_LONG"

# Review fix: 'without incident' must NOT suppress the claim → warn.
run_case "applied: without-incident still fires (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"Deployed to prod without incident.\""}}'

# Review fix: 'released the lock' prose no longer false-positives → silent.
run_case "applied: released-lock prose (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"Released the lock on the main thread.\""}}'

# Codex P2: baseRefName-only query (no state field) must NOT clear → warn.
AP_TRANSCRIPT_BRO=$(mktemp)
printf '%s\n' '{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","id":"t1","input":{"command":"gh pr view 543 --json baseRefName"}}]}}' > "$AP_TRANSCRIPT_BRO"
run_case "applied: baseRefName-only query does NOT clear (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue comment 100 --body \\\"The fix is applied to prod.\\\"\"},\"transcript_path\":\"$AP_TRANSCRIPT_BRO\"}"
rm -f "$AP_TRANSCRIPT_BRO"

echo ""
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"
  for name in "${FAILED_NAMES[@]}"; do
    echo "  - $name"
  done
  exit 1
fi
exit 0
