#!/usr/bin/env bash
# check-changelog-completeness.sh — fail when a release section's "N PRs since
# X.Y.Z" count disagrees with the PRs actually merged into that release (#750).
#
# Why a COUNT check and not a per-PR presence check: the v7.1.0 notes claimed
# "4 PRs since 7.0.0" while 11 had merged — four user-facing PRs (#729, #731,
# #733, #738) were silently dropped from the hand-written changelog, which is
# the source of the published release body (extract-changelog-section.sh →
# release.yml). A presence check keyed on the PR number is unreliable here
# because praxis cites the *issue* number as often as the PR (e.g. the debt
# skill is `(#711)` though its PR is #732), so a PR-number scan false-flags
# correctly-documented entries. The intro's "N PRs since P.Q.R" line, by
# contrast, is an unambiguous integer that was exactly what went wrong — this
# guard recomputes it from git and fails on a mismatch, listing the PRs so the
# author reconciles the count (and, in practice, the missing entries).
#
# Scope: only the NEWEST release section is checked, and only when it carries a
# "N PRs since P.Q.R" line (the minor-release convention). A section without
# that line — e.g. a major release written as prose — is skipped. The count is
# bounded by the two changelog sections, not by git tags, because this repo's
# release tags lag the changelog (tags stop at v6.3.0 while 6.3.1–7.1.0 sit
# untagged); the range is [commit that introduced `## [P.Q.R]`] .. [commit that
# introduced `## [thisVersion]`], excluding the version-bump commit itself, so
# post-release hotfixes never inflate a frozen release's count.
#
# Env overrides:
#   CHANGELOG_FILE           changelog path (default: <repo>/CHANGELOG.md)
#   PRAXIS_CC_SUBJECTS_FILE  test hook — newline-separated commit subjects used
#                            as the release range instead of `git log`; when
#                            set, git boundary detection is skipped
#
# Exit codes:
#   0   count matches, or the section opts out (no "N PRs since" line)
#   1   declared count disagrees with the computed count
#   2   usage / environment error
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANGELOG="${CHANGELOG_FILE:-$REPO_ROOT/CHANGELOG.md}"

if [[ ! -f "$CHANGELOG" ]]; then
  echo "error: CHANGELOG not found at $CHANGELOG" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# 1. Newest release version + its "N PRs since P.Q.R" claim.
# ---------------------------------------------------------------------------
# First "## [X.Y.Z]" header (Unreleased has no dotted version, so it is skipped).
this_version="$(grep -oE '^## \[[0-9]+\.[0-9]+\.[0-9]+\]' "$CHANGELOG" | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)"
if [[ -z "$this_version" ]]; then
  echo "warning: no released '## [X.Y.Z]' section found; nothing to check" >&2
  exit 0
fi

# The claim line lives in the section body: "<N> PRs since <P.Q.R>".
claim_line="$(awk -v ver="$this_version" '
  $0 ~ "^## \\[" ver "\\]" { cap = 1; next }
  cap && /^## \[/          { exit }
  cap                      { print }
' "$CHANGELOG" | grep -iE '[0-9]+ PRs? since [0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)"

if [[ -z "$claim_line" ]]; then
  echo "note: [$this_version] has no 'N PRs since X.Y.Z' line — count check skipped"
  exit 0
fi

declared="$(printf '%s' "$claim_line" | grep -oiE '[0-9]+ PRs? since' | grep -oE '^[0-9]+')"
prev_version="$(printf '%s' "$claim_line" | grep -oE 'since [0-9]+\.[0-9]+\.[0-9]+' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"

if [[ -z "$declared" || -z "$prev_version" ]]; then
  echo "warning: could not parse the 'N PRs since X.Y.Z' claim ('$claim_line'); skipping" >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# 2. Collect the release range's commit subjects.
# ---------------------------------------------------------------------------
subjects=""
if [[ -n "${PRAXIS_CC_SUBJECTS_FILE:-}" ]]; then
  if [[ ! -f "$PRAXIS_CC_SUBJECTS_FILE" ]]; then
    echo "error: PRAXIS_CC_SUBJECTS_FILE not found at $PRAXIS_CC_SUBJECTS_FILE" >&2
    exit 2
  fi
  subjects="$(cat "$PRAXIS_CC_SUBJECTS_FILE")"
else
  # Boundaries = the commits that first introduced each section header. -S
  # counts occurrences of the literal; the earliest such commit is the add.
  top="$(git -C "$REPO_ROOT" log --reverse --format=%H -S"## [${this_version}]" -- "$CHANGELOG" | head -n1 || true)"
  bottom="$(git -C "$REPO_ROOT" log --reverse --format=%H -S"## [${prev_version}]" -- "$CHANGELOG" | head -n1 || true)"
  if [[ -z "$top" || -z "$bottom" ]]; then
    # A shallow clone (or a squashed history) can hide one boundary. Fail open:
    # this guard must never block a PR just because it cannot see enough history.
    echo "warning: could not resolve the [$prev_version]..[$this_version] commit range (shallow clone?); skipping" >&2
    exit 0
  fi
  subjects="$(git -C "$REPO_ROOT" log "${bottom}..${top}" --no-merges --format='%s')"
fi

# ---------------------------------------------------------------------------
# 3. Count unique release PRs (trailing "(#N)"), excluding the version bump.
# ---------------------------------------------------------------------------
prs="$(printf '%s\n' "$subjects" \
  | grep -vE '^chore(\([^)]*\))?: bump version' \
  | grep -oE '\(#[0-9]+\)$' | tr -cd '0-9\n' | sed '/^$/d' | sort -u)"
actual="$(printf '%s' "$prs" | grep -c . || true)"

# ---------------------------------------------------------------------------
# 4. Compare.
# ---------------------------------------------------------------------------
if [[ "$actual" -ne "$declared" ]]; then
  echo "error: [$this_version] declares '$declared PRs since $prev_version' but $actual PR(s) merged into the release:" >&2
  printf '  #%s\n' $prs >&2
  echo "" >&2
  echo "Reconcile the changelog: add the missing entries and correct the count" >&2
  echo "(or fix the count if a PR is intentionally folded into another entry)." >&2
  exit 1
fi

echo "changelog-completeness OK — [$this_version] declares $declared PRs since $prev_version and $actual merged"
