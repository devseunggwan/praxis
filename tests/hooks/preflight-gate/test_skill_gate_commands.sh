#!/usr/bin/env bash
# test_skill_gate_commands.sh — coverage for
#   hooks/preflight-gate/skill-gate-commands/impl.py
#
# Asserts:
#   block  → rc=2, stderr contains " blocked"
#   silent → rc=0, stderr empty
#
# Transcript fixtures are built as real jsonl temp files so the transcript
# scan is genuinely exercised (mirrors test_block_commit_without_codex_review.sh).
#
# Usage: bash tests/hooks/preflight-gate/test_skill_gate_commands.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/skill-gate-commands/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Transcript fixtures (real jsonl temp files)
# ---------------------------------------------------------------------------

# Transcript WITH the required skill invocation (praxis:create-hub-pr)
TX_WITH_CREATE_PR=$(mktemp)
printf '%s\n' \
  '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"praxis:create-hub-pr"}}]}}' \
  >"$TX_WITH_CREATE_PR"

# Transcript WITH a different skill (for "wrong skill" tests)
TX_WITH_WRONG_SKILL=$(mktemp)
printf '%s\n' \
  '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"laplace-dev-hub:code-review"}}]}}' \
  >"$TX_WITH_WRONG_SKILL"

# Transcript WITH a skill whose name contains a colon (org:skill-name)
TX_WITH_COLON_SKILL=$(mktemp)
printf '%s\n' \
  '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"laplace-dev-hub:create-hub-pr"}}]}}' \
  >"$TX_WITH_COLON_SKILL"

# Transcript WITHOUT any matching skill
TX_WITHOUT=$(mktemp)
printf '%s\n' \
  '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"no skill invocation here"}]}}' \
  >"$TX_WITHOUT"

# Transcript with garbage lines THEN a valid skill entry (resilience)
TX_GARBAGE_THEN_SKILL=$(mktemp)
printf '%s\n' \
  'not valid json at all' \
  '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"praxis:create-hub-pr"}}]}}' \
  >"$TX_GARBAGE_THEN_SKILL"

# Nonexistent path (fail-open)
NONEXISTENT_TX="/tmp/does-not-exist-praxis-438-$$.jsonl"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case <name> <block|silent> <payload_json> [KEY=VALUE ...]
#
# Remaining positional args after the third are passed as env KEY=VALUE pairs.
# Two special env vars are always unset to prevent pollution:
#   PRAXIS_SKILL_GATED_COMMANDS, PRAXIS_HOOK_BYPASS_SKILL_GATE
run_case() {
  local name="$1" expectation="$2" payload="$3"
  shift 3
  local env_args=("$@")

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)

  if [ "${#env_args[@]}" -gt 0 ]; then
    echo "$payload" | env \
      -u PRAXIS_SKILL_GATED_COMMANDS \
      -u PRAXIS_HOOK_BYPASS_SKILL_GATE \
      "${env_args[@]}" \
      python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | env \
      -u PRAXIS_SKILL_GATED_COMMANDS \
      -u PRAXIS_HOOK_BYPASS_SKILL_GATE \
      python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    block)
      [ "$rc" -eq 2 ] || ok=0
      # Standard block format (issue #439): header line ends with " blocked"
      echo "$err" | grep -q " blocked$" || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    *)
      echo "  internal: unknown expectation '$expectation'" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    PASS=$((PASS + 1))
    echo "  PASS  $name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    echo "  FAIL  $name (rc=$rc, expected=$expectation)"
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -c 400)"
    [ -z "$err" ] && [ "$expectation" = "block" ] && echo "        stderr: (empty)"
  fi
}

# Convenience: build a payload JSON string.
# mk_payload <tool_name> <command> [transcript_path]
mk_payload() {
  local tool="$1" cmd="$2" tx="${3:-}"
  python3 -c '
import json, sys
p = {"tool_name": sys.argv[1], "tool_input": {"command": sys.argv[2]}}
if len(sys.argv) > 3 and sys.argv[3]:
    p["transcript_path"] = sys.argv[3]
print(json.dumps(p))
' "$tool" "$cmd" "$tx"
}

# Simple config for most tests: gh pr create => praxis:create-hub-pr
CFG_PR_CREATE="gh pr create=>praxis:create-hub-pr"
CFG_PR_MERGE="gh pr merge=>praxis:codex-review-wrap"
CFG_PUSH="git push origin=>praxis:codex-review-wrap"

echo "test_skill_gate_commands"

# ---------------------------------------------------------------------------
# 1. No config (default) → PASS regardless of command
# ---------------------------------------------------------------------------

run_case "no config, gh pr create (NO-OP, silent)" \
  "silent" \
  "$(mk_payload Bash 'gh pr create --title "feat: x"' "$TX_WITHOUT")"

# ---------------------------------------------------------------------------
# 2. gh pr create — BLOCK (no skill invocation in transcript)
# ---------------------------------------------------------------------------

run_case "gh pr create, no skill in transcript (block)" \
  "block" \
  "$(mk_payload Bash 'gh pr create --title "feat: x"' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

# ---------------------------------------------------------------------------
# 3. gh pr create — PASS (required skill IS in transcript)
# ---------------------------------------------------------------------------

run_case "gh pr create, required skill found (silent)" \
  "silent" \
  "$(mk_payload Bash 'gh pr create --title "feat: x"' "$TX_WITH_CREATE_PR")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

# ---------------------------------------------------------------------------
# 4. gh pr merge — BLOCK / PASS
# ---------------------------------------------------------------------------

run_case "gh pr merge, no skill in transcript (block)" \
  "block" \
  "$(mk_payload Bash 'gh pr merge 42 --squash --delete-branch' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_MERGE"

# Transcript with the required skill for merge gate
TX_WITH_MERGE_SKILL=$(mktemp)
printf '%s\n' \
  '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"praxis:codex-review-wrap"}}]}}' \
  >"$TX_WITH_MERGE_SKILL"

run_case "gh pr merge, required skill found (silent)" \
  "silent" \
  "$(mk_payload Bash 'gh pr merge 42 --squash --delete-branch' "$TX_WITH_MERGE_SKILL")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_MERGE"

# ---------------------------------------------------------------------------
# 5. git push origin — BLOCK / PASS
# ---------------------------------------------------------------------------

run_case "git push origin, no skill in transcript (block)" \
  "block" \
  "$(mk_payload Bash 'git push origin main' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PUSH"

TX_WITH_PUSH_SKILL=$(mktemp)
printf '%s\n' \
  '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"praxis:codex-review-wrap"}}]}}' \
  >"$TX_WITH_PUSH_SKILL"

run_case "git push origin, required skill found (silent)" \
  "silent" \
  "$(mk_payload Bash 'git push origin main' "$TX_WITH_PUSH_SKILL")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PUSH"

# ---------------------------------------------------------------------------
# 6. Non-configured command → PASS (not in config)
# ---------------------------------------------------------------------------

run_case "gh issue list not in config (silent)" \
  "silent" \
  "$(mk_payload Bash 'gh issue list --limit 20' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

run_case "git commit not in config (silent)" \
  "silent" \
  "$(mk_payload Bash 'git commit -m "feat: x"' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

# ---------------------------------------------------------------------------
# 7. Bypass env var → PASS
# ---------------------------------------------------------------------------

run_case "bypass env set, gh pr create still blocked without it would block (silent)" \
  "silent" \
  "$(mk_payload Bash 'gh pr create --title "feat: x"' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE" \
  "PRAXIS_HOOK_BYPASS_SKILL_GATE=1"

# ---------------------------------------------------------------------------
# 8. Global-flag ordering: gh -R owner/repo pr create → still matched
# ---------------------------------------------------------------------------

run_case "gh -R owner/repo pr create (global flag, block)" \
  "block" \
  "$(mk_payload Bash 'gh -R owner/my-repo pr create --title "feat: x"' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

run_case "gh --repo owner/my-repo pr create (long global flag, block)" \
  "block" \
  "$(mk_payload Bash 'gh --repo owner/my-repo pr create --title "feat: x"' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

# ---------------------------------------------------------------------------
# 9. Required skill name containing a colon → parsed correctly
# ---------------------------------------------------------------------------

CFG_COLON_SKILL="gh pr create=>laplace-dev-hub:create-hub-pr"

run_case "skill with colon in name, not invoked (block)" \
  "block" \
  "$(mk_payload Bash 'gh pr create --title "feat: x"' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_COLON_SKILL"

run_case "skill with colon in name, invoked (silent)" \
  "silent" \
  "$(mk_payload Bash 'gh pr create --title "feat: x"' "$TX_WITH_COLON_SKILL")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_COLON_SKILL"

# ---------------------------------------------------------------------------
# 10. Missing transcript_path → PASS (fail-open)
# ---------------------------------------------------------------------------

run_case "missing transcript_path (fail-open, silent)" \
  "silent" \
  "$(mk_payload Bash 'gh pr create --title "feat: x"')" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

# ---------------------------------------------------------------------------
# 11. Unreadable / nonexistent transcript → PASS (fail-open)
# ---------------------------------------------------------------------------

run_case "nonexistent transcript (fail-open, silent)" \
  "silent" \
  "$(mk_payload Bash 'gh pr create --title "feat: x"' "$NONEXISTENT_TX")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

# ---------------------------------------------------------------------------
# 12. Malformed JSON stdin → PASS (fail-open)
# ---------------------------------------------------------------------------

run_case "malformed JSON stdin (fail-open, silent)" \
  "silent" \
  "not-valid-json" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

# ---------------------------------------------------------------------------
# 13. Non-Bash tool → PASS
# ---------------------------------------------------------------------------

run_case "non-Bash tool (Edit, silent)" \
  "silent" \
  '{"tool_name":"Edit","tool_input":{"file_path":"/tmp/x"}}' \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

# ---------------------------------------------------------------------------
# 14. Transcript with garbage lines preceding valid skill → PASS (resilience)
# ---------------------------------------------------------------------------

run_case "garbage lines before skill entry in transcript (silent)" \
  "silent" \
  "$(mk_payload Bash 'gh pr create --title "feat: x"' "$TX_GARBAGE_THEN_SKILL")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

# ---------------------------------------------------------------------------
# 15. Wrong skill in transcript → still BLOCK
# ---------------------------------------------------------------------------

run_case "wrong skill in transcript (block)" \
  "block" \
  "$(mk_payload Bash 'gh pr create --title "feat: x"' "$TX_WITH_WRONG_SKILL")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

# ---------------------------------------------------------------------------
# 16. Multiple entries in config — second pattern also works
# ---------------------------------------------------------------------------

CFG_MULTI="gh pr create=>praxis:create-hub-pr,gh pr merge=>praxis:codex-review-wrap"

run_case "multi-config: gh pr create blocked (block)" \
  "block" \
  "$(mk_payload Bash 'gh pr create --title "feat: x"' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_MULTI"

run_case "multi-config: gh pr merge blocked (block)" \
  "block" \
  "$(mk_payload Bash 'gh pr merge 42 --squash' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_MULTI"

run_case "multi-config: gh issue list not in config (silent)" \
  "silent" \
  "$(mk_payload Bash 'gh issue list' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_MULTI"

# ---------------------------------------------------------------------------
# 17. git push origin with push flags (e.g. --force-with-lease)
# ---------------------------------------------------------------------------

run_case "git push --force-with-lease origin (block)" \
  "block" \
  "$(mk_payload Bash 'git push --force-with-lease origin main' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PUSH"

run_case "git push -u origin (block)" \
  "block" \
  "$(mk_payload Bash 'git push -u origin feature-branch' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PUSH"

# git push to a non-origin remote → should NOT match "git push origin" pattern
run_case "git push upstream (not origin, silent)" \
  "silent" \
  "$(mk_payload Bash 'git push upstream main' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PUSH"

# ---------------------------------------------------------------------------
# 18. Malformed config entry (no =>) → fail-safe pass
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 18b. Compound command with two gated patterns and only first skill satisfied
# (P2-3): gh pr create && gh pr merge — second gate must still be checked.
# ---------------------------------------------------------------------------

CFG_MULTI_DIFF="gh pr create=>praxis:create-hub-pr,gh pr merge=>praxis:codex-review-wrap"

# Transcript has create-hub-pr but NOT codex-review-wrap
TX_WITH_ONLY_CREATE=$(mktemp)
printf '%s\n' \
  '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"praxis:create-hub-pr"}}]}}' \
  >"$TX_WITH_ONLY_CREATE"

run_case "compound cmd: first skill satisfied, second not (block on second)" \
  "block" \
  "$(mk_payload Bash 'gh pr create --title x && gh pr merge 42 --squash' "$TX_WITH_ONLY_CREATE")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_MULTI_DIFF"

# Transcript has both skills
TX_WITH_BOTH=$(mktemp)
printf '%s\n' \
  '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"praxis:create-hub-pr"}}]}}' \
  '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"praxis:codex-review-wrap"}}]}}' \
  >"$TX_WITH_BOTH"

run_case "compound cmd: both skills satisfied (silent)" \
  "silent" \
  "$(mk_payload Bash 'gh pr create --title x && gh pr merge 42 --squash' "$TX_WITH_BOTH")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_MULTI_DIFF"

# ---------------------------------------------------------------------------
# 19. Semicolon attached to command token (P2-1): gh pr create; echo done
# ---------------------------------------------------------------------------

run_case "gh pr create; echo done (no space before ;, block)" \
  "block" \
  "$(mk_payload Bash 'gh pr create; echo done' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

run_case "echo x; gh pr create (prefix cmd, block)" \
  "block" \
  "$(mk_payload Bash 'echo x; gh pr create --title x' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PR_CREATE"

# ---------------------------------------------------------------------------
# 20. git push value-consuming flags (-o/--push-option) before origin (P2-2)
# ---------------------------------------------------------------------------

run_case "git push -o ci.skip origin (value flag, block)" \
  "block" \
  "$(mk_payload Bash 'git push -o ci.skip origin main' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PUSH"

run_case "git push --push-option ci.skip origin (long value flag, block)" \
  "block" \
  "$(mk_payload Bash 'git push --push-option ci.skip origin main' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PUSH"

run_case "git push --push-option=ci.skip origin (= form, block)" \
  "block" \
  "$(mk_payload Bash 'git push --push-option=ci.skip origin main' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=$CFG_PUSH"

run_case "malformed config entry no => (fail-safe, silent)" \
  "silent" \
  "$(mk_payload Bash 'gh pr create --title "feat: x"' "$TX_WITHOUT")" \
  "PRAXIS_SKILL_GATED_COMMANDS=gh pr create:praxis:create-hub-pr"

# ---------------------------------------------------------------------------
# Cleanup + summary
# ---------------------------------------------------------------------------

rm -f "$TX_WITH_CREATE_PR" "$TX_WITH_WRONG_SKILL" "$TX_WITH_COLON_SKILL" \
      "$TX_WITHOUT" "$TX_GARBAGE_THEN_SKILL" \
      "$TX_WITH_MERGE_SKILL" "$TX_WITH_PUSH_SKILL" \
      "$TX_WITH_ONLY_CREATE" "$TX_WITH_BOTH"

echo
TOTAL=$((PASS + FAIL))
echo "Result: $PASS/$TOTAL passed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
