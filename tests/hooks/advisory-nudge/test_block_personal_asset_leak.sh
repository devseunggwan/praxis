#!/bin/bash
# test_block_personal_asset_leak.sh — coverage for
# hooks/advisory-nudge/block-personal-asset-leak/impl.py
#
# Synthesizes Claude Code PreToolUse(Bash|Write|Edit) hook payloads and asserts:
#   warn   → exit 0 + stderr contains "REMINDER"
#   silent → exit 0 + stderr empty
#   block  → exit 2 + stderr contains "REMINDER" (when PRAXIS_PERSONAL_LEAK_STRICT=1)
#
# Class 2 (personal-repo reference, issue #658) cases run against throwaway
# local git fixtures whose origin remote URL encodes a team vs personal owner;
# no network access is needed — only the URL string matters to the hook.
#
# Usage: bash tests/hooks/advisory-nudge/test_block_personal_asset_leak.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/block-personal-asset-leak/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation strict payload_json [owners]
#   expectation: warn | silent | block
#   strict:      "strict" sets PRAXIS_PERSONAL_LEAK_STRICT=1, else advisory
#   owners:      optional; sets PRAXIS_PERSONAL_REPO_OWNERS (issue #658 owner
#                class). Omitted/empty → force-unset, asserting the
#                dotfiles-only baseline regardless of the invoking user's env.
run_case() {
  local name="$1" expectation="$2" strict="$3" payload="$4" owners="${5-}"

  local err_file
  err_file=$(mktemp)
  # env -u flags must precede assignments — env stops option parsing at the
  # first NAME=value word.
  local -a env_unsets=() env_sets=()
  if [ -n "$owners" ]; then
    env_sets+=("PRAXIS_PERSONAL_REPO_OWNERS=$owners")
  else
    env_unsets+=(-u PRAXIS_PERSONAL_REPO_OWNERS)
  fi
  if [ "$strict" = "strict" ]; then
    env_sets+=("PRAXIS_PERSONAL_LEAK_STRICT=1")
  else
    env_unsets+=(-u PRAXIS_PERSONAL_LEAK_STRICT)
  fi
  echo "$payload" | env "${env_unsets[@]}" "${env_sets[@]}" python3 "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    warn)
      [ "$rc" -eq 0 ] || ok=0
      echo "$err" | grep -q "REMINDER" || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q "REMINDER" || ok=0
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
    [ -n "$err" ] && echo "        stderr: $err" | head -c 400
  fi
}

echo "test_block_personal_asset_leak"

# --- leak detected → advisory ---
run_case "gh issue create + /Users dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title foo --body \"see /Users/alice/.claude/settings.json for config\""}}'

run_case "gh pr comment + /home .config path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 50 --body \"path is /home/bob/.config/foo/bar.json\""}}'

run_case "gh issue create -b form + dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t -b \"hook at /Users/carol/.claude/hooks/x.py\""}}'

run_case "gh issue create --body=value form + dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body=\"cfg /Users/dave/.codex/config.toml here\""}}'

run_case "gh pr review --body + dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr review 7 --request-changes --body \"remove /Users/erin/.aws/credentials ref\""}}'

run_case "gh issue edit + dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue edit 9 --body \"updated: /Users/frank/.ssh/config\""}}'

# --- false-positive boundary → silent ---
run_case "tilde ~/.claude form NOT flagged (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body \"edit ~/.claude/settings.json instead\""}}'

run_case "worktree /projects/ path NOT flagged (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title t --body \"built in /Users/alice/projects/praxis/hooks\""}}'

run_case "praxis hook name NOT flagged (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 1 --body \"the block-gh-state-all and pre-merge-approval-gate hooks fire here\""}}'

run_case "non-dotfiles absolute path NOT flagged (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body \"see /usr/local/bin/foo and /Users/alice/Documents/notes\""}}'

run_case "placeholder <name> username NOT flagged (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body \"docs write paths as /Users/<name>/.claude/settings.json\""}}'

# --- path-prefixed gh now detected (via _is_gh_binary) ---
run_case "path-prefixed /usr/bin/gh + dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"/usr/bin/gh issue create --title t --body \"cfg /Users/alice/.claude/x\""}}'

run_case "env-prefixed gh + dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"GH_TOKEN=xyz gh issue create --title t --body \"cfg /Users/alice/.claude/x\""}}'

# --- non-write / non-Bash → silent ---
run_case "gh issue list (not a write) silent" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list --search /Users/alice/.claude"}}'

run_case "Read tool (non-Bash) silent" \
  "silent" "advisory" \
  '{"tool_name":"Read","tool_input":{"file_path":"/Users/alice/.claude/x"}}'

run_case "gh write without body flag silent" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title foo"}}'

# --- duplicate body flags: leak in the LATER body must still fire (codex P2) ---
run_case "duplicate --body, leak in second (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body \"all clean here\" --body \"see /Users/alice/.claude/x\""}}'

run_case "duplicate --body, leak in first (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body \"/Users/alice/.claude/x\" --body \"clean\""}}'

# --- heredoc-variable body resolution (codex P2 round 3) ---
run_case "heredoc-var body with leak resolved (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"BODY=$(cat <<EOF\nsee /Users/alice/.claude/settings.json\nEOF\n)\ngh issue create --title t --body \"$BODY\""}}'

run_case "heredoc-var body clean (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"BODY=$(cat <<EOF\nall clean, use ~/.claude\nEOF\n)\ngh issue create --title t --body \"$BODY\""}}'

run_case "heredoc-var ${BODY} brace form resolved (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"BODY=$(cat <<MSG\nleak /home/bob/.config/x\nMSG\n)\ngh pr create --title t --body \"${BODY}\""}}'

# --- strict mode → block ---
run_case "strict mode + dotfiles path (block)" \
  "block" "strict" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"cfg at /Users/alice/.claude/settings.json\""}}'

run_case "strict mode + no marker (silent)" \
  "silent" "strict" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"all clean, use ~/.claude\""}}'

# --- malformed payload → fail-open ---
run_case "malformed JSON payload (silent fail-open)" \
  "silent" "advisory" \
  'not-json-at-all'

# --- multi-marker dedup (two distinct paths) → warn ---
run_case "two distinct dotfiles paths (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body \"/Users/alice/.claude/a and /home/bob/.config/b\""}}'

# --- --body-file disk read ---
BODY_FILE=$(mktemp)
printf 'config lives at /Users/grace/.claude/hooks/impl.py\n' > "$BODY_FILE"
run_case "gh issue create --body-file with dotfiles path (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title t --body-file $BODY_FILE\"}}"
rm -f "$BODY_FILE"

CLEAN_BODY_FILE=$(mktemp)
printf 'all paths use the portable ~/.claude form\n' > "$CLEAN_BODY_FILE"
run_case "gh issue create --body-file clean (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title t --body-file $CLEAN_BODY_FILE\"}}"
rm -f "$CLEAN_BODY_FILE"

run_case "gh issue create --body-file unreadable path (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body-file /nonexistent/does-not-exist-12345.md"}}'

# --- relative --body-file resolved against payload cwd (codex P2 round 2) ---
REL_DIR=$(mktemp -d)
printf 'cfg at /Users/grace/.claude/hooks/impl.py\n' > "$REL_DIR/body.md"
run_case "relative --body-file resolved against payload cwd (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title t -F body.md\"},\"cwd\":\"$REL_DIR\"}"
run_case "relative --body-file with wrong cwd (silent fail-open)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t -F body.md"},"cwd":"/nonexistent/dir-12345"}'
rm -rf "$REL_DIR"

# ===========================================================================
# Personal-repo owner class (issue #658) — opt-in via PRAXIS_PERSONAL_REPO_OWNERS
# ===========================================================================
# Two local git fixtures stand in for a team repo (origin owner != personal)
# and a personal repo (origin owner ∈ PRAXIS_PERSONAL_REPO_OWNERS). No network
# access — only the remote URL string matters to the hook.
TEAM_REPO=$(mktemp -d)
git -C "$TEAM_REPO" init -q
git -C "$TEAM_REPO" remote add origin https://github.com/exampleorg/team-wiki.git
printf '.omc/\n' > "$TEAM_REPO/.gitignore"

PERSONAL_REPO=$(mktemp -d)
git -C "$PERSONAL_REPO" init -q
git -C "$PERSONAL_REPO" remote add origin git@github.com:testowner/scratchpad.git

# --- Write/Edit surface: owner ref toward team repo → warn ---
run_case "Write owner-ref into team repo (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$TEAM_REPO/wiki/summary.md\",\"content\":\"evidence at testowner/scratchpad#209\"}}" \
  "testowner"

run_case "Edit new_string owner-ref into team repo (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TEAM_REPO/wiki/summary.md\",\"old_string\":\"x\",\"new_string\":\"see testowner/scratchpad for the query\"}}" \
  "testowner"

run_case "Write owner-ref URL form into team repo (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$TEAM_REPO/wiki/url.md\",\"content\":\"https://github.com/testowner/scratchpad#9\"}}" \
  "testowner"

run_case "Write owner-ref case-insensitive (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$TEAM_REPO/wiki/case.md\",\"content\":\"see TestOwner/Scratchpad#3\"}}" \
  "testowner"

run_case "Write dotfiles abs path into team repo (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$TEAM_REPO/wiki/dot.md\",\"content\":\"cfg at /Users/alice/.claude/settings.json\"}}" \
  "testowner"

# --- Write/Edit surface: target discrimination → silent ---
run_case "Write owner-ref into personal repo (silent — own repo exempt)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$PERSONAL_REPO/notes.md\",\"content\":\"see testowner/scratchpad#209\"}}" \
  "testowner"

run_case "Write owner-ref into gitignored path (silent — scratch exempt)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$TEAM_REPO/.omc/plans/draft.md\",\"content\":\"see testowner/scratchpad#209\"}}" \
  "testowner"

run_case "Write owner-ref into non-git dir (silent fail-open)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/nonexistent-root-dir-658/x.md\",\"content\":\"see testowner/scratchpad#209\"}}" \
  "testowner"

run_case "Write clean content into team repo (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$TEAM_REPO/wiki/clean.md\",\"content\":\"all evidence inlined below, no external refs\"}}" \
  "testowner"

run_case "Write prefixed non-owner slug NOT flagged (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$TEAM_REPO/wiki/boundary.md\",\"content\":\"see xtestowner/foo and nottestowner/bar\"}}" \
  "testowner"

run_case "Write worktree path with owner-as-username NOT flagged (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$TEAM_REPO/wiki/path.md\",\"content\":\"built in /Users/testowner/projects/praxis/hooks and ./testowner/tmp\"}}" \
  "testowner"

# --- env unset → Write/Edit surface fully inactive (issue #658 AC) ---
run_case "Write owner-ref, env unset (silent — surface opt-in)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$TEAM_REPO/wiki/summary.md\",\"content\":\"see testowner/scratchpad#209 and /Users/alice/.claude/x\"}}"

# --- Bash gh surface: owner class target discrimination ---
run_case "gh create --repo personal target + owner ref (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --repo testowner/scratchpad --title t --body \"ref testowner/scratchpad#209\""}}' \
  "testowner"

run_case "gh create --repo org target + owner ref (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --repo exampleorg/team --title t --body \"ref testowner/scratchpad#209\""}}' \
  "testowner"

run_case "gh create no --repo, cwd=team repo + owner ref (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title t --body \\\"ref testowner/scratchpad#209\\\"\"},\"cwd\":\"$TEAM_REPO\"}" \
  "testowner"

run_case "gh create no --repo, cwd=personal repo + owner ref (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title t --body \\\"ref testowner/scratchpad#209\\\"\"},\"cwd\":\"$PERSONAL_REPO\"}" \
  "testowner"

run_case "gh create owner ref, env unset (silent — class inactive)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --repo exampleorg/team --title t --body \"ref testowner/scratchpad#209\""}}'

# --- coverage for branches flagged by review: --repo= form, multi-owner env,
# --- heredoc-resolved body, scp-style TLD-less personal origin ---
run_case "gh create --repo=org target (=-joined form) + owner ref (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --repo=exampleorg/team --title t --body \"ref testowner/scratchpad#209\""}}' \
  "testowner"

run_case "multi-owner env, second owner ref (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --repo exampleorg/team --title t --body \"ref testowner/scratchpad#3\""}}' \
  "alice,testowner"

run_case "heredoc-var body with owner ref (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"BODY=$(cat <<EOF\nsee testowner/scratchpad#5\nEOF\n)\ngh issue create --repo exampleorg/team --title t --body \"$BODY\""}}' \
  "testowner"

INTERNAL_PERSONAL_REPO=$(mktemp -d)
git -C "$INTERNAL_PERSONAL_REPO" init -q
git -C "$INTERNAL_PERSONAL_REPO" remote add origin git@internalhost:testowner/scratchpad.git
run_case "Write owner-ref into TLD-less scp-origin personal repo (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$INTERNAL_PERSONAL_REPO/notes.md\",\"content\":\"see testowner/scratchpad#209\"}}" \
  "testowner"
rm -rf "$INTERNAL_PERSONAL_REPO"

# --- strict mode applies to the owner class too ---
run_case "strict + Write owner-ref into team repo (block)" \
  "block" "strict" \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$TEAM_REPO/wiki/s.md\",\"content\":\"testowner/scratchpad#1\"}}" \
  "testowner"

rm -rf "$TEAM_REPO" "$PERSONAL_REPO"

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
