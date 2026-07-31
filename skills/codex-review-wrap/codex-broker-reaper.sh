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
#   - A broker with ANY child process is KEPT. Its descendants include the live
#     `codex app-server` backend, and collect_tree() signals the whole tree — so
#     reaping such a broker kills the owning session, not leaked infra (#919:
#     13 live-but-idle sessions were killed this way).
#   - The idle gate (--max-age) is a necessary but NOT sufficient condition: it
#     measures whether the broker is serving right now, not whether its session
#     is alive. A session idle between turns leaves broker.log untouched.
#   - Only a childless, idle broker is reaped — the backend has already detached,
#     so the worst case is a benign respawn on the next codex call.
#   - When idleness cannot be determined (no logFile), the broker is KEPT.
#
# Modes:
#   --gc                 GC only: remove stale tmp sessionDirs whose broker pid
#                        is dead. Zero risk. (default)
#   --reap [--max-age N] Also kill RUNNING brokers that are childless AND idle
#                        > N minutes (default 30), then GC. Used by the launchd
#                        job and the opt-in
#                        PRAXIS_CODEX_REAP=1 path in codex-review-wrap.
#   --dry-run            Print actions without executing.
#
# macOS-only: the leak it addresses is inherent to launchd reparenting and the
# /var/folders sessionDirs the codex broker creates, and the companion launchd
# plist is macOS-only. BSD stat is used directly (no cross-OS shim).

set -euo pipefail

CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
STATE_DIR="$CONFIG_DIR/plugins/data/codex-openai-codex/state"
BROKER_PATTERN='app-server-broker.mjs'

MODE="gc"
MAX_AGE_MIN=30
DRY_RUN=false

usage() {
  cat <<'USAGE'
Usage: codex-broker-reaper.sh [--gc | --reap] [--max-age MINUTES] [--dry-run]

  --gc            Remove stale tmp sessionDirs of dead brokers (zero risk). Default.
  --reap          Also kill running brokers that have no child process AND have
                  been idle longer than --max-age, then GC. A broker with a
                  child still owns a live codex backend and is never killed.
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

now=$(date +%s)
max_age_sec=$(( MAX_AGE_MIN * 60 ))

scanned=0; reaped=0; gc_dirs=0; skipped=0

# mtime in epoch seconds (BSD/macOS stat). Returns empty on failure (including a
# file that vanished in a race) WITHOUT propagating a non-zero exit — otherwise
# the `m="$(mtime ...)"` assignment would trip `set -e` and abort the whole run
# before the caller's safe-default branch executes. Callers treat empty as
# "unknown" and KEEP.
mtime() { stat -f %m "$1" 2>/dev/null || true; }

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

# Direct children of a pid as a space-separated list; empty when it has none.
# `pgrep` exits 1 on no match, so the pipeline is terminated by `tr` (always 0)
# to keep `set -e` out of it.
child_pids_of() {
  local kids
  kids="$(pgrep -P "$1" 2>/dev/null | tr '\n' ' ')"
  [[ -n "${kids// /}" ]] && printf '%s' "${kids% }"
  return 0
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
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

# --- Pass 1: reap running, idle brokers (only in --reap mode) ---
if [[ "$MODE" == "reap" ]]; then
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

    # Owner-liveness gate (#919). A broker's children are its `codex app-server`
    # backend; collect_tree() below would sweep them into the kill. An idle log
    # only proves the broker is not serving *right now*, so children are the
    # evidence that a session still owns it.
    children="$(child_pids_of "$pid")"
    if [[ -n "$children" ]]; then
      echo "SKIP   pid=$pid (owner alive: child pids $children — reaping would kill the codex backend)"
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
      # Same re-check for ownership: a backend can attach in the gap, and
      # collect_tree() would then sweep it into the kill.
      children="$(child_pids_of "$pid")"
      if [[ -n "$children" ]]; then
        echo "SKIP   pid=$pid (owner attached before kill: child pids $children)"
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

# --- Pass 2: GC stale tmp sessionDirs of dead brokers (both modes) ---
if [[ -d "$STATE_DIR" ]]; then
  shopt -s nullglob
  for bj in "$STATE_DIR"/*/broker.json; do
    pid="$(jq -r '.pid // empty' "$bj" 2>/dev/null || true)"
    sdir="$(jq -r '.sessionDir // empty' "$bj" 2>/dev/null || true)"
    [[ -z "$pid" ]] && continue
    # Alive brokers are left to the reap pass (idle-gated); GC only dead ones.
    kill -0 "$pid" 2>/dev/null && continue
    if [[ -n "$sdir" ]] && is_safe_session_dir "$sdir" && [[ -d "$sdir" ]]; then
      if [[ "$DRY_RUN" == "true" ]]; then
        echo "WOULD GC  dir=$sdir (broker pid $pid dead)"
      else
        rm -rf "$sdir"
        echo "GC     dir=$sdir (broker pid $pid dead)"
      fi
      gc_dirs=$(( gc_dirs + 1 ))
    fi
  done
fi

echo "codex-broker-reaper: mode=$MODE dry_run=$DRY_RUN max_age_min=$MAX_AGE_MIN scanned=$scanned reaped=$reaped gc_dirs=$gc_dirs skipped=$skipped"
