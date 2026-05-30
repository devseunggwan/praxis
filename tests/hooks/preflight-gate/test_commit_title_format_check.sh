#!/bin/bash
# test_commit_title_format_check.sh — coverage for hooks/preflight-gate/commit-title-format-check/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   block   → stdout contains "permissionDecision" / "block", rc=2
#   silent  → stdout empty, stderr empty, rc=0
#   advisory → rc=0, stdout empty, stderr non-empty (STRICT=0 mode)
#
# Usage: bash tests/hooks/preflight-gate/test_commit_title_format_check.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/commit-title-format-check/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation payload_json [env_vars...]
#   expectation:
#     "block"    — stdout contains permissionDecision block, rc=2
#     "silent"   — stdout empty, stderr empty, rc=0
#     "advisory" — rc=0, stdout empty, stderr non-empty
run_case() {
  local name="$1" expectation="$2" payload="$3"
  shift 3
  local env_args=()
  for kv in "$@"; do
    env_args+=("$kv")
  done

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)

  local base_env=(
    "PRAXIS_COMMIT_TITLE_FORMAT_STRICT=1"
  )

  if [ "${#env_args[@]}" -gt 0 ]; then
    echo "$payload" | env "${base_env[@]}" "${env_args[@]}" python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | env "${base_env[@]}" python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    block)
      # Migrated hook: stderr + exit 2 (no stdout JSON permissionDecision)
      [ "$rc" -eq 2 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -n "$err" ]   || ok=0
      ;;
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -n "$err" ]   || ok=0
      ;;
    *)
      echo "  internal: unknown expectation '$expectation'" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    PASS=$((PASS + 1))
    echo "  PASS  $name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    echo "  FAIL  $name (rc=$rc, expected=$expectation)"
    [ -n "$out" ] && echo "        stdout: $(echo "$out" | head -c 400)"
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -c 400)"
  fi
}

echo "test_commit_title_format_check"

# ---------------------------------------------------------------------------
# PASS cases — valid conventional commit format
# ---------------------------------------------------------------------------

run_case "valid feat(scope): desc via -m" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"feat(scope): add new feature\""}}'

run_case "valid fix: desc via -m (no scope)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"fix: resolve null pointer\""}}'

run_case "valid docs via --message" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit --message \"docs(readme): update examples\""}}'

run_case "valid chore via -m= embedded" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m=\"chore: update dependencies\""}}'

run_case "valid refactor via -am combined flag" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -am \"refactor(core): extract validation logic\""}}'

run_case "valid test(scope): desc" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"test(auth): add unit tests for token refresh\""}}'

run_case "valid: perf in default allowed types" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"perf: reduce query round trips\""}}'

run_case "valid: ci in default allowed types" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"ci(pipeline): add lint step\""}}'

run_case "valid: build in default allowed types" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"build: upgrade webpack to v5\""}}'

run_case "valid style(fmt): desc" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"style(fmt): apply black formatting\""}}'

# ---------------------------------------------------------------------------
# PASS cases — whitelisted prefixes
# ---------------------------------------------------------------------------

run_case "whitelist: Merge branch" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"Merge branch '"'"'feature/long-branch-name'"'"' into main\""}}'

run_case "whitelist: Merge pull request" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"Merge pull request #123 from org/feature\""}}'

run_case "whitelist: Revert" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"Revert \\\"feat(auth): add oauth support\\\"\""}}'

run_case "whitelist: fixup!" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"fixup! fix: broken validation\""}}'

run_case "whitelist: squash!" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"squash! refactor(core): cleanup\""}}'

# ---------------------------------------------------------------------------
# PASS cases — non-commit commands
# ---------------------------------------------------------------------------

run_case "git status (non-commit, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git status"}}'

run_case "git push (non-commit, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git push origin main"}}'

run_case "gh pr list (not create, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr list --state open"}}'

run_case "gh issue view (not create, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue view 42"}}'

run_case "non-Bash tool_name (silent)" \
  "silent" \
  '{"tool_name":"Edit","tool_input":{"command":"git commit -m \"bad title\""}}'

run_case "malformed JSON (fail-open, silent)" \
  "silent" \
  "not-json"

run_case "echo git commit fake (silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"echo \"git commit -m '"'"'bad title'"'"'\""}}'

run_case "gh pr create no --title flag (silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --body \"some body\""}}'

# ---------------------------------------------------------------------------
# BLOCK cases — missing type
# ---------------------------------------------------------------------------

run_case "block: missing type (no conventional prefix)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"updated stuff\""}}'

run_case "block: plain description only" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"add user authentication\""}}'

# ---------------------------------------------------------------------------
# BLOCK cases — uppercase first letter after colon
# ---------------------------------------------------------------------------

run_case "block: uppercase description first letter" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"feat: Add something\""}}'

run_case "block: uppercase via --message" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit --message \"fix: Fix the bug\""}}'

# Verify uppercase is actually caught (not just regex assumption)
python3 -c "
import re
pattern = re.compile(r'^(feat|fix|docs|refactor|chore|test|perf|ci|build|style)(\([a-z0-9-]+\))?: [a-z]')
assert not pattern.match('feat: Add something'), 'uppercase A should not match'
assert not pattern.match('fix: Fix the bug'), 'uppercase F should not match'
" || { echo "  INTERNAL CHECK FAIL: uppercase regex verification"; exit 1; }

# ---------------------------------------------------------------------------
# BLOCK cases — Korean/non-ASCII description first char
# ---------------------------------------------------------------------------

run_case "block: Korean description first char" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"feat: 한글로 시작하는 설명\""}}'

run_case "block: Korean char via --message" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit --message \"fix(scope): 버그 수정\""}}'

# Verify Korean is actually caught
python3 -c "
import re
pattern = re.compile(r'^(feat|fix|docs|refactor|chore|test|perf|ci|build|style)(\([a-z0-9-]+\))?: [a-z]')
assert not pattern.match('feat: 한글'), 'Korean first char should not match'
assert not pattern.match('fix(scope): 버그'), 'Korean first char with scope should not match'
" || { echo "  INTERNAL CHECK FAIL: Korean char regex verification"; exit 1; }

# ---------------------------------------------------------------------------
# BLOCK cases — unreplaced placeholder types
# ---------------------------------------------------------------------------

run_case "block: unreplaced <type> placeholder" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"<type>(scope): some description\""}}'

run_case "block: unreplaced 'type' literal not in allowed list" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"type(scope): some description\""}}'

run_case "block: unreplaced placeholder in full form" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"feat(scope): description\" && git commit -m \"<type>(scope): bad\""}}'

# ---------------------------------------------------------------------------
# BLOCK cases — scope without parentheses
# ---------------------------------------------------------------------------

run_case "block: scope without parentheses" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"feat scope: add feature\""}}'

run_case "block: space before colon" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"feat : add feature\""}}'

# ---------------------------------------------------------------------------
# BLOCK cases — -m extraction variants
# ---------------------------------------------------------------------------

run_case "block: -mMSG attached short form" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m\"updated stuff\""}}'

run_case "block: --message=MSG embedded form" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit --message=\"updated stuff\""}}'

run_case "block: -am combined flag bad title" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -am \"updated stuff\""}}'

run_case "block: chained git fetch && git commit bad title" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git fetch origin && git commit -m \"updated stuff\""}}'

run_case "block: backslash newline continuation" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit \\\\\n  -m \\\"updated stuff\\\"\"}}"

# ---------------------------------------------------------------------------
# BLOCK cases — gh pr create --title
# ---------------------------------------------------------------------------

run_case "block: gh pr create --title bad format" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title \"Update user authentication\""}}'

run_case "block: gh pr create --title uppercase" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title \"feat: Add user authentication\""}}'

run_case "block: gh pr create --title= embedded form" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title=\"Update user authentication\" --body \"some body\""}}'

run_case "pass: gh pr create --title valid format" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title \"feat(auth): add user authentication\" --body \"desc\""}}'

run_case "block: gh pr create -t bad format (short flag)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create -t \"Update user authentication\" --body \"desc\""}}'

run_case "pass: gh pr create -t valid format (short flag)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create -t \"feat(auth): add user authentication\" --body \"desc\""}}'

# ---------------------------------------------------------------------------
# BLOCK cases — gh issue create --title
# ---------------------------------------------------------------------------

run_case "block: gh issue create --title bad format" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title \"Add authentication feature\""}}'

run_case "block: gh issue create --title Korean first char" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title \"feat: 인증 기능 추가\""}}'

run_case "pass: gh issue create --title valid format" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title \"feat(auth): add authentication feature\" --body \"desc\""}}'

run_case "block: gh issue create -t bad format (short flag)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create -t \"Add authentication feature\" --body \"desc\""}}'

run_case "pass: gh issue create -t valid format (short flag)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create -t \"feat(auth): add authentication feature\" --body \"desc\""}}'

# ---------------------------------------------------------------------------
# Advisory mode (STRICT=0)
# ---------------------------------------------------------------------------

run_case "advisory mode: bad title exits 0 with stderr" \
  "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"updated stuff\""}}' \
  "PRAXIS_COMMIT_TITLE_FORMAT_STRICT=0"

run_case "advisory mode: valid title still silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"feat: add feature\""}}' \
  "PRAXIS_COMMIT_TITLE_FORMAT_STRICT=0"

run_case "advisory mode: gh pr create bad title exits 0 with stderr" \
  "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title \"bad title\""}}' \
  "PRAXIS_COMMIT_TITLE_FORMAT_STRICT=0"

# ---------------------------------------------------------------------------
# Custom allowed types (PRAXIS_COMMIT_TITLE_ALLOWED_TYPES)
# ---------------------------------------------------------------------------

run_case "custom types: 'release' in override, valid" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"release(v2): bump version\""}}' \
  "PRAXIS_COMMIT_TITLE_ALLOWED_TYPES=release,hotfix"

run_case "custom types: 'feat' removed, feat title blocked" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"feat: add feature\""}}' \
  "PRAXIS_COMMIT_TITLE_ALLOWED_TYPES=fix,docs"

run_case "custom types: single type allowed" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"hotfix: emergency patch\""}}' \
  "PRAXIS_COMMIT_TITLE_ALLOWED_TYPES=hotfix"

# ---------------------------------------------------------------------------
# Body protection: second -m is body, not checked
# ---------------------------------------------------------------------------

run_case "body in 2nd -m not flagged (only title checked)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"fix: short\" -m \"This body could be anything including Updated Stuff\""}}'

# ---------------------------------------------------------------------------
# git global flags (mirrors commit-title-length-check F1 regression)
# ---------------------------------------------------------------------------

run_case "git -C /path commit bad title (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git -C /some/path commit -m \"updated stuff\""}}'

run_case "git -c key=val commit bad title (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git -c commit.gpgsign=true commit -m \"updated stuff\""}}'

run_case "git -C /path commit valid title (silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git -C /some/path commit -m \"feat(scope): add feature\""}}'
# Fail-open guard opt-in (issue #498): main() must be @fail_open-wrapped;
# guard behavior is tested centrally in tests/test_hook_runtime.sh.
_failopen_out=$(python3 - << PYEOF 2>&1
import importlib.util
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert getattr(mod.main, "__wrapped__", None) is not None, "main is not @fail_open-wrapped"
print("OK")
PYEOF
)
_failopen_rc=$?
if [ "$_failopen_rc" -eq 0 ] && [ "$_failopen_out" = "OK" ]; then
  echo "PASS  [fail-open] main() is wrapped by the shared @fail_open guard"
  PASS=$((PASS+1))
else
  echo "FAIL  [fail-open] main() not @fail_open-wrapped (rc=$_failopen_rc out=$_failopen_out)"
  FAIL=$((FAIL+1)); FAILED_NAMES+=("fail-open guard wrapping")
fi



# ---------------------------------------------------------------------------
echo ""
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"
  for name in "${FAILED_NAMES[@]}"; do
    echo "  - $name"
  done
  exit 1
fi
exit 0
