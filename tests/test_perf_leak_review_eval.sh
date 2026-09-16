#!/usr/bin/env bash
# test_perf_leak_review_eval.sh — scorer invariants for the perf-leak-review
# eval harness (#1429).
#
# The live recall number is produced by an LLM and cannot be a CI gate. The
# scorer that turns a reviewer envelope into a verdict is deterministic, and it
# is the part a wrong result would be blamed on — so every judgement it makes
# gets a case here, in both polarities. Case (b) is the AC-1 positive control:
# without it "the tree was unmodified" and "the oracle cannot see a
# modification" produce the same PASS line.
#
# Usage: bash tests/test_perf_leak_review_eval.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EVAL="$ROOT_DIR/scripts/perf-leak-review-eval.sh"
FIXTURES="$ROOT_DIR/tests/fixtures/perf-leak-review"

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

WORK="$(mktemp -d)" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
cleanup() {
  # Every prepared tree is write-protected; restore before removing so a failed
  # case cannot leave an undeletable directory behind.
  chmod -R u+w "$WORK" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

# prepared_path_of <results-dir>
prepared_path_of() {
  python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["prepared_path"])' \
    "$1/prepared.json"
}

# write_envelope <results-dir> <repo_root> [<class> <relative-file>]
write_envelope() {
  local dir="$1" root="$2" klass="${3:-}" file="${4:-}"
  if [ -z "$klass" ]; then
    printf '{"repo_root": "%s", "findings": []}\n' "$root" > "$dir/envelope.json"
    return
  fi
  cat > "$dir/envelope.json" <<JSON
{
  "repo_root": "$root",
  "findings": [
    {
      "class": "$klass",
      "file": "$file",
      "line": 1,
      "evidence": "quoted line",
      "confidence": "med",
      "grade": "candidate"
    }
  ]
}
JSON
}

echo "test_perf_leak_review_eval"

if [ ! -x "$EVAL" ]; then
  echo "FAIL  [eval_executable] expected=yes got=no"
  exit 1
fi

# ---------------------------------------------------------------- (a) baseline
dir_a="$WORK/a"
"$EVAL" prepare "$FIXTURES/clean" "$dir_a" > /dev/null 2>&1
root_a="$(prepared_path_of "$dir_a")"
write_envelope "$dir_a" "$root_a"
"$EVAL" score "$dir_a" > /dev/null 2>&1
run_case "a_clean_empty_envelope_scores_zero" "$?" "0"

# ------------------------------------------- (b) AC-1 positive control: a write
dir_b="$WORK/b"
"$EVAL" prepare "$FIXTURES/clean" "$dir_b" > /dev/null 2>&1
root_b="$(prepared_path_of "$dir_b")"
chmod -R u+w "$root_b"
: >> "$root_b/text/slug.py"
printf '\n# touched by the test\n' >> "$root_b/text/slug.py"
write_envelope "$dir_b" "$root_b"
out_b="$("$EVAL" score "$dir_b" 2>&1)"
rc_b="$?"
run_case "b_modified_tree_is_detected" "$rc_b" "1"
printf '%s' "$out_b" | grep -q "FAIL tree-unmodified"
run_case "b_modified_tree_names_the_check" "$?" "0"

# ------------------------------------------------- (c) prepared.json is missing
dir_c="$WORK/c"
mkdir -p "$dir_c"
printf '{"repo_root": "/nowhere", "findings": []}\n' > "$dir_c/envelope.json"
"$EVAL" score "$dir_c" > /dev/null 2>&1
run_case "c_missing_prepared_json_fails" "$?" "1"

# ------------------------------------ (d) write protection actually holds (EACCES)
if [ "$(id -u)" != 0 ]; then
  dir_d="$WORK/d"
  "$EVAL" prepare "$FIXTURES/clean" "$dir_d" > /dev/null 2>&1
  root_d="$(prepared_path_of "$dir_d")"
  ( printf 'x' > "$root_d/text/slug.py" ) 2> /dev/null
  run_case "d_write_protected_tree_rejects_a_write" "$?" "1"
  chmod -R u+w "$root_d"
else
  echo "SKIP  [d_write_protected_tree_rejects_a_write] running as root — a-w is ignored"
fi

# ------------------------- (e) TMPDIR inside another repo does not swallow the patch
dir_e="$WORK/e"
parent_repo="$WORK/parent"
mkdir -p "$parent_repo/tmp"
git -C "$parent_repo" init -q
TMPDIR="$parent_repo/tmp" "$EVAL" prepare "$FIXTURES/c1-n-plus-one" "$dir_e" > /dev/null 2>&1
base_e="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["base_sha"])' "$dir_e/prepared.json" 2>/dev/null)"
change_e="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["change_sha"])' "$dir_e/prepared.json" 2>/dev/null)"
if [ -n "$base_e" ] && [ -n "$change_e" ] && [ "$base_e" != "$change_e" ]; then
  run_case "e_tmpdir_inside_a_repo_still_commits_the_change" "0" "0"
else
  run_case "e_tmpdir_inside_a_repo_still_commits_the_change" "1" "0"
fi
root_e="$(prepared_path_of "$dir_e")"
chmod -R u+w "$root_e" 2>/dev/null

# ------------------------------------------ (f) the tree is gone without --keep
dir_f="$WORK/f"
"$EVAL" prepare "$FIXTURES/clean" "$dir_f" > /dev/null 2>&1
root_f="$(prepared_path_of "$dir_f")"
write_envelope "$dir_f" "$root_f"
"$EVAL" score "$dir_f" > /dev/null 2>&1
# `[ -d ]` is a condition, so its status is captured before anything else runs.
if [ -d "$root_f" ]; then still_there=0; else still_there=1; fi
run_case "f_prepared_tree_is_removed_after_scoring" "$still_there" "1"

# --------------------------------- (g) a symlink alias resolves to the same tree
dir_g="$WORK/g"
"$EVAL" prepare "$FIXTURES/clean" "$dir_g" > /dev/null 2>&1
root_g="$(prepared_path_of "$dir_g")"
alias_g="$WORK/alias-g"
ln -s "$root_g" "$alias_g"
write_envelope "$dir_g" "$alias_g"
"$EVAL" score "$dir_g" > /dev/null 2>&1
run_case "g_symlink_alias_is_accepted_as_the_same_tree" "$?" "0"

# ------------------------------- (h) a different REAL directory is not accepted
dir_h="$WORK/h"
other_h="$WORK/other-h"
mkdir -p "$other_h"
"$EVAL" prepare "$FIXTURES/clean" "$dir_h" > /dev/null 2>&1
root_h="$(prepared_path_of "$dir_h")"
write_envelope "$dir_h" "$other_h"
out_h="$("$EVAL" score "$dir_h" 2>&1)"
run_case "h_a_different_existing_dir_is_rejected" "$?" "1"
printf '%s' "$out_h" | grep -q "is not the prepared tree"
run_case "h_rejection_names_the_repo_root_check" "$?" "0"
chmod -R u+w "$root_h" 2>/dev/null

# ------------------------------------------- (i) a finding on a clean fixture
dir_i="$WORK/i"
"$EVAL" prepare "$FIXTURES/clean" "$dir_i" > /dev/null 2>&1
root_i="$(prepared_path_of "$dir_i")"
write_envelope "$dir_i" "$root_i" "C3" "text/slug.py"
out_i="$("$EVAL" score "$dir_i" 2>&1)"
run_case "i_finding_on_a_clean_fixture_fails" "$?" "1"
printf '%s' "$out_i" | grep -q "expected none"
run_case "i_clean_failure_names_the_control" "$?" "0"

# ------------------------------------- (j) an empty envelope on a planted fixture
dir_j="$WORK/j"
"$EVAL" prepare "$FIXTURES/c1-n-plus-one" "$dir_j" > /dev/null 2>&1
root_j="$(prepared_path_of "$dir_j")"
write_envelope "$dir_j" "$root_j"
"$EVAL" score "$dir_j" > /dev/null 2>&1
run_case "j_missed_planted_defect_fails" "$?" "1"

# ------------------------------------------ (k) the expected class is recognised
dir_k="$WORK/k"
"$EVAL" prepare "$FIXTURES/c1-n-plus-one" "$dir_k" > /dev/null 2>&1
root_k="$(prepared_path_of "$dir_k")"
write_envelope "$dir_k" "$root_k" "C1" "billing/summary.py"
out_k="$("$EVAL" score "$dir_k" 2>&1)"
run_case "k_expected_class_scores_zero" "$?" "0"
printf '%s' "$out_k" | grep -q "^precision: "
run_case "k_precision_row_is_printed" "$?" "0"

# ------------------------------- (k2) a finding whose file does not exist fails
dir_k2="$WORK/k2"
"$EVAL" prepare "$FIXTURES/c1-n-plus-one" "$dir_k2" > /dev/null 2>&1
root_k2="$(prepared_path_of "$dir_k2")"
write_envelope "$dir_k2" "$root_k2" "C1" "billing/does-not-exist.py"
out_k2="$("$EVAL" score "$dir_k2" 2>&1)"
run_case "k2_nonexistent_finding_path_fails" "$?" "1"
printf '%s' "$out_k2" | grep -q "does not exist under"
run_case "k2_path_failure_names_the_check" "$?" "0"

echo
echo "Passed: $PASS"
echo "Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf 'Failed cases: %s\n' "${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
