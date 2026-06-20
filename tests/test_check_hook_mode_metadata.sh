#!/usr/bin/env bash
# test_check_hook_mode_metadata.sh - verify the hook `mode` metadata drift gate
# (#688) in scripts/check-plugin-manifests.py fires in BOTH directions and
# restores clean.
#
#   (a) manifest `mode` value absent from the doc table  -> MODE DOC MISSING
#   (b) doc-table value absent from the manifest `mode`   -> MODE MANIFEST MISSING
#
# The manifest is mutated in a temp copy and restored via trap so a failing
# assertion never leaves the working tree dirty.

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
MANIFEST="$ROOT_DIR/hooks/manifest.json"

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

echo "test_check_hook_mode_metadata"

SECURITY="$ROOT_DIR/SECURITY.md"

BACKUP="$(mktemp)"
SEC_BACKUP="$(mktemp)"
cp "$MANIFEST" "$BACKUP"
cp "$SECURITY" "$SEC_BACKUP"
restore() { cp "$BACKUP" "$MANIFEST"; cp "$SEC_BACKUP" "$SECURITY"; }
trap 'restore; rm -f "$BACKUP" "$SEC_BACKUP"' EXIT

# Baseline must be clean.
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline_check_clean" "$?" "0"

# (a) Inject a manifest-only strict_env not present in docs/bypass-vars.md.
python3 - "$MANIFEST" <<'PY'
import json, sys
p = sys.argv[1]
m = json.load(open(p))
for e in m["hooks"]:
    if e.get("name") == "branch-name-check" and "mode" in e:
        e["mode"]["strict_env"] = "PRAXIS_BOGUS_STRICT_NOT_IN_DOCS"
json.dump(m, open(p, "w"), indent=2, ensure_ascii=False)
open(p, "a").write("\n")
PY
OUT="$(python3 "$CHECK" 2>&1)"
run_case "manifest_only_nonzero" "$?" "1"
echo "$OUT" | grep -q "MODE DOC MISSING branch-name-check"
run_case "manifest_only_message" "$?" "0"
restore

# (b) Remove a strict_env from the manifest that docs still document.
python3 - "$MANIFEST" <<'PY'
import json, sys
p = sys.argv[1]
m = json.load(open(p))
for e in m["hooks"]:
    if e.get("name") == "destructive-bash-guard" and "mode" in e:
        e["mode"].pop("strict_env", None)
json.dump(m, open(p, "w"), indent=2, ensure_ascii=False)
open(p, "a").write("\n")
PY
OUT="$(python3 "$CHECK" 2>&1)"
run_case "doc_orphan_nonzero" "$?" "1"
echo "$OUT" | grep -q "MODE MANIFEST MISSING destructive-bash-guard"
run_case "doc_orphan_message" "$?" "0"
restore

# (c) [P2-1] A doc row naming a hook absent from the manifest (typo / stale
# row) is an ORPHAN — the doc->manifest direction must FAIL on it, not silently
# `continue`. parse_doc_external_commands derives the name from the SECURITY.md
# `hooks/<role>/<name>/impl.py` path without pre-filtering, so a misspelled path
# is the realistic orphan source. Inject one and assert MODE DOC ORPHAN fires.
printf '| `hooks/advisory-nudge/bogus-typo-hook/impl.py` | `git status` |\n' >> "$SECURITY"
OUT="$(python3 "$CHECK" 2>&1)"
run_case "doc_orphan_typo_nonzero" "$?" "1"
echo "$OUT" | grep -q "MODE DOC ORPHAN bogus-typo-hook"
run_case "doc_orphan_typo_message" "$?" "0"
restore

# (d) [P2-2] external-write-path-existence-check declares the PRAXIS_STATE_DIR
# state path; the generated operating matrix must reflect it, not '-'.
MATRIX="$ROOT_DIR/docs/hook-operating-matrix.md"
grep -E "external-write-path-existence-check.*PRAXIS_STATE_DIR" "$MATRIX" >/dev/null 2>&1
run_case "p2_state_path_in_matrix" "$?" "0"

# Restoring the manifest returns the check to clean.
python3 "$CHECK" >/dev/null 2>&1
run_case "restore_check_clean" "$?" "0"

restore
trap - EXIT
rm -f "$BACKUP"

echo "---"
echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -ne 0 ]; then
  printf 'FAILED: %s\n' "${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
