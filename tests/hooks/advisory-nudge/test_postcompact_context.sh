#!/bin/bash
# test_postcompact_context.sh — coverage for the post-compaction context hook
# (issue #472).
#
# Synthesizes Claude Code UserPromptSubmit hook payloads with fixture
# transcript JSONLs and asserts:
#   emit   → exit 0 + stdout JSON with hookSpecificOutput.additionalContext
#   silent → exit 0 + stdout empty
#
# Per-test isolation: every case uses its own TMPDIR (state file) and its
# own transcript fixture so dedup state does not leak across cases.
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

# --- fixture builders -------------------------------------------------------

# write_transcript <path> <uuid> [<extra_marker>]
#   Creates a minimal JSONL with one user prompt + one compact summary
#   entry whose uuid is <uuid>. When <extra_marker> is provided, appends a
#   second compact summary with that uuid AFTER the first.
write_transcript() {
  local path="$1" uuid="$2" extra="${3:-}"
  python3 - "$path" "$uuid" "$extra" <<'PY'
import json, sys
path, uuid1, extra = sys.argv[1], sys.argv[2], sys.argv[3]
records = [
    {"type": "user", "uuid": "u-prompt-1", "timestamp": "2026-05-28T01:00:00.000Z",
     "message": {"role": "user", "content": "hello"}},
    {"type": "assistant", "uuid": "u-asst-1", "timestamp": "2026-05-28T01:00:01.000Z",
     "message": {"role": "assistant", "content": "hi"}},
    {"type": "user", "isCompactSummary": True, "uuid": uuid1,
     "timestamp": "2026-05-28T02:00:00.000Z",
     "message": {"role": "user", "content": "(compact summary)"}},
]
if extra:
    records.append({"type": "user", "isCompactSummary": True, "uuid": extra,
                    "timestamp": "2026-05-28T03:00:00.000Z",
                    "message": {"role": "user", "content": "(later compact)"}})
with open(path, "w", encoding="utf-8") as fh:
    for r in records:
        fh.write(json.dumps(r))
        fh.write("\n")
PY
}

write_transcript_no_compact() {
  local path="$1"
  python3 - "$path" <<'PY'
import json, sys
path = sys.argv[1]
records = [
    {"type": "user", "uuid": "u1", "timestamp": "2026-05-28T01:00:00.000Z",
     "message": {"role": "user", "content": "hello"}},
    {"type": "assistant", "uuid": "u2", "timestamp": "2026-05-28T01:00:01.000Z",
     "message": {"role": "assistant", "content": "hi"}},
]
with open(path, "w", encoding="utf-8") as fh:
    for r in records:
        fh.write(json.dumps(r))
        fh.write("\n")
PY
}

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
#   Executes the hook with PATH prefixed by a per-case mock dir. The caller
#   sets PRAXIS_POSTCOMPACT_CONTEXT_FILE and any other env via $env_setup.
run_hook() {
  local env_setup="$1" payload="$2"
  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  # Normalize empty env_setup to `true` so the bash -c body stays syntactically
  # valid (otherwise the leading `;` is a parse error, and `: ; ; echo ...`
  # produces `;;` which bash mis-parses as a case-arm terminator).
  local prefix="${env_setup:-true}"
  # Generous subprocess timeouts so full-suite CPU contention cannot trip the
  # hook's production-tuned 1.5s/3.0s defaults and produce a false timeout
  # (empty PR section → flaky "#999 rendered" assertion). These assertions
  # measure rendering logic, not host scheduling; the override keeps them
  # deterministic under load while production keeps the tight defaults.
  local timeouts="export PRAXIS_POSTCOMPACT_GIT_TIMEOUT=30 PRAXIS_POSTCOMPACT_GH_TIMEOUT=30"
  bash -c "$timeouts ; $prefix ; echo '$payload' | python3 '$HOOK'" >"$out_file" 2>"$err_file"
  local rc=$?
  LAST_OUT=$(cat "$out_file")
  LAST_ERR=$(cat "$err_file")
  LAST_RC=$rc
  rm -f "$out_file" "$err_file"
}

assert_emit() {
  local name="$1"
  local ok=1
  [ "$LAST_RC" -eq 0 ] || ok=0
  echo "$LAST_OUT" | grep -q '"hookEventName": "UserPromptSubmit"' || ok=0
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

# Compose a minimal valid payload as a JSON literal that can be inlined into
# the bash -c command body. We use python3 -c so quoting stays robust.
payload_for() {
  # cwd defaults to "$T" (per-case TMPDIR) so the impl's subprocess.run
  # with cwd=<path> can resolve git/gh — passing a non-existent cwd makes
  # subprocess raise FileNotFoundError before the mock binaries are reached.
  local transcript="$1" sid="${2:-sess-1}" cwd="${3:-$T}"
  python3 -c '
import json, sys
print(json.dumps({
    "session_id": sys.argv[1],
    "cwd": sys.argv[2],
    "transcript_path": sys.argv[3],
    "prompt": "what next?"
}))' "$sid" "$cwd" "$transcript"
}

# --- per-case scaffolding ---------------------------------------------------
#
# Each case body uses $T (per-case TMPDIR) and $TRANSCRIPT (transcript path).
# A common preamble sets PATH so the mock git/gh shadow the real binaries,
# and PRAXIS_* env vars are inlined per-case.

new_case_dir() {
  T=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
  TRANSCRIPT="$T/transcript.jsonl"
  STATE_FILE="$T/state.json"
  MOCK_BIN="$T/bin"
  mkdir -p "$MOCK_BIN"
}

# =============================================================================
# EMIT cases — compaction marker present, dedup state empty
# =============================================================================

new_case_dir
write_transcript "$TRANSCRIPT" "compact-uuid-1"
make_mock_git_clean "$MOCK_BIN" "feat-branch"
make_mock_gh_one_pr "$MOCK_BIN" 472 "https://github.com/o/r/pull/472" "feat: postcompact"
PAYLOAD=$(payload_for "$TRANSCRIPT")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_emit "emit on fresh compaction marker"

new_case_dir
write_transcript "$TRANSCRIPT" "compact-uuid-2"
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_emit "emit with no active PR (degrades gracefully)"
echo "$LAST_OUT" | grep -q "none for current branch" \
  && { echo "PASS  [emit body: no-PR placeholder rendered]"; PASS=$((PASS + 1)); } \
  || { echo "FAIL  [emit body: no-PR placeholder rendered]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("emit body: no-PR placeholder rendered"); }

new_case_dir
write_transcript "$TRANSCRIPT" "compact-uuid-3" "compact-uuid-4"
make_mock_git_clean "$MOCK_BIN" "feat-branch"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_emit "emit picks most-recent compaction (multi-compact tail)"
echo "$LAST_OUT" | grep -q "compact-uuid-4" \
  && { echo "PASS  [emit body: latest uuid in context]"; PASS=$((PASS + 1)); } \
  || { echo "FAIL  [emit body: latest uuid in context]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("emit body: latest uuid in context"); }

# =============================================================================
# DEDUP — second invocation with same uuid is silent
# =============================================================================

new_case_dir
write_transcript "$TRANSCRIPT" "compact-uuid-dedup"
make_mock_git_clean "$MOCK_BIN" "feat-branch"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
ENV_SETUP="export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'"
run_hook "$ENV_SETUP" "$PAYLOAD"
assert_emit "first call emits on compaction"
run_hook "$ENV_SETUP" "$PAYLOAD"
assert_silent "second call on same uuid is silent (dedup)"

# =============================================================================
# RE-EMIT on new uuid (overwrite earlier marker in transcript)
# =============================================================================

new_case_dir
write_transcript "$TRANSCRIPT" "compact-uuid-A"
make_mock_git_clean "$MOCK_BIN" "feat-branch"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
ENV_SETUP="export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'"
run_hook "$ENV_SETUP" "$PAYLOAD"
assert_emit "emit on first uuid"
# Append a NEW compaction with a different uuid
write_transcript "$TRANSCRIPT" "compact-uuid-A" "compact-uuid-B"
run_hook "$ENV_SETUP" "$PAYLOAD"
assert_emit "re-emit when a newer compaction uuid appears"

# =============================================================================
# SILENT — no compaction marker in tail
# =============================================================================

new_case_dir
write_transcript_no_compact "$TRANSCRIPT"
make_mock_git_clean "$MOCK_BIN" "feat-branch"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_silent "silent when transcript has no compaction"

# =============================================================================
# BYPASS — env var short-circuits before any read
# =============================================================================

new_case_dir
write_transcript "$TRANSCRIPT" "compact-uuid-bypass"
make_mock_git_clean "$MOCK_BIN" "feat-branch"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'; export PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT=1" "$PAYLOAD"
assert_silent "bypass env var skips everything"

# =============================================================================
# FAIL-OPEN — payload shape variants
# =============================================================================

new_case_dir
run_hook "" "not-json"
assert_silent "fail-open on malformed JSON stdin"

new_case_dir
run_hook "" '{"session_id": "sid-1"}'
assert_silent "fail-open on missing transcript_path"

new_case_dir
write_transcript "$TRANSCRIPT" "compact-uuid-missing-sid"
PAYLOAD=$(python3 -c 'import json; print(json.dumps({"transcript_path": "'$TRANSCRIPT'", "cwd": "/tmp"}))')
run_hook "" "$PAYLOAD"
assert_silent "fail-open on missing session_id"

new_case_dir
write_transcript "$TRANSCRIPT" "compact-uuid-missing-cwd"
PAYLOAD=$(python3 -c 'import json; print(json.dumps({"transcript_path": "'$TRANSCRIPT'", "session_id": "s"}))')
run_hook "" "$PAYLOAD"
assert_silent "fail-open on missing cwd"

new_case_dir
# transcript_path that does not exist
PAYLOAD=$(payload_for "$T/does-not-exist.jsonl")
run_hook "export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_silent "fail-open when transcript file does not exist"

new_case_dir
# transcript is empty (zero lines)
: > "$TRANSCRIPT"
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_silent "silent on empty transcript file"

new_case_dir
# transcript with a malformed JSONL line containing the lexical marker
echo '{"type": "user", "isCompactSummary": true, broken json' > "$TRANSCRIPT"
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_silent "silent on transcript with malformed line"

new_case_dir
# transcript with isCompactSummary: false (must NOT trigger)
python3 - "$TRANSCRIPT" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, "w") as fh:
    fh.write(json.dumps({"type": "user", "isCompactSummary": False, "uuid": "x",
                         "timestamp": "2026-05-28T01:00:00.000Z"}) + "\n")
PY
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_silent "silent when isCompactSummary is false"

new_case_dir
# transcript with type=assistant but isCompactSummary=true (wrong type)
python3 - "$TRANSCRIPT" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, "w") as fh:
    fh.write(json.dumps({"type": "assistant", "isCompactSummary": True, "uuid": "x",
                         "timestamp": "2026-05-28T01:00:00.000Z"}) + "\n")
PY
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_silent "silent when isCompactSummary type is not user"

new_case_dir
# transcript with valid compaction but missing uuid
python3 - "$TRANSCRIPT" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, "w") as fh:
    fh.write(json.dumps({"type": "user", "isCompactSummary": True,
                         "timestamp": "2026-05-28T01:00:00.000Z"}) + "\n")
PY
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_silent "silent when compaction record has no uuid"

# =============================================================================
# TAIL-LINES env var
# =============================================================================

new_case_dir
# Write a transcript with a compaction at line 1 + 200 trailing benign lines.
write_transcript "$TRANSCRIPT" "compact-uuid-far"
python3 - "$TRANSCRIPT" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, "a") as fh:
    for i in range(200):
        fh.write(json.dumps({"type": "user", "uuid": f"filler-{i}",
                             "timestamp": "2026-05-28T01:00:00.000Z"}) + "\n")
PY
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
# Default tail (100) should miss the compaction
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_silent "silent when compaction is outside default tail window"
# Increased tail should find it
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'; export PRAXIS_POSTCOMPACT_TAIL_LINES=500" "$PAYLOAD"
assert_emit "emit when tail window enlarged via env var"

# =============================================================================
# STRIKE state integration
# =============================================================================

new_case_dir
write_transcript "$TRANSCRIPT" "compact-uuid-strikes"
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
# Build a fake strike state for session_id "sess-strike"
STATE_DIR="$T/state"
mkdir -p "$STATE_DIR/strikes"
cat > "$STATE_DIR/strikes/sess-strike.json" <<'EOF'
{"count": 2, "reasons": ["violation A", "violation B"]}
EOF
PAYLOAD=$(payload_for "$TRANSCRIPT" "sess-strike")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'; export PRAXIS_STATE_DIR='$STATE_DIR'" "$PAYLOAD"
assert_emit "emit includes strike state when state file present"
echo "$LAST_OUT" | grep -q "2/3" \
  && { echo "PASS  [emit body: strike count rendered]"; PASS=$((PASS + 1)); } \
  || { echo "FAIL  [emit body: strike count rendered]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("emit body: strike count rendered"); }
echo "$LAST_OUT" | grep -q "violation A" \
  && { echo "PASS  [emit body: strike reason 1 rendered]"; PASS=$((PASS + 1)); } \
  || { echo "FAIL  [emit body: strike reason 1 rendered]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("emit body: strike reason 1 rendered"); }

new_case_dir
write_transcript "$TRANSCRIPT" "compact-uuid-nostrike"
make_mock_git_clean "$MOCK_BIN" "main"
make_mock_gh_no_pr "$MOCK_BIN"
STATE_DIR="$T/state"
mkdir -p "$STATE_DIR/strikes"
# No strikes for this session
PAYLOAD=$(payload_for "$TRANSCRIPT" "sess-no-strike")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'; export PRAXIS_STATE_DIR='$STATE_DIR'" "$PAYLOAD"
assert_emit "emit with absent strike state shows 0/3"
echo "$LAST_OUT" | grep -q "0/3" \
  && { echo "PASS  [emit body: 0/3 fallback rendered]"; PASS=$((PASS + 1)); } \
  || { echo "FAIL  [emit body: 0/3 fallback rendered]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("emit body: 0/3 fallback rendered"); }

# =============================================================================
# Detached HEAD (git branch returns empty)
# =============================================================================

new_case_dir
write_transcript "$TRANSCRIPT" "compact-uuid-detached"
mkdir -p "$MOCK_BIN"
cat > "$MOCK_BIN/git" <<'EOF'
#!/bin/bash
# Empty branch name → simulates detached HEAD
exit 0
EOF
chmod +x "$MOCK_BIN/git"
make_mock_gh_no_pr "$MOCK_BIN"
PAYLOAD=$(payload_for "$TRANSCRIPT")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_emit "emit on detached HEAD (branch empty)"
echo "$LAST_OUT" | grep -q "detached/unknown" \
  && { echo "PASS  [emit body: detached HEAD placeholder]"; PASS=$((PASS + 1)); } \
  || { echo "FAIL  [emit body: detached HEAD placeholder]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("emit body: detached HEAD placeholder"); }

# =============================================================================
# Active PR rendering
# =============================================================================

new_case_dir
write_transcript "$TRANSCRIPT" "compact-uuid-withpr"
make_mock_git_clean "$MOCK_BIN" "feat-x"
make_mock_gh_one_pr "$MOCK_BIN" 999 "https://github.com/o/r/pull/999" "feat: example"
PAYLOAD=$(payload_for "$TRANSCRIPT")
run_hook "export PATH='$MOCK_BIN:'\$PATH; export PRAXIS_POSTCOMPACT_CONTEXT_FILE='$STATE_FILE'" "$PAYLOAD"
assert_emit "emit with active PR rendered"
echo "$LAST_OUT" | grep -q "#999" \
  && { echo "PASS  [emit body: PR number rendered]"; PASS=$((PASS + 1)); } \
  || { echo "FAIL  [emit body: PR number rendered]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("emit body: PR number rendered"); }
echo "$LAST_OUT" | grep -q "github.com/o/r/pull/999" \
  && { echo "PASS  [emit body: PR URL rendered]"; PASS=$((PASS + 1)); } \
  || { echo "FAIL  [emit body: PR URL rendered]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("emit body: PR URL rendered"); }

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
