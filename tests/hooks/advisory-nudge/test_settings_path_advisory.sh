#!/bin/bash
# test_settings_path_advisory.sh — coverage for the settings-path PreToolUse
# advisory (issue #1337 item 3; RULE-BACKSTOP-GAPS gap #4, follow-up-write lane).
#
# Synthesizes Claude Code PreToolUse(Edit|Write) payloads and asserts:
#   advisory → exit 0, stderr carries the header + ADVISORY, stdout is the
#              additionalContext JSON carrying the same header
#   block    → exit 2, stderr carries BLOCKED, stdout empty
#   silent   → exit 0, stdout and stderr empty
#
# Usage: bash tests/hooks/advisory-nudge/test_settings_path_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/settings-path-advisory/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

# The self-edit skip must not swallow synthesized paths: point the plugin root
# at a path no test case uses.
export CLAUDE_PLUGIN_ROOT="/nonexistent-plugin-root-for-tests"
unset PRAXIS_HOOK_BYPASS_SETTINGS_PATH
unset PRAXIS_SETTINGS_PATH_STRICT

PASS=0
FAIL=0
FAILED_NAMES=()

HEADER='[settings-path-advisory] Claude Code settings write'

# run_case <name> <expected> <tool> <path> <text> [extra_env] [stderr-grep]
#   text goes to `content` for Write and `new_string` for Edit.
#   stderr-grep, when given, must also match stderr (advisory/block only).
run_case() {
  local name="$1" expected="$2" tool="$3" path="$4" text="$5" extra_env="$6" want="$7"

  local payload
  payload=$(python3 -c '
import json, sys
tool, path, text = sys.argv[1:4]
field = "content" if tool == "Write" else "new_string"
ti = {"file_path": path, field: text}
if tool == "Edit":
    ti["old_string"] = "x"
print(json.dumps({"session_id": "t-settings", "tool_name": tool, "tool_input": ti}))' "$tool" "$path" "$text")

  _run_payload "$name" "$expected" "$payload" "$extra_env" "$want"
}

# _run_payload <name> <expected> <payload> [extra_env] [stderr-grep]
_run_payload() {
  local name="$1" expected="$2" payload="$3" extra_env="$4" want="$5"
  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  if [ -n "$extra_env" ]; then
    printf '%s' "$payload" | env $extra_env python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    printf '%s' "$payload" | python3 "$HOOK" >"$out_file" 2>"$err_file"
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
      echo "$err" | grep -qF "$HEADER" || ok=0
      echo "$err" | grep -q "ADVISORY" || ok=0
      # stdout: one JSON object, PreToolUse additionalContext, same header.
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
h = d["hookSpecificOutput"]
assert h["hookEventName"] == "PreToolUse", h
assert sys.argv[1] in h["additionalContext"], h
assert "permissionDecision" not in h, h' "$HEADER" 2>/dev/null || ok=0
      if [ -n "$want" ]; then echo "$err" | grep -qF -- "$want" || ok=0; fi
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      [ -z "$out" ]   || ok=0
      echo "$err" | grep -qF "$HEADER" || ok=0
      echo "$err" | grep -q "BLOCKED" || ok=0
      if [ -n "$want" ]; then echo "$err" | grep -qF -- "$want" || ok=0; fi
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

PERMS='{"permissions": {"allow": ["Bash(git *)"]}}'

# === ADVISORY — every path kind ============================================

run_case "project .claude/settings.json (relative)" advisory Write ".claude/settings.json" "$PERMS" "" "(project or user settings)"
run_case "project .claude/settings.json (absolute)" advisory Write "/repo/.claude/settings.json" "$PERMS"
run_case "project .claude/settings.local.json" advisory Write "/repo/.claude/settings.local.json" "$PERMS"
run_case "user ~/.claude/settings.json" advisory Edit "$HOME/.claude/settings.json" '"allow": ["Bash(rm *)"]'
run_case "managed /etc/claude-code" advisory Write "/etc/claude-code/managed-settings.json" "{}" "" "(managed policy file)"
run_case "managed macOS path with spaces" advisory Write "/Library/Application Support/ClaudeCode/managed-settings.json" "{}"
run_case "backslash separators" advisory Write 'C:\Users\u\.claude\settings.json' "$PERMS"

# === ADVISORY — reason line names the widening shapes =====================

run_case "reason: permissions + allow + rule literal" advisory Write ".claude/settings.json" "$PERMS" "" 'carries "permissions", "allow", a permission-rule literal'
run_case "reason: hooks key" advisory Write ".claude/settings.json" '{"hooks": {"PreToolUse": []}}' "" 'carries "hooks"'
run_case "reason: disableAllHooks" advisory Write ".claude/settings.json" '{"disableAllHooks": true}' "" 'carries "disableAllHooks"'
run_case "reason: env + praxis variable" advisory Edit ".claude/settings.json" '"env": {"PRAXIS_HOOK_BYPASS_PROTECTED_PATHS": "1"}' "" 'carries "env", praxis variable PRAXIS_HOOK_BYPASS_PROTECTED_PATHS'
run_case "reason: deny + ask keys" advisory Write ".claude/settings.json" '{"permissions": {"deny": [], "ask": []}}' "" 'carries "permissions", "deny", "ask"'
run_case "reason: rule literal alone (Edit new_string)" advisory Edit ".claude/settings.json" '"Edit(*.ts)"' "" 'carries a permission-rule literal'
run_case "reason: no shape — path alone" advisory Write ".claude/settings.local.json" '{"model": "opus"}' "" 'no permission/hook key in the written text; the path alone is the surface'
run_case "reason: key word inside a value is not a key" advisory Write ".claude/settings.json" '{"note": "permissions allow env hooks"}' "" 'no permission/hook key'
run_case "reason: Write ignores new_string field" advisory Write ".claude/settings.json" '{"model": "opus"}' "" 'no permission/hook key'

# === ADVISORY — text about principle 5 and the authorship question ========

run_case "text: principle 5 + authorship" advisory Write ".claude/settings.json" "$PERMS" "" 'state in the response who asked for this edit'
run_case "text: bypass line present" advisory Write ".claude/settings.json" "$PERMS" "" 'PRAXIS_HOOK_BYPASS_SETTINGS_PATH=1'

# === SILENT — component-exact negatives ===================================

run_case "settings.json without .claude parent" silent Write "config/settings.json" "$PERMS"
run_case "settings.json at repo root" silent Write "settings.json" "$PERMS"
run_case "my.claude/settings.json (parent differs)" silent Write "my.claude/settings.json" "$PERMS"
run_case ".claude/settings.json.bak" silent Write ".claude/settings.json.bak" "$PERMS"
run_case ".claude/hooks.json" silent Write ".claude/hooks.json" "$PERMS"
run_case ".claude/settings/extra.json (nested dir)" silent Write ".claude/settings/extra.json" "$PERMS"
run_case "user .claude.json (global state file)" silent Write "$HOME/.claude.json" "$PERMS"
run_case "user .codex/config.toml" silent Write "$HOME/.codex/config.toml" "$PERMS"
run_case ".claude/settings.json inside deeper dir named settings.json" silent Write "settings.json/.claude/notes.md" "$PERMS"

# === SILENT — skip rules ===================================================

run_case "fixture: tests/fixtures/.claude/settings.json" silent Write "tests/fixtures/.claude/settings.json" "$PERMS"
run_case "fixture: __fixtures__" silent Write "src/__fixtures__/.claude/settings.local.json" "$PERMS"
run_case "fixture: testdata managed" silent Write "testdata/managed-settings.json" "$PERMS"
run_case "scratch: /tmp" silent Write "/tmp/x/.claude/settings.json" "$PERMS"
run_case "scratch: /private/tmp (macOS realpath)" silent Write "/private/tmp/x/.claude/settings.json" "$PERMS"
run_case "scratch: dot-segments staying under /tmp" silent Write "/tmp/x/../y/.claude/settings.json" "$PERMS"
run_case "not scratch: relative tmp/ is a project path" advisory Write "tmp/.claude/settings.json" "$PERMS"
run_case "not scratch: /tmp/.. escapes the prefix" advisory Write "/tmp/../repo/.claude/settings.json" "$PERMS"
run_case "not scratch: /tmpfs is not /tmp/" advisory Write "/tmpfs/.claude/settings.json" "$PERMS"
run_case "self-edit under CLAUDE_PLUGIN_ROOT" silent Write "/nonexistent-plugin-root-for-tests/.claude/settings.json" "$PERMS"
run_case "self-edit: relative path never exempted" advisory Write ".claude/settings.json" "$PERMS" "CLAUDE_PLUGIN_ROOT=$PWD"

# === SILENT — bypass ======================================================

run_case "bypass env silences" silent Write ".claude/settings.json" "$PERMS" "PRAXIS_HOOK_BYPASS_SETTINGS_PATH=1"

# === BLOCK — strict mode ===================================================

run_case "strict: exit 2, no additionalContext" block Write ".claude/settings.json" "$PERMS" "PRAXIS_SETTINGS_PATH_STRICT=1" 'carries "permissions"'
run_case "strict: exact value 1 only" advisory Write ".claude/settings.json" "$PERMS" "PRAXIS_SETTINGS_PATH_STRICT=yes"
run_case "strict: bypass wins" silent Write ".claude/settings.json" "$PERMS" "PRAXIS_SETTINGS_PATH_STRICT=1 PRAXIS_HOOK_BYPASS_SETTINGS_PATH=1"

# === SILENT — fail-open paths =============================================

_run_payload "fail-open: malformed stdin" silent 'not json'
_run_payload "fail-open: non-dict top level" silent '[1, 2]'
_run_payload "fail-open: NotebookEdit is not a target" silent '{"tool_name": "NotebookEdit", "tool_input": {"notebook_path": ".claude/settings.json"}}'
_run_payload "fail-open: Bash is not a target" silent '{"tool_name": "Bash", "tool_input": {"command": "cat > .claude/settings.json"}}'
_run_payload "fail-open: empty file_path" silent '{"tool_name": "Write", "tool_input": {"file_path": "  ", "content": "{}"}}'
_run_payload "fail-open: missing tool_input" silent '{"tool_name": "Write"}'
_run_payload "fail-open: tool_input not a dict" silent '{"tool_name": "Write", "tool_input": "x"}'
_run_payload "non-string content still classifies by path" advisory '{"tool_name": "Write", "tool_input": {"file_path": ".claude/settings.json", "content": 42}}' "" 'no permission/hook key'

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
