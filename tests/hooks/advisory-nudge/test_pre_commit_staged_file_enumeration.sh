#!/bin/bash
# test_pre_commit_staged_file_enumeration.sh — coverage for
# hooks/advisory-nudge/pre-commit-staged-file-enumeration/impl.py
#
# Builds a real fixture git repo, stages additions through different paths
# (bash redirect vs. simulated Write via transcript), then feeds the hook
# synthesized PreToolUse(Bash) payloads and asserts:
#   surface:<marker>  — exit 0, stderr contains <marker>
#   silent            — exit 0, stderr empty
#
# The hook shells out to `git` in its own process cwd, so every real-commit
# case runs the hook from inside the fixture repo via a subshell.
#
# Usage: bash tests/hooks/advisory-nudge/test_pre_commit_staged_file_enumeration.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../../" && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/pre-commit-staged-file-enumeration/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

record() {
  local name="$1" ok="$2" rc="$3" err="$4" expectation="$5"
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# build_transcript <out_path> <abs_file_path>...
# Writes a minimal transcript JSONL: one assistant message whose content is a
# Write tool_use block per supplied file path.
build_transcript() {
  local out="$1"; shift
  python3 -c '
import json, sys
out = sys.argv[1]
paths = sys.argv[2:]
blocks = [{"type": "tool_use", "name": "Write", "input": {"file_path": p}}
          for p in paths]
line = {"message": {"role": "assistant", "content": blocks}}
with open(out, "w") as f:
    f.write(json.dumps(line) + "\n")
' "$out" "$@"
}

# build_transcript_named <out_path> <tool_name> <key> <abs_file_path>
# One tool_use block for a non-Write file tool (Edit / MultiEdit / NotebookEdit).
build_transcript_named() {
  local out="$1" tool="$2" key="$3" path="$4"
  python3 -c '
import json, sys
out, tool, key, path = sys.argv[1:5]
line = {"message": {"role": "assistant",
    "content": [{"type": "tool_use", "name": tool, "input": {key: path}}]}}
with open(out, "w") as f:
    f.write(json.dumps(line) + "\n")
' "$out" "$tool" "$key" "$path"
}

# build_transcript_failed <out_path> <abs_file_path>
# A Write tool_use (id=w1) whose tool_result carries is_error:true — the write
# was rejected/failed, so its target must NOT be counted as seen.
build_transcript_failed() {
  local out="$1" path="$2"
  python3 -c '
import json, sys
out, path = sys.argv[1:3]
lines = [
  {"message": {"role": "assistant", "content": [
    {"type": "tool_use", "id": "w1", "name": "Write",
     "input": {"file_path": path}}]}},
  {"message": {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "w1", "is_error": True,
     "content": "blocked"}]}},
]
with open(out, "w") as f:
    for ln in lines:
        f.write(json.dumps(ln) + "\n")
' "$out" "$path"
}

# run_repo_case name expectation command transcript_path [env_assign]
#   Runs the hook from inside $REPO with the given command + transcript.
run_repo_case() {
  local name="$1" expectation="$2" command="$3" transcript="$4" env_assign="${5:-}"
  local payload
  payload=$(python3 -c '
import json, sys
d = {"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}
if sys.argv[2]:
    d["transcript_path"] = sys.argv[2]
print(json.dumps(d))
' "$command" "$transcript")

  local err_file rc err
  err_file=$(mktemp)
  if [ -n "$env_assign" ]; then
    ( cd "$REPO" && echo "$payload" | env "$env_assign" "$HOOK" >/dev/null 2>"$err_file" )
  else
    ( cd "$REPO" && echo "$payload" | "$HOOK" >/dev/null 2>"$err_file" )
  fi
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    surface:*)
      local marker="${expectation#surface:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in *"$marker"*) ;; *) ok=0 ;; esac
      ;;
    *)
      echo "FAIL  [$name] unknown expectation: $expectation"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac
  record "$name" "$ok" "$rc" "$err" "$expectation"
}

# run_dir_case name expectation dir command transcript — like run_repo_case but
# runs the hook from an arbitrary repo dir (for fresh-fixture cases).
run_dir_case() {
  local name="$1" expectation="$2" dir="$3" command="$4" transcript="$5"
  local payload
  payload=$(python3 -c '
import json, sys
d = {"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}
if sys.argv[2]:
    d["transcript_path"] = sys.argv[2]
print(json.dumps(d))
' "$command" "$transcript")
  local err_file rc err
  err_file=$(mktemp)
  ( cd "$dir" && echo "$payload" | "$HOOK" >/dev/null 2>"$err_file" )
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"
  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    surface:*)
      local marker="${expectation#surface:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in *"$marker"*) ;; *) ok=0 ;; esac
      ;;
  esac
  record "$name" "$ok" "$rc" "$err" "$expectation"
}

# assert_absent name command transcript token — surface fired but token NOT listed
run_repo_case_absent_token() {
  local name="$1" command="$2" transcript="$3" token="$4"
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "transcript_path": sys.argv[2]}))
' "$command" "$transcript")
  local err_file rc err
  err_file=$(mktemp)
  ( cd "$REPO" && echo "$payload" | "$HOOK" >/dev/null 2>"$err_file" )
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"
  local ok=1
  [ "$rc" -eq 0 ] || ok=0
  case "$err" in *"[staged-enum]"*) ;; *) ok=0 ;; esac   # must surface
  case "$err" in *"$token"*) ok=0 ;; *) ;; esac          # but not this token
  record "$name" "$ok" "$rc" "$err" "surface-without-token:$token"
}

# ---------------------------------------------------------------------------
# Fixture repo
# ---------------------------------------------------------------------------
REPO=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
git -C "$REPO" init -q
git -C "$REPO" config user.email test@example.com
git -C "$REPO" config user.name "Test User"
# Seed a tracked file so HEAD exists and modification-only cases have a base.
printf 'seed\n' > "$REPO/tracked.txt"
git -C "$REPO" add tracked.txt
git -C "$REPO" -c commit.gpgsign=false commit -q -m "seed"

TRANSCRIPT_EMPTY=$(mktemp)
build_transcript "$TRANSCRIPT_EMPTY"   # no Write blocks

# ---------------------------------------------------------------------------
# === CORE surface path ===
# ---------------------------------------------------------------------------

# C1: bash-created staged addition, not in transcript → surfaced.
printf 'from bash\n' > "$REPO/bash_made.txt"
git -C "$REPO" add bash_made.txt
run_repo_case "bash-created staged addition is surfaced" \
  "surface:bash_made.txt" \
  "git commit -m 'add file'" \
  "$TRANSCRIPT_EMPTY"

# C9: compound `&& git commit` still detected.
run_repo_case "compound chain git commit is detected" \
  "surface:bash_made.txt" \
  "echo ready && git commit -m 'add file'" \
  "$TRANSCRIPT_EMPTY"

# C10: path-prefixed git binary is detected (hook shells bare git regardless).
run_repo_case "path-prefixed git commit is detected" \
  "surface:bash_made.txt" \
  "/usr/bin/git commit -m 'add file'" \
  "$TRANSCRIPT_EMPTY"

# ---------------------------------------------------------------------------
# === SILENT: addition seen via Write transcript ===
# ---------------------------------------------------------------------------

# C2: same addition, but transcript records a Write for its absolute path → silent.
TRANSCRIPT_SEEN=$(mktemp)
build_transcript "$TRANSCRIPT_SEEN" "$REPO/bash_made.txt"
run_repo_case "addition present in Write transcript is silent" \
  "silent" \
  "git commit -m 'add file'" \
  "$TRANSCRIPT_SEEN"

# C3: mixed — one Write-seen, one bash-only → only the bash-only one surfaced.
printf 'second bash\n' > "$REPO/bash_only.txt"
git -C "$REPO" add bash_only.txt
# transcript sees bash_made.txt but NOT bash_only.txt
run_repo_case "mixed: only the unseen addition is surfaced" \
  "surface:bash_only.txt" \
  "git commit -m 'add both'" \
  "$TRANSCRIPT_SEEN"
run_repo_case_absent_token "mixed: seen addition is not listed" \
  "git commit -m 'add both'" \
  "$TRANSCRIPT_SEEN" \
  "bash_made.txt"

# C11: `git -c k=v commit` — global value flag is skipped, subcommand detected.
run_repo_case "git -c global value flag still detects commit" \
  "surface:bash_only.txt" \
  "git -c user.email=x commit -m 'add both'" \
  "$TRANSCRIPT_SEEN"

# Edit-seen addition is silent (seen-set covers Edit, not just Write).
printf 'via edit\n' > "$REPO/edit_made.txt"
git -C "$REPO" add edit_made.txt
TRANSCRIPT_EDIT=$(mktemp)
build_transcript_named "$TRANSCRIPT_EDIT" "Edit" "file_path" "$REPO/edit_made.txt"
# reset the index so only edit_made.txt is a new addition beyond what's tracked
run_repo_case_absent_token "Edit-seen addition is not listed" \
  "git commit -m 'edit add'" \
  "$TRANSCRIPT_EDIT" \
  "edit_made.txt"

# MultiEdit-seen addition is silent (seen-set covers MultiEdit).
TRANSCRIPT_MULTI=$(mktemp)
build_transcript_named "$TRANSCRIPT_MULTI" "MultiEdit" "file_path" "$REPO/edit_made.txt"
run_repo_case_absent_token "MultiEdit-seen addition is not listed" \
  "git commit -m 'multiedit add'" \
  "$TRANSCRIPT_MULTI" \
  "edit_made.txt"

# ---------------------------------------------------------------------------
# === SILENT: non-fresh-commit commands ===
# ---------------------------------------------------------------------------

# --amend folds staged additions into the replacement commit → still inspected.
run_repo_case "git commit --amend with unseen additions is surfaced" \
  "surface:bash_made.txt" \
  "git commit --amend --no-edit" \
  "$TRANSCRIPT_EMPTY"

run_repo_case "git commit --dry-run is silent (prints, no commit)" \
  "silent" \
  "git commit --dry-run" \
  "$TRANSCRIPT_EMPTY"

run_repo_case "git commit --help is silent (manpage, no commit)" \
  "silent" \
  "git commit --help" \
  "$TRANSCRIPT_EMPTY"

run_repo_case "git status is silent (no commit subcommand)" \
  "silent" \
  "git status" \
  "$TRANSCRIPT_EMPTY"

run_repo_case "git commit-tree is silent (single token != commit)" \
  "silent" \
  "git commit-tree HEAD^{tree} -m x" \
  "$TRANSCRIPT_EMPTY"

run_repo_case "quoted literal git commit is silent" \
  "silent" \
  "echo 'git commit'" \
  "$TRANSCRIPT_EMPTY"

# Documented shared-tokenizer boundary (see spec.md "Detection boundaries"):
# a git commit inside an assignment's command substitution is NOT detected —
# same as the blocking sibling block-rename-sweep-survivors. Pins the current
# behavior so a future _hook_utils tokenizer change surfaces here.
run_repo_case "assignment command-substitution commit is not detected (boundary)" \
  "silent" \
  "result=\$(git commit -m x)" \
  "$TRANSCRIPT_EMPTY"

# ---------------------------------------------------------------------------
# === SILENT: no staged additions ===
# ---------------------------------------------------------------------------

# Modify a tracked file (staged M, no A). Reset the index of the additions
# first so only a modification is staged.
MODREPO=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
git -C "$MODREPO" init -q
git -C "$MODREPO" config user.email test@example.com
git -C "$MODREPO" config user.name "Test User"
printf 'v1\n' > "$MODREPO/tracked.txt"
git -C "$MODREPO" add tracked.txt
git -C "$MODREPO" -c commit.gpgsign=false commit -q -m seed
printf 'v2\n' > "$MODREPO/tracked.txt"
git -C "$MODREPO" add tracked.txt   # staged modification, diff-filter=A empty
{
  payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash",
    "tool_input": {"command": "git commit -m mod"},
    "transcript_path": sys.argv[1]}))' "$TRANSCRIPT_EMPTY")
  err_file=$(mktemp)
  ( cd "$MODREPO" && echo "$payload" | "$HOOK" >/dev/null 2>"$err_file" )
  rc=$?; err=$(cat "$err_file"); rm -f "$err_file"
  ok=1; [ "$rc" -eq 0 ] || ok=0; [ -z "$err" ] || ok=0
  record "modification-only staged is silent" "$ok" "$rc" "$err" "silent"
}
rm -rf "$MODREPO" 2>/dev/null || true

# ---------------------------------------------------------------------------
# === Codex round-1 fixes: amend / symlink / failed-tool (fresh fixtures) ===
# ---------------------------------------------------------------------------

# --amend with a CLEAN index (no staged additions) → silent.
AMENDREPO=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
git -C "$AMENDREPO" init -q
git -C "$AMENDREPO" config user.email test@example.com
git -C "$AMENDREPO" config user.name "Test User"
printf 'seed\n' > "$AMENDREPO/tracked.txt"
git -C "$AMENDREPO" add tracked.txt
git -C "$AMENDREPO" -c commit.gpgsign=false commit -q -m seed
run_dir_case "amend with clean index is silent" \
  "silent" "$AMENDREPO" "git commit --amend --no-edit" "$TRANSCRIPT_EMPTY"
rm -rf "$AMENDREPO" 2>/dev/null || true

# A staged symlink whose TARGET was Write-seen must NOT be suppressed:
# canonical() preserves the final component so alias != target.
SYMREPO=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
git -C "$SYMREPO" init -q
git -C "$SYMREPO" config user.email test@example.com
git -C "$SYMREPO" config user.name "Test User"
printf 'seed\n' > "$SYMREPO/seed.txt"
git -C "$SYMREPO" add seed.txt
git -C "$SYMREPO" -c commit.gpgsign=false commit -q -m seed
printf 'real\n' > "$SYMREPO/target.txt"
( cd "$SYMREPO" && ln -s target.txt alias )
git -C "$SYMREPO" add alias                     # stage only the symlink
TRANSCRIPT_SYM=$(mktemp)
build_transcript "$TRANSCRIPT_SYM" "$SYMREPO/target.txt"   # Write saw target
run_dir_case "staged symlink to a seen target is still surfaced" \
  "surface:alias" "$SYMREPO" "git commit -m 'add alias'" "$TRANSCRIPT_SYM"
rm -rf "$SYMREPO" 2>/dev/null || true
rm -f "$TRANSCRIPT_SYM" 2>/dev/null || true

# A FAILED Write (tool_result is_error) must not count its target as seen —
# a bash-staged file at that path is surfaced.
FAILREPO=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
git -C "$FAILREPO" init -q
git -C "$FAILREPO" config user.email test@example.com
git -C "$FAILREPO" config user.name "Test User"
printf 'seed\n' > "$FAILREPO/seed.txt"
git -C "$FAILREPO" add seed.txt
git -C "$FAILREPO" -c commit.gpgsign=false commit -q -m seed
printf 'via bash after failed write\n' > "$FAILREPO/contested.txt"
git -C "$FAILREPO" add contested.txt
TRANSCRIPT_FAIL=$(mktemp)
build_transcript_failed "$TRANSCRIPT_FAIL" "$FAILREPO/contested.txt"
run_dir_case "failed Write target is not counted as seen (surfaced)" \
  "surface:contested.txt" "$FAILREPO" "git commit -m x" "$TRANSCRIPT_FAIL"
rm -rf "$FAILREPO" 2>/dev/null || true
rm -f "$TRANSCRIPT_FAIL" 2>/dev/null || true

# ---------------------------------------------------------------------------
# === Documented boundary verification (spec.md "Limitations" / "Detection
# === boundaries") — pin the exact behavior so a regression is visible ===
# ---------------------------------------------------------------------------

# #1 single-call create+add+commit: at PreToolUse the file is not yet in the
# index, so the additions list is empty → SILENT (documented miss). Fresh repo
# with a clean index isolates the boundary.
SINGLEREPO=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
git -C "$SINGLEREPO" init -q
git -C "$SINGLEREPO" config user.email test@example.com
git -C "$SINGLEREPO" config user.name "Test User"
printf 'seed\n' > "$SINGLEREPO/seed.txt"
git -C "$SINGLEREPO" add seed.txt
git -C "$SINGLEREPO" -c commit.gpgsign=false commit -q -m seed
run_dir_case "single-call create+add+commit is silent (documented miss)" \
  "silent" "$SINGLEREPO" \
  "printf x > forgotten.txt && git add forgotten.txt && git commit -m y" \
  "$TRANSCRIPT_EMPTY"
rm -rf "$SINGLEREPO" 2>/dev/null || true

# #3a partial commit (`--only <path>`) over-lists ALL staged additions, not
# just the pathspec — pin by asserting the EXCLUDED file is still surfaced.
ONLYREPO=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
git -C "$ONLYREPO" init -q
git -C "$ONLYREPO" config user.email test@example.com
git -C "$ONLYREPO" config user.name "Test User"
printf 'seed\n' > "$ONLYREPO/seed.txt"
git -C "$ONLYREPO" add seed.txt
git -C "$ONLYREPO" -c commit.gpgsign=false commit -q -m seed
printf a > "$ONLYREPO/only_this.txt"
printf b > "$ONLYREPO/but_not_this.txt"
git -C "$ONLYREPO" add only_this.txt but_not_this.txt
run_dir_case "partial commit --only over-lists the excluded addition (boundary)" \
  "surface:but_not_this.txt" "$ONLYREPO" \
  "git commit --only only_this.txt -m y" "$TRANSCRIPT_EMPTY"
rm -rf "$ONLYREPO" 2>/dev/null || true

# #3b a `git commit` literal inside a heredoc body is data, not a command, but
# the shared tokenizer line-splits it → false-surface. Pin the boundary.
HEREREPO=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
git -C "$HEREREPO" init -q
git -C "$HEREREPO" config user.email test@example.com
git -C "$HEREREPO" config user.name "Test User"
printf 'seed\n' > "$HEREREPO/seed.txt"
git -C "$HEREREPO" add seed.txt
git -C "$HEREREPO" -c commit.gpgsign=false commit -q -m seed
printf data > "$HEREREPO/staged.txt"
git -C "$HEREREPO" add staged.txt
run_dir_case "git commit literal in heredoc body false-surfaces (boundary)" \
  "surface:staged.txt" "$HEREREPO" \
  "$(printf 'cat > guide.md <<%sEOF%s\ngit commit -m in-body\nEOF' "'" "'")" \
  "$TRANSCRIPT_EMPTY"
rm -rf "$HEREREPO" 2>/dev/null || true

# ---------------------------------------------------------------------------
# === SILENT: opt-out marker + env bypass ===
# ---------------------------------------------------------------------------

run_repo_case "opt-out marker suppresses surface" \
  "silent" \
  "git commit -m 'add file'  # [staged-enum-ack]" \
  "$TRANSCRIPT_EMPTY"

run_repo_case "env bypass suppresses surface" \
  "silent" \
  "git commit -m 'add file'" \
  "$TRANSCRIPT_EMPTY" \
  "PRAXIS_SKIP_STAGED_FILE_ENUM=1"

# ---------------------------------------------------------------------------
# === fail-open: missing / unreadable transcript ===
# ---------------------------------------------------------------------------

# Missing transcript_path key → silent (cannot compute seen-set).
run_repo_case "missing transcript_path is silent (fail-open)" \
  "silent" \
  "git commit -m 'add file'" \
  ""

# Non-existent transcript file → silent.
run_repo_case "non-existent transcript file is silent (fail-open)" \
  "silent" \
  "git commit -m 'add file'" \
  "/no/such/transcript-$$.jsonl"

# ---------------------------------------------------------------------------
# === Infrastructure / fail-open cases (no repo cwd needed) ===
# ---------------------------------------------------------------------------

# Non-Bash tool → silent.
infra_err=$(mktemp)
echo '{"tool_name":"Read","tool_input":{"command":"git commit -m x"}}' \
  | "$HOOK" >/dev/null 2>"$infra_err"
infra_rc=$?; infra_content=$(cat "$infra_err"); rm -f "$infra_err"
ok=1; [ "$infra_rc" -eq 0 ] || ok=0; [ -z "$infra_content" ] || ok=0
record "non-Bash tool_name passthrough" "$ok" "$infra_rc" "$infra_content" "silent"

# Malformed JSON → silent.
mal_err=$(mktemp)
echo "not-valid-json" | "$HOOK" >/dev/null 2>"$mal_err"
mal_rc=$?; mal_content=$(cat "$mal_err"); rm -f "$mal_err"
ok=1; [ "$mal_rc" -eq 0 ] || ok=0; [ -z "$mal_content" ] || ok=0
record "malformed JSON stdin is silent" "$ok" "$mal_rc" "$mal_content" "silent"

# Empty command → silent.
empty_err=$(mktemp)
echo '{"tool_name":"Bash","tool_input":{"command":""}}' \
  | "$HOOK" >/dev/null 2>"$empty_err"
empty_rc=$?; empty_content=$(cat "$empty_err"); rm -f "$empty_err"
ok=1; [ "$empty_rc" -eq 0 ] || ok=0; [ -z "$empty_content" ] || ok=0
record "empty command is silent" "$ok" "$empty_rc" "$empty_content" "silent"

# Not inside a git repo → silent (run from a non-repo temp dir).
NONREPO=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
nonrepo_err=$(mktemp)
nonrepo_payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash",
    "tool_input": {"command": "git commit -m x"},
    "transcript_path": sys.argv[1]}))' "$TRANSCRIPT_EMPTY")
( cd "$NONREPO" && echo "$nonrepo_payload" | "$HOOK" >/dev/null 2>"$nonrepo_err" )
nonrepo_rc=$?; nonrepo_content=$(cat "$nonrepo_err"); rm -f "$nonrepo_err"
ok=1; [ "$nonrepo_rc" -eq 0 ] || ok=0; [ -z "$nonrepo_content" ] || ok=0
record "non-git-repo cwd is silent (fail-open)" "$ok" "$nonrepo_rc" "$nonrepo_content" "silent"
rm -rf "$NONREPO" 2>/dev/null || true

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
  echo "PASS  [main() is wrapped by the shared @fail_open guard]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [main() is wrapped by the shared @fail_open guard] rc=$_uncaught_rc out=$(echo "$_uncaught_out" | head -c 200)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("main() is wrapped by the shared @fail_open guard")
fi

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
rm -rf "$REPO" 2>/dev/null || true
rm -f "$TRANSCRIPT_EMPTY" "$TRANSCRIPT_SEEN" "$TRANSCRIPT_EDIT" \
  "$TRANSCRIPT_MULTI" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
fi

[ "$FAIL" -eq 0 ]
