#!/bin/bash
# test_paths.sh — coverage for hooks/_lib/_paths.py
#
# Asserts the host-neutral praxis home resolution and writable fallback:
#   - praxis_home() default = ~/.praxis (expanduser)
#   - PRAXIS_HOME override is honored (and expanded)
#   - resolve_writable creates <home>/<subdir> and returns the file path
#   - resolve_writable falls back to ${TMPDIR}/praxis-<file> when home is
#     not writable, and never raises
#   - the fire ledger follows PRAXIS_HOME in both its writers (issue #1340)

set +e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB="$ROOT_DIR/hooks/_lib"

PASS=0
FAIL=0
FAILED_NAMES=()

assert_eq() {
  local name="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected actual=$actual"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# 1. default home ends with /.praxis
default_home=$(env -u PRAXIS_HOME python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _paths import praxis_home
h = praxis_home()
print("yes" if h.endswith("/.praxis") and "~" not in h else f"no:{h}")
PYEOF
)
assert_eq "default home = ~/.praxis (expanded)" "yes" "$default_home"

# 2. PRAXIS_HOME override honored + a real file created under <home>/logs
TMP_HOME=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
override_path=$(PRAXIS_HOME="$TMP_HOME" python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _paths import resolve_writable
p = resolve_writable("logs", "x.jsonl")
open(p, "a").close()
print(p)
PYEOF
)
assert_eq "resolve_writable under PRAXIS_HOME" "$TMP_HOME/logs/x.jsonl" "$override_path"
if [ -d "$TMP_HOME/logs" ]; then
  echo "PASS  [resolve_writable created the subdir]"; PASS=$((PASS + 1))
else
  echo "FAIL  [resolve_writable did not create subdir]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("subdir created")
fi
rm -rf "$TMP_HOME"

# 3. Unwritable home -> TMPDIR fallback (never raises)
fallback=$(PRAXIS_HOME="/proc/nonexistent-praxis-home" TMPDIR="/tmp" python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _paths import resolve_writable
print(resolve_writable("logs", "hook-errors.jsonl"))
PYEOF
)
assert_eq "unwritable home falls back to TMPDIR" "/tmp/praxis-hook-errors.jsonl" "$fallback"

# 4. praxis_state_dir() default = ~/.praxis/state (PRAXIS_HOME-aware)
state_default=$(env -u PRAXIS_STATE_DIR PRAXIS_HOME="/tmp/ph-527" python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _paths import praxis_state_dir
print(praxis_state_dir())
PYEOF
)
assert_eq "praxis_state_dir default = <home>/state" "/tmp/ph-527/state" "$state_default"

# 5. PRAXIS_STATE_DIR override wins (back-compat)
state_override=$(PRAXIS_STATE_DIR="/custom/state" PRAXIS_HOME="/tmp/ph-527" python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _paths import praxis_state_dir
print(praxis_state_dir())
PYEOF
)
assert_eq "PRAXIS_STATE_DIR override wins" "/custom/state" "$state_override"

# 6. praxis_cache_dir() = ~/.praxis/cache
cache_dir=$(PRAXIS_HOME="/tmp/ph-527" python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _paths import praxis_cache_dir
print(praxis_cache_dir())
PYEOF
)
assert_eq "praxis_cache_dir = <home>/cache" "/tmp/ph-527/cache" "$cache_dir"

# 7. legacy_state_dir() = ~/.claude/state/praxis (expanded)
legacy=$(HOME="/tmp/fakehome-527" python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _paths import legacy_state_dir
print(legacy_state_dir())
PYEOF
)
assert_eq "legacy_state_dir = ~/.claude/state/praxis" "/tmp/fakehome-527/.claude/state/praxis" "$legacy"

# 8. prune_stale removes entries past the TTL and keeps fresh ones (#903)
prune_result=$(python3 - "$LIB" << 'PYEOF'
import os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
from _paths import prune_stale

d = tempfile.mkdtemp()
old, fresh = os.path.join(d, "old.json"), os.path.join(d, "fresh.json")
for p in (old, fresh):
    open(p, "w").close()
stale = time.time() - 8 * 86400
os.utime(old, (stale, stale))

olddir = os.path.join(d, "olddir")
os.makedirs(olddir)
inner = os.path.join(olddir, "inner")
open(inner, "w").close()
# Backdate the contents too: a directory is judged by its newest descendant,
# so backdating only the dir would (correctly) spare it. Case 15 covers that.
os.utime(inner, (stale, stale))
os.utime(olddir, (stale, stale))

removed = prune_stale(d, ttl_days=7)
print(f"{removed}:{os.path.exists(old)}:{os.path.exists(fresh)}:{os.path.exists(olddir)}")
PYEOF
)
assert_eq "prune_stale sweeps stale files and dirs, keeps fresh" "2:False:True:False" "$prune_result"

# 9. PRAXIS_CACHE_TTL_DAYS=0 disables the sweep
prune_disabled=$(PRAXIS_CACHE_TTL_DAYS=0 python3 - "$LIB" << 'PYEOF'
import os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
from _paths import prune_stale
d = tempfile.mkdtemp()
p = os.path.join(d, "old.json")
open(p, "w").close()
stale = time.time() - 400 * 86400
os.utime(p, (stale, stale))
print(f"{prune_stale(d)}:{os.path.exists(p)}")
PYEOF
)
assert_eq "PRAXIS_CACHE_TTL_DAYS=0 disables the sweep" "0:True" "$prune_disabled"

# 10. prune_stale on a missing directory returns 0 rather than raising
prune_missing=$(python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _paths import prune_stale
print(prune_stale("/nonexistent-praxis-903", ttl_days=1))
PYEOF
)
assert_eq "prune_stale on missing dir returns 0" "0" "$prune_missing"

# 10b. resolve_cache_file adopts a pre-#903 ${TMPDIR} file (#903)
# A session already holding intent/marker state when praxis upgrades must not
# read the new path as "no state" — that silently disarms the Stop gate.
adopt=$(python3 - "$LIB" << 'PYEOF'
import os, sys, tempfile
sys.path.insert(0, sys.argv[1])
home, tmp = tempfile.mkdtemp(), tempfile.mkdtemp()
os.environ["PRAXIS_HOME"], os.environ["TMPDIR"] = home, tmp
from _paths import resolve_cache_file
legacy = os.path.join(tmp, "praxis-session-intent-x.json")
with open(legacy, "w") as fh:
    fh.write('{"mutation_verb_seen": true}')
p = resolve_cache_file("session-intent-x.json")
print(f"{p.startswith(home)}:{open(p).read()}:{os.path.exists(legacy)}")
PYEOF
)
assert_eq "resolve_cache_file adopts the legacy TMPDIR file" \
  'True:{"mutation_verb_seen": true}:False' "$adopt"

# 10c. With no legacy file, resolve_cache_file just returns the new path
fresh=$(python3 - "$LIB" << 'PYEOF'
import os, sys, tempfile
sys.path.insert(0, sys.argv[1])
home, tmp = tempfile.mkdtemp(), tempfile.mkdtemp()
os.environ["PRAXIS_HOME"], os.environ["TMPDIR"] = home, tmp
from _paths import resolve_cache_file
p = resolve_cache_file("session-intent-y.json")
print(f"{p == os.path.join(home, 'cache', 'session-intent-y.json')}:{os.path.exists(p)}")
PYEOF
)
assert_eq "resolve_cache_file with no legacy file creates nothing" "True:False" "$fresh"

# --- _paths.sh must agree with _paths.py (#903) -----------------------------
# The writer/reader halves of a protocol are split across the two languages, so
# a divergence here silently sends them to different files.

sh_eval() {  # sh_eval <shell-expression>
  sh -c ". \"$LIB/_paths.sh\"; $1"
}

# 11. shell praxis_home honors PRAXIS_HOME and defaults to ~/.praxis
assert_eq "sh praxis_home: PRAXIS_HOME override" "/tmp/ph-903" \
  "$(PRAXIS_HOME=/tmp/ph-903 sh_eval 'praxis_home')"
assert_eq "sh praxis_home: default" "/tmp/fakehome-903/.praxis" \
  "$(unset PRAXIS_HOME; HOME=/tmp/fakehome-903 sh_eval 'praxis_home')"

# 12. shell state/cache dirs match the Python resolver, override precedence included
assert_eq "sh praxis_state_dir default" "/tmp/ph-903/state" \
  "$(unset PRAXIS_STATE_DIR; PRAXIS_HOME=/tmp/ph-903 sh_eval 'praxis_state_dir')"
assert_eq "sh PRAXIS_STATE_DIR override wins" "/custom/state" \
  "$(PRAXIS_STATE_DIR=/custom/state PRAXIS_HOME=/tmp/ph-903 sh_eval 'praxis_state_dir')"
assert_eq "sh praxis_cache_dir" "/tmp/ph-903/cache" \
  "$(PRAXIS_HOME=/tmp/ph-903 sh_eval 'praxis_cache_dir')"

# 12a. sh/py agree on PRAXIS_STATE_DIR with a literal tilde (issue #1215).
# Before the fix Python called os.path.expanduser() on the override, so
# PRAXIS_STATE_DIR=~/x produced $HOME/x in Python but ~/x in the shell —
# shell and Python hooks could not share state.  The fix removes expanduser,
# making both sides return the literal string with one trailing slash stripped.
TILDE='~'   # must stay outside single-quotes to prevent shell expansion
assert_eq "sh/py agree on PRAXIS_STATE_DIR=~/x (issue #1215)" \
  "$(HOME=/tmp/fakehome-903 PRAXIS_STATE_DIR="${TILDE}/state-x" PRAXIS_HOME=/tmp/ph-903 sh_eval 'praxis_state_dir')" \
  "$(HOME=/tmp/fakehome-903 PRAXIS_STATE_DIR="${TILDE}/state-x" PRAXIS_HOME=/tmp/ph-903 python3 -c "import sys; sys.path.insert(0, '$LIB'); import _paths; print(_paths.praxis_state_dir())")"
assert_eq "sh/py agree on PRAXIS_STATE_DIR=~ (bare tilde, issue #1215)" \
  "$(HOME=/tmp/fakehome-903 PRAXIS_STATE_DIR="${TILDE}" PRAXIS_HOME=/tmp/ph-903 sh_eval 'praxis_state_dir')" \
  "$(HOME=/tmp/fakehome-903 PRAXIS_STATE_DIR="${TILDE}" PRAXIS_HOME=/tmp/ph-903 python3 -c "import sys; sys.path.insert(0, '$LIB'); import _paths; print(_paths.praxis_state_dir())")"
assert_eq "sh/py agree on PRAXIS_STATE_DIR=/abs/path (literal path, issue #1215)" \
  "$(PRAXIS_STATE_DIR=/abs/path PRAXIS_HOME=/tmp/ph-903 sh_eval 'praxis_state_dir')" \
  "$(PRAXIS_STATE_DIR=/abs/path PRAXIS_HOME=/tmp/ph-903 python3 -c "import sys; sys.path.insert(0, '$LIB'); import _paths; print(_paths.praxis_state_dir())")"
assert_eq "sh/py agree on PRAXIS_STATE_DIR with trailing slash stripped (issue #1215)" \
  "$(PRAXIS_STATE_DIR=/custom/state/ PRAXIS_HOME=/tmp/ph-903 sh_eval 'praxis_state_dir')" \
  "$(PRAXIS_STATE_DIR=/custom/state/ PRAXIS_HOME=/tmp/ph-903 python3 -c "import sys; sys.path.insert(0, '$LIB'); import _paths; print(_paths.praxis_state_dir())")"

# 13. shell resolve_writable creates the subdir, and falls back like Python
SH_HOME=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
assert_eq "sh praxis_resolve_writable path" "$SH_HOME/cache/x.json" \
  "$(PRAXIS_HOME="$SH_HOME" sh_eval 'praxis_resolve_writable cache x.json')"
if [ -d "$SH_HOME/cache" ]; then
  echo "PASS  [sh praxis_resolve_writable created the subdir]"; PASS=$((PASS + 1))
else
  echo "FAIL  [sh praxis_resolve_writable did not create subdir]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("sh subdir created")
fi
rm -rf "$SH_HOME"
assert_eq "sh unwritable home falls back to TMPDIR" "/tmp/praxis-hook-errors.jsonl" \
  "$(PRAXIS_HOME=/proc/nonexistent-praxis-home TMPDIR=/tmp sh_eval 'praxis_resolve_writable logs hook-errors.jsonl')"

# 14. A literal tilde in PRAXIS_HOME must expand the same way on both sides.
# Python runs os.path.expanduser; shell left alone does not, so a quoted or
# config-exported PRAXIS_HOME='~/praxis' would split the two resolvers apart.
TILDE='~'   # kept in a variable so the literal never sits inside quotes
assert_eq "sh praxis_home expands a literal ~/" "/tmp/fakehome-903/praxis" \
  "$(HOME=/tmp/fakehome-903 PRAXIS_HOME="$TILDE/praxis" sh_eval 'praxis_home')"
assert_eq "sh praxis_home expands a bare ~" "/tmp/fakehome-903" \
  "$(HOME=/tmp/fakehome-903 PRAXIS_HOME="$TILDE" sh_eval 'praxis_home')"
assert_eq "sh/py agree on a literal ~/ PRAXIS_HOME" \
  "$(HOME=/tmp/fakehome-903 PRAXIS_HOME="$TILDE/praxis" python3 -c "import sys; sys.path.insert(0, '$LIB'); import _paths; print(_paths.praxis_home())")" \
  "$(HOME=/tmp/fakehome-903 PRAXIS_HOME="$TILDE/praxis" sh_eval 'praxis_home')"
assert_eq "sh praxis_home leaves a mid-path tilde alone" "/tmp/a~b" \
  "$(PRAXIS_HOME='/tmp/a~b' sh_eval 'praxis_home')"

# 15. prune_stale must judge a directory by its newest descendant, not its own
# mtime. The dedup hooks nest state two levels down, and writing there does not
# touch the top-level dir — so an mtime-only check deletes live session state.
PRUNE_HOME=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
mkdir -p "$PRUNE_HOME/tree/session-a"
: > "$PRUNE_HOME/tree/session-a/live"
: > "$PRUNE_HOME/loose-old"
# Backdate the parent dir and the loose file; leave the nested file fresh.
touch -t 202001010000 "$PRUNE_HOME/tree" "$PRUNE_HOME/tree/session-a" "$PRUNE_HOME/loose-old"
python3 -c "import sys; sys.path.insert(0, '$LIB'); import _paths; _paths.prune_stale('$PRUNE_HOME', 7.0)" >/dev/null
if [ -f "$PRUNE_HOME/tree/session-a/live" ]; then
  echo "PASS  [prune_stale keeps a stale dir holding fresh nested state]"; PASS=$((PASS + 1))
else
  echo "FAIL  [prune_stale deleted active nested state]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("prune nested state")
fi
if [ -f "$PRUNE_HOME/loose-old" ]; then
  echo "FAIL  [prune_stale left a genuinely stale file]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("prune stale file")
else
  echo "PASS  [prune_stale still removes a genuinely stale file]"; PASS=$((PASS + 1))
fi
# A tree that is stale all the way down still goes.
mkdir -p "$PRUNE_HOME/dead/session-b"
: > "$PRUNE_HOME/dead/session-b/old"
touch -t 202001010000 "$PRUNE_HOME/dead/session-b/old" "$PRUNE_HOME/dead/session-b" "$PRUNE_HOME/dead"
python3 -c "import sys; sys.path.insert(0, '$LIB'); import _paths; _paths.prune_stale('$PRUNE_HOME', 7.0)" >/dev/null
if [ -d "$PRUNE_HOME/dead" ]; then
  echo "FAIL  [prune_stale left a fully stale tree]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("prune stale tree")
else
  echo "PASS  [prune_stale removes a fully stale tree]"; PASS=$((PASS + 1))
fi
rm -rf "$PRUNE_HOME"

# 16. A concurrent migration must not hand back the abandoned legacy path.
# Two processes can both see the new path absent; the loser's move fails, and
# returning `legacy` then writes where no later reader looks.
RACE_HOME=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
RACE_TMP=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
mkdir -p "$RACE_HOME/cache"
printf '{"winner": true}' > "$RACE_HOME/cache/race.json"
printf '{"stale": true}' > "$RACE_TMP/praxis-race.json"
assert_eq "resolve_cache_file prefers the new path when both exist" \
  "$RACE_HOME/cache/race.json" \
  "$(PRAXIS_HOME="$RACE_HOME" TMPDIR="$RACE_TMP" python3 -c "import sys; sys.path.insert(0, '$LIB'); import _paths; print(_paths.resolve_cache_file('race.json'))")"
rm -rf "$RACE_HOME" "$RACE_TMP"

# 17. praxis_rotate_log (issue #1282): an append-only log past the cap is
# renamed to <log>.1 (replacing the previous predecessor) and one under it is
# left alone; a missing file is a no-op. Sourced the way the shell hooks do.
ROT_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
printf 'old\n' > "$ROT_DIR/a.log.1"
head -c 2048 /dev/zero | tr '\0' 'x' > "$ROT_DIR/a.log"
printf 'small\n' > "$ROT_DIR/b.log"
rot_out=$(sh -c '. "$1/_paths.sh"; praxis_rotate_log "$2/a.log" 1024; r1=$?; praxis_rotate_log "$2/b.log" 1024; r2=$?; praxis_rotate_log "$2/absent.log" 1024; r3=$?; praxis_rotate_log "$2/b.log" abc; r4=$?; echo "rc=$r1$r2$r3$r4"' _ "$LIB" "$ROT_DIR" 2>&1)
assert_eq "praxis_rotate_log never fails and prints nothing (incl. a bad cap)" "rc=0000" "$rot_out"
assert_eq "praxis_rotate_log moves an oversized log to .1" "yes" \
  "$([ ! -e "$ROT_DIR/a.log" ] && [ "$(wc -c < "$ROT_DIR/a.log.1" | tr -d ' ')" = "2048" ] && echo yes || echo no)"
assert_eq "praxis_rotate_log leaves a log under the cap alone" "small" "$(cat "$ROT_DIR/b.log")"
assert_eq "praxis_rotate_log ignores a missing log" "yes" "$([ ! -e "$ROT_DIR/absent.log.1" ] && echo yes || echo no)"
rm -rf "$ROT_DIR"

# 18. The fire ledger follows PRAXIS_HOME in both writers (issue #1340).
# PRIVACY.md lists PRAXIS_HOME as the override for ~/.praxis/telemetry:
# _fire_ledger.resolve_telemetry_dir() resolves through _paths.praxis_home()
# and record_fire.sh restates the shell rule inline (it is copied on its own
# into probe trees and cannot source a sibling). Probed out of a copied _lib
# with no .git two levels up — the dev-checkout branch (#934) is what sends
# in-repo runs to <checkout>/.praxis-dev-telemetry, and would mask a
# regression here. No PRAXIS_FIRE_TELEMETRY_FILE either: that override wins
# over everything and is exactly what #849 used to hide this defect.
LEDGER_ROOT=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
LEDGER_LIB="$LEDGER_ROOT/pkg/hooks/_lib"
mkdir -p "$LEDGER_LIB" "$LEDGER_ROOT/home"
cp "$LIB/_fire_ledger.py" "$LIB/_paths.py" "$LIB/_state_lock.py" "$LIB/record_fire.sh" "$LEDGER_LIB/"
LEDGER_TODAY=$(date -u +%Y-%m-%d)
LEDGER_FILE="$LEDGER_ROOT/relocated/telemetry/fire-events-$LEDGER_TODAY.jsonl"
env -u PRAXIS_FIRE_TELEMETRY_FILE HOME="$LEDGER_ROOT/home" PRAXIS_HOME="$LEDGER_ROOT/relocated" \
  python3 - "$LEDGER_LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
import _fire_ledger
assert _fire_ledger._checkout_root() is None, "probe tree must not read as a checkout"
_fire_ledger.record_session_fire("py-hook", "role", "pass", "sess-1340", "")
PYEOF
env -u PRAXIS_FIRE_TELEMETRY_FILE HOME="$LEDGER_ROOT/home" PRAXIS_HOME="$LEDGER_ROOT/relocated" \
  PRAXIS_LIB_DIR="$LEDGER_LIB" \
  sh -c '. "$1/record_fire.sh"; praxis_record_fire sh-hook role pass sess-1340 ""' _ "$LEDGER_LIB"
# The python writer counts its `pass` rather than appending it (issue #1238),
# so it lands in a `fire-counts-*` sibling; the shell fast path still appends.
# Both files sit in the same relocated telemetry directory, which is what this
# case is about, so the probe reads the directory rather than one file.
assert_eq "fire ledger: both writers land in \$PRAXIS_HOME/telemetry outside a checkout" \
  "py-hook sh-hook" \
  "$(python3 -c '
import glob, json, os, sys
hooks = []
for path in sorted(glob.glob(os.path.join(sys.argv[1], "fire-*.jsonl"))):
    with open(path) as handle:
        hooks.extend(json.loads(l)["hook"] for l in handle if l.strip())
print(" ".join(sorted(hooks)))' "$(dirname "$LEDGER_FILE")" 2>/dev/null)"
assert_eq "fire ledger: nothing written under HOME/.praxis" "" \
  "$(find "$LEDGER_ROOT/home" -type f 2>/dev/null)"
# The shell writer's inline rule is a restatement of _paths.sh praxis_home(),
# so pin the two to each other on the input they most easily diverge on: a
# literal tilde, which the shell must expand against HOME as Python does.
# The tilde is assembled from a variable: a quoted '~/...' word is what
# SC2088 flags, and here the unexpanded tilde is the input under test.
ledger_tilde='~'
sh_tilde_dir=$(env -u PRAXIS_FIRE_TELEMETRY_FILE HOME="$LEDGER_ROOT/home" PRAXIS_HOME='~/ph-1340' \
  PRAXIS_LIB_DIR="$LEDGER_LIB" \
  sh -c '. "$1/record_fire.sh"; praxis_record_fire sh-hook role pass sess-1340 ""; find "$2/home" -name "fire-events-*.jsonl"' _ "$LEDGER_LIB" "$LEDGER_ROOT")
assert_eq "record_fire.sh tilde PRAXIS_HOME matches _paths.sh praxis_home()/telemetry" \
  "$(HOME="$LEDGER_ROOT/home" PRAXIS_HOME="$ledger_tilde/ph-1340" sh_eval 'praxis_home')/telemetry" \
  "$(dirname "$sh_tilde_dir")"
assert_eq "_fire_ledger tilde PRAXIS_HOME matches _paths.sh praxis_home()/telemetry" \
  "$(HOME="$LEDGER_ROOT/home" PRAXIS_HOME="$ledger_tilde/ph-1340" sh_eval 'praxis_home')/telemetry" \
  "$(env -u PRAXIS_FIRE_TELEMETRY_FILE HOME="$LEDGER_ROOT/home" PRAXIS_HOME='~/ph-1340' python3 -c "import sys; sys.path.insert(0, '$LEDGER_LIB'); import _fire_ledger; print(_fire_ledger.resolve_telemetry_dir())")"
rm -rf "$LEDGER_ROOT"

echo ""
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  - %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
