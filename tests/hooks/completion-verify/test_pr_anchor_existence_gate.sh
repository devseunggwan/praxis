#!/bin/bash
# Tests for completion-verify/pr-anchor-existence-gate (Stop hook, issue #1113).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/pr-anchor-existence-gate/impl.py"

unset PRAXIS_PR_ANCHOR_BYPASS PRAXIS_PR_ANCHOR_ADVISORY PRAXIS_HOOK_ERROR_STDERR

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
result() { printf '{"message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"%s","is_error":%s,"content":%s}]}}' "$1" "$2" "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$3")"; }

# run_case <advisory|block|silent> <name> <stop_payload_extra_json> [ENV=v ...]
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
assert "[pr-anchor-existence-gate]" in d["systemMessage"], d
assert "decision" not in d, d
' || ok=0
      ;;
    block)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["decision"] == "block", d
assert "[pr-anchor-existence-gate]" in d["reason"], d
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
# Core — created, no post -> 1st Stop advisory
# =====================================================================
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178')]"
run_case advisory "created-no-post-first-fire" '{}'

# created + posted to same PR -> silent
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh pr comment 178 --body-file /tmp/anchor.md'),$(result t2 false ok)]"
run_case silent "created-and-posted" '{}'

# no gh pr create in session -> silent
build_transcript "[$(bash_use t1 'gh pr view 178')]"
run_case silent "no-create" '{}'

# draft create -> silent even with no post
build_transcript "[$(bash_use t1 'gh pr create --draft --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178')]"
run_case silent "draft-create-no-post" '{}'

# -d short flag also counts as draft -> silent
build_transcript "[$(bash_use t1 'gh pr create -d --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178')]"
run_case silent "draft-short-flag" '{}'

# failed create (is_error) -> silent
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 true 'error: some failure')]"
run_case silent "failed-create-excluded" '{}'

# posted to a DIFFERENT PR -> still fires (current PR unanchored)
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh pr comment 456 --body x'),$(result t2 false ok)]"
run_case advisory "posted-to-other-pr" '{}'

# POST gh api issues/comments counts as an anchor post -> silent
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh api --method POST repos/o/r/issues/178/comments -f body=x'),$(result t2 false ok)]"
run_case silent "api-issues-comments-post-counts" '{}'

# GET gh api issues/comments (listing) is NOT a post -> advisory
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh api repos/o/r/issues/178/comments')]"
run_case advisory "api-get-not-a-post" '{}'

# a --draft flag on ANOTHER call in the same compound command must not leak
# into this create's own segment (segmentation guard)
build_transcript "[$(bash_use t1 'gh pr create --title x --body y && echo --draft'),$(result t1 false 'https://github.com/o/r/pull/178')]"
run_case advisory "draft-token-in-sibling-segment-not-leaked" '{}'

# compound gh api calls: a POST on a DIFFERENT PR in the same compound
# command must not clear a GET-only PR's anchor (Codex review finding —
# method/target must pair per invocation, not whole-command)
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh api repos/o/r/issues/178/comments && gh api --method POST repos/o/r/issues/179/comments -f body=x'),$(result t2 false ok)]"
run_case advisory "compound-api-post-on-other-pr-not-credited" '{}'

# compound gh api calls: the POST on THIS PR still counts even when paired
# in a compound command with a GET on another PR (order reversed from above)
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh api --method POST repos/o/r/issues/178/comments -f body=x && gh api repos/o/r/issues/179/comments'),$(result t2 false ok)]"
run_case silent "compound-api-post-on-this-pr-still-credited" '{}'

# flag-before-positional `gh pr comment -b "…" 178` must still be detected as
# a post (CodeRabbit finding, PR #1115 — regex previously required the number
# directly after "comment")
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh pr comment -b anchor-text 178'),$(result t2 false ok)]"
run_case silent "comment-flag-before-positional" '{}'

# --body-file before the positional number must also be detected
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh pr comment --body-file /tmp/anchor.md 178'),$(result t2 false ok)]"
run_case silent "comment-body-file-before-positional" '{}'

# a number embedded inside a flag VALUE (not the positional) must not be
# mistaken for the target PR
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh pr comment -b "closes 999" 178'),$(result t2 false ok)]"
run_case silent "comment-number-in-flag-value-not-mistaken" '{}'

# a `gh pr comment` tool_use with NO matching tool_result at all (interrupted
# before any result landed) must NOT count as posted (CodeRabbit finding,
# PR #1115 — previously `result_is_error.get(tid) is None` fell through as
# "not is_error=True" and was accepted)
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh pr comment 178 --body anchor')]"
run_case advisory "comment-no-tool-result-not-credited" '{}'

# quoted && inside a gh pr comment body must NOT be read as a control operator
# (CodeRabbit finding, PR #1115 — the prior lookahead segmenter truncated the
# command mid-quote and dropped the whole invocation via a shlex ValueError)
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh pr comment -b "verified && ready" 178'),$(result t2 false ok)]"
run_case silent "comment-quoted-control-operator-not-truncated" '{}'

# a quoted && inside a gh pr create body must not swallow a later --draft flag
build_transcript "[$(bash_use t1 'gh pr create --title x --body "a && b" --draft'),$(result t1 false 'https://github.com/o/r/pull/178')]"
run_case silent "create-quoted-control-operator-draft-still-detected" '{}'

# an UNQUOTED && between two gh pr comment invocations must still split them
# (regression guard: quote-awareness must not stop splitting real compounds)
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh pr comment 179 --body x && gh pr comment 178 --body y'),$(result t2 false ok)]"
run_case silent "comment-unquoted-control-operator-still-splits" '{}'

# =====================================================================
# Escalation — advisory once, then block (issue #1113's design)
# =====================================================================
LEDGER=$(mktemp); rm -f "$LEDGER"
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178')]"
run_case advisory "escalation-first-stop-advisory" '{"session_id":"sESC1"}' PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER"
run_case block "escalation-second-stop-blocks" '{"session_id":"sESC1"}' PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER"

# forced advisory pins even on repeat fires
LEDGER2=$(mktemp); rm -f "$LEDGER2"
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/179')]"
run_case advisory "advisory-env-first" '{"session_id":"sESC2"}' PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER2" PRAXIS_PR_ANCHOR_ADVISORY=1
run_case advisory "advisory-env-pins-repeat" '{"session_id":"sESC2"}' PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER2" PRAXIS_PR_ANCHOR_ADVISORY=1

# =====================================================================
# Tiers / env
# =====================================================================
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178')]"
run_case silent "bypass-env" '{}' PRAXIS_PR_ANCHOR_BYPASS=1

build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178')]"
run_case silent "stop-hook-active" '{"stop_hook_active": true}'

# =====================================================================
# =====================================================================
# Issue #1250 — newline boundaries and leading assignments
# =====================================================================

# the post on a SECOND LINE of the same command -> silent
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'gh pr view 178
gh pr comment 178 --body-file /tmp/anchor.md'),$(result t2 false ok)]"
run_case silent "newline-separates-invocations" '{}'

# a leading VAR=value assignment must not hide the invocation -> silent
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'SP=/tmp/s gh pr comment 178 --body-file /tmp/anchor.md'),$(result t2 false ok)]"
run_case silent "leading-assignment-stripped" '{}'

# a --draft on a later LINE must not leak into this create's own segment
build_transcript "[$(bash_use t1 'gh pr create --title x --body y
echo --draft'),$(result t1 false 'https://github.com/o/r/pull/178')]"
run_case advisory "draft-token-on-later-line-not-leaked" '{}'

# a gh pr comment line that exists only inside a HEREDOC BODY never ran -> advisory
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'cat <<EOF > /tmp/a.md
gh pr comment 178 --body x
EOF'),$(result t2 false ok)]"
run_case advisory "heredoc-body-command-not-credited" '{}'

# ... and a real post after the heredoc terminator still counts -> silent
build_transcript "[$(bash_use t1 'gh pr create --title x --body y'),$(result t1 false 'https://github.com/o/r/pull/178'),$(bash_use t2 'cat <<EOF > /tmp/a.md
body
EOF
gh pr comment 178 --body-file /tmp/a.md'),$(result t2 false ok)]"
run_case silent "post-after-heredoc-terminator-counts" '{}'

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
