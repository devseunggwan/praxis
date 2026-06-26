#!/usr/bin/env bash
# Single entry point for the full test suite.
#
# Runs:
#   1. pytest   — Python unit tests under tests/
#   2. shell    — shell tests at tests/hooks/*/test_*.sh and tests/test_*.sh
#   3. manifest — scripts/check-plugin-manifests.py
#   4. invariants — scripts/check-hook-token-invariants.py
#
# Exit code is non-zero if any step fails.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

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
echo ""
if [[ $FAILED -ne 0 ]]; then
  echo "TEST SUITE FAILED" >&2
  exit 1
fi
echo "ALL TESTS PASSED"
