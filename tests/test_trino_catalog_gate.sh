#!/usr/bin/env bash
# test_trino_catalog_gate.sh — coverage for hooks/trino-catalog-gate.py
#
# Synthesizes Claude Code PreToolUse hook payloads against a private marker
# file (via PRAXIS_TRINO_CATALOG_GATE_FILE) and asserts:
#
#   pass   → rc=0, stdout empty or non-deny, stderr empty
#   deny   → rc=0, stdout JSON has permissionDecision "deny"
#
# Covers the 6 cases from the issue #321 verification matrix (a–f).
#
# Usage: bash tests/test_trino_catalog_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$ROOT_DIR/hooks/trino-catalog-gate.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "FAIL: python3 not available" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# Each case gets a fresh temp dir so state does not leak between cases.
new_marker_file() {
  mktemp -t praxis-catalog-gate-XXXXXX
}

# run_case name expectation env_extra payload_json
#   expectation:
#     "pass" — rc=0; stdout does NOT have permissionDecision=deny
#     "deny" — rc=0; stdout JSON has permissionDecision == "deny"
#   env_extra: space-separated KEY=VALUE pairs (added via env -i)
run_case() {
  local name="$1" expectation="$2" envs="$3" payload="$4"

  local marker_file
  marker_file=$(new_marker_file)
  # Remove it so the default "no SHOW CATALOGS yet" state is enforced.
  rm -f "$marker_file"

  local stdout_file stderr_file
  stdout_file=$(mktemp)
  stderr_file=$(mktemp)

  echo "$payload" | env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" \
    PRAXIS_TRINO_CATALOG_GATE_FILE="$marker_file" \
    $envs \
    python3 "$HOOK" >"$stdout_file" 2>"$stderr_file"
  local rc=$?
  local out err
  out=$(cat "$stdout_file")
  err=$(cat "$stderr_file")
  rm -f "$stdout_file" "$stderr_file" "$marker_file" 2>/dev/null

  local ok=1
  case "$expectation" in
    pass)
      [ "$rc" -eq 0 ] || ok=0
      # Stdout must NOT contain a deny decision.
      if [ -n "$out" ]; then
        echo "$out" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    decision = d.get('hookSpecificOutput', {}).get('permissionDecision', '')
    sys.exit(1 if decision == 'deny' else 0)
except Exception:
    sys.exit(0)
" 2>/dev/null || ok=0
      fi
      ;;
    deny)
      [ "$rc" -eq 0 ] || ok=0
      echo "$out" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    sys.exit(0 if d.get('hookSpecificOutput', {}).get('permissionDecision', '') == 'deny' else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null || ok=0
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

# run_show_then_query tests the two-invocation flow: run SHOW CATALOGS first
# (which creates the marker), then run a 3-part query — should pass.
run_show_then_query() {
  local name="$1" show_payload="$2" query_payload="$3"

  local marker_file
  marker_file=$(new_marker_file)
  rm -f "$marker_file"

  # Step 1: SHOW CATALOGS — creates marker.
  echo "$show_payload" | env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" \
    PRAXIS_TRINO_CATALOG_GATE_FILE="$marker_file" \
    python3 "$HOOK" >/dev/null 2>&1

  # Step 2: 3-part query — should pass because marker exists.
  local stdout_file stderr_file
  stdout_file=$(mktemp)
  stderr_file=$(mktemp)

  echo "$query_payload" | env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" \
    PRAXIS_TRINO_CATALOG_GATE_FILE="$marker_file" \
    python3 "$HOOK" >"$stdout_file" 2>"$stderr_file"
  local rc=$?
  local out err
  out=$(cat "$stdout_file")
  err=$(cat "$stderr_file")
  rm -f "$stdout_file" "$stderr_file" "$marker_file" 2>/dev/null

  local ok=1
  [ "$rc" -eq 0 ] || ok=0
  if [ -n "$out" ]; then
    echo "$out" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    decision = d.get('hookSpecificOutput', {}).get('permissionDecision', '')
    sys.exit(1 if decision == 'deny' else 0)
except Exception:
    sys.exit(0)
" 2>/dev/null || ok=0
  fi

  if [ "$ok" -eq 1 ]; then
    PASS=$((PASS + 1))
    echo "  PASS  $name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    echo "  FAIL  $name (rc=$rc, expected=pass-after-show)"
    [ -n "$out" ] && echo "        stdout: $(echo "$out" | head -c 400)"
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -c 400)"
  fi
}

# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

make_trino_payload() {
  # $1 = query string
  python3 -c "
import json, sys
payload = {
    'session_id': 'test-session-321',
    'tool_name': 'mcp__laplace-trino__trino_query',
    'tool_input': {'query': sys.argv[1]},
    'cwd': '/tmp',
}
print(json.dumps(payload))
" "$1"
}

make_other_tool_payload() {
  # $1 = tool_name, $2 = query string
  python3 -c "
import json, sys
payload = {
    'session_id': 'test-session-321',
    'tool_name': sys.argv[1],
    'tool_input': {'query': sys.argv[2]},
    'cwd': '/tmp',
}
print(json.dumps(payload))
" "$1" "$2"
}

# ---------------------------------------------------------------------------
# Case (a): SHOW CATALOGS query passes + creates marker
# ---------------------------------------------------------------------------
echo ""
echo "Case (a): SHOW CATALOGS passes and creates marker"
run_show_then_query \
  "SHOW CATALOGS then 3-part query → pass" \
  "$(make_trino_payload 'SHOW CATALOGS')" \
  "$(make_trino_payload 'SELECT * FROM iceberg.foo.bar')"

# Verify SHOW CATALOGS itself passes (no block on the SHOW CATALOGS call).
run_case \
  "SHOW CATALOGS itself passes" \
  "pass" \
  "" \
  "$(make_trino_payload 'SHOW CATALOGS')"

# ---------------------------------------------------------------------------
# Case (b): 3-part reference without prior SHOW CATALOGS → blocked
# ---------------------------------------------------------------------------
echo ""
echo "Case (b): 3-part reference without marker → deny"
run_case \
  "SELECT FROM iceberg.foo.bar without marker → deny" \
  "deny" \
  "" \
  "$(make_trino_payload 'SELECT * FROM iceberg.foo.bar LIMIT 10')"

run_case \
  "JOIN with catalog.schema.table without marker → deny" \
  "deny" \
  "" \
  "$(make_trino_payload 'SELECT a.id FROM t a JOIN hive.warehouse.orders o ON a.id = o.id')"

# ---------------------------------------------------------------------------
# Case (c): Inline bypass marker — must be in SQL comment (M1 fix)
# ---------------------------------------------------------------------------
echo ""
echo "Case (c): inline bypass marker → pass (only when in comment syntax)"

# Line comment: valid bypass
run_case \
  "3-part reference + '-- catalog-enumerated: verified' line comment → pass" \
  "pass" \
  "" \
  "$(make_trino_payload 'SELECT * FROM iceberg.foo.bar -- catalog-enumerated: verified')"

# Block comment: valid bypass
run_case \
  "3-part reference + '/* catalog-enumerated: verified */' block comment → pass" \
  "pass" \
  "" \
  "$(make_trino_payload 'SELECT * FROM iceberg.foo.bar /* catalog-enumerated: verified */')"

# String literal: NOT a bypass (M1 fix — was silently bypassing before)
run_case \
  "marker in string literal (not comment) → deny (M1 fix)" \
  "deny" \
  "" \
  "$(make_trino_payload "SELECT 'catalog-enumerated: verified' AS msg FROM iceberg.foo.bar")"

# ---------------------------------------------------------------------------
# Case (d): 2-part reference (not 3-part) → passes without marker
# ---------------------------------------------------------------------------
echo ""
echo "Case (d): SHOW SCHEMAS (2-part, no catalog ref) → pass"
run_case \
  "SHOW SCHEMAS FROM iceberg (no 3-part) → pass" \
  "pass" \
  "" \
  "$(make_trino_payload 'SHOW SCHEMAS FROM iceberg')"

run_case \
  "SELECT FROM schema.table (2-part) → pass" \
  "pass" \
  "" \
  "$(make_trino_payload 'SELECT * FROM foo.bar LIMIT 10')"

# ---------------------------------------------------------------------------
# Case (e): PRAXIS_TRINO_CATALOG_GATE=skip bypasses
# ---------------------------------------------------------------------------
echo ""
echo "Case (e): PRAXIS_TRINO_CATALOG_GATE=skip → bypass"
run_case \
  "PRAXIS_TRINO_CATALOG_GATE=skip with 3-part ref → pass" \
  "pass" \
  "PRAXIS_TRINO_CATALOG_GATE=skip" \
  "$(make_trino_payload 'SELECT * FROM iceberg.foo.bar')"

# ---------------------------------------------------------------------------
# Case (f): Non-Trino MCP tool → passthrough
# ---------------------------------------------------------------------------
echo ""
echo "Case (f): non-Trino tool → passthrough"
run_case \
  "MySQL MCP query tool → pass" \
  "pass" \
  "" \
  "$(make_other_tool_payload 'mcp__laplace-mysql__query_raw_sql' 'SELECT * FROM iceberg.foo.bar')"

run_case \
  "Non-MCP Bash tool → pass" \
  "pass" \
  "" \
  "$(python3 -c 'import json; print(json.dumps({"session_id":"test-session-321","tool_name":"Bash","tool_input":{"command":"echo iceberg.foo.bar"},"cwd":"/tmp"}))')"

# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
echo ""
echo "Edge cases"
run_case \
  "Malformed JSON → pass (fail-open)" \
  "pass" \
  "" \
  "not valid json at all"

run_case \
  "Empty query field → pass" \
  "pass" \
  "" \
  "$(make_trino_payload '')"

run_case \
  "3-part ref in SQL comment only → pass (comment stripped)" \
  "pass" \
  "" \
  "$(make_trino_payload 'SELECT 1 -- FROM iceberg.foo.bar')"

# ---------------------------------------------------------------------------
# M2: DML/DDL catalog references (INSERT, MERGE, UPDATE, CREATE TABLE/VIEW)
# ---------------------------------------------------------------------------
echo ""
echo "M2: DML/DDL catalog references → deny without marker"
run_case \
  "INSERT INTO catalog.schema.table → deny (M2 fix)" \
  "deny" \
  "" \
  "$(make_trino_payload 'INSERT INTO iceberg.foo.bar VALUES (1, 2, 3)')"

run_case \
  "MERGE INTO catalog.schema.table → deny (M2 fix)" \
  "deny" \
  "" \
  "$(make_trino_payload 'MERGE INTO iceberg.foo.bar t USING src s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.v = s.v')"

run_case \
  "UPDATE catalog.schema.table → deny (M2 fix)" \
  "deny" \
  "" \
  "$(make_trino_payload 'UPDATE iceberg.foo.bar SET col = 1 WHERE id = 42')"

run_case \
  "CREATE TABLE catalog.schema.table AS SELECT → deny (M2 fix)" \
  "deny" \
  "" \
  "$(make_trino_payload 'CREATE TABLE iceberg.foo.new_bar AS SELECT * FROM src')"

run_case \
  "CREATE VIEW catalog.schema.view AS SELECT → deny (M2 fix)" \
  "deny" \
  "" \
  "$(make_trino_payload 'CREATE VIEW iceberg.foo.my_view AS SELECT id FROM src')"

# DELETE FROM: covered by existing FROM arm
run_case \
  "DELETE FROM catalog.schema.table → deny (M2 fix, FROM arm)" \
  "deny" \
  "" \
  "$(make_trino_payload 'DELETE FROM iceberg.foo.bar WHERE id = 1')"

# ---------------------------------------------------------------------------
# M3: Multi-statement (SHOW CATALOGS + 3-part ref in same payload)
# ---------------------------------------------------------------------------
echo ""
echo "M3: multi-statement — SHOW CATALOGS + 3-part ref in same payload → deny"
run_case \
  "SHOW CATALOGS; SELECT FROM iceberg.foo.bar → deny (M3 fix, no prior marker)" \
  "deny" \
  "" \
  "$(make_trino_payload 'SHOW CATALOGS; SELECT * FROM iceberg.foo.bar')"

# SHOW CATALOGS alone (no 3-part ref) still passes
run_case \
  "SHOW CATALOGS alone → pass (no 3-part ref)" \
  "pass" \
  "" \
  "$(make_trino_payload 'SHOW CATALOGS')"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ ${#FAILED_NAMES[@]} -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
