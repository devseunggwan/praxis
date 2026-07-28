#!/usr/bin/env bash
# test_check_mktemp_guard.sh — verify check-plugin-manifests.py Rule 19 (#897):
# every `mktemp -d` in the test suite checks its own failure.
#
# The rule exists because the suite runs under `set +e`: an unchecked failure
# leaves the variable empty, and every path derived from it re-anchors at the
# filesystem root. The observed cost was eleven unrelated assertion failures
# from one unusable temp dir, plus an attempted `mkdir -p /_lib`.
#
# Usage: bash tests/test_check_mktemp_guard.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
TARGET="$ROOT_DIR/tests/hooks/_lib/test_record_fire.sh"

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

echo "test_check_mktemp_guard"

if [ ! -f "$TARGET" ]; then
  echo "FAIL  [target_exists] expected=yes got=no"
  exit 1
fi

BACKUP="$(mktemp)" || { echo "FATAL: mktemp failed" >&2; exit 1; }
cp "$TARGET" "$BACKUP"
trap 'cp "$BACKUP" "$TARGET"; rm -f "$BACKUP"' EXIT

# Rewrite the first guarded site to $1 and report the checker's exit code.
sub_first_site() {
  python3 - "$TARGET" "$1" <<'PY'
import re, sys
path, new = sys.argv[1:3]
body = open(path, encoding="utf-8").read()
line = re.search(r'(?m)^.*mktemp -d.*$', body)
assert line, "no mktemp -d site found — fixture is stale"
open(path, "w", encoding="utf-8").write(
    body[: line.start()] + new + body[line.end() :]
)
PY
  python3 "$CHECK" >/dev/null 2>&1
  echo "$?"
  cp "$BACKUP" "$TARGET"
}

# 1. Baseline: every site in the suite is guarded.
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline_check_clean" "$?" "0"

# 2. Dropping the guard is the regression the rule was written for.
run_case "unguarded_site_fails" "$(sub_first_site 'PROBE_ROOT="$(mktemp -d)"')" "1"

# 3. The bare-substitution form is equally unguarded.
run_case "unguarded_bare_form_fails" "$(sub_first_site 'PROBE_ROOT=$(mktemp -d)')" "1"

# 4. A template argument does not exempt the site.
run_case "unguarded_template_form_fails" \
  "$(sub_first_site 'PROBE_ROOT=$(mktemp -d "$HOME/x.XXXXXX")')" "1"

# 5. A commented-out site is not a call and must not be flagged — otherwise
#    the rule blocks documenting the very pattern it enforces.
run_case "commented_site_ignored" \
  "$(sub_first_site '# PROBE_ROOT=$(mktemp -d)  — see Rule 19')" "0"

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf 'failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
