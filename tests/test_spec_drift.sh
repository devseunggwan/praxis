#!/bin/bash
# tests/test_spec_drift.sh — spec-drift report coverage (issue #1005)
#
# Uses SYNTHETIC fixture specs under a temp dir — either as a throwaway
# PRAXIS_HOME or via --spec-dir. The developer's real spec store is never read
# and never written.
#
# Tested surface variants:
#   1. A passing Verify line is reported implemented
#   2. A failing Verify line is reported missing, with exit code
#   3. A failing Verify line carries the command and its output
#   4. A requirement with no Verify line is UNKNOWN, with the recommendation
#   5. Every command is printed before it runs (audit trail)
#   6. Prose backticks are NOT executed — the counter-example case
#   7. README.md and TEMPLATE.md in the spec dir are not treated as specs
#   8. A spec with no requirements does not crash
#   9. Verdicts are exhaustive and mutually exclusive (counts sum to reqs)
#  10. Exit code is 0 even when a requirement is missing (report-only)
#  11. Non-FR label prefixes (SC/NFR) are picked up
#  12. A Verify line before any requirement is ignored, not bound wrongly
#  13. The report writes nothing into the spec directory
#  14. The default store resolves through PRAXIS_HOME (no --spec-dir at all)
#  15. A caller-supplied --spec-dir overrides that resolved default
#
# Every case runs against a fixture spec dir. This file is a `Verify:` target
# for several of 0002's requirements, so running the report over the real spec
# store from here would re-enter this test through those lines.
#
# Run:  bash tests/test_spec_drift.sh
# Exit: 0 on success, 1 on at least one failure

set +e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$REPO_ROOT/skills/spec-drift/spec-drift"

if [ ! -x "$CLI" ]; then
  echo "FAIL: spec-drift not found or not executable: $CLI" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

ok() { echo "PASS  [$1]"; PASS=$((PASS + 1)); }
ng() {
  echo "FAIL  [$1] $2"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("$1")
}

check_has() {
  local name="$1" haystack="$2" needle="$3"
  if printf '%s' "$haystack" | grep -Fq -- "$needle"; then
    ok "$name"
  else
    ng "$name" "expected to find: $needle"
  fi
}

check_lacks() {
  local name="$1" haystack="$2" needle="$3"
  if printf '%s' "$haystack" | grep -Fq -- "$needle"; then
    ng "$name" "expected NOT to find: $needle"
  else
    ok "$name"
  fi
}

FIX=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$FIX"' EXIT

# --------------------------------------------------------------------------- #
# Fixture 1 — one of each verdict, plus the prose-backtick counter-example.
#
# FR-003's prose quotes `false` the way 001's FR-001 quotes `git check-ignore`:
# as a command the requirement exists to REJECT. If the parser ever widened to
# prose backticks it would run it, and the verdict for FR-003 would flip from
# UNKNOWN to missing — which is what case 6 detects.
# --------------------------------------------------------------------------- #
cat > "$FIX/0001-mixed.md" <<'EOF'
# Feature Specification: Mixed fixture

**Issue**: #1005

### Functional Requirements

- **FR-001**: Something that holds.
  - Verify: `true`
- **FR-002**: Something that does not hold.
  - Verify: `sh -c 'echo BROKEN_MARKER >&2; exit 3'`
- **FR-003**: Something with no eligible command. `false` is explicitly not
  the oracle here.

### Measurable Outcomes

- **SC-001**: A non-FR label is picked up too.
  - Verify: `true`
EOF

OUT1=$("$CLI" --spec-dir "$FIX" 2>&1)
RC1=$?

check_has "1 passing Verify -> implemented" "$OUT1" "implemented  FR-001"
check_has "2 failing Verify -> missing with exit code" "$OUT1" "missing      FR-002  (exit 3)"
check_has "3 missing carries its output" "$OUT1" "BROKEN_MARKER"
check_has "3b missing carries its command" "$OUT1" "running      FR-002: sh -c 'echo BROKEN_MARKER >&2; exit 3'"
check_has "4 no Verify line -> UNKNOWN" "$OUT1" "UNKNOWN      FR-003"
check_has "4b UNKNOWN carries the recommendation" "$OUT1" "add a \`Verify:\` line"
check_has "5 commands printed before running" "$OUT1" "running      FR-001: true"
check_lacks "6 prose backtick NOT executed" "$OUT1" "running      FR-003"
check_has "6b prose-backtick requirement stays UNKNOWN" "$OUT1" "UNKNOWN      FR-003"
check_has "11 non-FR label (SC) picked up" "$OUT1" "implemented  SC-001"
check_has "9 verdict counts sum to the 4 requirements" "$OUT1" \
  "1 spec(s): 2 implemented, 1 missing, 1 UNKNOWN"

if [ "$RC1" -eq 0 ]; then
  ok "10 exit 0 despite a missing requirement (report-only)"
else
  ng "10 exit 0 despite a missing requirement (report-only)" "rc=$RC1"
fi

# --------------------------------------------------------------------------- #
# Fixture 2 — non-spec files in the spec dir, and a spec with no requirements.
# --------------------------------------------------------------------------- #
FIX2=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
cat > "$FIX2/README.md" <<'EOF'
- **FR-999**: README must never be scanned as a spec.
  - Verify: `sh -c 'echo README_WAS_SCANNED; exit 1'`
EOF
cat > "$FIX2/TEMPLATE.md" <<'EOF'
- **FR-998**: TEMPLATE must never be scanned as a spec.
  - Verify: `sh -c 'echo TEMPLATE_WAS_SCANNED; exit 1'`
EOF
cat > "$FIX2/0002-empty.md" <<'EOF'
# Feature Specification: No requirements

**Issue**: #1005

Prose only.
EOF

OUT2=$("$CLI" --spec-dir "$FIX2" 2>&1)
RC2=$?

check_lacks "7 README.md not scanned" "$OUT2" "README_WAS_SCANNED"
check_lacks "7b TEMPLATE.md not scanned" "$OUT2" "TEMPLATE_WAS_SCANNED"
check_has "8 spec with no requirements does not crash" "$OUT2" "(no requirements found)"
if [ "$RC2" -eq 0 ]; then
  ok "8b no-requirements spec exits 0"
else
  ng "8b no-requirements spec exits 0" "rc=$RC2"
fi
rm -rf "$FIX2"

# --------------------------------------------------------------------------- #
# Fixture 3 — a Verify line that precedes every requirement has nothing to
# bind to. Binding it to the *next* requirement would silently verify the
# wrong thing, which is worse than ignoring it.
# --------------------------------------------------------------------------- #
FIX3=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
cat > "$FIX3/0003-orphan.md" <<'EOF'
# Feature Specification: Orphan verify

**Issue**: #1005

Some prose with a stray nested item:

  - Verify: `sh -c 'echo ORPHAN_RAN; exit 1'`

### Functional Requirements

- **FR-001**: This must stay UNKNOWN, not inherit the orphan above.
EOF

OUT3=$("$CLI" --spec-dir "$FIX3" 2>&1)
check_lacks "12 orphan Verify line is not executed" "$OUT3" "ORPHAN_RAN"
check_has "12b following requirement stays UNKNOWN" "$OUT3" "UNKNOWN      FR-001"
rm -rf "$FIX3"

# --------------------------------------------------------------------------- #
# Fixture 4 — the report must not write into the spec directory it reads.
#
# Deliberately a fixture, not the real spec store. This file is itself a
# `Verify:` target for several of 0002's requirements, so invoking the report
# over the store from here would re-enter this test through those
# lines — unbounded recursion that each level's timeout only slows down.
# "The repo's own specs report 0 missing" is a claim about the tree, not about
# this program, and it belongs in CI and the PR's verification anchor.
# --------------------------------------------------------------------------- #
FIX4=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
cat > "$FIX4/0004-readonly.md" <<'EOF'
# Feature Specification: Read-only check

**Issue**: #1005

### Functional Requirements

- **FR-001**: The report must not modify what it reads.
  - Verify: `true`
EOF

BEFORE=$(find "$FIX4" -type f | sort | xargs shasum 2>/dev/null)
BEFORE_LIST=$(find "$FIX4" | sort)
"$CLI" --spec-dir "$FIX4" >/dev/null 2>&1
AFTER=$(find "$FIX4" -type f | sort | xargs shasum 2>/dev/null)
AFTER_LIST=$(find "$FIX4" | sort)

if [ "$BEFORE" = "$AFTER" ] && [ "$BEFORE_LIST" = "$AFTER_LIST" ]; then
  ok "13 report writes nothing into the spec directory"
else
  ng "13 report writes nothing into the spec directory" "spec dir changed"
fi
rm -rf "$FIX4"

# --------------------------------------------------------------------------- #
# Fixture 5 — the store's location. Case 14 passes no --spec-dir at all, so the
# only thing that can find the spec is the shell entry's praxis_specs_dir()
# resolution; case 15 pins the precedence the entry point relies on, since it
# passes the resolved default ahead of the caller's arguments.
#
# PRAXIS_HOME is set rather than unset: the real store may hold specs whose
# Verify lines run this file, and a test that reads it would recurse.
# --------------------------------------------------------------------------- #
FIX5=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
mkdir -p "$FIX5/home/docs/specs" "$FIX5/elsewhere"
cat > "$FIX5/home/docs/specs/0005-from-praxis-home.md" <<'EOF'
# Feature Specification: Resolved through PRAXIS_HOME

**Issue**: #1005

### Functional Requirements

- **FR-001**: Found without any --spec-dir argument.
  - Verify: `sh -c 'echo HOME_STORE_MARKER; exit 0'`
EOF
cat > "$FIX5/elsewhere/0006-override.md" <<'EOF'
# Feature Specification: Caller override

**Issue**: #1005

### Functional Requirements

- **FR-001**: Found only when the caller overrides the resolved default.
  - Verify: `sh -c 'echo OVERRIDE_MARKER; exit 0'`
EOF

OUT5=$(PRAXIS_HOME="$FIX5/home" "$CLI" 2>&1)
check_has "14 default store resolves through PRAXIS_HOME" "$OUT5" "HOME_STORE_MARKER"
check_has "14b resolved store path is reported" "$OUT5" "0005-from-praxis-home.md"

OUT6=$(PRAXIS_HOME="$FIX5/home" "$CLI" --spec-dir "$FIX5/elsewhere" 2>&1)
check_has "15 caller --spec-dir wins over the resolved default" "$OUT6" "OVERRIDE_MARKER"
check_lacks "15b resolved default is not also scanned" "$OUT6" "HOME_STORE_MARKER"
rm -rf "$FIX5"

echo ""
echo "Passed: $PASS  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed tests:"
  for t in "${FAILED_NAMES[@]}"; do
    echo "  - $t"
  done
  exit 1
fi
exit 0
