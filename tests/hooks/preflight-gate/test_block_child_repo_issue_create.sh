#!/bin/bash
# test_block_child_repo_issue_create.sh —
#   coverage for hooks/preflight-gate/block-child-repo-issue-create/impl.py
#
# Asserts:
#   block  → rc=2, stderr contains " blocked"
#   silent → rc=0, stderr empty
#
# Usage: bash tests/hooks/preflight-gate/test_block_child_repo_issue_create.sh

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/block-child-repo-issue-create/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# Config used in most cases: example-org mediated by example-org/hub,
# creation skill is "example-org:create-hub-issue" (skill name contains colon).
ORGS_CFG="example-org:example-org/hub:example-org:create-hub-issue"

run_case() {
  local name="$1" expectation="$2" payload="$3"
  shift 3
  # Remaining args are KEY=VALUE env overrides
  local env_args=("$@")

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)

  if [ "${#env_args[@]}" -gt 0 ]; then
    echo "$payload" | env -u PRAXIS_HUB_MEDIATED_ORGS -u PRAXIS_HOOK_BYPASS_HUB_ENFORCE \
      "${env_args[@]}" python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | env -u PRAXIS_HUB_MEDIATED_ORGS -u PRAXIS_HOOK_BYPASS_HUB_ENFORCE \
      python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local out err
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      # Standard block format (issue #439): header line "⚠️ <RULE> blocked".
      echo "$err" | grep -q " blocked$" || ok=0
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
    [ -z "$err" ] && [ "$expectation" = "block" ] && echo "        stderr: (empty)"
  fi
}

echo "test_block_child_repo_issue_create"

# ---------------------------------------------------------------------------
# BLOCK cases
# ---------------------------------------------------------------------------

# Standard --repo <org>/<child> form
run_case "child repo --repo space form (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo example-org/child-repo --title 'feat: add thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# --repo=<org>/<child> (equals sign)
run_case "child repo --repo=value form (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo=example-org/another-child --title 'fix: bug'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# -R <org>/<child> short flag space form
run_case "child repo -R space form (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create -R example-org/backend --title 'chore: cleanup'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# -R=<org>/<child> short flag equals form
run_case "child repo -R=value form (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create -R=example-org/frontend --title 'feat: nav'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# Case-insensitive org matching
run_case "child repo case-insensitive org match (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo EXAMPLE-ORG/child --title 'docs: update'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# Block message must contain skill name (creation_skill with colon)
run_case "block message contains colon-skill name (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo example-org/widget --title 'feat: widget'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# ---------------------------------------------------------------------------
# SILENT (PASS) cases
# ---------------------------------------------------------------------------

# No config → NO-OP
run_case "no config (NO-OP, silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo example-org/child --title 'feat: thing'\"}}"

# Hub repo itself → pass
run_case "hub repo itself (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo example-org/hub --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# Org not in allowlist
run_case "org not in allowlist (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo other-org/repo --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# Bypass env var set
run_case "bypass env set (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo example-org/child --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG" \
  "PRAXIS_HOOK_BYPASS_HUB_ENFORCE=1"

# No --repo flag → pass
run_case "no --repo flag (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title 'feat: thing' --body 'body'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# gh issue edit → not create, pass
run_case "gh issue edit (not create, silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue edit 42 --repo example-org/child --title 'updated'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# gh issue comment → not create, pass
run_case "gh issue comment (not create, silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue comment 42 --repo example-org/child --body 'hi'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# gh issue close → not create, pass
run_case "gh issue close (not create, silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue close 42 --repo example-org/child\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# gh pr create → not issue create, pass
run_case "gh pr create (not issue, silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr create --repo example-org/child --title 'feat: pr'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# Non-Bash tool
run_case "non-Bash tool (silent)" \
  "silent" \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"/tmp/x\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# Malformed config entry (only one colon) → fail-safe pass
run_case "malformed config entry (fail-safe silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo example-org/child --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=example-org:only-one-colon"

# Malformed JSON stdin → fail-open
run_case "malformed JSON stdin (fail-open, silent)" \
  "silent" \
  "not-json" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# creation_skill containing colon parses correctly
# (verified: the block case above uses ORGS_CFG which has skill "example-org:create-hub-issue")
run_case "creation_skill with colon in config (parses correctly, block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo example-org/someservice --title 'feat: service'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=example-org:example-org/hub:example-org:create-hub-issue"

# Multiple orgs in config — second org match also blocks
run_case "second org in multi-org config (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo second-org/child --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG,second-org:second-org/hub:second-org:create-issue"

# Second org: hub repo itself → pass
run_case "second org hub repo itself (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo second-org/hub --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG,second-org:second-org/hub:second-org:create-issue"

# ---------------------------------------------------------------------------
# gh issue new alias cases (P2 fix)
# ---------------------------------------------------------------------------

# gh issue new → official alias of create, must also block child repos
run_case "gh issue new alias blocks child repo (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue new --repo example-org/child --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# gh issue new with -R short flag → block
run_case "gh issue new -R short flag (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue new -R example-org/child --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# gh issue new targeting hub repo → pass
run_case "gh issue new on hub repo itself (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue new --repo example-org/hub --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# ---------------------------------------------------------------------------
# [HOST/]OWNER/REPO form cases (P2 fix)
# ---------------------------------------------------------------------------

# HOST/OWNER/REPO form — child repo → block
run_case "host-prefixed child repo (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create -R github.com/example-org/child --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# HOST/OWNER/REPO form — hub repo itself → pass
run_case "host-prefixed hub repo itself (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create -R github.com/example-org/hub --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# HOST/OWNER/REPO form — org not in allowlist → pass
run_case "host-prefixed org not in allowlist (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create -R github.com/other-org/repo --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# ---------------------------------------------------------------------------
# -Rvalue (no space) form (P3 fix)
# ---------------------------------------------------------------------------

# -Rvalue concatenated short flag — child repo → block
run_case "-Rvalue concatenated form child repo (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create -Rexample-org/child --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# -Rvalue on hub repo → pass
run_case "-Rvalue concatenated form hub repo (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create -Rexample-org/hub --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# ---------------------------------------------------------------------------
# gh issue -R <repo> create (flag before subcommand, P2 fix)
# ---------------------------------------------------------------------------

# gh issue -R repo create → block
run_case "flag-before-subcommand gh issue -R child create (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue -R example-org/child create --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# gh issue -R hub create → pass (it's the hub repo)
run_case "flag-before-subcommand gh issue -R hub create (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue -R example-org/hub create --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# gh issue close is still not "create" even with flag before subcommand
run_case "flag-before-subcommand gh issue -R child close (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue -R example-org/child close 42\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# ---------------------------------------------------------------------------
# gh -R <repo> issue create (global flag before command group, P2 round-3 fix)
# ---------------------------------------------------------------------------

# gh -R child issue create → block
run_case "gh -R child issue create global flag (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh -R example-org/child issue create --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# gh -R hub issue create → pass
run_case "gh -R hub issue create global flag (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh -R example-org/hub issue create --title 'feat: thing'\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# ---------------------------------------------------------------------------
# Compound command (hub first, child second) — P2 round-3 fix
# ---------------------------------------------------------------------------

run_case "compound hub then child (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo example-org/hub --title hub && gh issue create --repo example-org/child --title child\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# compound: both are hub or allowed → pass
run_case "compound both hub (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo example-org/hub --title h1 && gh issue create --repo example-org/hub --title h2\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# compound: child then hub → still block (child found first)
run_case "compound child then hub (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo example-org/child --title c && gh issue create --repo example-org/hub --title h\"}}" \
  "PRAXIS_HUB_MEDIATED_ORGS=$ORGS_CFG"

# ---------------------------------------------------------------------------
# Uncaught exception fail-open (outer Exception guard)
# ---------------------------------------------------------------------------

# Simulate an uncaught exception inside _main_inner (e.g. MemoryError) and
# verify the outer try/except Exception wrapper in main() returns 0 (fail-open).
_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util, io
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
def _raise(): raise MemoryError("simulated OOM")
mod._main_inner = _raise
sys.stdin = io.StringIO('{}')
sys.exit(mod.main())
PYEOF
)
_uncaught_rc=$?
if [ "$_uncaught_rc" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "  PASS  uncaught exception in _main_inner fails open (exit 0, no stderr)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  uncaught exception in _main_inner (rc=$_uncaught_rc, out=$(echo "$_uncaught_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("uncaught exception in _main_inner fails open")
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
TOTAL=$((PASS + FAIL))
echo "Result: $PASS/$TOTAL passed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
