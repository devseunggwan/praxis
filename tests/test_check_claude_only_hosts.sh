#!/usr/bin/env bash
# test_check_claude_only_hosts.sh — verify check-plugin-manifests.py Rule 28
# (#1337): every registration on a Claude-only event declares
# hosts: ["claude"].
#
# `hosts` is optional in the schema and an absent value means every host, so
# the failure this guards is silent: the hook is written into the Codex and
# Cursor hooks.json for an event those hosts never raise.
#
# Absent:   a SubagentStop entry with no hosts key fails.
# Wider:    a PostToolUseFailure entry listing another host fails.
# Baseline: the untouched tree passes (negative control).
#
# Usage: bash tests/test_check_claude_only_hosts.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
MANIFEST="$ROOT_DIR/hooks/manifest.json"

PASS=0
FAIL=0
FAILED_NAMES=()

run_case() {
  local name="$1" result="$2" expected="$3"
  if [ "$result" = "$expected" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected got=$result"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

echo "test_check_claude_only_hosts"

if [ ! -f "$MANIFEST" ]; then
  echo "FATAL: fixture target missing: $MANIFEST" >&2
  exit 1
fi

BAK="${MANIFEST}.bak-claude-only-test"
cp "$MANIFEST" "$BAK" || exit 1
restore() {
  mv -f "$BAK" "$MANIFEST" 2>/dev/null
}
trap restore EXIT

# Baseline — untouched tree passes.
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline tree passes" "$?" "0"

# mutate <event> <new hosts JSON or the literal DROP>
mutate() {
  python3 - "$BAK" "$MANIFEST" "$1" "$2" <<'PY'
import json, sys
src, dst, event, hosts = sys.argv[1:5]
m = json.loads(open(src, encoding="utf-8").read())
changed = False
for entry in m["hooks"]:
    if entry.get("event") == event and not changed:
        if hosts == "DROP":
            entry.pop("hosts", None)
        else:
            entry["hosts"] = json.loads(hosts)
        changed = True
assert changed, f"no {event} registration to mutate"
open(dst, "w", encoding="utf-8").write(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
PY
}

# Absent — a SubagentStop entry with no hosts key.
mutate SubagentStop DROP
python3 "$CHECK" 2>&1 | grep -q "CLAUDE-ONLY HOSTS"
run_case "SubagentStop without hosts fails" "$?" "0"
cp "$BAK" "$MANIFEST"

# Wider — a PostToolUseFailure entry that also lists codex.
mutate PostToolUseFailure '["claude", "codex"]'
python3 "$CHECK" 2>&1 | grep -q "CLAUDE-ONLY HOSTS"
run_case "PostToolUseFailure listing another host fails" "$?" "0"
cp "$BAK" "$MANIFEST"

# Restored tree passes again (proves the mutations, not ambient state, drove
# the failures above).
python3 "$CHECK" >/dev/null 2>&1
run_case "restored tree passes" "$?" "0"

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
