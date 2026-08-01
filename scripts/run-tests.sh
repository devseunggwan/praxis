#!/usr/bin/env bash
# Single entry point for the full test suite.
#
# Runs:
#   1. pytest   — Python unit tests under tests/
#   2. shell    — shell tests at tests/hooks/*/test_*.sh and tests/test_*.sh
#   3. manifest — scripts/check-plugin-manifests.py
#   4. invariants — scripts/check-hook-token-invariants.py
#   5. ruff     — static Python lint (mirrors the ci.yml `ruff` job)
#   6. shellcheck — static shell lint (mirrors the ci.yml `shellcheck` job)
#   7. markdownlint — advisory markdown lint (mirrors the ci.yml `markdownlint` job)
#
# Steps 5-7 mirror CI jobs that used to have no local equivalent, so a change
# could pass here and still be flagged on the PR (issue #866). Each skips with
# an explicit SKIPPED line when its tool is absent, so a contributor without
# the toolchain is not blocked — CI remains authoritative either way.
#
# Exit code is non-zero if any step fails.
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

FAILED=0

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
# 5. Ruff (mirrors ci.yml `ruff` job — blocking there, blocking here)
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
  echo "SKIPPED: ruff (not installed — pip install 'ruff==0.15.8')"
elif ! "${RUFF[@]}" check .; then
  FAILED=1
fi

# ---------------------------------------------------------------------------
# 6. Shellcheck (mirrors ci.yml `shellcheck` job — same find/severity)
# ---------------------------------------------------------------------------
echo ""
echo "=== shellcheck ==="
if ! command -v shellcheck >/dev/null 2>&1; then
  echo "SKIPPED: shellcheck (not installed — brew install shellcheck)"
else
  # Mirrors the CI invocation verbatim so severity and file set cannot drift.
  if ! find . -name '*.sh' -not -path './.git/*' -print0 \
    | xargs -0 shellcheck --severity=warning; then
    FAILED=1
  fi
fi

# ---------------------------------------------------------------------------
# 7. Markdownlint (mirrors ci.yml `markdownlint` job)
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
  echo "SKIPPED: markdownlint (not installed — npm i -g markdownlint-cli2)"
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
echo "ALL TESTS PASSED"
