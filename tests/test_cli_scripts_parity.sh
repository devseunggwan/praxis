#!/usr/bin/env bash
# test_cli_scripts_parity.sh — verify install.sh and verify-symlinks.sh share
# a single CLI-script source of truth (scripts/cli-scripts.sh).
#
# Guards the drift class fixed in issue #580: the two scripts used to hold
# separate inline CLI_SCRIPTS arrays, and verify-symlinks.sh silently omitted
# bypass-review. This test fails if either consumer reintroduces an inline
# array or stops sourcing the canonical list, or if bypass-review falls out.
#
# Usage: bash tests/test_cli_scripts_parity.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL="$ROOT_DIR/scripts/install.sh"
VERIFY="$ROOT_DIR/scripts/verify-symlinks.sh"
CANONICAL="$ROOT_DIR/scripts/cli-scripts.sh"

PASS=0
FAIL=0
FAILED_NAMES=()

run_case() {
  local name="$1" result="$2" expected="$3"
  if [ "$result" = "$expected" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected got=$result"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

echo "test_cli_scripts_parity"

# ---------------------------------------------------------------------------
# 1. Canonical list exists, is non-empty, and contains bypass-review.
# ---------------------------------------------------------------------------
run_case "canonical_exists" "$([ -f "$CANONICAL" ] && echo yes || echo no)" "yes"

# Source the canonical list in a clean subshell and inspect it.
CANON_COUNT="$(bash -c "source '$CANONICAL'; echo \${#CLI_SCRIPTS[@]}" 2>/dev/null)"
run_case "canonical_nonempty" "$([ "${CANON_COUNT:-0}" -gt 0 ] && echo yes || echo no)" "yes"

CANON_HAS_BYPASS="$(bash -c "source '$CANONICAL'; printf '%s\n' \"\${CLI_SCRIPTS[@]}\"" 2>/dev/null | grep -c 'skills/bypass-review/bypass-review')"
run_case "canonical_has_bypass_review" "$CANON_HAS_BYPASS" "1"

# ---------------------------------------------------------------------------
# 2. Neither consumer declares an inline CLI_SCRIPTS array (single SoT).
# ---------------------------------------------------------------------------
INSTALL_INLINE="$(grep -c 'CLI_SCRIPTS=(' "$INSTALL")"
VERIFY_INLINE="$(grep -c 'CLI_SCRIPTS=(' "$VERIFY")"
run_case "install_no_inline_array" "$INSTALL_INLINE" "0"
run_case "verify_no_inline_array"  "$VERIFY_INLINE"  "0"

# ---------------------------------------------------------------------------
# 3. Both consumers source the canonical list.
# ---------------------------------------------------------------------------
INSTALL_SOURCES="$(grep -c 'cli-scripts.sh' "$INSTALL")"
VERIFY_SOURCES="$(grep -c 'cli-scripts.sh' "$VERIFY")"
run_case "install_sources_canonical" "$([ "$INSTALL_SOURCES" -ge 1 ] && echo yes || echo no)" "yes"
run_case "verify_sources_canonical"  "$([ "$VERIFY_SOURCES" -ge 1 ] && echo yes || echo no)" "yes"

# ---------------------------------------------------------------------------
# 4. Functional parity: install into a temp bin dir, then verify reports OK
#    for bypass-review (the previously-omitted entry), both exit 0.
# ---------------------------------------------------------------------------
TMP_BIN="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$TMP_BIN"' EXIT
INSTALL_OUT="$(PRAXIS_BIN_DIR="$TMP_BIN" bash "$INSTALL" 2>&1)"
INSTALL_RC=$?
VERIFY_OUT="$(PRAXIS_BIN_DIR="$TMP_BIN" bash "$VERIFY" 2>&1)"
VERIFY_RC=$?

run_case "install_exit_0" "$INSTALL_RC" "0"
run_case "verify_exit_0"  "$VERIFY_RC"  "0"

# bypass-review must appear as an OK line in the verify output (it would have
# been silently absent before the single-source fix).
VERIFY_COVERS_BYPASS="$(printf '%s\n' "$VERIFY_OUT" | grep -cE '^OK +bypass-review$')"
run_case "verify_covers_bypass_review" "$VERIFY_COVERS_BYPASS" "1"

rm -rf "$TMP_BIN"

# ---------------------------------------------------------------------------
# 5. Symlink-resolution-loop parity (issue #1174, PR #1191 review).
#    Five CLIs carry a copy of the readlink resolution block (REAL_PATH="$0"
#    ... SCRIPT_DIR=...); cmux-recover-sessions is the canonical copy. The
#    copies must stay byte-identical — a fix landing in one but not the
#    others silently reintroduces the class this block exists to kill.
# ---------------------------------------------------------------------------

RESOLVER_CANONICAL="$ROOT_DIR/skills/cmux-recover-sessions/cmux-recover-sessions"
RESOLVER_CARRIERS=(
  "skills/cmux-session-manager/cmux-session-status"
  "skills/cmux-session-manager/cmux-session-cleanup"
  "skills/cmux-save-sessions/cmux-save-sessions"
  "skills/recover-sessions/claude-recover"
)

extract_resolver_block() {
  awk '/^REAL_PATH="\$0"$/{f=1} f{print} f && /^SCRIPT_DIR=/{exit}' "$1"
}

CANON_BLOCK="$(extract_resolver_block "$RESOLVER_CANONICAL")"
run_case "resolver_canonical_block_present" \
  "$([ -n "$CANON_BLOCK" ] && echo yes || echo no)" "yes"

# The cap is what keeps a symlink cycle from spinning the loop forever —
# assert it structurally so a future copy-edit cannot drop it.
printf '%s\n' "$CANON_BLOCK" | grep -q 'HOPS.*-gt 40'
run_case "resolver_canonical_has_hop_cap" "$?" "0"

for rel in "${RESOLVER_CARRIERS[@]}"; do
  name="$(basename "$rel")"
  CARRIER_BLOCK="$(extract_resolver_block "$ROOT_DIR/$rel")"
  run_case "resolver_block_present:$name" \
    "$([ -n "$CARRIER_BLOCK" ] && echo yes || echo no)" "yes"
  run_case "resolver_block_matches_canonical:$name" \
    "$([ "$CARRIER_BLOCK" = "$CANON_BLOCK" ] && echo yes || echo no)" "yes"
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "ALL $PASS PASSED"
  exit 0
else
  echo "$FAIL FAILED / $PASS PASSED — ${FAILED_NAMES[*]}"
  exit 1
fi
