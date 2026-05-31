#!/bin/bash
# test_verify_commit_flag_override.sh — coverage for hooks/preflight-gate/verify-commit-flag-override/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   deny   → exit 2 + stdout JSON has permissionDecision "deny"
#   silent → exit 0 + stdout empty (no JSON, no permissionDecision)
#
# Coverage focuses on the lexical false-positive cases that motivated the
# port from a project-local hook (see #184): the prior regex-based
# implementation matched `-n` as a bare substring anywhere in the command,
# so heredoc bodies, echo arguments, head/sed/grep flags, and command
# substitutions all tripped it. The shlex tokenization here must not
# repeat that mistake.
#
# Usage: bash tests/test_verify_commit_flag_override.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/verify-commit-flag-override/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation payload_json
#   expectation:
#     "deny"   — stdout JSON has permissionDecision "deny", rc=2
#     "silent" — stdout empty, rc=0
run_case() {
  local name="$1" expectation="$2" payload="$3"

  local out_file
  out_file=$(mktemp)

  # Unset bypass env so the test exercises the real detection path.
  echo "$payload" | PRAXIS_SKIP_COMMIT_FLAG_CHECK='' python3 "$HOOK" >"$out_file" 2>/dev/null
  local rc=$?
  local out
  out=$(cat "$out_file")
  rm -f "$out_file"

  local ok=1
  case "$expectation" in
    deny)
      [ "$rc" -eq 2 ] || ok=0
      echo "$out" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    decision = d.get('hookSpecificOutput', {}).get('permissionDecision', '')
    sys.exit(0 if decision == 'deny' else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ] || ok=0
      ;;
    *)
      echo "UNKNOWN expectation: $expectation" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name (rc=$rc, out=$out)"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# helper: build a Bash PreToolUse payload with the given command string.
payload() {
  python3 -c "
import json, sys
print(json.dumps({
    'tool_name': 'Bash',
    'tool_input': {'command': sys.argv[1]},
}))
" "$1"
}

# ---------------------------------------------------------------------------
# Block cases (must deny)
# ---------------------------------------------------------------------------

run_case "B01: git commit -n -m msg" deny \
  "$(payload 'git commit -n -m "msg"')"

run_case "B02: git commit --no-verify -m msg" deny \
  "$(payload 'git commit --no-verify -m "msg"')"

run_case "B03: git -c commit.gpgsign=false commit -m msg" deny \
  "$(payload 'git -c commit.gpgsign=false commit -m "msg"')"

run_case "B04: git commit --no-gpg-sign -m msg" deny \
  "$(payload 'git commit --no-gpg-sign -m "msg"')"

run_case "B05: git commit -S -m msg" deny \
  "$(payload 'git commit -S -m "msg"')"

run_case "B06: git -c core.hooksPath=/tmp/x commit -m msg" deny \
  "$(payload 'git -c core.hooksPath=/tmp/x commit -m "msg"')"

run_case "B07: short combined -Skeyid" deny \
  "$(payload 'git commit -Sabc123 -m "msg"')"

# ---------------------------------------------------------------------------
# Pass cases (must NOT deny) — these are the lexical false-positives the
# port is meant to eliminate. The prior regex-based hook tripped on every
# one of these.
# ---------------------------------------------------------------------------

run_case "P01: echo -n followed by git commit (no override)" silent \
  "$(payload 'echo -n "len" | wc -c && git commit -m "msg"')"

run_case "P02: head -n 5 then git commit (no override)" silent \
  "$(payload 'head -n 5 file && git commit -m "msg"')"

run_case "P03: grep -n in pipe before commit" silent \
  "$(payload 'grep -n pattern file && git commit -m "msg"')"

run_case "P04: sed -n inside message body" silent \
  "$(payload 'git commit -m "Premise-Verified: sed -n 35,55p src/cli/_common.py"')"

run_case "P05: -n inside command substitution body" silent \
  "$(payload 'git commit -m "$([ -n \"\$X\" ] && echo a || echo b)"')"

run_case "P06: git commit -F- with heredoc body containing -n" silent \
  "$(payload '
git commit -F- <<EOF
Premise-Verified: ran sed -n 35,55p file
EOF
')"

run_case "P07: plain git commit -m" silent \
  "$(payload 'git commit -m "regular message"')"

run_case "P08: git log -n 5 (not commit)" silent \
  "$(payload 'git log -n 5')"

run_case "P09: not a git command at all" silent \
  "$(payload 'tail -n 10 file.txt')"

run_case "P10: gh issue create with --body containing -n example" silent \
  "$(payload 'gh issue create --body "use sed -n to read lines"')"

# ---------------------------------------------------------------------------
# Value-bearing git globals before `commit` (Codex review P1 followup).
#
# `git -C <path> commit ...` / `git --git-dir <path> commit ...` etc.
# The prior implementation advanced one token on any `-`-prefixed flag,
# letting the value get misread as the subcommand and bailing out before
# the override scan ran.
# ---------------------------------------------------------------------------

run_case "G01: git -C /tmp commit --no-verify" deny \
  "$(payload 'git -C /tmp commit --no-verify -m "msg"')"

run_case "G02: git --git-dir /tmp/foo commit -n" deny \
  "$(payload 'git --git-dir /tmp/foo commit -n -m "msg"')"

run_case "G03: git --git-dir=/tmp/foo commit --no-verify (= form)" deny \
  "$(payload 'git --git-dir=/tmp/foo commit --no-verify -m "msg"')"

run_case "G04: git --work-tree /tmp commit --no-gpg-sign" deny \
  "$(payload 'git --work-tree /tmp commit --no-gpg-sign -m "msg"')"

run_case "G05: git -C /tmp -c commit.gpgsign=false commit" deny \
  "$(payload 'git -C /tmp -c commit.gpgsign=false commit -m "msg"')"

run_case "G06: git -C /tmp commit -S (force sign)" deny \
  "$(payload 'git -C /tmp commit -S -m "msg"')"

# Sanity: value-bearing global + non-commit subcommand must NOT deny.
run_case "G07: git -C /tmp log (not commit)" silent \
  "$(payload 'git -C /tmp log -n 5')"

# ---------------------------------------------------------------------------
# Bare --gpg-sign long form (Codex review P2 followup).
#
# Prior matching covered `-S`, `-S<keyid>` (via startswith), and
# `--gpg-sign=<keyid>`. Bare `--gpg-sign` (no keyid attached, no separate
# argument) fell through to allow.
# ---------------------------------------------------------------------------

run_case "S01: bare --gpg-sign" deny \
  "$(payload 'git commit --gpg-sign -m "msg"')"

run_case "S02: --gpg-sign after -m" deny \
  "$(payload 'git commit -m "msg" --gpg-sign')"

run_case "S03: --gpg-sign=DEADBEEF (keyid form, regression)" deny \
  "$(payload 'git commit --gpg-sign=DEADBEEF -m "msg"')"

# Sanity: --gpg-sign text inside message body must NOT deny.
run_case "S04: --gpg-sign text inside -m body" silent \
  "$(payload 'git commit -m "discuss --gpg-sign policy in docs"')"

# ---------------------------------------------------------------------------
# Bypass case (PRAXIS_SKIP_COMMIT_FLAG_CHECK=1 must short-circuit to pass)
# ---------------------------------------------------------------------------

bypass_payload=$(payload 'git commit -n -m "msg"')
bypass_out=$(mktemp)
PRAXIS_SKIP_COMMIT_FLAG_CHECK=1 echo "$bypass_payload" | PRAXIS_SKIP_COMMIT_FLAG_CHECK=1 python3 "$HOOK" >"$bypass_out" 2>/dev/null
bypass_rc=$?
bypass_content=$(cat "$bypass_out")
rm -f "$bypass_out"

if [ "$bypass_rc" -eq 0 ] && [ -z "$bypass_content" ]; then
  echo "PASS: X01: PRAXIS_SKIP_COMMIT_FLAG_CHECK=1 bypasses block"
  PASS=$((PASS + 1))
else
  echo "FAIL: X01: PRAXIS_SKIP_COMMIT_FLAG_CHECK=1 bypasses block (rc=$bypass_rc, out=$bypass_content)"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("X01")
fi

# ---------------------------------------------------------------------------
# Summary
# Fail-open guard opt-in (issue #498): main() must be @fail_open-wrapped;
# guard behavior is tested centrally in tests/test_hook_runtime.sh.
_failopen_out=$(python3 - << PYEOF 2>&1
import importlib.util
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
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
echo "==========================="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
