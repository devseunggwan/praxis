#!/bin/bash
# tests/test_cross_boundary_preflight.sh
#
# Coverage for hooks/preflight-gate/cross-boundary-preflight/impl.py
#
# Three outcomes:
#   ask   — stdout contains permissionDecision "ask", exit 0
#   block — exit 2, stderr non-empty
#   pass  — exit 0, stdout empty, stderr empty
#
# Usage: bash tests/test_cross_boundary_preflight.sh
# Exit:  0 = all pass, 1 = at least one failure

set +e

# `GH_REPO` now participates in target resolution (issue #1149), so an
# exported value in the invoking shell would silently redirect every
# repo-less case away from its fixture. Unset it once, up front; the cases
# that need it set it per-invocation.
unset GH_REPO

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/preflight-gate/cross-boundary-preflight/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Checkout fixtures (issue #1148)
#
# The repo-less arm resolves its target from `git remote get-url origin` in
# the payload's cwd, so every case needs a deterministic checkout — otherwise
# the verdict would depend on where the suite happens to be invoked from.
# Three fixtures cover the whole resolution space:
#   FIX_REPO     — checkout with an `origin` on github.com  → resolvable
#   FIX_NOORIGIN — checkout with no remotes                 → unresolvable
#   FIX_PLAIN    — not a checkout at all                    → unresolvable
# ---------------------------------------------------------------------------
FIXTURE_ROOT=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$FIXTURE_ROOT"' EXIT
FIX_REPO="$FIXTURE_ROOT/resolvable"
FIX_NOORIGIN="$FIXTURE_ROOT/no-origin"
FIX_PLAIN="$FIXTURE_ROOT/not-a-repo"
mkdir -p "$FIX_REPO" "$FIX_NOORIGIN" "$FIX_PLAIN"
( cd "$FIX_REPO" && git init -q && git remote add origin git@github.com:devseunggwan/praxis.git ) >/dev/null 2>&1
( cd "$FIX_NOORIGIN" && git init -q ) >/dev/null 2>&1
# A non-GitHub host whose PATH contains "github". The marker must be matched
# in the host, not anywhere in the URL, or this resolves to `tools/repo` and
# the checklist names a repo the write never touches.
FIX_GITLAB="$FIXTURE_ROOT/gitlab-github-path"
mkdir -p "$FIX_GITLAB"
( cd "$FIX_GITLAB" && git init -q && git remote add origin https://gitlab.com/github/tools/repo.git ) >/dev/null 2>&1

# Checkouts whose EFFECTIVE gh target is not `origin` (issue #1149).
# `gh` resolves a repo-less write via GH_REPO, then `remote.<n>.gh-resolved`
# (what `gh repo set-default` writes), then the first remote in its own
# preference order — upstream, github, origin. Every one of these fixtures
# has a perfectly good `origin`, and naming it would be the bug.
FIX_SETDEFAULT="$FIXTURE_ROOT/set-default"        # gh-resolved → another repo
FIX_SETDEFAULT_BASE="$FIXTURE_ROOT/set-default-base"  # gh-resolved = base
FIX_SETDEFAULT_BAD="$FIXTURE_ROOT/set-default-bad"    # gh-resolved unparseable
FIX_FORK="$FIXTURE_ROOT/fork-with-upstream"       # upstream outranks origin
mkdir -p "$FIX_SETDEFAULT" "$FIX_SETDEFAULT_BASE" "$FIX_SETDEFAULT_BAD" "$FIX_FORK"
( cd "$FIX_SETDEFAULT" && git init -q \
    && git remote add origin git@github.com:devseunggwan/praxis.git \
    && git config --add remote.origin.gh-resolved redirected/target ) >/dev/null 2>&1
# `base` means "this remote's own repo", and it must also beat the upstream
# preference below it — otherwise honouring the key would be cosmetic.
( cd "$FIX_SETDEFAULT_BASE" && git init -q \
    && git remote add origin git@github.com:devseunggwan/praxis.git \
    && git remote add upstream https://github.com/upstreamowner/praxis.git \
    && git config --add remote.origin.gh-resolved base ) >/dev/null 2>&1
( cd "$FIX_SETDEFAULT_BAD" && git init -q \
    && git remote add origin git@github.com:devseunggwan/praxis.git \
    && git config --add remote.origin.gh-resolved "not a repo name" ) >/dev/null 2>&1
( cd "$FIX_FORK" && git init -q \
    && git remote add origin git@github.com:devseunggwan/praxis.git \
    && git remote add upstream https://github.com/upstreamowner/praxis.git ) >/dev/null 2>&1

# `git` walks parent directories looking for a checkout, so a TMPDIR that
# happens to sit inside one would make the not-a-repo fixture resolve an
# unrelated `origin` — the fail-open case would then pass for the wrong
# reason, or fail for a reason that has nothing to do with the hook.
export GIT_CEILING_DIRECTORIES="$FIXTURE_ROOT"

# Every fixture is verified, not just the resolvable one. The subshells above
# discard their exit status, and FIX_NOORIGIN / FIX_GITLAB both back cases
# that expect `pass`: if their `git init` or `git remote add` had failed, the
# directory would not be a checkout, resolution would return nothing, and
# those cases would still report PASS — proving nothing.
for _fixture in "$FIX_REPO" "$FIX_GITLAB"; do
  if ! ( cd "$_fixture" && git remote get-url origin ) >/dev/null 2>&1; then
    echo "FAIL: fixture $_fixture has no origin remote — its cases would pass vacuously" >&2
    exit 1
  fi
done
if ! ( cd "$FIX_NOORIGIN" && git rev-parse --git-dir ) >/dev/null 2>&1; then
  echo "FAIL: fixture $FIX_NOORIGIN is not a git checkout — its case would pass vacuously" >&2
  exit 1
fi
if ( cd "$FIX_NOORIGIN" && git remote get-url origin ) >/dev/null 2>&1; then
  echo "FAIL: fixture $FIX_NOORIGIN unexpectedly has an origin remote" >&2
  exit 1
fi
if ( cd "$FIX_PLAIN" && git rev-parse --git-dir ) >/dev/null 2>&1; then
  echo "FAIL: fixture $FIX_PLAIN resolves a git checkout — GIT_CEILING_DIRECTORIES is not holding" >&2
  exit 1
fi

# The #1149 fixtures are the same trap in a sharper form: each one asserts
# that the hook does NOT name `origin`, and a fixture whose `git config` or
# second `git remote add` silently failed has nothing but `origin` left — the
# deny-assertion would then pass while proving the opposite of its intent.
for _pair in \
  "$FIX_SETDEFAULT:remote.origin.gh-resolved" \
  "$FIX_SETDEFAULT_BASE:remote.origin.gh-resolved" \
  "$FIX_SETDEFAULT_BAD:remote.origin.gh-resolved" ; do
  _dir="${_pair%%:*}"; _key="${_pair##*:}"
  if ! ( cd "$_dir" && git config --local --get "$_key" ) >/dev/null 2>&1; then
    echo "FAIL: fixture $_dir has no $_key — its redirect case would pass vacuously" >&2
    exit 1
  fi
done
for _dir in "$FIX_SETDEFAULT_BASE" "$FIX_FORK"; do
  if ! ( cd "$_dir" && git remote get-url upstream ) >/dev/null 2>&1; then
    echo "FAIL: fixture $_dir has no upstream remote — its case would pass vacuously" >&2
    exit 1
  fi
done

# Default cwd for every case: the resolvable checkout. Cases that need a
# different checkout pass one explicitly as the trailing argument.
mk_payload() {
  python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}, "cwd": sys.argv[2]}))
' "$1" "${2:-$FIX_REPO}"
}

run_case() {
  local name="$1" expected="$2" command="$3" cwd="${4:-$FIX_REPO}"
  local out err_file err rc ok=1
  err_file=$(mktemp)
  out=$(mk_payload "$command" "$cwd" | "$HOOK" 2>"$err_file")
  rc=$?; err=$(cat "$err_file"); rm -f "$err_file"

  case "$expected" in
    ask)
      [ "$rc" -eq 0 ] || ok=0
      echo "$out" | grep -q '"permissionDecision": "ask"' || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      [ -n "$err" ]   || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$expected] $name"; PASS=$((PASS+1))
  else
    echo "FAIL  [$expected→rc=$rc,stdout=$([ -n "$out" ] && echo non-empty || echo empty),stderr=$([ -n "$err" ] && echo non-empty || echo empty)] $name"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# BLOCK cases — heredoc in same gh write command segment
# ---------------------------------------------------------------------------

run_case "heredoc in gh issue create" block \
  'gh issue create --title "foo" <<EOF'

# #1092: a path-prefixed gh binary must not slip past the boundary gate.
# `argv[0].text == "gh"` exact match previously let `/usr/bin/gh issue create`
# bypass; `_is_gh_binary` closes it.
run_case "heredoc in /usr/bin/gh issue create (path-prefix)" block \
  '/usr/bin/gh issue create --title "foo" <<EOF'

run_case "heredoc single-quoted in gh issue create" block \
  "gh issue create --title \"foo\" <<'EOF'"

run_case "heredoc dash strip in gh pr create" block \
  'gh pr create --title "t" <<-EOF'

run_case "heredoc via global --repo before subcommand" block \
  'gh --repo owner/repo issue create --title "t" <<EOF'

# ---------------------------------------------------------------------------
# ASK cases — --repo flag in gh write command
# ---------------------------------------------------------------------------

run_case "gh pr create --repo" ask \
  'gh pr create --repo owner/repo --title "feat: x" --body-file /tmp/b.md'

run_case "gh issue create --repo" ask \
  'gh issue create --repo owner/repo --title "bug report"'

run_case "gh issue comment --repo" ask \
  'gh issue comment 42 --repo owner/repo --body "hello"'

run_case "gh issue edit --repo" ask \
  'gh issue edit 7 --repo owner/repo --title "updated"'

run_case "gh pr edit --repo" ask \
  'gh pr edit 5 --repo owner/repo --title "updated"'

run_case "gh -R shorthand" ask \
  'gh -R owner/repo pr create --title "fix" --body-file /tmp/b.md'

run_case "--repo= equals form" ask \
  'gh issue create --repo=owner/repo --title "test"'

run_case "global --repo before pr create" ask \
  'gh --repo owner/repo pr create --title "t" --body-file /tmp/b.md'

run_case "gh pr new --repo" ask \
  'gh pr new --repo owner/repo --title "t" --body-file /tmp/b.md'

run_case "chained: safe cmd && gh pr create --repo" ask \
  'git fetch origin && gh pr create --repo owner/repo --title "t" --body-file /tmp/b.md'

# ---------------------------------------------------------------------------
# ASK: checklist includes Caller chain item for pr create
# ---------------------------------------------------------------------------

run_case_detail() {
  local name="$1" command="$2" needle="$3" cwd="${4:-$FIX_REPO}"
  local out err_file rc ok=1
  err_file=$(mktemp)
  out=$(mk_payload "$command" "$cwd" | "$HOOK" 2>"$err_file")
  rc=$?; rm -f "$err_file"
  [ "$rc" -eq 0 ] || ok=0
  echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); r=d.get('hookSpecificOutput',{}).get('permissionDecisionReason',''); sys.exit(0 if '$needle' in r else 1)" 2>/dev/null || ok=0
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [ask-detail] $name"; PASS=$((PASS+1))
  else
    echo "FAIL  [ask-detail: missing '$needle'] $name"; FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

run_case_detail "pr create checklist has Caller chain item" \
  'gh pr create --repo owner/repo --title "t" --body-file /tmp/b.md' \
  "Caller chain verified"

run_case_detail "issue create checklist no Caller chain item" \
  'gh issue create --repo owner/repo --title "t"' \
  "body-file"

# ---------------------------------------------------------------------------
# #993: own-org membership does not exempt a --repo write. The ASK must fire
# on an own-org target, and the checklist must say ownership is no exemption
# (the old wording read as "external repos only", which is what let own-org
# writes proceed without per-action approval).
# ---------------------------------------------------------------------------

run_case "own-org repo write is still cross-boundary (#993)" ask \
  'gh issue create --repo devseunggwan/praxis --title "t"'

run_case "own-org pr create is still cross-boundary (#993)" ask \
  'gh pr create --repo devseunggwan/praxis --title "t" --body-file /tmp/b.md'

run_case_detail "checklist states ownership is not an exemption (#993)" \
  'gh issue create --repo devseunggwan/praxis --title "t"' \
  "Ownership does NOT exempt"

# Opposite direction — the #993 wording must not turn read-only own-org
# traffic into an ask. This control has to stay silent.
run_case "own-org read-only stays silent (#993 control)" pass \
  'gh issue list --repo devseunggwan/praxis --state open'

# ---------------------------------------------------------------------------
# Message content: new bullet 3 appears in HEREDOC_BLOCK_MSG stderr
# ---------------------------------------------------------------------------

run_case_block_msg() {
  local name="$1" command="$2" needle="$3"
  local out err_file err rc ok=1
  err_file=$(mktemp)
  out=$(mk_payload "$command" | "$HOOK" 2>"$err_file")
  rc=$?; err=$(cat "$err_file"); rm -f "$err_file"
  [ "$rc" -eq 2 ] || ok=0
  echo "$err" | grep -qF "$needle" || ok=0
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [block-msg] $name"; PASS=$((PASS+1))
  else
    echo "FAIL  [block-msg: missing '$needle'] $name"; FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

run_case_block_msg "heredoc block msg contains ack placement note" \
  'gh issue create --title "foo" <<EOF' \
  "never inside the heredoc body"

# ---------------------------------------------------------------------------
# F1 regression: gh issue --repo X create (--repo between object and verb)
# ---------------------------------------------------------------------------

run_case "gh issue --repo X create (F1 fix)" ask \
  'gh issue --repo owner/repo create --title "t"'

run_case "gh pr --repo X create (F1 fix)" ask \
  'gh pr --repo owner/repo create --title "t" --body-file /tmp/b.md'

run_case "gh issue --repo X create with heredoc (F1+F2)" block \
  'gh issue --repo owner/repo create --title "t" <<EOF'

# ---------------------------------------------------------------------------
# F2 regression: heredoc attached to preceding token without space
# ---------------------------------------------------------------------------

run_case "heredoc attached to --title value (F2 fix)" block \
  'gh issue create --title foo<<EOF'

run_case "heredoc attached with double quotes (F2 fix)" block \
  'gh issue create --title "foo"<<EOF'

run_case "heredoc attached to --body-file path (F2 fix)" block \
  'gh issue create --body-file /tmp/body.md<<EOF'

# literal << inside quoted body string — must not BLOCK. It is repo-less, so
# in a resolvable checkout the #1148 arm asks; `ask` (not `block`) is what
# proves the heredoc path stayed out of it.
run_case "literal << in quoted body (F2 false-positive guard)" ask \
  'gh issue create --body "comparison: a << b is false"'

# ---------------------------------------------------------------------------
# ASK cases — no --repo flag, target resolved from the checkout (issue #1148)
#
# Same write, same target repo: the flag style must not decide the verdict.
# These four rows were `pass` before #1148 and are the asymmetry it removes.
# ---------------------------------------------------------------------------

run_case "gh pr create without --repo (#1148)" ask \
  'gh pr create --title "fix" --body "Caller chain verified: ok"'

run_case "gh issue create without --repo (#1148)" ask \
  'gh issue create --title "bug" --body-file /tmp/b.md'

# The --repo arm covers every pair in GH_WRITE_SUBCOMMANDS, so the repo-less
# arm must too — narrowing to `create` would only invent a fresh asymmetry.
run_case "gh issue new without --repo (#1148)" ask \
  'gh issue new --title "bug"'

run_case "gh pr new without --repo (#1148)" ask \
  'gh pr new --title "t" --body-file /tmp/b.md'

run_case "gh issue comment without --repo (#1148)" ask \
  'gh issue comment 42 --body "hello"'

run_case "gh issue edit without --repo (#1148)" ask \
  'gh issue edit 7 --title "updated"'

run_case "gh pr comment without --repo (#1148)" ask \
  'gh pr comment 5 --body "hello"'

run_case "gh pr edit without --repo (#1148)" ask \
  'gh pr edit 5 --title "updated"'

run_case "path-prefixed gh binary, repo-less (#1148)" ask \
  '/usr/bin/gh issue create --title "t"'

run_case "chained: safe cmd && repo-less gh issue create (#1148)" ask \
  'git fetch origin && gh issue create --title "t"'

# `cd <worktree> && gh ...` — the write runs in the cd target, so the target
# repo is resolved there, not in the payload's cwd.
run_case "cd into a resolvable checkout then repo-less gh (#1148)" ask \
  "cd $FIX_REPO && gh issue create --title \"t\"" \
  "$FIX_PLAIN"

# The checklist must name the repo the user is about to write to.
run_case_detail "repo-less checklist names the resolved target (#1148)" \
  'gh issue create --title "t"' \
  "devseunggwan/praxis"

run_case_detail "repo-less checklist says flag style is no exemption (#1148)" \
  'gh issue create --title "t"' \
  "Flag style does NOT exempt"

run_case_detail "repo-less pr create checklist keeps Caller chain item (#1148)" \
  'gh pr create --title "t" --body-file /tmp/b.md' \
  "Caller chain verified"

# ---------------------------------------------------------------------------
# SILENT controls for the repo-less arm — the fail-open contract (#1148)
#
# Anything that stops the local resolution from producing an `owner/repo`
# must exit 0 with no output. Silence beats guessing at the target.
# ---------------------------------------------------------------------------

run_case "repo-less write, cwd is not a checkout (#1148 fail-open)" pass \
  'gh issue create --title "t"' \
  "$FIX_PLAIN"

run_case "repo-less write, checkout has no origin remote (#1148 fail-open)" pass \
  'gh issue create --title "t"' \
  "$FIX_NOORIGIN"

run_case "repo-less write, cwd does not exist (#1148 fail-open)" pass \
  'gh issue create --title "t"' \
  "$FIXTURE_ROOT/does-not-exist"

run_case "cd into a non-checkout then repo-less gh (#1148 fail-open)" pass \
  "cd $FIX_PLAIN && gh issue create --title \"t\"" \
  "$FIX_REPO"

run_case "repo-less read-only stays silent in a resolvable checkout (#1148)" pass \
  'gh issue list --state open'

# ---------------------------------------------------------------------------
# EFFECTIVE TARGET — the ask must name the repo gh will actually write to
# (CodeRabbit CWE-863 / Incorrect Authorization on PR #1149)
#
# `origin` is not gh's answer to "which repo is this repo-less write for".
# cmdutil.OverrideBaseRepoFunc falls back to `GH_REPO`; failing that,
# Remotes.ResolvedRemote() honours `remote.<n>.gh-resolved` — the key
# `gh repo set-default` writes; failing that, gh takes the first remote in
# context.remoteNameSortScore order (upstream, github, origin, rest). Naming
# `origin` under any of the first three means the checklist shows one repo
# and the write lands in another, which is the whole failure this arm exists
# to prevent — the user authorizes a target they were never shown.
#
# Every case below is a NEGATIVE control as much as a positive one: `deny`
# pins the absence of the checkout's own `origin` slug. A positive assertion
# alone would pass on an ask that named both.
# ---------------------------------------------------------------------------

ORIGIN_SLUG="devseunggwan/praxis"

# assert_target <name> <GH_REPO|__unset__> <command> <want> <deny> [cwd]
assert_target() {
  local name="$1" gh_repo="$2" command="$3" want="$4" deny="$5" cwd="${6:-$FIX_REPO}"
  local out rc ok=1
  if [ "$gh_repo" = "__unset__" ]; then
    out=$(mk_payload "$command" "$cwd" | "$HOOK" 2>/dev/null)
  else
    out=$(mk_payload "$command" "$cwd" | GH_REPO="$gh_repo" "$HOOK" 2>/dev/null)
  fi
  rc=$?
  [ "$rc" -eq 0 ] || ok=0
  # want/deny travel as env vars, not as interpolated python source: a needle
  # containing a quote would otherwise rewrite the assertion it belongs to.
  printf '%s' "$out" | WANT="$want" DENY="$deny" python3 -c '
import json, os, sys
raw = sys.stdin.read().strip()
if not raw:
    sys.exit(1)
try:
    reason = json.loads(raw)["hookSpecificOutput"]["permissionDecisionReason"]
except Exception:
    sys.exit(1)
want, deny = os.environ["WANT"], os.environ["DENY"]
if want and want not in reason:
    sys.exit(1)
if deny and deny in reason:
    sys.exit(1)
sys.exit(0)
' || ok=0
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [target] $name"; PASS=$((PASS+1))
  else
    echo "FAIL  [target: want '$want' / deny '$deny'] $name"; FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# --- selector 1: GH_REPO ----------------------------------------------------

assert_target "GH_REPO redirects the ask away from the checkout's origin (#1149)" \
  "other/repo" 'gh issue create --title "t"' "other/repo" "$ORIGIN_SLUG"

# The env var need never reach the hook's own environment: an assignment
# prefixed to the command sets it for that one invocation, which is the most
# direct way there is to redirect a repo-less write.
assert_target "an inline GH_REPO= prefix redirects the ask too (#1149)" \
  "__unset__" 'GH_REPO=inline/target gh issue create --title "t"' \
  "inline/target" "$ORIGIN_SLUG"

assert_target "an env-wrapper GH_REPO= prefix redirects the ask too (#1149)" \
  "__unset__" 'env GH_REPO=wrapper/target gh issue create --title "t"' \
  "wrapper/target" "$ORIGIN_SLUG"

# `GH_REPO= gh …` CLEARS an inherited value — gh's os.Getenv then returns ""
# and falls through to the remotes, so the hook must fall through with it.
assert_target "an explicit empty GH_REPO= falls back to the checkout (#1149)" \
  "other/repo" 'GH_REPO= gh issue create --title "t"' \
  "$ORIGIN_SLUG" "other/repo"

# GH_REPO decides the target without consulting the checkout at all, so a cwd
# that is not a checkout no longer justifies silence.
assert_target "GH_REPO asks even when the cwd is not a checkout (#1149)" \
  "other/repo" 'gh issue create --title "t"' "other/repo" "" "$FIX_PLAIN"

# ...and for the same reason it outranks a `cd` this hook cannot model: the
# destination is irrelevant when the env var already fixed the target.
assert_target "GH_REPO outranks an unmodeled cd rather than asking UNRESOLVED (#1149)" \
  "other/repo" 'cd "$WORKTREE" && gh issue create --title "t"' \
  "other/repo" "UNRESOLVED" "$FIX_PLAIN"

# --- selector 2: gh repo set-default ---------------------------------------

assert_target "gh repo set-default redirects the ask away from origin (#1149)" \
  "__unset__" 'gh issue create --title "t"' \
  "redirected/target" "$ORIGIN_SLUG" "$FIX_SETDEFAULT"

# `base` names that remote's OWN repo, and must still beat the upstream
# preference that would otherwise apply in this fixture.
assert_target "a gh-resolved of 'base' names that remote's own repo (#1149)" \
  "__unset__" 'gh issue create --title "t"' \
  "$ORIGIN_SLUG" "upstreamowner" "$FIX_SETDEFAULT_BASE"

assert_target "GH_REPO outranks gh repo set-default, as it does for gh (#1149)" \
  "other/repo" 'gh issue create --title "t"' \
  "other/repo" "redirected/target" "$FIX_SETDEFAULT"

# --- selector 3: gh's remote preference order -------------------------------

# A fork checkout: gh scores upstream above origin, so origin is the wrong
# answer even with no GH_REPO and no set-default anywhere.
assert_target "an upstream remote outranks origin, as it does for gh (#1149)" \
  "__unset__" 'gh issue create --title "t"' \
  "upstreamowner/praxis" "$ORIGIN_SLUG" "$FIX_FORK"

# --- unparseable selectors ask UNRESOLVED, never origin ---------------------

assert_target "an unparseable GH_REPO asks UNRESOLVED, not origin (#1149)" \
  "not a repo name" 'gh issue create --title "t"' "UNRESOLVED" "$ORIGIN_SLUG"

assert_target "an unparseable gh-resolved asks UNRESOLVED, not origin (#1149)" \
  "__unset__" 'gh issue create --title "t"' \
  "UNRESOLVED" "$ORIGIN_SLUG" "$FIX_SETDEFAULT_BAD"

# --- the plain case is unchanged from f5e455d -------------------------------

assert_target "with neither selector set the ask still names origin (#1149 control)" \
  "__unset__" 'gh issue create --title "t"' "$ORIGIN_SLUG" "UNRESOLVED"

# The checklist must say WHICH selector chose the repo — "target: X" alone is
# not something the approver can check.
assert_target "the ask names the selector that chose the repo (#1149)" \
  "__unset__" 'gh issue create --title "t"' "resolved from git remote \`origin\`" ""

# --- GH_REPO must not widen or narrow any other arm -------------------------

run_case_env_pass() {
  local name="$1" expected="$2" command="$3" cwd="${4:-$FIX_REPO}"
  local out err_file err rc ok=1
  err_file=$(mktemp)
  out=$(mk_payload "$command" "$cwd" | GH_REPO="other/repo" "$HOOK" 2>"$err_file")
  rc=$?; err=$(cat "$err_file"); rm -f "$err_file"
  case "$expected" in
    pass)  { [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; } || ok=0 ;;
    block) { [ "$rc" -eq 2 ] && [ -n "$err" ]; } || ok=0 ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$expected+GH_REPO] $name"; PASS=$((PASS+1))
  else
    echo "FAIL  [$expected+GH_REPO→rc=$rc] $name"; FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

run_case_env_pass "GH_REPO does not make a read-only subcommand ask (#1149)" pass \
  'gh issue list --state open'

run_case_env_pass "GH_REPO does not make a non-gh command ask (#1149)" pass \
  'git status'

run_case_env_pass "GH_REPO does not silence the --help exclusion (#1149)" pass \
  'gh issue create --help'

run_case_env_pass "GH_REPO does not bypass the heredoc hard block (#1149)" block \
  'gh issue create --title "t" <<EOF'

# The `--repo` flag is gh's highest-precedence selector, above GH_REPO, so the
# flag arm must keep naming the flag's value.
assert_target "the --repo flag still outranks GH_REPO (#1149)" \
  "other/repo" 'gh issue create --repo flag/target --title "t"' \
  "flag/target" "other/repo"

# ---------------------------------------------------------------------------
# `--help` / `-h` is a usage query, not a write. The exclusion lives in the
# shared detector, so BOTH arms must stay silent — the --repo arm asked on
# these before #1148 and that was already wrong.
# ---------------------------------------------------------------------------

run_case "repo-less gh pr create --help stays silent (#1148)" pass \
  'gh pr create --help'

run_case "repo-less gh issue create -h stays silent (#1148)" pass \
  'gh issue create -h'

run_case "--repo gh pr create --help stays silent (#1148)" pass \
  'gh pr create --repo devseunggwan/praxis --help'

run_case "--repo gh issue create -h stays silent (#1148)" pass \
  'gh issue create --repo devseunggwan/praxis -h'

# Control: the exclusion must key on the flag, not on the word "help"
# appearing anywhere in the segment. A real write whose title says "help"
# still asks.
run_case "a write whose title contains 'help' still asks (#1148 control)" ask \
  'gh issue create --title "help the parser"'

# ---------------------------------------------------------------------------
# The usage-query exclusion must key on the token ROLE, not its text. Matching
# text alone let a flag VALUE of `-h` silence the whole segment — including
# the heredoc hard block, which is a hard block precisely because a heredoc
# body bypasses the caller-chain and falsification hooks.
# ---------------------------------------------------------------------------

run_case "a --title value of -h does not disable the --repo arm" ask \
  'gh issue create --repo victim/repo --title "-h" --body-file /tmp/b.md'

run_case "a --title value of -h does not disable the repo-less arm" ask \
  'gh issue create --title "-h" --body-file /tmp/b.md'

run_case "a --title value of -h does not defeat the heredoc block" block \
  'gh issue create --repo o/r --title "-h" <<EOF
body
EOF'

run_case "a real --help does not defeat the heredoc block either" block \
  'gh issue create --help <<EOF
body
EOF'

# ---------------------------------------------------------------------------
# A `cd` that cannot succeed must not move the resolution target: bash leaves
# the shell in the original checkout, so the write still lands here.
# ---------------------------------------------------------------------------

run_case "cd to a nonexistent dir then repo-less gh still asks" ask \
  "cd $FIXTURE_ROOT/definitely-not-here ; gh issue create --title \"t\""

run_case "cd to a nonexistent dir with && still asks" ask \
  "cd $FIXTURE_ROOT/definitely-not-here && gh issue create --title \"t\""

# ---------------------------------------------------------------------------
# Origin-host matching
# ---------------------------------------------------------------------------

run_case "a gitlab remote with 'github' in the path stays silent" pass \
  'gh issue create --title "t"' \
  "$FIX_GITLAB"

# A relative `cd`. The hook joins the target onto the effective cwd, so the
# relative branch of that join was never exercised by the absolute-path cases.
run_case "a relative cd into a resolvable checkout then repo-less gh asks" ask \
  'cd resolvable && gh issue create --title "t"' \
  "$FIXTURE_ROOT"

# ---------------------------------------------------------------------------
# A `cd` this hook cannot model must not fail open. Resolving from the OUTER
# cwd authorizes a write against a repo it never touches, and when that outer
# cwd is not a checkout the gate went silent entirely — a fail-open on an
# authorization decision (CodeRabbit CWE-863 on PR #1149).
# ---------------------------------------------------------------------------

run_case "cd to a shell-expanded target then gh asks with an unresolved target" ask \
  'cd "$WORKTREE" && gh issue create --title "t"' \
  "$FIX_PLAIN"

run_case "a subshell cd then gh asks with an unresolved target" ask \
  '(cd /elsewhere && gh issue create --title "t")' \
  "$FIX_PLAIN"

run_case "cd to a tilde path then gh asks with an unresolved target" ask \
  'cd ~/x && gh issue create --title "t"' \
  "$FIX_PLAIN"

run_case "a bare cd then gh asks with an unresolved target" ask \
  'cd && gh issue create --title "t"' \
  "$FIX_PLAIN"

run_case_detail "the unresolved-target ask says so rather than naming a repo" \
  'cd "$WORKTREE" && gh issue create --title "t"' \
  "UNRESOLVED"

# The unresolved ask must not render as a `--repo` flag the command does not
# carry: `_build_checklist`'s flag header puts the repo INSIDE the quoted
# command, so the header read "`gh issue create --repo UNRESOLVED — …`".
assert_target "the unresolved ask does not invent a --repo flag (#1149)" \
  "__unset__" 'cd "$WORKTREE" && gh issue create --title "t"' \
  "(no --repo flag)" "--repo UNRESOLVED" "$FIX_PLAIN"

# Controls: an unmodeled cd must not make everything ask.
run_case "an unmodeled cd with no gh write stays silent" pass \
  'cd "$WORKTREE" && ls -la' \
  "$FIX_PLAIN"

run_case "an unmodeled cd before a read-only gh stays silent" pass \
  'cd "$WORKTREE" && gh issue list --state open' \
  "$FIX_PLAIN"

run_case "a literal cd is still followed, not treated as unresolved" pass \
  "cd $FIXTURE_ROOT/not-a-repo && gh issue create --title \"t\"" \
  "$FIX_PLAIN"

run_case "repo-less gh pr view stays silent (#1148)" pass \
  'gh pr view 12 --json title'

run_case "gh mention inside an echo body stays silent (#1148)" pass \
  'echo "run gh issue create --title t to file it"'

run_case "gh mention inside a commit message stays silent (#1148)" pass \
  'git commit -m "docs: describe gh pr create usage"'

run_case "gh mention inside a grep pattern stays silent (#1148)" pass \
  'grep -rn "gh issue create" docs/'

run_case "opt-out marker silences the repo-less arm (#1148)" pass \
  'gh issue create --title "t"  # cross-boundary:ack'

# The heredoc hard-block sits ahead of BOTH ask arms and ignores the marker,
# including on a repo-less command in a resolvable checkout.
run_case "heredoc block precedes the repo-less arm (#1148)" block \
  'gh issue create --title "t" <<EOF'

run_case "heredoc block ignores marker on a repo-less write (#1148)" block \
  'gh issue create --title "t" <<EOF
body line
EOF
# cross-boundary:ack'

# ---------------------------------------------------------------------------
# PASS cases — read-only subcommands, non-gh commands, opt-out
# ---------------------------------------------------------------------------

run_case "gh issue list --repo (read-only subcommand)" pass \
  'gh issue list --repo owner/repo --state open'

run_case "gh pr list --repo (read-only subcommand)" pass \
  'gh pr list --repo owner/repo'

run_case "gh search issues (handled by block-gh-state-all)" pass \
  'gh search issues --repo owner/repo --state open'

run_case "non-gh command with <<" pass \
  'cat <<EOF > /tmp/file.txt'

run_case "git command" pass \
  'git push origin main'

run_case "opt-out marker" pass \
  'gh pr create --repo owner/repo --title "t" --body-file /tmp/b.md  # cross-boundary:ack'

# Codex #224: marker placed inside heredoc body must NOT be honored as opt-out.
# Otherwise the marker leaks into the published artifact AND the hook bypasses
# its block. The new _strip_heredoc_bodies sanitizes the command before the
# OPT_OUT_MARKER lookup, so this case re-enters the heredoc block path.
run_case "marker inside heredoc body is rejected as opt-out" block \
  'gh issue create --title "t" <<EOF
body line
# cross-boundary:ack
EOF'

# Codex round 2 — numeric / single-char heredoc delimiter must also strip body
# (regex now accepts [A-Za-z0-9_]+ instead of identifier-only).
run_case "numeric heredoc delimiter: marker in body still blocks" block \
  'gh issue create --title "t" <<1
body line
# cross-boundary:ack
1'

# Codex round 2 — `<<` literal inside quoted body must NOT trigger heredoc
# block (quoted-string false positive). shlex preserves internal spaces,
# so tokens with spaces are necessarily quoted; their `<<` is literal.
# Use --repo + ask expectation so cross-boundary checklist still surfaces,
# proving the heredoc-block path did NOT fire on this command.
run_case "quoted body containing << literal does not block" ask \
  'gh issue create --repo owner/repo --title "t" --body "code: a<<b"'

# Codex round 3 — opt-out marker placed in shell command portion must NOT
# bypass the heredoc hard-block. The marker only opts out of the cross-repo
# checklist; heredoc bypasses caller-chain evidence regardless of marker.
run_case "marker outside heredoc body does not bypass heredoc block" block \
  'gh issue create --title "t" <<EOF
body line
EOF
# cross-boundary:ack'

# Codex round 3+4 known limitation: `--title "foo bar"<<EOF` tokenizes as
# `foo bar<<EOF` (quoted-token guard skips it). The round-3 raw-command
# heredoc detection was reverted in round 4 because it caused false positives
# on legitimate var-heredoc and file-prep patterns (`BODY=$(cat <<EOF ... EOF);
# gh ...`, `cat <<EOF > /tmp/body.md; gh issue create --body-file /tmp/body.md`).
# Tracked as follow-up; for now we accept the narrow miss (heredoc attached
# directly after a quoted argument value) to keep the hook usable.

# Round 4 regression guard — var-heredoc on a different segment must not
# block. These three writes are repo-less, so since #1148 the expected
# non-block outcome is `ask`, not `pass`; `ask` still proves the heredoc
# hard-block (exit 2) did not fire on the separate segment.
run_case "var-heredoc separate segment then gh body does not block" ask \
  'BODY=$(cat <<EOF
some body
EOF
)
gh issue create --title "t" --body "$BODY"'

run_case "file-prep heredoc then gh body-file does not block" ask \
  'cat <<EOF > /tmp/body.md
some body
EOF
gh issue create --title "t" --body-file /tmp/body.md'

# Variable-assigned heredoc followed by gh pr create — heredoc in different segment
run_case "var-heredoc then gh pr create does not block" ask \
  'gh pr create --title "t" --body "$BODY"'

# ---------------------------------------------------------------------------
# Cascade-hint suffix (issue #229) — shared cascade advisory text appears in
# stderr when the heredoc block fires on a compound bash with a state-change.
# ---------------------------------------------------------------------------

_cascade_err=$(mk_payload 'echo seed > /tmp/x && gh pr create --title t <<EOF' | "$HOOK" 2>&1 >/dev/null)
if printf '%s' "$_cascade_err" | grep -q "PreToolUse rejection (block or denied ask) aborts ALL parts atomically"; then
  echo "PASS  [cascade hint on compound heredoc block]"; PASS=$((PASS+1))
else
  echo "FAIL  [cascade hint missing on compound heredoc block]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("cascade hint on heredoc block")
fi

# Single-command heredoc block: no compound chain → no cascade hint
_single_err=$(mk_payload 'gh pr create --title "t" <<EOF' | "$HOOK" 2>&1 >/dev/null)
if printf '%s' "$_single_err" | grep -q "PreToolUse rejection (block or denied ask) aborts ALL parts atomically"; then
  echo "FAIL  [cascade hint leaked on single-command heredoc block]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("cascade hint leaked single heredoc")
else
  echo "PASS  [no cascade hint on single-command heredoc block]"; PASS=$((PASS+1))
fi

# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

non_bash_out=$(echo '{"tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}' | "$HOOK" 2>/dev/null)
if [ -z "$non_bash_out" ]; then
  echo "PASS  [non-Bash passthrough]"; PASS=$((PASS+1))
else
  echo "FAIL  [non-Bash passthrough] got: $non_bash_out"; FAIL=$((FAIL+1)); FAILED_NAMES+=("non-Bash passthrough")
fi

bad_out=$(echo 'not-json' | "$HOOK" 2>/dev/null)
bad_rc=$?
if [ "$bad_rc" -eq 0 ] && [ -z "$bad_out" ]; then
  echo "PASS  [malformed JSON fail-open]"; PASS=$((PASS+1))
else
  echo "FAIL  [malformed JSON fail-open] rc=$bad_rc out=$bad_out"; FAIL=$((FAIL+1)); FAILED_NAMES+=("malformed JSON")
fi
# Fail-open guard opt-in (issue #498): main() must be @fail_open-wrapped;
# guard behavior is tested centrally in tests/test_hook_runtime.sh.
_failopen_out=$(python3 - << PYEOF 2>&1
import importlib.util
spec = importlib.util.spec_from_file_location("impl", "$REPO_ROOT/hooks/preflight-gate/cross-boundary-preflight/impl.py")
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
echo
echo "=================================="
echo "  PASS: $PASS  FAIL: $FAIL"
echo "=================================="
if [ "$FAIL" -gt 0 ]; then
  printf '  failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
