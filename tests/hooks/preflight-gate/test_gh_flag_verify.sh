#!/bin/bash
# test_gh_flag_verify.sh — coverage for hooks/preflight-gate/gh-flag-verify/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   deny   → exit 2 + stdout JSON has permissionDecision "deny"
#   silent → exit 0 + stdout empty (no JSON, no permissionDecision)
#
# Usage: bash tests/test_gh_flag_verify.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/gh-flag-verify/impl.py"

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

  echo "$payload" | python3 "$HOOK" >"$out_file" 2>/dev/null
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
    [ -n "$out" ] && echo "        stdout: $out" | head -c 400
  fi
}

echo "test_gh_flag_verify"

# ---------------------------------------------------------------------------
# T01: gh search issues --state all → DENY
# block-gh-state-all also covers this; here we verify gh-flag-verify catches
# it independently (--state is in the allowed set but value is validated by
# block-gh-state-all; --state itself is a valid flag so gh-flag-verify passes).
# Actually: --state IS in allowed set for search issues so this should pass
# the flag-presence check; the value restriction is block-gh-state-all's job.
# Revised: test that --state open is SILENT (valid flag + valid value).
# ---------------------------------------------------------------------------
run_case "T01: gh search issues --state open (valid flag, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh search issues --state open"}}'

# T02: gh search issues --state all → SILENT from flag-verify perspective
# (--state is a recognized flag; value validation is block-gh-state-all's job)
run_case "T02: gh search issues --state all (flag valid, value check not this hook, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh search issues --state all"}}'

# T03: gh search prs --state merged → DENY
# gh search prs --help shows --state accepts {open|closed} only, NOT "merged".
# Actually: --state IS listed in gh search prs flags (the flag exists); we only
# check flag NAME presence, not value. So this should be SILENT for flag-verify.
# The VALUE "merged" being invalid is a separate concern beyond flag-name checking.
# Correction: this hook validates flag NAMES not VALUES. --state is in COMPAT.
# Replacing T03 with a test for an actually invalid flag name.
run_case "T03: gh search prs --state open (valid flag, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh search prs --state open"}}'

# T04: gh issue list --base main → DENY
# --base is valid for gh pr list and gh pr create but NOT for gh issue list.
run_case "T04: gh issue list --base main (invalid flag for issue list, deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list --base main"}}'

# T05: gh issue list --state all (valid for issue list, silent)
run_case "T05: gh issue list --state all (valid for issue list, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list --state all"}}'

# T06: gh pr list --state merged (valid for pr list, silent)
run_case "T06: gh pr list --state merged (valid for pr list, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr list --state merged"}}'

# T07: gh pr list --include-prs → DENY
# --include-prs is only in gh search issues, not in gh pr list.
run_case "T07: gh pr list --include-prs (invalid flag for pr list, deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr list --include-prs"}}'

# T08: gh issue list --author monalisa --base main → DENY on --base
# Multiple flags, one of them bad.
run_case "T08: issue list --author ok --base bad (deny on first bad flag)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list --author monalisa --base main"}}'

# T09: gh search repos --archived (valid, silent)
run_case "T09: gh search repos --archived (valid, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh search repos --archived"}}'

# T10: gh search repos --state open → DENY
# gh search repos does NOT have --state flag (unlike search issues/prs).
run_case "T10: gh search repos --state open (invalid flag for search repos, deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh search repos --state open"}}'

# T11: gh issue create --title "Bug" --body "desc" (valid, silent)
run_case "T11: gh issue create with valid flags (silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title \"Bug\" --body \"desc\""}}'

# T12: gh issue create --base main → DENY
# --base is not a valid flag for gh issue create.
run_case "T12: gh issue create --base main (invalid flag, deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --base main"}}'

# T13: gh pr create with valid flags (silent)
run_case "T13: gh pr create --title foo --base dev --assignee @me (valid, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title foo --base dev --assignee @me"}}'

# T14: gh pr create --state open → DENY
# --state is not valid for gh pr create.
run_case "T14: gh pr create --state open (invalid flag, deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --state open"}}'

# T15: Unknown subcommand → SILENT (fail-open for unknown subcommands)
run_case "T15: gh release list (unknown subcommand, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh release list --limit 10"}}'

# T16: Non-Bash tool → SILENT
run_case "T16: non-Bash tool (Read) → silent" \
  "silent" \
  '{"tool_name":"Read","tool_input":{"file_path":"/tmp/foo"}}'

# T17: Malformed JSON → SILENT (fail-open)
run_case "T17: malformed JSON → silent" \
  "silent" \
  'not-json'

# T18: Quoted body mentioning --state all → SILENT
# The flag text appears inside a quoted string, not as an executable flag.
run_case "T18: quoted body with --state all text (silent, not an executable flag)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 1 --body \"use --state all for all issues\""}}'

# T19: Chained command with invalid flag in second segment → DENY
run_case "T19: chained: gh issue list && gh issue list --base main (deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list --state open && gh issue list --base main"}}'

# T20: gh with global -R flag before subcommand → SILENT (valid)
run_case "T20: gh -R owner/repo issue list --state all (global flag, valid, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh -R owner/repo issue list --state all"}}'

# T21: gh with global -R flag before subcommand, invalid flag after → DENY
run_case "T21: gh -R owner/repo issue list --base main (global flag + invalid, deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh -R owner/repo issue list --base main"}}'

# T22: gh issue comment with valid flags (silent)
run_case "T22: gh issue comment --body text (valid, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 5 --body \"hello\""}}'

# T23: gh pr comment with valid flags (silent)
run_case "T23: gh pr comment --body text (valid, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body \"hello\""}}'

# T24: gh issue list short flag -s all (valid short flag, silent)
run_case "T24: gh issue list -s all (short flag, valid, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list -s all"}}'

# T25: gh pr list short flag -B main (valid short flag, silent — -B is --base)
run_case "T25: gh pr list -B main (short flag -B, valid, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr list -B main"}}'

# T26: Empty command → SILENT
run_case "T26: empty command → silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":""}}'

# ---------------------------------------------------------------------------
# Regression tests for PR #191 codex review fixes
# ---------------------------------------------------------------------------

# T27: gh search issues "-label:bug" → SILENT
# GitHub advanced-search exclusion syntax: positional query starts with '-'.
# The leading '-' is part of the query value, not a flag identifier.
# P1 fix: _collect_flags skips value tokens so positionals after flags are
# not misidentified; and since the query is a positional (no preceding flag),
# it is silently ignored.
run_case "T27: gh search issues \"-label:bug\" (positional query with dash, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh search issues \"-label:bug\""}}'

# T28: gh search issues --label bug --author octocat → SILENT
# Multiple chained value-taking flags. P1 fix: each value token is consumed
# so "bug" and "octocat" are not re-interpreted as flag identifiers.
run_case "T28: gh search issues --label bug --author octocat (chained value flags, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh search issues --label bug --author octocat"}}'

# T29: gh issue list --hostname github.com → DENY
# P2 fix: --hostname removed from GH_GLOBAL_FLAGS. This was incorrectly
# passing before; verified: `gh issue list --hostname github.com` → "unknown flag".
run_case "T29: gh issue list --hostname github.com (invalid flag after P2 trim, deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list --hostname github.com"}}'

# T30: gh issue list --color always → DENY
# P2 fix: --color removed from GH_GLOBAL_FLAGS. This was incorrectly
# passing before; verified: `gh issue list --color always` → "unknown flag".
run_case "T30: gh issue list --color always (invalid flag after P2 trim, deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list --color always"}}'

# ---------------------------------------------------------------------------
# Regression tests for R2 P2: bogus pre-subcommand global flags
# Before the fix, _skip_gh_global_flags() silently walked past ANY -* token
# before the subcommand. Unknown flags placed before the subcommand would
# cause the hook to misidentify the next token as the subcommand (e.g.
# subcommand="github.com") or hit an unrecognised key and silent-pass.
# ---------------------------------------------------------------------------

# T31: gh --hostname github.com issue list → DENY
# The codex R2 finding: --hostname placed before subcommand was silently passed.
# gh itself rejects: "unknown flag: --hostname".
run_case "T31: gh --hostname github.com issue list (bogus pre-subcommand flag, deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh --hostname github.com issue list"}}'

# T32: gh --color always pr list → DENY
# --color is NOT a recognized global flag (not in GH_GLOBAL_FLAGS).
run_case "T32: gh --color always pr list (bogus pre-subcommand flag, deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh --color always pr list"}}'

# T33: gh --base main issue list → DENY
# --base is a subcommand-level flag, not a global flag. Placing it before
# the subcommand should deny, not silently re-route.
run_case "T33: gh --base main issue list (subcommand flag used before subcommand, deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh --base main issue list"}}'

# T34: gh -R owner/repo --hostname x issue list → DENY
# Valid global flag (-R) followed by an invalid global flag (--hostname).
# The -R flag and its value are consumed cleanly, then --hostname triggers denial.
run_case "T34: gh -R owner/repo --hostname x issue list (valid+invalid global flags, deny)" \
  "deny" \
  '{"tool_name":"Bash","tool_input":{"command":"gh -R owner/repo --hostname x issue list"}}'

# T35: gh -R owner/repo issue list → SILENT
# Confirm known global flag -R still works correctly after the fix.
run_case "T35: gh -R owner/repo issue list (known global flag, silent)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh -R owner/repo issue list"}}'

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
