#!/bin/bash
# Tests for completion-verify/pr-claim-mutation-gate (Stop hook).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/pr-claim-mutation-gate/impl.py"

unset PRAXIS_PR_CLAIM_BYPASS PRAXIS_PR_CLAIM_ADVISORY PRAXIS_HOOK_ERROR_STDERR

PASS=0
FAIL=0

# build_transcript <final_text> <evidence>
#   evidence: none|push|comment|review|api-write|api-read|mcp|write|prev-turn-push
#             |api-write-labels|push-dry-run|push-echoed|push-failed
#             |push-succeeded|mcp-read
# -> writes path to $TRANSCRIPT
build_transcript() {
  local final_text="$1" evidence="$2"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$final_text" "$evidence" <<'PY'
import json, sys
path, final_text, evidence = sys.argv[1], sys.argv[2], sys.argv[3]
events = [{"message": {"role": "user", "content": "review the PR comments"}}]

def bash_ev(cmd):
    return {"message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}}

if evidence == "prev-turn-push":
    # Mutation happened BEFORE the latest real user input -> outside current turn.
    events = [
        {"message": {"role": "user", "content": "push it"}},
        bash_ev("git push origin issue-868"),
        {"message": {"role": "user", "content": "review the PR comments"}},
    ]
elif evidence == "push":
    events.append(bash_ev("git push origin issue-868-feat-pr-claim-gate"))
elif evidence == "comment":
    events.append(bash_ev("gh pr comment 868 --body 'fixed'"))
elif evidence == "review":
    events.append(bash_ev("gh pr review 868 --comment --body 'ack'"))
elif evidence == "api-write":
    events.append(bash_ev("gh api repos/o/r/pulls/868/comments --method POST -f body=fixed"))
elif evidence == "api-read":
    events.append(bash_ev("gh api repos/o/r/pulls/868/comments --paginate"))
elif evidence == "mcp":
    events.append({"message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "mcp__github__add_pull_request_review_comment",
         "input": {"pr": 868}}]}})
elif evidence == "api-write-labels":
    # Write method, but a labels endpoint -- mutates GitHub, never the PR
    # review surface the claim is about.
    events.append(bash_ev("gh api repos/o/r/issues/868/labels --method POST -f labels=bug"))
elif evidence == "push-dry-run":
    events.append(bash_ev("git push --dry-run origin issue-868"))
elif evidence == "push-echoed":
    events.append(bash_ev("echo 'git push origin issue-868' >> /tmp/plan.txt"))
elif evidence == "push-failed":
    events.append({"message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_fail1", "name": "Bash",
         "input": {"command": "git push origin issue-868"}}]}})
    events.append({"message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_fail1", "is_error": True,
         "content": "! [rejected] issue-868 -> issue-868 (non-fast-forward)"}]}})
elif evidence == "push-succeeded":
    events.append({"message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_ok1", "name": "Bash",
         "input": {"command": "git push origin issue-868"}}]}})
    events.append({"message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_ok1", "is_error": False,
         "content": "a1b2c3d..e4f5g6h  issue-868 -> issue-868"}]}})
elif evidence == "mcp-read":
    events.append({"message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "mcp__github__get_review_comments",
         "input": {"pr": 868}}]}})
elif evidence == "write":
    events.append({"message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/tmp/x", "content": "y"}}]}})
events.append({"message": {"role": "assistant",
                           "content": [{"type": "text", "text": final_text}]}})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# run_case <block|advisory|silent> <name> <stop_payload_extra_json> [ENV=v ...]
run_case() {
  local expected="$1" name="$2" extra="$3"
  shift 3
  local payload err rc ok=1
  payload=$(python3 -c 'import json,sys
p={"transcript_path":sys.argv[1]}
p.update(json.loads(sys.argv[2]))
print(json.dumps(p))' "$TRANSCRIPT" "$extra")
  local out
  out=$(printf '%s' "$payload" | env "$@" python3 "$HOOK" 2>/tmp/pcmg-stderr.$$)
  rc=$?
  err=$(cat /tmp/pcmg-stderr.$$ 2>/dev/null; rm -f /tmp/pcmg-stderr.$$)
  case "$expected" in
    block)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["decision"] == "block"
assert "[pr-claim-mutation-gate]" in d["reason"]
' || ok=0
      ;;
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert "[pr-claim-mutation-gate]" in d["systemMessage"]
assert "decision" not in d
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

# --- motivating incident verbatim (issue #868): claim + zero mutation -------
build_transcript "CodeRabbit 리뷰 코멘트 3건을 모두 처리했습니다." none
run_case block "incident-verbatim-kr" '{}'

# --- EN claim, no mutation --------------------------------------------------
build_transcript "I've applied the fixes for all the PR review comments." none
run_case block "en-applied-no-mutation" '{}'

build_transcript "Fixed the comments on the PR." none
run_case block "en-fixed-comments-no-mutation" '{}'

build_transcript "Addressed the review feedback." none
run_case block "en-addressed-feedback-no-mutation" '{}'

# --- KR variants -------------------------------------------------------------
build_transcript "리뷰 피드백 반영했습니다." none
run_case block "kr-feedback-applied" '{}'

# --- claim WITH git push in turn -> silent ----------------------------------
build_transcript "PR 리뷰 코멘트를 모두 처리했습니다." push
run_case silent "claim-with-push" '{}'

# --- claim WITH gh pr comment -> silent -------------------------------------
build_transcript "Handled the review comments." comment
run_case silent "claim-with-gh-pr-comment" '{}'

# --- claim WITH gh pr review -> silent --------------------------------------
build_transcript "Resolved the PR comments." review
run_case silent "claim-with-gh-pr-review" '{}'

# --- claim WITH write-method gh api -> silent -------------------------------
build_transcript "Applied the review comment fixes." api-write
run_case silent "claim-with-gh-api-write" '{}'

# --- claim WITH read-only gh api (listing) -> STILL advisory ----------------
# Listing comments is not resolving them.
build_transcript "Fixed the review comments." api-read
run_case block "claim-with-gh-api-read-only-still-fires" '{}'

# --- claim WITH GitHub MCP comment tool -> silent ---------------------------
build_transcript "Addressed the PR review comments." mcp
run_case silent "claim-with-mcp-comment" '{}'

# --- Write tool_use is not PR-surface mutation -> advisory ------------------
build_transcript "Handled the review comments." write
run_case block "write-is-not-pr-mutation" '{}'

# --- mutation in PREVIOUS turn only -> advisory (turn-scoped evidence) ------
build_transcript "The PR review comments have been resolved." prev-turn-push
run_case block "prev-turn-mutation-not-evidence" '{}'

# --- no PR subject -> silent (generic completion, not this hook's scope) ---
build_transcript "작업을 완료했습니다." none
run_case silent "no-pr-subject" '{}'

# --- subject present, no claim verb -> silent -------------------------------
build_transcript "The PR has 3 open review comments." none
run_case silent "subject-no-claim" '{}'

# --- negated claim -> silent -------------------------------------------------
build_transcript "리뷰 코멘트는 아직 처리하지 못했습니다." none
run_case silent "negated-claim-kr" '{}'

build_transcript "I have not addressed the review comments yet." none
run_case silent "negated-claim-en" '{}'

# --- double negation is a KNOWN accepted gap: inner negation token still ---
# suppresses even though the sentence is semantically affirmative.
build_transcript "리뷰 코멘트를 처리하지 않은 게 아닙니다." none
run_case silent "double-negation-known-gap" '{}'

# --- hedged form -> silent (uncertainty disclosure, not a completion claim) -
build_transcript "리뷰 코멘트를 처리한 것 같습니다." none
run_case silent "hedged-kr" '{}'

build_transcript "I might have addressed the review comments." none
run_case silent "hedged-en" '{}'

# --- question form -> silent -------------------------------------------------
build_transcript "리뷰 코멘트 처리했나요?" none
run_case silent "question-form-kr" '{}'

build_transcript "Did I fix the review comments?" none
run_case silent "question-form-en" '{}'

# --- quoted line (reporting a claim, not making one) -> silent -------------
build_transcript "> 리뷰 코멘트를 처리했다고 보고했다 (사용자 인용)" none
run_case silent "quoted-line" '{}'

# --- advisory demote -> systemMessage instead of decision:block -------------
build_transcript "Fixed the review comments." none
run_case advisory "advisory-demote" '{}' PRAXIS_PR_CLAIM_ADVISORY=1

# --- write-method gh api on a NON-review endpoint -> still fires -----------
# The docstring promised a comments/reviews/threads endpoint restriction that
# the regex never enforced, so relabeling an issue cleared a review claim.
build_transcript "리뷰 코멘트 전부 반영했습니다." api-write-labels
run_case block "gh-api-write-on-labels-endpoint-still-fires" '{}'

# --- consolidated READ MCP tool -> still fires -----------------------------
# `get_review_comments` contains both "review" and "comment"; a bare substring
# match let a pure reader clear the claim.
build_transcript "리뷰 코멘트 전부 반영했습니다." mcp-read
run_case block "mcp-read-tool-still-fires" '{}'

# --- rehearsal / echoed forms are not mutations ----------------------------
build_transcript "리뷰 코멘트 전부 반영했습니다." push-dry-run
run_case block "push-dry-run-still-fires" '{}'

build_transcript "리뷰 코멘트 전부 반영했습니다." push-echoed
run_case block "echoed-push-still-fires" '{}'

# --- failed vs successful mutation -----------------------------------------
# A rejected push leaves the PR exactly as it was.
build_transcript "리뷰 코멘트 전부 반영했습니다." push-failed
run_case block "failed-push-still-fires" '{}'

build_transcript "리뷰 코멘트 전부 반영했습니다." push-succeeded
run_case silent "succeeded-push-clears" '{}'

# --- bypass env -> silent ----------------------------------------------------
build_transcript "Fixed the review comments." none
run_case silent "bypass" '{}' PRAXIS_PR_CLAIM_BYPASS=1

# --- stop_hook_active -> silent (loop guard) --------------------------------
build_transcript "Fixed the review comments." none
run_case silent "stop-hook-active" '{"stop_hook_active": true}'

# --- missing transcript -> silent (fail-open) -------------------------------
TRANSCRIPT="/nonexistent/transcript.jsonl"
run_case silent "missing-transcript" '{}'

echo ""
echo "pr-claim-mutation-gate: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
