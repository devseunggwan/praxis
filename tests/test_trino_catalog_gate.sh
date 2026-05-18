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
# C1: String-literal -- bypass leakage (round 3 fix)
# ---------------------------------------------------------------------------
echo ""
echo "C1: string-literal bypass leakage fix"

# String literal containing -- marker: NOT a bypass (C1 fix)
run_case \
  "string literal 'foo -- catalog-enumerated: verified' → deny (C1 fix)" \
  "deny" \
  "" \
  "$(make_trino_payload "SELECT name FROM iceberg.foo.bar WHERE name = 'foo -- catalog-enumerated: verified'")"

# String literal containing bare --: must not be mistaken for a comment
run_case \
  "string literal '--' AS sep → deny, no false-pass (C1 fix)" \
  "deny" \
  "" \
  "$(make_trino_payload "SELECT '--' AS sep FROM iceberg.foo.bar")"

# Block comment with marker: still a valid bypass (existing behaviour preserved)
run_case \
  "block comment /* catalog-enumerated: verified */ → pass (bypass preserved)" \
  "pass" \
  "" \
  "$(make_trino_payload 'SELECT * FROM iceberg.foo.bar /* catalog-enumerated: verified */')"

# Line comment with marker: still a valid bypass (existing behaviour preserved)
run_case \
  "line comment -- catalog-enumerated: verified → pass (bypass preserved)" \
  "pass" \
  "" \
  "$(make_trino_payload 'SELECT * FROM iceberg.foo.bar -- catalog-enumerated: verified')"

# ---------------------------------------------------------------------------
# C2/C3: Known limitations — behavior pinning (NOT fixing, tracking #336)
# ---------------------------------------------------------------------------
echo ""
echo "C2/C3: known limitation pinning (current behavior, NOT fixing)"

# C2: double-quoted identifier with -- marker → current behavior: silently PASSES
# (bypass unintentionally allowed because double-quoted identifiers are not masked)
# Tracked in #336 P-redesign for SQL lexer fix.
run_case \
  "C2 KNOWN-LIMIT: double-quoted ident '\"-- catalog-enumerated: verified\"' → pass (unintentional bypass, #336)" \
  "pass" \
  "" \
  "$(make_trino_payload 'SELECT "-- catalog-enumerated: verified" FROM iceberg.foo.bar')"

# C3: paired apostrophe in line comment → current behavior: DENY (false-positive)
# The apostrophe pair is consumed by string-literal mask, stripping the marker.
# Workaround: use /* block comment */ or PRAXIS_TRINO_CATALOG_GATE=skip.
# Tracked in #336 P-redesign for SQL lexer fix.
run_case \
  "C3 KNOWN-LIMIT: line comment with apostrophes '-- ''catalog-enumerated: verified''' → deny (false-positive, #336)" \
  "deny" \
  "" \
  "$(make_trino_payload "SELECT * FROM iceberg.foo.bar -- 'catalog-enumerated: verified'")"

# ---------------------------------------------------------------------------
# Item 1: PostToolUse — SHOW CATALOGS 실패 시 마커 삭제
# ---------------------------------------------------------------------------
echo ""
echo "Item 1: PostToolUse isError → marker deleted after failed SHOW CATALOGS"

# 헬퍼: PostToolUse 페이로드 생성 (run_post 경로)
make_post_payload_show_catalogs_error() {
  python3 -c "
import json, sys
payload = {
    'session_id': 'test-session-336-post',
    'tool_name': 'mcp__laplace-trino__trino_query',
    'tool_input': {'query': 'SHOW CATALOGS'},
    'tool_response': {'isError': True, 'content': [{'text': 'Connection refused'}]},
    'cwd': '/tmp',
}
print(json.dumps(payload))
"
}

make_post_payload_show_catalogs_ok() {
  python3 -c "
import json, sys
payload = {
    'session_id': 'test-session-336-post',
    'tool_name': 'mcp__laplace-trino__trino_query',
    'tool_input': {'query': 'SHOW CATALOGS'},
    'tool_response': {'isError': False, 'content': [{'text': 'catalog1\ncatalog2'}]},
    'cwd': '/tmp',
}
print(json.dumps(payload))
"
}

# 케이스: PreToolUse가 마커를 만든 뒤 PostToolUse(error)가 삭제 → 다음 3-part 쿼리 deny
_item1_marker=$(new_marker_file)
rm -f "$_item1_marker"
# Step 1: PreToolUse SHOW CATALOGS → 마커 생성
make_trino_payload 'SHOW CATALOGS' | env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" \
  PRAXIS_TRINO_CATALOG_GATE_FILE="$_item1_marker" \
  python3 "$HOOK" >/dev/null 2>&1
# 마커가 생성됐는지 확인
if [ ! -f "$_item1_marker" ]; then
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("Item1: marker not created by PreToolUse SHOW CATALOGS")
  echo "  FAIL  Item1: PreToolUse SHOW CATALOGS should create marker"
else
  # Step 2: PostToolUse(error) → 마커 삭제
  make_post_payload_show_catalogs_error | env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" \
    PRAXIS_TRINO_CATALOG_GATE_FILE="$_item1_marker" \
    python3 "$HOOK" post >/dev/null 2>&1
  if [ -f "$_item1_marker" ]; then
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("Item1: PostToolUse(error) should delete marker")
    echo "  FAIL  Item1: PostToolUse(isError=true) should delete marker but it still exists"
  else
    PASS=$((PASS + 1))
    echo "  PASS  Item1: PostToolUse(isError=true) deletes marker after failed SHOW CATALOGS"
  fi
fi
rm -f "$_item1_marker" 2>/dev/null

# 케이스: PostToolUse(success) → 마커 유지
_item1_marker2=$(new_marker_file)
rm -f "$_item1_marker2"
make_trino_payload 'SHOW CATALOGS' | env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" \
  PRAXIS_TRINO_CATALOG_GATE_FILE="$_item1_marker2" \
  python3 "$HOOK" >/dev/null 2>&1
make_post_payload_show_catalogs_ok | env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" \
  PRAXIS_TRINO_CATALOG_GATE_FILE="$_item1_marker2" \
  python3 "$HOOK" post >/dev/null 2>&1
if [ -f "$_item1_marker2" ]; then
  PASS=$((PASS + 1))
  echo "  PASS  Item1: PostToolUse(isError=false) preserves marker after successful SHOW CATALOGS"
else
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("Item1: PostToolUse(success) should preserve marker")
  echo "  FAIL  Item1: PostToolUse(isError=false) should keep marker"
fi
rm -f "$_item1_marker2" 2>/dev/null

# 케이스: PostToolUse payload에 tool_response 없을 때 마커 유지 (fail-open back-compat)
_item1_marker3=$(new_marker_file)
rm -f "$_item1_marker3"
make_trino_payload 'SHOW CATALOGS' | env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" \
  PRAXIS_TRINO_CATALOG_GATE_FILE="$_item1_marker3" \
  python3 "$HOOK" >/dev/null 2>&1
# PostToolUse payload without tool_response field
python3 -c "
import json
payload = {
    'session_id': 'test-session-336-post',
    'tool_name': 'mcp__laplace-trino__trino_query',
    'tool_input': {'query': 'SHOW CATALOGS'},
    'cwd': '/tmp',
}
print(json.dumps(payload))
" | env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" \
  PRAXIS_TRINO_CATALOG_GATE_FILE="$_item1_marker3" \
  python3 "$HOOK" post >/dev/null 2>&1
if [ -f "$_item1_marker3" ]; then
  PASS=$((PASS + 1))
  echo "  PASS  Item1: PostToolUse(no tool_response) preserves marker (fail-open)"
else
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("Item1: PostToolUse(no tool_response) should preserve marker")
  echo "  FAIL  Item1: missing tool_response should not delete marker"
fi
rm -f "$_item1_marker3" 2>/dev/null

# ---------------------------------------------------------------------------
# Item 3: 마커 파일 권한 0o600
# ---------------------------------------------------------------------------
echo ""
echo "Item 3: marker file created with 0o600 permissions"

_item3_marker=$(new_marker_file)
rm -f "$_item3_marker"
make_trino_payload 'SHOW CATALOGS' | env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" \
  PRAXIS_TRINO_CATALOG_GATE_FILE="$_item3_marker" \
  python3 "$HOOK" >/dev/null 2>&1
if [ -f "$_item3_marker" ]; then
  _perm=$(python3 -c "import os, stat; st=os.stat('$_item3_marker'); print(oct(stat.S_IMODE(st.st_mode)))")
  if [ "$_perm" = "0o600" ]; then
    PASS=$((PASS + 1))
    echo "  PASS  Item3: marker file permission is 0o600"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("Item3: marker file permission should be 0o600")
    echo "  FAIL  Item3: marker file permission is $_perm (expected 0o600)"
  fi
else
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("Item3: marker file not created")
  echo "  FAIL  Item3: marker file not created by SHOW CATALOGS"
fi
rm -f "$_item3_marker" 2>/dev/null

# ---------------------------------------------------------------------------
# Item 6: SELECT INTO — false-positive 억제 (INTO arm 분리)
# ---------------------------------------------------------------------------
echo ""
echo "Item 6: SELECT...INTO cat.s.t → pass (not Trino syntax, avoid misleading deny)"

# SELECT col INTO cat.s.t FROM src (PostgreSQL/SQL-Server SELECT-INTO 구문)
# Trino에서 지원되지 않으므로 gate가 block하면 misleading → pass해야 함
run_case \
  "Item6: SELECT col INTO cat.s.t → pass (not INSERT/MERGE INTO)" \
  "pass" \
  "" \
  "$(make_trino_payload 'SELECT col INTO iceberg.foo.dest FROM src WHERE id = 1')"

# INSERT INTO는 여전히 deny
run_case \
  "Item6: INSERT INTO cat.s.t → deny (regression guard)" \
  "deny" \
  "" \
  "$(make_trino_payload 'INSERT INTO iceberg.foo.bar VALUES (1)')"

# MERGE INTO는 여전히 deny
run_case \
  "Item6: MERGE INTO cat.s.t → deny (regression guard)" \
  "deny" \
  "" \
  "$(make_trino_payload 'MERGE INTO iceberg.foo.bar t USING src s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.v = s.v')"

# ---------------------------------------------------------------------------
# Item 7: MERGE ... USING <catalog>.<schema>.<table> 감지
# ---------------------------------------------------------------------------
echo ""
echo "Item 7: MERGE USING catalog.schema.table → deny"

run_case \
  "Item7: MERGE INTO t USING iceberg.foo.src → deny" \
  "deny" \
  "" \
  "$(make_trino_payload 'MERGE INTO local_tbl t USING iceberg.foo.src s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.v = s.v')"

run_case \
  "Item7: MERGE USING without catalog in target → deny (source ref caught)" \
  "deny" \
  "" \
  "$(make_trino_payload 'MERGE INTO local_tbl t USING hive.warehouse.orders s ON t.id = s.id WHEN NOT MATCHED THEN INSERT VALUES (s.id, s.v)')"

# USING with 2-part ref (not 3-part) → pass
run_case \
  "Item7: MERGE USING schema.table (2-part) → pass" \
  "pass" \
  "" \
  "$(make_trino_payload 'MERGE INTO tgt t USING src_schema.src_table s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.v = s.v')"

# ---------------------------------------------------------------------------
# Item 8: CREATE TABLE x (LIKE catalog.schema.t) LIKE 절 감지
# ---------------------------------------------------------------------------
echo ""
echo "Item 8: CREATE TABLE ... (LIKE catalog.schema.t) → deny"

run_case \
  "Item8: CREATE TABLE x (LIKE iceberg.foo.src) → deny" \
  "deny" \
  "" \
  "$(make_trino_payload 'CREATE TABLE local_tbl (LIKE iceberg.foo.src INCLUDING ALL)')"

run_case \
  "Item8: CREATE TABLE x (LIKE iceberg.foo.src EXCLUDING PROPERTIES) → deny" \
  "deny" \
  "" \
  "$(make_trino_payload 'CREATE TABLE new_tbl (LIKE iceberg.sch.src EXCLUDING PROPERTIES)')"

# LIKE with 2-part ref → pass
run_case \
  "Item8: CREATE TABLE x (LIKE schema.t) (2-part) → pass" \
  "pass" \
  "" \
  "$(make_trino_payload 'CREATE TABLE new_tbl (LIKE myschema.src_table)')"

# ---------------------------------------------------------------------------
# Item 9: 문서-계약 테스트 — CATALOG_REF_RE keyword set ↔ spec 표 일치 확인
# ---------------------------------------------------------------------------
echo ""
echo "Item 9: doc-contract test — keyword arm set matches spec table"

_contract_result=$(python3 -c "
import sys, re, ast, pathlib

hook_path = '$ROOT_DIR/hooks/trino-catalog-gate.py'
spec_path = '$ROOT_DIR/docs/hook/trino-catalog-gate.md'

# 1. spec 테이블에서 keyword arm 추출
spec_text = pathlib.Path(spec_path).read_text()
# 표 행: '| keyword |' 형태에서 keyword arm 추출 (헤더/구분선 제외)
spec_keywords = set()
for line in spec_text.splitlines():
    line = line.strip()
    if not line.startswith('|'):
        continue
    parts = [p.strip() for p in line.split('|') if p.strip()]
    if not parts or parts[0].startswith('-') or parts[0].lower() in ('keyword arm', 'keyword'):
        continue
    # keyword arm 컬럼은 첫 번째 셀; 코드 span (\`...\`) 제거
    kw = parts[0].strip('\`').upper()
    # 복합 arm (INSERT INTO → INTO) 은 실제 regex keyword
    # spec 표에서 인식하는 arm keyword들
    if kw in ('FROM', 'JOIN', 'INTO', 'UPDATE', 'TABLE', 'VIEW', 'USING', 'LIKE'):
        spec_keywords.add(kw)

# 2. 코드에서 CATALOG_REF_RE + MERGE_INSERT_INTO_RE 패턴 파싱
code_text = pathlib.Path(hook_path).read_text()

# CATALOG_REF_RE: \b(?:...) 에서 keyword 추출
m1 = re.search(r'CATALOG_REF_RE\s*=\s*re\.compile\(\s*rf?\"([^\"]+)\"', code_text)
code_kws_main = set()
if m1:
    # (?:FROM|JOIN|...) 패턴에서 추출
    kw_match = re.search(r'\(\?:([A-Z|]+)\)', m1.group(1).upper())
    if kw_match:
        code_kws_main = set(kw_match.group(1).split('|'))

# MERGE_INSERT_INTO_RE: INSERT|MERGE → 변환 INTO
m2 = re.search(r'MERGE_INSERT_INTO_RE\s*=\s*re\.compile\(\s*rf?\"([^\"]+)\"', code_text)
code_kws_insert = set()
if m2:
    pat = m2.group(1).upper()
    if 'INSERT' in pat or 'MERGE' in pat:
        code_kws_insert.add('INTO')

code_keywords = code_kws_main | code_kws_insert

if spec_keywords == code_keywords:
    print('PASS: spec={} code={}'.format(sorted(spec_keywords), sorted(code_keywords)))
    sys.exit(0)
else:
    only_spec = spec_keywords - code_keywords
    only_code = code_keywords - spec_keywords
    print('FAIL: spec={} code={} only_spec={} only_code={}'.format(
        sorted(spec_keywords), sorted(code_keywords), sorted(only_spec), sorted(only_code)))
    sys.exit(1)
" 2>&1)

if echo "$_contract_result" | grep -q "^PASS:"; then
  PASS=$((PASS + 1))
  echo "  PASS  Item9: doc-contract test — keyword sets match"
  echo "        $_contract_result"
else
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("Item9: doc-contract — keyword arm mismatch between spec and code")
  echo "  FAIL  Item9: doc-contract test"
  echo "        $_contract_result"
fi

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
