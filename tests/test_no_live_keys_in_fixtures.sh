#!/usr/bin/env bash
# AC-11 (H2): no committed catalog/fixture file may contain a live-key-shaped
# string outside the fabricated allowlist.
#
# Guards against a real AWS access-key-id / secret-access-key / PEM body leaking
# into version control through the silent-pass catalog or a test fixture (R7).
#
# Danger patterns:
#   1. AWS access key id:  AKIA[0-9A-Z]{16}
#   2. AWS secret shape:   a 40-char base64 token WITH entropy markers
#      (mixed case, OR a `/`|`+`). The entropy filter is essential: it flags a
#      REAL secret (mixed-case / base64-symbol) while NOT flagging the fabricated
#      lowercase-only stand-ins the silent-pass fixtures deliberately use (those
#      must stay OFF the catalog allowlist so the scanner still detects them as
#      credential-display, yet they are not live-key-shaped, so this scan stays
#      green — this is the deadlock-break the plan's entropy discriminator buys).
#
# A 40-hex git SHA (lowercase hex, no uppercase, no `/`|`+`) is intentionally
# NOT flagged.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
CATALOG="$ROOT/hooks/_lib/silent-pass-catalog.json"

PASS=0
FAIL=0

# The marker line lets run-tests.sh fold this whole-file skip into its
# strict-mode accounting (#1170) — without it the secret scan "passes" green.
command -v jq >/dev/null 2>&1 || { echo "PRAXIS_SUBSKIP: jq $0"; echo "SKIP  jq unavailable"; exit 0; }

# Allowlist of fabricated placeholders (from the catalog SoT).
ALLOW=$(jq -r '.fabricated_fixture_allowlist[]?' "$CATALOG" 2>/dev/null)

# Files in scope: the catalog + every committed test fixture.
FILES=()
[ -f "$CATALOG" ] && FILES+=("$CATALOG")
while IFS= read -r f; do FILES+=("$f"); done < <(find "$ROOT/tests/fixtures" -type f 2>/dev/null | sort)

is_allowlisted() { printf '%s\n' "$ALLOW" | grep -qxF "$1"; }

# Known-benign hits in verbatim fixtures, keyed as "<basename>:<exact token>".
# These are NOT fabricated placeholders (that list lives in the catalog and is
# also consumed by scan-silent-pass.sh), so they are exempted here instead. An
# entry must name the file AND the exact 40-char window, so it can never mask a
# real key elsewhere. Add one only with the issue that explains the text.
#
#   54128d0c.txt — real commit message (#736) reproduced verbatim by
#   tests/fixtures/commit-message-paren-check/ (issue #1302); the "token" is a
#   slash-separated run of markdownlint rule ids (MD032/MD040/...), not a key.
BENIGN_FIXTURE_HITS=(
  "54128d0c.txt:MD032/MD040/MD022/MD001/MD031/MD038/MD02"
)
is_benign_fixture_hit() {
  local key="$1:$2" entry
  for entry in "${BENIGN_FIXTURE_HITS[@]}"; do
    [ "$entry" = "$key" ] && return 0
  done
  return 1
}

leaked=0
for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue
  base=$(basename "$f")

  # (1) AKIA access-key ids.
  while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    is_allowlisted "$hit" && continue
    FAIL=$((FAIL + 1)); leaked=1
    printf 'FAIL  AKIA-shaped token in %s: %s\n' "$base" "$hit"
  done < <(grep -oE 'AKIA[0-9A-Z]{16}' "$f" 2>/dev/null | sort -u)

  # (2) 40-char secret shapes with entropy markers.
  while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    has_marker=no
    if printf '%s' "$hit" | grep -qE '[A-Z]' && printf '%s' "$hit" | grep -qE '[a-z]'; then
      has_marker=yes
    elif printf '%s' "$hit" | grep -qE '[/+]'; then
      has_marker=yes
    fi
    [ "$has_marker" = yes ] || continue
    is_allowlisted "$hit" && continue
    is_benign_fixture_hit "$base" "$hit" && continue
    FAIL=$((FAIL + 1)); leaked=1
    printf 'FAIL  live-key-shaped token in %s: %s\n' "$base" "$hit"
  done < <(grep -oE '[A-Za-z0-9/+]{40}' "$f" 2>/dev/null | sort -u)
done

if [ "$leaked" -eq 0 ]; then
  PASS=$((PASS + 1))
  printf 'PASS  no live-key-shaped string outside the fabricated allowlist (%d file(s) scanned)\n' "${#FILES[@]}"
fi

printf -- '--- no-live-keys-in-fixtures: %d pass / %d fail ---\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
