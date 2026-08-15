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

# ---- read-gate escalation (issue #1012) ---------------------------------------
#
# From the (N+1)-th block of this guard in one session the message escalates to
# "Read <spec.md> first". The prior-block count comes from the fire ledger's RICH
# rows, so the fixtures seed that ledger in the exact shape record_group_fires
# writes — and one case drives the REAL dispatcher, which is the only thing that
# proves writer and reader still agree on that shape.
#
# Both directions are pinned: the gate must fire on the nth un-Read block, and it
# must stay silent below the threshold, after a Read, and when its own referent
# does not resolve (the mandatory fail-open escape — a gate that cannot find its
# spec must not demand it be read).

RG_DIR="$(mktemp -d)" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$RG_DIR"' EXIT
RG_SPEC="$REPO_ROOT/hooks/preflight-gate/foreground-poll-loop-guard/spec.md"
RG_MD_POST="$REPO_ROOT/hooks/postuse-correction/pre-edit-md-escape-advisory/impl.py"
RG_LOOP='while true; do gh pr checks 7; sleep 20; done'

# seed_blocks <ledger> <session_id> <count> — RICH block rows for this guard.
seed_blocks() {
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
ledger, sid, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
with open(ledger, "a", encoding="utf-8") as fh:
    for _ in range(n):
        fh.write(json.dumps({
            "timestamp": "2026-01-01T00:00:00+00:00",
            "session_id": sid,
            "tool": "Bash",
            "hook": "foreground-poll-loop-guard",
            "role": "preflight-gate",
            "decision": "block",
            "granularity": "rich",
        }) + "\n")
PY
}

# rg_payload <session_id> [bg]
rg_payload() {
  python3 - "$1" "${2:-}" "$RG_LOOP" <<'PY'
import json, sys
sid, bg, cmd = sys.argv[1], sys.argv[2], sys.argv[3]
tool_input = {"command": cmd}
if bg == "bg":
    tool_input["run_in_background"] = True
print(json.dumps({"session_id": sid, "tool_name": "Bash", "tool_input": tool_input}))
PY
}

# rg_run <ledger> <history> <payload> [spec_override] — stderr of one guard run.
rg_run() {
  local ledger="$1" history="$2" payload="$3" spec_override="${4:-}"
  (
    export PRAXIS_FIRE_TELEMETRY_FILE="$ledger"
    export PRAXIS_MD_READ_HISTORY_FILE="$history"
    if [ -n "$spec_override" ]; then export PRAXIS_POLL_LOOP_GUARD_SPEC="$spec_override"; fi
    # Braces, not a bare `2>&1 >/dev/null`: both orderings capture stderr and
    # drop stdout, but shellcheck reads the bare form as a likely mistake
    # (SC2069) and the CI shellcheck job is blocking.
    { printf '%s' "$payload" | "$HOOK" >/dev/null; } 2>&1
  )
}

# rg_assert <name> <gate|nogate> <expected_rc> <actual_rc> <stderr>
rg_assert() {
  local name="$1" want="$2" want_rc="$3" rc="$4" err="$5"
  if [ "$rc" -ne "$want_rc" ]; then
    echo "FAIL  [$name] expected exit $want_rc, got $rc (stderr: ${err:-<empty>})"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi
  if [ "$want" = "gate" ] && ! echo "$err" | grep -q 'READ-GATE'; then
    echo "FAIL  [$name] expected READ-GATE escalation, got: ${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi
  if [ "$want" = "nogate" ] && echo "$err" | grep -q 'READ-GATE'; then
    echo "FAIL  [$name] expected NO escalation, got: ${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi
  PASS=$((PASS + 1)); echo "ok    [$name]"
}

# The block message must name the spec by ABSOLUTE path: it is both what the
# agent is told to read and the key the read-set is looked up by, and a
# repo-relative string resolves nowhere outside a praxis checkout.
err=$(rg_run "$RG_DIR/ref.jsonl" "$RG_DIR/ref-history.json" "$(rg_payload ref-sess)"); rc=$?
if [ "$rc" -eq 2 ] && echo "$err" | grep -qF "Reference: $RG_SPEC"; then
  PASS=$((PASS + 1)); echo "ok    [readgate-reference-is-absolute-spec]"
else
  echo "FAIL  [readgate-reference-is-absolute-spec] got: ${err:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("readgate-reference-is-absolute-spec")
fi

# 1 prior block is below the default threshold of 2 → base block only.
seed_blocks "$RG_DIR/below.jsonl" below-sess 1
err=$(rg_run "$RG_DIR/below.jsonl" "$RG_DIR/below-history.json" "$(rg_payload below-sess)"); rc=$?
rg_assert "readgate-below-threshold-silent" nogate 2 "$rc" "$err"

# 2 prior blocks, spec never Read → the 3rd block escalates.
seed_blocks "$RG_DIR/nth.jsonl" nth-sess 2
err=$(rg_run "$RG_DIR/nth.jsonl" "$RG_DIR/nth-history.json" "$(rg_payload nth-sess)"); rc=$?
rg_assert "readgate-nth-block-demands-read" gate 2 "$rc" "$err"

# Same state, but the spec was Read this session (recorded by the read-set owner
# pre-edit-md-escape-advisory, not by a second mechanism) → escalation released.
seed_blocks "$RG_DIR/read.jsonl" read-sess 2
printf '{"session_id":"read-sess","tool_name":"Read","tool_input":{"file_path":"%s"}}' "$RG_SPEC" \
  | PRAXIS_MD_READ_HISTORY_FILE="$RG_DIR/read-history.json" python3 "$RG_MD_POST" post >/dev/null 2>&1
err=$(rg_run "$RG_DIR/read.jsonl" "$RG_DIR/read-history.json" "$(rg_payload read-sess)"); rc=$?
rg_assert "readgate-released-after-read" nogate 2 "$rc" "$err"

# A Read of some OTHER .md must not satisfy the gate (the read-set is keyed by
# path — without this the previous case would pass for the wrong reason).
seed_blocks "$RG_DIR/other.jsonl" other-sess 2
printf '{"session_id":"other-sess","tool_name":"Read","tool_input":{"file_path":"%s"}}' "$RG_DIR/unrelated.md" \
  | PRAXIS_MD_READ_HISTORY_FILE="$RG_DIR/other-history.json" python3 "$RG_MD_POST" post >/dev/null 2>&1
err=$(rg_run "$RG_DIR/other.jsonl" "$RG_DIR/other-history.json" "$(rg_payload other-sess)"); rc=$?
rg_assert "readgate-other-md-read-does-not-satisfy" gate 2 "$rc" "$err"

# Unresolvable referent → fail OPEN to the base block. Deadlock escape: the gate
# must never demand a Read of a file that is not there.
seed_blocks "$RG_DIR/noref.jsonl" noref-sess 2
err=$(rg_run "$RG_DIR/noref.jsonl" "$RG_DIR/noref-history.json" "$(rg_payload noref-sess)" \
  "$RG_DIR/absent/spec.md"); rc=$?
rg_assert "readgate-unresolvable-reference-fails-open" nogate 2 "$rc" "$err"

# No session_id → nothing to count against → base block.
seed_blocks "$RG_DIR/nosid.jsonl" nosid-sess 2
err=$(rg_run "$RG_DIR/nosid.jsonl" "$RG_DIR/nosid-history.json" \
  '{"tool_name":"Bash","tool_input":{"command":"while true; do gh pr checks 7; sleep 20; done"}}'); rc=$?
rg_assert "readgate-no-session-id-silent" nogate 2 "$rc" "$err"

# The escalation only ever rewrites the message of a command that was already
# being blocked — an armed gate must not deny a correct retry.
seed_blocks "$RG_DIR/bg.jsonl" bg-sess 5
err=$(rg_run "$RG_DIR/bg.jsonl" "$RG_DIR/bg-history.json" "$(rg_payload bg-sess bg)"); rc=$?
rg_assert "readgate-armed-still-passes-background" nogate 0 "$rc" "$err"

# End to end: the REAL dispatcher writes the rows the read-gate reads. This is
# the case that fails if record_group_fires' record shape ever drifts from
# count_session_fires' filter.
(
  export PRAXIS_FIRE_TELEMETRY_FILE="$RG_DIR/e2e.jsonl"
  export PRAXIS_MD_READ_HISTORY_FILE="$RG_DIR/e2e-history.json"
  export PRAXIS_HOME="$RG_DIR/e2e-home"   # contain every hook's state writes
  for _ in 1 2; do
    rg_payload e2e-sess | python3 "$REPO_ROOT/hooks/_lib/_dispatch.py" PreToolUse Bash >/dev/null 2>&1
  done
)
err=$(rg_run "$RG_DIR/e2e.jsonl" "$RG_DIR/e2e-history.json" "$(rg_payload e2e-sess)"); rc=$?
rg_assert "readgate-dispatcher-written-count-escalates" gate 2 "$rc" "$err"

# ---- summary ------------------------------------------------------------------
echo ""
echo "passed: $PASS  failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "failed cases: ${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
