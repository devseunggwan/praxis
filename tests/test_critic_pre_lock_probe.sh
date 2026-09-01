#!/usr/bin/env bash
# tests/test_critic_pre_lock_probe.sh — Step 5g critic pre-lock probe gate
#
# Regression test for issue #346 (Phase 1):
#   codex-review-wrap SKILL.md Step 5g must require a live probe citation
#   whenever the critic emits a negative claim ("X is fabricated", "X does
#   not exist", etc.).  Memory-only retrieval has historically failed — the
#   gate language must be pinned in the SKILL.md itself.
#
# All assertions are static document checks against SKILL.md.  No hook
# binary is required — the test validates that the specification text is
# present and correct so that prompt drift surfaces in CI.
#
# Run:  bash tests/test_critic_pre_lock_probe.sh
# Exit: 0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL_DIR="$ROOT_DIR/skills/codex-review-wrap"

# The #1181 references/ split spread the pinned prose across the SKILL.md
# spine (execution order) and references/ (5g detail, error handling,
# limitations, worked example). The assertions pin content, not layout, so
# the target is the concatenation of the spine and every reference file.
SKILL="$(mktemp)" || { echo "FATAL: mktemp failed" >&2; exit 1; }
trap 'rm -f "$SKILL"' EXIT
cat "$SKILL_DIR/SKILL.md" "$SKILL_DIR/references/"*.md >"$SKILL"

# Reachability is a SEPARATE question from content. The buffer above is built
# by globbing the directory, so it holds every reference file whether or not
# the spine still links it — a deleted or misspelled link leaves each content
# assertion below passing while a real reader can no longer reach the prose.
# Check the links themselves: every reference file is linked from the spine,
# and every link the spine makes resolves to a file that exists.
_unreachable=""
for _ref in "$SKILL_DIR/references/"*.md; do
  _base="$(basename "$_ref")"
  grep -q "references/$_base" "$SKILL_DIR/SKILL.md" || _unreachable="$_unreachable $_base"
done
_dangling=""
for _target in $(grep -o "references/[A-Za-z0-9._-]*\.md" "$SKILL_DIR/SKILL.md" | sort -u); do
  [ -f "$SKILL_DIR/$_target" ] || _dangling="$_dangling $_target"
done
if [ -n "$_unreachable" ] || [ -n "$_dangling" ]; then
  echo "FAIL  references reachable from the SKILL.md spine"
  [ -n "$_unreachable" ] && echo "        never linked:$_unreachable"
  [ -n "$_dangling" ] && echo "        link with no file:$_dangling"
  exit 1
fi

# shellcheck source=./_assert_lib.sh
source "$SCRIPT_DIR/_assert_lib.sh"
assert_lib_init "$SKILL"

# ---------------------------------------------------------------------------
# 1. Execution order — 5g must appear in the numbered execution list
# ---------------------------------------------------------------------------

assert_present \
  "execution-order lists 5g" \
  "5g critic pre-lock probe check"

# ---------------------------------------------------------------------------
# 2. Negative-claim trigger forms — English variants
# ---------------------------------------------------------------------------

assert_present \
  "negative-claim: 'X is fabricated' English" \
  '"X is fabricated"'

assert_present \
  "negative-claim: 'X does not exist' English" \
  '"X does not exist"'

assert_present \
  "negative-claim: 'X is unused' English" \
  '"X is unused"'

assert_present \
  "negative-claim: 'X has no runtime effect' English" \
  '"X has no runtime effect"'

assert_present \
  "negative-claim: 'X is missing from {file/scope}' English" \
  '"X is missing from {file/scope}"'

# ---------------------------------------------------------------------------
# 3. Negative-claim trigger forms — Korean variants
# ---------------------------------------------------------------------------

assert_present \
  "negative-claim: Korean 존재하지 않음 variant" \
  "X 는 존재하지 않음"

assert_present \
  "negative-claim: Korean 없음 short form" \
  "X 는 없음"

assert_present \
  "negative-claim: Korean 사용되지 않음 variant" \
  "X 는 사용되지 않음"

assert_present \
  "negative-claim: Korean runtime effect 없음 variant" \
  "X 는 runtime effect 가 없음"

# ---------------------------------------------------------------------------
# 4. Probe citation format spec
# ---------------------------------------------------------------------------

assert_present \
  "probe citation format keyword 'Probe:'" \
  "Probe: <command>"

assert_present \
  "probe citation one-line output spec" \
  "one-line output"

assert_present \
  "probe re-run requirement (no cached knowledge)" \
  "re-run at the"

# ---------------------------------------------------------------------------
# 5. Cached-knowledge exemption is explicitly rejected
# ---------------------------------------------------------------------------

assert_present \
  "cached knowledge not a valid substitute" \
  '"I already read this file earlier"'

# ---------------------------------------------------------------------------
# 6. Critic prompt template block is present
# ---------------------------------------------------------------------------

assert_present \
  "critic prompt template header present" \
  "CRITIC PRE-LOCK PROBE GATE (mandatory)"

assert_present \
  "critic prompt template negative-claim list present" \
  '  - "X is fabricated"'

assert_present \
  "critic prompt template example probe present" \
  "Probe: grep -n PRAXIS_ASK_END_STRICT"

# ---------------------------------------------------------------------------
# 7. Worked example F2 (PRAXIS_ASK_END_STRICT fabrication claim) is present
# ---------------------------------------------------------------------------

assert_present \
  "worked example F2: PRAXIS_ASK_END_STRICT scenario described" \
  "PRAXIS_ASK_END_STRICT is a fabricated"

assert_present \
  "worked example F2: probe command shown" \
  "grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py"

# ---------------------------------------------------------------------------
# 8. Worked example F1 (git boolean-flag fix) is present
# ---------------------------------------------------------------------------

assert_present \
  "worked example F1: --literal-pathspecs / --super-prefix scenario described" \
  "--literal-pathspecs"

assert_present \
  "worked example F1: --super-prefix mentioned as taking a value" \
  "--super-prefix"

# ---------------------------------------------------------------------------
# 9. Error handling table covers 5g
# ---------------------------------------------------------------------------

assert_present \
  "error-handling: probe missing → halt finding" \
  "Halt the finding; prompt the critic to re-run with probe citation"

# ---------------------------------------------------------------------------
# 10. Regression — negative-claim WITHOUT probe citation must be flagged
#
#     This test checks that the SKILL.md spec explicitly states the gate
#     halts a claim that lacks a Probe: citation — simulating the scenario
#     where a critic outputs "X is fabricated" with no accompanying probe.
# ---------------------------------------------------------------------------

assert_present \
  "spec: negative claim without Probe: citation is halted" \
  "Critic negative claim emitted without"

# ---------------------------------------------------------------------------
# 11. Retraction protocol documented
# ---------------------------------------------------------------------------

assert_present \
  "retraction: 'Retracted:' keyword in spec" \
  "Retracted:"

assert_present \
  "retraction: probe disproves → retract before surfacing" \
  "retract the claim before surfacing"

# ---------------------------------------------------------------------------
# 12. Limitations section covers 5g scope
# ---------------------------------------------------------------------------

assert_present \
  "limitations: pattern-based detection caveat present" \
  "negative-claim detection is pattern-based"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

assert_lib_summary
