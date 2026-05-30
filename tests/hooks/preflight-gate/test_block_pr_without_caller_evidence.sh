#!/usr/bin/env bash
# test-block-pr-without-caller-evidence.sh — coverage for the caller-chain gate
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   block → exit 2 + stderr non-empty
#   pass  → exit 0 + stderr empty
#
# Usage: bash hooks/test-block-pr-without-caller-evidence.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/block-pr-without-caller-evidence/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

run_case() {
  local name="$1" expected="$2" tool_name="$3" command="$4"
  local payload err_file rc
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": sys.argv[1],
    "tool_input": {"command": sys.argv[2]},
}))' "$tool_name" "$command")
  err_file=$(mktemp)
  echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  if [ "$expected" = "block" ]; then
    [ "$rc" -eq 2 ] && [ -n "$err_content" ] || ok=0
  else
    [ "$rc" -eq 0 ] && [ -z "$err_content" ] || ok=0
  fi

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; ((PASS++))
  else
    echo "FAIL [$expected→rc=$rc,stderr=$([ -n "$err_content" ] && echo non-empty || echo empty)] $name"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# BLOCK cases — no Caller chain verified: line
# ---------------------------------------------------------------------------

run_case "no caller line" block Bash \
  'gh pr create --title "fix: something" --body "## Summary\nsome fix\n\nCloses #10"'

run_case "empty body" block Bash \
  'gh pr create --title "feat: add thing" --body ""'

run_case "caller line value empty" block Bash \
  'gh pr create --body "Caller chain verified:   "'

run_case "marker inside closed fence" block Bash \
  'BODY=$(cat <<'"'"'EOF'"'"'
```
Caller chain verified: inside fence
```
some content
EOF
)
gh pr create --body "$BODY"'

run_case "marker inside unclosed fence" block Bash \
  'BODY=$(cat <<'"'"'EOF'"'"'
```
Caller chain verified: unclosed fence
EOF
)
gh pr create --body "$BODY"'

run_case "non-bash tool ignored but gh still blocked" block Bash \
  'gh pr create --title "fix: x" --body "no marker here"'

# ---------------------------------------------------------------------------
# PASS cases — Caller chain verified: line present
# ---------------------------------------------------------------------------

run_case "grep summary" pass Bash \
  'gh pr create --body "Caller chain verified: grep found 3 callers in src/ -- Closes #10"'

run_case "new symbol whitelist" pass Bash \
  'gh pr create --body "Caller chain verified: new symbol, no caller expected"'

run_case "planned caller whitelist" pass Bash \
  'gh pr create --body "Caller chain verified: planned caller in #200"'

run_case "NA docs-only" pass Bash \
  'gh pr create --body "Caller chain verified: N/A -- docs-only change"'

run_case "case insensitive" pass Bash \
  'gh pr create --body "caller chain verified: grep found 0 external callers"'

run_case "short -b flag" pass Bash \
  'gh pr create -b "Caller chain verified: new symbol, no caller expected"'

run_case "VAR assignment heredoc" pass Bash \
  'BODY=$(cat <<'"'"'EOF'"'"'
Caller chain verified: grep found 2 callers
EOF
)
gh pr create --body "$BODY"'

# ---------------------------------------------------------------------------
# PASS cases — allow conditions
# ---------------------------------------------------------------------------

run_case "cross-project --repo" pass Bash \
  'gh pr create --repo other-org/other-repo --body "no marker needed"'

run_case "cross-project -R" pass Bash \
  'gh pr create -R other-org/repo --body "no marker"'

run_case "--help passthrough" pass Bash \
  'gh pr create --help'

run_case "-h passthrough" pass Bash \
  'gh pr create -h'

run_case "template without body" pass Bash \
  'gh pr create --template pull_request_template.md'

run_case "not a pr create" pass Bash \
  'gh pr list --state open'

run_case "not gh at all" pass Bash \
  'git push origin main'

run_case "non-Bash tool" pass Edit \
  'gh pr create --body "no marker"'

run_case "env wrapper transparent" pass Bash \
  'env GH_TOKEN=xyz gh pr create --body "Caller chain verified: new symbol, no caller expected"'

# ---------------------------------------------------------------------------
# --body-file cases (issue #220)
# ---------------------------------------------------------------------------

# (a) body-file with marker → allow
run_case "body-file with marker" pass Bash \
  "$(
    f=$(mktemp)
    printf 'Caller chain verified: N/A\n' >"$f"
    printf 'gh pr create --title "fix: x" --body-file %s' "$f"
  )"

# (b) body-file without marker → block
run_case "body-file without marker" block Bash \
  "$(
    f=$(mktemp)
    printf '## Summary\nno marker here\n' >"$f"
    printf 'gh pr create --title "fix: x" --body-file %s' "$f"
  )"

# (c) inline --body with marker (regression)
run_case "inline body with marker regression" pass Bash \
  'gh pr create --body "Caller chain verified: inline check"'

# (d) body-file path does not exist → BLOCK (Codex #226: missing-file allow
# created a bypass for `cat <<EOF > /tmp/x && gh pr create --body-file /tmp/x`
# compound patterns, since the redirect side-effect has not run at PreToolUse
# time. Treat missing file as empty body so the marker check fires.)
run_case "body-file nonexistent path blocks" block Bash \
  'gh pr create --title "fix: x" --body-file /tmp/does-not-exist-praxis-220.md'

# (d') compound bash redirect-then-pr-create with no marker → BLOCK (regression
# guard for the exact bypass pattern Codex flagged).
run_case "compound redirect then pr create no marker" block Bash \
  'cat <<EOF > /tmp/does-not-exist-praxis-220.md
body without marker
EOF
gh pr create --title "fix: x" --body-file /tmp/does-not-exist-praxis-220.md'

# (e) body-file stdin dash → BLOCK (Codex round 3: stdin content uninspectable
# at PreToolUse time; allowing it was a hard-gate bypass.)
run_case "body-file stdin dash blocks" block Bash \
  'printf "no marker" | gh pr create --title "fix: x" --body-file -'

# (e') stdin + inline body marker → allow (marker present satisfies gate)
run_case "body-file stdin dash with inline body marker" pass Bash \
  'echo body | gh pr create --title "fix: x" --body-file - --body "Caller chain verified: pipe"'

# Codex round 4 — TOCTOU: pre-existing marker file overwritten in same command
# before `gh pr create` runs. Hook must treat the body-file as untrustworthy
# (empty body → block) because PreToolUse reads pre-overwrite content.
TOCTOU_FILE=/tmp/praxis220-toctou-test-$$.md
echo 'Caller chain verified: stale prior content' > "$TOCTOU_FILE"
run_case "body-file overwritten in same command blocks" block Bash \
  "echo 'no marker overwritten' > $TOCTOU_FILE && gh pr create --title 'fix: x' --body-file $TOCTOU_FILE"

# TOCTOU control — same path but no overwrite in this command → allow
run_case "body-file pre-existing with marker no overwrite passes" pass Bash \
  "gh pr create --title 'fix: x' --body-file $TOCTOU_FILE"

# Tee variant of the TOCTOU pattern
run_case "body-file tee-overwritten in same command blocks" block Bash \
  "echo body | tee $TOCTOU_FILE && gh pr create --title 'fix: x' --body-file $TOCTOU_FILE"

rm -f "$TOCTOU_FILE"

# (f) block message contains shared cascade hint when command is compound +
#     has a state-changing redirect (issue #229). Single-command blocks do NOT
#     get the hint — the agent already knows the single bash call didn't run.

_capture_stderr() {
  python3 -c '
import json, sys
payload = json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
})
sys.stdout.write(payload)
' "$1" | python3 "$ROOT_DIR/hooks/preflight-gate/block-pr-without-caller-evidence/impl.py" 2>&1 >/dev/null || true
}

# Positive: compound bash with heredoc redirect → hint appears
_hint_err=$(_capture_stderr 'cat <<EOF > /tmp/body.md
no marker here
EOF
gh pr create --body-file /tmp/body.md')
if printf '%s' "$_hint_err" | grep -q "PreToolUse rejection (block or denied ask) aborts ALL parts atomically"; then
  echo "PASS [hint] compound block message contains cascade hint"; ((PASS++))
else
  echo "FAIL [hint] compound block message missing cascade hint"; ((FAIL++))
  FAILED_NAMES+=("compound block missing cascade hint")
fi

# Negative: single-command block → hint does NOT appear (no cascade to warn about)
_hint_err=$(_capture_stderr 'gh pr create --body "no marker"')
if printf '%s' "$_hint_err" | grep -q "PreToolUse rejection"; then
  echo "FAIL [hint] single-command block leaked cascade hint"; ((FAIL++))
  FAILED_NAMES+=("single-command block leaked cascade hint")
else
  echo "PASS [hint] single-command block has no cascade hint"; ((PASS++))
fi

# ---------------------------------------------------------------------------
# Summary
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
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
