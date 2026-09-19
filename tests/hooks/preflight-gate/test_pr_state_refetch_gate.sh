#!/usr/bin/env bash
# test_pr_state_refetch_gate.sh — coverage for
# hooks/preflight-gate/pr-state-refetch-gate/impl.py
#
# Synthesizes Claude Code PreToolUse(AskUserQuestion) payloads and asserts:
#   block → exit 2 + stderr non-empty
#   pass  → exit 0 (stderr optionally matched against a required pattern)
#
# Real `gh` calls are short-circuited via a per-case fake-bin dir prepended to
# PATH. The fake `gh` shim logs every invocation to $PRSRG_CALL_LOG so tests
# that assert "zero-cost pass-through" (no signal detected → no gh subprocess)
# can verify the hook never shelled out at all, not just that it exited 0.
#
# Usage: bash tests/hooks/preflight-gate/test_pr_state_refetch_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/pr-state-refetch-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

# build_payload <questions-json>
# questions-json is a JSON array literal for tool_input.questions.
build_payload() {
  python3 -c '
import json, sys
questions = json.loads(sys.argv[1])
print(json.dumps({
    "tool_name": "AskUserQuestion",
    "tool_input": {"questions": questions},
    "cwd": "/tmp",
}))
' "$1"
}

# ---------------------------------------------------------------------------
# Fake-bin helpers
# ---------------------------------------------------------------------------

# make_fake_gh <mode> [map-content]
#   mode:
#     map      — responds per PR number using "map-content" lines "N STATE"
#                (e.g. "714 MERGED"); unmapped numbers -> gh error exit 1
#                Optional extra columns: "<num> <state> <mergeStateStatus>
#                <mergeable> <isDraft>", defaulting to CLEAN / MERGEABLE /
#                false — the ask-ready values (issue #1436).
#     error    — every call exits 1 (auth-style failure)
#     badjson  — every call exits 0 but prints unparseable output
#     absent   — no gh binary at all (dir has no gh file)
# Every generated gh shim (except absent) appends its argv to $PRSRG_CALL_LOG.
make_fake_gh() {
  local mode="$1" map_content="${2:-}"
  local d
  d=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
  case "$mode" in
    absent)
      ;;
    error)
      cat >"$d/gh" <<'EOF'
#!/usr/bin/env bash
echo "$@" >> "$PRSRG_CALL_LOG"
echo "gh: authentication required" >&2
exit 1
EOF
      ;;
    badjson)
      cat >"$d/gh" <<'EOF'
#!/usr/bin/env bash
echo "$@" >> "$PRSRG_CALL_LOG"
echo "not json at all <<<"
exit 0
EOF
      ;;
    map)
      printf '%s\n' "$map_content" > "$d/.map"
      # Fully single-quoted (literal) heredocs — no shell-side interpolation
      # at generation time, so no nested-quoting escaping is needed. The
      # inner python3 does the number->state lookup at runtime, reading the
      # map path from $PRSRG_MAP_FILE (set by run_case, defaults to
      # "<fake-bin>/.map" alongside this shim).
      cat >"$d/gh" <<'EOF'
#!/usr/bin/env bash
echo "$@" >> "$PRSRG_CALL_LOG"
map_file="${PRSRG_MAP_FILE:-$(dirname "$0")/.map}"
python3 - "$map_file" "$@" <<'PY'
import json
import sys

# gh returns only the fields `--json` names, so this shim must too. Emitting
# all four regardless is what let the hook's query narrow without a single
# case failing — the shim answered for a query nobody had asked.
map_file, argv = sys.argv[1], sys.argv[2:]
num = None
requested: list[str] = []
i = 0
while i < len(argv):
    tok = argv[i]
    if tok == "--json" and i + 1 < len(argv):
        requested = [f for f in argv[i + 1].split(",") if f]
        i += 2
        continue
    if num is None and tok not in ("pr", "view") and not tok.startswith("-"):
        num = tok
    i += 1

state = None
fields = {}
with open(map_file) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        n = parts[0]
        if n == num:
            # `<num> <state> [mergeStateStatus] [mergeable] [isDraft]`.
            # The three optional columns default to the ask-ready values
            # (issue #1436) so a case that only cares about `state` keeps
            # meaning what it meant before the allowlist verdict landed.
            state = parts[1]
            fields = {
                "state": state,
                "mergeStateStatus": parts[2] if len(parts) > 2 else "CLEAN",
                "mergeable": parts[3] if len(parts) > 3 else "MERGEABLE",
                "isDraft": (parts[4].lower() == "true") if len(parts) > 4 else False,
            }
            # `absent` drops the key entirely — the only way to reproduce a
            # response that answers the query without answering this field.
            if len(parts) > 4 and parts[4].lower() == "absent":
                del fields["isDraft"]
            break
if state is None:
    sys.stderr.write("gh: no pull requests found\n")
    sys.exit(1)
if requested:
    fields = {k: v for k, v in fields.items() if k in requested}
print(json.dumps(fields))
PY
EOF
      ;;
  esac
  [ -f "$d/gh" ] && chmod +x "$d/gh"
  echo "$d"
}

# run_case <name> <expected: block|pass> <questions-json> <gh-mode> <gh-map> \
#          <strict: 0|1> [need_grep] [expect_no_gh_call: 0|1] [not_grep] \
#          [call_grep: pattern the gh argv must match]
run_case() {
  local name="$1" expected="$2" questions_json="$3" gh_mode="$4" gh_map="$5" \
        strict="$6" need_grep="${7:-}" expect_no_call="${8:-0}" not_grep="${9:-}" \
        call_grep="${10:-}"

  local payload fake_bin call_log err_file rc
  payload=$(build_payload "$questions_json")
  fake_bin=$(make_fake_gh "$gh_mode" "$gh_map")
  call_log=$(mktemp)
  err_file=$(mktemp)

  local strict_env=""
  [ "$strict" = "1" ] && strict_env="1"

  echo "$payload" | env PATH="$fake_bin:$PATH" PRSRG_CALL_LOG="$call_log" \
    PRAXIS_PR_STATE_REFETCH_STRICT="$strict_env" \
    python3 "$HOOK" >/dev/null 2>"$err_file"
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"
  local call_content
  call_content=$(cat "$call_log"); rm -f "$call_log"
  rm -rf "$fake_bin"

  local ok=1
  if [ "$expected" = "block" ]; then
    { [ "$rc" -eq 2 ] && [ -n "$err_content" ]; } || ok=0
  else
    [ "$rc" -eq 0 ] || ok=0
  fi
  if [ "$ok" -eq 1 ] && [ -n "$need_grep" ]; then
    printf '%s' "$err_content" | grep -Eq "$need_grep" || ok=0
  fi
  if [ "$ok" -eq 1 ] && [ -n "$not_grep" ]; then
    printf '%s' "$err_content" | grep -Eq "$not_grep" && ok=0
  fi
  if [ "$ok" -eq 1 ] && [ "$expect_no_call" = "1" ]; then
    [ -z "$call_content" ] || ok=0
  fi
  # The argv the hook actually sent. Every other assertion here reads the
  # shim's answer, which the shim decides — only this one can see the query.
  if [ "$ok" -eq 1 ] && [ -n "$call_grep" ]; then
    printf '%s' "$call_content" | grep -Eq -- "$call_grep" || ok=0
  fi

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; ((PASS++))
  else
    echo "FAIL [$expected→rc=$rc] $name"
    printf '  stderr: %s\n' "$err_content" | head -5
    printf '  gh calls: %s\n' "$call_content" | head -5
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Co-occurrence signal — true positives
# ---------------------------------------------------------------------------

run_case "EN merge keyword + number, same option label" pass \
  '[{"question":"","options":[{"label":"Merge PR #714","description":""}]}]' \
  map "714 MERGED" 0 'PR #714.*MERGED'

run_case "KO 머지 keyword + number, same option label" pass \
  '[{"question":"","options":[{"label":"PR #714 머지","description":""}]}]' \
  map "714 MERGED" 0 'PR #714.*MERGED'

run_case "number in question, verb in option label" pass \
  '[{"question":"PR #714에 대해 어떻게 할까요?","options":[{"label":"Merge","description":""},{"label":"Hold","description":""}]}]' \
  map "714 MERGED" 0 'PR #714.*MERGED'

run_case "squash keyword + number" pass \
  '[{"question":"","options":[{"label":"Squash PR #200","description":""}]}]' \
  map "200 CLOSED" 0 'PR #200.*CLOSED'

run_case "number in header only, verb in option label" pass \
  '[{"header":"PR #714","question":"어떻게 할까요?","options":[{"label":"Merge","description":""},{"label":"Hold","description":""}]}]' \
  map "714 MERGED" 0 'PR #714.*MERGED'

# ---------------------------------------------------------------------------
# False-positive boundary — no signal, zero gh subprocess cost
# ---------------------------------------------------------------------------

run_case "number without merge keyword" pass \
  '[{"question":"","options":[{"label":"PR #714 리뷰를 진행할까요?","description":""}]}]' \
  map "714 MERGED" 0 '' 1

run_case "merge keyword without number" pass \
  '[{"question":"","options":[{"label":"머지 충돌을 해결해볼까요?","description":""}]}]' \
  map "714 MERGED" 0 '' 1

run_case "cross-question non-pairing" pass \
  '[{"question":"#100 이슈 상태","options":[{"label":"Status","description":""}]},{"question":"","options":[{"label":"Please merge this","description":""}]}]' \
  map "100 MERGED" 0 '' 1

# ---------------------------------------------------------------------------
# Live re-fetch outcomes
# ---------------------------------------------------------------------------

run_case "live state OPEN -> silent pass" pass \
  '[{"question":"","options":[{"label":"Merge PR #714","description":""}]}]' \
  map "714 OPEN" 0

run_case "live state MERGED -> advisory" pass \
  '[{"question":"","options":[{"label":"Merge PR #714","description":""}]}]' \
  map "714 MERGED" 0 'stale|MERGED'

run_case "live state CLOSED -> advisory" pass \
  '[{"question":"","options":[{"label":"Merge PR #714","description":""}]}]' \
  map "714 CLOSED" 0 'stale|CLOSED'

run_case "multiple candidates, mixed states" pass \
  '[{"question":"","options":[{"label":"Merge PR #100","description":""},{"label":"Merge PR #200","description":""}]}]' \
  map $'100 OPEN\n200 MERGED' 0 'PR #200.*MERGED' 0 'PR #100'

# ---------------------------------------------------------------------------
# Strict mode
# ---------------------------------------------------------------------------

run_case "strict mode blocks on MERGED" block \
  '[{"question":"","options":[{"label":"Merge PR #714","description":""}]}]' \
  map "714 MERGED" 1 'PR #714.*MERGED'

# ---------------------------------------------------------------------------
# Ask-readiness allowlist (issue #1436)
#
# `praxis:merge-briefing` Step 1 allows a merge ask only when
# mergeable=MERGEABLE and mergeStateStatus is CLEAN or HAS_HOOKS. Before this,
# the gate answered only MERGED/CLOSED, so every other not-ready state reached
# the user as a question they had to correct.
# ---------------------------------------------------------------------------

Q_MERGE='[{"question":"merge PR #714?","options":[{"label":"yes","description":"go"}]}]'

run_case "OPEN + CLEAN + MERGEABLE is ask-ready (silent)" pass \
  "$Q_MERGE" map "714 OPEN CLEAN MERGEABLE false" 0 '' 0 '.'

run_case "OPEN + HAS_HOOKS is ask-ready (silent)" pass \
  "$Q_MERGE" map "714 OPEN HAS_HOOKS MERGEABLE false" 0 '' 0 '.'

run_case "UNSTABLE (non-passing checks) advises" pass \
  "$Q_MERGE" map "714 OPEN UNSTABLE MERGEABLE false" 0 'PR #714.*UNSTABLE'

run_case "BLOCKED advises" pass \
  "$Q_MERGE" map "714 OPEN BLOCKED MERGEABLE false" 0 'PR #714.*BLOCKED'

run_case "BEHIND advises" pass \
  "$Q_MERGE" map "714 OPEN BEHIND MERGEABLE false" 0 'PR #714.*BEHIND'

run_case "DIRTY advises" pass \
  "$Q_MERGE" map "714 OPEN DIRTY MERGEABLE false" 0 'PR #714.*DIRTY'

run_case "CONFLICTING mergeable advises even on a CLEAN merge state" pass \
  "$Q_MERGE" map "714 OPEN CLEAN CONFLICTING false" 0 'PR #714.*CONFLICTING'

# Draft is not a value of mergeStateStatus, so a draft PR reports CLEAN and
# would otherwise pass every other check.
run_case "draft + CLEAN advises" pass \
  "$Q_MERGE" map "714 OPEN CLEAN MERGEABLE true" 0 'PR #714.*draft'

run_case "strict mode blocks on UNSTABLE" block \
  "$Q_MERGE" map "714 OPEN UNSTABLE MERGEABLE false" 1 'PR #714.*UNSTABLE'

run_case "strict mode blocks on draft" block \
  "$Q_MERGE" map "714 OPEN CLEAN MERGEABLE true" 1 'PR #714.*draft'

# UNKNOWN is GitHub still computing the merge state — not `CLEAN`, so not
# silent; not a defect either, so it never blocks, strict mode included.
run_case "UNKNOWN advises" pass \
  "$Q_MERGE" map "714 OPEN UNKNOWN MERGEABLE false" 0 'PR #714.*re-poll'

run_case "UNKNOWN does not block under strict mode" pass \
  "$Q_MERGE" map "714 OPEN UNKNOWN MERGEABLE false" 1 'advisory only'

# An `isDraft` the response never carried is not a "not a draft" answer. Every
# other unanswered field above routes to a soft reason; this one resolved
# toward the ask.
run_case "absent isDraft advises rather than passing silently" pass \
  "$Q_MERGE" map "714 OPEN CLEAN MERGEABLE absent" 0 'draft status unknown'

# Only this case can see the query itself; every other assertion reads the
# shim's answer, and the shim used to answer for a query nobody had asked.
run_case "the live query names every field the verdict reads" pass \
  "$Q_MERGE" map "714 OPEN CLEAN MERGEABLE false" 0 '' 0 '' \
  '--json state,mergeStateStatus,mergeable,isDraft'


# ---------------------------------------------------------------------------
# gh infrastructure failures — fail-open
# ---------------------------------------------------------------------------

run_case "gh binary missing -> fail-open" pass \
  '[{"question":"","options":[{"label":"Merge PR #714","description":""}]}]' \
  absent "" 0

run_case "gh non-zero exit -> fail-open" pass \
  '[{"question":"","options":[{"label":"Merge PR #714","description":""}]}]' \
  error "" 0

run_case "gh unparseable JSON -> fail-open" pass \
  '[{"question":"","options":[{"label":"Merge PR #714","description":""}]}]' \
  badjson "" 0

# ---------------------------------------------------------------------------
# Passthroughs
# ---------------------------------------------------------------------------

run_non_ask_case() {
  local payload err_file rc call_log fake_bin
  payload='{"tool_name":"Bash","tool_input":{"command":"gh pr merge 714"}}'
  fake_bin=$(make_fake_gh "map" "714 MERGED")
  call_log=$(mktemp)
  err_file=$(mktemp)
  echo "$payload" | env PATH="$fake_bin:$PATH" PRSRG_CALL_LOG="$call_log" \
    python3 "$HOOK" >/dev/null 2>"$err_file"
  rc=$?
  local err_content call_content
  err_content=$(cat "$err_file"); rm -f "$err_file"
  call_content=$(cat "$call_log"); rm -f "$call_log"
  rm -rf "$fake_bin"

  if [ "$rc" -eq 0 ] && [ -z "$err_content" ] && [ -z "$call_content" ]; then
    echo "PASS [pass] non-AskUserQuestion tool passthrough"; ((PASS++))
  else
    echo "FAIL [pass→rc=$rc] non-AskUserQuestion tool passthrough"
    ((FAIL++)); FAILED_NAMES+=("non-AskUserQuestion tool passthrough")
  fi
}
run_non_ask_case

run_malformed_case() {
  local err_file rc
  err_file=$(mktemp)
  echo 'not valid json {' | python3 "$HOOK" >/dev/null 2>"$err_file"
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"
  if [ "$rc" -eq 0 ] && [ -z "$err_content" ]; then
    echo "PASS [pass] malformed payload fail-open"; ((PASS++))
  else
    echo "FAIL [pass→rc=$rc] malformed payload fail-open"
    ((FAIL++)); FAILED_NAMES+=("malformed payload fail-open")
  fi
}
run_malformed_case

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "== $PASS passed, $FAIL failed =="
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
