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
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/block-commit-without-codex-review/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

# Transcript fixtures -------------------------------------------------------
TX_WITH_SKILL=$(mktemp)
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"reviewing"},{"type":"tool_use","name":"Skill","input":{"skill":"praxis:codex-review-wrap"}}]}}' >"$TX_WITH_SKILL"

TX_WITH_SLASH=$(mktemp)
printf '%s\n' '{"type":"user","message":{"role":"user","content":"/praxis:codex-review-wrap"}}' >"$TX_WITH_SLASH"

# Assistant message that merely prints the slash command on a line — a
# suggestion, not an invocation. Must NOT satisfy the gate (Codex round-2 P2).
TX_ASSISTANT_SLASH=$(mktemp)
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"/praxis:codex-review-wrap"}]}}' >"$TX_ASSISTANT_SLASH"

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

run_case "assistant-suggested slash does NOT satisfy gate" block Bash \
  'git commit -m "feat: x"' "$TX_ASSISTANT_SLASH"

run_case "garbage lines + valid skill line" pass Bash \
  'git commit -m "feat: x"' "$TX_GARBAGE_PLUS_SKILL"

# ---------------------------------------------------------------------------
# PASS cases — exemptions / escape hatches
# ---------------------------------------------------------------------------

run_case "amend exempt" pass Bash \
  'git commit --amend --no-edit' "$TX_WITHOUT"

# --allow-empty / --allow-empty-message are NOT exempt: they permit an empty
# commit/message but do not prevent staged content from riding along, so a
# content commit must still be gated (Codex round-2 P1).
run_case "allow-empty not exempt (staged content can ride along)" block Bash \
  'git commit --allow-empty -m "ci: trigger"' "$TX_WITHOUT"

run_case "allow-empty-message not exempt, content commit blocks" block Bash \
  'git commit --allow-empty-message -m ""' "$TX_WITHOUT"

# An intentional empty CI-trigger commit uses the skip token instead.
run_case "allow-empty empty commit passes via skip token" pass Bash \
  'git commit --allow-empty -m "ci: trigger [skip-codex-review]"' "$TX_WITHOUT"

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
# Hardened parser cases — bypass forms blocked after PR #445 port
# (mirror of test_block_sciomc_finding_commit.sh hardened-parser section)
# ---------------------------------------------------------------------------

# grouped: `(git commit …)` — shlex.shlex punctuation_chars splits `(` into its
# own token; `_is_git_binary` then matches the bare `git` token.
run_case "grouped (git commit -m x) is detected" block Bash \
  '(git commit -m "feat: grouped")' "$TX_WITHOUT"

# unquoted command-substitution: `echo $(git commit …)` — punctuation_chars
# splits `$`, `(`, `git` into separate tokens; `git` matches directly.
run_case "unquoted command-substitution \$(git commit …) is detected" block Bash \
  'echo $(git commit -m "feat: subst")' "$TX_WITHOUT"

# space-less separator: `true;git commit …` — punctuation_chars splits `;` into
# its own token so `git` becomes its own token after the separator.
run_case "no-space semicolon separator true;git commit is detected" block Bash \
  'true;git commit -m "feat: chained"' "$TX_WITHOUT"

# nested command-substitution: paren-depth counter in _extract_substitutions
# recurses into `$(true && git commit …)` inside double-quoted outer string.
run_case "nested command-substitution \$(true && git commit …) is detected" block Bash \
  'echo "$(true && git commit -m nested)"' "$TX_WITHOUT"

# QUOTED command-substitution `echo "$(git commit …)"`:
# shlex folds the whole double-quoted span into one token; _extract_substitutions
# span-scan extracts `git commit …` and re-tokenizes it → detected.
run_case "quoted command-substitution echo \"\$(git commit …)\" is detected" block Bash \
  'echo "$(git commit -m subst)"' "$TX_WITHOUT"

# SINGLE-quoted form: `echo '$(git commit …)'` is a LITERAL string in bash —
# the inner command is NEVER executed, so it must NOT block.
run_case "single-quoted \$(git commit) is literal — not a commit (pass)" pass Bash \
  "echo '\$(git commit -m x)'" "$TX_WITHOUT"

# Double-quoted plain string `echo "git commit"` — no `$(…)` span, no
# token-level git+commit adjacency → must NOT block.
run_case "double-quoted literal echo \"git commit\" is not a commit (pass)" pass Bash \
  'echo "git commit"' "$TX_WITHOUT"

# Single-quoted plain string `echo 'git commit'` — same reasoning.
run_case "single-quoted literal echo 'git commit' is not a commit (pass)" pass Bash \
  "echo 'git commit'" "$TX_WITHOUT"

# --allow-empty is NOT an exemption (staged content can still ride along).
# Covered in the PASS/exemptions section above; add a hardened-parser companion
# confirming it is still detected when combined with the new tokenizer.
run_case "allow-empty still blocked with hardened tokenizer" block Bash \
  'git commit --allow-empty -m "ci: trigger"' "$TX_WITHOUT"

# --allow-empty-message likewise not exempt.
run_case "allow-empty-message still blocked with hardened tokenizer" block Bash \
  'git commit --allow-empty-message -m ""' "$TX_WITHOUT"

# git --help commit: terminal option → no commit runs (fail-open).
run_case "git --help commit is terminal option (pass)" pass Bash \
  'git --help commit' "$TX_WITHOUT"

# git --version commit: terminal option → no commit runs.
run_case "git --version commit is terminal option (pass)" pass Bash \
  'git --version commit' "$TX_WITHOUT"

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
# Uncaught exception fail-open (outer Exception guard)
# ---------------------------------------------------------------------------

# main() now opts into the shared @fail_open guard; verify the decorator is
# applied (fail-open behaviour itself is covered in tests/test_hook_runtime.sh).
_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util, io
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if getattr(mod.main, "__wrapped__", None) is None:
    sys.stderr.write("main not wrapped by @fail_open\n"); sys.exit(1)
PYEOF
)
_uncaught_rc=$?
if [ "$_uncaught_rc" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "PASS [pass] main() is wrapped by the shared @fail_open guard"; ((PASS++))
else
  echo "FAIL [pass→rc=$_uncaught_rc] main() is wrapped by the shared @fail_open guard"; ((FAIL++)); FAILED_NAMES+=("main() is wrapped by the shared @fail_open guard")
fi

# ---------------------------------------------------------------------------
# Cleanup + summary
# ---------------------------------------------------------------------------

rm -f "$TX_WITH_SKILL" "$TX_WITH_SLASH" "$TX_ASSISTANT_SLASH" "$TX_WITHOUT" "$TX_WRONG_SKILL" "$TX_GARBAGE_PLUS_SKILL"

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
