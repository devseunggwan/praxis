#!/usr/bin/env bash
# AC-12 (M2): the silent-pass catalog is monotonic.
#
# The class-id set in the current catalog MUST be a superset of the previous
# committed version — class ids may be ADDED but never silently REMOVED. A
# removed detection class is a silent completeness regression (a whole silent-
# pass conduct family stops being force-injected), so it must fail CI.
#
# Compares the working-tree catalog against `git show HEAD~1:<catalog>`. When no
# prior version exists (first introduction, or shallow/no git), the check passes
# by definition.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
CATALOG_REL="hooks/_lib/silent-pass-catalog.json"
CATALOG="$ROOT/$CATALOG_REL"

PASS=0
FAIL=0

command -v jq >/dev/null 2>&1 || { echo "SKIP  jq unavailable"; exit 0; }
[ -f "$CATALOG" ] || { echo "FAIL  catalog missing: $CATALOG"; exit 1; }

cur=$(jq -r '.classes[]?.id' "$CATALOG" 2>/dev/null | sort -u)
if [ -z "$cur" ]; then
  echo "FAIL  current catalog has no class ids"
  exit 1
fi

# Previous committed version of the catalog (if any).
prev_raw=$(git -C "$ROOT" show "HEAD~1:$CATALOG_REL" 2>/dev/null || true)
if [ -z "$prev_raw" ]; then
  echo "PASS  no prior catalog version (first introduction) — monotonic by definition"
  PASS=$((PASS + 1))
else
  prev=$(printf '%s' "$prev_raw" | jq -r '.classes[]?.id' 2>/dev/null | sort -u)
  # ids present in prev but absent in cur = silent deletion.
  removed=$(comm -23 <(printf '%s\n' "$prev") <(printf '%s\n' "$cur") | grep -v '^$' || true)
  if [ -n "$removed" ]; then
    echo "FAIL  class-id(s) removed vs HEAD~1 (non-monotonic):"
    printf '  - %s\n' $removed
    FAIL=$((FAIL + 1))
  else
    echo "PASS  class-id set is a superset of HEAD~1 (no silent deletion)"
    PASS=$((PASS + 1))
  fi
fi

printf -- '--- catalog-monotonic: %d pass / %d fail ---\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
