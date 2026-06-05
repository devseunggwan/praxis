#!/bin/bash
# tests/test_cross_boundary_preflight.sh
#
# Coverage for hooks/preflight-gate/cross-boundary-preflight/impl.py
#
# Three outcomes:
#   ask   — stdout contains permissionDecision "ask", exit 0
#   block — exit 2, stderr non-empty
#   pass  — exit 0, stdout empty, stderr empty
#
# Usage: bash tests/test_cross_boundary_preflight.sh
# Exit:  0 = all pass, 1 = at least one failure

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/preflight-gate/cross-boundary-preflight/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

mk_payload() {
  python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))
' "$1"
}

run_case() {
  local name="$1" expected="$2" command="$3"
  local out err_file err rc ok=1
  err_file=$(mktemp)
  out=$(mk_payload "$command" | "$HOOK" 2>"$err_file")
  rc=$?; err=$(cat "$err_file"); rm -f "$err_file"

  case "$expected" in
    ask)
      [ "$rc" -eq 0 ] || ok=0
      echo "$out" | grep -q '"permissionDecision": "ask"' || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      [ -n "$err" ]   || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$expected] $name"; PASS=$((PASS+1))
  else
    echo "FAIL  [$expected→rc=$rc,stdout=$([ -n "$out" ] && echo non-empty || echo empty),stderr=$([ -n "$err" ] && echo non-empty || echo empty)] $name"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# BLOCK cases — heredoc in same gh write command segment
# ---------------------------------------------------------------------------

run_case "heredoc in gh issue create" block \
  'gh issue create --title "foo" <<EOF'

run_case "heredoc single-quoted in gh issue create" block \
  "gh issue create --title \"foo\" <<'EOF'"

run_case "heredoc dash strip in gh pr create" block \
  'gh pr create --title "t" <<-EOF'

run_case "heredoc via global --repo before subcommand" block \
  'gh --repo owner/repo issue create --title "t" <<EOF'

# ---------------------------------------------------------------------------
# ASK cases — --repo flag in gh write command
# ---------------------------------------------------------------------------

run_case "gh pr create --repo" ask \
  'gh pr create --repo owner/repo --title "feat: x" --body-file /tmp/b.md'

run_case "gh issue create --repo" ask \
  'gh issue create --repo owner/repo --title "bug report"'

run_case "gh issue comment --repo" ask \
  'gh issue comment 42 --repo owner/repo --body "hello"'

run_case "gh issue edit --repo" ask \
  'gh issue edit 7 --repo owner/repo --title "updated"'

run_case "gh pr edit --repo" ask \
  'gh pr edit 5 --repo owner/repo --title "updated"'

run_case "gh -R shorthand" ask \
  'gh -R owner/repo pr create --title "fix" --body-file /tmp/b.md'

run_case "--repo= equals form" ask \
  'gh issue create --repo=owner/repo --title "test"'

run_case "global --repo before pr create" ask \
  'gh --repo owner/repo pr create --title "t" --body-file /tmp/b.md'

run_case "gh pr new --repo" ask \
  'gh pr new --repo owner/repo --title "t" --body-file /tmp/b.md'

run_case "chained: safe cmd && gh pr create --repo" ask \
  'git fetch origin && gh pr create --repo owner/repo --title "t" --body-file /tmp/b.md'

# ---------------------------------------------------------------------------
# ASK: checklist includes Caller chain item for pr create
# ---------------------------------------------------------------------------

run_case_detail() {
  local name="$1" command="$2" needle="$3"
  local out err_file rc ok=1
  err_file=$(mktemp)
  out=$(mk_payload "$command" | "$HOOK" 2>"$err_file")
  rc=$?; rm -f "$err_file"
  [ "$rc" -eq 0 ] || ok=0
  echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); r=d.get('hookSpecificOutput',{}).get('permissionDecisionReason',''); sys.exit(0 if '$needle' in r else 1)" 2>/dev/null || ok=0
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [ask-detail] $name"; PASS=$((PASS+1))
  else
    echo "FAIL  [ask-detail: missing '$needle'] $name"; FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

run_case_detail "pr create checklist has Caller chain item" \
  'gh pr create --repo owner/repo --title "t" --body-file /tmp/b.md' \
  "Caller chain verified"

run_case_detail "issue create checklist no Caller chain item" \
  'gh issue create --repo owner/repo --title "t"' \
  "body-file"

# ---------------------------------------------------------------------------
# Message content: new bullet 3 appears in HEREDOC_BLOCK_MSG stderr
# ---------------------------------------------------------------------------

run_case_block_msg() {
  local name="$1" command="$2" needle="$3"
  local out err_file err rc ok=1
  err_file=$(mktemp)
  out=$(mk_payload "$command" | "$HOOK" 2>"$err_file")
  rc=$?; err=$(cat "$err_file"); rm -f "$err_file"
  [ "$rc" -eq 2 ] || ok=0
  echo "$err" | grep -qF "$needle" || ok=0
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [block-msg] $name"; PASS=$((PASS+1))
  else
    echo "FAIL  [block-msg: missing '$needle'] $name"; FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

run_case_block_msg "heredoc block msg contains ack placement note" \
  'gh issue create --title "foo" <<EOF' \
  "never inside the heredoc body"

# ---------------------------------------------------------------------------
# F1 regression: gh issue --repo X create (--repo between object and verb)
# ---------------------------------------------------------------------------

run_case "gh issue --repo X create (F1 fix)" ask \
  'gh issue --repo owner/repo create --title "t"'

run_case "gh pr --repo X create (F1 fix)" ask \
  'gh pr --repo owner/repo create --title "t" --body-file /tmp/b.md'

run_case "gh issue --repo X create with heredoc (F1+F2)" block \
  'gh issue --repo owner/repo create --title "t" <<EOF'

# ---------------------------------------------------------------------------
# F2 regression: heredoc attached to preceding token without space
# ---------------------------------------------------------------------------

run_case "heredoc attached to --title value (F2 fix)" block \
  'gh issue create --title foo<<EOF'

run_case "heredoc attached with double quotes (F2 fix)" block \
  'gh issue create --title "foo"<<EOF'

run_case "heredoc attached to --body-file path (F2 fix)" block \
  'gh issue create --body-file /tmp/body.md<<EOF'

# literal << inside quoted body string — should pass
run_case "literal << in quoted body (F2 false-positive guard)" pass \
  'gh issue create --body "comparison: a << b is false"'

# ---------------------------------------------------------------------------
# PASS cases — no --repo, no heredoc in gh write segment
# ---------------------------------------------------------------------------

run_case "gh pr create without --repo" pass \
  'gh pr create --title "fix" --body "Caller chain verified: ok"'

run_case "gh issue create without --repo" pass \
  'gh issue create --title "bug" --body-file /tmp/b.md'

run_case "gh issue list --repo (read-only subcommand)" pass \
  'gh issue list --repo owner/repo --state open'

run_case "gh pr list --repo (read-only subcommand)" pass \
  'gh pr list --repo owner/repo'

run_case "gh search issues (handled by block-gh-state-all)" pass \
  'gh search issues --repo owner/repo --state open'

run_case "non-gh command with <<" pass \
  'cat <<EOF > /tmp/file.txt'

run_case "git command" pass \
  'git push origin main'

run_case "opt-out marker" pass \
  'gh pr create --repo owner/repo --title "t" --body-file /tmp/b.md  # cross-boundary:ack'

# Codex #224: marker placed inside heredoc body must NOT be honored as opt-out.
# Otherwise the marker leaks into the published artifact AND the hook bypasses
# its block. The new _strip_heredoc_bodies sanitizes the command before the
# OPT_OUT_MARKER lookup, so this case re-enters the heredoc block path.
run_case "marker inside heredoc body is rejected as opt-out" block \
  'gh issue create --title "t" <<EOF
body line
# cross-boundary:ack
EOF'

# Codex round 2 — numeric / single-char heredoc delimiter must also strip body
# (regex now accepts [A-Za-z0-9_]+ instead of identifier-only).
run_case "numeric heredoc delimiter: marker in body still blocks" block \
  'gh issue create --title "t" <<1
body line
# cross-boundary:ack
1'

# Codex round 2 — `<<` literal inside quoted body must NOT trigger heredoc
# block (quoted-string false positive). shlex preserves internal spaces,
# so tokens with spaces are necessarily quoted; their `<<` is literal.
# Use --repo + ask expectation so cross-boundary checklist still surfaces,
# proving the heredoc-block path did NOT fire on this command.
run_case "quoted body containing << literal does not block" ask \
  'gh issue create --repo owner/repo --title "t" --body "code: a<<b"'

# Codex round 3 — opt-out marker placed in shell command portion must NOT
# bypass the heredoc hard-block. The marker only opts out of the cross-repo
# checklist; heredoc bypasses caller-chain evidence regardless of marker.
run_case "marker outside heredoc body does not bypass heredoc block" block \
  'gh issue create --title "t" <<EOF
body line
EOF
# cross-boundary:ack'

# Codex round 3+4 known limitation: `--title "foo bar"<<EOF` tokenizes as
# `foo bar<<EOF` (quoted-token guard skips it). The round-3 raw-command
# heredoc detection was reverted in round 4 because it caused false positives
# on legitimate var-heredoc and file-prep patterns (`BODY=$(cat <<EOF ... EOF);
# gh ...`, `cat <<EOF > /tmp/body.md; gh issue create --body-file /tmp/body.md`).
# Tracked as follow-up; for now we accept the narrow miss (heredoc attached
# directly after a quoted argument value) to keep the hook usable.

# Round 4 regression guard — var-heredoc on a different segment must pass.
run_case "var-heredoc separate segment then gh body passes" pass \
  'BODY=$(cat <<EOF
some body
EOF
)
gh issue create --title "t" --body "$BODY"'

run_case "file-prep heredoc then gh body-file passes" pass \
  'cat <<EOF > /tmp/body.md
some body
EOF
gh issue create --title "t" --body-file /tmp/body.md'

# Variable-assigned heredoc followed by gh pr create — heredoc in different segment
run_case "var-heredoc then gh pr create passes" pass \
  'gh pr create --title "t" --body "$BODY"'

# ---------------------------------------------------------------------------
# Cascade-hint suffix (issue #229) — shared cascade advisory text appears in
# stderr when the heredoc block fires on a compound bash with a state-change.
# ---------------------------------------------------------------------------

_cascade_err=$(mk_payload 'echo seed > /tmp/x && gh pr create --title t <<EOF' | "$HOOK" 2>&1 >/dev/null)
if printf '%s' "$_cascade_err" | grep -q "PreToolUse rejection (block or denied ask) aborts ALL parts atomically"; then
  echo "PASS  [cascade hint on compound heredoc block]"; PASS=$((PASS+1))
else
  echo "FAIL  [cascade hint missing on compound heredoc block]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("cascade hint on heredoc block")
fi

# Single-command heredoc block: no compound chain → no cascade hint
_single_err=$(mk_payload 'gh pr create --title "t" <<EOF' | "$HOOK" 2>&1 >/dev/null)
if printf '%s' "$_single_err" | grep -q "PreToolUse rejection (block or denied ask) aborts ALL parts atomically"; then
  echo "FAIL  [cascade hint leaked on single-command heredoc block]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("cascade hint leaked single heredoc")
else
  echo "PASS  [no cascade hint on single-command heredoc block]"; PASS=$((PASS+1))
fi

# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

non_bash_out=$(echo '{"tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}' | "$HOOK" 2>/dev/null)
if [ -z "$non_bash_out" ]; then
  echo "PASS  [non-Bash passthrough]"; PASS=$((PASS+1))
else
  echo "FAIL  [non-Bash passthrough] got: $non_bash_out"; FAIL=$((FAIL+1)); FAILED_NAMES+=("non-Bash passthrough")
fi

bad_out=$(echo 'not-json' | "$HOOK" 2>/dev/null)
bad_rc=$?
if [ "$bad_rc" -eq 0 ] && [ -z "$bad_out" ]; then
  echo "PASS  [malformed JSON fail-open]"; PASS=$((PASS+1))
else
  echo "FAIL  [malformed JSON fail-open] rc=$bad_rc out=$bad_out"; FAIL=$((FAIL+1)); FAILED_NAMES+=("malformed JSON")
fi
# Fail-open guard opt-in (issue #498): main() must be @fail_open-wrapped;
# guard behavior is tested centrally in tests/test_hook_runtime.sh.
_failopen_out=$(python3 - << PYEOF 2>&1
import importlib.util
spec = importlib.util.spec_from_file_location("impl", "$REPO_ROOT/hooks/preflight-gate/cross-boundary-preflight/impl.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert getattr(mod.main, "__wrapped__", None) is not None, "main is not @fail_open-wrapped"
print("OK")
PYEOF
)
_failopen_rc=$?
if [ "$_failopen_rc" -eq 0 ] && [ "$_failopen_out" = "OK" ]; then
  echo "PASS  [fail-open] main() is wrapped by the shared @fail_open guard"
  PASS=$((PASS+1))
else
  echo "FAIL  [fail-open] main() not @fail_open-wrapped (rc=$_failopen_rc out=$_failopen_out)"
  FAIL=$((FAIL+1)); FAILED_NAMES+=("fail-open guard wrapping")
fi



# ---------------------------------------------------------------------------
echo
echo "=================================="
echo "  PASS: $PASS  FAIL: $FAIL"
echo "=================================="
if [ "$FAIL" -gt 0 ]; then
  printf '  failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
