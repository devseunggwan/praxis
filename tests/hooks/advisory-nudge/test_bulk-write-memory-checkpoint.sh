#!/usr/bin/env bash
# test_bulk-write-memory-checkpoint.sh — coverage for bulk-write-memory-checkpoint hook
#
# Synthesizes Claude Code PreToolUse payloads and asserts advisory vs. silent
# behavior.  Uses unique session_id per test group to isolate state.
#
# Expectation codes:
#   "advisory:<needle>" — exit 0 + stderr contains <needle>
#   "silent"            — exit 0 + stderr empty
#
# Usage: bash tests/hooks/advisory-nudge/test_bulk-write-memory-checkpoint.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/bulk-write-memory-checkpoint/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# ---------------------------------------------------------------------------
# run_case name expectation tool_name file_path session_id [ENV=VAL ...]
# ---------------------------------------------------------------------------
run_case() {
  local name="$1" expectation="$2" tool_name="$3" file_path="$4" session_id="$5"
  shift 5 || true
  local extra_env=("$@")

  local payload
  payload=$(python3 -c '
import json, sys
d = {
    "tool_name": sys.argv[1],
    "tool_input": {"file_path": sys.argv[2]},
    "session_id": sys.argv[3],
}
print(json.dumps(d))
' "$tool_name" "$file_path" "$session_id")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)

  printf '%s' "$payload" | env "${extra_env[@]}" python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    advisory:*)
      local needle="${expectation#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$needle"*) ;;
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

# Notebook-specific helper: builds payload with notebook_path instead of file_path.
run_case_notebook() {
  local name="$1" expectation="$2" notebook_path="$3" session_id="$4"
  shift 4 || true
  local extra_env=("$@")

  local payload
  payload=$(python3 -c '
import json, sys
d = {
    "tool_name": "NotebookEdit",
    "tool_input": {"notebook_path": sys.argv[1]},
    "session_id": sys.argv[2],
}
print(json.dumps(d))
' "$notebook_path" "$session_id")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)

  printf '%s' "$payload" | env "${extra_env[@]}" python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    advisory:*)
      local needle="${expectation#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$needle"*) ;;
        *) ok=0 ;;
      esac
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# SOT-flagged path buckets — each uses a FRESH session per bucket to isolate
# state, then tests: 1st write silent, 2nd write advisory, 3rd write silent
# (deduped per session+bucket).
# ---------------------------------------------------------------------------

# vault/
SID_VAULT="vault-$(date +%s)-$$"
run_case "vault: first write is silent (below threshold)" \
  "silent" "Write" "vault/notes/my-note.md" "$SID_VAULT"
run_case "vault: second write fires advisory" \
  "advisory:[praxis:bulk-write-checkpoint]" "Write" "vault/notes/other.md" "$SID_VAULT"
run_case "vault: third write is silent (deduped)" \
  "silent" "Write" "vault/notes/third.md" "$SID_VAULT"

# wiki/
SID_WIKI="wiki-$(date +%s)-$$"
run_case "wiki: first write is silent" \
  "silent" "Write" "wiki/pages/intro.md" "$SID_WIKI"
run_case "wiki: second write fires advisory" \
  "advisory:[praxis:bulk-write-checkpoint]" "Edit" "wiki/pages/faq.md" "$SID_WIKI"

# .claude/
SID_CLAUDE_DIR="claude-dir-$(date +%s)-$$"
run_case ".claude/: first write is silent" \
  "silent" "Write" ".claude/settings.json" "$SID_CLAUDE_DIR"
run_case ".claude/: second write fires advisory" \
  "advisory:[praxis:bulk-write-checkpoint]" "Write" ".claude/MEMORY.md" "$SID_CLAUDE_DIR"

# skills/
SID_SKILLS="skills-$(date +%s)-$$"
run_case "skills/: first write is silent" \
  "silent" "Write" "skills/my-skill/SKILL.md" "$SID_SKILLS"
run_case "skills/: second write fires advisory" \
  "advisory:[praxis:bulk-write-checkpoint]" "Write" "skills/other-skill/SKILL.md" "$SID_SKILLS"

# AGENTS.md basename
SID_AGENTS="agents-$(date +%s)-$$"
run_case "AGENTS.md: first write is silent" \
  "silent" "Write" "/repo/AGENTS.md" "$SID_AGENTS"
run_case "AGENTS.md: second write fires advisory" \
  "advisory:[praxis:bulk-write-checkpoint]" "Write" "/other/AGENTS.md" "$SID_AGENTS"

# CLAUDE.md basename
SID_CLAUDEMD="claudemd-$(date +%s)-$$"
run_case "CLAUDE.md: first write is silent" \
  "silent" "Edit" "/repo/CLAUDE.md" "$SID_CLAUDEMD"
run_case "CLAUDE.md: second write fires advisory" \
  "advisory:[praxis:bulk-write-checkpoint]" "Edit" "/other/CLAUDE.md" "$SID_CLAUDEMD"

# ---------------------------------------------------------------------------
# Cross-bucket isolation — two different buckets in one session each get
# their own per-bucket counter; second vault write in a session that already
# had a skills advisory does NOT trigger vault advisory yet.
# ---------------------------------------------------------------------------

SID_CROSS="cross-$(date +%s)-$$"
run_case "cross-bucket: first skills write is silent" \
  "silent" "Write" "skills/foo/SKILL.md" "$SID_CROSS"
run_case "cross-bucket: first vault write is silent (separate bucket)" \
  "silent" "Write" "vault/note.md" "$SID_CROSS"
run_case "cross-bucket: second skills write fires advisory" \
  "advisory:[praxis:bulk-write-checkpoint]" "Write" "skills/bar/SKILL.md" "$SID_CROSS"
run_case "cross-bucket: second vault write fires advisory" \
  "advisory:[praxis:bulk-write-checkpoint]" "Write" "vault/note2.md" "$SID_CROSS"

# ---------------------------------------------------------------------------
# Boundary collision: paths that CONTAIN sot-like strings but are NOT SOT
# ---------------------------------------------------------------------------

SID_BOUNDARY="boundary-$(date +%s)-$$"
# /my-claude-project/ — `.claude` is not a standalone component
run_case "boundary: my-claude-project/ is not SOT (silent)" \
  "silent" "Write" "/home/user/my-claude-project/file.md" "$SID_BOUNDARY"
run_case "boundary: my-claude-project/ second write still silent" \
  "silent" "Write" "/home/user/my-claude-project/other.md" "$SID_BOUNDARY"

# skills-playground/ — `skills` is not a standalone component of `skills-playground`
run_case "boundary: skills-playground/ is not SOT (silent)" \
  "silent" "Write" "skills-playground/test.py" "$SID_BOUNDARY"

# CLAUDE.md.bak — basename does not equal CLAUDE.md
run_case "boundary: CLAUDE.md.bak is not SOT (silent)" \
  "silent" "Write" "/repo/CLAUDE.md.bak" "$SID_BOUNDARY"

# agentsCLAUDE.md — basename does not equal AGENTS.md or CLAUDE.md
run_case "boundary: agentsCLAUDE.md is not SOT (silent)" \
  "silent" "Write" "/repo/agentsCLAUDE.md" "$SID_BOUNDARY"

# ---------------------------------------------------------------------------
# NotebookEdit tool
# ---------------------------------------------------------------------------

SID_NOTEBOOK="nb-$(date +%s)-$$"
run_case_notebook "NotebookEdit: first vault notebook is silent" \
  "silent" "vault/analysis.ipynb" "$SID_NOTEBOOK"
run_case_notebook "NotebookEdit: second vault notebook fires advisory" \
  "advisory:[praxis:bulk-write-checkpoint]" "vault/analysis2.ipynb" "$SID_NOTEBOOK"

# ---------------------------------------------------------------------------
# Non-SOT paths — always silent
# ---------------------------------------------------------------------------

SID_NONSOT="nonsot-$(date +%s)-$$"
run_case "non-SOT: src/main.py is silent" \
  "silent" "Write" "src/main.py" "$SID_NONSOT"
run_case "non-SOT: README.md is silent" \
  "silent" "Write" "README.md" "$SID_NONSOT"
run_case "non-SOT: docs/guide.md is silent" \
  "silent" "Write" "docs/guide.md" "$SID_NONSOT"
run_case "non-SOT: tests/test_foo.py is silent" \
  "silent" "Write" "tests/test_foo.py" "$SID_NONSOT"

# ---------------------------------------------------------------------------
# Bypass env var
# ---------------------------------------------------------------------------

SID_BYPASS="bypass-$(date +%s)-$$"
# Normally 2nd vault write would fire advisory — bypass suppresses it.
run_case "bypass: first vault write (silent)" \
  "silent" "Write" "vault/note1.md" "$SID_BYPASS"
run_case "bypass: PRAXIS_BULK_WRITE_BYPASS=1 suppresses advisory" \
  "silent" "Write" "vault/note2.md" "$SID_BYPASS" \
  "PRAXIS_BULK_WRITE_BYPASS=1"

# ---------------------------------------------------------------------------
# Fail-open: non-target tool name
# ---------------------------------------------------------------------------

SID_BASH="bash-$(date +%s)-$$"
run_case "fail-open: Bash tool is silent" \
  "silent" "Bash" "vault/note.md" "$SID_BASH"
run_case "fail-open: Read tool is silent" \
  "silent" "Read" "vault/note.md" "$SID_BASH"

# ---------------------------------------------------------------------------
# Fail-open: malformed JSON stdin
# ---------------------------------------------------------------------------

{
  printf 'not-json' | python3 "$HOOK" >/dev/null 2>/dev/null
  local_rc=$?
  if [ "$local_rc" -eq 0 ]; then
    echo "PASS  [fail-open: malformed JSON stdin exits 0]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [fail-open: malformed JSON stdin exits 0] rc=$local_rc"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("fail-open: malformed JSON stdin exits 0")
  fi
}

# ---------------------------------------------------------------------------
# Fail-open: internal logic error (missing tool_input)
# ---------------------------------------------------------------------------

{
  printf '{"tool_name":"Write"}' | python3 "$HOOK" >/dev/null 2>/dev/null
  local_rc=$?
  if [ "$local_rc" -eq 0 ]; then
    echo "PASS  [fail-open: missing tool_input exits 0]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [fail-open: missing tool_input exits 0] rc=$local_rc"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("fail-open: missing tool_input exits 0")
  fi
}

# ---------------------------------------------------------------------------
# Absolute path with nested SOT (e.g. /home/user/project/skills/x.py)
# ---------------------------------------------------------------------------

SID_ABS="abs-$(date +%s)-$$"
run_case "absolute path: first /project/skills/ write is silent" \
  "silent" "Write" "/home/user/project/skills/x.py" "$SID_ABS"
run_case "absolute path: second /project/skills/ write fires advisory" \
  "advisory:[praxis:bulk-write-checkpoint]" "Write" "/home/user/project/skills/y.py" "$SID_ABS"

# ---------------------------------------------------------------------------
# Summary
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
