#!/bin/bash
# Tests for completion-verify/merge-state-claim-gate (Stop hook).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/merge-state-claim-gate/impl.py"

unset PRAXIS_MERGE_CLAIM_BYPASS PRAXIS_MERGE_CLAIM_STRICT PRAXIS_HOOK_ERROR_STDERR

PASS=0
FAIL=0

# build_transcript <final_text> <evidence: none|gh|mcp> -> writes path to $TRANSCRIPT
build_transcript() {
  local final_text="$1" evidence="$2"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$final_text" "$evidence" <<'PY'
import json, sys
path, final_text, evidence = sys.argv[1], sys.argv[2], sys.argv[3]
events = [{"message": {"role": "user", "content": "please wrap up"}}]
asst_blocks = []
if evidence == "gh":
    asst_blocks.append({"type": "tool_use", "name": "Bash",
                        "input": {"command": "gh pr view 543 --json state"}})
elif evidence == "mcp":
    asst_blocks.append({"type": "tool_use", "name": "mcp__github__pull_request_read",
                        "input": {"pullNumber": 543}})
if asst_blocks:
    events.append({"message": {"role": "assistant", "content": asst_blocks}})
events.append({"message": {"role": "assistant",
                           "content": [{"type": "text", "text": final_text}]}})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# run_case <advisory|advisory-strict|silent> <name> <stop_payload_extra_json> [ENV=v ...]
run_case() {
  local expected="$1" name="$2" extra="$3"
  shift 3
  local payload err rc ok=1
  payload=$(python3 -c 'import json,sys
p={"transcript_path":sys.argv[1]}
p.update(json.loads(sys.argv[2]))
print(json.dumps(p))' "$TRANSCRIPT" "$extra")
  err=$(printf '%s' "$payload" | env "$@" python3 "$HOOK" 2>&1 1>/dev/null)
  rc=$?
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      printf '%s' "$err" | grep -q "\[merge-state-claim-gate\]" || ok=0
      ;;
    advisory-strict)
      [ "$rc" -eq 2 ] || ok=0
      printf '%s' "$err" | grep -q "\[merge-state-claim-gate\]" || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc err=<$err>"; FAIL=$((FAIL + 1))
  fi
}

# --- English claim, no evidence -> advisory -------------------------------
build_transcript "Done — PR #543 created and merged, issue closed." none
run_case advisory "en-claim-no-evidence" '{}'

# --- Korean claim, no evidence -> advisory --------------------------------
build_transcript "PR #543 를 머지했고 이슈도 닫았습니다." none
run_case advisory "ko-claim-no-evidence" '{}'

# --- claim WITH gh evidence -> silent -------------------------------------
build_transcript "PR #543 merged successfully." gh
run_case silent "claim-with-gh-evidence" '{}'

# --- claim WITH github MCP evidence -> silent -----------------------------
build_transcript "PR #543 is now merged." mcp
run_case silent "claim-with-mcp-evidence" '{}'

# --- neutral final message (no claim) -> silent ---------------------------
build_transcript "I updated the README and ran the tests; all green." none
run_case silent "no-claim" '{}'

# --- negated claim -> silent ----------------------------------------------
build_transcript "The PR is not merged yet — still waiting on CI." none
run_case silent "negated-claim" '{}'

# --- future intent (not past) -> silent -----------------------------------
build_transcript "Next I will create a PR and then merge it." none
run_case silent "future-intent" '{}'

# --- strict mode -> exit 2 ------------------------------------------------
build_transcript "PR #543 created and merged." none
run_case advisory-strict "strict-mode" '{}' PRAXIS_MERGE_CLAIM_STRICT=1

# --- bypass env -> silent -------------------------------------------------
build_transcript "PR #543 merged, worktree removed." none
run_case silent "bypass" '{}' PRAXIS_MERGE_CLAIM_BYPASS=1

# --- stop_hook_active -> silent (loop guard) ------------------------------
build_transcript "PR #543 merged." none
run_case silent "stop-hook-active" '{"stop_hook_active": true}'

# --- worktree cleanup claim, no evidence -> advisory ----------------------
build_transcript "All set — worktree cleaned up and the issue closed." none
run_case advisory "worktree-claim" '{}'

# --- fix #1: GitHub MCP get_issue tool_use as evidence -> silent ----------
# Regression guard: \bissue\b failed to match get_issue/close_issue because
# underscore is \w and blocks \b before "issue" in the tool name.
build_transcript "Issue #503 closed and worktree cleaned." none
# Build a transcript with a mcp__github__get_issue tool_use as evidence
python3 - "$TRANSCRIPT" <<'PY'
import json, sys
path = sys.argv[1]
events = [{"message": {"role": "user", "content": "please wrap up"}}]
asst_blocks = [{"type": "tool_use", "name": "mcp__github__get_issue",
                "input": {"issueNumber": 503}}]
events.append({"message": {"role": "assistant", "content": asst_blocks}})
events.append({"message": {"role": "assistant",
                           "content": [{"type": "text", "text": "Issue #503 closed and worktree cleaned."}]}})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
run_case silent "mcp-get-issue-as-evidence" '{}'

# Also test close_issue as evidence
python3 - "$TRANSCRIPT" <<'PY'
import json, sys
path = sys.argv[1]
events = [{"message": {"role": "user", "content": "please wrap up"}}]
asst_blocks = [{"type": "tool_use", "name": "mcp__github__close_issue",
                "input": {"issueNumber": 503}}]
events.append({"message": {"role": "assistant", "content": asst_blocks}})
events.append({"message": {"role": "assistant",
                           "content": [{"type": "text", "text": "Issue #503 closed."}]}})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
run_case silent "mcp-close-issue-as-evidence" '{}'

# --- fix #2 regression guard: "no conflicts" phrasing must NOT suppress ---
# The removed \bno\b arm previously suppressed these valid completion claims.
build_transcript "Issue #503 closed — no conflicts." none
run_case advisory "no-conflicts-still-fires" '{}'

build_transcript "PR #543 merged — no further action needed." none
run_case advisory "no-further-action-still-fires" '{}'

# --- fix #3: Korean particle after PR -> advisory fires -------------------
# \bPR\b failed to match 'PR을'/'PR이' because Hangul is \w and blocks \b.
build_transcript "PR을 머지했습니다." none
run_case advisory "pr-korean-particle-merged" '{}'

build_transcript "PR이 닫혔습니다." none
run_case advisory "pr-korean-particle-closed" '{}'

# --- fix #3 regression guard: IMPROVE / PROXY_URL must NOT trigger -------
build_transcript "IMPROVE the README — all steps completed." none
run_case silent "pr-substring-improve" '{}'

# --- known tolerable FP: future passive 'will be merged' -> advisory ------
# 'will' is NOT in _NEGATION_RE (line-level veto too blunt — it would suppress
# mixed-tense lines). This future-passive line fires as advisory; accepted as
# tolerable noise on an advisory-only hook. Documented, not fixed.
build_transcript "The PR will be merged after review." none
run_case advisory "future-passive-known-fp" '{}'

# --- regression guard: mixed-tense (real claim + future clause) -> advisory
# Removing 'will' from negation must NOT suppress lines where a genuine
# completion claim co-occurs with a future clause on the same line.
build_transcript "PR #543 merged — this will close the issue." none
run_case advisory "merged-claim-with-will-clause" '{}'

# --- missing transcript path -> fail-open silent --------------------------
TRANSCRIPT="/nonexistent/transcript.jsonl"
run_case silent "missing-transcript" '{}'

# --- malformed JSON stdin -> fail-open silent -----------------------------
err=$(printf 'not json' | python3 "$HOOK" 2>&1 1>/dev/null)
rc=$?
if [ "$rc" -eq 0 ] && [ -z "$err" ]; then
  echo "PASS  [malformed-json]"; PASS=$((PASS + 1))
else
  echo "FAIL  [malformed-json] rc=$rc err=<$err>"; FAIL=$((FAIL + 1))
fi

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
