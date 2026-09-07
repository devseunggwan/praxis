#!/bin/bash
# test_postcompact_context.sh — coverage for the post-compaction context hook
# (issues #472, #1339).
#
# Synthesizes Claude Code SessionStart hook payloads and asserts:
#   emit   → exit 0 + stdout JSON with hookSpecificOutput.hookEventName
#            "SessionStart" and additionalContext
#   silent → exit 0 + stdout empty
#
# Since #1339 the hook is registered on SessionStart with matcher `compact`,
# so the payload's `source` field is the trigger: "compact" (or absent)
# injects, anything else is silent. There is no transcript fixture and no
# dedup state any more — the event fires once per compaction.
#
# Usage: bash tests/hooks/advisory-nudge/test_postcompact_context.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/postcompact-context/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# --- mocks ------------------------------------------------------------------

make_mock_git_clean() {
  local dir="$1" branch="$2"
  mkdir -p "$dir"
  cat > "$dir/git" <<EOF
#!/bin/bash
if [ "\$1" = "-C" ] && [ "\$3" = "branch" ] && [ "\$4" = "--show-current" ]; then
  echo "$branch"
  exit 0
fi
exit 1
EOF
  chmod +x "$dir/git"
}

make_mock_gh_no_pr() {
  local dir="$1"
  mkdir -p "$dir"
  cat > "$dir/gh" <<'EOF'
#!/bin/bash
echo "[]"
exit 0
EOF
  chmod +x "$dir/gh"
}

make_mock_gh_one_pr() {
  local dir="$1" number="$2" url="$3" title="$4"
  mkdir -p "$dir"
  cat > "$dir/gh" <<EOF
#!/bin/bash
echo '[{"number": $number, "url": "$url", "title": "$title"}]'
exit 0
EOF
  chmod +x "$dir/gh"
}

# --- runner -----------------------------------------------------------------

# run_hook <env_setup_command> <payload_json>
#   Executes the hook with whatever env the caller sets up (typically a PATH
#   prefix so mock git/gh shadow the real binaries). Records rc, stdout,
#   stderr and wall time in LAST_RC / LAST_OUT / LAST_ERR / LAST_SECS.
run_hook() {
  local env_setup="$1" payload="$2"
  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  # Normalize empty env_setup to `true` so the bash -c body stays syntactically
  # valid (a leading `;` is a parse error).
  local prefix="${env_setup:-true}"
  # Generous subprocess timeouts so full-suite CPU contention cannot trip the
  # hook's production-tuned 1.5s/3.0s defaults and produce a false timeout
  # (empty PR section → flaky "#999 rendered" assertion). These assertions
  # measure rendering logic, not host scheduling; the override keeps them
  # deterministic under load while production keeps the tight defaults.
  local timeouts="export PRAXIS_POSTCOMPACT_GIT_TIMEOUT=30 PRAXIS_POSTCOMPACT_GH_TIMEOUT=30"
  # Wall time in whole seconds via the bash builtin — portable to bash 3.2
  # (macOS) where `date +%N` is not, and coarse enough for an 8s budget.
  SECONDS=0
  bash -c "$timeouts ; $prefix ; echo '$payload' | python3 '$HOOK'" >"$out_file" 2>"$err_file"
  local rc=$?
  LAST_SECS=$SECONDS
  LAST_OUT=$(cat "$out_file")
  LAST_ERR=$(cat "$err_file")
  LAST_RC=$rc
  rm -f "$out_file" "$err_file"
}

assert_emit() {
  local name="$1"
  local ok=1
  [ "$LAST_RC" -eq 0 ] || ok=0
  echo "$LAST_OUT" | grep -q '"hookEventName": "SessionStart"' || ok=0
  echo "$LAST_OUT" | grep -q '"additionalContext"' || ok=0
  echo "$LAST_OUT" | grep -q 'Praxis post-compaction context' || ok=0
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=emit rc=$LAST_RC"
    [ -n "$LAST_OUT" ] && echo "        stdout: $LAST_OUT"
    [ -n "$LAST_ERR" ] && echo "        stderr: $LAST_ERR"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

assert_silent() {
  local name="$1"
  local ok=1
  [ "$LAST_RC" -eq 0 ] || ok=0
  [ -z "$LAST_OUT" ]   || ok=0
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=silent rc=$LAST_RC"
    [ -n "$LAST_OUT" ] && echo "        stdout: $LAST_OUT"
    [ -n "$LAST_ERR" ] && echo "        stderr: $LAST_ERR"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

assert_body() {
  local name="$1" needle="$2"
  if echo "$LAST_OUT" | grep -q -- "$needle"; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] missing: $needle"
    [ -n "$LAST_OUT" ] && echo "        stdout: $LAST_OUT"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# payload_for <source> [<sid>] [<cwd>]
#   A SessionStart payload. `source` "" omits the field entirely (an older
#   host shape); any other value is passed through. cwd defaults to "$T" so
#   the impl's subprocess.run with cwd=<path> can resolve the mock binaries.
payload_for() {
  local source="$1" sid="${2:-sess-1}" cwd="${3:-$T}"
  python3 -c '
import json, sys
source, sid, cwd = sys.argv[1], sys.argv[2], sys.argv[3]
payload = {
    "session_id": sid,
    "cwd": cwd,
    "hook_event_name": "SessionStart",
    "transcript_path": cwd + "/transcript.jsonl",
}
if source:
    payload["source"] = source
print(json.dumps(payload))' "$source" "$sid" "$cwd"
}

new_case_dir() {
  T=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
  MOCK_BIN="$T/bin"
  mkdir -p "$MOCK_BIN"
}

# =============================================================================
# EMIT — source: "compact"
# =============================================================================

new_case_dir
make_mock_git_clean "$MOCK_BIN" "feat-branch"
make_mock_gh_one_pr "$MOCK_BIN" 472 "https://github.com/o/r/pull/472" "feat: postcompact"
PAYLOAD=$(payload_for "compact")
run_hook "export PATH='$MOCK_BIN:'\$PATH" "$PAYLOAD"
assert_emit "emit on source=compact"
assert_body "emit body: session_id rendered" "sess-1"
assert_body "emit body: cwd rendered" "$T"
assert_body "emit body: branch rendered" "feat-branch"
assert_body "emit body: PR number rendered" "#472"
assert_body "emit body: PR URL rendered" "github.com/o/r/pull/472"

new_case_dir
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "compact")
run_hook "export PATH='$MOCK_BIN:'\$PATH" "$PAYLOAD"
assert_emit "emit with no active PR (degrades gracefully)"
assert_body "emit body: no-PR placeholder rendered" "none for current branch"

# The output must be one JSON document whose hookSpecificOutput carries
# exactly the SessionStart event name — the host keys the channel on it.
new_case_dir
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "compact")
run_hook "export PATH='$MOCK_BIN:'\$PATH" "$PAYLOAD"
if echo "$LAST_OUT" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
hso = doc["hookSpecificOutput"]
assert hso["hookEventName"] == "SessionStart", hso
assert isinstance(hso["additionalContext"], str) and hso["additionalContext"]
assert set(doc) == {"hookSpecificOutput"}, doc
' 2>/dev/null; then
  echo "PASS  [stdout is a single SessionStart hookSpecificOutput document]"; PASS=$((PASS + 1))
else
  echo "FAIL  [stdout is a single SessionStart hookSpecificOutput document]"
  echo "        stdout: $LAST_OUT"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("stdout is a single SessionStart hookSpecificOutput document")
fi

# Same event, second delivery: no dedup state exists, so it emits again. The
# host fires SessionStart(compact) once per compaction; the hook does not
# second-guess that.
new_case_dir
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "compact")
# The removed dedup state lived at $PRAXIS_HOME/cache/postcompact-context-<sid>.json,
# so the leak check scans a PRAXIS_HOME dedicated to these two runs, not cwd.
HOME_DIR="$T/praxis-home"
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_HOME='$HOME_DIR'" "$PAYLOAD"
assert_emit "first compact delivery emits"
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_HOME='$HOME_DIR'" "$PAYLOAD"
assert_emit "second compact delivery emits again (event is the trigger, no dedup state)"
STATE_LEAK=0
if [ -d "$HOME_DIR" ]; then
  while IFS= read -r _f; do
    [ -n "$_f" ] && STATE_LEAK=1
  done < <(find "$HOME_DIR" -name '*postcompact*' 2>/dev/null)
fi
[ "$STATE_LEAK" -eq 1 ] \
  && { echo "FAIL  [no state file is written under PRAXIS_HOME]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("no state file is written under PRAXIS_HOME"); } \
  || { echo "PASS  [no state file is written under PRAXIS_HOME]"; PASS=$((PASS + 1)); }

# =============================================================================
# EMIT — source absent (older host shape; the manifest matcher already filtered)
# =============================================================================

new_case_dir
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "")
run_hook "export PATH='$MOCK_BIN:'\$PATH" "$PAYLOAD"
assert_emit "emit when source is absent"

# =============================================================================
# SILENT — every non-compact source
# =============================================================================

for src in startup resume clear fork; do
  new_case_dir
  make_mock_git_clean "$MOCK_BIN" "main"
  make_mock_gh_no_pr "$MOCK_BIN"
  PAYLOAD=$(payload_for "$src")
  run_hook "export PATH='$MOCK_BIN:'\$PATH" "$PAYLOAD"
  assert_silent "silent on source=$src"
done

new_case_dir
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(python3 -c 'import json,sys; print(json.dumps({"session_id":"s","cwd":sys.argv[1],"source":42}))' "$T")
run_hook "export PATH='$MOCK_BIN:'\$PATH" "$PAYLOAD"
assert_silent "silent on non-string source"

new_case_dir
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "Compact")
run_hook "export PATH='$MOCK_BIN:'\$PATH" "$PAYLOAD"
assert_silent "silent on source=Compact (exact match, case-sensitive)"

# =============================================================================
# BYPASS — env var short-circuits before any read
# =============================================================================

new_case_dir
make_mock_git_clean "$MOCK_BIN" "feat-branch"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "compact")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT=1" "$PAYLOAD"
assert_silent "bypass env var skips everything"

new_case_dir
make_mock_git_clean "$MOCK_BIN" "feat-branch"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "compact")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT=0" "$PAYLOAD"
assert_emit "bypass env var set to 0 does not bypass"

# =============================================================================
# FAIL-OPEN — payload shape variants
# =============================================================================

new_case_dir
run_hook "" "not-json"
assert_silent "fail-open on malformed JSON stdin"

new_case_dir
run_hook "" '[1, 2, 3]'
assert_silent "fail-open on non-object JSON stdin"

new_case_dir
PAYLOAD=$(python3 -c 'import json,sys; print(json.dumps({"source":"compact","cwd":sys.argv[1]}))' "$T")
run_hook "" "$PAYLOAD"
assert_silent "fail-open on missing session_id"

new_case_dir
PAYLOAD=$(python3 -c 'import json; print(json.dumps({"source":"compact","session_id":"   "}))')
run_hook "" "$PAYLOAD"
assert_silent "fail-open on blank session_id"

new_case_dir
PAYLOAD=$(python3 -c 'import json; print(json.dumps({"source":"compact","session_id":"s"}))')
run_hook "" "$PAYLOAD"
assert_silent "fail-open on missing cwd"

# =============================================================================
# BUDGET — missing git / gh must still exit 0 quickly
# =============================================================================

new_case_dir
# Run with PATH pointing ONLY at a dir that has python3 but no git/gh, so
# both lookups fail with ENOENT rather than reaching a real binary.
mkdir -p "$T/onlypy"
ln -s "$(command -v python3)" "$T/onlypy/python3"
PAYLOAD=$(payload_for "compact")
run_hook "export PATH='$T/onlypy'" "$PAYLOAD"
assert_emit "emit with git and gh absent from PATH (fields degrade, still exits 0)"
assert_body "emit body: detached/unknown when git is absent" "detached/unknown"
assert_body "emit body: no-PR placeholder when gh is absent" "none for current branch"
if [ "$LAST_SECS" -lt 8 ]; then
  echo "PASS  [missing git/gh path completes inside the 8s manifest budget (${LAST_SECS}s)]"; PASS=$((PASS + 1))
else
  echo "FAIL  [missing git/gh path completes inside the 8s manifest budget (${LAST_SECS}s)]"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("missing git/gh path completes inside the 8s manifest budget")
fi

new_case_dir
# git present but failing (non-zero), gh hangs past its (shortened) timeout:
# the hook must still exit 0 with degraded fields, inside the budget.
cat > "$MOCK_BIN/git" <<'EOF'
#!/bin/bash
exit 128
EOF
cat > "$MOCK_BIN/gh" <<'EOF'
#!/bin/bash
sleep 30
EOF
chmod +x "$MOCK_BIN/git" "$MOCK_BIN/gh"
PAYLOAD=$(payload_for "compact")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_GIT_TIMEOUT=1 PRAXIS_POSTCOMPACT_GH_TIMEOUT=1" "$PAYLOAD"
assert_emit "emit when git fails and gh would hang (timeouts bound it)"
if [ "$LAST_SECS" -lt 8 ]; then
  echo "PASS  [failing git + hanging gh completes inside the 8s manifest budget (${LAST_SECS}s)]"; PASS=$((PASS + 1))
else
  echo "FAIL  [failing git + hanging gh completes inside the 8s manifest budget (${LAST_SECS}s)]"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("failing git + hanging gh completes inside the 8s manifest budget")
fi

# =============================================================================
# STRIKE state integration
# =============================================================================

new_case_dir
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
STATE_DIR="$T/state"
mkdir -p "$STATE_DIR/strikes"
cat > "$STATE_DIR/strikes/sess-strike.json" <<'EOF'
{"count": 2, "reasons": ["violation A", "violation B"]}
EOF
PAYLOAD=$(payload_for "compact" "sess-strike")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_STATE_DIR='$STATE_DIR'" "$PAYLOAD"
assert_emit "emit includes strike state when state file present"
assert_body "emit body: strike count rendered" "2/3"
assert_body "emit body: strike reason 1 rendered" "violation A"
assert_body "emit body: strike reason 2 rendered" "violation B"

new_case_dir
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
STATE_DIR="$T/state"
mkdir -p "$STATE_DIR/strikes"
PAYLOAD=$(payload_for "compact" "sess-no-strike")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_STATE_DIR='$STATE_DIR'" "$PAYLOAD"
assert_emit "emit with absent strike state shows 0/3"
assert_body "emit body: 0/3 fallback rendered" "0/3"

# =============================================================================
# Detached HEAD (git branch returns empty)
# =============================================================================

new_case_dir
cat > "$MOCK_BIN/git" <<'EOF'
#!/bin/bash
# Empty branch name → simulates detached HEAD
exit 0
EOF
chmod +x "$MOCK_BIN/git"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "compact")
run_hook "export PATH='$MOCK_BIN:'\$PATH" "$PAYLOAD"
assert_emit "emit on detached HEAD (branch empty)"
assert_body "emit body: detached HEAD placeholder" "detached/unknown"

# =============================================================================
# Active PR rendering
# =============================================================================

new_case_dir
make_mock_git_clean "$MOCK_BIN" "feat-x"
make_mock_gh_one_pr "$MOCK_BIN" 999 "https://github.com/o/r/pull/999" "feat: example"
PAYLOAD=$(payload_for "compact")
run_hook "export PATH='$MOCK_BIN:'\$PATH" "$PAYLOAD"
assert_emit "emit with active PR rendered"
assert_body "emit body: PR number rendered" "#999"
assert_body "emit body: PR URL rendered" "github.com/o/r/pull/999"

# =============================================================================
# Summary
# =============================================================================

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
