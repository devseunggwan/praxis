#!/bin/bash
# test_fallback_negative_warn.sh — coverage for the advisory hook.
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory → exit 0 + stderr contains the advisory marker
#   silent   → exit 0 + stderr empty
#
# Usage: bash tests/hooks/advisory-nudge/test_fallback_negative_warn.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/fallback-negative-warn/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expected command
#   expected: "advisory"  (exit 0, stderr contains the marker substring)
#             "silent"    (exit 0, stderr empty)
run_case() {
  local name="$1" expected="$2" command="$3"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}))' "$command")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  echo "$payload" | "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      echo "$err" | grep -q "\[fallback-negative-warn\]" || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    *)
      echo "FAIL  [$name] unknown expected: $expected"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc"
    [ -n "$out" ] && echo "        stdout: $out"
    [ -n "$err" ] && echo "        stderr: $err"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# === ADVISORY paths (all 3 conditions hold) =================================

# Regression — issue #893's own motivating incident: an in-flight-session
# gate written in exactly this shape used a subcommand that never existed;
# the failure and the true-negative were indistinguishable in the output.
run_case "issue #893 motivating pattern" \
  advisory \
  'session-cli status 2>/dev/null | grep -i active || echo "(no session)"'

run_case "condition scoped across a pipeline (redirect before pipe, || after)" \
  advisory \
  'cmd 2>/dev/null | grep -i key || echo "(no session)"'

run_case "combined redirect: >/dev/null 2>&1 (order that actually suppresses stderr)" \
  advisory \
  'cmd >/dev/null 2>&1 || printf "none\n"'

# Regression — codex review round 1 F1: `2>&1 >/dev/null` (dup-then-
# redirect order), with NO pipe following, does NOT actually suppress
# stderr. `2>&1` dups fd2 onto fd1's CURRENT target (the original stdout,
# e.g. the Bash tool's captured output) BEFORE `>/dev/null` moves fd1
# elsewhere — fd2 keeps pointing at the original target, so the error
# text stays visible. Verified live: `bogus_cmd 2>&1 >/dev/null || echo
# none` prints the "command not found" error.
run_case "dup-then-redirect order, no pipe: stderr stays visible (no advisory)" \
  silent \
  'cmd 2>&1 >/dev/null || echo "none"'

# Regression — codex review round 2 F1: the SAME dup-then-redirect order
# DOES effectively hide stderr when a pipe follows — fd2 is duped onto
# fd1's current target, which is now the pipe's write end, so the error
# text is folded into the pipeline's input instead of reaching the
# terminal/tool output. If the downstream filter doesn't match it, it's
# indistinguishable from a genuine 0-match. Verified live: `bogus_cmd
# 2>&1 >/dev/null | grep -i hello || echo "(no session)"` prints ONLY
# "(no session)" — the error text never appears anywhere.
run_case "dup-then-redirect order WITH a following pipe: stderr is hidden in the pipe" \
  advisory \
  'cmd 2>&1 >/dev/null | grep -i key || echo "no match found"'

# Regression — codex review round 1 F2: a legal space between the
# redirect operator and its target (`2> /dev/null`) tokenizes as two
# separate words, unlike the fused `2>/dev/null` form.
run_case "whitespace-separated stderr redirect: 2> /dev/null" \
  advisory \
  'cmd 2> /dev/null | grep -i key || echo "(no session)"'

# Regression — codex review round 2 F2: the combined redirect also has a
# legal whitespace-separated form for the bare `>` (stdout) operator —
# `> /dev/null 2>&1` — not just the fused `>/dev/null 2>&1`. Verified
# live: `bogus_cmd > /dev/null 2>&1 || echo none` fully suppresses the
# error text (same semantics as the fused form).
run_case "whitespace-separated combined redirect: > /dev/null 2>&1" \
  advisory \
  'cmd > /dev/null 2>&1 || echo "none"'

run_case "&&-separated statement shares scope with the following ||" \
  advisory \
  'cd /worktree && cmd 2>/dev/null | grep foo || echo "(no session)"'

run_case "empty vocabulary" \
  advisory \
  'cmd 2>/dev/null || echo "empty"'

run_case "skip vocabulary" \
  advisory \
  'cmd 2>/dev/null || echo "skip"'

run_case "(0 vocabulary" \
  advisory \
  'cmd 2>/dev/null || echo "(0 results)"'

run_case "none vocabulary via printf" \
  advisory \
  'cmd 2>/dev/null || printf "none"'

run_case "Korean 없 vocabulary" \
  advisory \
  'cmd 2>/dev/null || echo "결과 없음"'

# === SILENT paths (issue #893 required cases) ===============================

# Condition 1+2 only, neutral fallback text — issue #893's own required
# silent case.
run_case "condition 1+2 only, neutral fallback text" \
  silent \
  'cmd 2>/dev/null || echo "(retrying)"'

# Condition 1 alone.
run_case "2>/dev/null alone (no ||)" \
  silent \
  'cmd 2>/dev/null'

# Condition 2 alone (no redirect at all).
run_case "|| true alone (no redirect)" \
  silent \
  'cmd || true'

# Condition 1+2, fallback is `true` — true never carries a message, so
# condition 3 can never hold.
run_case "redirect + || true (true has no message)" \
  silent \
  'cmd 2>/dev/null | grep foo || true'

run_case "0 matches is not (0 (issue's literal vocabulary list)" \
  silent \
  'cmd 2>/dev/null || echo "0 matches"'

run_case "neutral fallback text, no negative vocabulary" \
  silent \
  'git status 2>/dev/null | grep -i foo || echo "not the target"'

run_case "fresh statement after ; does not inherit prior redirect" \
  silent \
  'cmd 2>/dev/null; other-cmd || echo "(no session)"'

run_case "redirect present but fallback binary is not echo/printf/true" \
  silent \
  'cmd 2>/dev/null || false'

# === SILENT paths (quoted-literal / example-text false-positive guard) =====

run_case "quoted-string literal in echo argument, not a live pipeline" \
  silent \
  'echo "example: cmd 2>/dev/null || echo (none)"'

run_case "quoted-string literal inside gh issue create --body" \
  silent \
  'gh issue create --body "example: cmd 2>/dev/null || echo (none)"'

# Documented known limitation (codex review round 2 F3, accepted — see
# spec.md "Known limitations"): safe_tokenize dequotes before this hook
# ever sees the tokens, so a quoted argument whose ENTIRE content happens
# to equal a redirect token is indistinguishable from a live redirect.
# pipefail-advisory carries the identical exposure for its own operators
# and documents rather than fixes it (shared-tokenizer quote-provenance
# loss, out of scope for an advisory-only hook). This is a false-positive
# ADVISORY, not silent — asserting the accepted behavior so a future
# tokenizer change that fixes it is caught as a test change, not a
# silent regression.
run_case "known limitation: quoted arg exactly matching a redirect token (accepted false positive)" \
  advisory \
  "gh issue create --body '2>/dev/null' || echo 'none'"

# === Infrastructure passthrough ============================================

run_case "empty command" \
  silent \
  ""

# Non-Bash tool → silent pass.
ipayload=$(python3 -c '
import json
print(json.dumps({
    "tool_name": "Write",
    "tool_input": {"command": "cmd 2>/dev/null || echo none"},
}))')
i_out=$(mktemp); i_err=$(mktemp)
echo "$ipayload" | "$HOOK" >"$i_out" 2>"$i_err"
i_rc=$?
i_out_v=$(cat "$i_out"); i_err_v=$(cat "$i_err")
rm -f "$i_out" "$i_err"
if [ "$i_rc" -eq 0 ] && [ -z "$i_out_v" ] && [ -z "$i_err_v" ]; then
  echo "PASS  [non-Bash passthrough]"; PASS=$((PASS + 1))
else
  echo "FAIL  [non-Bash passthrough] rc=$i_rc out='$i_out_v' err='$i_err_v'"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-Bash passthrough")
fi

# Malformed JSON → fail-open.
mpayload="{not-json"
m_out=$(mktemp); m_err=$(mktemp)
echo "$mpayload" | "$HOOK" >"$m_out" 2>"$m_err"
m_rc=$?
m_out_v=$(cat "$m_out"); m_err_v=$(cat "$m_err")
rm -f "$m_out" "$m_err"
if [ "$m_rc" -eq 0 ] && [ -z "$m_out_v" ] && [ -z "$m_err_v" ]; then
  echo "PASS  [malformed JSON fail-open]"; PASS=$((PASS + 1))
else
  echo "FAIL  [malformed JSON fail-open] rc=$m_rc out='$m_out_v' err='$m_err_v'"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed JSON fail-open")
fi

# ---------------------------------------------------------------------------
# @fail_open structural assertion
# ---------------------------------------------------------------------------

_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if getattr(mod.main, "__wrapped__", None) is None:
    sys.stderr.write("main not wrapped by @fail_open\n"); sys.exit(1)
PYEOF
)
_uncaught_rc=$?
if [ "$_uncaught_rc" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "  PASS  main() is wrapped by the shared @fail_open guard (exit 0, no stderr)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  main() not wrapped by @fail_open (rc=$_uncaught_rc, out=$(echo "$_uncaught_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("main() is wrapped by the shared @fail_open guard")
fi

echo
echo "----"
echo "Passed: $PASS"
echo "Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
