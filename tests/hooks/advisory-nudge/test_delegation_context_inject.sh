#!/usr/bin/env bash
# test_delegation_context_inject.sh — coverage for
# hooks/advisory-nudge/delegation-context-inject/impl.py
#
# The hook never blocks, so the assertions are about stdout: valid JSON, the
# mirrored hookEventName the harness requires, and every isolation variable
# named individually (the recorded failure was a general phrase that did not
# make the delegator think of them).
#
# The payload is the MEASURED SubagentStart shape — notably WITHOUT a `task`
# field, which the published reference lists. One case pins that: a payload
# carrying `task` must produce the same output, so no future edit can start
# depending on a field the runtime does not send.
#
# Usage: bash tests/hooks/advisory-nudge/test_delegation_context_inject.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/delegation-context-inject/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

ok() { echo "PASS  [$1]"; PASS=$((PASS + 1)); }
ng() { echo "FAIL  [$1] ($2)"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("$1"); }

payload() {
  python3 -c '
import json, sys
p = {
    "hook_event_name": "SubagentStart",
    "session_id": "sess-test",
    "prompt_id": "p-1",
    "cwd": "/tmp",
    "agent_id": "a1",
    "agent_type": "general-purpose",
    "transcript_path": "/tmp/t.jsonl",
}
if len(sys.argv) > 1 and sys.argv[1] == "with-task":
    p["task"] = "do the thing"
print(json.dumps(p))
' "$@"
}

# --- 1. emits valid JSON with the mirrored event name -------------------------
OUT=$(payload | env -u PRAXIS_HOOK_BYPASS_DELEGATION_CONTEXT python3 "$HOOK" 2>/dev/null)
RC=$?
NAME=$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["hookEventName"])' 2>/dev/null)
if [ "$RC" -eq 0 ] && [ "$NAME" = "SubagentStart" ]; then
  ok "valid JSON, hookEventName mirrors SubagentStart"
else
  ng "valid JSON, hookEventName mirrors SubagentStart" "rc=$RC name=$NAME"
fi

# --- 2. every isolation variable is named individually ------------------------
CTX=$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"])' 2>/dev/null)
MISSING=""
for v in PRAXIS_HOME PRAXIS_FIRE_TELEMETRY_FILE PRAXIS_STATE_DIR HOME; do
  case "$CTX" in *"$v"*) ;; *) MISSING="$MISSING $v" ;; esac
done
if [ -z "$MISSING" ]; then
  ok "contract names every isolation variable"
else
  ng "contract names every isolation variable" "missing:$MISSING"
fi

# --- 2b. every contract RULE survives, not just the variable names -----------
# Case 2 above passes even when a whole clause is deleted, because the four
# variable names live in one of the four rules. Each rule is pinned by a phrase
# distinctive enough that rewording the sentence is a deliberate act, not a
# typo — the point is that silently dropping a rule fails loudly.
MISSING_RULE=""
while IFS= read -r rule; do
  [ -n "$rule" ] || continue
  case "$CTX" in *"$rule"*) ;; *) MISSING_RULE="$MISSING_RULE | $rule" ;; esac
done <<'RULES'
Do NOT set or override
never under ~/.praxis or ~/.claude
read-only unless the task named it
the target's reach, not the verb
RULES
if [ -z "$MISSING_RULE" ]; then
  ok "contract carries every rule, not only the variable names"
else
  ng "contract carries every rule, not only the variable names" "missing:$MISSING_RULE"
fi

# --- 3. a `task` field changes nothing (the runtime never sends one) ----------
OUT_TASK=$(payload with-task | env -u PRAXIS_HOOK_BYPASS_DELEGATION_CONTEXT python3 "$HOOK" 2>/dev/null)
if [ "$OUT_TASK" = "$OUT" ]; then
  ok "output does not depend on the undocumented-but-absent task field"
else
  ng "output does not depend on the undocumented-but-absent task field" "outputs differ"
fi

# --- 4. bypass suppresses all output -----------------------------------------
OUT_BY=$(payload | PRAXIS_HOOK_BYPASS_DELEGATION_CONTEXT=1 python3 "$HOOK" 2>/dev/null)
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT_BY" ]; then
  ok "bypass env var suppresses injection"
else
  ng "bypass env var suppresses injection" "rc=$RC out=$OUT_BY"
fi

# --- 5. malformed payload fails open with no stdout --------------------------
OUT_BAD=$(printf 'not json' | python3 "$HOOK" 2>/dev/null)
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT_BAD" ]; then
  ok "malformed payload fails open"
else
  ng "malformed payload fails open" "rc=$RC out=$OUT_BAD"
fi

# --- summary -----------------------------------------------------------------
echo ""
echo "Passed: $PASS  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed tests:"
  for t in "${FAILED_NAMES[@]}"; do
    echo "  - $t"
  done
  exit 1
fi
exit 0
