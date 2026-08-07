#!/bin/bash
# test_commit_title_length_check.sh — coverage for hooks/preflight-gate/commit-title-length-check/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   ask    → stdout contains "permissionDecision" / "ask", rc=0
#   silent → stdout empty, stderr empty, rc=0
#
# Usage: bash tests/test_commit_title_length_check.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/commit-title-length-check/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation payload_json [env_vars...]
#   expectation:
#     "ask"    — stdout contains permissionDecision ask, rc=0
#     "silent" — stdout empty, stderr empty, rc=0
run_case() {
  local name="$1" expectation="$2" payload="$3"
  shift 3
  # remaining args are KEY=VALUE env pairs
  local env_args=()
  for kv in "$@"; do
    env_args+=("$kv")
  done

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)

  if [ "${#env_args[@]}" -gt 0 ]; then
    echo "$payload" | env "${env_args[@]}" python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | env -u CLAUDE_COMMIT_TITLE_MAX python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    ask)
      [ "$rc" -eq 0 ] || ok=0
      echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['hookSpecificOutput']['permissionDecision']=='ask'" 2>/dev/null || ok=0
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
    [ -n "$out" ] && echo "        stdout: $(echo "$out" | head -c 400)"
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -c 400)"
  fi
}

echo "test_commit_title_length_check"

# ---------------------------------------------------------------------------
# PASS cases — titles within limit
# ---------------------------------------------------------------------------

# Exactly 50 chars
TITLE_50="feat(scope): exactly fifty character title heree!!"
echo -n "$TITLE_50" | wc -c | grep -q "^.*50$" 2>/dev/null || true

run_case "50-char title via -m (boundary, pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m \\\"feat(scope): exactly fifty character title heree!!\\\"\"}}"

run_case "49-char title via -m (pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m \\\"feat(scope): forty-nine character title here!!\\\"\"}}"

run_case "short title via --message (pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit --message \\\"fix(auth): correct null pointer\\\"\"}}"

run_case "short title via -m=value embedded (pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m=\\\"fix(auth): short title\\\"\"}}"

# Merge commit — skip regardless of length
run_case "Merge commit auto-generated title (skip, pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m \\\"Merge branch 'feature/very-long-branch-name-that-exceeds-fifty-characters' into main\\\"\"}}"

# Revert commit — skip
run_case "Revert commit auto-generated title (skip, pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m \\\"Revert \\\\\\\"feat(scope): some feature that was reverted because it broke things\\\\\\\"\\\"\"}}"

# Body protection: second -m is body, not title
run_case "body in 2nd -m not flagged (pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m \\\"fix: short\\\" -m \\\"long body that exceeds fifty characters easily but should not be flagged at all\\\"\"}}"

# Non-commit git command — silent
run_case "git status (non-commit, silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git status\"}}"

# Different tool altogether — silent
run_case "gh issue create with -m (non-Bash tool, silent)" \
  "silent" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/tmp/x\",\"content\":\"y\"}}"

# Opt-out marker
run_case "opt-out marker bypasses long title (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m \\\"fix(custom-pipeline): include base_source CTE for empty-transform subquery sources\\\" # title-length:ack\"}}"

# Non-Bash tool_name with commit-like command — silent
run_case "non-Bash tool_name (silent)" \
  "silent" \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"command\":\"git commit -m \\\"fix(custom-pipeline): include base_source CTE for empty-transform subquery sources\\\"\"}}"

# Malformed JSON — silent fail-open
run_case "malformed JSON (fail-open, silent)" \
  "silent" \
  "not-json"

# Quoted commit-like string in echo — silent (echo is argv[0], not git)
run_case "echo git commit fake (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo \\\"git commit -m 'a very long fake title that exceeds fifty characters easily'\\\"\"}}"

# ---------------------------------------------------------------------------
# ASK cases — titles exceeding limit
# ---------------------------------------------------------------------------

# 51 chars — one over limit
run_case "51-char title via -m (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m \\\"feat(scope): exactly fifty-one character title hre!\\\"\"}}"

# Hub #1912 regression: 78-char title
run_case "78-char title Hub#1912 regression (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m \\\"fix(custom-pipeline): include base_source CTE for empty-transform subquery sources\\\"\"}}"

# Korean title exceeding 50 code points (51 code points exactly)
run_case "Korean 51-char title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m \\\"feat(한글범위): 한글로 된 커밋 타이틀 길이를 검사하는 테스트 케이스입니다가나다라마바사\\\"\"}}"

# --message long title
run_case "long title via --message (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit --message \\\"refactor(auth): this title is way too long and well exceeds fifty characters total\\\"\"}}"

# Chained command: long title in second segment
run_case "chained git fetch && git commit long title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git fetch origin && git commit -m \\\"fix(custom-pipeline): include base_source CTE for empty-transform subquery sources\\\"\"}}"

# git commit --amend with long title
run_case "git commit --amend long title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit --amend -m \\\"fix(custom-pipeline): include base_source CTE for empty-transform subquery sources\\\"\"}}"

# combined -am flag with long title
run_case "git commit -am long title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -am \\\"fix(custom-pipeline): include base_source CTE for empty-transform subquery sources\\\"\"}}"

# ---------------------------------------------------------------------------
# ENV override: CLAUDE_COMMIT_TITLE_MAX=80 — 78-char title should pass
# ---------------------------------------------------------------------------

# 75-char title: exceeds default 50 but within max=80 → pass
run_case "CLAUDE_COMMIT_TITLE_MAX=80, 75-char title (pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m \\\"fix(pipeline): include base_source CTE for empty-transform subquery sources\\\"\"}}" \
  "CLAUDE_COMMIT_TITLE_MAX=80"

# ENV override lower — 30-char max, 31-char title should ask
run_case "CLAUDE_COMMIT_TITLE_MAX=30, 31-char title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m \\\"feat(auth): add refresh token support\\\"\"}}" \
  "CLAUDE_COMMIT_TITLE_MAX=30"

# ---------------------------------------------------------------------------
# -F file path cases
# ---------------------------------------------------------------------------

# Create a temp file with a short title
TMPFILE=$(mktemp)
echo "fix: short title" >"$TMPFILE"
run_case "-F file with short title (pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -F $TMPFILE\"}}"

# Create a temp file with a long title
TMPFILE_LONG=$(mktemp)
echo "fix(custom-pipeline): include base_source CTE for empty-transform subquery sources" >"$TMPFILE_LONG"
run_case "-F file with long title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -F $TMPFILE_LONG\"}}"

# -F - stdin — acknowledged limitation, silent pass
run_case "-F - stdin acknowledged limitation (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -F -\"}}"

rm -f "$TMPFILE" "$TMPFILE_LONG"

# ---------------------------------------------------------------------------
# F1 regression — git global flags between `git` and `commit`
# ---------------------------------------------------------------------------

LONG_51="feat(scope): exactly fifty-one character title hre!"
LONG_50="feat(scope): exactly fifty character title heree!!"

run_case "F1: git -C /path commit long title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git -C /some/path commit -m \\\"$LONG_51\\\"\"}}"

run_case "F1: git -c flag commit long title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git -c commit.gpgsign=true commit -m \\\"$LONG_51\\\"\"}}"

run_case "F1: git --git-dir=path commit long title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git --git-dir=/repo/.git commit -m \\\"$LONG_51\\\"\"}}"

run_case "F1: git --no-pager commit long title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git --no-pager commit -m \\\"$LONG_51\\\"\"}}"

run_case "F1: git -C /tmp -c foo=bar commit long title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git -C /tmp -c foo=bar commit -m \\\"$LONG_51\\\"\"}}"

run_case "F1: git -C /path commit 50-char title (boundary pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git -C /some/path commit -m \\\"$LONG_50\\\"\"}}"

run_case "F1: git -C /path status (not commit, silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git -C /some/path status\"}}"

run_case "F1: git -C /path log (not commit, silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git -C /some/path log\"}}"

# ---------------------------------------------------------------------------
# F2 regression — attached -m<value> short-option form
# ---------------------------------------------------------------------------

run_case "F2: git commit -m\"long title\" attached (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m\\\"$LONG_51\\\"\"}}"

run_case "F2: git commit -m\"50-char title\" attached (boundary pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m\\\"$LONG_50\\\"\"}}"

run_case "F2: git commit -mshort attached (pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -mshort\"}}"

run_case "F2: attached -m long + 2nd -m body not flagged (ask on title only)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m\\\"$LONG_51\\\" -m \\\"long body that should not be checked because it is the body not the title\\\"\"}}"

# ---------------------------------------------------------------------------
# Round 2 — F3: Bash backslash line continuation. AI agents routinely split
# long invocations across lines; without `\<newline>` preprocessing the hook
# silently misses the title check.
# ---------------------------------------------------------------------------

run_case "R2-F3: backslash newline + -m long title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit \\\\\n  -m \\\"$LONG_51\\\"\"}}"

run_case "R2-F3: backslash newline + 50-char (boundary pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit \\\\\n  -m \\\"$LONG_50\\\"\"}}"

run_case "R2-F3: backslash newline + git -C global flag + long title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git -C /some/path commit \\\\\n  -m \\\"$LONG_51\\\"\"}}"

# ---------------------------------------------------------------------------
# Round 4 — F1: -S<keyid> signed commit (POSIX combined-short rule). 'm' may
# appear in a key id like an email "-Smike@example.com"; round 1 detected
# combined-m via "m anywhere in tok[1:]" which incorrectly fired on -S<value>
# tokens. Tighten to "m must be the LAST char" — combined-short requires the
# argument-taking option to terminate the cluster.
# ---------------------------------------------------------------------------

run_case "R4-F1: -Smike@x.com + -m long title (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -Smike@example.com -m \\\"$LONG_51\\\"\"}}"

run_case "R4-F1: -Smike@x.com + -m 50-char (boundary pass)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -Smike@example.com -m \\\"$LONG_50\\\"\"}}"

run_case "R4-F1: -Skeymanish -m long (ask, m mid-token not LAST)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -Skeymanish -m \\\"$LONG_51\\\"\"}}"

run_case "R4-F1: -am long (preserves -am combined behavior, ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -am \\\"$LONG_51\\\"\"}}"

# ---------------------------------------------------------------------------
# Round 4 — F2: `git -C <dir> commit -F <relative-msg>`. Git resolves <msg>
# relative to <dir>, but the hook used to open relative to its own cwd —
# either wrong file or unreadable, silent pass either way. Carry the -C base
# into _title_from_file.
# ---------------------------------------------------------------------------

R4_F2_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
R4_F2_MSG="$R4_F2_DIR/.gitmsg"
echo "fix(very-very-long-scope): include description that easily exceeds fifty" >"$R4_F2_MSG"
R4_F2_ABS_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
R4_F2_ABS_MSG="$R4_F2_ABS_DIR/.gitmsg"
echo "fix(very-very-long-scope): absolute path with 73 chars title for boundary" >"$R4_F2_ABS_MSG"

run_case "R4-F2: git -C <dir> commit -F <relative> long (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git -C $R4_F2_DIR commit -F .gitmsg\"}}"

run_case "R4-F2: git -C <dir> commit -F <absolute> long (ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git -C $R4_F2_DIR commit -F $R4_F2_ABS_MSG\"}}"

run_case "R4-F2: git -C <dir1> -C <dir2-rel> commit -F <rel> (stacking, ask)" \
  "ask" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git -C $R4_F2_DIR -C . commit -F .gitmsg\"}}"

rm -f "$R4_F2_MSG" "$R4_F2_ABS_MSG"
rmdir "$R4_F2_DIR" "$R4_F2_ABS_DIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Squash-merge path (issue #843) — `gh pr merge --squash` resolves the PR
# title via a live `gh pr view` call, faked here via a per-case PATH-
# prepended fake `gh` binary. Advisory (stderr, non-blocking), never `ask`.
# ---------------------------------------------------------------------------

# make_fake_gh_title <title> — `gh pr view ... -q .title` prints <title>.
make_fake_gh_title() {
  local title="$1" d
  d=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
  cat >"$d/gh" <<EOF
#!/usr/bin/env bash
echo "$title"
exit 0
EOF
  chmod +x "$d/gh"
  echo "$d"
}

# make_fake_gh_error — every `gh` call exits 1 (auth-style failure).
make_fake_gh_error() {
  local d
  d=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
  cat >"$d/gh" <<'EOF'
#!/usr/bin/env bash
echo "gh: authentication required" >&2
exit 1
EOF
  chmod +x "$d/gh"
  echo "$d"
}

# run_case_gh name expectation command gh_dir
#   expectation:
#     advisory — stdout empty, stderr contains "Squash-merge title too long", rc=0
#     silent   — stdout empty, stderr empty, rc=0
run_case_gh() {
  local name="$1" expectation="$2" command="$3" gh_dir="$4"
  local payload out_file err_file
  payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))
' "$command")

  out_file=$(mktemp)
  err_file=$(mktemp)
  echo "$payload" | env -u CLAUDE_COMMIT_TITLE_MAX PATH="$gh_dir:$PATH" python3 "$HOOK" \
    >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      echo "$err" | grep -q "Squash-merge title too long" || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
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
    [ -n "$out" ] && echo "        stdout: $(echo "$out" | head -c 400)"
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -c 400)"
  fi
}

LONG_TITLE_GH=$(make_fake_gh_title "fix(momentum-gate): scope merge-briefing window to a very long prior turn")
SHORT_TITLE_GH=$(make_fake_gh_title "fix(hook): short title")
ERROR_GH=$(make_fake_gh_error)

run_case_gh "squash + long PR title (advisory)" \
  advisory "gh pr merge 42 --squash" "$LONG_TITLE_GH"

run_case_gh "squash short flag -s + long PR title (advisory)" \
  advisory "gh pr merge 42 -s" "$LONG_TITLE_GH"

run_case_gh "squash + short PR title (silent)" \
  silent "gh pr merge 42 --squash" "$SHORT_TITLE_GH"

run_case_gh "no --squash flag at all (silent, no gh call needed)" \
  silent "gh pr merge 42" "$ERROR_GH"

run_case_gh "-t/--subject overrides PR title, long (advisory, no gh call)" \
  advisory 'gh pr merge 42 --squash -t "this subject line is definitely far too long to pass fifty chars"' "$ERROR_GH"

run_case_gh "-t/--subject overrides PR title, short (silent, no gh call)" \
  silent 'gh pr merge 42 --squash -t "short title"' "$ERROR_GH"

run_case_gh "gh pr view errors -> fail-open (silent)" \
  silent "gh pr merge 42 --squash" "$ERROR_GH"

run_case_gh "gh -R owner/repo pr merge --squash (global flag before subcommand)" \
  advisory "gh -R owner/repo pr merge 42 --squash" "$LONG_TITLE_GH"

run_case_gh "opt-out marker suppresses squash advisory too (silent)" \
  silent "gh pr merge 42 --squash  # title-length:ack" "$LONG_TITLE_GH"

run_case_gh "gh api pulls/N/merge squash method (advisory, resolved via gh pr view)" \
  advisory "gh api repos/o/r/pulls/42/merge -X PUT -f merge_method=squash" "$LONG_TITLE_GH"

run_case_gh "gh api pulls/N/merge with explicit commit_title over limit (advisory, no gh view)" \
  advisory 'gh api repos/o/r/pulls/42/merge -X PUT -f merge_method=squash -f commit_title="this subject line is definitely far too long to pass fifty chars"' "$ERROR_GH"

run_case_gh "gh api pulls/N/merge merge_method=merge is silent (not squash)" \
  silent 'gh api repos/o/r/pulls/42/merge -X PUT -f merge_method=merge -f commit_title="this title can stay long because merge_method is merge"' "$ERROR_GH"

run_case_gh "gh pr merge --merge (no squash) is silent even with long title" \
  silent "gh pr merge 42 --merge" "$LONG_TITLE_GH"

run_case_gh "gh pr view (unrelated gh command) is silent" \
  silent "gh pr view 42 --json title" "$LONG_TITLE_GH"

# ---------------------------------------------------------------------------
echo ""
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"
  for name in "${FAILED_NAMES[@]}"; do
    echo "  - $name"
  done
  exit 1
fi
exit 0
