#!/usr/bin/env bash
# tests/test_wrapper_stdout_contract.sh — Step 4 wrapper template, shape half (#1054)
#
# tests/test_wrapper_stdout_contract.py runs the extracted `claude)` branch
# under a PTY and pins its stdio behaviour. The guards below live in the same
# SKILL.md wrapper template but outside what that run reaches (rc gating, the
# prompt-file and argv-size guards, the trap), so their shape is pinned here.
# The rationale prose around them is not pinned: no code reads it.
#
# Run:  bash tests/test_wrapper_stdout_contract.sh
# Exit: 0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL="$ROOT_DIR/skills/cmux-delegate/SKILL.md"

# shellcheck source=./_assert_lib.sh
source "$SCRIPT_DIR/_assert_lib.sh"
assert_lib_init "$SKILL"

# ---------------------------------------------------------------------------
# 1. The completion signal must follow the exit code
# ---------------------------------------------------------------------------

# A notify that ignores the exit code reports a dead worker as a finished one —
# #1054's original symptom.
assert_present \
  "the notify branches on rc rather than firing unconditionally" \
  'if [ "$rc" -eq 0 ]; then'

assert_present \
  "the failure branch carries the exit code to the reader" \
  "Task FAILED (exit \$rc)"

# rc here reports how the session closed, not whether the task finished.
assert_present \
  "the claude notify says session exit, not task completion" \
  'Claude session exited (rc=$rc)'

# ---------------------------------------------------------------------------
# 2. The argv size limit is guarded
# ---------------------------------------------------------------------------

assert_present \
  "the prompt is measured against the kernel argument limit" \
  "ARG_LIMIT=\$(( \$(getconf ARG_MAX) / 4 ))"

# ARG_MAX is the argv+envp total; Linux caps each single string separately at
# 32 pages, which is 4x smaller than ARG_MAX/4 on a typical Linux box.
assert_present \
  "the per-string kernel limit is enforced alongside the total" \
  'STR_LIMIT=$(( 32 * $(getconf PAGE_SIZE) ))'

assert_present \
  "the smaller of the two limits wins" \
  '[ "$STR_LIMIT" -lt "$ARG_LIMIT" ] && ARG_LIMIT=$STR_LIMIT'

# A missing prompt file passed the old guard and handed claude an empty argv:
# the worker sat idle and the wrapper reported rc=0 — #1054's shape again.
assert_present \
  "the wrapper refuses to start on an unreadable or empty prompt file" \
  'if ! PROMPT_BYTES=$(wc -c < "$PROMPT_FILE" 2>/dev/null) || [ "$PROMPT_BYTES" -eq 0 ]; then'

# ---------------------------------------------------------------------------
# 3. Shapes this template must not regain
# ---------------------------------------------------------------------------

assert_present \
  "the trap still deletes only the .sh" \
  "trap 'rm -f \"\$SCRIPT_FILE\"' EXIT"

assert_absent \
  "the prompt is never piped into claude again" \
  'cat "$PROMPT_FILE" | {claude_env} claude'

assert_absent \
  "no -p form is reintroduced" \
  'claude -p "$(cat'

assert_absent \
  "the worker is not handed a permission-mode override" \
  "--permission-mode {permission_mode}"

# --max-budget-usd is print-mode only, and this worker is interactive.
assert_absent \
  "no budget flag reaches the interactive claude invocation" \
  "{budget_flag} \\"

assert_lib_summary
