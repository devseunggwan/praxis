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

# #1092: a path-prefixed git binary must not slip past the override gate.
# `argv[0] == "git"` exact match previously let `/usr/bin/git commit
# --no-verify` bypass; `_is_git_binary` closes it.
run_case "B08: /usr/bin/git commit --no-verify (path-prefix, deny)" deny \
  "$(payload '/usr/bin/git commit --no-verify -m "msg"')"

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
# Bundled short-flag clusters carrying -n (=--no-verify) (#512). The prior
# exact COMMIT_FLAG_TOKENS match let clusters like `-vn`/`-anm` slip through;
# clean clusters (no `n`) must stay silent.
# ---------------------------------------------------------------------------

run_case "C01: git commit -vn -m msg (bundled no-verify)" deny \
  "$(payload 'git commit -vn -m "msg"')"

run_case "C02: git commit -nv -m msg (order swapped)" deny \
  "$(payload 'git commit -nv -m "msg"')"

run_case "C03: git commit -anm msg (-a -n -m bundled)" deny \
  "$(payload 'git commit -anm "msg"')"

run_case "C04: git commit -nm msg (-n -m bundled)" deny \
  "$(payload 'git commit -nm "msg"')"

# Clean bundled clusters with NO -n must pass.
run_case "C05: git commit -v -m msg (separate, no override)" silent \
  "$(payload 'git commit -v -m "msg"')"

run_case "C06: git commit -am msg (bundled, no override)" silent \
  "$(payload 'git commit -am "msg"')"

run_case "C07: git commit -vs -m msg (bundled value-less, no n)" silent \
  "$(payload 'git commit -vs -m "msg"')"

# `-mn` is `-m` with value `n` — the message, NOT a no-verify cluster.
run_case "C08: git commit -mn (message value 'n', not no-verify)" silent \
  "$(payload 'git commit -mn')"

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
# Sibling git-commit gate checklist on first block (issue #941)
# ---------------------------------------------------------------------------

# Positive: a deny surfaces the full sibling-gate checklist, not just this
# gate's own token.
deny_out=$(payload 'git commit -n -m "msg"' | \
  env -u PRAXIS_SKIP_COMMIT_FLAG_CHECK python3 "$HOOK" 2>/dev/null)
if echo "$deny_out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
reason = d['hookSpecificOutput']['permissionDecisionReason']
for token in (
    'block-commit-without-codex-review',
    'commit-title-format-check',
    'commit-title-length-check',
    'pre-commit-staged-file-enumeration',
):
    assert token in reason, f'missing {token!r} from deny message'
" 2>/dev/null; then
  echo "PASS: T01: deny message enumerates every sibling git-commit gate"
  PASS=$((PASS + 1))
else
  echo "FAIL: T01: deny message enumerates every sibling git-commit gate"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("T01")
fi

# Positive: the deny message states the reason+approval requirement
# explicitly — the bypass env var alone is not framed as sufficient.
if echo "$deny_out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
reason = d['hookSpecificOutput']['permissionDecisionReason']
assert 'BOTH of the following are required' in reason, f'missing reason+approval requirement: {reason}'
" 2>/dev/null; then
  echo "PASS: T01b: deny message requires BOTH reason and user approval"
  PASS=$((PASS + 1))
else
  echo "FAIL: T01b: deny message requires BOTH reason and user approval"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("T01b")
fi

# Negative contrast: no override detected → fully silent, no checklist leaks
# into a passing invocation.
silent_out=$(payload 'git commit -m "regular message"' | \
  env -u PRAXIS_SKIP_COMMIT_FLAG_CHECK python3 "$HOOK" 2>/dev/null)
if [ -z "$silent_out" ]; then
  echo "PASS: T02: no override → silent, checklist does not fire"
  PASS=$((PASS + 1))
else
  echo "FAIL: T02: no override → silent, checklist does not fire (out=$silent_out)"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("T02")
fi

# ---------------------------------------------------------------------------
# Host-aware checklist (issue #1154)
#
# Two of the four rows name hooks carrying hosts: ["claude"], so on any other
# host the unfiltered text pointed at gates that are not installed. The rows
# printed must therefore differ per host — a single-host assertion would pass
# on the broken build too.
# ---------------------------------------------------------------------------

# rows_for_host <host|"">  → the `← <hook>` names the checklist would print.
# An empty host argument means "not resolvable" (standalone run).
rows_for_host() {
  python3 - "$HOOK" "$1" << 'PYEOF'
import importlib.util, sys, re
spec = importlib.util.spec_from_file_location("impl_rows", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
host = sys.argv[2] or None
print(" ".join(sorted(re.findall(r"\u2190\s*([a-z0-9][a-z0-9-]*)",
                                 mod.render_gate_checklist(host)))))
PYEOF
}

ALL_ROWS="block-commit-without-codex-review commit-title-format-check commit-title-length-check pre-commit-staged-file-enumeration"
NON_CLAUDE_ROWS="commit-title-format-check commit-title-length-check"

run_rows_case() {
  local name="$1" host="$2" want="$3"
  local got
  got=$(rows_for_host "$host")
  if [ "$got" = "$want" ]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name (want='$want' got='$got')"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

run_rows_case "T03: claude keeps every row" claude "$ALL_ROWS"
run_rows_case "T04: codex drops the claude-only rows" codex "$NON_CLAUDE_ROWS"
run_rows_case "T05: cursor drops the claude-only rows" cursor "$NON_CLAUDE_ROWS"
# Negative control: an unknown host must NOT shrink the list — losing a gate
# the host does install is the worse failure of the two.
run_rows_case "T06: unresolvable host prints every row" "" "$ALL_ROWS"
run_rows_case "T07: unknown host id prints every row" not-a-host "$ALL_ROWS"

# The remedy prose under a dropped row goes with it, not just its header line.
codex_body=$(python3 - "$HOOK" << 'PYEOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("impl_body", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.render_gate_checklist("codex"))
PYEOF
)
if ! echo "$codex_body" | grep -q "CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE" &&
   ! echo "$codex_body" | grep -q "codex-review-wrap" &&
   echo "$codex_body" | grep -q "title-length:ack"; then
  echo "PASS: T08: dropped rows take their remedy lines with them"
  PASS=$((PASS + 1))
else
  echo "FAIL: T08: dropped rows take their remedy lines with them"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("T08")
fi

# Live path: the host reaches the hook only through the dispatcher's argv, so
# the branch above is only real if a dispatcher run reproduces it.
dispatch_rows() {
  local host="$1"
  payload 'git commit -n -m "fix: probe title"  # side-effect:ack' |     python3 "$ROOT_DIR/hooks/_lib/_dispatch.py" PreToolUse Bash "$host" 2>/dev/null |     python3 -c "
import json, re, sys
d = json.load(sys.stdin)
reason = d['hookSpecificOutput']['permissionDecisionReason']
assert 'BLOCKED: Commit-flag override' in reason, 'another gate won the decision'
print(' '.join(sorted(re.findall(r'\u2190\s*([a-z0-9][a-z0-9-]*)', reason))))
"
}

for _h in claude codex; do
  _want="$ALL_ROWS"
  [ "$_h" = "claude" ] || _want="$NON_CLAUDE_ROWS"
  _got=$(dispatch_rows "$_h")
  if [ "$_got" = "$_want" ]; then
    echo "PASS: T09[$_h]: dispatcher run prints the host's rows"
    PASS=$((PASS + 1))
  else
    echo "FAIL: T09[$_h]: dispatcher run prints the host's rows (want='$_want' got='$_got')"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("T09[$_h]")
  fi
done

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
