#!/bin/bash
# test_bash_worktree_existence_advisory.sh — coverage for
# hooks/bash-worktree-existence-advisory.sh
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory:<marker>  — exit 0, stderr contains <marker>
#   silent             — exit 0, stderr empty
#
# Usage: bash tests/test_bash_worktree_existence_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$ROOT_DIR/hooks/bash-worktree-existence-advisory.sh"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation command session_id
#   expectation:
#     "advisory:<marker>" — exit 0, stderr contains <marker>
#     "silent"            — exit 0, stderr empty
run_case() {
  local name="$1" expectation="$2" command="$3" sid="${4:-}"

  # Build payload. Use empty string for session_id when not provided so
  # dedupe is disabled (each test case sees a fresh state).
  local payload
  payload=$(python3 -c '
import json, sys
d = {
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}
if sys.argv[2]:
    d["session_id"] = sys.argv[2]
print(json.dumps(d))
' "$command" "$sid")

  local err_file
  err_file=$(mktemp)
  echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    advisory:*)
      local marker="${expectation#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$marker"*) ;;
        *) ok=0 ;;
      esac
      ;;
    *)
      echo "FAIL  [$name] unknown expectation: $expectation"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Setup: scratch directories for testing
# ---------------------------------------------------------------------------

# Existing directory that is NOT a registered git worktree.
NON_WT_DIR=$(mktemp -d)

# Path that does not exist at all.
MISSING_PATH="$NON_WT_DIR/does-not-exist-$$"

# A real registered worktree: the root of the praxis clone (always registered).
REAL_WT_PATH="$(git -C "$ROOT_DIR" rev-parse --show-toplevel 2>/dev/null)"

# ---------------------------------------------------------------------------
# === CORE advisory path ===
# ---------------------------------------------------------------------------

# C1: missing path → [worktree-missing]
run_case "missing path emits worktree-missing" \
  "advisory:[worktree-missing]" \
  "cd $MISSING_PATH && ls"

# Compound form: missing path in the cd segment
run_case "missing path in compound command" \
  "advisory:[worktree-missing]" \
  "cd $MISSING_PATH && git status && echo done"

# ---------------------------------------------------------------------------
# === SILENT paths — existing dir, registered worktree ===
# ---------------------------------------------------------------------------

# Existing non-worktree dir: [worktree-stale] removed (M5 option B) — silent.
run_case "existing non-worktree dir is silent (M5: stale removed)" \
  "silent" \
  "cd $NON_WT_DIR && ls"

# Registered worktree → silent.
if [ -n "$REAL_WT_PATH" ] && [ -d "$REAL_WT_PATH" ]; then
  run_case "registered worktree path is silent" \
    "silent" \
    "cd $REAL_WT_PATH && git status"
else
  echo "SKIP  [registered worktree path is silent] — cannot determine repo root"
fi

# ---------------------------------------------------------------------------
# === C1 fix: tilde expansion → silent ===
# ---------------------------------------------------------------------------

run_case "cd tilde alone is silent" \
  "silent" \
  "cd ~"

run_case "cd tilde-slash prefix is silent" \
  "silent" \
  "cd ~/projects/foo && ls"

run_case "cd user-tilde is silent" \
  "silent" \
  "cd ~root/foo && ls"

# ---------------------------------------------------------------------------
# === missing path fires regardless of hook-process cwd (no git worktree lookup) ===
# [worktree-stale] was removed (M5 option B) — the hook now only checks
# whether the path exists on disk, not whether it is a registered worktree.
# ---------------------------------------------------------------------------

run_case "missing path advisory works regardless of cwd context" \
  "advisory:[worktree-missing]" \
  "cd $MISSING_PATH"

# ---------------------------------------------------------------------------
# === P1 (Phase 2): pushd and subshell (cd ...) detection ===
# ---------------------------------------------------------------------------

# pushd to non-existent path fires advisory (Phase 2 — #337 P1).
run_case "pushd non-existent path emits worktree-missing" \
  "advisory:[worktree-missing]" \
  "pushd $MISSING_PATH && ls"

# subshell (cd ...) to non-existent path fires advisory (Phase 2 — #337 P1).
run_case "subshell cd non-existent emits worktree-missing" \
  "advisory:[worktree-missing]" \
  "(cd $MISSING_PATH && ls)"

# pushd to existing path is silent.
run_case "pushd existing path is silent" \
  "silent" \
  "pushd $NON_WT_DIR && ls"

# subshell (cd ...) to existing path is silent.
run_case "subshell cd existing path is silent" \
  "silent" \
  "(cd $NON_WT_DIR && ls)"

# ---------------------------------------------------------------------------
# === Codex P2 fixes: spaced subshell form + subshell cwd isolation ===
# ---------------------------------------------------------------------------

# Spaced subshell form `( cd /path && ... )` fires advisory for missing path.
run_case "spaced subshell cd non-existent emits worktree-missing" \
  "advisory:[worktree-missing]" \
  "( cd $MISSING_PATH && ls )"

# Spaced subshell form to existing path is silent.
run_case "spaced subshell cd existing path is silent" \
  "silent" \
  "( cd $NON_WT_DIR && ls )"

# Subshell cwd does not leak: `(cd existing); cd existing2` must NOT emit a
# false advisory for existing2 (which exists at the original cwd level, not
# inside the subshell target).
# Use $ROOT_DIR itself as the outer reference to guarantee it exists.
run_case "subshell cwd does not leak into subsequent relative cd (compact form)" \
  "silent" \
  "(cd $NON_WT_DIR && pwd); cd $ROOT_DIR"

run_case "spaced subshell cwd does not leak into subsequent relative cd" \
  "silent" \
  "( cd $NON_WT_DIR && pwd ); cd $ROOT_DIR"

# ---------------------------------------------------------------------------
# === M2 fix: heredoc body containing cd is skipped (no false-positive) ===
# ---------------------------------------------------------------------------

# `cat << EOF\ncd /missing\nEOF` — body segment `cd /missing` must be silent.
run_case "heredoc body cd non-existent is silent" \
  "silent" \
  "$(printf 'cat << EOF\ncd /this/does/not/exist/anywhere\nEOF')"

# cd AFTER the heredoc close still fires (not suppressed by heredoc skip).
run_case "cd after heredoc close fires advisory" \
  "advisory:[worktree-missing]" \
  "$(printf 'cat << EOF\ncd /this/does/not/exist/anywhere\nEOF\ncd /also/does/not/exist')"

# Non-cd opener command (cat /tmp/foo.txt) with no heredoc — still silent.
run_case "non-cd command without heredoc is silent" \
  "silent" \
  "cat /tmp/foo.txt"

# ---------------------------------------------------------------------------
# === P6 (Phase 2): fused heredoc openers — regression guard (#337 P6) ===
# shlex tokenizes fused forms as single POSITIONAL tokens; _extract_heredoc_end_marker
# now detects them by matching the `<<` prefix pattern. Body `cd /path` must
# be silent (no false-positive advisory). These 3 cases were formerly pinned as
# known-limitation false-positives and are now expected to be silent.
# ---------------------------------------------------------------------------

run_case "fused <<EOF body cd is silent (P6 fix)" \
  "silent" \
  "$(printf 'cat <<EOF\ncd /this/does/not/exist/anywhere\nEOF')"

run_case "fused <<'EOF' body cd is silent (P6 fix)" \
  "silent" \
  "$(printf "cat <<'EOF'\ncd /this/does/not/exist/anywhere\nEOF")"

run_case "fused <<-EOF body cd is silent (P6 fix)" \
  "silent" \
  "$(printf 'cat <<-EOF\n\tcd /this/does/not/exist/anywhere\n\tEOF')"

# fused <<"EOF" form — shlex strips quotes and produces <<EOF token (same as <<EOF).
run_case "fused <<\"EOF\" body cd is silent (P6 fix)" \
  "silent" \
  "$(printf 'cat <<"EOF"\ncd /this/does/not/exist/anywhere\nEOF')"

# cd AFTER a fused heredoc close still fires advisory (regression guard:
# the post-heredoc segment is processed normally).
run_case "cd after fused <<EOF heredoc close fires advisory" \
  "advisory:[worktree-missing]" \
  "$(printf 'cat <<EOF\ncd /this/does/not/exist/anywhere\nEOF\ncd /also/does/not/exist')"

# ---------------------------------------------------------------------------
# === M3 fix: dedupe keyed on (session, path, state) — state-change aware ===
# ---------------------------------------------------------------------------

# Same session, same missing path → second call deduplicated.
DEDUP_SID="test-dedup-$$"

run_case "first call emits advisory" \
  "advisory:[worktree-missing]" \
  "cd $MISSING_PATH" \
  "$DEDUP_SID"

run_case "second call for same missing path is deduped (silent)" \
  "silent" \
  "cd $MISSING_PATH" \
  "$DEDUP_SID"

# Different session → advisory fires again (not deduped across sessions).
run_case "different session fires advisory independently" \
  "advisory:[worktree-missing]" \
  "cd $MISSING_PATH" \
  "test-dedup-other-$$"

# M3 state-transition: missing → mkdir (cd existing clears marker) → rmdir → cd missing again fires.
# Step 1: cd missing → advisory + marker written.
TRANSITION_SID="test-transition-$$"
TRANSITION_PATH="$NON_WT_DIR/transition-dir-$$"

run_case "state-transition step1: missing fires advisory" \
  "advisory:[worktree-missing]" \
  "cd $TRANSITION_PATH" \
  "$TRANSITION_SID"

# Step 2: create the directory, then cd existing → marker cleared.
mkdir -p "$TRANSITION_PATH"
run_case "state-transition step2: existing path is silent and clears marker" \
  "silent" \
  "cd $TRANSITION_PATH" \
  "$TRANSITION_SID"

# Step 3: remove the directory, cd again → advisory fires (marker was cleared in step 2).
rmdir "$TRANSITION_PATH" 2>/dev/null || true
run_case "state-transition step3: missing again fires advisory (marker cleared)" \
  "advisory:[worktree-missing]" \
  "cd $TRANSITION_PATH" \
  "$TRANSITION_SID"

# ---------------------------------------------------------------------------
# === Infrastructure / fail-open cases ===
# ---------------------------------------------------------------------------

# Non-Bash tool_name → silent.
infra_payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Read",
    "tool_input": {"command": "cd /nonexistent/path && ls"},
    "session_id": "test-infra-read-$$",
}))' 2>/dev/null)
infra_err=$(mktemp)
echo "$infra_payload" | "$HOOK" >/dev/null 2>"$infra_err"
infra_rc=$?
infra_err_content=$(cat "$infra_err")
rm -f "$infra_err"
if [ "$infra_rc" -eq 0 ] && [ -z "$infra_err_content" ]; then
  echo "PASS  [non-Bash tool_name passthrough]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [non-Bash tool_name passthrough] rc=$infra_rc stderr=${infra_err_content:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-Bash tool_name passthrough")
fi

# Malformed JSON → silent (fail-open).
malformed_err=$(mktemp)
echo "not-valid-json" | "$HOOK" >/dev/null 2>"$malformed_err"
malformed_rc=$?
malformed_err_content=$(cat "$malformed_err")
rm -f "$malformed_err"
if [ "$malformed_rc" -eq 0 ] && [ -z "$malformed_err_content" ]; then
  echo "PASS  [malformed JSON stdin is silent]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [malformed JSON stdin is silent] rc=$malformed_rc stderr=${malformed_err_content:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed JSON stdin is silent")
fi

run_case "bare cd is silent" \
  "silent" \
  "cd"

run_case "cd with dollar-sign variable is silent" \
  "silent" \
  'cd $SOME_VAR && ls'

run_case "cd dash is silent" \
  "silent" \
  "cd -"

run_case "opt-out marker suppresses advisory" \
  "silent" \
  "cd $MISSING_PATH  # worktree-advisory:ack"

run_case "empty command is silent" \
  "silent" \
  ""

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

rm -rf "$NON_WT_DIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
fi

[ "$FAIL" -eq 0 ]
