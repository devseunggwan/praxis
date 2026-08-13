#!/usr/bin/env bash
# tests/test_cwd_trust.sh — workspace-trust lookup for cmux-delegate (#981 Work B)
#
# Claude Code's trust dialog consumes piped stdin, so a delegation into an
# untrusted directory loses its prompt and the worker sits in an empty REPL.
# Step 4 now passes the prompt over argv so nothing can be eaten; this helper
# answers whether the worker will nonetheless stop at that dialog, so the
# delegator can say so up front instead of discovering it as silence.
#
# Every case runs against a fixture `.claude.json` under a temp dir — the real
# config is never read, so the result does not drift with the developer's own
# trust decisions.
#
# Run:  bash tests/test_cwd_trust.sh
# Exit: 0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HELPER="$ROOT_DIR/skills/cmux-delegate/cwd-trust.sh"

# shellcheck source=./_assert_lib.sh
source "$SCRIPT_DIR/_assert_lib.sh"
assert_lib_init "$HELPER"

TMP="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

TRUSTED="$TMP/trusted"
DECLINED="$TMP/declined"
UNSEEN="$TMP/unseen"
CHILD="$TRUSTED/child"
mkdir -p "$TRUSTED" "$DECLINED" "$UNSEEN" "$CHILD"

# realpath, because the helper resolves its argument and macOS maps /tmp to
# /private/tmp — comparing against the unresolved form would fail for a reason
# that has nothing to do with trust.
r() { python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1"; }

CONFIG_DIR="$TMP/cfg"
mkdir -p "$CONFIG_DIR"
python3 - "$CONFIG_DIR/.claude.json" "$(r "$TRUSTED")" "$(r "$DECLINED")" <<'PY'
import json, sys
out, trusted, declined = sys.argv[1:4]
json.dump({"projects": {
    trusted: {"hasTrustDialogAccepted": True},
    declined: {"hasTrustDialogAccepted": False},
}}, open(out, "w"))
PY

# Field extractor: the helper emits eval-able `key='value'` pairs, so read them
# the way the documented caller does rather than by regex.
field() {  # $1 = key, $2 = directory, $3... = env assignments
  local key="$1" dir="$2"; shift 2
  ( eval "$(env "$@" sh "$HELPER" "$dir")"; eval "printf '%s' \"\${$key:-}\"" )
}

check() {  # $1 = name, $2 = expected, $3 = actual
  _assert_record "$1" "$([ "$2" = "$3" ] && echo 1 || echo 0)" "expected '$2', got '$3'"
}

# ---------------------------------------------------------------------------
# 1. The three verdicts
# ---------------------------------------------------------------------------

check "accepted entry reads as trusted" \
  "yes" "$(field trusted "$TRUSTED" "CLAUDE_CONFIG_DIR=$CONFIG_DIR")"

check "declined entry reads as untrusted" \
  "no" "$(field trusted "$DECLINED" "CLAUDE_CONFIG_DIR=$CONFIG_DIR")"

check "a directory with no entry reads as untrusted" \
  "no" "$(field trusted "$UNSEEN" "CLAUDE_CONFIG_DIR=$CONFIG_DIR")"

# Declined and never-seen are both `no` but call for different words to the
# user — one had a dialog answered, the other has never had one.
check "declined and unseen are distinguishable by reason" \
  "entry-declined" "$(field reason "$DECLINED" "CLAUDE_CONFIG_DIR=$CONFIG_DIR")"

check "an unseen directory says so" \
  "no-entry" "$(field reason "$UNSEEN" "CLAUDE_CONFIG_DIR=$CONFIG_DIR")"

# ---------------------------------------------------------------------------
# 2. Unreadable config is `unknown`, never `no`
#
# `no` means "a human will have to press a key". Attaching that to every
# delegation whose config could not be read is how a warning stops being read.
# ---------------------------------------------------------------------------

check "a missing config is unknown" \
  "unknown" "$(field trusted "$TRUSTED" "CLAUDE_CONFIG_DIR=$TMP/nowhere")"

BROKEN="$TMP/broken"
mkdir -p "$BROKEN"
printf '{"projects": {' > "$BROKEN/.claude.json"
check "a malformed config is unknown" \
  "unknown" "$(field trusted "$TRUSTED" "CLAUDE_CONFIG_DIR=$BROKEN")"

SHAPE="$TMP/shape"
mkdir -p "$SHAPE"
printf '{"projects": ["not", "a", "map"]}' > "$SHAPE/.claude.json"
check "an unexpected config shape is unknown" \
  "unknown" "$(field trusted "$SHAPE" "CLAUDE_CONFIG_DIR=$SHAPE")"

# ---------------------------------------------------------------------------
# 3. The account split
#
# `--account claude-2` sends the worker to ~/.claude-2/.claude.json. A
# delegator that reads its own config would answer confidently about someone
# else's trust state, which is worse than not answering.
# ---------------------------------------------------------------------------

OTHER="$TMP/other-account"
mkdir -p "$OTHER"
printf '{"projects": {}}' > "$OTHER/.claude.json"
check "the same directory is untrusted under another account" \
  "no" "$(field trusted "$TRUSTED" "CLAUDE_CONFIG_DIR=$OTHER")"

check "the config actually read is reported" \
  "$OTHER/.claude.json" "$(field config "$TRUSTED" "CLAUDE_CONFIG_DIR=$OTHER")"

# ---------------------------------------------------------------------------
# 4. A trusted ancestor is reported, never substituted for the verdict
#
# Whether Claude Code inherits trust from a parent directory is UNVERIFIED (see
# the helper's header). The helper must therefore not answer that question by
# way of its verdict — it reports the ancestor and leaves the verdict keyed on
# the directory it was asked about.
# ---------------------------------------------------------------------------

check "a child of a trusted directory is not silently trusted" \
  "no" "$(field trusted "$CHILD" "CLAUDE_CONFIG_DIR=$CONFIG_DIR")"

check "the trusted ancestor is surfaced" \
  "$(r "$TRUSTED")" "$(field ancestor "$CHILD" "CLAUDE_CONFIG_DIR=$CONFIG_DIR")"

check "no ancestor is claimed when there is none" \
  "" "$(field ancestor "$UNSEEN" "CLAUDE_CONFIG_DIR=$CONFIG_DIR")"

# ---------------------------------------------------------------------------
# 5. Calling convention
# ---------------------------------------------------------------------------

env "CLAUDE_CONFIG_DIR=$CONFIG_DIR" sh "$HELPER" "$TRUSTED" >/dev/null 2>&1
_assert_record "a well-formed call exits 0" "$([ $? -eq 0 ] && echo 1 || echo 0)" \
  "helper exited non-zero on a valid lookup"

env "CLAUDE_CONFIG_DIR=$TMP/nowhere" sh "$HELPER" "$TRUSTED" >/dev/null 2>&1
_assert_record "an unreadable config still exits 0" "$([ $? -eq 0 ] && echo 1 || echo 0)" \
  "a fail-open probe must not fail the caller"

sh "$HELPER" >/dev/null 2>&1
_assert_record "a missing argument exits 2" "$([ $? -eq 2 ] && echo 1 || echo 0)" \
  "usage error did not exit 2"

# A value carrying a single quote must not break out of the eval the documented
# caller performs — the directory name is attacker-adjacent input in the sense
# that it comes from whatever path the user delegated into.
QUOTED="$TMP/it's here"
mkdir -p "$QUOTED"
check "a quote in the path survives the eval" \
  "no" "$(field trusted "$QUOTED" "CLAUDE_CONFIG_DIR=$CONFIG_DIR")"

assert_lib_summary
