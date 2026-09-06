#!/usr/bin/env bash
# Single entry point for the full test suite.
#
# Runs:
#   1. pytest   — Python unit tests under tests/, under coverage.py when the
#                 module is importable (floor in .coveragerc, issue #1303)
#   2. shell    — shell tests at tests/hooks/*/test_*.sh and tests/test_*.sh
#   3. manifest — scripts/check-plugin-manifests.py
#   4. invariants — scripts/check-hook-token-invariants.py
#   5. sibling-gates — scripts/check-sibling-commit-gates.py
#   6. memory-frontmatter — scripts/check-memory-frontmatter.py
#   7. omc-name-drift — scripts/check-omc-name-drift.py
#   8. workflow-pins — scripts/check-workflow-pins.py
#   9. skill-arg-substitution — scripts/check-skill-arg-substitution.py
#  10. ruff     — static Python lint (mirrors the ci.yml `ruff` job)
#  11. shellcheck — static shell lint (mirrors the ci.yml `shellcheck` job)
#  12. markdownlint — advisory markdown lint (mirrors the ci.yml `markdownlint` job)
#  13. mypy     — static Python type check (mirrors the ci.yml `mypy` job)
#
# Steps 10-13 mirror CI jobs that used to have no local equivalent, so a change
# could pass here and still be flagged on the PR (issue #866). Each skips with
# an explicit SKIPPED line when its tool is absent, so a contributor without
# the toolchain is not blocked — CI remains authoritative either way. Step 1's
# coverage half follows the same protocol: without the `coverage` module the
# tests still run, plain, and the missing floor is announced as a SKIPPED line
# rather than passed over (issue #1303).
#
# Step 6 is a different repo-internal script, same family as steps 3-5 (no
# external toolchain — nothing to install), but its skip condition is not
# like steps 10-13's: the memory directory it lints is a local, gitignored,
# per-user store that is structurally absent in CI or a fresh checkout,
# always, forever (issue #942) — not a "toolchain not installed" gap a
# contributor can close. It prints "N/A", never "SKIPPED", to keep that
# distinction visible in the log (see its own docstring / #917 below), and
# this call strips PRAXIS_TESTS_STRICT (`env -u`, not passed through like
# steps 10-13) so that permanent N/A can never fail the job. Real drift
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
#
# `--doctor` runs nothing: it prints one table of every external tool the
# steps and sub-suites above need — found/missing, version, and the install
# hint the matching SKIPPED line would print — and exits 0 (issue #1302). Use
# it to see which SKIPPED lines a full run will emit before paying for the
# run. Without the flag the runner behaves exactly as before.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# --doctor: toolchain report (issue #1302)
#
# One table, every external tool the runner or one of its sub-suites needs,
# with found/missing, the version when found, and the same install hint the
# corresponding SKIPPED line prints. Informational only — always exits 0 — so
# a contributor can see at a glance which SKIPPED lines a full run WILL emit
# before spending the minutes on it. It touches nothing else in this file: the
# dispatch below runs before the isolation preamble, so a flag-less run is
# unchanged.
#
# Bash 3.2 compatible on purpose (no mapfile, no associative arrays): macOS is
# one of the hosts this table is for.
# ---------------------------------------------------------------------------

# First line of a command's combined output, empty when it cannot run. `sed`
# reads to EOF so the producer never sees SIGPIPE under pipefail.
doctor_version() {
  "$@" 2>&1 | sed -n '1p' || true
}

doctor_row() {
  if [[ -n "$5" ]]; then
    printf '%-18s %-8s %-26s %-46s %s\n' "$1" "$2" "$3" "$4" "$5"
  else
    # No trailing pad when the last cell is empty.
    printf '%-18s %-8s %-26s %s\n' "$1" "$2" "$3" "$4"
  fi
}

# doctor_probe <tool> <needed-by> <install-hint> <version-command...>
# Column 5 carries the install hint only on a MISSING row, so a healthy table
# stays narrow.
doctor_probe() {
  local tool="$1" needed="$2" hint="$3" ver
  shift 3
  if command -v "$tool" >/dev/null 2>&1; then
    ver="$(doctor_version "$@")"
    doctor_row "$tool" found "${ver:-?}" "$needed" ""
  else
    doctor_row "$tool" MISSING "-" "$needed" "$hint"
  fi
}

# doctor_probe_pymod <label> <module> <needed-by> <install-hint> <version-expr>
doctor_probe_pymod() {
  local label="$1" mod="$2" needed="$3" hint="$4" expr="$5" ver
  if python3 -c "import $mod" >/dev/null 2>&1; then
    ver="$(doctor_version python3 -c "import $mod; print($expr)")"
    doctor_row "$label" found "${ver:-?}" "$needed" ""
  else
    doctor_row "$label" MISSING "-" "$needed" "$hint"
  fi
}

doctor() {
  local ver
  echo "praxis test toolchain (scripts/run-tests.sh --doctor)"
  echo ""
  doctor_row TOOL STATUS VERSION "NEEDED BY" "INSTALL"
  doctor_row ------------------ -------- -------------------------- \
    ---------------------------------------------- ----------------------------------
  doctor_probe python3 "steps 1,3-10; most shell sub-suites" \
    "apt install python3 / brew install python" python3 --version
  doctor_probe_pymod pytest pytest "step 1" "pip install pytest" \
    "'pytest ' + pytest.__version__"
  doctor_probe_pymod coverage coverage "step 1 coverage floor" \
    "pip install 'coverage==7.16.0'" "'coverage ' + coverage.__version__"
  doctor_probe_pymod mypy mypy "step 13" \
    "pip install 'mypy==1.20.0' 'types-PyYAML==6.0.12.20260815'" \
    "'mypy ' + __import__('mypy.version').version.__version__"
  doctor_probe_pymod PyYAML yaml "step 8 workflow-pin check" "pip install PyYAML" \
    "'PyYAML ' + yaml.__version__"
  doctor_probe git "step 12 diff base; sub-suite fixture repos" \
    "apt install git / brew install git" git --version
  doctor_probe jq "catalog, fixture-key, reaper, retrospect suites" \
    "apt install jq / brew install jq" jq --version
  doctor_probe zsh "test_block_unmatched_glob.sh" \
    "apt install zsh / brew install zsh" zsh --version
  doctor_probe tmux "test_cmux_session_*.sh sub-suites" \
    "apt install tmux / brew install tmux" tmux -V
  doctor_probe lsof "test_codex_broker_reaper.sh lsof sub-cases" \
    "apt install lsof" sh -c 'lsof -v 2>&1 | sed -n "s/^ *revision: */lsof /p"'

  # Steps 10-12 mirror the runner's own discovery exactly, module form and
  # node_modules probe included, so this report and the SKIPPED lines agree.
  if command -v ruff >/dev/null 2>&1; then
    ver="$(doctor_version ruff --version)"
    doctor_row ruff found "${ver:-?}" "step 10" ""
  elif python3 -m ruff --version >/dev/null 2>&1; then
    ver="$(doctor_version python3 -m ruff --version)"
    doctor_row ruff found "${ver:-?} (python3 -m ruff)" "step 10" ""
  else
    doctor_row ruff MISSING "-" "step 10" "pip install 'ruff==0.15.8'"
  fi

  doctor_probe shellcheck "step 11" "brew install shellcheck" \
    sh -c 'shellcheck --version 2>&1 | sed -n "s/^version: /shellcheck /p"'

  if command -v markdownlint-cli2 >/dev/null 2>&1; then
    ver="$(doctor_version markdownlint-cli2 --help)"
    doctor_row markdownlint-cli2 found "${ver:-?}" "step 12 (advisory)" ""
  elif command -v markdownlint >/dev/null 2>&1; then
    ver="$(doctor_version markdownlint --version)"
    doctor_row markdownlint-cli2 found "markdownlint ${ver:-?}" "step 12 (advisory)" ""
  elif [[ -x node_modules/.bin/markdownlint-cli2 ]]; then
    ver="$(doctor_version node_modules/.bin/markdownlint-cli2 --help)"
    doctor_row markdownlint-cli2 found "${ver:-?} (node_modules)" "step 12 (advisory)" ""
  else
    doctor_row markdownlint-cli2 MISSING "-" "step 12 (advisory)" \
      "npm i -g markdownlint-cli2@0.23.2"
  fi

  echo ""
  echo "MISSING rows become SKIPPED lines in a full run (a failure under PRAXIS_TESTS_STRICT=1)."
  echo "This report is informational and always exits 0."
}

if [[ "${1-}" == "--doctor" ]]; then
  doctor || true
  exit 0
fi

# Run against a throwaway PRAXIS_HOME (#903). Hooks now resolve their runtime
# files through it, so without this the suite would write into — and let
# prune_stale sweep — the developer's own ~/.praxis. Individual cases that care
# about the default still unset it themselves (see tests/test_paths.sh).
PRAXIS_HOME="$(mktemp -d)" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
export PRAXIS_HOME
trap 'rm -rf "$PRAXIS_HOME"' EXIT

# The fire ledger needs no export of its own any more. #849 added a suite-wide
# PRAXIS_FIRE_TELEMETRY_FILE here because resolve_path() in
# hooks/_lib/_fire_ledger.py ignored PRAXIS_HOME and defaulted to
# ~/.praxis/telemetry regardless; since #1340 the ledger honours PRAXIS_HOME
# (its default is $PRAXIS_HOME/telemetry), and since #934 a hook run out of a
# development checkout writes to <checkout>/.praxis-dev-telemetry before
# either. The throwaway PRAXIS_HOME above therefore covers the ledger too, for
# the instrumented impl.sh/impl.py files most tests/hooks/*/test_*.sh invoke
# directly (mirrors tests/conftest.py, whose per-test override remains for
# tests that assert on ledger CONTENT). A test that exercises the default
# resolution itself must still take PRAXIS_HOME out of its own environment —
# see tests/hooks/_lib/test_record_fire.sh.

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
# Coverage floor (issue #1303). When coverage.py is importable, pytest runs
# under `coverage run` and `coverage report` enforces the `fail_under` set in
# .coveragerc (exit 2 below the floor — counted as a step failure). Only the
# Python that pytest imports in-process is measured; impl.py executions driven
# from the shell suites in step 2 are subprocesses and stay uncounted until
# the follow-up half of #1303. Without the module the tests still run, plain,
# and the absent floor goes through skip_step() like steps 10-13 so strict
# mode (CI) fails on it instead of quietly measuring nothing.
#
# The data file lands under the throwaway PRAXIS_HOME unless the caller has
# already chosen a COVERAGE_FILE — ci.yml does, so the report can be re-read
# into the job summary after this script's EXIT trap has swept the temp dir.
if python3 -c 'import coverage' 2>/dev/null; then
  export COVERAGE_FILE="${COVERAGE_FILE:-$PRAXIS_HOME/.coverage}"
  if ! python3 -m coverage run -m pytest tests/ -q; then
    FAILED=1
  fi
  echo ""
  echo "=== coverage report (floor: .coveragerc fail_under) ==="
  if ! python3 -m coverage report; then
    FAILED=1
  fi
else
  if ! python3 -m pytest tests/ -q; then
    FAILED=1
  fi
  skip_step coverage "pip install 'coverage==7.16.0'"
fi

# ---------------------------------------------------------------------------
# 2. Shell tests
# ---------------------------------------------------------------------------
echo ""
echo "=== shell tests ==="
SHELL_FAILED=0

# Sub-suite skip protocol (#1170). A test file that must skip entirely because
# a tool is absent prints, to stdout,
#     PRAXIS_SUBSKIP: <tool> <file>
# and exits 0. run_sh() tees stdout so the live stream is preserved, then scans
# the capture and folds each announced tool into SKIPPED_TOOLS — the same
# accounting as the top-level steps 10-13, so PRAXIS_TESTS_STRICT=1 fails the
# run on them too. Before this, "SKIP jq unavailable; exit 0" inside a file
# was indistinguishable from a pass and strict mode never saw it.
SUBSKIP_MARKER="PRAXIS_SUBSKIP:"

run_sh() {
  local f="$1"
  local out="$PRAXIS_HOME/run-sh-capture.txt"
  local rc=0
  # pipefail (set at the top) carries the test's own exit status through tee.
  # stderr is deliberately left out of the pipe: markers are stdout-only, and
  # error output keeps streaming unbuffered.
  bash "$f" | tee "$out" || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "FAIL: $f" >&2
    SHELL_FAILED=1
    return 0
  fi
  local tool
  while IFS= read -r tool; do
    [[ -n "$tool" ]] || continue
    # Dedupe: several files skip on the same absent tool (e.g. python3).
    case " ${SKIPPED_TOOLS[*]-} " in
      *" $tool "*) ;;
      *) SKIPPED_TOOLS+=("$tool")
         echo "SKIPPED: $tool (sub-suite $f skipped itself — tool not installed)" ;;
    esac
  done < <(sed -n "s/^${SUBSKIP_MARKER} \([^ ][^ ]*\) .*/\1/p" "$out" | sort -u)
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
# steps 10-13 (a tool a contributor could install), the memory dir this checks
# is a local, gitignored, per-user store that structurally never exists in
# CI or a fresh checkout — treating its absence as a strict-mode failure
# would fail every CI run forever, not flag a fixable gap. The script prints
# "N/A", not "SKIPPED", for exactly this reason: #917 exists because a
# scrolling SKIPPED line lost its signal value once contributors stopped
# reading it, and a condition that can never be fixed would sit there as
# permanent unresolvable noise if it wore the same "SKIPPED" label as steps
# 10-13's genuinely-fixable tool-absence skips. An N/A here is always benign;
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
# 8. Workflow pinning discipline (supply-chain drift guard, issue #1171)
#
# Same family as steps 3-5 and 7: a repo-internal script with no external
# toolchain, so it never skips. It asserts every `uses:` in
# .github/workflows/ is pinned to a 40-char commit SHA and every `runs-on:`
# names a pinned image label (never `*-latest`), so an unpinned dependency
# can't slip in through a future workflow edit.
# ---------------------------------------------------------------------------
echo ""
echo "=== workflow-pin check ==="
# The canary parses workflows with PyYAML. Without it the check cannot run, and
# a crash here would read as drift; route it through the skip path instead, so
# CI (PRAXIS_TESTS_STRICT=1, PyYAML installed) still fails on a silent skip.
if ! python3 -c 'import yaml' 2>/dev/null; then
  skip_step "workflow-pin check" "pip install PyYAML"
elif ! python3 ./scripts/check-workflow-pins.py; then
  FAILED=1
fi

# ---------------------------------------------------------------------------
# 9. SKILL.md argument-substitution guard (issue #1259)
#
# Same family as steps 3-8: a repo-internal script with no external toolchain,
# so it never skips. Claude Code rewrites a SKILL.md body's positional-parameter
# references with the invocation's arguments at load time, so a shell snippet in
# a skill reads correctly on disk and reaches the model corrupted. This asserts
# no SKILL.md carries such a reference.
# ---------------------------------------------------------------------------
echo ""
echo "=== skill-arg-substitution check ==="
if ! python3 ./scripts/check-skill-arg-substitution.py; then
  FAILED=1
fi

# ---------------------------------------------------------------------------
# 10. Ruff (mirrors ci.yml `ruff` job — blocking there, blocking here)
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
# 11. Shellcheck (mirrors ci.yml `shellcheck` job — same discovery/severity)
# ---------------------------------------------------------------------------
echo ""
echo "=== shellcheck ==="
if ! command -v shellcheck >/dev/null 2>&1; then
  skip_step shellcheck "brew install shellcheck"
else
  # Mirrors the CI invocation verbatim so severity, excludes, and file set
  # cannot drift: discovery is delegated to scripts/shellcheck-files.sh (the
  # single source shared with ci.yml, which also covers the extensionless
  # skills/ CLI scripts — #1175). Runtime code gets no rule exclusions;
  # tests/ excludes SC2154/SC2034, the test-harness-only false positives
  # that used to be disabled repo-wide via .shellcheckrc (see its comments).
  if ! bash scripts/shellcheck-files.sh runtime \
    | xargs -0 shellcheck --severity=warning; then
    FAILED=1
  fi
  if ! bash scripts/shellcheck-files.sh tests \
    | xargs -0 shellcheck --severity=warning --exclude=SC2154,SC2034; then
    FAILED=1
  fi
fi

# ---------------------------------------------------------------------------
# 12. Markdownlint (mirrors ci.yml `markdownlint` job)
#
# Version parity: ci.yml's test job pins `markdownlint-cli2@0.23.2` (issue
# #1171). Locally any installed markdownlint-cli2 is accepted — forcing a
# global npm install from a test runner is not this script's place — but if
# your findings disagree with CI, check your version against that pin first.
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
  skip_step markdownlint "npm i -g markdownlint-cli2@0.23.2"
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
# 13. mypy (mirrors ci.yml `mypy` job — blocking there, blocking here)
#
# Scope and flags come from mypy.ini (hooks/_lib + scripts, issue #1301), so
# the invocation carries no arguments and cannot drift from CI's. Version
# parity: ci.yml pins `mypy==1.20.0` and `types-PyYAML==6.0.12.20260815`, the
# stub package for scripts/check-workflow-pins.py's PyYAML import. An
# installed mypy without that stub is not a skip — mypy reports the import as
# untyped and the step FAILS, with mypy's own install hint on the line. Bump
# both pins alongside the workflow.
#
# `python3 -m mypy` is probed BEFORE a bare `mypy` on PATH — the reverse of
# step 10's order — because mypy resolves stub packages from the interpreter
# it runs under. A `mypy` launcher installed for a different Python (a
# pipx/user-site shim) does not see the types-PyYAML installed next to
# python3 and fails on the yaml import while CI, which spells it
# `python3 -m mypy`, passes. The module form is CI's exact invocation; the
# bare binary is only the fallback for a mypy that is not importable from
# python3 at all.
# ---------------------------------------------------------------------------
echo ""
echo "=== mypy ==="
if python3 -m mypy --version >/dev/null 2>&1; then
  MYPY=(python3 -m mypy)
elif command -v mypy >/dev/null 2>&1; then
  MYPY=(mypy)
else
  MYPY=()
fi

if [[ ${#MYPY[@]} -eq 0 ]]; then
  skip_step mypy "pip install 'mypy==1.20.0' 'types-PyYAML==6.0.12.20260815'"
elif ! "${MYPY[@]}"; then
  FAILED=1
fi

# ---------------------------------------------------------------------------
echo ""
if [[ $FAILED -ne 0 ]]; then
  echo "TEST SUITE FAILED" >&2
  exit 1
fi

# Scope note: this counts the four tool steps this script owns (10-13), the
# coverage half of step 1 (same missing-module shape, #1303), plus
# any tool a shell sub-suite announced via the PRAXIS_SUBSKIP marker before
# exiting 0 (#1170) — a whole file that silently skipped on a missing tool is
# a missing-toolchain gap exactly like steps 10-13. Step 6 has its own N/A line
# (deliberately not "SKIPPED") and is excluded from this tally on purpose — it
# is never a missing-toolchain skip. Per-gate platform skips inside a running
# sub-suite — e.g. the Darwin-only "no cwd source" sub-case in
# tests/test_codex_broker_reaper.sh, unreachable where /proc exists — still
# announce themselves only in their own summary and are not aggregated here;
# conflating them would make a portable-by-design skip look like a missing
# toolchain.
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
