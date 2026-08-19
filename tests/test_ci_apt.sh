#!/usr/bin/env bash
# test_ci_apt.sh — verify scripts/ci-apt.sh retries a stalled apt phase (#1049).
#
# The real failure cannot be reproduced on demand: it needs an Ubuntu mirror
# that accepts the connection and then stops responding. What CAN be pinned is
# the script's own control flow under a controlled dependency, so `sudo`,
# `timeout` and `apt-get` are stubbed on PATH and made to fail in specific ways.
#
# The case that matters most is `exit_124_is_retried`: 124 is timeout(1)'s
# "I killed the child" code and is exactly what the production stall produced
# (observed on run 32223240516). A retry loop that treated 124 as fatal would
# look correct in every other case and still be useless for the bug it exists
# for.
#
# Usage: bash tests/test_ci_apt.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="$ROOT_DIR/scripts/ci-apt.sh"

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

echo "test_ci_apt"

if [ ! -f "$TARGET" ]; then
  echo "FAIL  [target_exists] expected=yes got=no"
  exit 1
fi

STUB_ROOT="$(mktemp -d)" || { echo "FAIL  [mktemp] could not create temp dir"; exit 1; }
trap 'rm -rf "$STUB_ROOT"' EXIT

BIN="$STUB_ROOT/bin"
mkdir -p "$BIN" || { echo "FAIL  [mkdir_bin]"; exit 1; }

# `sudo` and `timeout` become transparent pass-throughs so the stubbed apt-get
# is what decides each attempt's outcome. timeout drops its duration argument.
cat > "$BIN/sudo" <<'EOF'
#!/usr/bin/env bash
exec "$@"
EOF
cat > "$BIN/timeout" <<'EOF'
#!/usr/bin/env bash
shift
exec "$@"
EOF

# apt-get counts its own invocations in a file and exits with whatever
# STUB_APT_EXIT says. STUB_APT_SUCCEED_ON, when set, makes the Nth *update*
# call (and everything after it) succeed, modelling a mirror that recovers.
cat > "$BIN/apt-get" <<'EOF'
#!/usr/bin/env bash
mode=""
for arg in "$@"; do
  case "$arg" in
    update|install) mode="$arg"; break ;;
  esac
done
if [ "$mode" = "update" ]; then
  n=$(( $(cat "$STUB_COUNT" 2>/dev/null || echo 0) + 1 ))
  echo "$n" > "$STUB_COUNT"
  if [ -n "${STUB_APT_SUCCEED_ON:-}" ] && [ "$n" -ge "$STUB_APT_SUCCEED_ON" ]; then
    exit 0
  fi
  exit "${STUB_APT_EXIT:-1}"
fi
# install: only reached when update succeeded
exit 0
EOF
chmod +x "$BIN/sudo" "$BIN/timeout" "$BIN/apt-get"

export STUB_COUNT="$STUB_ROOT/count"

# Backoff to 0 so the suite does not spend real seconds sleeping.
run_target() {
  : > "$STUB_COUNT"
  PATH="$BIN:$PATH" \
  CI_APT_ATTEMPTS="${ATTEMPTS_OVERRIDE:-3}" \
  CI_APT_TIMEOUT=5 \
  CI_APT_BACKOFF=0 \
    bash "$TARGET" somepkg >"$STUB_ROOT/out" 2>&1
  echo "$?"
}

attempts_made() { cat "$STUB_COUNT" 2>/dev/null || echo 0; }

# 1. No packages named is a usage error, not a silent success.
PATH="$BIN:$PATH" bash "$TARGET" >/dev/null 2>&1
run_case "no_args_is_usage_error" "$?" "2"

# 2. A mirror down for the whole window fails the step after N attempts.
STUB_APT_EXIT=1 run_case "all_attempts_fail_exits_1" "$(STUB_APT_EXIT=1 run_target)" "1"
run_case "all_attempts_fail_tries_3" "$(attempts_made)" "3"

# 3. The production signature: timeout killed apt. Must be retried, not fatal.
run_case "exit_124_is_retried" "$(STUB_APT_EXIT=124 run_target)" "1"
run_case "exit_124_tries_3" "$(attempts_made)" "3"
grep -q "exit 124 -- mirror stalled" "$STUB_ROOT/out"
run_case "exit_124_names_the_stall" "$?" "0"

# 4. A mirror that recovers mid-window is the whole point: succeed and stop.
run_case "recovers_on_second_attempt" "$(STUB_APT_EXIT=1 STUB_APT_SUCCEED_ON=2 run_target)" "0"
run_case "recovery_stops_early" "$(attempts_made)" "2"

# 5. First-attempt success must not retry at all — the common path.
run_case "clean_run_succeeds" "$(STUB_APT_SUCCEED_ON=1 run_target)" "0"
run_case "clean_run_tries_once" "$(attempts_made)" "1"

# 6. The attempt count is configurable, so the budget can be tuned per caller.
run_case "attempts_are_configurable" "$(ATTEMPTS_OVERRIDE=2 STUB_APT_EXIT=1 run_target)" "1"
run_case "attempts_override_respected" "$(attempts_made)" "2"

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf 'failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
