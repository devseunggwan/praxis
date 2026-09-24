#!/bin/bash
# test_cited_rule_gate.sh: coverage for the cited-rule PreToolUse advisory
# (issue #1487).
#
# Each case writes a transcript whose last assistant message holds the call
# under test (tool_use id "cur"), then pipes a PreToolUse payload and asserts:
#   advisory → exit 0, stderr carries ADVISORY, stdout is additionalContext JSON
#   ask      → exit 0, stdout is a permissionDecision "ask" JSON
#   silent   → exit 0, stdout and stderr empty
#
# Usage: bash tests/hooks/advisory-nudge/test_cited_rule_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/cited-rule-gate/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

unset PRAXIS_HOOK_BYPASS_CITED_RULE PRAXIS_CITED_RULE_STRICT PRAXIS_CITED_RULE_PREFIXES

TMP=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

RULES="$TMP/rules.md"
cat >"$RULES" <<'EOF'
# Rules

## Scope Discipline `[E1]`

## Failure, Stall & Concealment `[E1]`

### Layer 1: Source Verification `[E1]`

```
## Fenced Heading
```
EOF
export PRAXIS_CITED_RULE_FILES="$RULES"

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case <name> <expected> <tool> <tool_input_json> <events_json> [extra_env]
#   events_json is a list of compact events, written to the transcript in order:
#     ["user", "<text>"]                 human message
#     ["text", "<text>", "<msg_id>"]     assistant text block
#     ["think", "<text>", "<msg_id>"]    assistant thinking block
#     ["tool", "<id>", "<name>", "<msg_id>"]  assistant tool_use block
#     ["result", "<tool_use_id>"]        tool_result
#     ["side", "<text>"]                 sidechain assistant text
run_case() {
  local name="$1" expected="$2" tool="$3" input="$4" events="$5" extra_env="$6"
  local transcript="$TMP/t.jsonl"

  python3 - "$transcript" "$events" <<'PY'
import json, sys
path, spec = sys.argv[1], json.loads(sys.argv[2])
rows = []
for ev in spec:
    kind = ev[0]
    if kind == "user":
        rows.append({"type": "user", "message": {"role": "user", "content": ev[1]}})
    elif kind in ("text", "think"):
        block = ({"type": "text", "text": ev[1]} if kind == "text"
                 else {"type": "thinking", "thinking": ev[1]})
        rows.append({"type": "assistant",
                     "message": {"id": ev[2], "role": "assistant", "content": [block]}})
    elif kind == "tool":
        rows.append({"type": "assistant", "message": {"id": ev[3], "role": "assistant",
                     "content": [{"type": "tool_use", "id": ev[1], "name": ev[2], "input": {}}]}})
    elif kind == "result":
        rows.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": ev[1], "content": "ok"}]}})
    elif kind == "side":
        rows.append({"type": "assistant", "isSidechain": True,
                     "message": {"id": "side", "role": "assistant",
                                 "content": [{"type": "text", "text": ev[1]}]}})
    rows.append({"type": "attachment", "attachment": {"type": "hook_success"}})
with open(path, "w") as fh:
    for r in rows:
        fh.write(json.dumps(r) + "\n")
PY

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({"session_id": "t-cited", "tool_name": sys.argv[1],
                  "tool_input": json.loads(sys.argv[2]), "tool_use_id": "cur",
                  "transcript_path": sys.argv[3], "cwd": "/nonexistent"}))' \
    "$tool" "$input" "$transcript")

  local out_file="$TMP/out" err_file="$TMP/err"
  if [ -n "$extra_env" ]; then
    printf '%s' "$payload" | env $extra_env python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    printf '%s' "$payload" | python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")

  local ok=1
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      echo "$err" | grep -q "\[cited-rule-gate\] ADVISORY" || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
h = json.load(sys.stdin)["hookSpecificOutput"]
assert h["hookEventName"] == "PreToolUse", h
assert "[cited-rule-gate]" in h["additionalContext"], h
assert "permissionDecision" not in h, h' 2>/dev/null || ok=0
      ;;
    ask)
      [ "$rc" -eq 0 ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
h = json.load(sys.stdin)["hookSpecificOutput"]
assert h["permissionDecision"] == "ask", h
assert "[cited-rule-gate] ASK" in h["permissionDecisionReason"], h' 2>/dev/null || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ] || ok=0
      [ -z "$err" ] || ok=0
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

PUSH='{"command": "git push origin main"}'
STATUS='{"command": "git status"}'
EDIT='{"file_path": "/repo/a.py", "old_string": "a", "new_string": "b"}'

# === Fires on the first uncited mutation =====================================

run_case "bash mutation, no text" advisory Bash "$PUSH" \
  '[["user","push it"],["tool","cur","Bash","m1"]]'
run_case "bash mutation, text without citation" advisory Bash "$PUSH" \
  '[["user","push it"],["text","Pushing now.","m1"],["tool","cur","Bash","m1"]]'
run_case "edit, no citation" advisory Edit "$EDIT" \
  '[["user","fix"],["tool","cur","Edit","m1"]]'
run_case "mcp write verb, no citation" advisory mcp__srv__create_issue '{}' \
  '[["user","file it"],["tool","cur","mcp__srv__create_issue","m1"]]'
run_case "citation names no real heading" advisory Bash "$PUSH" \
  '[["user","go"],["text","Rule: Push Freely","m1"],["tool","cur","Bash","m1"]]'
run_case "citation before the previous tool call is stale" advisory Bash "$PUSH" \
  '[["user","go"],["text","Rule: Scope Discipline","m1"],["tool","p1","Bash","m1"],["result","p1"],["text","Now pushing.","m2"],["tool","cur","Bash","m2"]]'
run_case "citation in an earlier human turn" advisory Bash "$PUSH" \
  '[["text","Rule: Scope Discipline","m0"],["user","push"],["tool","cur","Bash","m1"]]'
run_case "citation only in thinking" advisory Bash "$PUSH" \
  '[["user","go"],["think","Rule: Scope Discipline","m1"],["tool","cur","Bash","m1"]]'
run_case "citation only in a sidechain" advisory Bash "$PUSH" \
  '[["user","go"],["side","Rule: Scope Discipline"],["tool","cur","Bash","m1"]]'
run_case "heading inside a code fence is not a heading" advisory Bash "$PUSH" \
  '[["user","go"],["text","Rule: Fenced Heading","m1"],["tool","cur","Bash","m1"]]'
run_case "prefix mid-line is not a citation line" advisory Bash "$PUSH" \
  '[["user","go"],["text","See Rule: Scope Discipline","m1"],["tool","cur","Bash","m1"]]'

# === Stays silent on a cited mutation ========================================

run_case "cited heading, trailing token stripped" silent Bash "$PUSH" \
  '[["user","go"],["text","Rule: Scope Discipline","m1"],["tool","cur","Bash","m1"]]'
run_case "cited heading with its token" silent Bash "$PUSH" \
  '[["user","go"],["text","Rule: Scope Discipline `[E1]`","m1"],["tool","cur","Bash","m1"]]'
run_case "cited heading holding a comma" silent Bash "$PUSH" \
  '[["user","go"],["text","Rule: Failure, Stall & Concealment","m1"],["tool","cur","Bash","m1"]]'
run_case "several names, one real" silent Bash "$PUSH" \
  '[["user","go"],["text","Rule: Made Up · Layer 1: Source Verification","m1"],["tool","cur","Bash","m1"]]'
run_case "markdown-wrapped citation line" silent Bash "$PUSH" \
  '[["user","go"],["text","Plan.\n- **Rule:** Scope Discipline\nGo.","m1"],["tool","cur","Bash","m1"]]'
run_case "one citation covers a parallel batch" silent Bash "$PUSH" \
  '[["user","go"],["text","Rule: Scope Discipline","m1"],["tool","p1","Bash","m1"],["tool","cur","Bash","m1"],["result","p1"]]'
run_case "sibling result between batch calls" silent Bash "$PUSH" \
  '[["user","go"],["text","Rule: Scope Discipline","m1"],["tool","p1","Bash","m1"],["result","p1"],["tool","cur","Bash","m1"]]'
run_case "no readable rule file: any citation passes" silent Bash "$PUSH" \
  '[["user","go"],["text","Rule: Anything","m1"],["tool","cur","Bash","m1"]]' \
  "PRAXIS_CITED_RULE_FILES=$TMP/missing.md"
run_case "locale prefix" silent Bash "$PUSH" \
  '[["user","go"],["text","규칙: Scope Discipline","m1"],["tool","cur","Bash","m1"]]' \
  "PRAXIS_CITED_RULE_PREFIXES=Rule:,규칙:"

# === Stays silent on read-only calls and unjudgeable input ===================

run_case "read-only bash" silent Bash "$STATUS" \
  '[["user","go"],["tool","cur","Bash","m1"]]'
run_case "read-only mcp" silent mcp__srv__list_items '{}' \
  '[["user","go"],["tool","cur","mcp__srv__list_items","m1"]]'
run_case "read tool" silent Read '{"file_path": "/repo/a.py"}' \
  '[["user","go"],["tool","cur","Read","m1"]]'
run_case "tool_use id absent from transcript" silent Bash "$PUSH" \
  '[["user","go"],["tool","other","Bash","m1"]]'
run_case "bypass env" silent Bash "$PUSH" \
  '[["user","go"],["tool","cur","Bash","m1"]]' "PRAXIS_HOOK_BYPASS_CITED_RULE=1"

# === Strict mode asks ========================================================

run_case "strict: uncited mutation asks" ask Bash "$PUSH" \
  '[["user","go"],["tool","cur","Bash","m1"]]' "PRAXIS_CITED_RULE_STRICT=1"
run_case "strict: cited mutation silent" silent Bash "$PUSH" \
  '[["user","go"],["text","Rule: Scope Discipline","m1"],["tool","cur","Bash","m1"]]' \
  "PRAXIS_CITED_RULE_STRICT=1"

# Malformed stdin fails open.
out=$(printf 'not json' | python3 "$HOOK" 2>&1); rc=$?
if [ "$rc" -eq 0 ] && [ -z "$out" ]; then
  echo "PASS  [malformed stdin]"; PASS=$((PASS + 1))
else
  echo "FAIL  [malformed stdin] rc=$rc out=$out"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed stdin")
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  - %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
