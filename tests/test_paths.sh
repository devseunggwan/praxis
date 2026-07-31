#!/bin/bash
# test_paths.sh — coverage for hooks/_lib/_paths.py
#
# Asserts the host-neutral praxis home resolution and writable fallback:
#   - praxis_home() default = ~/.praxis (expanduser)
#   - PRAXIS_HOME override is honored (and expanded)
#   - resolve_writable creates <home>/<subdir> and returns the file path
#   - resolve_writable falls back to ${TMPDIR}/praxis-<file> when home is
#     not writable, and never raises

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
open(os.path.join(olddir, "inner"), "w").close()
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

echo ""
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  - %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
