#!/usr/bin/env bash
# test_cli_scripts_behavior.sh — behavioral coverage for install.sh and
# verify-symlinks.sh (issue #647 F6).
#
# The sibling test_cli_scripts_parity.sh guards the shared CLI_SCRIPTS list
# and runs a happy-path smoke; this suite exercises the *detection and
# conflict paths* that smoke never reaches: MISSING / NOT-A-LINK / DANGLING /
# DRIFT / NOT-EXEC on the verify side, and REFUSE / --force BACKUP+REBIND /
# idempotent re-run / missing-source / bad-args on the install side.
#
# Isolation: the real scripts are copied into a throwaway mini-clone whose
# cli-scripts.sh tracks a single fixture CLI. This keeps every path (source
# file, bin dir, link target) inside mktemp space — the user's ~/.local/bin
# and this repo's permission bits are never touched.
#
# Usage: bash tests/test_cli_scripts_behavior.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

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

echo "test_cli_scripts_behavior"

# ---------------------------------------------------------------------------
# Fixture: mini-clone with one tracked CLI ("foo"), fresh bin dir per stanza.
# ---------------------------------------------------------------------------

TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT

CLONE="$TMP_ROOT/clone"
mkdir -p "$CLONE/scripts" "$CLONE/skills/foo"
cp "$ROOT_DIR/scripts/install.sh" "$CLONE/scripts/install.sh"
cp "$ROOT_DIR/scripts/verify-symlinks.sh" "$CLONE/scripts/verify-symlinks.sh"
cat > "$CLONE/scripts/cli-scripts.sh" <<'EOF'
# shellcheck disable=SC2034
CLI_SCRIPTS=(
  "skills/foo/foo"
)
EOF
printf '#!/bin/sh\necho foo\n' > "$CLONE/skills/foo/foo"
chmod +x "$CLONE/skills/foo/foo"

fresh_bin() {
  BIN=$(mktemp -d "$TMP_ROOT/bin.XXXXXX")
}

install_sh() { PRAXIS_BIN_DIR="$BIN" bash "$CLONE/scripts/install.sh" "$@" 2>&1; }
verify_sh()  { PRAXIS_BIN_DIR="$BIN" bash "$CLONE/scripts/verify-symlinks.sh" 2>&1; }

# ---------------------------------------------------------------------------
# install.sh — fresh install, idempotency
# ---------------------------------------------------------------------------

fresh_bin
out=$(install_sh); rc=$?
run_case "install: fresh run exits 0" "$rc" "0"
echo "$out" | grep -q "^LINK     foo" ; run_case "install: fresh run reports LINK" "$?" "0"
run_case "install: symlink created" "$([ -L "$BIN/foo" ] && echo yes || echo no)" "yes"

out=$(install_sh); rc=$?
run_case "install: re-run exits 0 (idempotent)" "$rc" "0"
echo "$out" | grep -q "^OK       foo"; run_case "install: re-run reports OK" "$?" "0"
echo "$out" | grep -q "already=1"; run_case "install: re-run counts already=1" "$?" "0"

out=$(verify_sh); rc=$?
run_case "verify: clean install passes" "$rc" "0"
echo "$out" | grep -q "^OK         foo"; run_case "verify: clean install reports OK" "$?" "0"

# ---------------------------------------------------------------------------
# install.sh — conflict refuse (default) vs --force backup + overwrite
# ---------------------------------------------------------------------------

fresh_bin
echo "precious user binary" > "$BIN/foo"
out=$(install_sh); rc=$?
run_case "install: regular-file conflict exits 1" "$rc" "1"
echo "$out" | grep -q "^REFUSE   foo"; run_case "install: conflict reports REFUSE" "$?" "0"
run_case "install: refused file left untouched" \
  "$([ "$(cat "$BIN/foo")" = "precious user binary" ] && echo yes || echo no)" "yes"

out=$(install_sh --force); rc=$?
run_case "install: --force on regular file exits 0" "$rc" "0"
echo "$out" | grep -q "^BACKUP   foo -> "; run_case "install: --force reports BACKUP" "$?" "0"
run_case "install: --force replaced file with symlink" \
  "$([ -L "$BIN/foo" ] && echo yes || echo no)" "yes"
bak=$(find "$BIN" -name 'foo.bak.*' | head -1)
run_case "install: .bak preserves original content" \
  "$([ -n "$bak" ] && [ "$(cat "$bak")" = "precious user binary" ] && echo yes || echo no)" "yes"

# ---------------------------------------------------------------------------
# install.sh — --force REBIND of a symlink pointing at another clone
# ---------------------------------------------------------------------------

fresh_bin
OTHER="$TMP_ROOT/other-clone-foo"
printf '#!/bin/sh\necho other\n' > "$OTHER"; chmod +x "$OTHER"
ln -s "$OTHER" "$BIN/foo"
out=$(install_sh --force); rc=$?
run_case "install: --force rebind exits 0" "$rc" "0"
echo "$out" | grep -q "^REBIND   foo: $OTHER -> "; run_case "install: rebind logs old target" "$?" "0"
bak=$(find "$BIN" -name 'foo.bak.*' | head -1)
run_case "install: rebind backup preserves the link itself" \
  "$([ -L "$bak" ] && [ "$(readlink "$bak")" = "$OTHER" ] && echo yes || echo no)" "yes"
run_case "install: rebind points dst at this clone" \
  "$([ "$(readlink "$BIN/foo")" = "$CLONE/skills/foo/foo" ] && echo yes || echo no)" "yes"

# ---------------------------------------------------------------------------
# install.sh — missing source, bad arguments
# ---------------------------------------------------------------------------

fresh_bin
mv "$CLONE/skills/foo/foo" "$CLONE/skills/foo/foo.hidden"
out=$(install_sh); rc=$?
run_case "install: missing source exits 1" "$rc" "1"
echo "$out" | grep -q "^MISSING  foo"; run_case "install: missing source reports MISSING" "$?" "0"
mv "$CLONE/skills/foo/foo.hidden" "$CLONE/skills/foo/foo"

install_sh --no-such-flag >/dev/null 2>&1
run_case "install: unknown option exits 2" "$?" "2"

# ---------------------------------------------------------------------------
# verify-symlinks.sh — each drift class
# ---------------------------------------------------------------------------

fresh_bin
out=$(verify_sh); rc=$?
run_case "verify: missing link exits 1" "$rc" "1"
echo "$out" | grep -q "^MISSING    foo"; run_case "verify: reports MISSING" "$?" "0"

fresh_bin
echo "not a link" > "$BIN/foo"
out=$(verify_sh); rc=$?
run_case "verify: regular file exits 1" "$rc" "1"
echo "$out" | grep -q "^NOT-A-LINK foo"; run_case "verify: reports NOT-A-LINK" "$?" "0"

fresh_bin
ln -s "$TMP_ROOT/does-not-exist" "$BIN/foo"
out=$(verify_sh); rc=$?
run_case "verify: dangling link exits 1" "$rc" "1"
echo "$out" | grep -q "^DANGLING   foo"; run_case "verify: reports DANGLING" "$?" "0"

fresh_bin
DRIFT_TARGET="$TMP_ROOT/drift-target-foo"   # own fixture — independent of the REBIND stanza
printf '#!/bin/sh\necho drift\n' > "$DRIFT_TARGET"; chmod +x "$DRIFT_TARGET"
# Precondition: DRIFT is only meaningful if the target canonicalizes to a
# different file than the tracked source — guard explicitly so a fixture
# refactor that moves it under the clone can't silently turn DRIFT into OK.
# Canonicalize via python3 like the scripts themselves (BSD/GNU realpath differ).
canon() { /usr/bin/env python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1"; }
run_case "verify: drift fixture resolves outside the clone source" \
  "$([ "$(canon "$DRIFT_TARGET")" != "$(canon "$CLONE/skills/foo/foo")" ] && echo yes || echo no)" "yes"
ln -s "$DRIFT_TARGET" "$BIN/foo"   # resolves fine, but to a different file
out=$(verify_sh); rc=$?
run_case "verify: foreign target exits 1" "$rc" "1"
echo "$out" | grep -q "^DRIFT      foo"; run_case "verify: reports DRIFT" "$?" "0"

fresh_bin
ln -s "$CLONE/skills/foo/foo" "$BIN/foo"
chmod -x "$CLONE/skills/foo/foo"
out=$(verify_sh); rc=$?
run_case "verify: non-executable target exits 1" "$rc" "1"
echo "$out" | grep -q "^NOT-EXEC   foo"; run_case "verify: reports NOT-EXEC" "$?" "0"
chmod +x "$CLONE/skills/foo/foo"

fresh_bin
rm -rf "$CLONE/skills/foo"
out=$(verify_sh); rc=$?
run_case "verify: absent source exits 1" "$rc" "1"
echo "$out" | grep -q "^NO-SOURCE  foo"; run_case "verify: reports NO-SOURCE" "$?" "0"

# ---------------------------------------------------------------------------

echo ""
echo "=== summary ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
