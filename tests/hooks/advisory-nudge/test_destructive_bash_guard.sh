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

# === ADVISORY — git reset --hard ===========================================

run_case "git reset --hard HEAD" advisory "git reset --hard HEAD"
run_case "git reset --hard origin/main" advisory "git reset --hard origin/main"
run_case "git -C /tmp reset --hard" advisory "git -C /tmp reset --hard HEAD~1"

# === SILENT — git reset without --hard =====================================

run_case "git reset HEAD foo" silent "git reset HEAD foo"
run_case "git reset --mixed" silent "git reset --mixed"

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

# === Summary ==============================================================

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
