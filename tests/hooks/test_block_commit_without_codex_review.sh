#!/usr/bin/env bash
# test_block_commit_without_codex_review.sh — coverage for the codex-review commit gate
#
# Synthesizes Claude Code PreToolUse(Bash) payloads (with a transcript fixture)
# and asserts:
#   block → exit 2 + stderr non-empty
#   pass  → exit 0 + stderr empty
#
# Usage: bash tests/hooks/test_block_commit_without_codex_review.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOK="$ROOT_DIR/hooks/block-commit-without-codex-review.sh"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

# Transcript fixtures -------------------------------------------------------
TX_WITH_SKILL=$(mktemp)
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"reviewing"},{"type":"tool_use","name":"Skill","input":{"skill":"praxis:codex-review-wrap"}}]}}' >"$TX_WITH_SKILL"

TX_WITH_SLASH=$(mktemp)
printf '%s\n' '{"type":"user","message":{"role":"user","content":"/praxis:codex-review-wrap"}}' >"$TX_WITH_SLASH"

TX_WITHOUT=$(mktemp)
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"no review here"}]}}' >"$TX_WITHOUT"
printf '%s\n' '{"type":"user","message":{"role":"user","content":"should I run /praxis:codex-review-wrap? (prose, not invocation)"}}' >>"$TX_WITHOUT"

TX_WRONG_SKILL=$(mktemp)
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"laplace-dev-hub:code-review"}}]}}' >"$TX_WRONG_SKILL"

TX_GARBAGE_PLUS_SKILL=$(mktemp)
printf '%s\n' 'not valid json at all' >"$TX_GARBAGE_PLUS_SKILL"
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"praxis:codex-review-wrap"}}]}}' >>"$TX_GARBAGE_PLUS_SKILL"

NONEXISTENT_TX="/tmp/does-not-exist-praxis-425-$$.jsonl"

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
# BLOCK cases — content commit, no codex-review-wrap invocation in transcript
# ---------------------------------------------------------------------------

run_case "commit, transcript without review" block Bash \
  'git commit -m "feat: x"' "$TX_WITHOUT"

run_case "commit, wrong skill only" block Bash \
  'git commit -m "feat: x"' "$TX_WRONG_SKILL"

run_case "commit -F file, no review" block Bash \
  'git commit -F /tmp/msg.txt' "$TX_WITHOUT"

run_case "commit, slash-command is prose only" block Bash \
  'git commit -m "fix: y"' "$TX_WITHOUT"

# ---------------------------------------------------------------------------
# PASS cases — codex-review-wrap invoked
# ---------------------------------------------------------------------------

run_case "Skill tool_use present" pass Bash \
  'git commit -m "feat: x"' "$TX_WITH_SKILL"

run_case "slash-command invocation present" pass Bash \
  'git commit -m "feat: x"' "$TX_WITH_SLASH"

run_case "garbage lines + valid skill line" pass Bash \
  'git commit -m "feat: x"' "$TX_GARBAGE_PLUS_SKILL"

# ---------------------------------------------------------------------------
# PASS cases — exemptions / escape hatches
# ---------------------------------------------------------------------------

run_case "amend exempt" pass Bash \
  'git commit --amend --no-edit' "$TX_WITHOUT"

run_case "allow-empty exempt" pass Bash \
  'git commit --allow-empty -m "ci: trigger"' "$TX_WITHOUT"

run_case "git merge exempt" pass Bash \
  'git merge --no-ff feature' "$TX_WITHOUT"

run_case "git revert exempt" pass Bash \
  'git revert abc123' "$TX_WITHOUT"

run_case "git rebase exempt" pass Bash \
  'git rebase origin/main' "$TX_WITHOUT"

run_case "git cherry-pick exempt" pass Bash \
  'git cherry-pick abc123' "$TX_WITHOUT"

run_case "skip token in -m" pass Bash \
  'git commit -m "docs: typo [skip-codex-review]"' "$TX_WITHOUT"

run_case "skip token case-insensitive" pass Bash \
  'git commit -m "docs: typo [SKIP-CODEX-REVIEW]"' "$TX_WITHOUT"

run_case "env bypass" pass Bash \
  'git commit -m "x"' "$TX_WITHOUT" "CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1"

# ---------------------------------------------------------------------------
# PASS cases — out of scope
# ---------------------------------------------------------------------------

run_case "non-Bash tool" pass Edit \
  'git commit -m "x"' "$TX_WITHOUT"

run_case "git push not commit" pass Bash \
  'git push origin main' "$TX_WITHOUT"

run_case "git status not commit" pass Bash \
  'git status' "$TX_WITHOUT"

# ---------------------------------------------------------------------------
# Token-level classification edge cases (Codex P1/P2 + reviewer HIGH)
# ---------------------------------------------------------------------------

# commit-tree plumbing must NOT match `commit` (reviewer HIGH)
run_case "git commit-tree plumbing exempt" pass Bash \
  'git commit-tree abc123 -m "tree"' "$TX_WITHOUT"

# `git commit` as a substring inside a quoted arg must NOT trip the gate (P1 FP)
run_case "echo git commit string not a commit" pass Bash \
  'echo "remember to git commit later"' "$TX_WITHOUT"

run_case "git log grep git commit not a commit" pass Bash \
  'git log --grep="git commit" --oneline' "$TX_WITHOUT"

# `--amend` inside the -m message is NOT the amend flag → must still BLOCK (P1 bypass)
run_case "amend word inside message still blocks" block Bash \
  'git commit -m "docs: explain the --amend flag"' "$TX_WITHOUT"

# skip token OUTSIDE the commit message must NOT bypass (P2)
run_case "skip token outside message via semicolon blocks" block Bash \
  "git commit -m \"feat: x\"; echo '[skip-codex-review]'" "$TX_WITHOUT"

run_case "skip token outside message via && blocks" block Bash \
  'git commit -m "feat: x" && echo "[skip-codex-review]"' "$TX_WITHOUT"

# real content commit after a shell separator still gated
run_case "commit after && still gated" block Bash \
  'cd /repo && git commit -m "feat: y"' "$TX_WITHOUT"

# joined -m form carries the skip token correctly
run_case "joined -m skip token" pass Bash \
  'git commit -m"docs: typo [skip-codex-review]"' "$TX_WITHOUT"

# --message= form carries the skip token correctly
run_case "message= form skip token" pass Bash \
  'git commit --message="docs [skip-codex-review]"' "$TX_WITHOUT"

# unbalanced quotes → unparseable → fail-open
run_case "unparseable command fail-open" pass Bash \
  'git commit -m "unterminated' "$TX_WITHOUT"

# ---------------------------------------------------------------------------
# Fail-open cases
# ---------------------------------------------------------------------------

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

rm -f "$TX_WITH_SKILL" "$TX_WITH_SLASH" "$TX_WITHOUT" "$TX_WRONG_SKILL" "$TX_GARBAGE_PLUS_SKILL"

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
