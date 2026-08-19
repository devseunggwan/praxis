#!/usr/bin/env bash
# Install packages with apt under a bounded, retried phase (issue #1049).
#
# #1046 put `timeout 300` around apt because a stalled Ubuntu mirror otherwise
# holds the step until the job's own timeout kills it. The bound works -- it was
# observed firing with exit 124 at exactly 300s -- but bounding alone only makes
# the job fail faster, it never makes it pass. This script adds the missing half:
# a finite retry around the whole phase.
#
# Acquire::Retries cannot do this. It retries a failed *download*, so it needs a
# transfer to be judged failed; a mirror that accepts the connection and then
# stops responding never reaches that point. The stall has to be cut from
# outside, and so does the retry.
#
# All attempts failing is a hard failure on purpose. If apt genuinely cannot
# install the package, run-tests.sh under PRAXIS_TESTS_STRICT aborts on the
# missing tool anyway (#917), so passing the step here would only move the same
# red further down and make it harder to read.
set -euo pipefail

# Per-transfer bounds, kept from #1046. They still do useful work inside an
# attempt; they are simply not sufficient on their own.
apt_opts=(
  -o Acquire::Retries=3
  -o Acquire::http::Timeout=20
  -o Acquire::https::Timeout=20
)

# One attempt = update followed by install, and it must cost ONE timeout, not
# one each: two 120s bounds in sequence make an attempt cost up to 240s, which
# blows the per-job budget the numbers below are chosen against. The script
# re-invokes itself in this mode so the pair runs inside a single `timeout` and
# `apt_opts` keeps a single definition. Not a user-facing flag.
if [ "${1:-}" = "--run-attempt" ]; then
  shift
  apt-get "${apt_opts[@]}" update -y
  apt-get "${apt_opts[@]}" install -y "$@"
  exit 0
fi

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <package>..." >&2
  exit 2
fi

packages=("$@")
self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

# 3 x (120s + 10s kill grace) + 2 x 15s backoff = 7m00s worst case. Measured
# against the two jobs that call this: `test` spends ~7m on everything else
# against a 15-minute budget, and `shellcheck` ~35s against 10 minutes, so both
# absorb the worst case. Overridable so a caller can tune without editing here.
attempts="${CI_APT_ATTEMPTS:-3}"
per_attempt="${CI_APT_TIMEOUT:-120}"
backoff="${CI_APT_BACKOFF:-15}"
# Without this, TERM is the only signal timeout sends: an apt that ignores or
# delays it keeps running past the bound and overlaps the next attempt.
kill_grace="${CI_APT_KILL_GRACE:-10}"

for ((attempt = 1; attempt <= attempts; attempt++)); do
  echo "apt attempt ${attempt}/${attempts} (timeout ${per_attempt}s): ${packages[*]}"

  # `set +e` around the call, then read the status explicitly: an `if cmd; then`
  # wrapper would report the *if statement's* status (0 when the condition is
  # false and there is no else branch), which silently hides the failure.
  set +e
  sudo timeout --kill-after="$kill_grace" "$per_attempt" \
    bash "$self" --run-attempt "${packages[@]}"
  status=$?
  set -e

  if [ "$status" -eq 0 ]; then
    echo "apt succeeded on attempt ${attempt}"
    exit 0
  fi

  # 124 is timeout(1)'s own code for "TERM ended the run", so it identifies a
  # stall. 137 does not identify anything on its own -- it is the conventional
  # 128+SIGKILL value, which the --kill-after escalation produces, but so does
  # an OOM kill, and a command is free to exit 137 by itself. Report what was
  # observed and list the candidates; folding it into the generic branch would
  # still lose the one code most worth looking at.
  case "$status" in
    124)
      echo "apt attempt ${attempt} hit the ${per_attempt}s bound (exit 124 -- mirror stalled)" >&2
      ;;
    137)
      echo "apt attempt ${attempt} exited 137 (SIGKILL by convention -- timeout's ${kill_grace}s escalation, an OOM kill, or apt's own status)" >&2
      ;;
    *)
      echo "apt attempt ${attempt} failed with exit ${status}" >&2
      ;;
  esac

  if [ "$attempt" -lt "$attempts" ]; then
    sleep "$backoff"
  fi
done

echo "apt failed after ${attempts} attempts: ${packages[*]}" >&2
exit 1
