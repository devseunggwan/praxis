#!/bin/bash
# test_protected_paths_guard.sh — coverage for the protected-paths PreToolUse
# advisory (issue #464).
#
# Synthesizes Claude Code PreToolUse(Edit|NotebookEdit|Write) payloads and
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

# White-box assertion for _is_self_edit — black-box run_case cannot distinguish
# the rel == ".." parent-dir case (its basename is never a protected file, so
# the hook is silent either way). Assert the function return value directly.
assert_self_edit() {
  local name="$1" path="$2" plugin_root="$3" expected="$4"
  local got
  got=$(CLAUDE_PLUGIN_ROOT="$plugin_root" python3 -c '
import os, sys, importlib.util
spec = importlib.util.spec_from_file_location("pg", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m._is_self_edit(sys.argv[2]))' "$HOOK" "$path")
  if [ "$got" = "$expected" ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] _is_self_edit=$got expected=$expected"
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
run_case ".omc/plans (relative)" silent Write ".omc/plans/sketch.env"

# === issue #1362 — the scratch exemption is for absolute /tmp only =========
#
# _is_planning_artifact used to write "/" + path.lstrip("/") before the prefix
# test, so every relative `tmp/` path took the scratch exemption, and it
# compared the prefix before collapsing `..`, so a path could start with
# `/tmp/` and resolve into the project. Both are advisory-bearing paths.

run_case "relative tmp/.env is a project path" advisory Write "tmp/.env"
run_case "relative tmp/ nested" advisory Write "tmp/config/credentials"
run_case "relative private/tmp/.env" advisory Write "private/tmp/.env"
run_case "/tmp/.. escapes scratch" advisory Write "/tmp/../proj/.env"
run_case "/private/tmp/.. escapes scratch" advisory Write "/private/tmp/../proj/.env"
run_case "fragment .. escapes planning" advisory Write "/proj/.omc/plans/../../.env"
run_case "/tmp/./ stays scratch" silent Write "/tmp/./sketch.env"

# === SILENT — self-edit (CLAUDE_PLUGIN_ROOT) ==============================

# Use a temp dir as CLAUDE_PLUGIN_ROOT and a path under it
run_case "self-edit under plugin root" silent Write "/nonexistent-plugin-root-for-tests/hooks/foo/.env" "CLAUDE_PLUGIN_ROOT=/nonexistent-plugin-root-for-tests"

# === issue #495 — relative-path self-edit exemption bypass fix =============
#
# Regression: cwd=plugin_root, relative ".env" → _is_self_edit must return False
# so the write is NOT silently exempted.
#
# CLAUDE_PLUGIN_ROOT is set to ROOT_DIR (this very worktree) so that the
# abspath-based path would previously have resolved ".env" to inside the root
# and returned True (false exemption). After the fix it must return False and
# the write must be detected (advisory/block).

run_case "relative .env blocked in strict (cwd=plugin root, issue #495)" block \
  Write ".env" \
  "PRAXIS_PROTECTED_PATHS_STRICT=1 CLAUDE_PLUGIN_ROOT=$ROOT_DIR"

run_case "relative .env advisory (cwd=plugin root, issue #495)" advisory \
  Write ".env" \
  "CLAUDE_PLUGIN_ROOT=$ROOT_DIR"

# Absolute path to a file that genuinely lives inside the plugin root must
# still be silently exempted (regression guard for the legitimate self-edit path).
run_case "abs-path self-edit still silent (issue #495 regression)" silent \
  Write "$ROOT_DIR/hooks/advisory-nudge/protected-paths-guard/impl.py" \
  "CLAUDE_PLUGIN_ROOT=$ROOT_DIR"

# White-box: rel == "." (path == plugin_root) is inside; rel == ".." (path is the
# parent dir, outside) must NOT be exempted — the bug this guards against.
assert_self_edit "self-edit: plugin_root itself (rel '.')" "$ROOT_DIR" "$ROOT_DIR" True
assert_self_edit "self-edit: file inside plugin_root" "$ROOT_DIR/hooks/x/.env" "$ROOT_DIR" True
assert_self_edit "self-edit: parent dir not exempt (rel '..')" "$(dirname "$ROOT_DIR")" "$ROOT_DIR" False
assert_self_edit "self-edit: relative path never exempt" ".env" "$ROOT_DIR" False

# === ADVISORY — .git-credentials / .pgpass (issue #495 LOW additions) ======

run_case ".git-credentials advisory" advisory Write "$HOME/.git-credentials"
run_case ".git-credentials in dir" advisory Write "config/.git-credentials"
run_case ".pgpass advisory" advisory Write "$HOME/.pgpass"
run_case ".pgpass in dir" advisory Write "db/.pgpass"

run_case ".git-credentials block (strict)" block Write "$HOME/.git-credentials" "PRAXIS_PROTECTED_PATHS_STRICT=1"
run_case ".pgpass block (strict)" block Write "$HOME/.pgpass" "PRAXIS_PROTECTED_PATHS_STRICT=1"

# === ADVISORY — .aws / .kube / .gnupg directory components (issue #495 LOW)

run_case ".aws/credentials" advisory Write "$HOME/.aws/credentials"
run_case ".aws/config" advisory Write "$HOME/.aws/config"
run_case ".kube/config" advisory Write "$HOME/.kube/config"
run_case ".gnupg/secring.gpg" advisory Write "$HOME/.gnupg/secring.gpg"
run_case ".gnupg/trustdb.gpg" advisory Edit "$HOME/.gnupg/trustdb.gpg"

run_case ".aws/credentials block (strict)" block Write "$HOME/.aws/credentials" "PRAXIS_PROTECTED_PATHS_STRICT=1"
run_case ".kube/config block (strict)" block Write "$HOME/.kube/config" "PRAXIS_PROTECTED_PATHS_STRICT=1"

# fixture paths under protected dirs must still be silent
run_case ".aws in fixture dir (silent)" silent Write "tests/fixtures/.aws/credentials"
run_case ".kube in fixture dir (silent)" silent Write "src/__fixtures__/.kube/config"

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

# === Summary ==============================================================

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
