#!/usr/bin/env bash
# tests/test_wrapper_stdout_contract.sh — Step 4 stdio contract, prose half (#1054)
#
# The executable behaviour is pinned in tests/test_wrapper_stdout_contract.py.
# What this file pins is the SKILL.md text, for reasons the code cannot cover:
#
#   1. Piping the prompt looks harmless and reads as the safer form — it is the
#      shape most callers reach for. The comment saying what it costs (fd 0, and
#      with it every permission prompt and `cmux send`) is the load-bearing part,
#      so it is asserted like code.
#   2. `"$(cat file)"` looks like the shell-interpolation failure the skill spent
#      years excluding. The sentence separating an inline literal from a quoted
#      command substitution is what stops a future reader from "restoring" the
#      pipe on that reasoning.
#   3. A notify that ignores the exit code reports a dead worker as a finished
#      one. That is #1054's original symptom, so the rc gating is asserted here
#      too — deleting it silently reopens the bug.
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
# 1. Why the prompt is not piped — the cost is named, not just the rule
# ---------------------------------------------------------------------------

assert_present \
  "the call site says the prompt goes through argv" \
  "The prompt goes through argv and stdin stays empty"

assert_present \
  "the mechanism is stated: cat exiting is what closes fd 0" \
  "the moment \`cat\` exits the worker's fd 0 closes"

assert_present \
  "the measured consequence is cited, not just asserted" \
  '"Awaiting your confirmation" and exited 0'

assert_present \
  "the stdout half names its own cost — the pane goes blank" \
  "block buffering kicked in and the running"

# ---------------------------------------------------------------------------
# 2. The distinction that keeps the pipe from being "restored"
# ---------------------------------------------------------------------------

# "Why Wrapper Script?" records a real failure with an inline literal prompt
# (Hub #1001). The new prose must keep that exclusion while separating it from
# the argv form, or a future reader re-derives the pipe from the same story.
assert_present \
  "the inline-literal exclusion is restated as still binding" \
  "as a literal in the script body or on the command line"

assert_present \
  "the two are separated: a quoted substitution is not re-expanded" \
  "the shell does not re-interpret the substitution result"

assert_present \
  "the separation is backed by a transcribed run, not an argument" \
  'cost is $5 `whoami` ${HOME}'

# ---------------------------------------------------------------------------
# 3. The completion signal must follow the exit code
# ---------------------------------------------------------------------------

assert_present \
  "the notify branches on rc rather than firing unconditionally" \
  'if [ "$rc" -eq 0 ]; then'

assert_present \
  "the failure branch carries the exit code to the reader" \
  "Task FAILED (exit \$rc)"

assert_present \
  "why rc gating exists is stated, not left to be re-derived" \
  "that is #1054's original symptom"

# Measured after the switch: an argv+TTY worker stays interactive, so the rc
# branch is never reached on task completion. Leaving that unsaid re-creates
# #1054's false-completion expectation with a different mechanism.
assert_present \
  "the notify is disclaimed as a completion signal on the claude branch" \
  "these lines are not run at task completion"

# There is no completion oracle to name any more (#1130): the skill is
# fire-and-forget, so the disclaimer has to say where the result actually is
# instead of pointing at a collection step.
assert_present \
  "the notify's disclaimer routes the reader to the worker's own tab" \
  "user checks the result directly in cmux"

# ---------------------------------------------------------------------------
# 4. The argv size limit is guarded, and the guard refuses a fallback
# ---------------------------------------------------------------------------

assert_present \
  "the prompt is measured against the kernel argument limit" \
  "ARG_LIMIT=\$(( \$(getconf ARG_MAX) / 4 ))"

assert_present \
  "the guard fails loudly instead of falling back to the pipe" \
  "No fallback is provided"

# ---------------------------------------------------------------------------
# 5. Contracts this change must not displace
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

# ---------------------------------------------------------------------------
# 6. Findings from the #1057 review — each one was live-reproduced, so each
#    gets an assertion rather than a comment. Deleting any of these silently
#    restores a shape that was measured to fail.
# ---------------------------------------------------------------------------

# A missing prompt file passed the old guard and handed claude an empty argv:
# the worker sat idle and the wrapper reported rc=0 — #1054's shape again.
assert_present \
  "the wrapper refuses to start on an unreadable or empty prompt file" \
  'if ! PROMPT_BYTES=$(wc -c < "$PROMPT_FILE" 2>/dev/null) || [ "$PROMPT_BYTES" -eq 0 ]; then'

# ARG_MAX is the argv+envp total; Linux caps each single string separately at
# 32 pages, which is 4x smaller than ARG_MAX/4 on a typical Linux box.
assert_present \
  "the per-string kernel limit is enforced alongside the total" \
  'STR_LIMIT=$(( 32 * $(getconf PAGE_SIZE) ))'

assert_present \
  "the smaller of the two limits wins" \
  '[ "$STR_LIMIT" -lt "$ARG_LIMIT" ] && ARG_LIMIT=$STR_LIMIT'

# --max-budget-usd is print-mode only, and this worker is interactive.
assert_absent \
  "no budget flag reaches the interactive claude invocation" \
  "{budget_flag} \\"

# rc here reports how the session closed, not whether the task finished.
assert_present \
  "the claude notify says session exit, not task completion" \
  'Claude session exited (rc=$rc)'

# Command substitution strips trailing newlines; the pipe did not.
assert_present \
  "the one behavioural difference from the pipe is stated" \
  "trailing newlines are stripped"

assert_lib_summary
