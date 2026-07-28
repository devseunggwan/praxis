#!/bin/bash
# test_destructive_bash_guard.sh — coverage for the destructive-bash
# PreToolUse advisory (issue #463).
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory → exit 0 + stderr contains [destructive-bash-guard] + ADVISORY
#   ask      → exit 0 + stdout contains "permissionDecision":"ask" + stderr empty
#   silent   → exit 0 + stderr empty + stdout empty
#
# Usage: bash tests/hooks/advisory-nudge/test_destructive_bash_guard.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/destructive-bash-guard/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

unset PRAXIS_HOOK_BYPASS_DESTRUCTIVE_BASH
unset PRAXIS_DESTRUCTIVE_BASH_STRICT

PASS=0
FAIL=0
FAILED_NAMES=()

run_case() {
  local name="$1" expected="$2" command="$3" extra_env="$4"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}))' "$command")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  if [ -n "$extra_env" ]; then
    echo "$payload" | env $extra_env python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      echo "$err" | grep -q "\[destructive-bash-guard\]" || ok=0
      echo "$err" | grep -q "ADVISORY" || ok=0
      ;;
    signal)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      echo "$err" | grep -q "\[destructive-bash-guard\]" || ok=0
      echo "$err" | grep -q "outcome-proxy signal detected" || ok=0
      echo "$err" | grep -q "INFORMATIONAL" || ok=0
      # never the destructive banner, never wording implying blocking
      echo "$err" | grep -q "destructive command detected" && ok=0
      ;;
    ask)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      echo "$out" | grep -q '"permissionDecision": "ask"' || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    *)
      echo "FAIL  [$name] unknown expected: $expected"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc"
    [ -n "$out" ] && echo "        stdout: $out"
    [ -n "$err" ] && echo "        stderr: $err"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# === ADVISORY — rm recursive force =========================================

run_case "rm -rf .;" advisory "rm -rf ."
run_case "rm -fr /tmp/x" advisory "rm -fr /tmp/x"
run_case "rm -Rf /tmp/y" advisory "rm -Rf /tmp/y"
run_case "rm -r --force foo" advisory "rm -r --force foo"
run_case "rm --recursive --force bar" advisory "rm --recursive --force bar"
run_case "rm -rfv (with verbose bundled)" advisory "rm -rfv /tmp/z"
run_case "rm -r -- -f (-- terminator, -f is filename)" silent "rm -r -- -f"

# === SILENT — rm without recursive+force ===================================

run_case "rm /tmp/file (no flags)" silent "rm /tmp/file"
run_case "rm -i file (interactive)" silent "rm -i file.txt"
run_case "rm -f file (force, no recursive)" silent "rm -f file.txt"
run_case "rm -r file (recursive, no force)" silent "rm -r /tmp/dir"

# === SILENT — basename doesn't match rm ====================================

run_case "ls removed/ (path with rm prefix)" silent "ls removed/"
run_case "echo remove me" silent "echo remove me"
run_case "cat removed_file" silent "cat removed_file"

# === ADVISORY — escalation prefix ==========================================

run_case "sudo apt update" advisory "sudo apt update"
run_case "sudo rm foo" advisory "sudo rm foo"
run_case "doas pacman -Syu" advisory "doas pacman -Syu"
run_case "sudo -u admin kubectl apply" advisory "sudo -u admin kubectl apply -f x"
run_case "VAR=1 sudo apt update (env-assign prefix)" advisory "VAR=1 sudo apt update"
run_case "env sudo apt (env wrapper prefix)" advisory "env sudo apt update"
run_case "time sudo apt (time wrapper prefix)" advisory "time sudo apt update"
run_case "if true; then sudo X; fi (shell keyword prefix)" advisory "if true; then sudo apt update; fi"
run_case "env -i sudo X (wrapper with flag)" advisory "env -i sudo apt update"

# === ADVISORY — dd raw I/O =================================================

run_case "dd if=/dev/urandom of=/dev/sda" advisory "dd if=/dev/urandom of=/dev/sda"
run_case "dd of=/dev/null" advisory "dd of=/dev/null"
run_case "dd if=image.iso of=out.img" advisory "dd if=image.iso of=out.img"
run_case "bare dd (no operands)" silent "dd"

# === ADVISORY — mkfs =======================================================

run_case "mkfs.ext4" advisory "mkfs.ext4 /dev/sda1"
run_case "mkfs.btrfs" advisory "mkfs.btrfs /dev/nvme0n1"
run_case "mkfs (bare)" advisory "mkfs -t ext4 /dev/sda1"

# === ADVISORY — chmod recursive open =======================================

run_case "chmod -R 777 /var/www" advisory "chmod -R 777 /var/www"
run_case "chmod --recursive 777 ." advisory "chmod --recursive 777 ."
run_case "chmod 777 -R . (mode-before-flag order)" advisory "chmod 777 -R ."
run_case "chmod -R 0777 dir" advisory "chmod -R 0777 dir"
run_case "chmod -R 666 dir" advisory "chmod -R 666 dir"

# === SILENT — chmod variants that don't match ==============================

run_case "chmod 777 file (no -R)" silent "chmod 777 file"
run_case "chmod -R 755 dir (safe mode)" silent "chmod -R 755 dir"
run_case "chmod -R u+x dir (symbolic)" silent "chmod -R u+x dir"

# === ADVISORY — chown recursive ============================================

run_case "chown -R user file" advisory "chown -R user:user /etc/foo"
run_case "chown --recursive root /opt" advisory "chown --recursive root /opt"

# === SILENT — chown non-recursive ==========================================

run_case "chown user file (no -R)" silent "chown user file"

# === ADVISORY — block-device redirect ======================================

run_case "cat foo > /dev/sda" advisory "cat foo > /dev/sda"
run_case "tar c . > /dev/nvme0n1" advisory "tar c . > /dev/nvme0n1"
run_case "echo x >> /dev/disk0" advisory "echo x >> /dev/disk0"
run_case "cat foo >/dev/sda (glued)" advisory "cat foo >/dev/sda"
run_case "cmd &> /dev/sda (combined stderr+stdout)" advisory "cmd &> /dev/sda"
run_case "cmd 3>/dev/sda (fd-numbered >2)" advisory "cmd 3>/dev/sda"

# === SILENT — safe device targets ==========================================

run_case "echo done > /dev/null" silent "echo done > /dev/null"
run_case "echo err > /dev/stderr" silent "echo err > /dev/stderr"
run_case "echo x > /dev/null && true" silent "echo x > /dev/null && true"

# === ADVISORY — git clean force ============================================

run_case "git clean -fdx" advisory "git clean -fdx"
run_case "git clean -fd" advisory "git clean -fd"
run_case "git clean --force ." advisory "git clean --force ."
run_case "git -C /tmp clean -f" advisory "git -C /tmp clean -f"

# === SILENT — git clean without force ======================================

run_case "git clean -dn (dry-run)" silent "git clean -dn"
run_case "git clean -n" silent "git clean -n"
run_case "git clean -n -f (force + dry-run)" silent "git clean -n -f"
run_case "git clean -nf (bundled dry-run+force)" silent "git clean -nf"
run_case "git clean -fn (bundled force+dry-run)" silent "git clean -fn"
run_case "git clean --dry-run --force" silent "git clean --dry-run --force"

# === ADVISORY — git reset --hard ===========================================

run_case "git reset --hard HEAD" advisory "git reset --hard HEAD"
run_case "git reset --hard origin/main" advisory "git reset --hard origin/main"
run_case "git -C /tmp reset --hard" advisory "git -C /tmp reset --hard HEAD~1"

# === SILENT — git reset without --hard =====================================

run_case "git reset HEAD foo" silent "git reset HEAD foo"
run_case "git reset --mixed" silent "git reset --mixed"

# === ADVISORY — git push --force (issue #844) ==============================

run_case "git push --force origin branch" advisory "git push --force origin branch"
run_case "git push -f origin branch" advisory "git push -f origin branch"
run_case "git push --force-with-lease origin branch" advisory "git push --force-with-lease origin branch"
run_case "git push --force-with-lease=branch:sha origin branch (inline refspec)" advisory "git push --force-with-lease=branch:sha origin branch"
run_case "git -C /tmp push --force (global flag)" advisory "git -C /tmp push --force origin branch"
run_case "git push origin branch --force (flag after refspec)" advisory "git push origin branch --force"

# === SILENT — git push without a force flag ================================

run_case "git push origin branch" silent "git push origin branch"
run_case "git push --no-force origin branch" silent "git push --no-force origin branch"
run_case "git push --force-with-lease-typo (not the real flag)" silent "git push --force-with-lease-typo origin branch"
run_case "git push -- --force (positional after --)" silent "git push -- --force"

# === ADVISORY — find -delete ===============================================

run_case "find /tmp -delete" advisory "find /tmp -name '*.bak' -delete"
run_case "find . -delete (no name filter)" advisory "find . -delete"

# === SILENT — find without -delete =========================================

run_case "find . -name foo" silent "find . -name foo"
run_case "find . -print" silent "find . -print"

# === ADVISORY — truncate -s 0 ==============================================

run_case "truncate -s 0 log.txt" advisory "truncate -s 0 log.txt"
run_case "truncate --size 0 log.txt" advisory "truncate --size 0 log.txt"
run_case "truncate -s0 log.txt (glued)" advisory "truncate -s0 log.txt"
run_case "truncate --size=0 log.txt" advisory "truncate --size=0 log.txt"

# === SILENT — truncate non-zero ============================================

run_case "truncate -s 100M file" silent "truncate -s 100M file"
run_case "truncate -s 0B file (zero in B)" advisory "truncate -s 0B file"

# === ADVISORY — shred ======================================================

run_case "shred secret.txt" advisory "shred secret.txt"
run_case "shred -u -z secret.txt" advisory "shred -u -z secret.txt"

# === ADVISORY — fork bomb ==================================================

run_case "fork bomb canonical" advisory ":(){ :|:& };:"
run_case "fork bomb no spaces" advisory ":(){:|:&};:"
run_case "named bomb() (legit user function — out of scope)" silent "bomb(){ echo hi; }; bomb"

# === SIGNAL — outcome-proxy revert-adjacent commands (issue #737) =========

run_case "git revert HEAD" signal "git revert HEAD"
run_case "git revert --no-edit HEAD~1" signal "git revert --no-edit HEAD~1"
run_case "git -C /tmp revert HEAD" signal "git -C /tmp revert HEAD"
run_case "gh pr close 5" signal "gh pr close 5"
run_case "gh pr close https://github.com/o/r/pull/5" signal "gh pr close https://github.com/o/r/pull/5"
run_case "gh issue reopen 10" signal "gh issue reopen 10"
run_case "gh -R o/r pr close 5 (-R before subcommand)" signal "gh -R octocat/Hello-World pr close 5"
run_case "gh --repo o/r issue reopen 10 (--repo before subcommand)" signal "gh --repo octocat/Hello-World issue reopen 10"
run_case "gh --repo=o/r pr close 5 (glued --repo=)" signal "gh --repo=octocat/Hello-World pr close 5"
run_case "gh pr close 5 -R o/r (-R after args)" signal "gh pr close 5 -R octocat/Hello-World"

# === SILENT — commands that must NOT trigger the revert signal =============

run_case "git reset HEAD (not revert)" silent "git reset HEAD foo"
run_case "git log --oneline (not revert)" silent "git log --oneline"
run_case "gh pr status (not close)" silent "gh pr status"
run_case "gh pr view 5 (not close)" silent "gh pr view 5"
run_case "gh issue list (not reopen)" silent "gh issue list"
run_case "gh issue close 10 (opposite action)" silent "gh issue close 10"
run_case "gh pr merge 5 (not close)" silent "gh pr merge 5"

# === ADVISORY+SIGNAL — compound command carries both categories ============

run_case "rm -rf x && git revert HEAD (both fire)" advisory "rm -rf /tmp/x && git revert HEAD"

# === ASK — strict mode does NOT escalate signal-only commands ==============

run_case "git revert in strict mode stays signal (not ask)" signal "git revert HEAD" "PRAXIS_DESTRUCTIVE_BASH_STRICT=1"
run_case "gh pr close in strict mode stays signal (not ask)" signal "gh pr close 5" "PRAXIS_DESTRUCTIVE_BASH_STRICT=1"

# === ADVISORY — compound command decomposition =============================

run_case "mkdir x && rm -rf x (second segment matches)" advisory "mkdir /tmp/x && rm -rf /tmp/x"
run_case "cd / && rm -rf foo" advisory "cd / && rm -rf foo"
run_case "echo start; sudo apt update" advisory "echo start; sudo apt update"

# === SILENT — non-destructive commands =====================================

run_case "ls -la" silent "ls -la"
run_case "echo hello" silent "echo hello"
run_case "git status" silent "git status"
run_case "git push origin main" silent "git push origin main"
run_case "kubectl get pods" silent "kubectl get pods"

# === ASK — strict mode escalates to permissionDecision =====================

run_case "rm -rf in strict mode" ask "rm -rf /tmp/x" "PRAXIS_DESTRUCTIVE_BASH_STRICT=1"
run_case "sudo apt in strict mode" ask "sudo apt update" "PRAXIS_DESTRUCTIVE_BASH_STRICT=1"
run_case "dd of= in strict mode" ask "dd of=/dev/sda if=foo" "PRAXIS_DESTRUCTIVE_BASH_STRICT=1"

# === SILENT — full bypass env var ==========================================

run_case "rm -rf with bypass" silent "rm -rf /tmp/x" "PRAXIS_HOOK_BYPASS_DESTRUCTIVE_BASH=1"
run_case "strict + bypass (bypass wins)" silent "rm -rf /tmp/x" "PRAXIS_DESTRUCTIVE_BASH_STRICT=1 PRAXIS_HOOK_BYPASS_DESTRUCTIVE_BASH=1"

# === Fail-open infrastructure ==============================================

run_case_raw_payload() {
  local name="$1" expected="$2" payload="$3"

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc"
    [ -n "$out" ] && echo "        stdout: $out"
    [ -n "$err" ] && echo "        stderr: $err"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

run_case_raw_payload "non-Bash tool passes silently" silent \
  '{"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x"}}'

run_case_raw_payload "malformed JSON fails open silently" silent \
  'not valid json {{{'

run_case_raw_payload "empty Bash command silent" silent \
  '{"tool_name": "Bash", "tool_input": {"command": ""}}'

# ---------------------------------------------------------------------------
# @fail_open structural assertion
# ---------------------------------------------------------------------------

# main() opts into the shared @fail_open guard; verify the decorator is
# applied (fail-open behaviour itself is covered in tests/test_hook_runtime.sh).
_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util, io
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if getattr(mod.main, "__wrapped__", None) is None:
    sys.stderr.write("main not wrapped by @fail_open\n"); sys.exit(1)
PYEOF
)
_uncaught_rc=$?
if [ "$_uncaught_rc" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "  PASS  main() is wrapped by the shared @fail_open guard (exit 0, no stderr)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  main() not wrapped by @fail_open (rc=$_uncaught_rc, out=$(echo "$_uncaught_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("main() is wrapped by the shared @fail_open guard")
fi

# === ADVISORY — fixup-commit alternative appears only for force-push =======

_fp_out=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push --force origin branch"}}))' \
  | python3 "$HOOK" 2>&1 1>/dev/null)
if echo "$_fp_out" | grep -q "git commit --fixup="; then
  echo "PASS  [git push --force message includes fixup-commit alternative]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [git push --force message includes fixup-commit alternative]"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("git push --force message includes fixup-commit alternative")
fi

_rm_out=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/x"}}))' \
  | python3 "$HOOK" 2>&1 1>/dev/null)
if echo "$_rm_out" | grep -q "git commit --fixup="; then
  echo "FAIL  [rm -rf message does NOT include the fixup-commit hint]"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("rm -rf message does NOT include fixup-commit hint")
else
  echo "PASS  [rm -rf message does NOT include the fixup-commit hint]"
  PASS=$((PASS + 1))
fi

# === Summary ==============================================================

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
