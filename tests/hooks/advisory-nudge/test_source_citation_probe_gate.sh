#!/bin/bash
# test_source_citation_probe_gate.sh — coverage for hooks/advisory-nudge/source-citation-probe-gate/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   warn   → exit 0 + stderr contains "REMINDER"
#   silent → exit 0 + stderr empty
#   block  → exit 2 + stderr contains "REMINDER" (when PRAXIS_SOURCE_CITATION_STRICT=1)
#
# Usage: bash tests/hooks/advisory-nudge/test_source_citation_probe_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/source-citation-probe-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

# Throwaway fire-telemetry sink so test runs never pollute the real ledger.
TEL_FILE=$(mktemp)
export PRAXIS_FIRE_TELEMETRY_FILE="$TEL_FILE"

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation strict payload_json
#   expectation:
#     "warn"   — stderr contains REMINDER, rc=0
#     "silent" — stderr empty, rc=0
#     "block"  — stderr contains REMINDER, rc=2
run_case() {
  local name="$1" expectation="$2" strict="$3" payload="$4"

  local err_file
  err_file=$(mktemp)
  if [ "$strict" = "strict" ]; then
    echo "$payload" | env PRAXIS_SOURCE_CITATION_STRICT=1 python3 "$HOOK" >/dev/null 2>"$err_file"
  else
    echo "$payload" | env -u PRAXIS_SOURCE_CITATION_STRICT python3 "$HOOK" >/dev/null 2>"$err_file"
  fi
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    warn)
      [ "$rc" -eq 0 ] || ok=0
      echo "$err" | grep -q "REMINDER" || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q "REMINDER" || ok=0
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
    [ -n "$err" ] && echo "        stderr: $err" | head -c 400
  fi
}

echo "test_source_citation_probe_gate"

# --- AC trio -----------------------------------------------------------------

run_case "unbacked file:line citation (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"The bug is at impl.py:42 in the tokenizer.\""}}'

# Multi-line bodies go through --body-file: a literal \n inside a quoted
# --body value is a known tokenizer limit (body splits — see spec Known
# limits), so an inline-body variant of this case would silent-pass for the
# wrong reason.
PROBE_TMP=$(mktemp /tmp/scpg-probe-XXXXXX.md)
printf 'Bug at impl.py:42.\nProbe: grep -n foo impl.py → 42: def foo\n' > "$PROBE_TMP"
run_case "in-body filled Probe: line (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $PROBE_TMP\"}}"
rm -f "$PROBE_TMP"

run_case "no citation at all (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"Looks good to me, approved.\""}}'

# --- Surface variants ---------------------------------------------------------

run_case "-b inline body with file:line (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 -b \"see hooks/foo/impl.py:17 for the root cause\""}}'

run_case "--body=value form with file:line (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title foo --body=\"regression at parser.py:9\""}}'

BODY_TMP=$(mktemp /tmp/scpg-body-XXXXXX.md)
printf 'root cause at extractor.py:301 (unverified recall)\n' > "$BODY_TMP"
run_case "-F tmp file with file:line (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr create --title foo -F $BODY_TMP\"}}"
rm -f "$BODY_TMP"

run_case "--body-file relative missing path (silent fail-open)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title foo --body-file does-not-exist-830.md"}}'

run_case "--body-file - stdin placeholder (silent fail-open)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title foo --body-file -"}}'

run_case "gh pr review with file:line (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr review 12 --comment --body \"off-by-one at walker.py:88\""}}'

run_case "chained echo && gh pr comment (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"echo staged && gh pr comment 7 --body \"crash at runner.py:5\""}}'

run_case "env-prefix FOO=1 gh issue comment (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"FOO=1 gh issue comment 3 --body \"leak at pool.py:120\""}}'

run_case "gh issue list — not a write (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list --search impl.py:42"}}'

# The Slack/Notion MCP registrations were dropped (#1359); these hooks are
# PreToolUse(Bash) only now, so an MCP payload falls through the `else` arm
# and is silent. Kept as the regression pin for the removal — the payloads
# still carry the shapes that used to warn.
run_case "MCP slack send is out of scope (silent)" \
  "silent" "advisory" \
  '{"tool_name":"mcp__laplace-slack__slack_send_message","tool_input":{"channel":"C1","text":"prod incident traced to handler.py:230"}}'

run_case "MCP notion append is out of scope (silent)" \
  "silent" "advisory" \
  '{"tool_name":"mcp__laplace-notion__notion_append_blocks","tool_input":{"children":[{"paragraph":{"rich_text":[{"text":{"content":"regression introduced at dag.py:14"}}]}}]}}'

# --- False-positive guards (all silent) ---------------------------------------

run_case "version tag v1.2:3 (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"released as v1.2:3 yesterday\""}}'

run_case "ip:port 127.0.0.1:8080 (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"listening on 127.0.0.1:8080\""}}'

run_case "ratio 1.5:1 (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"compression ratio 1.5:1 observed\""}}'

run_case "https URL with port (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"see https://example.com:8080/a.py:1 for docs\""}}'

run_case "scheme-less example.com:8080 (silent — TLD denylist)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"host is example.com:8080\""}}'

FENCE_TMP=$(mktemp /tmp/scpg-fence-XXXXXX.md)
printf 'usage:\n```python\nparse(cfg.path)\nfoo.py:42\n```\ndone\n' > "$FENCE_TMP"
run_case "fenced code block sample (silent — author-exempt territory)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $FENCE_TMP\"}}"
rm -f "$FENCE_TMP"

# --- T2 / T3 tiers -------------------------------------------------------------

run_case "T2 inline-code call syntax with attr arg (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"the fix calls `resolve_body(flag.name)` internally\""}}'

run_case "T2 prose call syntax outside code span (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"the fix calls resolve_body(flag.name) internally\""}}'

run_case "T3 test-semantics claim (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"test_extract_gh_body asserts the -F path is read\""}}'

run_case "[verified] on citation line (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"bug at impl.py:42 [verified]\""}}'

# --- Anti-bypass ----------------------------------------------------------------

PLACEHOLDER_TMP=$(mktemp /tmp/scpg-placeholder-XXXXXX.md)
printf 'bug at impl.py:42\nProbe: <command> → 42: def foo\n' > "$PLACEHOLDER_TMP"
run_case "Probe: with <command> placeholder remaining (still warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $PLACEHOLDER_TMP\"}}"
rm -f "$PLACEHOLDER_TMP"

EMPTY_EVIDENCE_TMP=$(mktemp /tmp/scpg-empty-XXXXXX.md)
printf 'bug at impl.py:42\nProbe: grep -n foo impl.py →\n' > "$EMPTY_EVIDENCE_TMP"
run_case "Probe: with empty evidence after arrow (still warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $EMPTY_EVIDENCE_TMP\"}}"
rm -f "$EMPTY_EVIDENCE_TMP"

# --- Transcript clearing (Arm B) -------------------------------------------------

TRANSCRIPT=$(mktemp /tmp/scpg-transcript-XXXXXX.jsonl)
cat > "$TRANSCRIPT" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Read","input":{"file_path":"/repo/hooks/foo/impl.py"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"grep -n resolve_body hooks/foo/other.py"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"python3 -m pytest tests/test_walker.py -q"}}]}}
EOF

run_case "T1 cleared by transcript Read of cited basename (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body \\\"bug at impl.py:42\\\"\"},\"transcript_path\":\"$TRANSCRIPT\"}"

run_case "T1 NOT cleared — different basename in transcript (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body \\\"bug at unrelated.py:42\\\"\"},\"transcript_path\":\"$TRANSCRIPT\"}"

run_case "T2 cleared by transcript grep of function name (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body \\\"calls \`resolve_body(flag.name)\` internally\\\"\"},\"transcript_path\":\"$TRANSCRIPT\"}"

run_case "T3 cleared by transcript pytest run (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body \\\"test_extract asserts the -F path is read\\\"\"},\"transcript_path\":\"$TRANSCRIPT\"}"

rm -f "$TRANSCRIPT"

# --- Strict mode / fail-open ------------------------------------------------------

run_case "strict mode converts warn to block (rc 2)" \
  "block" "strict" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"bug at impl.py:42\""}}'

run_case "strict mode still silent when cleared" \
  "silent" "strict" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"bug at impl.py:42 [verified]\""}}'

run_case "malformed stdin JSON (silent fail-open)" \
  "silent" "advisory" \
  'this is not json'

run_case "non-write non-MCP tool (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Read","tool_input":{"file_path":"/tmp/impl.py"}}'

# --- Summary ----------------------------------------------------------------------

rm -f "$TEL_FILE"

echo ""
echo "passed: $PASS, failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
