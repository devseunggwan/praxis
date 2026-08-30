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

# Subagent-transcript fixtures (praxis issue #730) ---------------------------
# Claude Code lays subagent transcripts out as
# <project-dir>/<session_id>/subagents/agent-*.jsonl next to the root
# <project-dir>/<session_id>.jsonl — reproduce that layout under a temp dir so
# the root path's `.jsonl` suffix + stem-dir lookup resolves correctly.
SUB_TX_ROOT_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
SUB_SESSION_ID="session-$$"
SUB_ROOT_TX="$SUB_TX_ROOT_DIR/$SUB_SESSION_ID.jsonl"
SUB_AGENTS_DIR="$SUB_TX_ROOT_DIR/$SUB_SESSION_ID/subagents"
mkdir -p "$SUB_AGENTS_DIR"

# Root transcript has no review evidence of its own.
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"implementing"}]}}' >"$SUB_ROOT_TX"

# Subagent transcript DOES contain the Skill tool_use (matches live-observed
# subagent JSONL shape: message.content[] tool_use with name=Skill).
printf '%s\n' '{"parentUuid":null,"isSidechain":true,"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_1","name":"Skill","input":{"skill":"praxis:codex-review-wrap"},"caller":{"type":"direct"}}]}}' >"$SUB_AGENTS_DIR/agent-abc123.jsonl"

# A second, sibling temp-dir layout where the subagents dir exists but no
# agent transcript invokes codex-review-wrap — must still BLOCK.
SUB_TX_ROOT_DIR_NOINVOKE=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
SUB_SESSION_ID_NOINVOKE="session-noinvoke-$$"
SUB_ROOT_TX_NOINVOKE="$SUB_TX_ROOT_DIR_NOINVOKE/$SUB_SESSION_ID_NOINVOKE.jsonl"
SUB_AGENTS_DIR_NOINVOKE="$SUB_TX_ROOT_DIR_NOINVOKE/$SUB_SESSION_ID_NOINVOKE/subagents"
mkdir -p "$SUB_AGENTS_DIR_NOINVOKE"
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"implementing"}]}}' >"$SUB_ROOT_TX_NOINVOKE"
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Read","input":{"file_path":"x.py"}}]}}' >"$SUB_AGENTS_DIR_NOINVOKE/agent-def456.jsonl"

# A third layout: the subagent transcript has a literal
# "/praxis:codex-review-wrap" line as a `user`-type entry — this is NOT a
# genuine human slash-command keystroke (subagent "user" turns are
# Task-dispatch prompts / tool_results), so it must NOT satisfy the gate.
# Regression guard for the root-only slash-scoping fix (code-review MEDIUM).
SUB_TX_ROOT_DIR_SLASH=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
SUB_SESSION_ID_SLASH="session-slash-$$"
SUB_ROOT_TX_SLASH="$SUB_TX_ROOT_DIR_SLASH/$SUB_SESSION_ID_SLASH.jsonl"
SUB_AGENTS_DIR_SLASH="$SUB_TX_ROOT_DIR_SLASH/$SUB_SESSION_ID_SLASH/subagents"
mkdir -p "$SUB_AGENTS_DIR_SLASH"
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"implementing"}]}}' >"$SUB_ROOT_TX_SLASH"
printf '%s\n' '{"type":"user","isSidechain":true,"message":{"role":"user","content":"/praxis:codex-review-wrap"}}' >"$SUB_AGENTS_DIR_SLASH/agent-ghi789.jsonl"

PASS=0; FAIL=0; FAILED_NAMES=()

# Capability tiering (#1187, per #1159): this runner has no codex CLI on
# PATH, so without attestation every deny would demote to advisory. The
# detection matrix below verifies transcript scanning, not tier — pin the
# deny via the strict env; dedicated tier cases at the end override it.
export PRAXIS_CODEX_REVIEW_STRICT=1

# run_case <name> <block|pass> <tool_name> <command> [transcript_path|"NONE"] [env]
run_case() {
  local name="$1" expected="$2" tool_name="$3" command="$4" tx="${5:-NONE}" env_kv="${6:-}" env_kv2="${7:-}"
  local payload err_file rc err_content
  payload=$(python3 -c '
import json, sys
p = {"tool_name": sys.argv[1], "tool_input": {"command": sys.argv[2]}}
if sys.argv[3] != "NONE":
    p["transcript_path"] = sys.argv[3]
print(json.dumps(p))' "$tool_name" "$command" "$tx")
  err_file=$(mktemp)
  if [ -n "$env_kv2" ]; then
    echo "$payload" | env "$env_kv" "$env_kv2" "$HOOK" >/dev/null 2>"$err_file"
  elif [ -n "$env_kv" ]; then
    echo "$payload" | env "$env_kv" "$HOOK" >/dev/null 2>"$err_file"
  else
    echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  fi
  rc=$?
  err_content=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  if [ "$expected" = "block" ]; then
    [ "$rc" -eq 2 ] && [ -n "$err_content" ] || ok=0
  elif [ "$expected" = "warn" ]; then
    # #1187 capability-absent tier: proceeds (rc 0), the advisory names
    # both escalation routes, and no BLOCKED banner ships.
    [ "$rc" -eq 0 ] && [ -n "$err_content" ] || ok=0
    echo "$err_content" | grep -q "\[advisory\]" || ok=0
    echo "$err_content" | grep -q "PRAXIS_CODEX_REVIEW_STRICT" || ok=0
    echo "$err_content" | grep -q "BLOCKED" && ok=0
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

# NOTE: this simulates the env var already present in the hook's OWN process
# env (the only case that actually works — see spec.md Escape hatches). It
# does NOT simulate `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1 git commit …` as
# an inline command prefix, which never reaches this process (issue #730).
run_case "persistent env var in hook process bypasses the gate" pass Bash \
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
# Subagent-transcript scanning (praxis issue #730)
# ---------------------------------------------------------------------------

run_case "codex-review-wrap run inside a subagent this session satisfies the gate" pass Bash \
  'git commit -m "feat: x"' "$SUB_ROOT_TX"

run_case "subagent dir exists but no subagent invoked codex-review-wrap" block Bash \
  'git commit -m "feat: x"' "$SUB_ROOT_TX_NOINVOKE"

run_case "slash-command line inside a subagent transcript does NOT satisfy the gate" block Bash \
  'git commit -m "feat: x"' "$SUB_ROOT_TX_SLASH"

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
# Repeated same-session block escalation (issue #805)
#
# The block message escalates from the 2nd same-session block onward, read from
# the fire ledger (count_session_fires). These cases point the ledger at a temp
# file, seed prior RICH block records for a session_id, and assert the
# ESCALATION banner appears only when a prior block exists — while the block
# verdict (rc 2) is unchanged in every case.
# ---------------------------------------------------------------------------

ESC_LEDGER=$(mktemp)
ESC_SESSION="sess-escalate-$$"

# emits a RICH block record for the given session into the ledger
esc_seed_block() {
  local sid="$1"
  printf '%s\n' "{\"granularity\":\"rich\",\"hook\":\"block-commit-without-codex-review\",\"session_id\":\"$sid\",\"decision\":\"block\"}" >>"$ESC_LEDGER"
}

# run_escalation_case <name> <session_id> <expect-banner:yes|no>
run_escalation_case() {
  local name="$1" sid="$2" expect_banner="$3"
  local payload err_file rc err_content has_banner
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": "git commit -m \"feat: x\""},
    "transcript_path": sys.argv[1],
    "session_id": sys.argv[2],
}))' "$TX_WITHOUT" "$sid")
  err_file=$(mktemp)
  echo "$payload" | env "PRAXIS_FIRE_TELEMETRY_FILE=$ESC_LEDGER" "$HOOK" >/dev/null 2>"$err_file"
  rc=$?
  err_content=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  # verdict is always block (rc 2 + non-empty stderr), regardless of escalation
  [ "$rc" -eq 2 ] && [ -n "$err_content" ] || ok=0
  case "$err_content" in *ESCALATION*) has_banner=yes;; *) has_banner=no;; esac
  [ "$has_banner" = "$expect_banner" ] || ok=0

  if [ "$ok" -eq 1 ]; then
    echo "PASS [block/banner=$expect_banner] $name"; ((PASS++))
  else
    echo "FAIL [block/banner want=$expect_banner got=$has_banner rc=$rc] $name"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}

# 1st block for the session: ledger empty → no escalation banner
run_escalation_case "1st same-session block: no escalation banner" "$ESC_SESSION" no
# after 1 prior block: 2nd block escalates
esc_seed_block "$ESC_SESSION"
run_escalation_case "2nd same-session block: escalation banner present" "$ESC_SESSION" yes
# a different session with no prior blocks is independent (no escalation)
run_escalation_case "different session is independent: no banner" "other-session-$$" no

rm -f "$ESC_LEDGER"

# ---------------------------------------------------------------------------
# #1187 capability tiering — deny only when the codex capability is present
# ---------------------------------------------------------------------------
unset PRAXIS_CODEX_REVIEW_STRICT

# Controlled no-codex PATH: a dir holding only a python3 symlink (the hook
# shebang needs it) so these cases hold on machines that DO have a real
# codex CLI installed — trusting the runner's PATH is env-dependent.
SAFE_BIN_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
ln -s "$(command -v python3)" "$SAFE_BIN_DIR/python3"

run_case "1187: no capability (sanitized PATH) → warn (advisory)" warn Bash \
  'git commit -m "feat: x"' "$TX_WITHOUT" "PATH=$SAFE_BIN_DIR"

# Strict env pins the deny regardless of detection.
run_case "1187: no capability, STRICT=1 → block" block Bash \
  'git commit -m "feat: x"' "$TX_WITHOUT" "PRAXIS_CODEX_REVIEW_STRICT=1"

# A codex binary on PATH attests the capability → deny without any env.
FAKE_BIN_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
printf '#!/bin/sh\nexit 0\n' > "$FAKE_BIN_DIR/codex"; chmod +x "$FAKE_BIN_DIR/codex"
run_case "1187: codex on PATH, env unset → block (detected)" block Bash \
  'git commit -m "feat: x"' "$TX_WITHOUT" "PATH=$FAKE_BIN_DIR:$PATH"

# Negative control: review present in transcript stays silent in BOTH tiers.
run_case "1187: review ran, no capability → pass (silent)" pass Bash \
  'git commit -m "feat: x"' "$TX_WITH_SKILL" "PATH=$SAFE_BIN_DIR"
run_case "1187: review ran, STRICT=1 → pass (silent)" pass Bash \
  'git commit -m "feat: x"' "$TX_WITH_SKILL" "PRAXIS_CODEX_REVIEW_STRICT=1"
run_case "1187: no capability, STRICT=0 → warn (explicit demote)" warn Bash \
  'git commit -m "feat: x"' "$TX_WITHOUT" "PRAXIS_CODEX_REVIEW_STRICT=0"

# Detected-but-demoted (STRICT=0 with codex ON PATH): the advisory must not
# falsely claim the CLI is missing — it names the demoting env instead.
run_case "1187: codex detected + STRICT=0 → warn (demoted variant)" warn Bash \
  'git commit -m "feat: x"' "$TX_WITHOUT" "PATH=$FAKE_BIN_DIR:$PATH" "PRAXIS_CODEX_REVIEW_STRICT=0"
demoted_err=$(python3 -c '
import json, sys
print(json.dumps({"tool_name":"Bash","tool_input":{"command":"git commit -m \"feat: x\""},"transcript_path":sys.argv[1]}))' "$TX_WITHOUT" | \
  env "PATH=$FAKE_BIN_DIR:$PATH" "PRAXIS_CODEX_REVIEW_STRICT=0" "$HOOK" 2>&1 >/dev/null)
if echo "$demoted_err" | grep -q "IS on PATH" && ! echo "$demoted_err" | grep -q "not detected"; then
  echo "PASS [wording] demoted variant names the env, not a missing CLI"; ((PASS++))
else
  echo "FAIL [wording] demoted variant: $demoted_err"; ((FAIL++)); FAILED_NAMES+=("demoted variant wording")
fi
rm -rf "$FAKE_BIN_DIR" "$SAFE_BIN_DIR"

# ---------------------------------------------------------------------------
# Cleanup + summary
# ---------------------------------------------------------------------------

rm -f "$TX_WITH_SKILL" "$TX_WITH_SLASH" "$TX_ASSISTANT_SLASH" "$TX_WITHOUT" "$TX_WRONG_SKILL" "$TX_GARBAGE_PLUS_SKILL"
rm -rf "$SUB_TX_ROOT_DIR" "$SUB_TX_ROOT_DIR_NOINVOKE" "$SUB_TX_ROOT_DIR_SLASH"

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
