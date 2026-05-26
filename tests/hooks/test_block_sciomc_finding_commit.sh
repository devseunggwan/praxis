#!/usr/bin/env bash
# test_block_sciomc_finding_commit.sh — coverage for the sciomc finding gate
#
# Synthesizes Claude Code PreToolUse(Bash) payloads (with a transcript fixture)
# and asserts:
#   block → exit 2 + stderr non-empty
#   pass  → exit 0 + stderr empty
#
# This suite covers the #427 command-classification layer (shlex token parsing,
# --allow-empty no longer exempt). The gate fires only when a finding marker is
# present AND no consensus re-fetch follows it, so the command-classification
# cases use a transcript that already satisfies those finding conditions.
#
# Usage: bash tests/hooks/test_block_sciomc_finding_commit.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOK="$ROOT_DIR/hooks/block-sciomc-finding-commit.sh"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

# Transcript fixtures -------------------------------------------------------
# A finding marker present, no consensus re-fetch → gate conditions (b)+(c) hold.
TX_FINDING=$(mktemp)
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"[FINDING:f1] the user value looks sibling-deviant"}]}}' >"$TX_FINDING"

# Finding present, but a consensus re-fetch follows → gate must NOT fire.
TX_FINDING_THEN_REFETCH=$(mktemp)
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"[FINDING:f1] the user value looks off"}]}}' >"$TX_FINDING_THEN_REFETCH"
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"running gh pr view 12 --json body --jq .body to re-confirm"}]}}' >>"$TX_FINDING_THEN_REFETCH"

# No finding marker at all → gate must NOT fire regardless of command.
TX_NO_FINDING=$(mktemp)
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"just an ordinary edit, nothing analytical here"}]}}' >"$TX_NO_FINDING"

NONEXISTENT_TX="/tmp/does-not-exist-praxis-427-$$.jsonl"

PASS=0; FAIL=0; FAILED_NAMES=()

# run_case <name> <block|pass> <tool_name> <command> [transcript_path|"NONE"] [env]
run_case() {
  local name="$1" expected="$2" tool_name="$3" command="$4" tx="${5:-NONE}" env_kv="${6:-}"
  local payload err_file rc err_content
  payload=$(python3 -c '
import json, sys
p = {"tool_name": sys.argv[1], "tool_input": {"command": sys.argv[2]}}
if sys.argv[3] != "NONE":
    p["transcript_path"] = sys.argv[3]
print(json.dumps(p))' "$tool_name" "$command" "$tx")
  err_file=$(mktemp)
  if [ -n "$env_kv" ]; then
    echo "$payload" | env "$env_kv" "$HOOK" >/dev/null 2>"$err_file"
  else
    echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  fi
  rc=$?
  err_content=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  if [ "$expected" = "block" ]; then
    [ "$rc" -eq 2 ] && [ -n "$err_content" ] || ok=0
  else
    [ "$rc" -eq 0 ] && [ -z "$err_content" ] || ok=0
  fi

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; ((PASS++))
  else
    echo "FAIL [$expected→rc=$rc,stderr=$([ -n "$err_content" ] && echo non-empty || echo empty)] $name"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# BLOCK — content commit, finding present, no re-fetch
# ---------------------------------------------------------------------------

run_case "content commit blocks" block Bash \
  'git commit -m "feat: x"' "$TX_FINDING"

run_case "commit -F file blocks" block Bash \
  'git commit -F /tmp/msg.txt' "$TX_FINDING"

# --allow-empty / --allow-empty-message no longer exempt (#427): staged content
# can ride along, so a content commit using them is still gated.
run_case "allow-empty not exempt blocks" block Bash \
  'git commit --allow-empty -m "ci: trigger"' "$TX_FINDING"

run_case "allow-empty-message not exempt blocks" block Bash \
  'git commit --allow-empty-message -m ""' "$TX_FINDING"

# --amend inside the message is part of the message value, not the flag.
run_case "amend word inside message still blocks" block Bash \
  'git commit -m "docs: explain the --amend flag"' "$TX_FINDING"

# ratification token OUTSIDE the commit message must not bypass.
run_case "ratification token outside message blocks" block Bash \
  "git commit -m \"feat: x\"; echo '[user-approved]'" "$TX_FINDING"

run_case "commit after && still gated" block Bash \
  'cd /repo && git commit -m "feat: y"' "$TX_FINDING"

# Compound bypass (Codex #427 round-1 P2-A): a second content commit must not
# hide behind an earlier exempt --amend.
run_case "amend then content commit — second still gated" block Bash \
  'git commit --amend --no-edit && git commit -m "feat: x"' "$TX_FINDING"

# Compound bypass (Codex #427 round-1 P2-B): a later command's -m value glued
# after `;` must not be read as this commit's message.
run_case "trailing echo -m token does not ratify" block Bash \
  'git commit -m "feat: x"; echo -m "[user-approved]"' "$TX_FINDING"

# ---------------------------------------------------------------------------
# PASS — exemptions (non-content subcommands / --amend)
# ---------------------------------------------------------------------------

run_case "amend exempt" pass Bash \
  'git commit --amend --no-edit' "$TX_FINDING"

run_case "git merge exempt" pass Bash \
  'git merge --no-ff feature' "$TX_FINDING"

run_case "git rebase exempt" pass Bash \
  'git rebase origin/main' "$TX_FINDING"

run_case "git revert exempt" pass Bash \
  'git revert abc123' "$TX_FINDING"

run_case "git cherry-pick exempt" pass Bash \
  'git cherry-pick abc123' "$TX_FINDING"

# ---------------------------------------------------------------------------
# PASS — ratification token in the commit message (escape hatch)
# ---------------------------------------------------------------------------

run_case "user-approved token in -m" pass Bash \
  'git commit -m "feat: x [user-approved]"' "$TX_FINDING"

run_case "ratified-by-user token in -m" pass Bash \
  'git commit -m "feat: x [ratified-by-user]"' "$TX_FINDING"

run_case "joined -m ratification token" pass Bash \
  'git commit -m"feat: x [user-approved]"' "$TX_FINDING"

run_case "message= ratification token" pass Bash \
  'git commit --message="feat: x [user-ratified]"' "$TX_FINDING"

run_case "env bypass" pass Bash \
  'git commit -m "x"' "$TX_FINDING" "CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1"

# Compound where every invocation is exempt or ratified → no gated commit.
run_case "amend then ratified commit passes" pass Bash \
  'git commit --amend --no-edit && git commit -m "feat: x [user-approved]"' "$TX_FINDING"

# ---------------------------------------------------------------------------
# PASS — token-level classification edge cases
# ---------------------------------------------------------------------------

run_case "git commit-tree plumbing exempt" pass Bash \
  'git commit-tree abc123 -m "tree"' "$TX_FINDING"

run_case "echo git commit string not a commit" pass Bash \
  'echo "remember to git commit later"' "$TX_FINDING"

run_case "git log grep git commit not a commit" pass Bash \
  'git log --grep="git commit" --oneline' "$TX_FINDING"

# ---------------------------------------------------------------------------
# PASS — out of scope
# ---------------------------------------------------------------------------

run_case "non-Bash tool" pass Edit \
  'git commit -m "x"' "$TX_FINDING"

run_case "git push not commit" pass Bash \
  'git push origin main' "$TX_FINDING"

run_case "git status not commit" pass Bash \
  'git status' "$TX_FINDING"

# ---------------------------------------------------------------------------
# PASS — finding-context gating (unchanged Layer-2 behavior, sanity)
# ---------------------------------------------------------------------------

run_case "no finding marker → content commit passes" pass Bash \
  'git commit -m "feat: x"' "$TX_NO_FINDING"

run_case "consensus re-fetch after finding → passes" pass Bash \
  'git commit -m "feat: x"' "$TX_FINDING_THEN_REFETCH"

# ---------------------------------------------------------------------------
# PASS — fail-open
# ---------------------------------------------------------------------------

run_case "unparseable command fail-open" pass Bash \
  'git commit -m "unterminated' "$TX_FINDING"

run_case "no transcript_path fail-open" pass Bash \
  'git commit -m "x"' "NONE"

run_case "nonexistent transcript path fail-open" pass Bash \
  'git commit -m "x"' "$NONEXISTENT_TX"

# Malformed stdin → fail-open (exit 0, no stderr)
_malformed_err=$(mktemp)
printf 'not json' | "$HOOK" >/dev/null 2>"$_malformed_err"; _mrc=$?
if [ "$_mrc" -eq 0 ] && [ ! -s "$_malformed_err" ]; then
  echo "PASS [pass] malformed stdin fail-open"; ((PASS++))
else
  echo "FAIL [pass→rc=$_mrc] malformed stdin fail-open"; ((FAIL++)); FAILED_NAMES+=("malformed stdin fail-open")
fi
rm -f "$_malformed_err"

# ---------------------------------------------------------------------------
# Cleanup + summary
# ---------------------------------------------------------------------------

rm -f "$TX_FINDING" "$TX_FINDING_THEN_REFETCH" "$TX_NO_FINDING"

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
