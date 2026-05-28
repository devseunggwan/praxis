#!/bin/bash
# test_protected_paths_guard.sh — coverage for the protected-paths PreToolUse
# advisory (issue #464).
#
# Synthesizes Claude Code PreToolUse(Edit|Write|NotebookEdit) payloads and
# asserts:
#   advisory → exit 0 + stderr contains the advisory header
#   block    → exit 2 + stderr contains the BLOCKED marker
#   silent   → exit 0 + stderr empty
#
# Usage: bash tests/hooks/advisory-nudge/test_protected_paths_guard.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/protected-paths-guard/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

# Ensure CLAUDE_PLUGIN_ROOT doesn't accidentally skip everything during local
# test runs. Point it at a non-existent path so the self-edit guard never
# fires for synthesized test paths.
export CLAUDE_PLUGIN_ROOT="/nonexistent-plugin-root-for-tests"
unset PRAXIS_HOOK_BYPASS_PROTECTED_PATHS
unset PRAXIS_PROTECTED_PATHS_STRICT

PASS=0
FAIL=0
FAILED_NAMES=()

run_case() {
  local name="$1" expected="$2" tool="$3" path="$4" extra_env="$5"

  local payload
  if [ "$tool" = "NotebookEdit" ]; then
    payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": sys.argv[1],
    "tool_input": {"notebook_path": sys.argv[2]},
}))' "$tool" "$path")
  else
    payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": sys.argv[1],
    "tool_input": {"file_path": sys.argv[2]},
}))' "$tool" "$path")
  fi

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
      echo "$err" | grep -q "\[protected-paths-guard\]" || ok=0
      echo "$err" | grep -q "ADVISORY" || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      [ -z "$out" ]   || ok=0
      echo "$err" | grep -q "\[protected-paths-guard\]" || ok=0
      echo "$err" | grep -q "BLOCKED" || ok=0
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

# === ADVISORY — basename-exact protected files =============================

run_case ".env (bare)" advisory Write ".env"
run_case ".env (with dir)" advisory Write "config/.env"
run_case ".env (abs path)" advisory Write "/srv/app/.env"
run_case ".netrc" advisory Edit "$HOME/.netrc"
run_case ".npmrc" advisory Write "project/.npmrc"
run_case "credentials" advisory Write "aws/credentials"
run_case "id_rsa" advisory Edit "keys/id_rsa"
run_case "id_ed25519" advisory Write "keys/id_ed25519"
run_case "id_dsa" advisory Write "keys/id_dsa"
run_case "id_ecdsa" advisory Write "keys/id_ecdsa"

# === ADVISORY — .env.<env> prefix ==========================================

run_case ".env.production" advisory Write "config/.env.production"
run_case ".env.local" advisory Write ".env.local"
run_case ".env.staging" advisory Edit "/srv/app/.env.staging"
run_case ".env.example.local (override — not template)" advisory Write "config/.env.example.local"

# === SILENT — .env allowlist (template files) ==============================

run_case ".env.example" silent Write "config/.env.example"
run_case ".env.sample" silent Write ".env.sample"
run_case ".env.template" silent Write "config/.env.template"
run_case ".env.defaults" silent Edit ".env.defaults"

# === ADVISORY — extension family ===========================================

run_case "server.pem" advisory Write "keys/server.pem"
run_case "client.key" advisory Edit "tls/client.key"
run_case "cert.p12" advisory Write "tls/cert.p12"
run_case "store.keystore" advisory Write "tls/store.keystore"

# === ADVISORY — .ssh/ directory component ==================================

run_case ".ssh/config" advisory Write "/home/u/.ssh/config"
run_case ".ssh/known_hosts" advisory Edit "$HOME/.ssh/known_hosts"
run_case ".ssh/id_rsa (under .ssh/)" advisory Write "$HOME/.ssh/id_rsa"
run_case ".ssh/id_rsa.pub (under .ssh/)" advisory Write "$HOME/.ssh/id_rsa.pub"
run_case ".ssh/authorized_keys" advisory Write "$HOME/.ssh/authorized_keys"

# === SILENT — public-key allow (NOT under .ssh/) ===========================

run_case "pubkeys/id_rsa.pub" silent Write "pubkeys/id_rsa.pub"
run_case "pubkeys/id_ed25519.pub" silent Edit "config/keys/id_ed25519.pub"

# === SILENT — basename-component boundary (no substring) ===================

run_case ".environment (not .env)" silent Write "config/.environment"
run_case "envfile.txt (not .env)" silent Write ".envfile.txt"
run_case "myenv (not .env)" silent Write "config/myenv"
run_case "node_modules_backup/.env_old (not .env*)" silent Write "node_modules_backup/.env_old"
run_case ".envrc (direnv config — not .env)" silent Write ".envrc"
run_case ".gitkeep (not credentials)" silent Write ".gitkeep"
run_case "credentials.json.example" silent Write "fixtures/credentials.json.example"

# === SILENT — test fixtures ================================================

run_case "tests/fixtures/.env.local (fixture)" silent Write "tests/fixtures/.env.local"
run_case "src/__fixtures__/sample.pem (fixture)" silent Write "src/__fixtures__/sample.pem"
run_case "test-data/secret.key (fixture)" silent Write "test-data/secret.key"
run_case "testdata/.netrc (fixture)" silent Write "testdata/.netrc"

# === SILENT — planning artifacts ==========================================

run_case "/tmp/scratch.env.production" silent Write "/tmp/scratch.env.production"
run_case "/tmp/cert.pem (scratch)" silent Write "/tmp/cert.pem"
run_case "/private/tmp/.env (macOS realpath)" silent Write "/private/tmp/.env"
run_case "/private/tmp/cert.pem (macOS realpath)" silent Write "/private/tmp/cert.pem"
run_case ".omc/plans/sketch.env" silent Write "/proj/.omc/plans/sketch.env"
run_case ".claude/projects/X/log.env" silent Write "/proj/.claude/projects/X/log.env"

# === SILENT — self-edit (CLAUDE_PLUGIN_ROOT) ==============================

# Use a temp dir as CLAUDE_PLUGIN_ROOT and a path under it
run_case "self-edit under plugin root" silent Write "/nonexistent-plugin-root-for-tests/hooks/foo/.env" "CLAUDE_PLUGIN_ROOT=/nonexistent-plugin-root-for-tests"

# === BLOCK — strict mode escalates exit code ===============================

run_case ".env in strict mode (block)" block Write ".env" "PRAXIS_PROTECTED_PATHS_STRICT=1"
run_case ".ssh/id_rsa in strict mode" block Write "$HOME/.ssh/id_rsa" "PRAXIS_PROTECTED_PATHS_STRICT=1"
run_case "server.pem in strict mode" block Edit "keys/server.pem" "PRAXIS_PROTECTED_PATHS_STRICT=1"

# === SILENT — full bypass env var =========================================

run_case ".env with bypass" silent Write ".env" "PRAXIS_HOOK_BYPASS_PROTECTED_PATHS=1"
run_case ".ssh/id_rsa with bypass" silent Write "$HOME/.ssh/id_rsa" "PRAXIS_HOOK_BYPASS_PROTECTED_PATHS=1"
run_case "strict + bypass (bypass wins)" silent Write ".env" "PRAXIS_PROTECTED_PATHS_STRICT=1 PRAXIS_HOOK_BYPASS_PROTECTED_PATHS=1"

# === ADVISORY — NotebookEdit target ========================================

run_case "NotebookEdit .env" advisory NotebookEdit ".env"
run_case "NotebookEdit fixture (silent)" silent NotebookEdit "tests/fixtures/.env.production"

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

run_case_raw_payload "non-target tool passes silently" silent \
  '{"tool_name": "Bash", "tool_input": {"command": "ls"}}'

run_case_raw_payload "malformed JSON fails open silently" silent \
  'not valid json {{{'

run_case_raw_payload "empty file_path silent" silent \
  '{"tool_name": "Write", "tool_input": {"file_path": ""}}'

run_case_raw_payload "missing file_path silent" silent \
  '{"tool_name": "Write", "tool_input": {}}'

# === Summary ==============================================================

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
