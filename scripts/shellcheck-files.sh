#!/usr/bin/env bash
# scripts/shellcheck-files.sh — canonical discovery of the shell files to
# lint (#1175). (The basename is not on this line's start: a leading
# "# shellcheck" comment would parse as a malformed shellcheck directive.)
#
# Prints a NUL-delimited file list on stdout. Shared by the ci.yml `shellcheck`
# job and scripts/run-tests.sh step 9 as the single discovery point, so the two
# call sites cannot drift apart (local/CI parity is a hard repo rule).
#
# Scope argument (the two shellcheck invocations differ only in excludes):
#   runtime — every tracked shell file outside tests/ (hooks/, scripts/, and
#             the skills/ CLI tools). Linted with no rule exclusions.
#   tests   — every tracked shell file under tests/. Linted with
#             --exclude=SC2154,SC2034 (see .shellcheckrc for why those two
#             are false positives only in the test harness).
#
# Enumeration walks `git ls-files -z` — tracked files only. A filesystem
# `find` would also descend into gitignored trees: .claude/worktrees/agent-*/
# (this repo's own lingering subagent worktrees) would contribute a second
# copy of tests/ to the *runtime* scope — linted with zero excludes — plus
# whatever scratch scripts sit around, and node_modules would need its own
# special-case. Tracked-only makes local and CI see the same set by
# construction. Two selection rules per file:
#   1. *.sh by extension — what the old `find . -name '*.sh'` covered.
#   2. non-.sh files whose first line is a bash/sh shebang — the skills/ CLI
#      scripts (claude-recover, cmux-*, spec-drift) ship without a .sh
#      suffix, so rule 1 alone missed ~1,475 lines of runtime shell.
#      Python CLIs (claude-recover-scan, bypass-review) don't match and stay
#      with ruff.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

scope="${1:?usage: shellcheck-files.sh runtime|tests}"
case "$scope" in
  runtime|tests) ;;
  *)
    echo "usage: shellcheck-files.sh runtime|tests" >&2
    exit 2
    ;;
esac

# A shell shebang means: interpreter whose *basename* is `bash` or `sh`,
# directly or via env — `#!/bin/bash`, `#!/usr/bin/sh`, `#!/usr/bin/env bash`,
# optional interpreter args after. Anchored so `/bin/shiny` or `/bin/dash`
# cannot overmatch a `/bin/sh` prefix.
shebang_re='^#![[:space:]]*([^[:space:]]*/)?(env[[:space:]]+)?(bash|sh)([[:space:]]|$)'

git ls-files -z | while IFS= read -r -d '' f; do
  case "$f" in
    tests/*) [ "$scope" = tests ] || continue ;;
    *)       [ "$scope" = runtime ] || continue ;;
  esac
  # Skip tracked-but-absent paths (mid-rebase deletions) and symlinks — the
  # tracked symlinks here (CLAUDE.md, plugins/*) alias content that is already
  # linted at its real path, or point at directories.
  { [ -f "$f" ] && [ ! -L "$f" ]; } || continue
  case "$f" in
    *.sh)
      printf '%s\0' "$f"
      continue
      ;;
  esac
  # `read` exits nonzero when the first line lacks a trailing newline but
  # still populates the variable — only skip when nothing was read at all.
  IFS= read -r shebang < "$f" || [ -n "$shebang" ] || continue
  if [[ "$shebang" =~ $shebang_re ]]; then
    printf '%s\0' "$f"
  fi
done
