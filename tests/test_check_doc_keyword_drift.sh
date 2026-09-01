#!/usr/bin/env bash
# test_check_doc_keyword_drift.sh — verify check-plugin-manifests.py Rule 13e
# (#1177): docs/skills.md trigger-keyword cells mirror the skill's frontmatter
# description.
#
# The case that matters: a description's `Do NOT activate on "..."` clause lists
# phrases that must NOT route to the skill. Searching the whole description
# would accept one of them as a valid trigger, so the roster could advertise
# `strike a balance` and pass.
#
# Usage: bash tests/test_check_doc_keyword_drift.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
DOC="$ROOT_DIR/docs/skills.md"
# The `strike` row is the fixture: its description carries a negative clause
# whose phrases are quoted, which is exactly the shape under test.
ROW_HEAD='| `strike` | `/strike`'

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

echo "test_check_doc_keyword_drift"

grep -qF "$ROW_HEAD" "$DOC"
if [ "$?" -ne 0 ]; then
  echo "FAIL  [fixture_row_present] expected=yes got=no ($ROW_HEAD)"
  exit 1
fi

BACKUP="$(mktemp)"
cp "$DOC" "$BACKUP"
restore() { cp "$BACKUP" "$DOC"; }
trap 'restore; rm -f "$BACKUP"' EXIT

# 1. Baseline is clean — without this, every case below could pass on a tree
#    that was already failing for an unrelated reason.
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline_check_clean" "$?" "0"

# 2. A phrase from the description's negative clause is NOT a trigger.
python3 - "$DOC" <<'PYEOF'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text()
old = "| `strike` | `/strike`"
p.write_text(s.replace(old, "| `strike` | `strike a balance`, `/strike`", 1))
PYEOF
OUT="$(python3 "$CHECK" 2>&1)"
run_case "excluded_phrase_nonzero" "$?" "1"
echo "$OUT" | grep -q "DOC KEYWORD DRIFT docs/skills.md: \`strike\` row lists 'strike a balance'"
run_case "excluded_phrase_named" "$?" "0"
restore

# 3. An invented keyword is still caught — the guard above must not have been
#    bought by disabling the rule.
python3 - "$DOC" <<'PYEOF'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text()
old = "| `strike` | `/strike`"
p.write_text(s.replace(old, "| `strike` | `zzz-not-a-real-trigger`, `/strike`", 1))
PYEOF
OUT="$(python3 "$CHECK" 2>&1)"
run_case "invented_keyword_nonzero" "$?" "1"
echo "$OUT" | grep -q "zzz-not-a-real-trigger"
run_case "invented_keyword_named" "$?" "0"
restore

# 4. Restored tree is clean again.
trap - EXIT
rm -f "$BACKUP"
python3 "$CHECK" >/dev/null 2>&1
run_case "restored_check_clean" "$?" "0"

echo "---"
echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -ne 0 ]; then
  printf 'FAILED: %s\n' "${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
