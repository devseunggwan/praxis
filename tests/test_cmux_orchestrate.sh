#!/usr/bin/env bash
# tests/test_cmux_orchestrate.sh — Run/Task ledger document pins (issue #982)
#
# Two documents are pinned here: the cmux-orchestrate skill that owns the
# ledger, and the cmux-delegate steps that feed it.
#
# The load-bearing assertion is an ABSENCE. Issue #982's body asked for the
# Task lifecycle to be reflected through `cmux todo add / start / check`, and
# `cmux todo --help` forbids exactly that:
#
#   Note for coding agents: this checklist belongs to the user. Do not add,
#   edit, complete, remove, or replace items on your own initiative.
#
# So the route was not taken. A later editor reading only the issue would see
# an unticked checkbox and an obvious command to tick it with, which is how a
# vendor prohibition gets walked back by someone acting in good faith. §4 is
# what stops that, and it is worth more than any of the presence checks.
#
# Run:  bash tests/test_cmux_orchestrate.sh
# Exit: 0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL="$ROOT_DIR/skills/cmux-orchestrate/SKILL.md"
DELEGATE="$ROOT_DIR/skills/cmux-delegate/SKILL.md"

# shellcheck source=./_assert_lib.sh
source "$SCRIPT_DIR/_assert_lib.sh"
assert_lib_init "$SKILL"

# ---------------------------------------------------------------------------
# 1. The record survives what the view does not
# ---------------------------------------------------------------------------

assert_present \
  "the ledger is named as the record and cmux as the view" \
  "the ledger is the record and cmux is a view"

assert_present \
  "an absent cmux costs no answer" \
  "The ledger is written and the answer is unaffected"

assert_present \
  "a malformed invocation is the one failure not swallowed" \
  "exit 2"

# ---------------------------------------------------------------------------
# 2. The state vocabulary the caller reads
# ---------------------------------------------------------------------------

for state in pending running blocked complete closed empty; do
  assert_present_re \
    "state '$state' is documented" \
    "^\| \`$state\` \|"
done

assert_present \
  "empty is distinguished from complete, and why" \
  "0 == 0"

assert_present \
  "blocked is not sticky" \
  "Blocked is a current condition, not a scar"

# ---------------------------------------------------------------------------
# 3. The selector is the UUID
# ---------------------------------------------------------------------------

assert_present \
  "binding takes the workspace UUID, not the ref number" \
  "workspace **UUID**, not \`workspace:N\`"

# ---------------------------------------------------------------------------
# 4. The vendor prohibition — an absence, and the reason it is written down
# ---------------------------------------------------------------------------

assert_absent \
  "the skill never invokes cmux todo" \
  "cmux todo add"

assert_absent \
  "no todo start either" \
  "cmux todo start"

assert_absent \
  "no todo check either" \
  "cmux todo check"

assert_present \
  "the prohibition is quoted rather than paraphrased" \
  "this checklist belongs to the user"

assert_present \
  "the unadopted checkbox is recorded as a decision, not an omission" \
  "unadopted for this reason rather than unfinished"

# ---------------------------------------------------------------------------
# 5. cmux-delegate feeds the ledger
# ---------------------------------------------------------------------------

assert_lib_retarget "$DELEGATE"

assert_absent \
  "the delegate skill does not reach for todo either" \
  "cmux todo"

assert_present \
  "--run is an argument" \
  "\`--run\`"

assert_present \
  "the run id is stashed on disk, not carried in a shell variable" \
  "/tmp/cmux-delegate-{timestamp}.run"

assert_present \
  "binding happens where the UUID is known" \
  "UUID 를 아는 지점이 여기뿐입니다"

assert_order \
  "the run is resolved before the workspaces that bind to it" \
  "### Step 3.6: Resolve the Run" \
  "### Step 5a: Launch cmux Workspace"

assert_present \
  "crash and waiting-input both fold to blocked" \
  "waiting-input|crash) LEDGER_EVENT=block"

assert_present \
  "done is withheld where one report is shared by N workers" \
  "워커가 하나일 때만 기록합니다"

assert_lib_summary
