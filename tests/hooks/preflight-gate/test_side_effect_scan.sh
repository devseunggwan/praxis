#!/bin/bash
# tests/test_side_effect_scan.sh — PreToolUse(Bash) hook coverage
#
# Invokes the side-effect-scan impl.py with synthesized hook payloads and asserts
# the hook's decision: "ask" (permissionDecision JSON on stdout), "advise"
# (stderr text, no stdout — issue #874's git-commit tier), or "pass" (silent on
# both streams, exit 0).
#
# Run:  ./tests/test_side_effect_scan.sh
# Exit: 0 on success, 1 on first failure (after summary).

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/preflight-gate/side-effect-scan/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expected command [extra_prod_flag]
#   expected: "ask"    — permissionDecision=ask on stdout
#             "advise" — issue #874's ADVISE tier: stderr text, stdout empty
#             "pass"   — silent on BOTH streams
#
# `pass` checks stderr too since #874. Before the advise tier existed, stderr
# was discarded here (`2>/dev/null`) and "pass" meant only "no ask" — which
# would now let an advisory leaking onto `git status` go unnoticed.
run_case() {
  local name="$1" expected="$2" command="$3" prod_expected="${4:-}"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}))' "$command")

  local err_file out rc err
  err_file=$(mktemp)
  out=$(echo "$payload" | "$HOOK" 2>"$err_file")
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"

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
    advise)
      if [ -n "$out" ]; then
        echo "FAIL  [$name] expected advise (no stdout), got: $out"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      if ! echo "$err" | grep -q '\[side-effect-scan\]'; then
        echo "FAIL  [$name] expected advise on stderr, got: ${err:-<empty>}"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      if [ "$prod_expected" = "prod" ]; then
        if ! echo "$err" | grep -q 'PROD scope'; then
          echo "FAIL  [$name] expected prod emphasis on stderr, got: $err"
          FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
        fi
      fi
      ;;
    pass)
      if [ -n "$out" ]; then
        echo "FAIL  [$name] expected pass (no output), got: $out"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      if [ -n "$err" ]; then
        echo "FAIL  [$name] expected pass (silent), got stderr: $err"
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

# Assert a fire carries the RIGHT reason. `run_case ... ask` only checks that
# something fired, so it would still pass if the hook picked the wrong category —
# which is precisely what #985 is about. Used where the category is the point.
#
# `channel` (5th arg, default "ask") selects which stream carries the reason:
# "ask" reads stdout, "advise" reads stderr and additionally requires stdout to
# be empty (issue #874 — the demoted tier must not also raise a prompt).
run_case_reason() {
  local name="$1" want="$2" forbid="$3" command="$4" channel="${5:-ask}"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}))' "$command")

  local err_file out rc err reason
  err_file=$(mktemp)
  out=$(echo "$payload" | "$HOOK" 2>"$err_file")
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"

  if [ "$rc" -ne 0 ]; then
    echo "FAIL  [$name] hook exited $rc (expected 0)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi
  if [ "$channel" = "advise" ]; then
    if [ -n "$out" ]; then
      echo "FAIL  [$name] expected advise (no stdout), got: $out"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
    fi
    if ! echo "$err" | grep -q '\[side-effect-scan\]'; then
      echo "FAIL  [$name] expected advise on stderr, got: ${err:-<empty>}"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
    fi
    reason="$err"
  else
    if ! echo "$out" | grep -q '"permissionDecision": "ask"'; then
      echo "FAIL  [$name] expected ask, got: ${out:-<empty>}"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
    fi
    reason="$out"
  fi
  if ! echo "$reason" | grep -qF "$want"; then
    echo "FAIL  [$name] reason missing '$want', got: $reason"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi
  if [ -n "$forbid" ] && echo "$reason" | grep -qiF "$forbid"; then
    echo "FAIL  [$name] reason must not mention '$forbid', got: $reason"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi
  echo "PASS  [$name]"
  PASS=$((PASS + 1))
}

# --- detection: git mutation ------------------------------------------------
# git-commit is ADVISE since issue #874, and on reversibility alone since
# issue #1153: no remote ref moves and the pre-command HEAD is recoverable from
# the same shell. A repository's own git hooks can still reach the network, so
# that is a property of plain git, not of every checkout. The
# eight sibling commit hooks that gate this argv are context, not ground.
# git-push stays ASK (publishes shared state).
run_case "git-commit bare"          advise "git commit -m 'wip'"
run_case "git-merge"                advise "git merge feature-x"
run_case "git-rebase"               advise "git rebase -i HEAD~3"
run_case "git-cherry-pick"          advise "git cherry-pick abc1234"
run_case "git-revert"               advise "git revert abc1234"
run_case "git-push"                 ask  "git push origin main"

# --- detection: gh remote trigger ------------------------------------------
run_case "gh pr merge"              ask  "gh pr merge 42 --squash"
# #1092: a path-prefixed gh binary must not slip past the side-effect scan.
# `argv[0] == "gh"` exact match (in _gh_subcommand_pair) previously let
# `/usr/bin/gh pr merge` bypass; `_is_gh_binary` closes it.
run_case "gh pr merge path-prefixed"  ask  "/usr/bin/gh pr merge 42 --squash"
run_case "gh pr create"             ask  "gh pr create --title x --body y"
run_case "gh workflow run"          ask  "gh workflow run deploy.yml"

# issue #514 결함1 — a leading gh global flag shifts the subcommand off the
# fixed argv[1]/argv[2] positions the old detector assumed, so these forms
# were undetected (no ask). The flag-aware walk must still ask.
run_case "514: gh --repo pr merge"     ask  "gh --repo owner/repo pr merge --squash"
run_case "514: gh -R pr merge"         ask  "gh -R owner/repo pr merge"
run_case "514: gh --repo= pr merge"    ask  "gh --repo=owner/repo pr merge"
run_case "514: gh -R pr create"        ask  "gh -R owner/repo pr create --title x"
run_case "514: gh -R workflow run"     ask  "gh -R owner/repo workflow run deploy.yml"

# --- detection: kubectl shared write ---------------------------------------
run_case "kubectl apply"            ask  "kubectl apply -f pod.yaml"
run_case "kubectl delete"           ask  "kubectl delete ns my-ns"

# --- detection: configured wrapper CLIs that commit internally -------------
# Issue #874 split these out of git-commit and left them at ASK: the eight
# sibling gates match a literal `git commit` argv, so a commit made inside a
# wrapper process is invisible to all of them. Since #1157 the pattern list
# comes from PRAXIS_WRAPPER_COMMIT_CMDS (shipped default empty).
export PRAXIS_WRAPPER_COMMIT_CMDS="iceberg-schema migrate|promote, omc ralph"
run_case_reason "iceberg-schema migrate (env-configured)" "[wrapper-commit]" "" \
  "iceberg-schema migrate --table users"
run_case_reason "iceberg-schema promote (env-configured)" "[wrapper-commit]" "" \
  "iceberg-schema promote --table users"
run_case_reason "omc ralph (env-configured)"              "[wrapper-commit]" "" \
  "omc ralph --task foo"
run_case "wrapper cmd, sub not in configured set (pass)" pass \
  "iceberg-schema describe --table users"
unset PRAXIS_WRAPPER_COMMIT_CMDS

# Negative control for #1157: with the env unset the shipped default is an
# empty pattern list — the same commands must pass silently.
run_case "iceberg-schema migrate, env unset (pass)" pass \
  "iceberg-schema migrate --table users"
run_case "omc ralph, env unset (pass)" pass \
  "omc ralph --task foo"

# --- prod emphasis ----------------------------------------------------------
run_case "git push prod branch"     ask  "git push origin prod"             prod
run_case "kubectl apply --env prod" ask  "kubectl apply -f app.yaml --env prod" prod

# --- negative: benign commands (no side effects) ---------------------------
run_case "git status"               pass "git status"
run_case "git log"                  pass "git log --oneline -5"
run_case "gh pr view"               pass "gh pr view 42"
run_case "514: gh -R pr view (neg)"  pass "gh -R owner/repo pr view 42"
run_case "kubectl get"              pass "kubectl get pods -n default"
run_case "ls with git in path"      pass "ls /usr/local/git-lfs"
run_case "echo commit word"         pass "echo 'i will commit later'"

# --- opt-out marker ---------------------------------------------------------
run_case "ack bypasses detection"   pass "git push origin main  # side-effect:ack"

# --- shlex-aware evasions --------------------------------------------------
# Without shlex, pipeline-hidden git push would evade naive substring match.
run_case "pipeline hides git push"  ask  "echo foo | git push origin main"
# Compound command in same line — second segment is a mutating git commit.
run_case "compound git-commit"      advise "make build && git commit -am 'release'"
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
run_case "env wrapper"              advise "env GIT_TRACE=1 git commit -m x"
run_case "sudo wrapper"             ask  "sudo kubectl apply -f app.yaml"
run_case "sudo -u user kubectl"     ask  "sudo -u admin kubectl apply -f x.yaml"
run_case "multi-env prefix"         ask  "A=1 B=2 git push origin prod"          prod
run_case "env wrapper no assign"    advise "env git commit -am x"

# --- wrapper option flags (Codex P2) ---------------------------------------
run_case "env -i flag"              ask  "env -i git push origin main"
run_case "sudo --user long opt"     ask  "sudo --user admin kubectl apply -f x.yaml"
run_case "sudo --user=equals"       ask  "sudo --user=admin kubectl apply -f x.yaml"
run_case "nice -n 10 prefix"        advise "nice -n 10 git commit -am x"
run_case "stdbuf -oL prefix"        ask  "stdbuf -oL git push"
run_case "sudo -E bare flag"        ask  "sudo -E kubectl delete ns my-ns"
run_case "nested sudo env wrapper"  ask  "sudo -E env GIT_TRACE=1 git push"

# --- shell control-flow (Codex P1) -----------------------------------------
run_case "if-then-git-push"         ask  "if true; then git push origin main; fi"
run_case "for-do-kubectl"           ask  "for x in 1; do kubectl apply -f x.yaml; done"
run_case "while-do-commit"          advise "while true; do git commit -am x; done"
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

# --- CMUX_DELEGATE is inert (issue #1055) ---------------------------------
# #180 let the gh-merge category pass silently under CMUX_DELEGATE=1. #1055
# removed that bypass — nothing ever set the variable, and the delegated worker
# now runs with a live stdin, so it answers prompts like any other session.
# These cases keep the env var set and assert every category decides normally.

cmux_run() {
  local name="$1" expected="$2" command="$3"
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}))' "$command")
  local err_file out rc err
  err_file=$(mktemp)
  out=$(echo "$payload" | CMUX_DELEGATE=1 "$HOOK" 2>"$err_file")
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"
  if [ "$rc" -ne 0 ]; then
    echo "FAIL  [$name] hook exited $rc"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi
  case "$expected" in
    advise)
      if [ -n "$out" ] || ! echo "$err" | grep -q '\[side-effect-scan\]'; then
        echo "FAIL  [$name] expected advise under CMUX_DELEGATE=1, got stdout: ${out:-<empty>} stderr: ${err:-<empty>}"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
      else
        echo "PASS  [$name]"; PASS=$((PASS + 1))
      fi
      ;;
    ask)
      if ! echo "$out" | grep -q '"permissionDecision": "ask"'; then
        echo "FAIL  [$name] expected ask under CMUX_DELEGATE=1, got: ${out:-<empty>}"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
      else
        echo "PASS  [$name]"; PASS=$((PASS + 1))
      fi
      ;;
  esac
}

cmux_run "delegate gh pr merge now asks"      ask  "gh pr merge 1 --squash"
cmux_run "delegate gh -R pr merge now asks"   ask  "gh -R owner/repo pr merge 1"
cmux_run "delegate gh --repo pr merge asks"   ask  "gh --repo owner/repo pr merge 1"
cmux_run "delegate gh pr create still asks"   ask  "gh pr create --title x --body y"
cmux_run "delegate gh workflow run still ask" ask  "gh workflow run deploy.yml"
cmux_run "delegate git push still asks"       ask  "git push origin main"
# Issue #874: git-commit is ADVISE tier — a delegate session must still SEE the
# commit advisory on stderr, not have it silenced.
cmux_run "delegate git commit still advises" advise "git commit -m wip"
cmux_run "delegate kubectl apply still ask"   ask  "kubectl apply -f x.yaml"

# --- compound cascade-hint suffix (issue #229) -----------------------------
# When the ask fires on a compound command containing a state-changing step
# (mkdir, redirect, tee, curl -o, …), the ask reason must include the shared
# cascade advisory text from _hook_utils.compound_cascade_hint.

_hint_payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))
' 'mkdir -p /tmp/staged && git push origin main')
_hint_out=$(echo "$_hint_payload" | "$HOOK" 2>/dev/null)
if echo "$_hint_out" | grep -q "PreToolUse rejection (block or denied ask) aborts ALL parts atomically"; then
  echo "PASS  [cascade hint on compound git push ask]"; PASS=$((PASS + 1))
else
  echo "FAIL  [cascade hint missing on compound git push ask]"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("cascade hint compound git push")
fi

# Single-command ask (no compound chain) → reason must NOT carry the cascade hint
_single_payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))
' 'git push origin main')
_single_out=$(echo "$_single_payload" | "$HOOK" 2>/dev/null)
if echo "$_single_out" | grep -q "PreToolUse rejection (block or denied ask) aborts ALL parts atomically"; then
  echo "FAIL  [cascade hint leaked on single-command ask]"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("cascade hint leaked single command")
else
  echo "PASS  [no cascade hint on single-command ask]"; PASS=$((PASS + 1))
fi

# --- #874 tier boundary ----------------------------------------------------
#
# Both directions. The demotion must reduce prompts for a local commit AND
# must not reduce them for anything the commit is chained to — a test that
# only pins the quiet half would let the loud half go quiet unnoticed.

# Mixed match → ASK, and the ask reason must still name BOTH categories. The
# `ask` expectation alone would pass even if the demoted category were dropped
# from the reason entirely.
_mixed_out=$(python3 -c '
import json, sys
print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))
' 'git commit -am x && git push origin main' | "$HOOK" 2>/dev/null)
if echo "$_mixed_out" | grep -q '"permissionDecision": "ask"' \
   && echo "$_mixed_out" | grep -qF '[git-commit]' \
   && echo "$_mixed_out" | grep -qF '[git-push]'; then
  echo "PASS  [874: commit+push mixed match stays ask with both categories]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [874: commit+push mixed match] got: ${_mixed_out:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("874: commit+push mixed match")
fi

# The advisory must NOT carry the compound cascade hint: that text describes
# what a DENIED PreToolUse decision does to the rest of the chain (issue #229),
# and an advisory cannot be denied. Positive control immediately below proves
# the same command still produces the advisory at all.
_adv_err=$(python3 -c '
import json, sys
print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))
' 'mkdir -p /tmp/staged && git commit -am x' | "$HOOK" 2>&1 >/dev/null)
if echo "$_adv_err" | grep -q "PreToolUse rejection (block or denied ask) aborts ALL parts atomically"; then
  echo "FAIL  [874: cascade hint leaked into the advisory] got: $_adv_err"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("874: cascade hint leaked into advisory")
elif echo "$_adv_err" | grep -qF '[git-commit]'; then
  echo "PASS  [874: compound commit advises without the cascade hint]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [874: compound commit produced no advisory] got: ${_adv_err:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("874: compound commit advisory missing")
fi

# PROD emphasis survives the demotion — it just rides the advisory now.
run_case "874: prod-scoped commit advises with PROD prefix" advise \
  "git commit -m prod" prod

# --- non-Bash tool passthrough ---------------------------------------------
non_bash_out=$(echo '{"tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}' | "$HOOK" 2>/dev/null)
if [ -z "$non_bash_out" ]; then
  echo "PASS  [non-Bash tool passthrough]"; PASS=$((PASS + 1))
else
  echo "FAIL  [non-Bash tool passthrough] got: $non_bash_out"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-Bash tool passthrough")
fi

# --- #985: help output and heredoc bodies trigger nothing --------------------
run_case "gh pr merge --help (usage only)" pass "gh pr merge --help"
run_case "gh pr merge -h (usage only)"     pass "gh pr merge -h"
run_case "gh pr create --help (usage only)" pass "gh pr create --help"
# Was `pass` until issue #987. The tokenizer dropped the whole quoted line, so the
# `git commit` itself was invisible and the case asserted "no output at all" — an
# over-broad assertion that encoded the blindness rather than #985's intent. With the
# line visible, `git commit` fires its own intrinsic category (a bare single-line
# `git commit -m x` fires it too). #985's actual point — a quoted `gh pr merge` is not
# a merge — is what the forbidden substring now pins.
#
# Issue #874 moved that fire from the ask channel to the advise channel. The
# assertion is otherwise unchanged and still tests #985's intent exactly: the
# reason must name `[git-commit]` and must NOT mention `merge`. If the heredoc
# body ever leaked back into the token stream, `gh pr merge` would match the
# ASK-tier `gh-merge` category — which would flip this case to an ask and fail
# on the empty-stdout check as well as the forbidden substring. Two independent
# ways for that regression to be caught, where the ask-channel form had one.
run_case_reason "commit message quoting a merge" "[git-commit]" "merge" \
  $'git commit -m "$(cat <<\'EOF\'\ngh pr merge is only quoted here\nEOF\n)"' \
  advise
run_case "-h as a --subject value"         ask  "gh pr merge 985 --squash --subject -h"
run_case "merge after heredoc terminator"  ask  $'cat <<\'EOF\'\nbody\nEOF\ngh pr merge 985 --squash'


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
