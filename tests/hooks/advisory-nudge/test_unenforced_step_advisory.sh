#!/bin/bash
# test_unenforced_step_advisory.sh — coverage for
# hooks/advisory-nudge/unenforced-step-advisory/impl.py
#
# Synthesizes Claude Code PreToolUse payloads against a throwaway git repo and
# asserts:
#   advise → exit 0 + stderr contains "UNENFORCED MANDATORY STEP"
#   silent → exit 0 + stderr empty
#   block  → exit 2 + stderr contains "UNENFORCED MANDATORY STEP"
#            (PRAXIS_UNENFORCED_STEP_STRICT=1)
#
# The rebase predicates need a real repo whose HEAD is genuinely behind a base
# ref, so the fixture builds one: a `base` branch with an extra commit, and
# `origin/main` pointed at it through a self-remote.
#
# Usage: bash tests/hooks/advisory-nudge/test_unenforced_step_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/unenforced-step-advisory/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

TMP_ROOT=$(mktemp -d /tmp/unenf-XXXXXX) || { echo "FAIL: mktemp -d" >&2; exit 1; }
trap 'rm -rf "$TMP_ROOT"' EXIT

TEL_FILE="$TMP_ROOT/telemetry.jsonl"
export PRAXIS_FIRE_TELEMETRY_FILE="$TEL_FILE"

PASS=0
FAIL=0
FAILED_NAMES=()

run_case() {
  local name="$1" expectation="$2" mode="$3" payload="$4"

  local err_file="$TMP_ROOT/stderr.$$"
  case "$mode" in
    strict)
      echo "$payload" | env -u PRAXIS_UNENFORCED_STEP_SKIP \
        PRAXIS_UNENFORCED_STEP_STRICT=1 python3 "$HOOK" >/dev/null 2>"$err_file"
      ;;
    skip)
      echo "$payload" | env -u PRAXIS_UNENFORCED_STEP_STRICT \
        PRAXIS_UNENFORCED_STEP_SKIP=1 python3 "$HOOK" >/dev/null 2>"$err_file"
      ;;
    *)
      echo "$payload" | env -u PRAXIS_UNENFORCED_STEP_STRICT \
        -u PRAXIS_UNENFORCED_STEP_SKIP python3 "$HOOK" >/dev/null 2>"$err_file"
      ;;
  esac
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    advise)
      [ "$rc" -eq 0 ] || ok=0
      echo "$err" | grep -q "UNENFORCED MANDATORY STEP" || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q "UNENFORCED MANDATORY STEP" || ok=0
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
  fi
}

echo "test_unenforced_step_advisory"

# --- Fixtures ----------------------------------------------------------------

# BEHIND: a repo whose HEAD lacks one commit that origin/main has.
BEHIND="$TMP_ROOT/behind"
mkdir -p "$BEHIND"
(
  cd "$BEHIND" || exit 1
  git init -q -b main .
  git -c user.name=t -c user.email=t@e commit -q --allow-empty -m base
  git branch -q feature
  git -c user.name=t -c user.email=t@e commit -q --allow-empty -m ahead
  git update-ref refs/remotes/origin/main HEAD
  git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
  git checkout -q feature
) >/dev/null 2>&1

# CURRENT: same shape, but HEAD is level with origin/main.
CURRENT="$TMP_ROOT/current"
mkdir -p "$CURRENT"
(
  cd "$CURRENT" || exit 1
  git init -q -b main .
  git -c user.name=t -c user.email=t@e commit -q --allow-empty -m base
  git update-ref refs/remotes/origin/main HEAD
  git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
) >/dev/null 2>&1

# NOBASE: no origin ref at all — the rebase predicates cannot be measured.
NOBASE="$TMP_ROOT/nobase"
mkdir -p "$NOBASE"
(
  cd "$NOBASE" || exit 1
  git init -q -b main .
  git -c user.name=t -c user.email=t@e commit -q --allow-empty -m base
) >/dev/null 2>&1

# A session that did none of the four steps, but DID run codex-review-wrap
# (which is what leaves the review predicate live rather than deferring to
# block-commit-without-codex-review).
BARE="$TMP_ROOT/bare.jsonl"
cat > "$BARE" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"praxis:codex-review-wrap"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"git status --short"}}]}}
EOF

# The same session with no codex-review-wrap call at all.
NOCODEX="$TMP_ROOT/nocodex.jsonl"
cat > "$NOCODEX" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"git status --short"}}]}}
EOF

# Sessions that performed each step.
REVIEWED="$TMP_ROOT/reviewed.jsonl"
cat > "$REVIEWED" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"praxis:codex-review-wrap"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Agent","input":{"subagent_type":"oh-my-claudecode:code-reviewer","prompt":"review"}}]}}
EOF

REBASED="$TMP_ROOT/rebased.jsonl"
cat > "$REBASED" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"git fetch origin && git rebase origin/main"}}]}}
EOF

PULLED="$TMP_ROOT/pulled.jsonl"
cat > "$PULLED" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"git pull --rebase origin main"}}]}}
EOF

SCANNED="$TMP_ROOT/scanned.jsonl"
cat > "$SCANNED" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"gh pr list --state open --limit 50"}}]}}
EOF

# The exact miss the issue records: the session's only enumeration was
# --state merged.
MERGED_ONLY="$TMP_ROOT/merged_only.jsonl"
cat > "$MERGED_ONLY" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"gh pr list --state merged --limit 20"}}]}}
EOF

SHORT_MERGED="$TMP_ROOT/short_merged.jsonl"
cat > "$SHORT_MERGED" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"gh pr list -s merged --limit 20"}}]}}
EOF

EQ_MERGED="$TMP_ROOT/eq_merged.jsonl"
cat > "$EQ_MERGED" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"gh pr list --state=merged"}}]}}
EOF

SEARCH_CLOSED="$TMP_ROOT/search_closed.jsonl"
cat > "$SEARCH_CLOSED" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"gh search prs --state closed praxis"}}]}}
EOF

SEARCH_OPEN="$TMP_ROOT/search_open.jsonl"
cat > "$SEARCH_OPEN" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"gh search prs --repo o/r issue-9"}}]}}
EOF

# A code-reviewer dispatch recorded only in a SUBAGENT transcript — the root
# transcript sees just the tool_result, so a root-only scan would miss it.
SUB_ROOT="$TMP_ROOT/subroot.jsonl"
cat > "$SUB_ROOT" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Skill","input":{"skill":"praxis:codex-review-wrap"}}]}}
EOF
mkdir -p "$TMP_ROOT/subroot/subagents"
cat > "$TMP_ROOT/subroot/subagents/agent-1.jsonl" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Agent","input":{"subagent_type":"oh-my-claudecode:code-reviewer","prompt":"review"}}]}}
EOF

# A session whose only mention of the enumeration is inside a heredoc body it
# wrote to a file — authored text, never an executed command.
HEREDOC_ONLY="$TMP_ROOT/heredoc_only.jsonl"
python3 - "$HEREDOC_ONLY" <<'PYGEN'
import json, sys
inner = "cat > /tmp/fixture.jsonl <<'XEOF'\n{\"command\": \"$(gh pr list --state open)\"}\nXEOF\n"
rec = {"type": "assistant", "message": {"role": "assistant", "content": [
    {"type": "tool_use", "name": "Bash", "input": {"command": inner}}]}}
open(sys.argv[1], "w").write(json.dumps(rec) + "\n")
PYGEN

payload() {  # payload <command> <transcript> <cwd>
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
print(json.dumps({
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "transcript_path": sys.argv[2],
    "cwd": sys.argv[3],
    "session_id": "test-1064",
}))
PY
}

COMMIT_CMD="git commit -m 'feat(x): thing'"

# --- Predicate: review agent before a content commit -------------------------

run_case "review: no code-reviewer dispatch, codex-review-wrap ran (advise)" \
  "advise" "advisory" "$(payload "$COMMIT_CMD" "$BARE" "$CURRENT")"

run_case "review: code-reviewer dispatched (silent)" \
  "silent" "advisory" "$(payload "$COMMIT_CMD" "$REVIEWED" "$CURRENT")"

run_case "review: codex-review-wrap absent defers to the blocking gate (silent)" \
  "silent" "advisory" "$(payload "$COMMIT_CMD" "$NOCODEX" "$CURRENT")"

run_case "review: --amend is a fix-up of an already-gated call (silent)" \
  "silent" "advisory" \
  "$(payload "git commit --amend --no-edit" "$BARE" "$CURRENT")"

run_case "review: env prefix does not exempt the trigger (advise)" \
  "advise" "advisory" \
  "$(payload "env FOO=1 $COMMIT_CMD" "$BARE" "$CURRENT")"

run_case "review: separator-chained trigger is classified (advise)" \
  "advise" "advisory" \
  "$(payload "true; $COMMIT_CMD" "$BARE" "$CURRENT")"

# Global options that consume the NEXT token. Skipping the flag without its
# value leaves the value where the subcommand should be and every match fails
# silently — `git -C /repo commit` and `gh -R owner/repo pr merge` are the
# common forms.
run_case "review: git -C <path> before the subcommand (advise)" \
  "advise" "advisory" \
  "$(payload "git -C $CURRENT commit -m x" "$BARE" "$CURRENT")"

run_case "review: git -c key=value before the subcommand (advise)" \
  "advise" "advisory" \
  "$(payload "git -c user.name=t commit -m x" "$BARE" "$CURRENT")"

run_case "review: git --help commit runs no subcommand (silent)" \
  "silent" "advisory" \
  "$(payload "git --help commit" "$BARE" "$CURRENT")"

# `-h` as a FLAG VALUE is not a help request — this commits, so it must advise.
# `--amend` as a MESSAGE VALUE is not an amend — this is a content commit, and
# treating it as exempt would walk it past strict mode's block too.
run_case "review: --amend as a message value is a content commit (advise)" \
  "advise" "advisory" \
  "$(payload "git commit -m --amend" "$BARE" "$CURRENT")"

# A later segment can hold a real content commit even when the first is exempt.
run_case "review: amend then a content commit in one command (advise)" \
  "advise" "advisory" \
  "$(payload "git commit --amend --no-edit && git commit -m x" "$BARE" "$CURRENT")"

run_case "review: -h as a message value is still a real commit (advise)" \
  "advise" "advisory" \
  "$(payload "git commit -m -h" "$BARE" "$CURRENT")"

run_case "review: command-substitution-wrapped commit is classified (advise)" \
  "advise" "advisory" \
  "$(payload "OUT=\$(git commit -m x)" "$BARE" "$CURRENT")"

# spec Detection item 4: a dispatch recorded only in a subagent transcript
# still clears the predicate.
run_case "review: code-reviewer dispatched inside a subagent (silent)" \
  "silent" "advisory" \
  "$(payload "$COMMIT_CMD" "$SUB_ROOT" "$CURRENT")"

# --- Predicate: pre-PR rebase ------------------------------------------------

run_case "pre-pr: no rebase and branch is behind base (advise)" \
  "advise" "advisory" \
  "$(payload "gh pr create --fill" "$BARE" "$BEHIND")"

run_case "pre-pr: branch already current — rebase is a no-op (silent)" \
  "silent" "advisory" \
  "$(payload "gh pr create --fill" "$BARE" "$CURRENT")"

# A rebase earlier in the session does NOT clear it: what is measured is the
# branch's distance from its base, so a stale rebase cannot silence the check.
run_case "pre-pr: rebase ran but branch is still behind (advise)" \
  "advise" "advisory" \
  "$(payload "gh pr create --fill" "$REBASED" "$BEHIND")"

run_case "pre-merge: rebase ran at PR time, base moved since (advise)" \
  "advise" "advisory" \
  "$(payload "gh pr merge 42 --squash" "$REBASED" "$BEHIND")"

run_case "pre-pr: rebase ran and branch is current (silent)" \
  "silent" "advisory" \
  "$(payload "gh pr create --fill" "$REBASED" "$CURRENT")"

run_case "pre-pr: base ref unresolvable — unmeasured premise (silent)" \
  "silent" "advisory" \
  "$(payload "gh pr create --fill" "$BARE" "$NOBASE")"

run_case "pre-pr: gh -R owner/repo before the subcommand (advise)" \
  "advise" "advisory" \
  "$(payload "gh -R devseunggwan/praxis pr create --fill" "$BARE" "$BEHIND")"

# --- Predicate: pre-merge rebase ---------------------------------------------

run_case "pre-merge: no rebase and branch is behind base (advise)" \
  "advise" "advisory" \
  "$(payload "gh pr merge 42 --squash --delete-branch" "$BARE" "$BEHIND")"

run_case "pre-merge: branch already current (silent)" \
  "silent" "advisory" \
  "$(payload "gh pr merge 42 --squash" "$BARE" "$CURRENT")"

run_case "pre-merge: --repo between gh and the subcommand (advise)" \
  "advise" "advisory" \
  "$(payload "gh --repo o/r pr merge 42 --squash" "$BARE" "$BEHIND")"

run_case "pre-merge: -R between the group and the verb (advise)" \
  "advise" "advisory" \
  "$(payload "gh pr -R o/r merge 42 --squash" "$BARE" "$BEHIND")"

# --- Predicate: in-flight PR check -------------------------------------------

run_case "in-flight: worktree add with no open-PR enumeration (advise)" \
  "advise" "advisory" \
  "$(payload "git worktree add /tmp/wt-9 -b issue-9-x origin/main" "$BARE" "$CURRENT")"

run_case "in-flight: --state merged does not clear it (advise)" \
  "advise" "advisory" \
  "$(payload "git worktree add /tmp/wt-9 -b issue-9-x origin/main" "$MERGED_ONLY" "$CURRENT")"

run_case "in-flight: open-PR enumeration ran (silent)" \
  "silent" "advisory" \
  "$(payload "git worktree add /tmp/wt-9 -b issue-9-x origin/main" "$SCANNED" "$CURRENT")"

run_case "in-flight: -s merged short form does not clear it (advise)" \
  "advise" "advisory" \
  "$(payload "git worktree add /tmp/wt-9 -b issue-9-x origin/main" "$SHORT_MERGED" "$CURRENT")"

run_case "in-flight: --state=merged equals form does not clear it (advise)" \
  "advise" "advisory" \
  "$(payload "git worktree add /tmp/wt-9 -b issue-9-x origin/main" "$EQ_MERGED" "$CURRENT")"

run_case "in-flight: gh search prs --state closed does not clear it (advise)" \
  "advise" "advisory" \
  "$(payload "git worktree add /tmp/wt-9 -b issue-9-x origin/main" "$SEARCH_CLOSED" "$CURRENT")"

# A heredoc body is data handed to another program, not commands the shell
# runs. Authoring a fixture that quotes the enumeration must not register as
# having run it (measured live during this hook's own implementation).
run_case "in-flight: enumeration quoted inside a heredoc body does not clear it (advise)" \
  "advise" "advisory" \
  "$(payload "git worktree add /tmp/wt-9 -b issue-9-x origin/main" "$HEREDOC_ONLY" "$CURRENT")"

run_case "in-flight: gh search prs (open by default) clears it (silent)" \
  "silent" "advisory" \
  "$(payload "git worktree add /tmp/wt-9 -b issue-9-x origin/main" "$SEARCH_OPEN" "$CURRENT")"

# The repo's own dispatch idiom wraps the call in a command substitution
# (skills/cmux-delegate/SKILL.md:492). strip_prefix reads `WS_RAW=$(cmux` as an
# env assignment and drops the binary, so without span extraction the canonical
# call is invisible.
run_case "in-flight: WS_RAW=\$(cmux new-workspace …) is classified (advise)" \
  "advise" "advisory" \
  "$(payload "WS_RAW=\$(cmux new-workspace --name issue-9)" "$BARE" "$CURRENT")"

run_case "in-flight: cmux --config <path> before the verb (advise)" \
  "advise" "advisory" \
  "$(payload "cmux --config /tmp/c.json new-workspace --name x" "$BARE" "$CURRENT")"

run_case "in-flight: git -C add worktree list is not a trigger (silent)" \
  "silent" "advisory" \
  "$(payload "git -C add worktree list" "$BARE" "$CURRENT")"

run_case "in-flight: cmux new-workspace is a dispatch trigger (advise)" \
  "advise" "advisory" \
  "$(payload "cmux new-workspace --name issue-9" "$BARE" "$CURRENT")"

run_case "in-flight: cmux workspace create is a dispatch trigger (advise)" \
  "advise" "advisory" \
  "$(payload "cmux workspace create issue-9" "$BARE" "$CURRENT")"

run_case "in-flight: git worktree list is not a trigger (silent)" \
  "silent" "advisory" \
  "$(payload "git worktree list --porcelain" "$BARE" "$CURRENT")"

# --- Modes -------------------------------------------------------------------

run_case "strict: a fire becomes a hard block" \
  "block" "strict" "$(payload "$COMMIT_CMD" "$BARE" "$CURRENT")"

run_case "skip: silences a would-be fire" \
  "silent" "skip" "$(payload "$COMMIT_CMD" "$BARE" "$CURRENT")"

# --- Fail-open ---------------------------------------------------------------

run_case "fail-open: non-Bash tool (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"/x\"},\"transcript_path\":\"$BARE\"}"

run_case "fail-open: missing transcript (silent)" \
  "silent" "advisory" \
  "$(payload "$COMMIT_CMD" "$TMP_ROOT/does-not-exist.jsonl" "$CURRENT")"

run_case "fail-open: no transcript_path key (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"$COMMIT_CMD\"}}"

run_case "fail-open: unbalanced quotes are unparseable (silent)" \
  "silent" "advisory" \
  "$(payload "git commit -m 'unterminated" "$BARE" "$CURRENT")"

run_case "fail-open: unrelated command (silent)" \
  "silent" "advisory" \
  "$(payload "ls -la" "$BARE" "$CURRENT")"

run_case "fail-open: the trigger word inside a quoted string (silent)" \
  "silent" "advisory" \
  "$(payload "echo 'gh pr create'" "$BARE" "$BEHIND")"

# --- Summary -----------------------------------------------------------------

echo
echo "  passed: $PASS   failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  for n in "${FAILED_NAMES[@]}"; do echo "    - $n"; done
  exit 1
fi
exit 0
