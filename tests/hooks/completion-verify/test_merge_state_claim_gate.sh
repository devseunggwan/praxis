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
elif evidence == "merge-base":
    asst_blocks.append({"type": "tool_use", "name": "Bash",
                        "input": {"command": "git merge-base --is-ancestor abc123 origin/prod"}})
elif evidence == "baserefname":
    asst_blocks.append({"type": "tool_use", "name": "Bash",
                        "input": {"command": "gh pr view 543 --json state,baseRefName"}})
elif evidence == "grep-baserefname":
    asst_blocks.append({"type": "tool_use", "name": "Bash",
                        "input": {"command": "grep -n baseRefName hooks/completion-verify/merge-state-claim-gate/impl.py"}})
elif evidence == "baserefname-only":
    asst_blocks.append({"type": "tool_use", "name": "Bash",
                        "input": {"command": "gh pr view 543 --json baseRefName"}})
elif evidence == "branch-contains-long":
    asst_blocks.append({"type": "tool_use", "name": "Bash",
                        "input": {"command": "git branch --merged --contains abc123"}})
elif evidence == "gh-864":
    asst_blocks.append({"type": "tool_use", "name": "Bash",
                        "input": {"command": "gh pr view 864 --json state"}})
elif evidence == "gh-other":
    asst_blocks.append({"type": "tool_use", "name": "Bash",
                        "input": {"command": "gh pr view 111 --json state"}})
elif evidence == "mcp-864":
    asst_blocks.append({"type": "tool_use", "name": "mcp__github__pull_request_read",
                        "input": {"pullNumber": 864}})
elif evidence == "gh-1864":
    asst_blocks.append({"type": "tool_use", "name": "Bash",
                        "input": {"command": "gh pr view 1864 --json state"}})
elif evidence == "mcp-1864":
    asst_blocks.append({"type": "tool_use", "name": "mcp__github__pull_request_read",
                        "input": {"pullNumber": 1864}})
elif evidence == "gh-864-in-slug":
    asst_blocks.append({"type": "tool_use", "name": "Bash",
                        "input": {"command": "gh pr view 111 --repo org/project-864 --json state"}})
elif evidence == "mcp-864-in-owner":
    asst_blocks.append({"type": "tool_use", "name": "mcp__github__pull_request_read",
                        "input": {"owner": "team864", "pullNumber": 111}})
elif evidence == "gh-864-merge":
    asst_blocks.append({"type": "tool_use", "name": "Bash",
                        "input": {"command": "gh pr merge 864 --squash"}})
elif evidence == "mcp-864-merge":
    asst_blocks.append({"type": "tool_use", "name": "mcp__github__merge_pull_request",
                        "input": {"pullNumber": 864}})
elif evidence == "gh-864-url":
    asst_blocks.append({"type": "tool_use", "name": "Bash",
                        "input": {"command": "gh pr view https://github.com/o/r/pull/864 --json state"}})
if asst_blocks:
    events.append({"message": {"role": "assistant", "content": asst_blocks}})
events.append({"message": {"role": "assistant",
                           "content": [{"type": "text", "text": final_text}]}})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# run_case <advisory|advisory-unchanged-only|advisory-strict|silent> <name> <stop_payload_extra_json> [ENV=v ...]
run_case() {
  local expected="$1" name="$2" extra="$3"
  shift 3
  local payload err rc ok=1
  payload=$(python3 -c 'import json,sys
p={"transcript_path":sys.argv[1]}
p.update(json.loads(sys.argv[2]))
print(json.dumps(p))' "$TRANSCRIPT" "$extra")
  # issue #647 H3: advisory/block both arrive as stdout JSON (exit always 0);
  # stderr must stay empty in every case.
  local out
  out=$(printf '%s' "$payload" | env "$@" python3 "$HOOK" 2>/tmp/msc-stderr.$$)
  rc=$?
  err=$(cat /tmp/msc-stderr.$$ 2>/dev/null; rm -f /tmp/msc-stderr.$$)
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert "[merge-state-claim-gate]" in d["systemMessage"]
assert "decision" not in d
' || ok=0
      ;;
    advisory-unchanged-only)
      # the advisory carries ONLY the per-number persistence guidance — the
      # generic "no fresh state query" sentence belongs to the merged claim,
      # which the generic query already cleared.
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
m = d["systemMessage"]
assert "[merge-state-claim-gate]" in m
assert "decision" not in d
assert "still-open / unchanged / no-loss" in m
assert "asserts a" not in m, m
' || ok=0
      ;;
    advisory-strict)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["decision"] == "block"
assert "[merge-state-claim-gate]" in d["reason"]
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

# --- strict mode -> decision:block JSON ------------------------------------------------
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

# --- #656: applied-on-branch claim, no evidence -> advisory ----------------
build_transcript "The fix is now applied to prod." none
run_case advisory "applied-en-no-evidence" '{}'

# --- #656: Korean applied claim with branch-only subject -> advisory -------
build_transcript "수정이 prod에 적용됐습니다." none
run_case advisory "applied-ko-branch-subject" '{}'

# --- #656 CORE: state-only gh evidence must NOT clear an applied claim -----
# This is the 2026-05-15 incident shape: `gh pr view --json state` was run,
# state=MERGED, but the merged PR's base was a feature branch (stacked PR).
build_transcript "PR #543 merged and the change is applied to dev." gh
run_case advisory "applied-not-cleared-by-state-query" '{}'

# --- #656: reachability evidence (merge-base) clears applied -> silent -----
build_transcript "The fix is applied to prod." merge-base
run_case silent "applied-with-merge-base-evidence" '{}'

# --- #656: baseRefName field query clears applied -> silent ----------------
build_transcript "변경이 dev 브랜치에 반영됐습니다." baserefname
run_case silent "applied-with-baserefname-evidence" '{}'

# --- #656: negated applied claim -> silent ----------------------------------
build_transcript "아직 prod에 적용되지 않았습니다." none
run_case silent "applied-negated-ko" '{}'

# --- #656: branch token without applied token -> silent ---------------------
build_transcript "Checked out the dev branch and ran the tests." none
run_case silent "branch-token-no-applied-token" '{}'

# --- #656: applied token without any subject -> silent ----------------------
build_transcript "Formatting applied; all files clean." none
run_case silent "applied-token-no-subject" '{}'

# --- #656: mixed merged+applied with state-only evidence -> advisory --------
# gh state query clears "merged" but "applied" survives; advisory text must
# carry the reachability guidance.
build_transcript "PR #543 merged — fix applied to prod." gh
run_case advisory "mixed-kinds-applied-survives" '{}'
printf '%s' "$(python3 -c 'import json,sys; print(json.dumps({"transcript_path": sys.argv[1]}))' "$TRANSCRIPT")" \
  | python3 "$HOOK" 2>/dev/null \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)
msg = d["systemMessage"]
assert "merge-base --is-ancestor" in msg, msg
assert "baseRefName" in msg, msg
assert "applied" in msg, msg
' && { echo "PASS  [applied-advisory-has-reachability-guidance]"; PASS=$((PASS + 1)); } \
  || { echo "FAIL  [applied-advisory-has-reachability-guidance]"; FAIL=$((FAIL + 1)); }

# --- #656: strict mode applied claim -> decision:block ----------------------
build_transcript "Deployed to prod and verified." none
run_case advisory-strict "applied-strict-mode" '{}' PRAXIS_MERGE_CLAIM_STRICT=1

# --- #656 review fix: grep of the baseRefName literal must NOT clear -------
# Bare-token matching would let `grep baseRefName impl.py` (this hook's own
# source contains the literal) silently clear a genuine claim.
build_transcript "The fix is applied to prod." grep-baserefname
run_case advisory "grep-baserefname-does-not-clear" '{}'

# --- #656 review fix: long-flag `git branch --merged --contains` clears ----
build_transcript "The fix is applied to prod." branch-contains-long
run_case silent "branch-contains-long-flag-clears" '{}'

# --- #656 review fix: 'without incident' must NOT suppress applied claim ---
build_transcript "Deployed to prod without incident." none
run_case advisory "applied-without-incident-still-fires" '{}'

# --- #656 review fix: 'released the lock' no longer false-positives --------
build_transcript "Released the lock on the main thread." none
run_case silent "released-lock-prose-silent" '{}'

# --- #656 codex P2: baseRefName-only query (no state field) must NOT clear -
# Knowing the base alone never confirms the PR actually merged; the canonical
# probe is `--json state,baseRefName` in one command.
build_transcript "The fix is applied to prod." baserefname-only
run_case advisory "baserefname-only-does-not-clear" '{}'

# --- CodeRabbit fix: applied-only advisory must NOT carry the (false) -------
# "no fresh state query" sentence when a state query IS present — only the
# applied-specific guidance applies in that shape.
build_transcript "PR #543 merged — fix applied to prod." gh
printf '%s' "$(python3 -c 'import json,sys; print(json.dumps({"transcript_path": sys.argv[1]}))' "$TRANSCRIPT")" \
  | python3 "$HOOK" 2>/dev/null \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)
msg = d["systemMessage"]
assert "applied-on-branch" in msg, msg
assert "no fresh state query" not in msg, msg
' && { echo "PASS  [applied-only-advisory-no-false-sentence]"; PASS=$((PASS + 1)); } \
  || { echo "FAIL  [applied-only-advisory-no-false-sentence]"; FAIL=$((FAIL + 1)); }

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

# =====================================================================
# Negative-polarity persistence claims (issue #869)
# =====================================================================

# --- motivating incident verbatim: still-open + no-loss, zero re-query -----
build_transcript "PR #864 는 여전히 OPEN, 커밋 유실 없음." none
run_case advisory "unchanged-incident-verbatim-kr" '{}'

# --- same claim, cleared by a per-number gh query --------------------------
build_transcript "PR #864 는 여전히 OPEN, 커밋 유실 없음." gh-864
run_case silent "unchanged-cleared-by-matching-number-query" '{}'

# --- same claim, a DIFFERENT number's query does NOT clear it -------------
build_transcript "PR #864 는 여전히 OPEN, 커밋 유실 없음." gh-other
run_case advisory "unchanged-not-cleared-by-other-number-query" '{}'

# --- cleared by a per-number GitHub MCP read -------------------------------
build_transcript "PR #864 는 여전히 OPEN, 커밋 유실 없음." mcp-864
run_case silent "unchanged-cleared-by-mcp-number-read" '{}'

# --- EN "has not been merged" persistence claim ----------------------------
build_transcript "PR #864 has not been merged yet." none
run_case advisory "unchanged-en-not-merged-no-query" '{}'

build_transcript "PR #864 has not been merged yet." gh-864
run_case silent "unchanged-en-not-merged-cleared" '{}'

# --- "no commits lost" phrasing --------------------------------------------
build_transcript "No commits were lost from PR #864." none
run_case advisory "unchanged-no-commits-lost" '{}'

# --- numberless unchanged claim -> silent (deliberate narrowing, #869 scope)
build_transcript "The branch still has no open PR." none
run_case silent "unchanged-numberless-out-of-scope" '{}'

build_transcript "여전히 열려 있습니다." none
run_case silent "unchanged-numberless-kr-out-of-scope" '{}'

# --- mixed message: a cleared "merged" claim alongside an uncleared --------
# unchanged claim for a DIFFERENT number -> advisory carries only the
# unchanged sentence (the generic query clears "merged" but not #864).
# The two claims MUST sit on separate lines: `detect_claims()` is line-scoped
# and the `유실 없음` negation would otherwise disqualify the merged claim
# sharing that line, so the intended path would never be exercised.
build_transcript "PR #497 merged.
PR #864 는 여전히 OPEN, 커밋 유실 없음." gh
run_case advisory-unchanged-only "mixed-merged-cleared-unchanged-not" '{}'

# --- number-collision regressions: a query for #1864 must NOT clear #864 ---
build_transcript "PR #864 는 여전히 OPEN." gh-1864
run_case advisory "unchanged-not-cleared-by-longer-number" '{}'

build_transcript "PR #864 는 여전히 OPEN." mcp-1864
run_case advisory "unchanged-not-cleared-by-longer-number-mcp" '{}'

# --- the number must sit at the READ subcommand's target position ----------
# (codex review on #884): whole-command scanning cleared #864 off an
# unrelated PR whose repo slug carried the digits, and off a mutation that
# says nothing about the post-merge state the claim asserts.
build_transcript "PR #864 는 여전히 OPEN." gh-864-in-slug
run_case advisory "unchanged-not-cleared-by-digits-in-repo-slug" '{}'

build_transcript "PR #864 는 여전히 OPEN." mcp-864-in-owner
run_case advisory "unchanged-not-cleared-by-digits-in-owner" '{}'

build_transcript "PR #864 는 여전히 OPEN." gh-864-merge
run_case advisory "unchanged-not-cleared-by-cli-mutation" '{}'

build_transcript "PR #864 는 여전히 OPEN." mcp-864-merge
run_case advisory "unchanged-not-cleared-by-mcp-mutation" '{}'

build_transcript "PR #864 는 여전히 OPEN." gh-864-url
run_case silent "unchanged-cleared-by-pr-url-read" '{}'

# --- strict mode on an unchanged-only claim --------------------------------
build_transcript "PR #864 는 여전히 OPEN, 커밋 유실 없음." none
run_case advisory-strict "unchanged-strict-mode" '{}' PRAXIS_MERGE_CLAIM_STRICT=1

# --- SubagentStop (issue #1337) --------------------------------------------
#
# On SubagentStop `transcript_path` is the PARENT session's and
# `agent_transcript_path` is the subagent's own. Reading the wrong one does
# not error, it answers about the wrong conversation — so each case below
# makes the two transcripts disagree and asserts the subagent's verdict.

mark_sidechain() {
  # $1 = transcript path; rewrites it with isSidechain: true on every event.
  python3 - "$1" <<'MARKPY'
import json, sys
path = sys.argv[1]
events = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
for e in events:
    e["isSidechain"] = True
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
MARKPY
}

# Parent turn HAS the fresh state query (would be silent); the subagent's does
# not. The advisory must come from the subagent's transcript.
build_transcript "PR #543 머지 완료." gh
SS_PARENT="$TRANSCRIPT"
build_transcript "PR #543 머지 완료." none
SS_AGENT="$TRANSCRIPT"
TRANSCRIPT="$SS_PARENT"
run_case advisory "subagent-stop-grades-the-subagent" \
  "{\"agent_transcript_path\": \"$SS_AGENT\"}"

# Mirror image: the subagent DID query and the parent did not. Reading the
# parent would advise on a clean subagent run.
build_transcript "PR #543 머지 완료." none
SS_PARENT="$TRANSCRIPT"
build_transcript "PR #543 머지 완료." gh
SS_AGENT="$TRANSCRIPT"
TRANSCRIPT="$SS_PARENT"
run_case silent "subagent-stop-clears-on-the-subagents-own-query" \
  "{\"agent_transcript_path\": \"$SS_AGENT\"}"

# The agent transcript may carry isSidechain markers; keeping the filter would
# empty its turn and clear the gate silently — a false clear, so this case is
# built to advise.
build_transcript "PR #543 머지 완료." gh
SS_PARENT="$TRANSCRIPT"
build_transcript "PR #543 머지 완료." none
SS_AGENT="$TRANSCRIPT"
mark_sidechain "$SS_AGENT"
TRANSCRIPT="$SS_PARENT"
run_case advisory "subagent-stop-sidechain-marked-agent-transcript" \
  "{\"agent_transcript_path\": \"$SS_AGENT\"}"

# last_assistant_message is the documented source for the final text; here the
# agent transcript's last message carries no claim and the payload's does.
build_transcript "PR #543 머지 완료." gh
SS_PARENT="$TRANSCRIPT"
build_transcript "리뷰만 했습니다." none
SS_AGENT="$TRANSCRIPT"
TRANSCRIPT="$SS_PARENT"
run_case advisory "subagent-stop-payload-last-assistant-message" \
  "{\"agent_transcript_path\": \"$SS_AGENT\", \"last_assistant_message\": \"PR #543 머지 완료.\"}"

# An agent transcript that has not been flushed resolves to NOTHING. The
# parent's turn here would advise, so a fallback emits a verdict about the
# wrong conversation; with a fresh parent query it would instead CLEAR a
# subagent claim on evidence the subagent never produced (CodeRabbit on #1358).
build_transcript "PR #543 머지 완료." none
run_case silent "subagent-stop-unflushed-agent-transcript-is-not-the-parent" \
  '{"agent_transcript_path": "/nonexistent/subagents/never-written.jsonl"}'

build_transcript "PR #543 머지 완료." gh
run_case silent "subagent-stop-unflushed-cannot-borrow-parent-evidence" \
  '{"agent_transcript_path": "/nonexistent/subagents/never-written.jsonl", "last_assistant_message": "PR #543 머지 완료."}'

# The event name alone keeps the parent's transcript out.
build_transcript "PR #543 머지 완료." none
run_case silent "subagent-stop-without-the-key-still-refuses-the-parent" \
  '{"hook_event_name": "SubagentStop"}'

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
