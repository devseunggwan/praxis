#!/usr/bin/env bash
# test_check_spec_paths.sh — verify check-plugin-manifests.py Rule 22
# (#1179): every repo-path-looking token in a hooks/*/*/spec.md exists on disk.
#
# The rule is a token extractor, so what it must get right is which shapes it
# picks up and which it deliberately lets through. One case per shape:
#
# Detected:  a dangling path inside a backticked span; a dangling path as an
#            argument inside a fenced code block.
# Ignored:   a path under an unchecked top-level dir; a token carrying a glob
#            or a `<placeholder>`; a `../` out-of-tree example; a bare
#            basename with no `/` (the rule cannot check those without
#            flagging every `impl.py` and `spec.md` in the corpus).
# Exempt:    a path listed in SPEC_PATH_EXEMPT stays silent even though it
#            does not exist.
# Baseline:  the untouched tree passes, and passes again after every restore
#            (negative control — proves the mutations drove the failures).
#
# Usage: bash tests/test_check_spec_paths.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
# Any spec works as the carrier; this one has no SPEC_PATH_EXEMPT entry, so
# nothing it says is pre-exempted.
SPEC="$ROOT_DIR/hooks/advisory-nudge/memory-hint/spec.md"
# This one IS in SPEC_PATH_EXEMPT, and its exempt set already lists a
# nonexistent path — that is the exemption case, checked without mutation.
EXEMPT_SPEC="$ROOT_DIR/hooks/advisory-nudge/external-write-path-existence-check/spec.md"

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

echo "test_check_spec_paths"

for f in "$SPEC" "$EXEMPT_SPEC"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: fixture target missing: $f" >&2
    exit 1
  fi
done

BAK="${SPEC}.bak-spec-paths-test"
cp "$SPEC" "$BAK" || exit 1
restore() { mv -f "$BAK" "$SPEC" 2>/dev/null; }
trap restore EXIT

# append_and_check <line...> — append lines to the carrier spec, run the
# checker, and report whether Rule 22 named the carrier. Always restores.
append_and_check() {
  cp "$BAK" "$SPEC"
  printf '\n%s\n' "$@" >> "$SPEC"
  python3 "$CHECK" 2>&1 | grep -q "SPEC DANGLING PATH hooks/advisory-nudge/memory-hint/spec.md"
  local rc=$?
  cp "$BAK" "$SPEC"
  return $rc
}

# Baseline — untouched tree passes.
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline tree passes" "$?" "0"

# --- shapes the rule MUST detect -------------------------------------------

append_and_check 'See `hooks/zz-no-such-file.py` for the pattern.'
run_case "dangling path in a backticked span is reported" "$?" "0"

append_and_check '```bash' 'bash tests/zz-no-such-test.sh' '```'
run_case "dangling path as a fenced command argument is reported" "$?" "0"

# --- shapes the rule MUST NOT flag -----------------------------------------

append_and_check 'Config lives at `vault/zz-no-such-note.md`.'
run_case "unchecked top-level dir is ignored" "$?" "1"

append_and_check 'Run `tests/zz-*.sh` or `hooks/<name>/impl.py`.'
run_case "glob and placeholder tokens are ignored" "$?" "1"

append_and_check 'Compare against `../zz-sibling-repo/impl.py`.'
run_case "out-of-tree ../ example is ignored" "$?" "1"

append_and_check 'Module constants live in `zz-no-such-hook.py`.'
run_case "bare basename with no slash is ignored" "$?" "1"

# --- exemption --------------------------------------------------------------

# No mutation: this spec ships a nonexistent path that SPEC_PATH_EXEMPT
# covers. Positive control for the exemption is the baseline above — the same
# checker reports other specs, so silence here is the exemption, not a dead
# rule.
grep -q 'hooks/pre-tool-use/fake.sh' "$EXEMPT_SPEC"
run_case "exempt fixture still carries its phantom path" "$?" "0"
python3 "$CHECK" 2>&1 | grep -q "SPEC DANGLING PATH hooks/advisory-nudge/external-write-path-existence-check/spec.md"
run_case "SPEC_PATH_EXEMPT path is not reported" "$?" "1"

# Restored tree passes again.
python3 "$CHECK" >/dev/null 2>&1
run_case "restored tree passes" "$?" "0"

echo ""
echo "Result: $PASS/$((PASS + FAIL)) passed"
if [ "$FAIL" -gt 0 ]; then
  printf 'Failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
