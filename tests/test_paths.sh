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
TMP_HOME=$(mktemp -d)
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

echo ""
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  - %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
