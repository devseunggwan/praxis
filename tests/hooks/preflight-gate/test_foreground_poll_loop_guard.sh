#!/bin/bash
# tests/hooks/preflight-gate/test_foreground_poll_loop_guard.sh — PreToolUse(Bash) hook coverage
#
# Invokes the foreground-poll-loop-guard impl.py with synthesized hook payloads
# and asserts the hook's decision: "block" (exit 2 + BLOCKED message on stderr)
# or "pass" (exit 0, no stderr).
#
# Run:  ./tests/hooks/preflight-gate/test_foreground_poll_loop_guard.sh
# Exit: 0 on success, 1 on first failure (after summary).

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/preflight-gate/foreground-poll-loop-guard/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expected command [background]
#   expected: "block" (exit 2 + BLOCKED on stderr) | "pass" (exit 0)
#   background: "bg" → payload carries run_in_background: true
run_case() {
  local name="$1" expected="$2" command="$3" background="${4:-}"

  local payload
  payload=$(python3 -c '
import json, sys
tool_input = {"command": sys.argv[1]}
if len(sys.argv) > 2 and sys.argv[2] == "bg":
    tool_input["run_in_background"] = True
print(json.dumps({"tool_name": "Bash", "tool_input": tool_input}))' "$command" "$background")

  local err
  err=$(echo "$payload" | "$HOOK" 2>&1 >/dev/null)
  local rc=$?

  case "$expected" in
    block)
      if [ "$rc" -ne 2 ]; then
        echo "FAIL  [$name] expected exit 2, got $rc (stderr: ${err:-<empty>})"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      if ! echo "$err" | grep -q 'FOREGROUND POLL-LOOP GUARD blocked'; then
        echo "FAIL  [$name] exit 2 but message missing: ${err:-<empty>}"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      ;;
    pass)
      if [ "$rc" -ne 0 ]; then
        echo "FAIL  [$name] expected exit 0, got $rc (stderr: ${err:-<empty>})"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      ;;
  esac
  PASS=$((PASS + 1))
  echo "ok    [$name]"
}

# ---- block: bounded for-loops whose worst-case >= 100s ----------------------
run_case "bounded-seq-1-40-x3" block \
  'for i in $(seq 1 40); do gh pr checks 777; sleep 3; done'
run_case "bounded-seq-20-x20" block \
  'for i in $(seq 20); do curl -s "$URL"; sleep 20; done'
run_case "bounded-brace-30-x5" block \
  'for i in {1..30}; do kubectl get pod app; sleep 5; done'
run_case "bounded-sleeps-sum-per-iteration" block \
  'for i in $(seq 1 6); do sleep 9; work; sleep 9; done'
run_case "bounded-seq-step-form" block \
  'for i in $(seq 0 2 100); do sleep 3; done'
run_case "bounded-c-style-for" block \
  'for ((i=0;i<40;i++)); do gh pr checks 7; sleep 3; done'
run_case "bounded-literal-list-25" block \
  'for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25; do curl x; sleep 5; done'
run_case "bounded-minute-suffix" block \
  'for i in $(seq 1 5); do foo; sleep 2m; done'

# ---- block: unbounded while/until + sleep ------------------------------------
run_case "unbounded-while-true" block \
  'while true; do gh pr checks 777; sleep 20; done'
run_case "unbounded-until-cond" block \
  'until aws cloudformation describe-stacks --stack-name s | grep -q COMPLETE; do sleep 15; done'
run_case "unbounded-until-pipe" block \
  'until ! gh pr checks 777 | grep -q pending; do sleep 20; done'
run_case "unbounded-multiline" block \
  'while true; do
  gh run view "$RUN_ID" --json status
  sleep 30
done'
run_case "unbounded-masked-by-short-for" block \
  'for i in $(seq 1 3); do echo tick; sleep 1; done; while true; do sleep 30; done'
run_case "unbounded-minute-suffix" block \
  'while true; do poll; sleep 1m; done'
run_case "unbounded-nested-in-for" block \
  'for i in $(seq 1 2); do while true; do sleep 10; done; done'
run_case "unbounded-path-invoked-sleep" block \
  'while true; do /bin/sleep 20; done'
run_case "bounded-descending-brace" block \
  'for i in {30..1}; do kubectl get pod; sleep 5; done'
run_case "unbounded-echo-done-no-early-close" block \
  'while true; do echo done; sleep 30; done'
run_case "unbounded-quoted-done-argument" block \
  'while true; do echo "done"; sleep 30; done'
run_case "bounded-keywords-as-list-words" block \
  'for i in do done while; do sleep 50; done'

# ---- pass: short bounded / background / consumers / no-sleep ------------------
run_case "bounded-short-90s" pass \
  'for i in $(seq 1 5); do gh pr checks 777; sleep 18; done'
run_case "backgrounded-poll" pass \
  'while true; do gh pr checks 777; sleep 20; done' bg
run_case "while-read-consumer" pass \
  'tail -f app.log | while read line; do echo "$line"; sleep 1; done'
run_case "while-ifs-read-consumer" pass \
  'while IFS= read -r line; do process "$line"; sleep 1; done < input.txt'
run_case "no-sleep-loop" pass \
  'for f in *.py; do ruff check "$f"; done'
run_case "leading-sleep-no-loop" pass \
  'sleep 2 && gh pr view 777'
run_case "sleep-var-unparseable" pass \
  'while true; do poll; sleep $INTERVAL; done'

# ---- pass: per-loop scoping — sleep outside a loop's own body ------------------
run_case "scoped-while-breaks-sleep-elsewhere" pass \
  'while true; do echo hi; break; done; for i in $(seq 1 5); do sleep 18; done'
run_case "scoped-trailing-sleep-not-in-loop" pass \
  'until false; do echo x; break; done; sleep 200'
run_case "scoped-count-and-sleep-different-loops" pass \
  'for i in $(seq 1 50); do build $i; done; for j in $(seq 1 2); do notify; sleep 10; done'
run_case "scoped-seq-a-b-real-count" pass \
  'for i in $(seq 30 32); do foo; sleep 4; done'
run_case "literal-list-short" pass \
  'for i in 1 2 3; do sleep 5; done'
run_case "c-style-narrow-window" pass \
  'for ((i=100;i<105;i++)); do sleep 1; done'
run_case "list-with-substitution-fail-open" pass \
  'for x in $(printf %s only); do sleep 40; done'

# ---- pass: non-executable text (comments, heredoc bodies) ----------------------
run_case "trailing-comment-loop-text" pass \
  'echo hi # while true; do sleep 20; done'
run_case "heredoc-body-loop-text" pass \
  'cat <<'"'"'EOF'"'"'
while true; do sleep 20; done
EOF'
run_case "heredoc-then-real-loop-still-blocks" block \
  'cat <<'"'"'EOF'"'"'
sample text
EOF
while true; do poll; sleep 20; done'
run_case "heredoc-tilde-delimiter" pass \
  'cat <<~EOF
while true; do sleep 20; done
~EOF'

# ---- pass: quoted literals must not trigger (token-based detection) ----------
run_case "quoted-commit-message" pass \
  'git commit -m "retry while deploy sleeps 30 until done"'
run_case "quoted-loop-in-echo" pass \
  'echo "while true; do sleep 5; done"'
run_case "quoted-seq-no-downgrade" block \
  'for i in $(seq 1 40); do log "seq 1 2"; sleep 3; done'

# ---- pass: non-Bash / malformed / empty / bypass ------------------------------
payload='{"tool_name": "Write", "tool_input": {"file_path": "x", "content": "while true; do sleep 9; done"}}'
err=$(echo "$payload" | "$HOOK" 2>&1 >/dev/null); rc=$?
if [ "$rc" -eq 0 ]; then PASS=$((PASS + 1)); echo "ok    [non-bash-tool]"; else
  echo "FAIL  [non-bash-tool] expected exit 0, got $rc"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-bash-tool"); fi

err=$(echo 'not json' | "$HOOK" 2>&1 >/dev/null); rc=$?
if [ "$rc" -eq 0 ]; then PASS=$((PASS + 1)); echo "ok    [malformed-stdin]"; else
  echo "FAIL  [malformed-stdin] expected exit 0, got $rc"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed-stdin"); fi

run_case "empty-command" pass ''

err=$(echo '{"tool_name": "Bash", "tool_input": {"command": "while true; do sleep 20; done"}}' \
  | PRAXIS_HOOK_BYPASS_POLL_LOOP_GUARD=1 "$HOOK" 2>&1 >/dev/null); rc=$?
if [ "$rc" -eq 0 ]; then PASS=$((PASS + 1)); echo "ok    [env-bypass]"; else
  echo "FAIL  [env-bypass] expected exit 0, got $rc"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("env-bypass"); fi

# ---- summary ------------------------------------------------------------------
echo ""
echo "passed: $PASS  failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "failed cases: ${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
