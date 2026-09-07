#!/bin/bash
# test_pipefail_advisory.sh — coverage for the pipefail advisory (issue #788).
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory → exit 0 + stderr contains the advisory marker
#   silent   → exit 0 + stderr empty
#
# Usage: bash tests/hooks/advisory-nudge/test_pipefail_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/pipefail-advisory/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

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
  # `env -u`: these cases pin the DEFAULT arm, so an inherited
  # PRAXIS_PIPEFAIL_ADVISORY_CONTEXT from the caller's shell must not turn the
  # #874 treatment arm on underneath them (every `advisory`/`silent` case
  # asserts empty stdout).
  echo "$payload" | env -u PRAXIS_PIPEFAIL_ADVISORY_CONTEXT python3 "$HOOK" \
    >"$out_file" 2>"$err_file"
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
      echo "$err" | grep -q "\[pipefail-advisory\]" || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    # The two detectors share the `[pipefail-advisory]` marker, so
    # `advisory` above cannot tell them apart — and telling them apart is
    # the whole assertion for issue #1271: a case must reach the detector
    # it was written for, not merely produce some advisory. `gating` and
    # `pipe` pin the headline instead of the marker.
    gating)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      echo "$err" | grep -q "masked exit code gates an irreversible command" || ok=0
      ;;
    pipe)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      echo "$err" | grep -q "mutating command piped without" || ok=0
      echo "$err" | grep -q "masked exit code gates" && ok=0
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

# === ADVISORY — mutating command piped to tail/head/grep ==================

run_case "git commit | tail (gen-1 pattern)" \
  advisory \
  "git commit -m x | tail"

run_case "gh pr merge 2>&1 | tail -3 (gen-2 pattern)" \
  advisory \
  "gh pr merge 123 --squash 2>&1 | tail -3"

run_case "git push 2>&1 | tail -3" \
  advisory \
  "git push origin main 2>&1 | tail -3"

run_case "git merge | tail" \
  advisory \
  "git merge feature-branch | tail -5"

run_case "git rebase | tail" \
  advisory \
  "git rebase origin/main | tail -10"

run_case "git cherry-pick | tail" \
  advisory \
  "git cherry-pick abc123 | tail"

run_case "git revert | tail" \
  advisory \
  "git revert HEAD | tail"

run_case "gh issue create | head" \
  advisory \
  "gh issue create --title x --body y | head"

run_case "gh issue close | grep" \
  advisory \
  "gh issue close 42 | grep -i done"

run_case "gh release create | tail" \
  advisory \
  "gh release create v1.0 | tail"

run_case "gh label create | tail" \
  advisory \
  "gh label create bug --color ff0000 | tail"

run_case "gh workflow run | tail" \
  advisory \
  "gh workflow run ci.yml | tail"

run_case "3-segment chain: git commit | tee | tail" \
  advisory \
  "git commit -m x | tee /tmp/log | tail -20"

run_case "git -C dir commit | tail (global flag)" \
  advisory \
  "git -C /tmp/repo commit -m x | tail"

# === ADVISORY — subshell / substitution / |& prefix recovery (codex review) ==

run_case "OUT=\$(gh pr merge ... | tail) (assignment+subst prefix)" \
  advisory \
  'OUT=$(gh pr merge 123 | tail -3)'

run_case "(git commit ... | tail) (compact subshell, bare sink)" \
  advisory \
  "(git commit -m x | tail)"

run_case "(git commit ... | tail -3) (compact subshell, sink w/ args)" \
  advisory \
  "(git commit -m x | tail -3)"

run_case "gh pr merge 1 |& tail -3 (pipe-with-stderr operator)" \
  advisory \
  "gh pr merge 1 |& tail -3"

# === ADVISORY — round-2 codex review (here-string, gh flag position, ======
# === multi-assignment prefix) ==============================================

run_case "here-string before mutating pipe is not mistaken for a heredoc" \
  advisory \
  "read x <<< value; git commit -m x | tail"

run_case "gh -R before object still finds the mutating verb" \
  advisory \
  "gh issue -R owner/repo create --body x | head"

run_case "gh --repo between object and verb still finds the mutating verb" \
  advisory \
  "gh pr --repo owner/repo merge 1 | tail"

run_case "plain env assignment before a capture assignment (LANG=C RESULT=\$(...))" \
  advisory \
  'LANG=C RESULT=$(gh pr merge 1 | tail -3)'

# === ADVISORY — round-3 codex review (standalone group token, balanced ====
# === self-contained substitution assignment) ================================

run_case "standalone group token with trailing space does not blank argv[0]" \
  advisory \
  "( git commit -m x | tail )"

run_case "balanced self-contained substitution assignment reaches the real command" \
  advisory \
  'FOO=$(date) git commit -m x | tail'

# === ADVISORY — round-4 codex review (multi-token substitution, keyword+ ==
# === grouping-prefix combination) ===========================================

run_case "multi-token substitution (space inside \$(...)) still recovers the real command" \
  advisory \
  'STAMP=$(date +%s) git commit -m x | tail'

run_case "shell keyword before a parenthesized pipeline still recovers the real command" \
  advisory \
  "if (git commit -m x | tail); then echo done; fi"

# === ADVISORY — round-5 codex review (pipe-then-newline continuation, =====
# === embedded pipeline inside a quoted command substitution) ===============

run_case "pipe operator followed by a newline (valid multi-line pipeline)" \
  advisory \
  "$(printf 'gh pr merge 1 --squash |\n tail -3')"

run_case "quoted command substitution hides an internal mutating pipeline" \
  advisory \
  'OUT="$(gh pr merge 1 2>&1 | tail -3)"'

# === ADVISORY — round-6 codex review (sibling substitutions in one =========
# === quoted token, gh api mutating method) ==================================

run_case "sibling substitutions in one quoted token — mutating one is not the first" \
  advisory \
  'OUT="$(date) $(gh pr merge 1 | tail -3)"'

run_case "gh api -X POST piped to tail (mutating REST method)" \
  advisory \
  "gh api repos/o/r/issues -X POST 2>&1 | tail -3"

run_case "gh api --method=DELETE piped to tail (mutating REST method, = form)" \
  advisory \
  "gh api repos/o/r/issues/1 --method=DELETE 2>&1 | tail -3"

# === SILENT — round-6 codex review (read-only gh api method) ===============

run_case "gh api (default GET) piped to tail is read-only" \
  silent \
  "gh api repos/o/r/issues 2>&1 | tail -3"

# === SILENT — read-only command piped (no mutation) ========================

run_case "git log | head (read-only)" \
  silent \
  "git log --oneline | head -20"

run_case "git status | grep (read-only)" \
  silent \
  "git status | grep modified"

run_case "git diff | tail (read-only)" \
  silent \
  "git diff | tail -20"

run_case "gh pr list | head (read-only)" \
  silent \
  "gh pr list | head -5"

run_case "gh pr view | grep (read-only)" \
  silent \
  "gh pr view 1 | grep merged"

run_case "gh issue list | tail (read-only)" \
  silent \
  "gh issue list | tail -10"

# === SILENT — mutating command piped to a non-truncating sink ==============

run_case "git commit | cat (non-truncating sink)" \
  silent \
  "git commit -m x | cat"

run_case "git push | sort (non-truncating sink)" \
  silent \
  "git push origin main | sort"

run_case "gh pr merge | wc -l (non-truncating sink)" \
  silent \
  "gh pr merge 1 | wc -l"

# === SILENT — no pipe (different separator / single command) ==============

run_case "git commit && git push (&& is a different defect class)" \
  silent \
  "git commit -m x && git push"

run_case "git commit ; git push (; sequencing)" \
  silent \
  "git commit -m x ; git push"

run_case "git commit || echo failed (|| fallback)" \
  silent \
  "git commit -m x || echo failed"

run_case "single git commit (no chain)" \
  silent \
  "git commit -m x"

run_case "grep | sort | uniq (no mutating segment)" \
  silent \
  "grep X foo | sort | uniq"

# === SILENT — false-positive surfaces (issue #788 requirement) ============

run_case "quoted-string literal pipe in --body" \
  silent \
  'gh issue create --title x --body "example: git commit -m x | tail -3"'

run_case "heredoc body containing mutating+pipe example text" \
  silent \
  "$(printf 'BODY=$(cat <<'"'"'EOF'"'"'\nExample of the bug: git commit -m x | tail -3\nEOF\n)\ngh issue create --title x --body "$BODY"')"

run_case "heredoc body line starting with marker word but not alone on the line (EOF-not-end guard)" \
  silent \
  "$(printf 'cat <<EOF\ngit commit | tail\nEOF not-end\nreal body line: git push | tail -1\nEOF')"

# === ADVISE-channel experiment arm (issue #874) ============================
#
# Both directions, because a one-directional test lets the opposite error in:
#   - the treatment arm must ADD additionalContext on a firing command, and
#     must keep the stderr line (the fire ledger classifies `advise` from
#     stderr, and the dispatcher forwards stderr unconditionally);
#   - the treatment arm must stay SILENT on a non-firing command — the env var
#     is an arm switch, not a second trigger.
#
# NOTE (issue #874): these assert the hook's OWN stdout. The other half of the
# path — `_dispatch.run_group` merging non-decision `additionalContext` and
# writing it (hooks/_lib/_dispatch.py:208-223) — ships in the same PR and is
# covered by tests/hooks/_lib/test_dispatch.py. See pipefail-advisory/spec.md
# § "ADVISE-channel experiment" for the end-to-end measurement.

run_case_context() {
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
  echo "$payload" | PRAXIS_PIPEFAIL_ADVISORY_CONTEXT=1 python3 "$HOOK" \
    >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    context)
      [ "$rc" -eq 0 ] || ok=0
      # stderr (control arm) must survive the treatment arm.
      echo "$err" | grep -q "\[pipefail-advisory\]" || ok=0
      # stdout must be the PreToolUse additionalContext shape, carrying the
      # SAME text as stderr — not a summary, not a decision.
      echo "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
hso = d["hookSpecificOutput"]
assert set(d) == {"hookSpecificOutput"}, d
assert hso["hookEventName"] == "PreToolUse", hso
assert "permissionDecision" not in hso, hso
assert hso["additionalContext"].startswith("[pipefail-advisory]"), hso
' 2>/dev/null || ok=0
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

# Must fire — gen-2 pattern, the command the audit's largest advisory load is
# made of.
run_case_context "874: treatment arm emits additionalContext + keeps stderr" \
  context \
  "gh pr merge 123 --squash 2>&1 | tail -3"

run_case_context "874: treatment arm on gen-1 pattern" \
  context \
  "git commit -m x | tail"

# Positive control for the negative side: the same env var on a command the
# hook does not flag must produce NOTHING on either channel. Without this, a
# hook that emitted additionalContext unconditionally would still pass the two
# cases above.
run_case_context "874: treatment arm silent on read-only pipe (control)" \
  silent \
  "git log --oneline | head -20"

run_case_context "874: treatment arm silent on non-truncating sink (control)" \
  silent \
  "git commit -m x | cat"

# The switch is exact-value "1" — mirrors PRAXIS_ANCHOR_GATE_ADVISORY. Any
# other value leaves the default (stderr-only) arm in place.
_arm_out=$(python3 -c '
import json
print(json.dumps({"tool_name":"Bash","tool_input":{"command":"git commit -m x | tail"}}))' \
  | PRAXIS_PIPEFAIL_ADVISORY_CONTEXT=true python3 "$HOOK" 2>/dev/null)
if [ -z "$_arm_out" ]; then
  echo "PASS  [874: non-'1' env value keeps the default stderr-only arm]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [874: non-'1' env value turned the arm on] stdout: $_arm_out"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("874: non-'1' env value keeps default arm")
fi

# === GATING — masked exit code on the left of `&&` (issue #1271) ==========
#
# The gap: `pipefail-advisory`'s original predicate needs the MUTATING
# command to be the piped one, and `inspection-chain-advisory` is silent by
# spec on any `&&` chain mixing inspection with state change. A chain whose
# non-mutating segment is piped and whose mutating segment is not reaches
# neither, so these cases must hit the NEW detector specifically.

run_case "issue #1271 verbatim: git switch | tail && gh pr merge" \
  gating \
  "git switch main 2>&1 | tail -1 && gh pr merge 1264 --squash --delete-branch"

run_case "cd prefix before the masked segment" \
  gating \
  "cd /repo && git switch main 2>&1 | tail -1 && gh pr merge 4964 --squash --delete-branch"

run_case "head sink gating git push" \
  gating \
  "git log --oneline | head -1 && git push origin main"

run_case "grep sink gating gh workflow run" \
  gating \
  "gh pr view 1 --json state | grep OPEN && gh workflow run ci.yml"

run_case "grep sink gating a mutating gh api call" \
  gating \
  "gh pr view 1 --json state | grep OPEN && gh api -X PATCH /repos/o/r/issues/comments/1 -F body=@b.md"

# `&&` gates the whole pipeline on its right, so the irreversible command need
# not be that pipeline's first segment. Scanning only unit[0] missed these.
run_case "irreversible command is the second segment of the gated pipeline" \
  gating \
  "git switch main 2>&1 | tail -1 && echo x | gh pr merge 1264"

run_case "irreversible command is the last of three gated segments" \
  gating \
  "git status --porcelain | tail -1 && cat f | tee log | git push origin main"

# A substitution body is its own command list, so an `&&` chain written inside
# one gates the same way. What does not cross the boundary is the
# substitution's own exit code — that case stays out of scope (spec.md).
run_case "gating chain written inside a command substitution" \
  gating \
  'OUT="$(git switch main 2>&1 | tail -1 && gh pr merge 1264)"'

run_case "substitution body needs the fd-dup merge to see the sink" \
  gating \
  'OUT="$(git switch main 2>&1 | tail -1 && gh workflow run ci.yml)"'

# --- SILENT counterparts: each removes exactly one element of the predicate

run_case "a semicolon does not gate, so a masked exit changes nothing" \
  silent \
  "git switch main 2>&1 | tail -1 ; gh pr merge 1264 --squash"

run_case "gated command is reversible (git commit) — not this predicate" \
  silent \
  "git status --porcelain | tail -1 && git commit -m x"

run_case "left side has no pipe at all" \
  silent \
  "git switch main && gh pr merge 1264 --squash"

run_case "left side pipes into a non-truncating sink" \
  silent \
  "git switch main 2>&1 | cat && gh pr merge 1264 --squash"

run_case "masked pipeline sits AFTER the mutation — gates nothing" \
  silent \
  "gh pr merge 1264 --squash && git log --oneline | tail -1"

run_case "heredoc body holding the issue's own example" \
  silent \
  "cat <<EOF
git switch main 2>&1 | tail -1 && gh pr merge 1264 --squash
EOF"

# --- The original detector must keep its own headline, not be shadowed

run_case "gen-2 pattern still reports the piped-mutation headline" \
  pipe \
  "gh pr merge 123 --squash 2>&1 | tail -3"

run_case "gen-1 pattern still reports the piped-mutation headline" \
  pipe \
  "git commit -m x | tail"

# === Fail-open infrastructure =============================================

run_case_raw_payload() {
  local name="$1" expected="$2" payload="$3"

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  echo "$payload" | env -u PRAXIS_PIPEFAIL_ADVISORY_CONTEXT python3 "$HOOK" \
    >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
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

run_case_raw_payload "non-Bash tool passes silently" silent \
  '{"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x"}}'

run_case_raw_payload "malformed JSON fails open silently" silent \
  'not valid json {{{'

run_case_raw_payload "empty Bash command silent" silent \
  '{"tool_name": "Bash", "tool_input": {"command": ""}}'

run_case_raw_payload "whitespace-only Bash command silent" silent \
  '{"tool_name": "Bash", "tool_input": {"command": "   "}}'

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

# === Summary ==============================================================

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
