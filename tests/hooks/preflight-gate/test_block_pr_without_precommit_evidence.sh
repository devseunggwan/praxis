#!/usr/bin/env bash
# test-block-pr-without-precommit-evidence.sh — coverage for the pre-commit gate
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   block → exit 2 + stderr non-empty
#   pass  → exit 0 + stderr empty
#
# Usage: bash hooks/test-block-pr-without-precommit-evidence.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/block-pr-without-precommit-evidence/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# Attested-convention tiering (#1186, per #1159): the detection matrix below
# verifies marker parsing, not tier — attest via the strict env so every
# block expectation stays meaningful. Dedicated tier cases at the end
# override / unset this per case.
export PRAXIS_PR_EVIDENCE_STRICT=1

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
  elif [ "$expected" = "warn" ]; then
    # #1186 advisory tier: proceeds (rc 0), guidance ships, and the header
    # promised by the spec is present - the [advisory] prefix, the
    # escalation env by name, and a tier-neutral first line (no BLOCKED
    # banner, no cascade-abort hint: nothing was blocked).
    [ "$rc" -eq 0 ] && [ -n "$err_content" ] || ok=0
    echo "$err_content" | grep -q "\[advisory\]" || ok=0
    echo "$err_content" | grep -q "PRAXIS_PR_EVIDENCE_STRICT" || ok=0
    echo "$err_content" | grep -q "BLOCKED" && ok=0
    echo "$err_content" | grep -q "aborts ALL parts" && ok=0
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
# BLOCK cases — no Pre-commit evidence line
# ---------------------------------------------------------------------------

run_case "no precommit line" block Bash \
  'gh pr create --title "fix: something" --body "## Summary\nsome fix\n\nCloses #10"'

run_case "empty body" block Bash \
  'gh pr create --title "feat: add thing" --body ""'

run_case "verified line value empty" block Bash \
  'gh pr create --body "Pre-commit verified:   "'

run_case "CI line value empty" block Bash \
  'gh pr create --body "Pre-commit: verified by CI   "'

run_case "n/a line value empty" block Bash \
  'gh pr create --body "Pre-commit: n/a   "'

run_case "marker inside closed fence" block Bash \
  'BODY=$(cat <<'"'"'EOF'"'"'
```
Pre-commit verified: inside fence
```
some content
EOF
)
gh pr create --body "$BODY"'

run_case "marker inside unclosed fence" block Bash \
  'BODY=$(cat <<'"'"'EOF'"'"'
```
Pre-commit verified: unclosed fence
EOF
)
gh pr create --body "$BODY"'

run_case "plain body no marker" block Bash \
  'gh pr create --title "fix: x" --body "no marker here"'

# Pre-commit-adjacent-but-not-matching strings must NOT pass
run_case "wrong keyword precommit no dash" block Bash \
  'gh pr create --body "Precommit verified: missing dash"'

run_case "marker missing colon" block Bash \
  'gh pr create --body "Pre-commit verified ran tests"'

# ---------------------------------------------------------------------------
# Differs from sibling: --repo presence still requires marker
# ---------------------------------------------------------------------------

run_case "--repo with no marker still blocks" block Bash \
  'gh pr create --repo other-org/other-repo --title "fix: x" --body "no marker"'

run_case "-R with no marker still blocks" block Bash \
  'gh pr create -R other-org/repo --body "no marker"'

# ---------------------------------------------------------------------------
# PASS cases — 3 marker patterns
# ---------------------------------------------------------------------------

run_case "verified: ran command" pass Bash \
  'gh pr create --body "Pre-commit verified: ran pre-commit run --all-files"'

run_case "verified: free text" pass Bash \
  'gh pr create --body "Pre-commit verified: lint passed locally"'

run_case "CI: workflow path" pass Bash \
  'gh pr create --body "Pre-commit: verified by CI (.github/workflows/ci.yml)"'

run_case "CI: url" pass Bash \
  'gh pr create --body "Pre-commit: verified by CI (https://github.com/o/r/actions/runs/123)"'

run_case "n/a: docs-only" pass Bash \
  'gh pr create --body "Pre-commit: n/a (docs-only repo)"'

run_case "n/a: legacy" pass Bash \
  'gh pr create --body "Pre-commit: n/a (legacy repo, lint deferred)"'

run_case "case insensitive verified" pass Bash \
  'gh pr create --body "pre-commit verified: lowercase"'

run_case "case insensitive CI" pass Bash \
  'gh pr create --body "PRE-COMMIT: VERIFIED BY CI (file.yml)"'

run_case "case insensitive n/a" pass Bash \
  'gh pr create --body "Pre-Commit: N/A (mixed case)"'

run_case "short -b flag" pass Bash \
  'gh pr create -b "Pre-commit verified: ran tests"'

run_case "VAR assignment heredoc" pass Bash \
  'BODY=$(cat <<'"'"'EOF'"'"'
Pre-commit verified: ran lint
EOF
)
gh pr create --body "$BODY"'

# ---------------------------------------------------------------------------
# PASS cases — allow conditions
# ---------------------------------------------------------------------------

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
  'env GH_TOKEN=xyz gh pr create --body "Pre-commit verified: ran lint"'

# ---------------------------------------------------------------------------
# --body-file cases (mirror sibling)
# ---------------------------------------------------------------------------

# (a) body-file with marker → allow
run_case "body-file with marker" pass Bash \
  "$(
    f=$(mktemp)
    printf 'Pre-commit: n/a (docs-only)\n' >"$f"
    printf 'gh pr create --title "fix: x" --body-file %s' "$f"
  )"

# (b) body-file without marker → block
run_case "body-file without marker" block Bash \
  "$(
    f=$(mktemp)
    printf '## Summary\nno marker here\n' >"$f"
    printf 'gh pr create --title "fix: x" --body-file %s' "$f"
  )"

# (c) inline --body with marker
run_case "inline body with marker" pass Bash \
  'gh pr create --body "Pre-commit verified: inline check"'

# (d) body-file path does not exist → BLOCK
run_case "body-file nonexistent path blocks" block Bash \
  'gh pr create --title "fix: x" --body-file /tmp/does-not-exist-praxis-406.md'

# (d') compound bash redirect-then-pr-create with no marker → BLOCK
run_case "compound redirect then pr create no marker" block Bash \
  'cat <<EOF > /tmp/does-not-exist-praxis-406.md
body without marker
EOF
gh pr create --title "fix: x" --body-file /tmp/does-not-exist-praxis-406.md'

# (e) body-file stdin dash → BLOCK
run_case "body-file stdin dash blocks" block Bash \
  'printf "no marker" | gh pr create --title "fix: x" --body-file -'

# (e') stdin + inline body marker → allow
run_case "body-file stdin dash with inline body marker" pass Bash \
  'echo body | gh pr create --title "fix: x" --body-file - --body "Pre-commit: n/a (pipe)"'

# TOCTOU: pre-existing marker file overwritten in same command
TOCTOU_FILE=/tmp/praxis406-toctou-test-$$.md
echo 'Pre-commit verified: stale prior content' > "$TOCTOU_FILE"
run_case "body-file overwritten in same command blocks" block Bash \
  "echo 'no marker overwritten' > $TOCTOU_FILE && gh pr create --title 'fix: x' --body-file $TOCTOU_FILE"

# TOCTOU control — same path, no overwrite → allow
run_case "body-file pre-existing with marker no overwrite passes" pass Bash \
  "gh pr create --title 'fix: x' --body-file $TOCTOU_FILE"

# Tee variant of TOCTOU
run_case "body-file tee-overwritten in same command blocks" block Bash \
  "echo body | tee $TOCTOU_FILE && gh pr create --title 'fix: x' --body-file $TOCTOU_FILE"

rm -f "$TOCTOU_FILE"

# ---------------------------------------------------------------------------
# body-file path-not-found diagnostic (praxis #608)
# ---------------------------------------------------------------------------

# Capture stderr for a given command — mirrors _capture_stderr below but
# is defined early so these cases can use it inline.
_capture_stderr_for() {
  python3 -c '
import json, sys
payload = json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
})
sys.stdout.write(payload)
' "$1" | { python3 "$ROOT_DIR/hooks/preflight-gate/block-pr-without-precommit-evidence/impl.py" >/dev/null; } 2>&1 || true
}

# path-not-found → DISTINCT diagnostic (not generic token-missing message)
_missing_path="/tmp/praxis-608-does-not-exist-$$.md"
_pnf_err=$(_capture_stderr_for "gh pr create --title 'fix: x' --body-file $_missing_path")
_pnf_rc=0
# Must be a block (rc=2 handled by run_case above) — here we assert message content
if printf '%s' "$_pnf_err" | grep -q "body-file not found"; then
  echo "PASS [pnf-diagnostic] missing body-file emits path-not-found message"; ((PASS++))
else
  echo "FAIL [pnf-diagnostic] missing body-file did not emit path-not-found message (stderr: $_pnf_err)"; ((FAIL++))
  FAILED_NAMES+=("pnf-diagnostic: path-not-found message absent")
fi
if printf '%s' "$_pnf_err" | grep -q "absolute path"; then
  echo "PASS [pnf-diagnostic] path-not-found message advises absolute path"; ((PASS++))
else
  echo "FAIL [pnf-diagnostic] path-not-found message missing absolute-path advice (stderr: $_pnf_err)"; ((FAIL++))
  FAILED_NAMES+=("pnf-diagnostic: absolute-path advice absent")
fi
# Must NOT emit the generic token-missing message
if printf '%s' "$_pnf_err" | grep -q "without pre-commit evidence"; then
  echo "FAIL [pnf-diagnostic] missing body-file leaked generic token-missing message"; ((FAIL++))
  FAILED_NAMES+=("pnf-diagnostic: generic message leaked")
else
  echo "PASS [pnf-diagnostic] missing body-file does not emit generic token-missing message"; ((PASS++))
fi

# absolute path + token → PASS (no block, no stderr)
_abs_body=$(mktemp)
printf 'Pre-commit verified: ran ruff + tests\n' >"$_abs_body"
_abs_err=$(_capture_stderr_for "gh pr create --title 'fix: x' --body-file $_abs_body")
if [ -z "$_abs_err" ]; then
  echo "PASS [pnf-regression] absolute body-file with token passes (no block)"; ((PASS++))
else
  echo "FAIL [pnf-regression] absolute body-file with token was blocked (stderr: $_abs_err)"; ((FAIL++))
  FAILED_NAMES+=("pnf-regression: absolute body-file with token blocked")
fi
rm -f "$_abs_body"

# ---------------------------------------------------------------------------
# Cascade hint (mirror sibling)
# ---------------------------------------------------------------------------

_capture_stderr() {
  python3 -c '
import json, sys
payload = json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
})
sys.stdout.write(payload)
' "$1" | { python3 "$ROOT_DIR/hooks/preflight-gate/block-pr-without-precommit-evidence/impl.py" >/dev/null; } 2>&1 || true
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

# Negative: single-command block → hint does NOT appear
_hint_err=$(_capture_stderr 'gh pr create --body "no marker"')
if printf '%s' "$_hint_err" | grep -q "PreToolUse rejection"; then
  echo "FAIL [hint] single-command block leaked cascade hint"; ((FAIL++))
  FAILED_NAMES+=("single-command block leaked cascade hint")
else
  echo "PASS [hint] single-command block has no cascade hint"; ((PASS++))
fi

# Shared pr-body evidence checklist (#824, mirror sibling): the deny message
# must also enumerate the SIBLING gate's token so one deny teaches all tokens.
_checklist_err=$(_capture_stderr 'gh pr create --body "no marker"')
if printf '%s' "$_checklist_err" | grep -q "Caller chain verified:"; then
  echo "PASS [checklist] block message enumerates sibling Caller-chain token"; ((PASS++))
else
  echo "FAIL [checklist] block message missing sibling Caller-chain token"; ((FAIL++))
  FAILED_NAMES+=("checklist missing sibling Caller-chain token")
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

# ---------------------------------------------------------------------------
# #1186 attested-convention tiering — deny only when attested
# ---------------------------------------------------------------------------
unset PRAXIS_PR_EVIDENCE_STRICT
run_case "1186: no marker, env unset → warn (advisory default)" warn Bash \
  'gh pr create --title "fix: x" --body "## Summary\nplain body"'
run_case "1186: marker present, env unset → pass (silent)" pass Bash \
  'gh pr create --title "fix: x" --body "Pre-commit verified: pre-commit run --all-files clean"'
export PRAXIS_PR_EVIDENCE_STRICT=0
run_case "1186: no marker, STRICT=0 → warn (explicit advisory)" warn Bash \
  'gh pr create --title "fix: x" --body "## Summary\nplain body"'
export PRAXIS_PR_EVIDENCE_STRICT=1
run_case "1186: marker present, STRICT=1 → pass (silent, both tiers)" pass Bash \
  'gh pr create --title "fix: x" --body "Pre-commit verified: pre-commit run --all-files clean"'
run_case "1186: no marker, STRICT=1 → block (attested)" block Bash \
  'gh pr create --title "fix: x" --body "## Summary\nplain body"'
# Negative control: a non-pr-create command stays silent in BOTH tiers.
unset PRAXIS_PR_EVIDENCE_STRICT
run_case "1186: gh pr list, env unset → pass (out of scope)" pass Bash \
  'gh pr list --state open'

# Missing body-file diagnostic follows the same tiering (#1186).
run_case "1186: body-file missing, env unset → warn" warn Bash \
  'gh pr create --title "fix: x" --body-file /nonexistent/body-1186.md'
export PRAXIS_PR_EVIDENCE_STRICT=1
run_case "1186: body-file missing, STRICT=1 → block" block Bash \
  'gh pr create --title "fix: x" --body-file /nonexistent/body-1186.md'
unset PRAXIS_PR_EVIDENCE_STRICT

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
