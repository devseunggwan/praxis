#!/bin/bash
# test_block_unmatched_glob.sh — coverage for block-unmatched-glob.sh
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   block → exit 2 + stderr non-empty
#   pass  → exit 0 + stderr empty
#
# Cases run against a temporary fixture tree so that "matches" and
# "matches nothing" are both deterministic, independent of the checkout.
#
# Usage: bash tests/hooks/preflight-gate/test_block_unmatched_glob.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export ROOT_DIR
HOOK="$ROOT_DIR/hooks/preflight-gate/block-unmatched-glob/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

# The gate delegates every verdict to a real zsh and fails open without one, so
# each blocking assertion below would silently become a fail-open assertion.
# Skip loudly rather than report 14 unexplained failures (CI installs zsh).
if ! command -v zsh >/dev/null 2>&1; then
  # stdout marker so run-tests.sh folds this skip into strict mode (#1170).
  echo "PRAXIS_SUBSKIP: zsh $0"
  echo "SKIPPED: zsh not available — block-unmatched-glob has nothing to delegate to"
  exit 0
fi

# The gate reads $SHELL to decide whether the executing shell is zsh at all.
# CI runners report bash, so without this every blocking assertion would test
# the non-zsh pass-through path instead. The portability block near the end
# overrides SHELL per invocation and is unaffected.
SHELL=$(command -v zsh)
export SHELL

FIXTURE=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$FIXTURE"' EXIT
mkdir -p "$FIXTURE/logs" "$FIXTURE/nested/deep"
: >"$FIXTURE/logs/alpha.log"
: >"$FIXTURE/logs/beta.log"
: >"$FIXTURE/nested/deep/found.txt"

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expected tool_name command
#   expected: "block" (exit 2, stderr non-empty) | "pass" (exit 0, stderr empty)
run_case() {
  local name="$1" expected="$2" tool_name="$3" command="$4"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": sys.argv[1],
    "tool_input": {"command": sys.argv[2]},
    "cwd": sys.argv[3],
}))' "$tool_name" "$command" "$FIXTURE")

  local err_file
  err_file=$(mktemp)
  echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  if [ "$expected" = "block" ]; then
    [ "$rc" = 2 ] && [ -n "$err" ] || ok=0
  else
    [ "$rc" = 0 ] && [ -z "$err" ] || ok=0
  fi

  if [ "$ok" = 1 ]; then
    PASS=$((PASS + 1))
    printf '  ok   %s\n' "$name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    printf '  FAIL %s (expected=%s rc=%s stderr_len=%s)\n' \
      "$name" "$expected" "$rc" "${#err}"
  fi
}

echo "== block-unmatched-glob =="

# --- blocking cases: the shell would abort these before they run -------------
run_case "absolute glob matching nothing" block Bash \
  "ls -d $FIXTURE/nosuchdir/*.json"
run_case "relative glob matching nothing" block Bash \
  "cat *.nonexistent-xyz"
run_case "unquoted find -name (aborts before find runs)" block Bash \
  "find $FIXTURE -maxdepth 1 -name *.nonexistent-xyz"
# Deliberate false negative: a pipeline is compound, so the gate passes it
# through rather than guess which segment runs where. zsh does abort this.
run_case "compound command passes through (known miss)" pass Bash \
  "echo start | cat $FIXTURE/nope/*.txt"
run_case "intermediate directory component unmatched" block Bash \
  "ls $FIXTURE/*/absent-leaf"
run_case "original incident shape (multi-arg ls with 2>/dev/null)" block Bash \
  "ls -d $FIXTURE/logs $FIXTURE/*/resume 2>/dev/null"

# --- passing cases ----------------------------------------------------------
run_case "glob that matches" pass Bash \
  "ls $FIXTURE/logs/*.log"
run_case "multi-level glob that matches" pass Bash \
  "ls $FIXTURE/*/deep/*.txt"
run_case "single-quoted pattern is never expanded" pass Bash \
  "find $FIXTURE -maxdepth 1 -name '*.nonexistent-xyz'"
run_case "double-quoted pattern is never expanded" pass Bash \
  "grep -r \"*.nonexistent-xyz\" $FIXTURE"
run_case "zsh nullglob qualifier makes zero matches legal" pass Bash \
  "ls $FIXTURE/*.nonexistent-xyz(N)"
run_case "no glob metacharacters at all" pass Bash \
  "git status"
run_case "inert pattern inside a quoted echo" pass Bash \
  "echo \"no matches for *.nonexistent-xyz\""
run_case "non-Bash tool is ignored" pass Read \
  "$FIXTURE/*.nonexistent-xyz"

# --- regression: cases where a pure-Python model disagreed with live zsh ----
# Every case below was verified against `zsh -f` before being asserted here.
run_case "test builtin bracket is not a glob" pass Bash \
  "[ -d . ]"
run_case "double-bracket conditional is not a glob" pass Bash \
  "[[ -d . ]]"
run_case "arithmetic expansion is passed through" pass Bash \
  "echo \$((2*3))"
run_case "pattern inside a trailing comment" pass Bash \
  "echo ok # *.missing"
run_case "variable prefix is unresolvable → pass" pass Bash \
  "ls \$HOME/*"
run_case "recursive ** is zsh syntax, resolved by zsh" pass Bash \
  "echo $FIXTURE/**/found.txt"
run_case "noglob disables the failure" pass Bash \
  "noglob print *.nonexistent-xyz"
run_case "setopt in-command changes glob behavior → pass" pass Bash \
  "setopt nullglob; print *.nonexistent-xyz"
run_case "brace expansion with no match still aborts" block Bash \
  "echo {logs,nested}/*.nonexistent-xyz"
run_case "cd in a compound command passes through" pass Bash \
  "cd $FIXTURE && echo *.nonexistent-xyz"
run_case "unexecuted branch passes through" pass Bash \
  "true || echo *.nonexistent-xyz"
run_case "if/then body passes through" pass Bash \
  "if false; then echo *.nonexistent-xyz; fi"
run_case "assignment value is not glob-expanded" pass Bash \
  "FOO=*.nonexistent-xyz print ok"
run_case "flag-attached glob is expanded by the shell" block Bash \
  "grep -r --include=*.nonexistent-xyz needle $FIXTURE"
run_case "mixed quoting inside one word" block Bash \
  "echo $FIXTURE/logs/alpha*\".nonexistent-xyz\""
# Same trade-off: `;` makes this compound. The per-occurrence qualifier logic
# is still exercised by the single-command case below.
run_case "sequenced command passes through (known miss)" pass Bash \
  "print *.nonexistent-xyz(N); print *.nonexistent-xyz"
run_case "qualifier applies to its own occurrence only" pass Bash \
  "print *.nonexistent-xyz(N)"

# --- regression: word position decides meaning (round 6) --------------------
# Every case below was verified against live zsh before being asserted here.
run_case "prefix assignment does not shield the rest of the command" block Bash \
  "LC_ALL=C ls $FIXTURE/nosuchdir/*.json"
run_case "disabler word as an argument is not a disabler" block Bash \
  "echo noglob $FIXTURE/nosuchdir/*.json"
run_case "control word as an argument is not control flow" block Bash \
  "echo cd $FIXTURE/nosuchdir/*.json"
run_case "quoted pipe is not a pipeline" block Bash \
  "grep 'a|b' $FIXTURE/nosuchdir/*.json"
run_case "single-quoted dollar is not an expansion" block Bash \
  "grep '\$value' $FIXTURE/nosuchdir/*.json"
run_case "double-quoted dollar still is an expansion" pass Bash \
  "grep \"\$value\" $FIXTURE/nosuchdir/*.json"
run_case "disabler in command position still passes through" pass Bash \
  "noglob print $FIXTURE/nosuchdir/*.json"
run_case "assignment-only word is still not expanded" pass Bash \
  "FOO=$FIXTURE/nosuchdir/*.json print ok"

# The executing shell's glob options must reach the probe: under
# `setopt extendedglob`, `^<something>` is a negation pattern that DOES match
# here, so a probe running plain `zsh -f` would wrongly report no matches.
if zsh -f -c 'setopt extendedglob' 2>/dev/null; then
  if python3 - "$FIXTURE" <<'PYEOF'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location(
    "h", os.path.join(os.environ["ROOT_DIR"],
                      "hooks/preflight-gate/block-unmatched-glob/impl.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
span = "logs/^*.nonexistent-xyz"
without = m.zsh_finds_no_match(span, sys.argv[1])
with_opt = m.zsh_finds_no_match(span, sys.argv[1], options=("extendedglob",))
# Without the option the pattern is literal and matches nothing; with it, the
# negation matches both fixture logs. Divergence proves the option is honored.
sys.exit(0 if (without and not with_opt) else 1)
PYEOF
  then
    PASS=$((PASS + 1)); printf '  ok   %s\n' "probe honors a forwarded glob option (extendedglob)"
  else
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("probe honors forwarded glob option")
    printf '  FAIL %s\n' "probe honors a forwarded glob option (extendedglob)"
  fi
fi

# --- security: a preflight gate must never execute the command it inspects --
# zsh glob qualifiers can carry code (`*(e:'cmd':)`), which the probe would
# otherwise run before the permission boundary. The side effect would land in
# the payload cwd — FIXTURE — so that is the only directory worth asserting on.
run_case "qualified pattern is never probed" pass Bash \
  "ls *(e:'touch SIDE_EFFECT':)"
if [ -e "$FIXTURE/SIDE_EFFECT" ]; then
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("qualifier code executed during probe")
  printf '  FAIL %s\n' "qualifier code executed during probe ($FIXTURE/SIDE_EFFECT created)"
  rm -f "$FIXTURE/SIDE_EFFECT"
else
  PASS=$((PASS + 1)); printf '  ok   %s\n' "no side effect from qualifier probe"
fi

# Guard the guard: with the qualifier skip removed the probe DOES execute the
# body, so this asserts the check above can actually fail.
python3 - "$FIXTURE" <<'PYEOF'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location(
    "h", os.path.join(os.environ["ROOT_DIR"],
                      "hooks/preflight-gate/block-unmatched-glob/impl.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.zsh_finds_no_match("*(e:'touch CANARY':)", sys.argv[1])
PYEOF
if [ -e "$FIXTURE/CANARY" ]; then
  PASS=$((PASS + 1)); printf '  ok   %s\n' "canary confirms the probe would execute an unskipped qualifier"
  rm -f "$FIXTURE/CANARY"
else
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("canary did not fire")
  printf '  FAIL %s\n' "canary did not fire — the security test cannot detect a regression"
fi

# --- conditional expressions ------------------------------------------------
run_case "pattern inside [[ ]] is a match operand" pass Bash \
  "[[ abc = *.nonexistent-xyz ]]"

# --- portability: the premise is zsh-specific -------------------------------
# bash and fish hand the literal pattern to the command, which then runs and
# whose stderr `2>/dev/null` does suppress. Blocking there is a false positive.
for shell_path in /bin/bash /usr/bin/fish ""; do
  label="SHELL=${shell_path:-<unset>} does not block"
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "cwd": sys.argv[2],
}))' "ls -d $FIXTURE/nosuchdir/*.json" "$FIXTURE")
  err_file=$(mktemp)
  if [ -n "$shell_path" ]; then
    echo "$payload" | SHELL="$shell_path" "$HOOK" >/dev/null 2>"$err_file"
  else
    echo "$payload" | env -u SHELL "$HOOK" >/dev/null 2>"$err_file"
  fi
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"
  if [ "$rc" = 0 ] && [ -z "$err" ]; then
    PASS=$((PASS + 1)); printf '  ok   %s\n' "$label"
  else
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$label")
    printf '  FAIL %s (rc=%s stderr_len=%s)\n' "$label" "$rc" "${#err}"
  fi
done
# Guard the guard: the very same payload under a zsh SHELL must still block, or
# the three assertions above would pass for the wrong reason.
err_file=$(mktemp)
echo "$payload" | SHELL=/bin/zsh "$HOOK" >/dev/null 2>"$err_file"
rc=$?; err=$(cat "$err_file"); rm -f "$err_file"
if [ "$rc" = 2 ] && [ -n "$err" ]; then
  PASS=$((PASS + 1)); printf '  ok   %s\n' "same payload under SHELL=zsh still blocks"
else
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("zsh control case")
  printf '  FAIL %s (rc=%s)\n' "same payload under SHELL=zsh still blocks" "$rc"
fi

# --- fail-open --------------------------------------------------------------
err_file=$(mktemp)
printf 'not json' | "$HOOK" >/dev/null 2>"$err_file"
rc=$?
err=$(cat "$err_file")
rm -f "$err_file"
if [ "$rc" = 0 ] && [ -z "$err" ]; then
  PASS=$((PASS + 1))
  printf '  ok   %s\n' "malformed stdin fails open"
else
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("malformed stdin fails open")
  printf '  FAIL %s (rc=%s)\n' "malformed stdin fails open" "$rc"
fi

echo
echo "pass=$PASS fail=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf 'failed: %s\n' "${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
