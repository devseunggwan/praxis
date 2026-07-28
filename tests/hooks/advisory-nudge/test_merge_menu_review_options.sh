#!/usr/bin/env bash
# test_merge_menu_review_options.sh — coverage for
# hooks/advisory-nudge/merge-menu-review-options-advisory/impl.py
#
# Synthesizes Claude Code PreToolUse(AskUserQuestion) payloads and asserts:
#   advisory → exit 0 + stderr non-empty  (default mode)
#   block    → exit 2 + stderr non-empty  (PRAXIS_MERGE_MENU_REVIEW_STRICT=1)
#   pass     → exit 0 + stderr empty
#
# The hook inspects only options[].label — no transcript is needed.
#
# Usage: bash tests/hooks/advisory-nudge/test_merge_menu_review_options.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/merge-menu-review-options-advisory/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# Build a JSON payload for an AskUserQuestion tool call from an options array.
# $1 = options JSON array (e.g., '["squash 머지", "대기"]')
build_payload() {
  local options_json="$1"
  python3 - "$options_json" <<'PY'
import json, sys
options = json.loads(sys.argv[1])
payload = {
    "session_id": "test-session",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {
                "question": "Merge?",
                "header": "Merge",
                "multiSelect": False,
                "options": [{"label": opt, "description": "test desc"} for opt in options],
            }
        ]
    },
    "cwd": "/tmp",
}
print(json.dumps(payload))
PY
}

# run_case name expected mode payload
#   expected: advisory | block | pass
#   mode:     default            → no STRICT env set
#             strict             → PRAXIS_MERGE_MENU_REVIEW_STRICT=1
#             strictval=<value>  → PRAXIS_MERGE_MENU_REVIEW_STRICT=<value>
run_case() {
  local name="$1" expected="$2" mode="$3" payload="$4"
  local err_file rc

  err_file=$(mktemp)
  case "$mode" in
    strict)
      echo "$payload" | PRAXIS_MERGE_MENU_REVIEW_STRICT=1 "$HOOK" >/dev/null 2>"$err_file"
      ;;
    strictval=*)
      echo "$payload" | PRAXIS_MERGE_MENU_REVIEW_STRICT="${mode#strictval=}" "$HOOK" >/dev/null 2>"$err_file"
      ;;
    *)
      echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
      ;;
  esac
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  case "$expected" in
    advisory) [ "$rc" -eq 0 ] && [ -n "$err_content" ] || ok=0 ;;
    block)    [ "$rc" -eq 2 ] && [ -n "$err_content" ] || ok=0 ;;
    pass)     [ "$rc" -eq 0 ] && [ -z "$err_content" ] || ok=0 ;;
    *) echo "INTERNAL: unknown expected '$expected'" >&2; ok=0 ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; PASS=$((PASS+1))
  else
    echo "FAIL [$expected→rc=$rc,stderr=$([ -n "$err_content" ] && echo non-empty || echo empty)] $name"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# (a) ADVISORY — merge-decision menu, no review/debate option
# ---------------------------------------------------------------------------
run_case "KO merge token, no review"        advisory default "$(build_payload '["squash 머지 + 정리", "대기"]')"
run_case "EN merge token, no review"        advisory default "$(build_payload '["squash merge + cleanup", "hold"]')"
run_case "EN squash token, no review"       advisory default "$(build_payload '["squash and merge", "wait"]')"
run_case "mixed-script squash 머지"          advisory default "$(build_payload '["squash 머지", "대기"]')"
run_case "uppercase EN merge token"          advisory default "$(build_payload '["MERGE NOW", "HOLD"]')"
run_case "TitleCase Squash Merge"            advisory default "$(build_payload '["Squash Merge", "Hold"]')"

# ---------------------------------------------------------------------------
# (b) PASS — review/debate option already present
# ---------------------------------------------------------------------------
run_case "review present: codex review (KO)"   pass default "$(build_payload '["squash 머지", "codex review 재실행", "대기"]')"
run_case "review present: code-reviewer (EN)"  pass default "$(build_payload '["merge", "re-run code-reviewer", "hold"]')"
run_case "review present: critic"              pass default "$(build_payload '["머지", "critic 토론", "대기"]')"
run_case "review present: 리뷰 (KO)"            pass default "$(build_payload '["머지", "리뷰 한번 더", "대기"]')"
run_case "review present: 검토 (KO)"            pass default "$(build_payload '["merge", "추가 검토", "hold"]')"

# ---------------------------------------------------------------------------
# (c) PASS — no merge-decision option (not a merge gate)
# ---------------------------------------------------------------------------
run_case "no merge token"                  pass default "$(build_payload '["Plan A", "Plan B"]')"
run_case "false-positive: merged (not merge)" pass default "$(build_payload '["view merged PRs", "cancel"]')"
run_case "false-positive: merger"          pass default "$(build_payload '["contact the merger", "skip"]')"
run_case "empty options"                   pass default "$(build_payload '[]')"

# ---------------------------------------------------------------------------
# (d) BLOCK — strict mode, merge menu without review option
# ---------------------------------------------------------------------------
run_case "strict: KO merge, no review"     block strict "$(build_payload '["squash 머지", "대기"]')"
run_case "strict: EN merge, no review"     block strict "$(build_payload '["merge now", "hold"]')"
run_case "strict: review present → pass"   pass  strict "$(build_payload '["머지", "codex 재실행", "대기"]')"

# strict-env value contract: ONLY `=1` (after strip) activates strict. Any
# disable-intent or non-1 value falls back to advisory (documented `=1` contract).
run_case "strict=1 with whitespace → block"  block "strictval= 1 " "$(build_payload '["squash 머지", "대기"]')"
run_case "strict=no → advisory (not strict)"  advisory "strictval=no" "$(build_payload '["squash 머지", "대기"]')"
run_case "strict=0 → advisory"                advisory "strictval=0" "$(build_payload '["squash 머지", "대기"]')"
run_case "strict=false → advisory"            advisory "strictval=false" "$(build_payload '["squash 머지", "대기"]')"
run_case "strict=true → advisory (only 1)"    advisory "strictval=true" "$(build_payload '["squash 머지", "대기"]')"

# ---------------------------------------------------------------------------
# (e) PASS — non-AskUserQuestion tool / malformed payload (fail-open)
# ---------------------------------------------------------------------------
run_case "non-AskUserQuestion tool"        pass default '{"tool_name":"Bash","tool_input":{"command":"git merge"}}'
run_case "malformed JSON payload"          pass default 'not-json-at-all'
run_case "tool_input not a dict"           pass default '{"tool_name":"AskUserQuestion","tool_input":"oops"}'
run_case "questions missing"               pass default '{"tool_name":"AskUserQuestion","tool_input":{}}'

# ---------------------------------------------------------------------------
# (f) multi-question payload — merge token in 2nd question, no review
# ---------------------------------------------------------------------------
MULTIQ='{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"q1","header":"a","options":[{"label":"Plan A","description":"d"}]},{"question":"q2","header":"b","options":[{"label":"squash 머지","description":"d"},{"label":"대기","description":"d"}]}]}}'
run_case "multi-question, merge in q2, no review" advisory default "$MULTIQ"

# ---------------------------------------------------------------------------
# (g) documented-guarantee pins (spec "Known limitations")
# ---------------------------------------------------------------------------
# label-only detection: review intent in the *description* is NOT counted →
# the menu still nudges (review option must live in the label).
DESC_ONLY_REVIEW='{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"q","header":"h","options":[{"label":"squash 머지","description":"or run a codex review first"},{"label":"대기","description":"d"}]}]}}'
run_case "review only in description → still nudges" advisory default "$DESC_ONLY_REVIEW"

# Review detection runs only on NON-merge labels (a review lever is a distinct
# option, not the merge action). So a merge label that happens to contain a
# review substring does NOT self-suppress:
#  - "merge after preview" → merge label, 'preview' no longer suppresses → nudge
run_case "merge label w/ review substring → still nudges" advisory default "$(build_payload '["merge after preview", "hold"]')"
#  - "merge security fix" / "merge designer changes" → merge labels describing
#    the change area must NOT suppress (codex #562 P2: else the nudge dies on
#    exactly the high-stakes merges these labels describe)
run_case "merge label 'security' substring → still nudges" advisory default "$(build_payload '["merge security fix", "대기"]')"
run_case "merge label 'designer' substring → still nudges" advisory default "$(build_payload '["merge designer changes", "hold"]')"

# L2 self-consistency: designer / security as a DISTINCT (non-merge) option label
# correctly suppresses the nudge:
run_case "designer option suppresses"      pass default "$(build_payload '["squash 머지", "designer 실행", "대기"]')"
run_case "security audit option suppresses" pass default "$(build_payload '["squash 머지", "security audit", "hold"]')"

# KO inflection exclusions: 머지된 (already-merged) and 머지하지 (do-NOT-merge)
# are NOT merge-decision gates — negative lookahead excludes them.
run_case "KO 머지된 (already-merged) → no trigger"   pass default "$(build_payload '["머지된 PR 목록 보기", "취소"]')"
run_case "KO 머지하지 (explicit no-merge) → no trigger" pass default "$(build_payload '["머지하지 말고 대기", "확인"]')"
# real KO gates still trigger after the lookahead is added
run_case "KO 머지하기 still triggers (하기≠하지)"      advisory default "$(build_payload '["머지하기", "대기"]')"
run_case "KO 머지할까요 still triggers"               advisory default "$(build_payload '["지금 머지할까요?", "대기"]')"

# ---------------------------------------------------------------------------
# (h) L2 — context-aware reviewer routing (#562)
# ---------------------------------------------------------------------------
# These cases exercise the real git-diff path: a temp repo is created with
# specific changed files on a feature branch vs a `main` base, and the hook is
# run with cwd=that repo. The advisory text should lead with the reviewer family
# routed from the changed paths. (Inner git commands run inside this script as
# subprocesses — the praxis PreToolUse hooks only intercept the agent's
# top-level Bash calls, not nested subprocess git, so non-conventional branch /
# commit names here are fine.)

# setup_git_repo <file>...  → echoes a temp repo path with the files committed
# on a feature branch over a `main` base. git identity is injected inline
# (no global config dependency); no 2>/dev/null on setup (surface real errors).
setup_git_repo() {
  local tmp; tmp=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
  git -C "$tmp" init -q -b main
  git -C "$tmp" -c user.name=t -c user.email=t@t.io commit -q --allow-empty -m "chore: base"
  git -C "$tmp" checkout -q -b feature
  local f
  for f in "$@"; do
    mkdir -p "$tmp/$(dirname "$f")"
    printf 'x\n' > "$tmp/$f"
  done
  git -C "$tmp" add -A
  git -C "$tmp" -c user.name=t -c user.email=t@t.io commit -q -m "feat: changes"
  printf '%s' "$tmp"
}

# payload_with_cwd <cwd> → a merge-decision menu (머지/대기, no review) at <cwd>
payload_with_cwd() {
  python3 - "$1" <<'PY'
import json, sys
print(json.dumps({
    "tool_name": "AskUserQuestion",
    "cwd": sys.argv[1],
    "tool_input": {"questions": [{"options": [
        {"label": "squash 머지", "description": "x"},
        {"label": "대기", "description": "x"},
    ]}]},
}))
PY
}

# route_present name expect_substr <file>...  → stderr must contain expect_substr
route_present() {
  local name="$1" expect="$2"; shift 2
  local repo; repo=$(setup_git_repo "$@")
  local err; err=$(payload_with_cwd "$repo" | "$HOOK" 2>&1 >/dev/null)
  rm -rf "$repo"
  if printf '%s' "$err" | grep -qF "$expect"; then
    echo "PASS [route] $name → $expect"; PASS=$((PASS+1))
  else
    echo "FAIL [route] $name (expected '$expect' in stderr)"; FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# route_absent name <file>...  → routed "touches" line must be ABSENT (static
# fallback), generic codex-review-wrap lever still present.
route_absent() {
  local name="$1"; shift
  local repo; repo=$(setup_git_repo "$@")
  local err; err=$(payload_with_cwd "$repo" | "$HOOK" 2>&1 >/dev/null)
  rm -rf "$repo"
  if printf '%s' "$err" | grep -q 'touches'; then
    echo "FAIL [route] $name (unexpected routed line)"; FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  elif printf '%s' "$err" | grep -q 'codex-review-wrap'; then
    echo "PASS [route] $name → static fallback"; PASS=$((PASS+1))
  else
    echo "FAIL [route] $name (no generic lever)"; FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

route_present "security family (auth path)"   "보안 리뷰"       "src/auth/login.py"
route_present "security family (.pem)"         "보안 리뷰"       "certs/server.pem"
route_present "data family (.sql)"             "데이터/SQL 리뷰" "migrations/001_add.sql"
route_present "design family (.prisma)"        "설계 리뷰"        "db/schema.prisma"
route_present "ux family (.tsx)"               "디자인/UX 리뷰"   "web/components/Button.tsx"
# priority: a file matching both security(auth) and data(.sql) → security wins
route_present "priority security > data"       "보안 리뷰"       "migrations/add_auth_token.sql"
# priority: design(.prisma) + ux(.tsx) across two files → design (rank 3) > ux (rank 4)
route_present "priority design > ux"           "설계 리뷰"        "db/schema.prisma" "web/page.tsx"
# no family match → static fallback, no routed line
route_absent  "no family match (docs only)"   "README.md" "docs/guide.md"
# data-signal tightness (code-review #562): these formerly mis-routed to data
route_present "FE template .tsx → ux not data" "디자인/UX 리뷰"  "web/templates/Card.tsx"
route_absent  "loader.py is not data"          "src/loader.py"
route_absent  "getlist.py is not data (etl)"   "src/getlist.py"

# multi-base: branch forks from `dev` (which carries an auth file) while `main`
# also exists. Nearest-fork-point resolution must pick `dev`, so the diff is the
# branch's OWN change (a .tsx → ux) — NOT dev's auth file (would mis-route to
# security with naive first-resolvable base selection).
MB_REPO=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
git -C "$MB_REPO" init -q -b main
git -C "$MB_REPO" -c user.name=t -c user.email=t@t.io commit -q --allow-empty -m "chore: base"
git -C "$MB_REPO" checkout -q -b dev
mkdir -p "$MB_REPO/src/auth"; printf 'x\n' > "$MB_REPO/src/auth/login.py"
git -C "$MB_REPO" add -A && git -C "$MB_REPO" -c user.name=t -c user.email=t@t.io commit -q -m "feat: auth on dev"
git -C "$MB_REPO" checkout -q -b issue-1-feat-ui
mkdir -p "$MB_REPO/web"; printf 'x\n' > "$MB_REPO/web/Button.tsx"
git -C "$MB_REPO" add -A && git -C "$MB_REPO" -c user.name=t -c user.email=t@t.io commit -q -m "feat: ui"
MB_ERR=$(payload_with_cwd "$MB_REPO" | "$HOOK" 2>&1 >/dev/null)
rm -rf "$MB_REPO"
if printf '%s' "$MB_ERR" | grep -qF "디자인/UX 리뷰"; then
  echo "PASS [route] multi-base picks dev fork-point (ux not security)"; PASS=$((PASS+1))
elif printf '%s' "$MB_ERR" | grep -qF "보안 리뷰"; then
  echo "FAIL [route] multi-base mis-routed to security (base not nearest)"; FAIL=$((FAIL+1)); FAILED_NAMES+=("multi-base")
else
  echo "FAIL [route] multi-base unexpected output"; FAIL=$((FAIL+1)); FAILED_NAMES+=("multi-base")
fi

# fail-open: cwd is a non-git directory → static fallback (no routed line)
NONGIT_TMP=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
NONGIT_ERR=$(payload_with_cwd "$NONGIT_TMP" | "$HOOK" 2>&1 >/dev/null)
rm -rf "$NONGIT_TMP"
if printf '%s' "$NONGIT_ERR" | grep -q 'touches'; then
  echo "FAIL [route] non-git cwd (unexpected routed line)"; FAIL=$((FAIL+1)); FAILED_NAMES+=("non-git cwd")
elif printf '%s' "$NONGIT_ERR" | grep -q 'codex-review-wrap'; then
  echo "PASS [route] non-git cwd → static fallback"; PASS=$((PASS+1))
else
  echo "FAIL [route] non-git cwd (no generic lever)"; FAIL=$((FAIL+1)); FAILED_NAMES+=("non-git cwd")
fi

# strict mode + routing: routed reviewer leads the BLOCK message (exit 2)
STRICT_REPO=$(setup_git_repo "migrations/001.sql")
STRICT_ERR=$(payload_with_cwd "$STRICT_REPO" | PRAXIS_MERGE_MENU_REVIEW_STRICT=1 "$HOOK" 2>&1 >/dev/null)
STRICT_RC=$?
rm -rf "$STRICT_REPO"
if [ "$STRICT_RC" -eq 2 ] && printf '%s' "$STRICT_ERR" | grep -qF "데이터/SQL 리뷰"; then
  echo "PASS [route] strict block carries routed reviewer"; PASS=$((PASS+1))
else
  echo "FAIL [route] strict block (rc=$STRICT_RC, routed reviewer missing)"; FAIL=$((FAIL+1)); FAILED_NAMES+=("strict routed")
fi

# ---------------------------------------------------------------------------
echo "=== summary ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf 'FAILED: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
