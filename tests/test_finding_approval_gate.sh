#!/usr/bin/env bash
# tests/test_finding_approval_gate.sh — Step 5i per-finding user approval gate
#
# Regression test for issue #861:
#   codex-review-wrap SKILL.md Step 5i must require an explicit per-finding
#   AskUserQuestion answer before any edit is applied.  The gate is prose-only
#   (no hook binary), so the spec text is the enforcement surface — pin it here
#   so drift surfaces in CI instead of during a review round.
#
# All assertions are static document checks against SKILL.md.
#
# Run:  bash tests/test_finding_approval_gate.sh
# Exit: 0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL="$ROOT_DIR/skills/codex-review-wrap/SKILL.md"

# shellcheck source=./_assert_lib.sh
source "$SCRIPT_DIR/_assert_lib.sh"
assert_lib_init "$SKILL"

# ---------------------------------------------------------------------------
# 1. Section exists and is reachable from the execution-order list
# ---------------------------------------------------------------------------

assert_present \
  "5i section heading present" \
  "#### 5i. Per-finding user approval gate"

assert_present \
  "execution-order lists 5i" \
  "5i per-finding user approval gate"

# ---------------------------------------------------------------------------
# 2. No-auto-apply directive — the core guarantee of the gate
# ---------------------------------------------------------------------------

assert_present \
  "no finding edited on agent judgement alone" \
  "No finding may be edited on the agent's judgement alone"

assert_present \
  "explicitly denies an auto-apply path" \
  "There is no auto-apply path"

# ---------------------------------------------------------------------------
# 3. Scope — every finding class is gated
# ---------------------------------------------------------------------------

assert_present \
  "scope covers every finding surviving 5b/5c" \
  "every finding of the round that survives 5b and 5c"

assert_present \
  "evidence-rejected findings are not offered as 적용" \
  "never a decision worth surfacing"

assert_present \
  "evidence-rejected findings get a one-line report" \
  "N findings rejected on evidence"

assert_present \
  "scope names all three 5a classes" \
  "fact-modifying, structural,"

assert_present \
  "5h synthesized findings re-enter the gate" \
  "Synthesized findings re-enter 5g and 5i"

# ---------------------------------------------------------------------------
# 4. Batching rule — 4 findings per AskUserQuestion call
# ---------------------------------------------------------------------------

assert_present \
  "batch size is 4 findings per call" \
  "**4 findings per call**"

assert_present \
  "runtime cap cited" \
  "accepts at most **4 questions per"

assert_present \
  "one question per finding (no compression)" \
  "Do not compress two findings into"

assert_present \
  "batch size follows the running host's own cap" \
  "the cap of the host"

assert_present \
  "host without a callable ask-user tool takes the non-interactive path" \
  "no callable ask-user tool at all"

assert_present \
  "question text carries a stable finding ID" \
  "must start with a stable finding ID"

assert_present \
  "interactivity check runs before the 5d-i question" \
  "**Interactivity check**"

assert_present \
  "background runs re-enter Step 5 rather than skipping it" \
  "Backgrounding defers Step 5, it does not skip it"

assert_present \
  "background handoff branches on whether a user is reachable" \
  "User is back in an interactive foreground turn"

assert_present \
  "Other approval paraphrase is re-asked, not accepted" \
  "apply only on a literal \`적용\`"

assert_present \
  "resolved flips re-enter 5i for their own 적용 answer" \
  "re-enters 5i as a normal finding"

assert_present \
  "5h runs after approved findings, not after all findings" \
  "after every approved finding has been"

# ---------------------------------------------------------------------------
# 5. Options — 적용 / 미적용 / 후속이슈
# ---------------------------------------------------------------------------

assert_present "option 적용 present"    "\`적용\`"
assert_present "option 미적용 present"  "\`미적용\`"
assert_present "option 후속이슈 present" "\`후속이슈\`"

assert_present \
  "Other free-text slot handled" \
  "\`Other\` (free text)"

assert_present \
  "Other proposing a different edit re-enters 5a/5b/5c" \
  "re-run 5a classification, 5b premise verification, and the 5c flip scan"

assert_present \
  "Other is never itself an 적용 answer for a modified edit" \
  "never itself an \`적용\` answer"

# ---------------------------------------------------------------------------
# 6. Question body carries evidence
# ---------------------------------------------------------------------------

assert_present \
  "question body requires Probe: line" \
  "Probe: {command} → {one-line output}"

assert_present \
  "non-fact-modifying findings still carry a Probe: line" \
  "Probe: n/a (structural)"

assert_present \
  "question body requires flip scan result" \
  "flip: none"

assert_present \
  "Recommended label requires a column-0 Falsified: line" \
  "the literal prefix \`Falsified:\`"

assert_present \
  "Falsified: contract cites the enforcing hook" \
  "hooks/advisory-nudge/output-block-falsify-advisory/impl.py"

assert_present \
  "5g runs before 5i in the execution order" \
  "Runs **before** 5i"

assert_present \
  "적용 writes the Premise-Verified trailer only for fact-modifying edits" \
  "trailer **if the edit is fact-modifying**"

# ---------------------------------------------------------------------------
# 7. Ledger integration — five record shapes including deferred
# ---------------------------------------------------------------------------

assert_present \
  "ledger declares five record shapes" \
  "**five record shapes**"

assert_present_re \
  "deferred: record shape defined" \
  "^deferred: +\{file\}"

assert_present \
  "deferred: excluded from flip scan prefixes" \
  "or \`deferred:\`) in the ledger"

assert_present \
  "user declines write a rejected: row with a user: prefix" \
  "reason: user: declined (round N)"

assert_present \
  "5c branches user decisions away from evidence conflicts" \
  "Re-proposal of a user-declined finding"

assert_present \
  "Probe: n/a barred when the finding carries a 5g negative claim" \
  "carries no 5g negative claim"

assert_present \
  "Overview summary scoped to 5b/5c survivors" \
  "that survived 5b and 5c"

assert_present \
  "5h synthesized edits take no Premise-Verified trailer" \
  "5e is the single rule for trailer scope"

# ---------------------------------------------------------------------------
# 8. Follow-up issue handling and approval-scope boundaries
# ---------------------------------------------------------------------------

assert_present \
  "follow-up issues batched behind an approach review" \
  "Do **not** create an issue per answer"

assert_present \
  "적용 does not transfer to sibling PR" \
  "on the current PR only"

# ---------------------------------------------------------------------------
# 9. Error handling + limitations coverage
# ---------------------------------------------------------------------------

assert_present \
  "error-handling: cancellation row" \
  "Stop applying for the round"

assert_present \
  "error-handling: non-interactive run row defers only 5b/5c survivors" \
  "record every finding that survived 5b/5c as"

assert_present \
  "worked example: structural question carries a Falsified: line" \
  "Falsified: '호출부가 이미 run_query 를 쓴다'"

assert_present \
  "limitations: interactive-session requirement" \
  "Step 5i requires an interactive session"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

assert_lib_summary
