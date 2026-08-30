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
#   runtime — every shell file outside tests/ (hooks/, scripts/, and the
#             skills/ CLI tools). Linted with no rule exclusions.
#   tests   — every shell file under tests/. Linted with
#             --exclude=SC2154,SC2034 (see .shellcheckrc for why those two
#             are false positives only in the test harness).
#
# Discovery is two passes, identical for both scopes:
#   1. *.sh by extension — what the old `find . -name '*.sh'` covered.
#   2. extensionless files whose first line is a bash/sh shebang — the skills/
#      CLI scripts (claude-recover, cmux-*, spec-drift) ship without a .sh
#      suffix, so pass 1 alone missed ~1,475 lines of runtime shell.
#      Python CLIs (claude-recover-scan, bypass-review) don't match and stay
#      with ruff.
#
# .git is pruned as before; node_modules too, because a contributor's local
# `npm i` tree must not be linted (CI checkouts never have one) and pass 2
# would otherwise read the first line of every vendored file.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

scope="${1:?usage: shellcheck-files.sh runtime|tests}"

case "$scope" in
  runtime)
    find_pre=(. \( -path ./.git -o -path ./tests -o -path ./node_modules \) -prune -o -type f)
    ;;
  tests)
    find_pre=(./tests -type f)
    ;;
  *)
    echo "usage: shellcheck-files.sh runtime|tests" >&2
    exit 2
    ;;
esac

# Pass 1: *.sh by extension.
find "${find_pre[@]}" -name '*.sh' -print0

# Pass 2: extensionless (well, non-.sh) files carrying a bash/sh shebang.
find "${find_pre[@]}" ! -name '*.sh' -print0 \
  | while IFS= read -r -d '' f; do
      IFS= read -r shebang < "$f" || continue
      case "$shebang" in
        '#!'*bash*|'#!/bin/sh'*|'#!/usr/bin/env sh'*) printf '%s\0' "$f" ;;
      esac
    done
