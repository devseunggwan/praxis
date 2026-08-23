#!/usr/bin/env bash
# test_recover_scan_display_name.sh — verify claude-recover-scan picks
# display names from the compact-summary fallback chain (#467).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SCAN="$REPO_ROOT/skills/recover-sessions/claude-recover-scan"

if [[ ! -f "$SCAN" ]]; then
  echo "FAIL: scanner not found at $SCAN" >&2
  exit 1
fi

TMPHOME=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
PROJ="$TMPHOME/.claude/projects/-tmp-fake-cwd"
mkdir -p "$PROJ"

cleanup() {
  rm -rf "$TMPHOME"
}
trap cleanup EXIT INT TERM

PAD=$(python3 -c "print('x' * 12000)")
NOW=$(date +%Y-%m-%dT%H:%M:%S.000Z)
CWD="/tmp/fake-cwd"

# Each fixture file needs >= 4 real user records (MIN_USER_MSGS) — compaction
# records no longer count toward user_count after the fix, so the padding
# records are required to keep the file from being filtered as "short".
emit_padding_users() {
  local path="$1"
  for i in 1 2 3 4 5; do
    printf '{"type":"user","isSidechain":false,"timestamp":"%s","cwd":"%s","message":{"content":"padding %d"}}\n' \
      "$NOW" "$CWD" "$i" >> "$path"
  done
  printf '{"type":"assistant","timestamp":"%s","message":{"content":"%s"}}\n' \
    "$NOW" "$PAD" >> "$path"
}

build_fixture() {
  local path="$1" head_payload="$2"
  : > "$path"
  printf '%s\n' "$head_payload" >> "$path"
  emit_padding_users "$path"
}

# Fixture A: compact-summary with NESTED labels (the real-world shape).
# The outer "Primary Request and Intent:" wraps an inner "Original request:".
# The display must extract the inner request text, not leak "- Original request:".
COMPACT_NESTED='This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.\n\nSummary:\n1. Primary Request and Intent:\n   - **Original request**: Run praxis:codex-review-wrap on each currently open PR\n'
A_PAYLOAD=$(python3 -c "
import json
print(json.dumps({
  'type': 'user', 'isSidechain': False, 'isCompactSummary': True,
  'timestamp': '$NOW', 'cwd': '$CWD',
  'message': {'content': '''$COMPACT_NESTED'''.replace(chr(92)+'n', chr(10))}
}))
")
build_fixture "$PROJ/aaaa1111-1111-1111-1111-111111111111.jsonl" "$A_PAYLOAD"
# Add a /retrospect record AFTER compaction to confirm compact_summary wins.
python3 -c "
import json
print(json.dumps({
  'type':'user','isSidechain':False,'timestamp':'$NOW','cwd':'$CWD',
  'message':{'content':'/retrospect'}
}))
" >> "$PROJ/aaaa1111-1111-1111-1111-111111111111.jsonl"

# Fixture B: no compact summary, plain first message.
B_PAYLOAD=$(python3 -c "
import json
print(json.dumps({
  'type':'user','isSidechain':False,'timestamp':'$NOW','cwd':'$CWD',
  'message':{'content':'Investigate the broken hook chain in PR 472'}
}))
")
build_fixture "$PROJ/bbbb2222-2222-2222-2222-222222222222.jsonl" "$B_PAYLOAD"

# Fixture P (#1095): first message contains a literal pipe. The scanner must
# sanitize it at emit time (replace "|" with "-") so the pipe-delimited record
# does not shift fields and corrupt the trailing home_label on the consumer.
P_PAYLOAD=$(python3 -c "
import json
print(json.dumps({
  'type':'user','isSidechain':False,'timestamp':'$NOW','cwd':'$CWD',
  'message':{'content':'Run foo | grep bar | sort the output'}
}))
")
build_fixture "$PROJ/pppp8888-8888-8888-8888-888888888888.jsonl" "$P_PAYLOAD"

# Fixture C: compact-only "Summary:" form (no Primary/Original labels).
COMPACT_SHORT='This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.\nSummary: Investigate flaky tests in the integration suite.'
C_PAYLOAD=$(python3 -c "
import json
print(json.dumps({
  'type':'user','isSidechain':False,'isCompactSummary':True,
  'timestamp':'$NOW','cwd':'$CWD',
  'message':{'content':'''$COMPACT_SHORT'''.replace(chr(92)+'n', chr(10))}
}))
")
build_fixture "$PROJ/cccc3333-3333-3333-3333-333333333333.jsonl" "$C_PAYLOAD"

# Fixture D: Korean unicode in compact summary.
COMPACT_KR='This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.\nSummary: 네이버 검색광고 v2 전환 현황 확인 요청'
D_PAYLOAD=$(python3 -c "
import json
print(json.dumps({
  'type':'user','isSidechain':False,'isCompactSummary':True,
  'timestamp':'$NOW','cwd':'$CWD',
  'message':{'content':'''$COMPACT_KR'''.replace(chr(92)+'n', chr(10))}
}))
")
build_fixture "$PROJ/dddd4444-4444-4444-4444-444444444444.jsonl" "$D_PAYLOAD"

# Fixture E: drifted boilerplate (one word changed). The regex strip must
# still remove it so the next "This session…" prefix doesn't leak.
DRIFTED='This session is being continued from the previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.\nSummary: Resume work on the auth refactor.'
E_PAYLOAD=$(python3 -c "
import json
print(json.dumps({
  'type':'user','isSidechain':False,'isCompactSummary':True,
  'timestamp':'$NOW','cwd':'$CWD',
  'message':{'content':'''$DRIFTED'''.replace(chr(92)+'n', chr(10))}
}))
")
build_fixture "$PROJ/eeee5555-5555-5555-5555-555555555555.jsonl" "$E_PAYLOAD"

# Fixture G: compacted session followed by a /continue slash-command — the
# bootstrap turn must NOT cause the session to be filtered as command.
# Regression guard for codex round 1 P2.
COMPACT_G='This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.\nSummary: Long-running refactor of the auth layer.'
G_PATH="$PROJ/gggg7777-7777-7777-7777-777777777777.jsonl"
: > "$G_PATH"
python3 -c "
import json
print(json.dumps({
  'type':'user','isSidechain':False,'isCompactSummary':True,
  'timestamp':'$NOW','cwd':'$CWD',
  'message':{'content':'''$COMPACT_G'''.replace(chr(92)+'n', chr(10))}
}))
" >> "$G_PATH"
python3 -c "
import json
print(json.dumps({
  'type':'user','isSidechain':False,'timestamp':'$NOW','cwd':'$CWD',
  'message':{'content':'<command-name>/continue</command-name>'}
}))
" >> "$G_PATH"
# Pad to clear the 10 KB / MIN_USER_MSGS threshold even though the
# compact-summary bypass would let it through anyway.
emit_padding_users "$G_PATH"

# Fixture F: session with no real first message AND no compact summary.
# Per #467 fallback chain, display_name must fall back to session_id.
# All user records are `<...>`-wrapped so none qualify as first_real_msg.
F_PATH="$PROJ/ffff6666-6666-6666-6666-666666666666.jsonl"
: > "$F_PATH"
# Use `<system-tag>`-wrapped content: all records start with `<` so
# first_real_msg never sets, none match command/teammate triggers, and
# none have isCompactSummary — display_name should land on session_id.
for i in 1 2 3 4 5; do
  python3 -c "
import json
print(json.dumps({
  'type':'user','isSidechain':False,'timestamp':'$NOW','cwd':'$CWD',
  'message':{'content':'<system-tag>placeholder $i</system-tag>'}
}))
" >> "$F_PATH"
done
printf '{"type":"assistant","timestamp":"%s","message":{"content":"%s"}}\n' \
  "$NOW" "$PAD" >> "$F_PATH"

# Run scanner against the temp config home.
out=$(HOME="$TMPHOME" STRICT_TIMESTAMP=0 python3 "$SCAN" 3 "" "" 2>&1 || true)

pass=0
fail=0

# Pick the display-name field (5th pipe-separated column) for a session id prefix.
extract_field() {
  local sid_prefix="$1"
  grep -F "|${sid_prefix}" <<< "$out" | head -1 | awk -F'|' '{print $5}'
}

assert_equals() {
  local label="$1" sid_prefix="$2" expected="$3"
  local actual
  actual=$(extract_field "$sid_prefix")
  if [[ "$actual" == "$expected" ]]; then
    echo "PASS  [$label]"
    pass=$((pass + 1))
  else
    echo "FAIL  [$label]"
    echo "  expected (exact): '$expected'"
    echo "  actual:           '$actual'"
    fail=$((fail + 1))
  fi
}

assert_no_prefix() {
  local label="$1" sid_prefix="$2" forbidden="$3"
  local actual
  actual=$(extract_field "$sid_prefix")
  if [[ "$actual" == "$forbidden"* ]]; then
    echo "FAIL  [$label]"
    echo "  display starts with forbidden prefix '$forbidden': '$actual'"
    fail=$((fail + 1))
  else
    echo "PASS  [$label]  (no '$forbidden' prefix in: '$actual')"
    pass=$((pass + 1))
  fi
}

# A: nested-label extraction returns the INNER request text, no "- Original request:" leak
assert_equals "nested-label-extracts-inner-request" \
  "aaaa1111" \
  "Run praxis:codex-review-wrap on each currently open PR"
assert_no_prefix "no-dash-prefix-leak" \
  "aaaa1111" \
  "- "
assert_no_prefix "no-original-request-label-leak" \
  "aaaa1111" \
  "Original request"

# B: plain first message preserved
assert_equals "plain-first-message" \
  "bbbb2222" \
  "Investigate the broken hook chain in PR 472"

# P (#1095): pipe chars in the message are sanitized to dashes so the record
# stays 6-field. The display field must carry no literal "|", and the trailing
# home_label field must be intact ("claude") — a leaked pipe would push the
# real message tail into the home column.
assert_equals "pipe-in-message-sanitized" \
  "pppp8888" \
  "Run foo - grep bar - sort the output"
# Consume exactly as the recover scripts do: `IFS='|' read` into 6 fields.
# With an unsanitized pipe the trailing home_label absorbs the message tail
# ("grep bar - sort the output|claude"), so this is the faithful regression.
p_record=$(grep -F "|pppp8888" <<< "$out" | head -1)
IFS='|' read -r _ _ _ _ _ p_home <<< "$p_record"
if [[ "$p_home" == "claude" ]]; then
  echo "PASS  [pipe-record-home-field-intact]  (home='$p_home')"
  pass=$((pass + 1))
else
  echo "FAIL  [pipe-record-home-field-intact]"
  echo "  expected home field: 'claude'"
  echo "  actual:              '$p_home'"
  fail=$((fail + 1))
fi

# C: "Summary: <body>" short form
assert_equals "summary-prefix-stripped" \
  "cccc3333" \
  "Investigate flaky tests in the integration suite"

# D: Korean unicode survives
assert_equals "korean-summary" \
  "dddd4444" \
  "네이버 검색광고 v2 전환 현황 확인 요청"

# E: drifted boilerplate stripped (regex tolerant)
assert_equals "drifted-boilerplate-stripped" \
  "eeee5555" \
  "Resume work on the auth refactor"

# F: session_id fallback per spec
assert_equals "session-id-final-fallback" \
  "ffff6666" \
  "ffff6666-6666-6666-6666-666666666666"

# G: compacted-then-/continue session — display from compact_summary, not filtered
assert_equals "compacted-then-continue-survives-filter" \
  "gggg7777" \
  "Long-running refactor of the auth layer"

# Boilerplate-leak guard across the entire scan output
if grep -F 'This session is being continued from' <<< "$out"; then
  echo "FAIL  [boilerplate-leak-guard]"
  fail=$((fail + 1))
else
  echo "PASS  [boilerplate-leak-guard]"
  pass=$((pass + 1))
fi

echo "Results: $pass passed, $fail failed"
exit $(( fail > 0 ? 1 : 0 ))
