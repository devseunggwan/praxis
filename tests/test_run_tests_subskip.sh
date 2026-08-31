#!/usr/bin/env bash
# test_run_tests_subskip.sh — verify run-tests.sh folds sub-suite skips into
# SKIPPED_TOOLS via the PRAXIS_SUBSKIP marker protocol (#1170).
#
# Before #1170, a test file that hit a missing tool printed "SKIP jq
# unavailable" and exited 0 — indistinguishable from a pass, invisible to
# PRAXIS_TESTS_STRICT (#917). The protocol: a sub-suite that must skip
# entirely prints, to stdout,
#     PRAXIS_SUBSKIP: <tool> <file>
# and exits 0; run_sh() in scripts/run-tests.sh scans its captured stdout and
# folds each announced tool into the same SKIPPED_TOOLS accounting as the
# top-level tool steps.
#
# run_sh() is extracted from run-tests.sh by its literal function-body span
# and exercised against synthetic sub-suites, so the code under test is the
# shipped code, not a copy that could drift. Running the whole runner would
# recurse into this very file.
#
# The fake marker lines are never re-emitted at column 0 on this file's own
# stdout: this test itself runs under run_sh(), and a leaked literal marker
# would fold the fake tool into the real run's strict-mode accounting.
#
# Usage: bash tests/test_run_tests_subskip.sh
# Exit:  0 = all pass; 1 = at least one fail

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
RUNNER="$ROOT/scripts/run-tests.sh"

PASS=0
FAIL=0
FAILED_NAMES=()

pass() { PASS=$((PASS + 1)); echo "PASS  $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED_NAMES+=("$1"); echo "FAIL  $1: $2" >&2; }

[ -f "$RUNNER" ] || { echo "FAIL: runner not found: $RUNNER" >&2; exit 1; }

# --- Extract the shipped marker constant and run_sh() ------------------------
MARKER_DEF="$(grep '^SUBSKIP_MARKER=' "$RUNNER")"
RUN_SH_DEF="$(sed -n '/^run_sh() {/,/^}/p' "$RUNNER")"

if [ -z "$MARKER_DEF" ] || [ -z "$RUN_SH_DEF" ]; then
  echo "FAIL: could not extract SUBSKIP_MARKER / run_sh() from $RUNNER" >&2
  echo "      (the extraction anchors must track any refactor of run-tests.sh)" >&2
  exit 1
fi

eval "$MARKER_DEF"
eval "$RUN_SH_DEF"

# --- Environment run_sh() expects --------------------------------------------
PRAXIS_HOME="$(mktemp -d)" || { echo "FAIL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$PRAXIS_HOME"' EXIT
SHELL_FAILED=0
SKIPPED_TOOLS=()

FAKES="$PRAXIS_HOME/fakes"
mkdir -p "$FAKES"

# Synthetic sub-suites. Marker text is assembled from the extracted constant so
# these fixtures cannot drift from the runner's own token.
printf '#!/usr/bin/env bash\necho "%s faketool $0"\necho "SKIP  faketool unavailable"\nexit 0\n' \
  "$SUBSKIP_MARKER" > "$FAKES/test_skips.sh"
printf '#!/usr/bin/env bash\necho "%s faketool $0"\nexit 0\n' \
  "$SUBSKIP_MARKER" > "$FAKES/test_skips_again.sh"
printf '#!/usr/bin/env bash\necho "%s othertool $0" >&2\nexit 0\n' \
  "$SUBSKIP_MARKER" > "$FAKES/test_stderr_marker.sh"
printf '#!/usr/bin/env bash\necho "all good"\nexit 0\n' > "$FAKES/test_clean.sh"
printf '#!/usr/bin/env bash\necho "boom"\nexit 1\n' > "$FAKES/test_fails.sh"

# --- 1. A marker on stdout is folded into SKIPPED_TOOLS ----------------------
run_sh "$FAKES/test_skips.sh" >/dev/null
if [ "${#SKIPPED_TOOLS[@]}" -eq 1 ] && [ "${SKIPPED_TOOLS[0]}" = "faketool" ]; then
  pass "marker on stdout folds the tool into SKIPPED_TOOLS"
else
  fail "marker on stdout folds the tool into SKIPPED_TOOLS" \
    "SKIPPED_TOOLS=(${SKIPPED_TOOLS[*]-})"
fi
if [ "$SHELL_FAILED" -eq 0 ]; then
  pass "a marker-announced skip is not a shell-test failure"
else
  fail "a marker-announced skip is not a shell-test failure" "SHELL_FAILED=$SHELL_FAILED"
fi

# --- 2. The same tool from a second file is deduped --------------------------
run_sh "$FAKES/test_skips_again.sh" >/dev/null
if [ "${#SKIPPED_TOOLS[@]}" -eq 1 ]; then
  pass "same tool announced twice is recorded once"
else
  fail "same tool announced twice is recorded once" "SKIPPED_TOOLS=(${SKIPPED_TOOLS[*]-})"
fi

# --- 3. Marker on stderr is NOT part of the protocol -------------------------
run_sh "$FAKES/test_stderr_marker.sh" >/dev/null 2>/dev/null
case " ${SKIPPED_TOOLS[*]-} " in
  *" othertool "*) fail "stderr marker is ignored (protocol is stdout-only)" \
    "othertool was folded from stderr" ;;
  *) pass "stderr marker is ignored (protocol is stdout-only)" ;;
esac

# --- 4. A clean pass adds nothing --------------------------------------------
before=${#SKIPPED_TOOLS[@]}
run_sh "$FAKES/test_clean.sh" >/dev/null
if [ "${#SKIPPED_TOOLS[@]}" -eq "$before" ] && [ "$SHELL_FAILED" -eq 0 ]; then
  pass "clean pass adds no skip and no failure"
else
  fail "clean pass adds no skip and no failure" \
    "SKIPPED_TOOLS=(${SKIPPED_TOOLS[*]-}) SHELL_FAILED=$SHELL_FAILED"
fi

# --- 5. A failing sub-suite still fails --------------------------------------
run_sh "$FAKES/test_fails.sh" >/dev/null 2>/dev/null
if [ "$SHELL_FAILED" -eq 1 ]; then
  pass "nonzero sub-suite exit still sets SHELL_FAILED"
else
  fail "nonzero sub-suite exit still sets SHELL_FAILED" "SHELL_FAILED=$SHELL_FAILED"
fi

# --- 6. Every known whole-file skip guard emits the marker -------------------
# Static check (no PATH games): a guard that exits 0 for a missing tool must
# print the marker first, or the skip is invisible to strict mode again.
GUARDED_FILES=(
  "tests/test_no_live_keys_in_fixtures.sh"
  "tests/test_catalog_monotonic.sh"
  "tests/test_bypass_review.sh"
  "tests/hooks/postuse-correction/test_second_failure_advisory.sh"
  "tests/hooks/postuse-correction/test_builtin_task_postuse.sh"
  "tests/hooks/postuse-correction/test_askuserquestion_loop_signal.sh"
  "tests/hooks/postuse-correction/test_bypass_telemetry.sh"
  "tests/hooks/preflight-gate/test_block_unmatched_glob.sh"
  "tests/test_cmux_session_orphan_windows.sh"
)
missing=()
for gf in "${GUARDED_FILES[@]}"; do
  grep -q "^  *echo \"$SUBSKIP_MARKER" "$ROOT/$gf" \
    || grep -q "{ echo \"$SUBSKIP_MARKER" "$ROOT/$gf" \
    || missing+=("$gf")
done
if [ "${#missing[@]}" -eq 0 ]; then
  pass "all known whole-file skip guards emit the marker"
else
  fail "all known whole-file skip guards emit the marker" "missing: ${missing[*]}"
fi

# --- Summary -----------------------------------------------------------------
echo ""
echo "Passed: $PASS  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf 'failed: %s\n' "${FAILED_NAMES[@]}" >&2
  exit 1
fi
exit 0
