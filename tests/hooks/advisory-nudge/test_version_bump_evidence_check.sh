#!/usr/bin/env bash
# test_version_bump_evidence_check.sh — coverage for version-bump-evidence-check hook
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   warn   → exit 0  + stderr non-empty  (advisory mode)
#   pass   → exit 0  + stderr empty
#   block  → exit 2  + stderr non-empty  (strict mode only)
#   infra  → exit 0  (fail-open)
#
# Usage: bash tests/test_version_bump_evidence_check.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/../../../hooks/version-bump-evidence-check.sh"
PY_HOOK="$SCRIPT_DIR/../../../hooks/advisory-nudge/version-bump-evidence-check/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Helper: run a test case with inline --body
# ---------------------------------------------------------------------------
# Usage: run_case <name> <expected> <gh_command_with_body>
# expected: "warn" | "pass" | "infra" (advisory mode)
run_case() {
  local name="$1" expected="$2" command="$3"
  local payload err_file rc
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}))' "$command" 2>/dev/null)
  err_file=$(mktemp)
  unset PRAXIS_VERSION_BUMP_STRICT PRAXIS_VERSION_BUMP_BYPASS
  echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  case "$expected" in
    warn) [ "$rc" -eq 0 ] && [ -n "$err_content" ] || ok=0 ;;
    pass) [ "$rc" -eq 0 ] && [ -z "$err_content" ] || ok=0 ;;
    infra) [ "$rc" -eq 0 ] || ok=0 ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; ((PASS++))
  else
    echo "FAIL [$expected→rc=$rc,stderr=$([ -n "$err_content" ] && echo non-empty || echo empty)] $name"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Helper: run with PRAXIS_VERSION_BUMP_STRICT=1
# ---------------------------------------------------------------------------
run_case_strict() {
  local name="$1" expected="$2" command="$3"
  local payload err_file rc
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}))' "$command" 2>/dev/null)
  err_file=$(mktemp)
  echo "$payload" | PRAXIS_VERSION_BUMP_STRICT=1 "$HOOK" >/dev/null 2>"$err_file"
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  case "$expected" in
    block) [ "$rc" -eq 2 ] && [ -n "$err_content" ] || ok=0 ;;
    pass)  [ "$rc" -eq 0 ] && [ -z "$err_content" ] || ok=0 ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected/strict] $name"; ((PASS++))
  else
    echo "FAIL [$expected/strict→rc=$rc,stderr=$([ -n "$err_content" ] && echo non-empty || echo empty)] $name"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Helper: run with PRAXIS_VERSION_BUMP_BYPASS=1
# ---------------------------------------------------------------------------
run_case_bypass() {
  local name="$1" command="$2"
  local payload err_file rc
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}))' "$command" 2>/dev/null)
  err_file=$(mktemp)
  echo "$payload" | PRAXIS_VERSION_BUMP_BYPASS=1 "$HOOK" >/dev/null 2>"$err_file"
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"

  if [ "$rc" -eq 0 ] && [ -z "$err_content" ]; then
    echo "PASS [bypass] $name"; ((PASS++))
  else
    echo "FAIL [bypass→rc=$rc,stderr=$([ -n "$err_content" ] && echo non-empty || echo empty)] $name"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Helper: run with a --body-file (reads file)
# ---------------------------------------------------------------------------
run_case_file() {
  local name="$1" expected="$2" body="$3"
  local tmp_file payload err_file rc
  tmp_file=$(mktemp /tmp/praxis-test-vbump-XXXXXX.md)
  printf '%s' "$body" >"$tmp_file"
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": f"gh issue create --title \"bump\" --body-file {sys.argv[1]}"},
}))' "$tmp_file" 2>/dev/null)
  err_file=$(mktemp)
  unset PRAXIS_VERSION_BUMP_STRICT PRAXIS_VERSION_BUMP_BYPASS
  echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"
  rm -f "$tmp_file"

  local ok=1
  case "$expected" in
    warn) [ "$rc" -eq 0 ] && [ -n "$err_content" ] || ok=0 ;;
    pass) [ "$rc" -eq 0 ] && [ -z "$err_content" ] || ok=0 ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; ((PASS++))
  else
    echo "FAIL [$expected→rc=$rc,stderr=$([ -n "$err_content" ] && echo non-empty || echo empty)] $name"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}

# ===========================================================================
# TRIGGER cases (advisory mode → warn: exit 0, stderr non-empty)
# ===========================================================================

# S1: version pair with arrow
run_case "version pair arrow → triggers" warn \
  'gh issue create --title "bump" --body "Upgrading from v24.0 → v25.0"'

run_case "version pair dash-arrow → triggers" warn \
  'gh pr create --title "bump" --body "migrating v1.2 -> v1.3"'

run_case "version pair to-word → triggers" warn \
  'gh issue create --body "upgrading v0.9 to v1.0"'

run_case "version pair v-prefix on right only → triggers" warn \
  'gh issue create --body "bumping 24.0 -> v25.0"'

run_case "version pair uppercase V → triggers" warn \
  'gh pr create --body "V1.2 → V1.3"'

# Round-2 critic M1: agent-authored bodies often omit whitespace around `-->`.
run_case "version pair no-space double-dash → triggers (M1)" warn \
  'gh pr create --body "upgrading v1.2-->v1.3 today"'

run_case "version pair no-space single-dash → triggers (M1)" warn \
  'gh pr create --body "v1.2->v1.3 cleanup"'

# Round-2 critic M2: language-runtime mentions without `v`/`V` prefix should
# NOT trigger — these are common in retrospect bodies (e.g. "Python 3.11 to 3.12").
run_case "language runtime no v-prefix → silent (M2)" pass \
  'gh issue create --body "Retrospect: we migrated Python 3.11 to 3.12 last quarter"'

run_case "java runtime no v-prefix → silent (M2)" pass \
  'gh pr create --body "We are running Java 11 to 17 across all clusters"'

# S2: bump-phrase
run_case "bump sdk from ... to → triggers" warn \
  'gh issue create --body "bump sdk from 3.0 to 4.0"'

run_case "bump api from ... to → triggers" warn \
  'gh pr create --body "Bump API from v2.1 to v3.0"'

run_case "bump client from ... to → triggers" warn \
  'gh issue create --body "bump client from 1.0 to 2.0 for compatibility"'

# S3: deprecation keyword + version pair
run_case "deprecated + version pair → triggers" warn \
  'gh issue create --body "The SDK is deprecated as of v1.0 → v2.0"'

run_case "EOL + version pair → triggers" warn \
  'gh pr create --body "v3.0 -> v4.0 eol migration required"'

run_case "breaking change + version pair → triggers" warn \
  'gh issue create --body "breaking change in v1.5 -> v2.0"'

# ===========================================================================
# FALSE-POSITIVE CONTROL — S3 keyword alone (no version pair) must NOT trigger
# ===========================================================================

run_case "deprecated keyword alone → silent" pass \
  'gh issue create --body "The old approach was deprecated last year in our retrospect"'

run_case "sunset keyword alone → silent" pass \
  'gh issue create --body "We are sunsetting the feature after review"'

run_case "EOL keyword alone → silent" pass \
  'gh pr create --body "This is related to our EOL strategy document"'

run_case "breaking change alone → silent" pass \
  'gh issue create --body "This is a breaking change in our internal API design"'

# ===========================================================================
# EVIDENCE PRESENT — should be silent (pass)
# ===========================================================================

run_case "changelog URL present → pass" pass \
  'gh issue create --body "v1.0 → v2.0 see https://github.com/foo/bar/releases/tag/v2.0 for details"'

run_case "releases URL present → pass" pass \
  'gh pr create --body "bump sdk from 1.0 to 2.0\nhttps://pypi.org/project/some-sdk/releases"'

run_case "Fetched: line present → pass" pass \
  'gh issue create --body "v1.0 -> v2.0\nFetched: https://docs.example.com/changelog"'

run_case "Cross-reference matrix heading → pass" pass \
  'gh pr create --body "bump api from 2.0 to 3.0\n## Cross-reference matrix\n| col | val |\n| --- | --- |"'

run_case "Inventory heading → pass" pass \
  'gh issue create --body "deprecated + v1.0 to v2.0\n# Inventory\nsome content"'

# ===========================================================================
# BYPASS env var
# ===========================================================================

run_case_bypass "PRAXIS_VERSION_BUMP_BYPASS=1 → silent" \
  'gh issue create --body "v1.0 → v2.0 no evidence"'

# ===========================================================================
# STRICT MODE — exit 2 when triggered
# ===========================================================================

run_case_strict "strict mode + no evidence → block" block \
  'gh pr create --body "bump sdk from 1.0 to 2.0 no changelog"'

run_case_strict "strict mode + evidence → pass" pass \
  'gh pr create --body "bump sdk from 1.0 to 2.0\nFetched: https://sdk.example.com/releases"'

# ===========================================================================
# --body-file path read
# ===========================================================================

run_case_file "body-file with version pair → warn" warn \
  "Upgrading v24.0 -> v25.0 without any evidence"

run_case_file "body-file with changelog URL → pass" pass \
  "v1.0 → v2.0 see https://github.com/foo/bar/releases for details"

run_case_file "body-file empty → pass (no trigger)" pass ""

# ===========================================================================
# INFRASTRUCTURE / fail-open
# ===========================================================================

# Missing body-file → fail-open (exit 0, no output)
_infra_payload='{"tool_name":"Bash","tool_input":{"command":"gh issue create --body-file /tmp/does-not-exist-praxis-327.md"}}'
_err_file=$(mktemp)
echo "$_infra_payload" | "$HOOK" >/dev/null 2>"$_err_file"
_rc=$?
_err_content=$(cat "$_err_file"); rm -f "$_err_file"
if [ "$_rc" -eq 0 ]; then
  echo "PASS [infra] missing body-file → fail-open exit 0"; ((PASS++))
else
  echo "FAIL [infra] missing body-file → rc=$_rc (expected 0)"; ((FAIL++)); FAILED_NAMES+=("missing body-file fail-open")
fi

# Malformed stdin → fail-open (exit 0)
_err_file=$(mktemp)
echo "not-json" | "$HOOK" >/dev/null 2>"$_err_file"
_rc=$?
rm -f "$_err_file"
if [ "$_rc" -eq 0 ]; then
  echo "PASS [infra] malformed stdin → fail-open exit 0"; ((PASS++))
else
  echo "FAIL [infra] malformed stdin → rc=$_rc (expected 0)"; ((FAIL++)); FAILED_NAMES+=("malformed stdin fail-open")
fi

# Non-Bash tool → pass (hook is Bash-only)
_err_file=$(mktemp)
echo '{"tool_name":"Edit","tool_input":{"file_path":"/tmp/x","old_string":"a","new_string":"b"}}' | "$HOOK" >/dev/null 2>"$_err_file"
_rc=$?
_err_content=$(cat "$_err_file"); rm -f "$_err_file"
if [ "$_rc" -eq 0 ] && [ -z "$_err_content" ]; then
  echo "PASS [pass] non-Bash tool → silent"; ((PASS++))
else
  echo "FAIL [pass] non-Bash tool → rc=$_rc stderr=$([ -n "$_err_content" ] && echo non-empty || echo empty)"; ((FAIL++)); FAILED_NAMES+=("non-Bash tool silent")
fi

# Not a gh issue/pr command → silent
run_case "git push → silent" pass 'git push origin main'
run_case "gh pr list → silent" pass 'gh pr list --state open'

# ===========================================================================
# Summary
# ===========================================================================

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
