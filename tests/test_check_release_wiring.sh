#!/usr/bin/env bash
# test_check_release_wiring.sh — verify check-plugin-manifests.py Rule 9 (#1172):
# release-please extra-files wiring for versioned artifacts.
#
# Presence:  a versioned output missing from extra-files is reported.
# Semantics: an entry whose type or jsonpath is wrong is reported too — the
#            case a path-only check cannot see, because a marketplace output
#            carries `version` at the top level AND inside plugins[0], so a
#            narrowed `$.version` updates one and leaves the other stale.
#
# Usage: bash tests/test_check_release_wiring.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
TARGET="$ROOT_DIR/release-please-config.json"

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

echo "test_check_release_wiring"

if [ ! -f "$TARGET" ]; then
  echo "FAIL  [target_exists] expected=yes got=no"
  exit 1
fi

BACKUP="$(mktemp)"
cp "$TARGET" "$BACKUP"
restore() { cp "$BACKUP" "$TARGET"; }
trap 'restore; rm -f "$BACKUP"' EXIT

# Rewrite the first marketplace entry's <field> to <value>; a null value drops
# the key entirely, which is how the "missing jsonpath" case is built.
tamper() {
  python3 - "$TARGET" "$1" "$2" <<'PY'
import json, sys
path, field, value = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(path))
files = cfg["packages"]["."]["extra-files"]
target = next(e for e in files if e["path"].endswith("marketplace.json"))
if value == "__DELETE__":
    target.pop(field, None)
else:
    target[field] = value
json.dump(cfg, open(path, "w"), indent=2)
PY
}

# 1. Baseline is clean — without this the failures below prove nothing, since a
#    checker that always reports drift would pass every case that follows.
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline_check_clean" "$?" "0"

# 2. A narrowed jsonpath is reported. This is the whole point of the rule: the
#    path is still listed, so a presence-only check stays silent.
tamper jsonpath '$.version'
python3 "$CHECK" 2>&1 | grep -q "extra-files jsonpath is"
run_case "narrowed_jsonpath_reported" "$?" "0"
restore

# 3. A missing jsonpath is reported the same way.
tamper jsonpath '__DELETE__'
python3 "$CHECK" 2>&1 | grep -q "extra-files jsonpath is"
run_case "absent_jsonpath_reported" "$?" "0"
restore

# 4. A wrong updater type is reported.
tamper type 'generic'
python3 "$CHECK" 2>&1 | grep -q "extra-files type is"
run_case "wrong_type_reported" "$?" "0"
restore

# 5. Dropping the entry entirely still reports the original presence drift.
python3 - "$TARGET" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
files = cfg["packages"]["."]["extra-files"]
cfg["packages"]["."]["extra-files"] = [
    e for e in files if not e["path"].endswith("marketplace.json")
]
json.dump(cfg, open(sys.argv[1], "w"), indent=2)
PY
python3 "$CHECK" 2>&1 | grep -q "is not listed in"
run_case "absent_entry_reported" "$?" "0"
restore

# 6. The restore path itself works, so a later suite does not inherit a tampered
#    config from this file.
python3 "$CHECK" >/dev/null 2>&1
run_case "restore_leaves_tree_clean" "$?" "0"

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
