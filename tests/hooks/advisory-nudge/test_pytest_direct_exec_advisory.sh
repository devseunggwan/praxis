#!/bin/bash
# Coverage for hooks/advisory-nudge/pytest-direct-exec-advisory/impl.py.
#
# Usage: bash tests/hooks/advisory-nudge/test_pytest_direct_exec_advisory.sh
# Exit: 0 = all pass; 1 = at least one failure

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/pytest-direct-exec-advisory/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

FIXTURE_ROOT=$(mktemp -d) || {
  echo "FATAL: mktemp -d failed" >&2
  exit 1
}
trap 'rm -rf "$FIXTURE_ROOT"' EXIT

mkdir -p "$FIXTURE_ROOT/tests" "$FIXTURE_ROOT/scripts"

cat >"$FIXTURE_ROOT/tests/test_plain.py" <<'PY'
def test_plain_assertion():
    assert True
PY

cat >"$FIXTURE_ROOT/tests/test_async.py" <<'PY'
async def test_async_assertion():
    assert True
PY

cat >"$FIXTURE_ROOT/tests/test_class.py" <<'PY'
class TestExample:
    def test_method(self):
        assert True
PY

cat >"$FIXTURE_ROOT/tests/helper.py" <<'PY'
import pytest

VALUE = pytest.approx(1)
PY

cat >"$FIXTURE_ROOT/tests/test_main_runner.py" <<'PY'
def test_with_runner():
    assert True

if __name__ == "__main__":
    print("manual diagnostics")
PY

cat >"$FIXTURE_ROOT/tests/no_signal.py" <<'PY'
TEXT = "def test_only_in_a_string(): pass"
# import pytest
PY

cat >"$FIXTURE_ROOT/tests/ordinary.py" <<'PY'
def helper():
    return 1
PY

cat >"$FIXTURE_ROOT/tests/structural_near_miss.py" <<'PY'
class TestWithoutMethods:
    def helper(self):
        return 1

def wrapper():
    def test_nested():
        return True

    return test_nested
PY

cat >"$FIXTURE_ROOT/tests/invalid.py" <<'PY'
def broken(
PY

cat >"$FIXTURE_ROOT/test_root.py" <<'PY'
def test_at_repo_root():
    assert True
PY

cat >"$FIXTURE_ROOT/scripts/pytest_helper.py" <<'PY'
import pytest
PY

cat >"$FIXTURE_ROOT/scripts/unit_test.py" <<'PY'
def test_custom_pattern():
    assert True
PY

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation command
#   expectation:
#     advisory:<marker> — exit 0, stdout empty, stderr contains marker
#     silent            — exit 0, stdout and stderr empty
run_case() {
  local name="$1" expectation="$2" command="$3"
  local payload stdout_file stderr_file stdout_text stderr_text rc marker ok

  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "cwd": sys.argv[2],
}))
' "$command" "$FIXTURE_ROOT")
  stdout_file="$FIXTURE_ROOT/stdout"
  stderr_file="$FIXTURE_ROOT/stderr"

  printf '%s\n' "$payload" |
    python3 "$HOOK" >"$stdout_file" 2>"$stderr_file"
  rc=$?
  stdout_text=$(<"$stdout_file")
  stderr_text=$(<"$stderr_file")
  ok=1

  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$stdout_text" ] || ok=0
      [ -z "$stderr_text" ] || ok=0
      ;;
    advisory:*)
      marker="${expectation#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$stdout_text" ] || ok=0
      case "$stderr_text" in
        *"$marker"*) ;;
        *) ok=0 ;;
      esac
      ;;
    *)
      echo "FAIL  [$name] unknown expectation: $expectation"
      FAIL=$((FAIL + 1))
      FAILED_NAMES+=("$name")
      return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc"
    echo "      stdout=${stdout_text:-<empty>}"
    echo "      stderr=${stderr_text:-<empty>}"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# Direct execution of pytest-shaped files warns.
run_case "plain test function without pytest import" \
  "advisory:python3 -m pytest tests/test_plain.py" \
  "python3 tests/test_plain.py"
run_case "async test function" \
  "advisory:tests/test_async.py" \
  "python tests/test_async.py"
run_case "Test class method" \
  "advisory:tests/test_class.py" \
  "/usr/bin/python3 \"$FIXTURE_ROOT/tests/test_class.py\""
run_case "pytest import under tests directory" \
  "advisory:tests/helper.py" \
  "env MODE=test python3 tests/helper.py"
run_case "shell environment assignment" \
  "advisory:tests/test_plain.py" \
  "MODE=test python3 tests/test_plain.py"
run_case "root test filename" \
  "advisory:test_root.py" \
  "python3 test_root.py"
run_case "interpreter boolean flag" \
  "advisory:tests/test_plain.py" \
  "python3 -u tests/test_plain.py"
run_case "combined interpreter boolean flags" \
  "advisory:tests/test_plain.py" \
  "python3 -B -O tests/test_plain.py"
run_case "interpreter value flag W" \
  "advisory:tests/test_plain.py" \
  "python3 -W error tests/test_plain.py"
run_case "interpreter value flag X" \
  "advisory:tests/test_plain.py" \
  "python3 -X dev tests/test_plain.py"
run_case "double-dash separator" \
  "advisory:tests/test_plain.py" \
  "python3 -- tests/test_plain.py"
run_case "compound command" \
  "advisory:tests/test_plain.py" \
  "echo ready && python3 tests/test_plain.py"
run_case "legitimate main runner remains advisory" \
  "advisory:tests/test_main_runner.py" \
  "python3 tests/test_main_runner.py"

# Pytest/module execution and non-script interpreter modes are correct or
# outside the direct-file-execution surface.
run_case "pytest module execution" \
  "silent" \
  "python3 -m pytest tests/test_plain.py"
run_case "python command string" \
  "silent" \
  "python3 -c 'print(1)'"
run_case "python stdin" \
  "silent" \
  "python3 -"
run_case "other module execution" \
  "silent" \
  "python3 -m compileall tests/test_plain.py"

# Path and source evidence must both be present.
run_case "pytest source outside selected path shape" \
  "silent" \
  "python3 scripts/pytest_helper.py"
run_case "custom suffix outside tests directory is out of scope" \
  "silent" \
  "python3 scripts/unit_test.py"
run_case "comments and strings do not count" \
  "silent" \
  "python3 tests/no_signal.py"
run_case "ordinary file under tests has no test structure" \
  "silent" \
  "python3 tests/ordinary.py"
run_case "Test class and nested test near-misses" \
  "silent" \
  "python3 tests/structural_near_miss.py"
run_case "invalid Python fails open" \
  "silent" \
  "python3 tests/invalid.py"
run_case "missing file fails open" \
  "silent" \
  "python3 tests/test_missing.py"
printf -v dynamic_command 'python3 "$%s"' TEST_FILE
run_case "dynamic path fails open" \
  "silent" \
  "$dynamic_command"
run_case "only first script operand is inspected" \
  "silent" \
  "python3 tests/ordinary.py tests/test_plain.py"

# Non-executable text is not a Python command segment.
run_case "commented command" \
  "silent" \
  "# python3 tests/test_plain.py"
run_case "echoed command string" \
  "silent" \
  "echo 'python3 tests/test_plain.py'"
run_case "heredoc body" \
  "silent" \
  $'cat <<\'EOF\'\npython3 tests/test_plain.py\nEOF'
run_case "direct execution before heredoc opener" \
  "advisory:tests/test_plain.py" \
  $'python3 tests/test_plain.py && cat <<\'EOF\'\ntext\nEOF'
run_case "direct execution after heredoc closes" \
  "advisory:tests/test_plain.py" \
  $'cat <<\'EOF\'\ntext\nEOF\npython3 tests/test_plain.py'
run_case "quoted heredoc lookalike does not hide later execution" \
  "advisory:tests/test_plain.py" \
  $'echo "<<EOF"\npython3 tests/test_plain.py'
run_case "here-string does not hide later execution" \
  "advisory:tests/test_plain.py" \
  "read value <<< text; python3 tests/test_plain.py"

# Fail-open protocol cases.
stdout_file="$FIXTURE_ROOT/stdout"
stderr_file="$FIXTURE_ROOT/stderr"
printf '%s\n' '{"tool_name":"Read","tool_input":{"command":"python3 tests/test_plain.py"}}' |
  python3 "$HOOK" >"$stdout_file" 2>"$stderr_file"
rc=$?
if [ "$rc" -eq 0 ] && [ ! -s "$stdout_file" ] && [ ! -s "$stderr_file" ]; then
  echo "PASS  [non-Bash tool is silent]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [non-Bash tool is silent]"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("non-Bash tool is silent")
fi

printf '%s\n' 'not json' |
  python3 "$HOOK" >"$stdout_file" 2>"$stderr_file"
rc=$?
if [ "$rc" -eq 0 ] && [ ! -s "$stdout_file" ] && [ ! -s "$stderr_file" ]; then
  echo "PASS  [malformed JSON fails open]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [malformed JSON fails open]"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("malformed JSON fails open")
fi

echo ""
echo "== $PASS passed, $FAIL failed =="
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for name in "${FAILED_NAMES[@]}"; do
    echo "  - $name"
  done
  exit 1
fi
