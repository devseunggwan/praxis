#!/usr/bin/env bash
# Single entry point for the full test suite.
#
# Runs:
#   1. pytest   — Python unit tests under tests/
#   2. shell    — shell tests at tests/hooks/*/test_*.sh and tests/test_*.sh
#   3. manifest — scripts/check-plugin-manifests.py
#   4. invariants — scripts/check-hook-token-invariants.py
#   5. sibling-gates — scripts/check-sibling-commit-gates.py
#   6. memory-frontmatter — scripts/check-memory-frontmatter.py
#   7. omc-name-drift — scripts/check-omc-name-drift.py
#   8. ruff     — static Python lint (mirrors the ci.yml `ruff` job)
#   9. shellcheck — static shell lint (mirrors the ci.yml `shellcheck` job)
#  10. markdownlint — advisory markdown lint (mirrors the ci.yml `markdownlint` job)
#
# Steps 8-10 mirror CI jobs that used to have no local equivalent, so a change
# could pass here and still be flagged on the PR (issue #866). Each skips with
# an explicit SKIPPED line when its tool is absent, so a contributor without
# the toolchain is not blocked — CI remains authoritative either way.
#
# Step 6 is a different repo-internal script, same family as steps 3-5 (no
# external toolchain — nothing to install), but its skip condition is not
# like steps 8-10's: the memory directory it lints is a local, gitignored,
# per-user store that is structurally absent in CI or a fresh checkout,
# always, forever (issue #942) — not a "toolchain not installed" gap a
# contributor can close. It prints "N/A", never "SKIPPED", to keep that
# distinction visible in the log (see its own docstring / #917 below), and
# this call strips PRAXIS_TESTS_STRICT (`env -u`, not passed through like
# steps 8-10) so that permanent N/A can never fail the job. Real drift
# (nonzero exit with violations listed) still counts as FAILED, same as
# steps 3-5 — this is not routed through the SKIPPED_TOOLS/skip_step() path
# at all.
#
# A skip is not a pass (#917). The SKIPPED line used to scroll past and the run
# still ended on a bare "ALL TESTS PASSED", which read as full coverage: PR #912
# shipped 15 markdownlint violations that way, and PR #864 had shipped 23 for the
# same reason five days earlier. The summary now names what was skipped, and
# PRAXIS_TESTS_STRICT=1 turns any skip into a non-zero exit — opt-in locally,
# always on in CI, where the toolchain is installed and a skip means the job
# silently stopped checking something.
#
# Exit code is non-zero if any step fails, or if anything was skipped under
# PRAXIS_TESTS_STRICT=1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

# Run against a throwaway PRAXIS_HOME (#903). Hooks now resolve their runtime
# files through it, so without this the suite would write into — and let
# prune_stale sweep — the developer's own ~/.praxis. Individual cases that care
# about the default still unset it themselves (see tests/test_paths.sh).
PRAXIS_HOME="$(mktemp -d)" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
export PRAXIS_HOME
trap 'rm -rf "$PRAXIS_HOME"' EXIT

# Same isolation, for the fire-ledger (#849). resolve_path() in
# hooks/_lib/_fire_ledger.py does NOT fall under PRAXIS_HOME — it defaults to
# Path.home()/.praxis/telemetry regardless, so PRAXIS_HOME alone leaves it
# writing into the developer's real ledger. Most tests/hooks/*/test_*.sh files
# invoke an instrumented impl.sh/impl.py directly and never set
# PRAXIS_FIRE_TELEMETRY_FILE themselves (only a handful do, e.g.
# tests/hooks/_lib/test_record_fire.sh) — mirrors the pytest-side fix in
# tests/conftest.py: an inline `PRAXIS_FIRE_TELEMETRY_FILE=... command`
# prefix on a specific call still wins over this exported default.
export PRAXIS_FIRE_TELEMETRY_FILE="$PRAXIS_HOME/fire-events-test.jsonl"

# Third isolation, and the one that breaks the pattern of the two above (#1003).
# CLAUDE_CONFIG_DIR relocates Claude Code's config root, and resolve_memory_dir()
# treats it as authoritative over the HOME-derived default (#853). A test that
# builds a HOME fixture and expects that default therefore reads the developer's
# real store instead. The fix cannot be a throwaway export the way PRAXIS_HOME
# was: what wins is the variable being *set*, not its value, so an override
# fails those tests identically to the ambient value. Unset is the only
# isolation. Saved first, because step 6 wants the real root back.
CLAUDE_CONFIG_DIR_AMBIENT="${CLAUDE_CONFIG_DIR-}"
unset CLAUDE_CONFIG_DIR

FAILED=0
SKIPPED_TOOLS=()

# Records a skipped step and prints its SKIPPED line in one place, so the
# summary can never drift from what the run actually announced.
skip_step() {
  SKIPPED_TOOLS+=("$1")
  echo "SKIPPED: $1 (not installed — $2)"
}

# ---------------------------------------------------------------------------
# 1. pytest
# ---------------------------------------------------------------------------
echo "=== pytest ==="
if ! python3 -m pytest tests/ -q; then
  FAILED=1
fi

# ---------------------------------------------------------------------------
# 2. Shell tests
# ---------------------------------------------------------------------------
echo ""
echo "=== shell tests ==="
SHELL_FAILED=0

run_sh() {
  local f="$1"
  if ! bash "$f"; then
    echo "FAIL: $f" >&2
    SHELL_FAILED=1
  fi
}

# nullglob: an unmatched glob expands to nothing instead of the literal pattern,
# so a missing tests/hooks/ dir does not produce a spurious "No such file" failure.
shopt -s nullglob

# hook-level shell tests
for f in tests/hooks/*/test_*.sh; do
  run_sh "$f"
done

# top-level shell tests
for f in tests/test_*.sh; do
  run_sh "$f"
done

shopt -u nullglob

if [[ $SHELL_FAILED -ne 0 ]]; then
  FAILED=1
fi

# ---------------------------------------------------------------------------
# 3. Manifest check
# ---------------------------------------------------------------------------
echo ""
echo "=== manifest check ==="
if ! python3 ./scripts/check-plugin-manifests.py; then
  FAILED=1
fi

# ---------------------------------------------------------------------------
# 4. Hook-token invariant canary (dual-SoT drift guard, issue #712)
# ---------------------------------------------------------------------------
echo ""
echo "=== hook-token invariant check ==="
if ! python3 ./scripts/check-hook-token-invariants.py; then
  FAILED=1
fi

# ---------------------------------------------------------------------------
# 5. Sibling commit-gate derivation (manifest-vs-prose drift guard, issue #1127)
#
# Same family as steps 3-4: a repo-internal script with no external toolchain,
# so it never skips. It re-derives the `git commit` sibling-gate list from the
# manifest's `gates` field and fails when a prose enumeration or a count word
# drifted from it.
# ---------------------------------------------------------------------------
echo ""
echo "=== sibling commit-gate check ==="
if ! python3 ./scripts/check-sibling-commit-gates.py; then
  FAILED=1
fi

# ---------------------------------------------------------------------------
# 6. Memory frontmatter lint (schema-drift guard, issue #942)
# ---------------------------------------------------------------------------
echo ""
echo "=== memory frontmatter lint ==="
# PRAXIS_TESTS_STRICT is deliberately NOT propagated to this one call: unlike
# steps 8-10 (a tool a contributor could install), the memory dir this checks
# is a local, gitignored, per-user store that structurally never exists in
# CI or a fresh checkout — treating its absence as a strict-mode failure
# would fail every CI run forever, not flag a fixable gap. The script prints
# "N/A", not "SKIPPED", for exactly this reason: #917 exists because a
# scrolling SKIPPED line lost its signal value once contributors stopped
# reading it, and a condition that can never be fixed would sit there as
# permanent unresolvable noise if it wore the same "SKIPPED" label as steps
# 8-10's genuinely-fixable tool-absence skips. An N/A here is always benign;
# only actual detected drift (exit 1 with violations listed) fails this step.
# The script's own PRAXIS_TESTS_STRICT support still works for direct
# standalone invocation (see its tests / docstring).
# Re-inject the ambient CLAUDE_CONFIG_DIR the preamble unset (#1003). This is
# the one step whose whole point is reading the developer's *real* memory store,
# so the suite-wide isolation above would silently retarget it — at the default
# root, which on a relocated host holds a different store or none at all.
MEMCHECK_ENV=(env -u PRAXIS_TESTS_STRICT)
if [ -n "$CLAUDE_CONFIG_DIR_AMBIENT" ]; then
  MEMCHECK_ENV+=("CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR_AMBIENT")
fi
if ! "${MEMCHECK_ENV[@]}" python3 ./scripts/check-memory-frontmatter.py; then
  FAILED=1
fi

# ---------------------------------------------------------------------------
# 7. omc name-drift guard (retired-workflow references, issue #1122)
# ---------------------------------------------------------------------------
echo ""
echo "=== omc name-drift check ==="
if ! python3 ./scripts/check-omc-name-drift.py; then
  FAILED=1
fi

# ---------------------------------------------------------------------------
# 8. Ruff (mirrors ci.yml `ruff` job — blocking there, blocking here)
# ---------------------------------------------------------------------------
echo ""
echo "=== ruff ==="
if command -v ruff >/dev/null 2>&1; then
  RUFF=(ruff)
elif python3 -m ruff --version >/dev/null 2>&1; then
  RUFF=(python3 -m ruff)
else
  RUFF=()
fi

if [[ ${#RUFF[@]} -eq 0 ]]; then
  skip_step ruff "pip install 'ruff==0.15.8'"
elif ! "${RUFF[@]}" check .; then
  FAILED=1
fi

# ---------------------------------------------------------------------------
# 9. Shellcheck (mirrors ci.yml `shellcheck` job — same find/severity)
# ---------------------------------------------------------------------------
echo ""
echo "=== shellcheck ==="
if ! command -v shellcheck >/dev/null 2>&1; then
  skip_step shellcheck "brew install shellcheck"
else
  # Mirrors the CI invocation verbatim so severity and file set cannot drift.
  if ! find . -name '*.sh' -not -path './.git/*' -print0 \
    | xargs -0 shellcheck --severity=warning; then
    FAILED=1
  fi
fi

# ---------------------------------------------------------------------------
# 10. Markdownlint (mirrors ci.yml `markdownlint` job)
#
# CI runs this with filter_mode:added and never fails the check, so the repo's
# existing backlog stays untouched. The advisory half is mirrored exactly — a
# violation warns without setting FAILED. The scope is deliberately WIDER than
# CI: reviewdog filters to added *lines*, this filters to changed *files*, so
# pre-existing violations in a file you touched will also print. Reproducing
# per-line filtering would need the reviewdog diff machinery; since nothing
# here can fail the run, the extra noise is the cheaper trade.
# ---------------------------------------------------------------------------
echo ""
echo "=== markdownlint (advisory) ==="
if command -v markdownlint-cli2 >/dev/null 2>&1; then
  MDL=(markdownlint-cli2)
elif command -v markdownlint >/dev/null 2>&1; then
  MDL=(markdownlint)
elif [[ -x node_modules/.bin/markdownlint-cli2 ]]; then
  # Probed by path, not by invoking npx: `npx --no-install <pkg>` still hits the
  # registry to resolve the manifest before refusing to install, so on an
  # offline or proxy-blocked host the detection itself stalls the runner.
  MDL=(node_modules/.bin/markdownlint-cli2)
else
  MDL=()
fi

if [[ ${#MDL[@]} -eq 0 ]]; then
  skip_step markdownlint "npm i -g markdownlint-cli2"
else
  # Diff base: the merge-base with origin/main when it is known, else the whole
  # tracked set. `git merge-base` failing (no origin/main in a fresh clone) must
  # not abort the runner, hence the guarded assignment under `set -e`.
  MD_BASE=""
  if git rev-parse --verify --quiet origin/main >/dev/null; then
    MD_BASE="$(git merge-base HEAD origin/main 2>/dev/null || true)"
  fi

  # Collected with a read loop, not `mapfile`: macOS ships bash 3.2, which has
  # no mapfile, and local/CI parity is the whole point of this step.
  MD_FILES=()
  if [[ -n "$MD_BASE" ]]; then
    while IFS= read -r f; do
      [[ -n "$f" ]] && MD_FILES+=("$f")
    done < <(git diff --name-only --diff-filter=d "$MD_BASE" -- '*.md')
  else
    echo "NOTE: origin/main unavailable — linting all tracked markdown"
    while IFS= read -r f; do
      [[ -n "$f" ]] && MD_FILES+=("$f")
    done < <(git ls-files -- '*.md')
  fi

  if [[ ${#MD_FILES[@]} -eq 0 ]]; then
    echo "no changed markdown files"
  elif ! "${MDL[@]}" "${MD_FILES[@]}"; then
    echo "WARNING: markdownlint findings above are advisory (CI does not fail on them)" >&2
  fi
fi

# ---------------------------------------------------------------------------
echo ""
if [[ $FAILED -ne 0 ]]; then
  echo "TEST SUITE FAILED" >&2
  exit 1
fi

# Scope note: this counts the three tool steps this script owns (8-10). Step 6
# has its own N/A line (deliberately not "SKIPPED") and is excluded from this
# tally on purpose — it is never a missing-toolchain skip. Gates
# that a sub-suite skips internally — e.g. the Darwin-only gate in
# tests/test_codex_broker_reaper.sh — announce themselves in their own summary
# and are not aggregated here; conflating them would make a portable-by-design
# skip look like a missing toolchain.
if [[ ${#SKIPPED_TOOLS[@]} -eq 0 ]]; then
  echo "ALL TESTS PASSED"
  exit 0
fi

skipped_list="$(IFS=', '; echo "${SKIPPED_TOOLS[*]}")"
if [[ "${PRAXIS_TESTS_STRICT:-0}" == "1" ]]; then
  echo "TEST SUITE INCOMPLETE (${#SKIPPED_TOOLS[@]} skipped: $skipped_list)" >&2
  echo "PRAXIS_TESTS_STRICT=1 treats a skipped step as a failure — install the tool(s) above or unset the variable." >&2
  exit 1
fi
echo "ALL TESTS PASSED (${#SKIPPED_TOOLS[@]} skipped: $skipped_list)"
