#!/usr/bin/env bash
# extract-changelog-section.sh — Print one version's section from CHANGELOG.md
#
# Single source for release-note bodies: both the one-off release backfill and
# the release.yml workflow call this so the note text always matches the
# changelog. Given a version (with or without a leading "v"), prints the block
# from its "## [X.Y.Z]" header up to — but not including — the next "## ["
# header. The header line (with its date) is kept as the first line so a
# backfilled release, whose GitHub "published" date is the creation date,
# still records the original release date in its body.
#
# Trailing blank lines are dropped by command substitution; the printed
# section ends with exactly one newline.
#
# Exit codes:
#   0   section found and printed
#   1   usage error / CHANGELOG missing
#   2   version not present in CHANGELOG.md
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANGELOG="${CHANGELOG_FILE:-$REPO_ROOT/CHANGELOG.md}"

if [[ $# -ne 1 ]]; then
  echo "usage: $(basename "$0") <version>   # e.g. 6.2.0 or v6.2.0" >&2
  exit 1
fi

version="${1#v}" # strip an optional leading "v"

if [[ ! -f "$CHANGELOG" ]]; then
  echo "error: CHANGELOG not found at $CHANGELOG" >&2
  exit 1
fi

# Escape the dots so the version matches literally, not as "any char".
ver_re="${version//./\\.}"

# Stop at the next "## [" version header, or at the trailing link-reference
# definition block — the last version's section has no following header, so
# without this it would absorb every compare-link line. The stop matches only
# version/Unreleased labels ("[X.Y.Z]: " / "[Unreleased]: "), not any "[x]: "
# line, so a markdown link-reference inside a release body is never mistaken
# for the trailing block and silently truncated.
section="$(awk -v ver="$ver_re" '
  $0 ~ "^## \\[" ver "\\]" { capture = 1; print; next }
  capture && /^## \[/                                    { exit }
  capture && /^\[(Unreleased|[0-9]+\.[0-9]+\.[0-9]+)\]: / { exit }
  capture                                                { print }
' "$CHANGELOG")"

if [[ -z "$section" ]]; then
  echo "error: version $version not found in $CHANGELOG" >&2
  exit 2
fi

printf '%s\n' "$section"
