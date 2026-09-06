#!/bin/bash
# codex-broker-reaper.sh — reap leaked openai-codex app-server brokers.
#
# Why this exists:
#   The openai-codex plugin starts a per-session app-server broker that is meant
#   to persist for reuse within a session, but it is reparented to launchd
#   (ppid=1) and is NOT killed when its owning Claude session exits. Over
#   multi-day uptime these accumulate (observed: 65 broker groups / ~1.37 GB
#   RSS). Once cumulative RSS crosses the macOS memory-compressor threshold,
#   every idle broker's periodic wakeup triggers compress/decompress churn that
#   surfaces as kernel_task sys time — a non-linear spike, not a linear one.
#
# Safety model:
#   - The idle gate (--max-age) is necessary but NOT sufficient: it measures
#     whether the broker is serving right now, not whether its owner is alive.
#     A session idle between turns leaves broker.log untouched — that is how
#     #919 killed 13 live sessions.
#   - So a kill also requires positive evidence that the owner is GONE. Two
#     signals qualify, both derived from the workspace root recovered from the
#     broker's state dir (see the owner-liveness oracle below):
#       C  the workspace directory has been deleted;
#       D  the workspace still exists, but no live process outside the broker's
#          own tree has its cwd inside it (#926).
#   - Every other signal KEEPS the broker: unknown idleness (no logFile), no
#     unambiguous state dir for the pid, no recorded workspaceRoot, or a
#     workspace whose ownership cannot be decided because no cwd source is
#     available. Under-reaping is the intended bias.
#   - A reap therefore costs at worst a respawn on the next codex call in a
#     workspace that has been deleted.
#
# Modes:
#   --gc                 GC only: remove stale tmp sessionDirs whose broker pid
#                        is dead. Zero risk. (default)
#   --reap [--max-age N] Also kill RUNNING brokers idle > N minutes (default 30)
#                        whose workspace root has been deleted (signal C) or
#                        survives with nobody working in it (signal D), then GC.
#                        Used by the launchd job and the opt-in
#                        PRAXIS_CODEX_REAP=1 path in codex-review-wrap.
#   --dry-run            Print actions without executing.
#
# macOS-only: the leak it addresses is inherent to launchd reparenting and the
# /var/folders sessionDirs the codex broker creates, and the companion launchd
# plist is macOS-only. BSD stat is used directly (no cross-OS shim). The one
# exception is the cwd source behind signal D, which also reads /proc on Linux
# so the signal is testable off Darwin rather than silently unreachable there.

set -euo pipefail

CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
STATE_DIR="$CONFIG_DIR/plugins/data/codex-openai-codex/state"
STATE_SUFFIX="plugins/data/codex-openai-codex/state"

# Every state root this host may hold, not just our own (#1056).
#
# A host can run several Claude config dirs (~/.claude, ~/.claude-2). The broker
# writes its broker.json under the config dir of the SESSION THAT STARTED IT, so
# a reaper reading only $STATE_DIR cannot resolve the rest: pid+sessionDir
# matches nothing, owner_status returns `unknown`, and the under-reap bias keeps
# them forever. Measured on the author's host: 2161 of 2687 skips across 544
# launchd runs.
#
# Candidates are SIBLINGS of CONFIG_DIR rather than a $HOME glob. Production
# CONFIG_DIR is ~/.claude, so the parent is $HOME either way — but keying off
# the parent is what lets a sandboxed CLAUDE_CONFIG_DIR enumerate sandboxed
# siblings, i.e. what makes this testable at all.
#
# The path suffix is what discriminates, not the directory name: dotglob is
# needed because the real ones are dotfiles, and a `*` alone would miss every
# one of them.
#
# Widening is only safe if BOTH halves of the sweep widen together. The GC pass
# rm -rf's sessionDirs named by these records, and its live-claimant guard reads
# the same roots; widening the records alone would let it delete a dir a sibling
# config's live broker still claims — #921 on a new axis.
state_roots() {
  local d saved
  # `shopt -p` exits 1 when the option is UNSET, and `set -e` would kill the
  # subshell this runs in before a single path was emitted.
  saved="$(shopt -p nullglob dotglob || true)"
  shopt -s nullglob dotglob
  { [[ -d "$STATE_DIR" ]] && printf '%s\n' "$STATE_DIR"
    for d in "$(dirname "$CONFIG_DIR")"/*/"$STATE_SUFFIX"; do
      [[ -d "$d" ]] && printf '%s\n' "$d"
    done
  } | while IFS= read -r d; do
        # Physical paths: a symlinked config dir must not be swept twice.
        (cd "$d" 2>/dev/null && pwd -P) || printf '%s\n' "$d"
      done | awk '!seen[$0]++'
  eval "$saved"
}

# One "<pid> <sessionDir> <state-dir>" row per broker.json, built ONCE per pass
# and reused by every lookup (#1056 review F1).
#
# The obvious shape -- re-scan every record for each broker -- costs one grep
# process per record per broker. Measured on the author's host: 996 records x 7
# live brokers = 6972 processes for a single pass, while the reaper holds its
# lock and the launchd job runs every 30 minutes. The record count only grows,
# since a crashed broker's state dir survives until a later GC sweeps it.
#
# Tab-separated because every field can contain spaces: state dirs live under a
# relocatable CLAUDE_CONFIG_DIR and sessionDirs under $TMPDIR. A tab cannot
# appear in either without the plugin having written one.
BROKER_INDEX=""

# The index row: pid, sessionDir, and the state dir the record was read from.
# Held in a variable because broker_index_ensure runs it two ways -- once over
# every file, and once per file on the malformed-record fallback.
BROKER_INDEX_JQ='select(.pid != null and .sessionDir != null)
  | [(.pid|tostring), .sessionDir,
     (input_filename | rtrimstr("/broker.json"))] | @tsv'

broker_index_ensure() {
  [[ -n "$BROKER_INDEX" ]] && return 0
  local f bj
  local -a files=()
  f="$(mktemp "${TMPDIR:-/tmp}/codex-reaper-idx.XXXXXX")" || return 1
  while IFS= read -r bj; do
    [[ -n "$bj" ]] || continue
    files+=("$bj")
  done < <(all_broker_jsons)
  # ONE jq process for the whole index, not one per record. A per-record loop is
  # still O(records) spawns -- 996 here -- and the early test gates invoke the
  # reaper repeatedly, so it showed up as a suite that no longer finishes.
  # input_filename yields the file jq is currently reading, which is how each row
  # keeps its own state dir without a --arg per call. An empty file list must not
  # reach jq: with no arguments it reads stdin and would block.
  if (( ${#files[@]} > 0 )); then
    if ! jq -r "$BROKER_INDEX_JQ" "${files[@]}" 2>/dev/null > "$f"; then
      # jq treats multiple file arguments as ONE concatenated stream and aborts
      # the whole stream at the first parse error -- it does not skip the bad
      # file and continue. Verified: three records with the second malformed
      # yields only the first record's row and exit 5.
      #
      # A truncated index is worse than a slow one. Every record after the bad
      # file disappears, so a LIVE broker's claim on a sessionDir goes unseen,
      # session_dir_has_live_claimant reports the dir unclaimed, and the GC pass
      # deletes a directory still in use -- #921, reached by a different road.
      # The emptiness check below cannot catch it: the index is short, not empty.
      #
      # Re-read file by file so one unparseable record costs only itself. This
      # is the O(records) spawn path the single call exists to avoid, so it runs
      # only when a malformed record actually exists.
      : > "$f"
      for bj in "${files[@]}"; do
        jq -r "$BROKER_INDEX_JQ" "$bj" 2>/dev/null >> "$f" || true
      done
    fi
    # A jq that failed leaves an EMPTY index behind, and an empty index is
    # indistinguishable from "no records exist" at every call site. The reap
    # pass survives that (no match -> owner undetermined -> broker kept), but
    # the GC pass would read zero rows and report gc_dirs=0 as if the sweep had
    # run. Files present + zero rows also happens legitimately when every
    # record is malformed; both readings call for the same answer, which is why
    # this reports failure instead of telling them apart -- refusing to sweep is
    # correct whether jq died or the records are unusable.
    if [[ ! -s "$f" ]]; then
      rm -f "$f"
      return 1
    fi
  fi
  BROKER_INDEX="$f"
  return 0
}

broker_index_clear() {
  [[ -n "$BROKER_INDEX" && -f "$BROKER_INDEX" ]] && rm -f "$BROKER_INDEX"
  BROKER_INDEX=""
}

# Every broker.json across every state root, one path per line.
all_broker_jsons() {
  local root bj saved
  saved="$(shopt -p nullglob || true)"
  shopt -s nullglob
  while IFS= read -r root; do
    [[ -n "$root" ]] || continue
    for bj in "$root"/*/broker.json; do printf '%s\n' "$bj"; done
  done < <(state_roots)
  eval "$saved"
}
BROKER_PATTERN='app-server-broker.mjs'

MODE="gc"
MAX_AGE_MIN=30
DRY_RUN=false

usage() {
  cat <<'USAGE'
Usage: codex-broker-reaper.sh [--gc | --reap] [--max-age MINUTES] [--dry-run]

  --gc            Remove stale tmp sessionDirs of dead brokers (zero risk). Default.
  --reap          Also kill running brokers idle longer than --max-age that no
                  longer have an owner, then GC. A broker loses its owner when
                  its workspace root is deleted, or when the workspace survives
                  but no live process outside the broker's own tree has its cwd
                  inside it. A broker whose owner cannot be determined is kept.
  --max-age N     Idle-minutes threshold for --reap (default: 30).
  --dry-run       Show what would happen; make no changes.
  -h, --help      This help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gc) MODE="gc" ;;
    --reap) MODE="reap" ;;
    --max-age)
      # Require an explicit numeric value; never consume a following option
      # (e.g. `--max-age --dry-run` must error, not silently drop --dry-run and
      # then fall back to a real reap).
      case "${2:-}" in
        ''|*[!0-9]*) echo "--max-age requires an integer >= 1 (minutes)" >&2; usage; exit 2 ;;
      esac
      # All-digits but zero-valued ("0", "00", ...) must also fail: max_age_sec=0
      # would make the idle gate skip nothing and reap fresh, in-use brokers.
      # 10# forces base-10 so leading zeros never trip octal evaluation.
      if (( 10#$2 < 1 )); then
        echo "--max-age requires an integer >= 1 (minutes)" >&2; usage; exit 2
      fi
      MAX_AGE_MIN="$2"; shift ;;
    --dry-run) DRY_RUN=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

# Validate --max-age is a positive integer; fall back to default otherwise.
case "$MAX_AGE_MIN" in
  ''|*[!0-9]*) MAX_AGE_MIN=30 ;;
esac

# Warn when this copy is not the one the plugin manifest points at (#1059).
#
# The launchd plist renders the reaper's path with the plugin version in it
# (`.../cache/praxis/praxis/<ver>/skills/...`), so updating the plugin leaves the
# job running the old copy and nothing says so. LAUNCHD.md documents re-running
# the Install steps, but a manual step nobody is reminded of is a step that gets
# skipped: this host sat three versions behind for weeks.
#
# The check speaks only when the running script is itself inside the plugin
# cache. A development checkout can never equal installPath, so warning there
# would fire on every local run and train the reader to ignore the line.
#
# A missing or unparseable manifest is silence, not drift -- it is the absence
# of an answer. Advisory only: nothing here changes what the run does or what it
# exits with, because running an old reaper still beats running none.
warn_on_version_drift() {
  local self plugin_root installed cache_root
  self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || return 0
  # Both sides are resolved with `pwd -P` before comparing. On macOS /var is a
  # symlink to /private/var, so a CONFIG_DIR that came in as a literal string
  # and a path that went through `pwd -P` disagree on the same directory and the
  # prefix test silently never matches.
  cache_root="$(cd "$CONFIG_DIR/plugins/cache" 2>/dev/null && pwd -P)" || return 0
  case "$self" in
    "$cache_root"/*) ;;
    *) return 0 ;;
  esac
  # <plugin_root>/skills/codex-review-wrap -> <plugin_root>, the shape
  # installPath records.
  plugin_root="$(cd "$self/../.." && pwd -P)" || return 0
  local manifest="$CONFIG_DIR/plugins/installed_plugins.json"
  [[ -f "$manifest" ]] || return 0
  command -v jq >/dev/null 2>&1 || return 0
  installed="$(jq -r '.plugins["praxis@praxis"][0].installPath // empty' "$manifest" 2>/dev/null || true)"
  [[ -n "$installed" ]] || return 0
  # Same resolution on the manifest's side. It records whatever string the
  # installer wrote, which need not be the physical path.
  installed="$(cd "$installed" 2>/dev/null && pwd -P)" || return 0
  [[ "$installed" != "$plugin_root" ]] || return 0
  echo "WARN version drift: running $plugin_root but the manifest installs $installed — re-run the LAUNCHD.md Install steps to re-render the plist" >&2
}
warn_on_version_drift

now=$(date +%s)
max_age_sec=$(( MAX_AGE_MIN * 60 ))

scanned=0; reaped=0; gc_dirs=0; skipped=0

# mtime in epoch seconds. Returns empty on failure (including a file that
# vanished in a race) WITHOUT propagating a non-zero exit — otherwise the
# `m="$(mtime ...)"` assignment would trip `set -e` and abort the whole run
# before the caller's safe-default branch executes. Callers treat empty as
# "unknown" and KEEP.
#
# Portable across the stat dialects (issue #1302): BSD/macOS `stat -f %m` first
# (the platform this script targets), then GNU coreutils `stat -c %Y`, then
# python3, else empty. Each result is captured and accepted only when it is a
# bare integer, never echoed through raw: on GNU `-f` means *filesystem*
# status, and `stat -f %m` there exits 1 yet still writes a multi-line block
# to stdout, so a plain `a || b` chain would be fine on exit code and wrong
# on output.
mtime() {
  local m
  m="$(stat -f %m "$1" 2>/dev/null)" || m=""
  case "$m" in ''|*[!0-9]*)
    m="$(stat -c %Y "$1" 2>/dev/null)" || m="" ;;
  esac
  case "$m" in ''|*[!0-9]*)
    m="$(python3 -c 'import os, sys; print(int(os.stat(sys.argv[1]).st_mtime))' "$1" 2>/dev/null)" || m="" ;;
  esac
  case "$m" in ''|*[!0-9]*) m="" ;; esac
  [[ -z "$m" ]] || echo "$m"
  return 0
}

# Collect a pid and all its descendants (children-first) into a space list.
# Captured ONCE before signalling so the SIGKILL pass cannot miss a child that
# SIGTERM reparented to launchd (which would leave it invisible to the next run).
# The codex broker tree is: app-server-broker.mjs -> node codex app-server ->
# native codex app-server.
collect_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    collect_tree "$child"
  done
  printf '%s ' "$pid"
}

# Resolve the broker's sessionDir from its command-line endpoint:
#   ... serve --endpoint unix:/<sessionDir>/broker.sock
session_dir_of() {
  ps -o command= -p "$1" 2>/dev/null \
    | sed -nE 's@.*unix:(.*/cxc-[A-Za-z0-9]+)/broker\.sock.*@\1@p' \
    | head -1
}

# Guard before any rm -rf: a sessionDir must be a cxc-* dir under a known temp
# root. A corrupt broker.json carrying sessionDir="/" or "$HOME" is rejected so
# the GC pass can never wipe a real tree on malformed input.
is_safe_session_dir() {
  local d="$1"
  # Reject anything but a clean absolute path. A traversal-shaped path such as
  # /tmp/../Users/me/cxc-x string-matches the /tmp/* allowlist below yet resolves
  # OUTSIDE the temp root, so a downstream rm -rf would escape. Screen the raw
  # string for relative form and parent-dir / empty segments before allowlisting.
  [[ "$d" = /* ]] || return 1
  case "$d" in
    *"/../"*|*"/.."|*"//"*) return 1 ;;
  esac
  case "$(basename "$d")" in cxc-*) ;; *) return 1 ;; esac
  case "$d" in
    /var/folders/*|/private/var/folders/*|/tmp/*|/private/tmp/*) return 0 ;;
    *) return 1 ;;
  esac
}

# --- cwd snapshot (signal D, #926) ------------------------------------------
# ONE snapshot per pass, reused across every candidate. Enumerating live process
# cwds costs ~0.4s on a 500-process host; re-running it per broker is what made
# this signal too slow to sit inside the 5-minute stale-lock window.
#
# Source order: `lsof` when present (the macOS path this script targets), then
# /proc/<pid>/cwd on Linux for hosts without lsof. When neither exists the
# snapshot stays unavailable and every workspace-exists broker is KEPT — the
# behaviour that predates this signal.
CWD_SNAP=""

cwd_snapshot_clear() {
  if [[ -n "$CWD_SNAP" && -f "$CWD_SNAP" ]]; then
    rm -f "$CWD_SNAP" || true
  fi
  CWD_SNAP=""
}

# Emit "<pid> <cwd>" lines; emit nothing when no source is available.
#
# SAME-USER ASSUMPTION. Both sources are partial for an unprivileged caller:
# `lsof` reports only the caller's own processes (measured on the author's host
# — 36 uids running, zero root-owned entries in a 435-row snapshot), and
# `readlink /proc/<pid>/cwd` gets EACCES for other users. So a NON-EMPTY
# snapshot does not prove the process list is complete: an owner running as a
# different user is invisible, and signal D would read that workspace as
# unowned. This is sound here only because a broker and its owning session are
# started by the same user — the same reason `pgrep` finds only that user's
# brokers to begin with. If that ever stops holding, this signal needs a
# privileged source, not a wider match.
cwd_pairs() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -a -d cwd -Fpn 2>/dev/null \
      | awk '/^p/{pid=substr($0,2)} /^n/{if (pid != "") print pid" "substr($0,2)}'
    return 0
  fi
  [[ "$(uname -s)" == "Linux" ]] || return 0
  local d pid target
  for d in /proc/[0-9]*; do
    pid="${d#/proc/}"
    target="$(readlink "$d/cwd" 2>/dev/null || true)"
    [[ -n "$target" ]] && printf '%s %s\n' "$pid" "$target"
  done
  return 0
}

# Build the snapshot once per pass. Returns non-zero when no cwd source
# produced anything, which callers MUST read as "undetermined" — never as
# "no owner", or an unreadable /proc would license killing live sessions.
cwd_snapshot_ensure() {
  [[ -n "$CWD_SNAP" ]] && return 0
  local f
  f="$(mktemp "${TMPDIR:-/tmp}/codex-reaper-cwd.XXXXXX")" || return 1
  cwd_pairs > "$f" 2>/dev/null || true
  if [[ ! -s "$f" ]]; then
    rm -f "$f" || true
    return 1
  fi
  CWD_SNAP="$f"
  return 0
}

# True when some live process OUTSIDE this broker's own tree has its cwd inside
# $wroot.
#
# Both qualifiers are load-bearing:
#   - own tree excluded: every live broker keeps its workspace as its own cwd,
#     and so do the `codex app-server` / `codex-code-mode-host` children it
#     spawns (observed 4-deep: broker -> node -> codex -> code-mode-host). Count
#     those and every broker certifies itself as owned, forever — the #919
#     failure mode restated.
#   - component-boundary containment, not a bare string prefix: sibling
#     worktrees routinely share a name prefix. On the author's host
#     `laplace-dev-hub` string-prefixes `laplace-dev-hub-hub-4682` and
#     `-4687`, and a bare prefix test claimed 8 processes from those siblings
#     as owners of the parent.
#
# Both sides are compared as PHYSICAL paths. `lsof` resolves symlinks before
# reporting a cwd, and on macOS the temp roots this tooling lives under are
# symlinked (/tmp -> /private/tmp, /var/folders -> /private/var/folders), so a
# workspaceRoot recorded through the symlinked name never string-matches the
# cwd lsof reports for a process sitting in it. Comparing raw strings makes
# every such broker look unowned — a false reap, the dangerous direction.
workspace_has_live_owner() {
  local bpid="$1" wroot="$2" tree pid path root
  # Fail CLOSED. Today the only caller runs cwd_snapshot_ensure first, so this
  # cannot fire — but if a future one forgets, the `done < "$CWD_SNAP"` redirect
  # below fails, the function returns non-zero, and owner_status reports `dead`.
  # That is a kill on a missing file. Verified by running this function in
  # isolation against an absent snapshot: it returns non-zero, i.e. "no owner".
  [[ -n "$CWD_SNAP" && -s "$CWD_SNAP" ]] || return 0
  root="$(cd "$wroot" 2>/dev/null && pwd -P || printf '%s' "$wroot")"
  tree=" $(collect_tree "$bpid") "
  while read -r pid path; do
    [[ -n "$pid" ]] || continue
    case "$path" in
      "$root"|"$root"/*|"$wroot"|"$wroot"/*) ;;
      *) continue ;;
    esac
    [[ "$tree" == *" $pid "* ]] && continue
    return 0
  done < "$CWD_SNAP"
  return 1
}

# --- Owner-liveness oracle (#919) -------------------------------------------
# "Has no children" is NOT a liveness signal: app-server-broker.mjs connects a
# CodexAppServerClient at startup (which spawns `codex app-server` as its child)
# and only closes it from its own shutdown path, so a genuine orphan keeps that
# child forever. Every live broker observed had one, and so would every orphan.
#
# The broker's owning unit is the WORKSPACE ROOT, not the session. Exactly ONE
# positive orphan signal is trusted here:
#   C  workspaceRoot is known (from the state dir's jobs/*.json) and that
#      directory no longer exists → the worktree is gone → orphan.
# Every other outcome KEEPS the broker: workspace still present, no unambiguous
# state dir for the pid, no jobs file, no recorded workspaceRoot.
#
# A second signal — "workspace exists but nobody is working in it", decided by
# matching live process cwds against the state dir's workspace hash — was built
# and then dropped from this pass. It could not be made safe here: the plugin
# records the GIT ROOT as workspaceRoot while a session's cwd may be any
# subdirectory (lib/workspace.mjs `resolveWorkspaceRoot` -> `ensureGitRepository`),
# so exact matching reports a live owner as dead. It also depends on `lsof`,
# which the Ubuntu CI runner cannot be assumed to provide, and costs a full cwd
# scan per broker. That recovery path is tracked separately.
# The one state dir that belongs to this broker, or empty when that cannot be
# decided. pid alone is not a key: broker.json files left behind by crashed
# brokers are never GC'd, and pids get reused (observed: two state dirs claiming
# pid 17869). The live broker's own command line carries the authoritative
# sessionDir, so a candidate must match on pid AND sessionDir — and if that
# still leaves 0 or several candidates, the caller must treat the owner as
# undetermined rather than trust an arbitrary one.
state_dir_of_broker() {
  local pid="$1" want="$2" ipid isess idir found="" n=0
  [[ -n "$want" ]] || return 0
  broker_index_ensure || return 0
  # Read whole fields, never word-split: a state dir or sessionDir may contain
  # spaces (CLAUDE_CONFIG_DIR is relocatable, and the suite's own fixtures are
  # spaced on purpose to keep that a standing regression).
  while IFS=$'\t' read -r ipid isess idir; do
    [[ "$ipid" == "$pid" ]] || continue
    [[ "$isess" == "$want" ]] || continue
    found="$idir"; n=$(( n + 1 ))
  done < "$BROKER_INDEX"
  (( n == 1 )) && printf '%s' "$found"
  return 0
}

# "<status>:<reason>" where status is dead | alive | unknown. Only `dead`
# licenses a kill. $2 is the sessionDir read off the broker's command line.
owner_status() {
  local pid="$1" want="$2" sdir wroot
  sdir="$(state_dir_of_broker "$pid" "$want")"
  if [[ -z "$sdir" ]]; then
    printf 'unknown:no single state dir matches this pid and sessionDir'; return 0
  fi
  wroot="$(jq -r '.workspaceRoot // empty' "$sdir"/jobs/*.json 2>/dev/null | head -1 || true)"
  if [[ -z "$wroot" ]]; then
    printf 'unknown:no workspaceRoot recorded in %s/jobs' "$(basename "$sdir")"; return 0
  fi
  if [[ ! -d "$wroot" ]]; then
    printf 'dead:workspace %s no longer exists' "$wroot"; return 0
  fi
  # Signal D (#926): the workspace survives, but nobody is working in it.
  if ! cwd_snapshot_ensure; then
    printf 'alive:workspace %s exists (no cwd source — owner undetermined)' "$wroot"
    return 0
  fi
  if workspace_has_live_owner "$pid" "$wroot"; then
    printf 'alive:workspace %s has a live process working in it' "$wroot"; return 0
  fi
  printf 'dead:workspace %s exists but no live process works in it' "$wroot"
}

# Best-effort single instance: the opt-in phase-end reaper and the launchd job
# can otherwise overlap on the same sessionDirs. A stale lock (>5 min — a prior
# run killed before its EXIT trap) is stolen.
LOCK="${TMPDIR:-/tmp}/com.praxis.codex-broker-reaper.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  lock_m="$(mtime "$LOCK")"
  if [[ -n "$lock_m" ]] && (( now - lock_m > 300 )); then
    rmdir "$LOCK" 2>/dev/null || true
    mkdir "$LOCK" 2>/dev/null || { echo "codex-broker-reaper: lock contended — exiting"; exit 0; }
  else
    echo "codex-broker-reaper: another instance running — exiting"
    exit 0
  fi
fi
trap 'cwd_snapshot_clear; broker_index_clear; rmdir "$LOCK" 2>/dev/null || true' EXIT

# --- Pass 1: reap running, idle brokers (only in --reap mode) ---
if [[ "$MODE" == "reap" ]]; then
  # Build the cwd snapshot HERE, in the parent shell. `owner_status` is called
  # as `$(...)`, and a command substitution runs in a subshell whose variable
  # assignments are discarded — so a lazy build inside it re-enumerates every
  # process once per broker (measured: 4 enumerations for 4 candidates) while
  # looking correct. The subshell inherits this value and reuses the file.
  cwd_snapshot_ensure || true
  for pid in $(pgrep -f "$BROKER_PATTERN" 2>/dev/null || true); do
    scanned=$(( scanned + 1 ))
    sdir="$(session_dir_of "$pid")"
    log="$sdir/broker.log"

    if [[ -z "$sdir" || ! -e "$log" ]]; then
      echo "SKIP   pid=$pid (no logFile — idle indeterminate, kept)"
      skipped=$(( skipped + 1 )); continue
    fi

    m="$(mtime "$log")"
    case "$m" in ''|*[!0-9]*) m=$now ;; esac   # raced/removed → treat as fresh → keep
    idle=$(( now - m ))
    if (( idle < max_age_sec )); then
      echo "SKIP   pid=$pid (active: idle ${idle}s < ${max_age_sec}s gate)"
      skipped=$(( skipped + 1 )); continue
    fi

    # Owner-liveness gate (#919). Idleness only says the broker is not serving
    # right now; the kill needs positive evidence that its workspace is gone.
    owner="$(owner_status "$pid" "$sdir")"
    if [[ "${owner%%:*}" != "dead" ]]; then
      echo "SKIP   pid=$pid (owner ${owner%%:*}: ${owner#*:})"
      skipped=$(( skipped + 1 )); continue
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
      echo "WOULD REAP pid=$pid idle=${idle}s dir=$sdir"
    else
      # Re-check right before the kill: if the broker started serving in the
      # gap its log advanced — skip rather than kill mid-request.
      now2=$(date +%s); m2="$(mtime "$log")"
      case "$m2" in ''|*[!0-9]*) m2=$now2 ;; esac
      if (( now2 - m2 < max_age_sec )); then
        echo "SKIP   pid=$pid (became active before kill)"
        skipped=$(( skipped + 1 )); continue
      fi
      # Same re-check for ownership: the workspace can be restored (a worktree
      # re-added, a checkout redone) between the gate and the kill, and a
      # session can start working in it. Drop the cwd snapshot first — reusing
      # the pass-entry one would re-answer from state captured before the gap
      # this re-check exists to cover.
      cwd_snapshot_clear
      cwd_snapshot_ensure || true
      owner="$(owner_status "$pid" "$sdir")"
      if [[ "${owner%%:*}" != "dead" ]]; then
        echo "SKIP   pid=$pid (owner ${owner%%:*} before kill: ${owner#*:})"
        skipped=$(( skipped + 1 )); continue
      fi
      tree="$(collect_tree "$pid")"
      for p in $tree; do kill -TERM "$p" 2>/dev/null || true; done
      sleep 1
      for p in $tree; do kill -KILL "$p" 2>/dev/null || true; done
      [[ -n "$sdir" ]] && is_safe_session_dir "$sdir" && [[ -d "$sdir" ]] && rm -rf "$sdir"
      echo "REAPED pid=$pid idle=${idle}s dir=$sdir"
    fi
    reaped=$(( reaped + 1 ))
  done
fi

# True when a broker.json other than the one under inspection claims $1 as its
# sessionDir and its pid is live. A dead record is not evidence the directory is
# unowned: crashed brokers leave their broker.json behind and pids get reused,
# so a stale record can name a directory a live broker is still writing to.
# #923 gave the reap pass this pid+sessionDir disambiguation; the GC pass had
# none, and rm -rf'd the live session's dir (#921).
session_dir_has_live_claimant() {
  local want="$1" self="$2" opid osdir odir
  # Fail CLOSED: without an index there is no evidence the dir is unclaimed, and
  # a non-zero return here is what licenses the caller's rm -rf. The GC pass
  # gates on the index before it gets here, so this cannot fire today; it is the
  # convention workspace_has_live_owner already follows, kept consistent so a
  # future caller cannot inherit the dangerous default.
  broker_index_ensure || return 0
  # $self is the record under inspection, identified by its state dir -- the
  # index carries no broker.json path, and dirname($self) is exactly the third
  # field. Same per-pass index as state_dir_of_broker: this runs once per GC
  # candidate, so a rescan here is the same N-by-M cost the index removes.
  local selfdir; selfdir="$(dirname "$self")"
  while IFS=$'\t' read -r opid osdir odir; do
    [[ "$odir" == "$selfdir" ]] && continue
    [[ "$osdir" == "$want" ]] || continue
    [[ -n "$opid" ]] || continue
    kill -0 "$opid" 2>/dev/null && return 0
  done < "$BROKER_INDEX"
  return 1
}

# --- Pass 2: GC stale tmp sessionDirs of dead brokers (both modes) ---
# Iterate the per-pass index, not the files. Re-parsing each broker.json here
# costs two `jq` spawns per record (measured 6.2ms each on the author's host),
# which is the whole runtime of this script: 998 records made it ~12s while the
# reap pass above finished in 1.6s. The index already carries every field this
# loop reads, built by one jq call over all the files at once.
# `$bj` is reconstructed rather than carried because the only thing downstream
# wants from it is dirname() -- the state dir, which is the index's third field.
# Fail CLOSED and SAY SO. Without the index this loop has no input, and
# `done < ""` would skip every record while the summary still printed
# gc_dirs=0 -- a silent no-op that reads exactly like "nothing to collect".
if ! broker_index_ensure || [[ -z "$BROKER_INDEX" || ! -f "$BROKER_INDEX" ]]; then
  echo "SKIP GC (broker index unavailable — no records inspected)" >&2
  GC_INPUT="/dev/null"
else
  GC_INPUT="$BROKER_INDEX"
fi
while IFS=$'\t' read -r pid sdir idir; do
    [[ -n "$pid" && -n "$idir" ]] || continue
    bj="$idir/broker.json"
    # Alive brokers are left to the reap pass (idle-gated); GC only dead ones.
    kill -0 "$pid" 2>/dev/null && continue
    if [[ -n "$sdir" ]] && is_safe_session_dir "$sdir" && [[ -d "$sdir" ]]; then
      if session_dir_has_live_claimant "$sdir" "$bj"; then
        echo "SKIP GC dir=$sdir (a live broker still claims this sessionDir)"
        continue
      fi
      if [[ "$DRY_RUN" == "true" ]]; then
        echo "WOULD GC  dir=$sdir (broker pid $pid dead)"
      else
        rm -rf "$sdir"
        echo "GC     dir=$sdir (broker pid $pid dead)"
      fi
      gc_dirs=$(( gc_dirs + 1 ))
    fi
done < "$GC_INPUT"

echo "codex-broker-reaper: mode=$MODE dry_run=$DRY_RUN max_age_min=$MAX_AGE_MIN scanned=$scanned reaped=$reaped gc_dirs=$gc_dirs skipped=$skipped"
