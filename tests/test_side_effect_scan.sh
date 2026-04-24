#!/bin/bash
# tests/test_side_effect_scan.sh — PreToolUse(Bash) hook coverage
#
# Invokes hooks/side-effect-scan.sh with synthesized hook payloads and asserts
# the hook's decision: "ask" (reason emitted) or "pass" (no output, exit 0).
#
# Run:  ./tests/test_side_effect_scan.sh
# Exit: 0 on success, 1 on first failure (after summary).

set +e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$REPO_ROOT/hooks/side-effect-scan.sh"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expected command [extra_prod_flag]
#   expected: "ask" (hook must return permissionDecision=ask) | "pass" (no output)
run_case() {
  local name="$1" expected="$2" command="$3" prod_expected="${4:-}"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}))' "$command")

  local out
  out=$(echo "$payload" | "$HOOK" 2>/dev/null)
  local rc=$?

  if [ "$rc" -ne 0 ]; then
    echo "FAIL  [$name] hook exited $rc (expected 0)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi

  case "$expected" in
    ask)
      if ! echo "$out" | grep -q '"permissionDecision": "ask"'; then
        echo "FAIL  [$name] expected ask, got: ${out:-<empty>}"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      if [ "$prod_expected" = "prod" ]; then
        if ! echo "$out" | grep -q 'PROD scope'; then
          echo "FAIL  [$name] expected prod emphasis, got: $out"
          FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
        fi
      fi
      ;;
    pass)
      if [ -n "$out" ]; then
        echo "FAIL  [$name] expected pass (no output), got: $out"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      ;;
    *)
      echo "FAIL  [$name] unknown expected: $expected"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac
  echo "PASS  [$name]"
  PASS=$((PASS + 1))
}

# --- detection: git mutation ------------------------------------------------
run_case "git-commit bare"          ask  "git commit -m 'wip'"
run_case "git-merge"                ask  "git merge feature-x"
run_case "git-rebase"               ask  "git rebase -i HEAD~3"
run_case "git-push"                 ask  "git push origin main"

# --- detection: gh remote trigger ------------------------------------------
run_case "gh pr merge"              ask  "gh pr merge 42 --squash"
run_case "gh pr create"             ask  "gh pr create --title x --body y"
run_case "gh workflow run"          ask  "gh workflow run deploy.yml"

# --- detection: kubectl shared write ---------------------------------------
run_case "kubectl apply"            ask  "kubectl apply -f pod.yaml"
run_case "kubectl delete"           ask  "kubectl delete ns my-ns"

# --- detection: known wrapper CLIs that commit internally ------------------
run_case "iceberg-schema migrate"   ask  "iceberg-schema migrate --table users"
run_case "omc ralph"                ask  "omc ralph --task foo"

# --- prod emphasis ----------------------------------------------------------
run_case "git push prod branch"     ask  "git push origin prod"             prod
run_case "kubectl apply --env prod" ask  "kubectl apply -f app.yaml --env prod" prod

# --- negative: benign commands (no side effects) ---------------------------
run_case "git status"               pass "git status"
run_case "git log"                  pass "git log --oneline -5"
run_case "gh pr view"               pass "gh pr view 42"
run_case "kubectl get"              pass "kubectl get pods -n default"
run_case "ls with git in path"      pass "ls /usr/local/git-lfs"
run_case "echo commit word"         pass "echo 'i will commit later'"

# --- opt-out marker ---------------------------------------------------------
run_case "ack bypasses detection"   pass "git push origin main  # side-effect:ack"

# --- shlex-aware evasions --------------------------------------------------
# Without shlex, pipeline-hidden git push would evade naive substring match.
run_case "pipeline hides git push"  ask  "echo foo | git push origin main"
# Compound command in same line — second segment is a mutating git commit.
run_case "compound git-commit"      ask  "make build && git commit -am 'release'"
# Quoted string containing git push should NOT trigger (shlex doesn't expose
# the inner tokens as commands).
run_case "quoted git push literal"  pass "echo \"git push origin main\""
# Subshell form: $(git push ...) — inner command IS still a new command start.
# (Hook detects at token level; $() is opaque to shlex as a single token,
# so this stays pass — a known limitation documented in AGENTS.md.)
run_case "subshell opaque"          pass "echo \$(date)"

# --- operator-adjacent (no surrounding whitespace) — regression guards ----
run_case "git push&&echo"           ask  "git push origin main&&echo ok"
run_case "git push;echo"            ask  "git push origin main;echo ok"
run_case "echo|git push"            ask  "echo x|git push origin main"
run_case "commit&&push chained"     ask  "git commit -am x&&git push"

# --- env / sudo / wrapper prefixes — regression guards --------------------
run_case "env-assign prefix"        ask  "FOO=1 git push origin main"
run_case "env wrapper"              ask  "env GIT_TRACE=1 git commit -m x"
run_case "sudo wrapper"             ask  "sudo kubectl apply -f app.yaml"
run_case "sudo -u user kubectl"     ask  "sudo -u admin kubectl apply -f x.yaml"
run_case "multi-env prefix"         ask  "A=1 B=2 git push origin prod"          prod
run_case "env wrapper no assign"    ask  "env git commit -am x"

# --- wrapper option flags (Codex P2) ---------------------------------------
run_case "env -i flag"              ask  "env -i git push origin main"
run_case "sudo --user long opt"     ask  "sudo --user admin kubectl apply -f x.yaml"
run_case "sudo --user=equals"       ask  "sudo --user=admin kubectl apply -f x.yaml"
run_case "nice -n 10 prefix"        ask  "nice -n 10 git commit -am x"
run_case "stdbuf -oL prefix"        ask  "stdbuf -oL git push"
run_case "sudo -E bare flag"        ask  "sudo -E kubectl delete ns my-ns"
run_case "nested sudo env wrapper"  ask  "sudo -E env GIT_TRACE=1 git push"

# --- shell control-flow (Codex P1) -----------------------------------------
run_case "if-then-git-push"         ask  "if true; then git push origin main; fi"
run_case "for-do-kubectl"           ask  "for x in 1; do kubectl apply -f x.yaml; done"
run_case "while-do-commit"          ask  "while true; do git commit -am x; done"
run_case "if-direct-git-push"       ask  "if git push origin main; then echo ok; fi"
run_case "elif branch"              ask  "if false; then echo x; elif true; then gh pr merge 1; fi"

# --- newline-separated multi-line commands (Codex P1 round 3) --------------
run_case "newline sep git push"     ask  $'echo prep\ngit push origin main'
run_case "newline multi-command"    ask  $'git log\ngit commit -am x\ngit push'
run_case "newline no-sep benign"    pass $'echo hello\necho world'

# --- GNU time wrapper with arg flag (Codex P2 round 3) --------------------
run_case "time -f %E git push"      ask  "time -f %E git push origin main"
run_case "time -o FILE kubectl"     ask  "time -o /tmp/t.log kubectl apply -f x.yaml"
run_case "time --format= git push"  ask  "time --format=%E git push"

# --- non-Bash tool passthrough ---------------------------------------------
non_bash_out=$(echo '{"tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}' | "$HOOK" 2>/dev/null)
if [ -z "$non_bash_out" ]; then
  echo "PASS  [non-Bash tool passthrough]"; PASS=$((PASS + 1))
else
  echo "FAIL  [non-Bash tool passthrough] got: $non_bash_out"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-Bash tool passthrough")
fi

# --- malformed JSON ---------------------------------------------------------
bad_out=$(echo 'not json' | "$HOOK" 2>/dev/null)
bad_rc=$?
if [ "$bad_rc" -eq 0 ] && [ -z "$bad_out" ]; then
  echo "PASS  [malformed JSON fails safe]"; PASS=$((PASS + 1))
else
  echo "FAIL  [malformed JSON] rc=$bad_rc out=$bad_out"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed JSON fails safe")
fi

echo
echo "=========================================="
echo "  PASS: $PASS  FAIL: $FAIL"
echo "=========================================="
if [ "$FAIL" -gt 0 ]; then
  printf '  failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
