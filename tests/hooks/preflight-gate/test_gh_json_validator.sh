#!/bin/bash
# test_gh_json_validator.sh — coverage for hooks/preflight-gate/gh-json-validator/impl.py
#
# 10-case suite covering the full block / pass / bypass / skip / fail-open
# surface documented in issue #403 and hooks/preflight-gate/gh-json-validator/spec.md.
#
# Payload format: PreToolUse(Bash) JSON with tool_name, tool_input.command,
# and session_id piped directly to hooks/preflight-gate/gh-json-validator/impl.py (Python invoked
# directly — not via the .sh wrapper — for deterministic exit-code control).
#
# Usage: bash tests/test_gh_json_validator.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK_PY="$ROOT_DIR/hooks/preflight-gate/gh-json-validator/impl.py"

if [ ! -f "$HOOK_PY" ]; then
  echo "FAIL: hook script not found: $HOOK_PY" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Hermetic gh stub
#
# The hook discovers valid JSON fields by running `gh <subcmd> --json help` and
# parsing the "Available fields:" list out of gh's error output. That output is
# version- / auth- / repo-context-dependent, so relying on the ambient gh makes
# the block/pass cases non-deterministic: they pass on the dev's macOS but fail
# on the CI ubuntu runner (gh sits in /usr/bin there and the field probe behaves
# differently). Shadow gh with a fixed-field stub so the test exercises the
# hook's PARSING logic rather than whichever gh happens to be installed.
# ---------------------------------------------------------------------------
GH_STUB_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
cat >"$GH_STUB_DIR/gh" <<'STUB'
#!/usr/bin/env bash
# Deterministic stand-in for `gh <subcmd> --json help`. Emits the real
# `gh pr view` JSON field set (state/author/title present, `merged` absent)
# to stderr in gh's documented error shape, so the validator can classify
# fields offline.
for arg in "$@"; do
  if [ "$arg" = "--json" ]; then
    cat >&2 <<'FIELDS'
Unknown JSON field: "help"
Available fields:
  additions
  assignees
  author
  baseRefName
  body
  changedFiles
  closed
  closedAt
  comments
  commits
  createdAt
  deletions
  files
  headRefName
  id
  isDraft
  labels
  mergeStateStatus
  mergeable
  mergedAt
  mergedBy
  number
  reviewDecision
  reviews
  state
  statusCheckRollup
  title
  updatedAt
  url
FIELDS
    exit 1
  fi
done
exit 0
STUB
chmod +x "$GH_STUB_DIR/gh"

# Curated dir for the "gh not in PATH" fail-open case: holds python3 (the hook
# is launched via `env PATH=… python3`) but deliberately NO gh, so the field
# probe hits FileNotFoundError regardless of where the ambient gh lives. A bare
# PATH=/usr/bin:/bin no longer works — CI ubuntu ships gh in /usr/bin.
# Symlink the REAL interpreter (sys.executable), not `command -v python3`: the
# latter can resolve to a pyenv/asdf shim (a `#!/usr/bin/env bash` script) that
# fails to launch under the restricted PATH ("env: bash: No such file").
NO_GH_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
ln -sf "$(python3 -c 'import sys; print(sys.executable)')" "$NO_GH_DIR/python3"

trap 'rm -rf "$GH_STUB_DIR" "$NO_GH_DIR"' EXIT

# ---------------------------------------------------------------------------
# run_case name expectation command [ENV=VAL ...]
#   expectation:
#     "block:<marker>" — exit 2, stdout contains <marker>
#     "pass"           — exit 0, stdout empty
# ---------------------------------------------------------------------------
run_case() {
  local name="$1" expectation="$2" command="$3"
  shift 3
  # remaining args are ENV=VAL pairs
  local extra_env=("$@")

  local sid
  sid="test-$(date +%s)-$$-$RANDOM"
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "session_id": sys.argv[2],
}))
' "$command" "$sid")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)

  # Default the hook's PATH to the hermetic gh stub unless the case sets PATH
  # itself (fail-open uses NO_GH_DIR; bypass cases override their own env).
  local case_env=("${extra_env[@]}") _has_path=0 _e
  for _e in "${extra_env[@]}"; do
    case "$_e" in PATH=*) _has_path=1 ;; esac
  done
  if [ "$_has_path" -eq 0 ]; then
    # Prepend the stub so it shadows the ambient gh, but inherit the rest of
    # PATH so python3 stays resolvable wherever it lives (/usr/bin on CI ubuntu,
    # /usr/local/bin on the python docker images) — do not hardcode a location.
    case_env=("PATH=$GH_STUB_DIR:$PATH" "${extra_env[@]}")
  fi

  printf '%s' "$payload" | env "${case_env[@]}" python3 "$HOOK_PY" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    pass)
      [ "$rc" -eq 0 ] || ok=0
      ;;
    block:*)
      local marker="${expectation#block:}"
      [ "$rc" -eq 2 ] || ok=0
      case "$out" in
        *"$marker"*) ;;
        *) ok=0 ;;
      esac
      ;;
    *)
      echo "FAIL  [$name] unknown expectation: $expectation"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>} stdout=${out:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Case 1: Block — invalid field (`--json merged`)
# `merged` is not a valid `gh pr view` JSON field; hook must block (exit 2).
# ---------------------------------------------------------------------------
run_case "block: invalid field --json merged" \
  "block:BLOCKED" \
  "gh pr view 1 --json merged"

# #1092: a path-prefixed gh binary must not slip past the --json field gate.
# `argv[0] == "gh"` exact match previously let `/usr/bin/gh pr view 1 --json
# merged` bypass; `_is_gh_binary` closes it. (The hook still shells out to the
# hermetic `gh` stub for the field list regardless of argv[0]'s path.)
run_case "block: /usr/bin/gh pr view --json merged (path-prefix)" \
  "block:BLOCKED" \
  "/usr/bin/gh pr view 1 --json merged"

# ---------------------------------------------------------------------------
# Case 2: Pass — valid field (`--json state`)
# `state` IS a valid `gh pr view` JSON field; hook must pass (exit 0).
# ---------------------------------------------------------------------------
run_case "pass: valid field --json state" \
  "pass" \
  "gh pr view 1 --json state"

# ---------------------------------------------------------------------------
# Case 3: Pass — multiple valid fields (`--json state,author,title`)
# All three fields are valid; hook must pass.
# ---------------------------------------------------------------------------
run_case "pass: multiple valid fields --json state,author,title" \
  "pass" \
  "gh pr view 1 --json state,author,title"

# ---------------------------------------------------------------------------
# Case 4: Block — mixed valid+invalid (`--json state,merged`)
# One field valid, one invalid; any invalid field triggers block.
# ---------------------------------------------------------------------------
run_case "block: mixed valid+invalid --json state,merged" \
  "block:BLOCKED" \
  "gh pr view 1 --json state,merged"

# ---------------------------------------------------------------------------
# Case 5: Bypass — inline comment marker
# Command contains `# PRAXIS_GH_JSON_BYPASS=skip`; must pass even
# with an invalid field.
# ---------------------------------------------------------------------------
run_case "bypass: inline comment marker" \
  "pass" \
  "gh pr view 1 --json merged  # PRAXIS_GH_JSON_BYPASS=skip"

# ---------------------------------------------------------------------------
# Case 6: Bypass — env var PRAXIS_GH_JSON_BYPASS=1
# Must pass even with an invalid field when env var is set.
# ---------------------------------------------------------------------------
run_case "bypass: env var PRAXIS_GH_JSON_BYPASS=1" \
  "pass" \
  "gh api repos/owner/repo/pulls/1 --json merged" \
  "PRAXIS_GH_JSON_BYPASS=1"

# ---------------------------------------------------------------------------
# Case 7: Skip — `gh api` subcommand is out of scope
# `gh api` follows REST schema; hook must pass unconditionally.
# ---------------------------------------------------------------------------
run_case "skip: gh api subcommand is out of scope" \
  "pass" \
  "gh api repos/owner/repo/pulls/1 --json merged"

# ---------------------------------------------------------------------------
# Case 8: Skip — non-gh Bash command (`ls -la`)
# Hook only intercepts gh commands; plain shell commands must pass.
# ---------------------------------------------------------------------------
run_case "skip: non-gh bash command ls -la" \
  "pass" \
  "ls -la /tmp"

# ---------------------------------------------------------------------------
# Case 9: Fail-open — gh not in PATH
# When gh binary is missing, hook must exit 0 (fail-open, never break session).
# ---------------------------------------------------------------------------
run_case "fail-open: gh not in PATH" \
  "pass" \
  "gh pr view 1 --json merged" \
  "PATH=$NO_GH_DIR"

# ---------------------------------------------------------------------------
# Case 10: Skip — no `--json` flag present
# Command references gh but has no --json; prefilter skips it (exit 0).
# ---------------------------------------------------------------------------
run_case "skip: no --json flag" \
  "pass" \
  "gh pr view 1"

# ---------------------------------------------------------------------------
# issue #514 결함5: `--json help` is gh's OWN field introspection — gh prints
# the valid field list. Blocking it is self-contradictory because the
# remediation message tells the agent to run exactly `gh <subcmd> --json help`.
# A lone `help` field must PASS.
# ---------------------------------------------------------------------------
run_case "514: --json help introspection passes" \
  "pass" \
  "gh pr view 1 --json help"

run_case "514: --json=help inline introspection passes" \
  "pass" \
  "gh pr view 1 --json=help"

# But `help` combined with a real invalid field is a data projection request —
# still block.
run_case "514: --json help,merged still blocks" \
  "block:BLOCKED" \
  "gh pr view 1 --json help,merged"

# ---------------------------------------------------------------------------
# Summary
# Fail-open guard opt-in (issue #498): main() must be @fail_open-wrapped;
# guard behavior is tested centrally in tests/test_hook_runtime.sh.
_failopen_out=$(python3 - << PYEOF 2>&1
import importlib.util
spec = importlib.util.spec_from_file_location("impl", "$HOOK_PY")
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
# Shared probe deadline (issue #1167): both `gh --json help` probes (initial +
# dummy-positional retry) draw from ONE deadline, so their sum can never exceed
# the member budget inside the Bash dispatch group. An expired deadline must
# fail open immediately WITHOUT spawning gh. Asserted directly — reproducing it
# through a payload would burn real wall-clock.
# ---------------------------------------------------------------------------
_deadline_out=$(python3 - << PYEOF 2>&1
import json, sys, importlib.util, time
spec = importlib.util.spec_from_file_location("impl", "$HOOK_PY")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# gh would answer here — make any spawn after expiry a hard failure instead.
def _boom(*a, **k):
    raise AssertionError("gh spawned after the deadline had passed")
mod.subprocess.run = _boom

if mod._fetch_valid_fields(("pr", "view"), time.monotonic() - 1) is not None:
    sys.stderr.write("expired deadline did not fail open\n"); sys.exit(1)

# Constant parity with the manifest timeout (round-2 review): the standalone
# probe budget derives from _HOOK_TIMEOUT_SEC, so a manifest bump this
# constant misses would silently shrink (or overrun) the real budget.
manifest = json.load(open("$ROOT_DIR/hooks/manifest.json"))
timeout = next(h["timeout"] for h in manifest["hooks"] if h["name"] == "gh-json-validator")
if mod._HOOK_TIMEOUT_SEC != timeout:
    sys.stderr.write("_HOOK_TIMEOUT_SEC=%r drifted from manifest timeout %r\n"
                     % (mod._HOOK_TIMEOUT_SEC, timeout))
    sys.exit(1)
if mod._HOOK_TIMEOUT_SEC - mod._HOOK_TIMEOUT_MARGIN_SEC >= timeout:
    sys.stderr.write("probe budget leaves no margin under the manifest timeout\n")
    sys.exit(1)
PYEOF
)
_deadline_rc=$?
if [ "$_deadline_rc" -eq 0 ] && [ -z "$_deadline_out" ]; then
  echo "PASS  [deadline] expired probe deadline fails open without spawning gh"
  PASS=$((PASS+1))
else
  echo "FAIL  [deadline] deadline not honoured (rc=$_deadline_rc out=$(echo "$_deadline_out" | head -c 200))"
  FAIL=$((FAIL+1)); FAILED_NAMES+=("shared probe deadline")
fi


# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
