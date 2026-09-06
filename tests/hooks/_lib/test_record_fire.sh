#!/bin/bash
# Tests for _lib/record_fire.sh — fire-ledger instrumentation for shell hooks
# (issue #848). Covers the helper's own contract plus one end-to-end record
# per instrumented impl.sh hook, because the helper working in isolation says
# nothing about whether a hook actually armed it on the branch that fires.
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

PASS=0
FAIL=0

ok() { PASS=$((PASS + 1)); echo "PASS  [$1]"; }
ko() { FAIL=$((FAIL + 1)); echo "FAIL  [$1] $2"; }

# assert_record <ledger> <name> <expected_hook> <expected_decision> <expected_session>
assert_record() {
  local ledger="$1" name="$2" hook="$3" decision="$4" session="$5"
  if [ ! -s "$ledger" ]; then
    ko "$name" "no ledger record written"
    return
  fi
  python3 - "$ledger" "$hook" "$decision" "$session" <<'PY'
import json, sys
path, hook, decision, session = sys.argv[1:5]
rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
match = [r for r in rows
         if r["hook"] == hook and r["decision"] == decision
         and r["session_id"] == session and r["granularity"] == "rich"]
# Exactly one: a duplicate arm (say, sourcing record_fire.sh twice) would
# double-count the engagement, and "at least one" cannot see that.
assert len(match) == 1, (
    f"expected exactly 1 rich {hook}/{decision}/{session} record, "
    f"got {len(match)} in {rows}"
)
PY
  if [ $? -eq 0 ]; then ok "$name"; else ko "$name" "record mismatch"; fi
}

# --- helper contract -------------------------------------------------------
# A shell hook lives two directories below _lib, so the probe harness has to
# reproduce that depth for the relative source path to resolve.
PROBE_ROOT="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
mkdir -p "$PROBE_ROOT/_lib" "$PROBE_ROOT/role/name"
cp "$ROOT_DIR/hooks/_lib/record_fire.sh" "$PROBE_ROOT/_lib/"
cp "$ROOT_DIR/hooks/_lib/_fire_ledger.py" "$PROBE_ROOT/_lib/"
cat > "$PROBE_ROOT/role/name/impl.sh" <<'EOF'
#!/bin/sh
. "$(dirname "$0")/../../_lib/record_fire.sh"
praxis_fire_arm probe-hook probe-role "sess-probe" ""
[ "${PROBE_DECIDE:-}" = "block" ] && PRAXIS_FIRE_DECISION=block
exit 0
EOF

LEDGER="$PROBE_ROOT/led.jsonl"
PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" sh "$PROBE_ROOT/role/name/impl.sh"
assert_record "$LEDGER" "arm defaults to pass" probe-hook pass sess-probe

rm -f "$LEDGER"
PROBE_DECIDE=block PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" sh "$PROBE_ROOT/role/name/impl.sh"
assert_record "$LEDGER" "decision override recorded" probe-hook block sess-probe

# fail-open: telemetry disabled
rm -f "$LEDGER"
PRAXIS_FIRE_TELEMETRY_DISABLE=1 PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
  sh "$PROBE_ROOT/role/name/impl.sh"
rc=$?
if [ "$rc" -eq 0 ] && [ ! -f "$LEDGER" ]; then
  ok "disable env writes nothing, exit 0"
else
  ko "disable env writes nothing, exit 0" "rc=$rc ledger=$(ls "$LEDGER" 2>&1)"
fi

# _fire_ledger.py missing from the resolved _lib: the pure-shell append
# (issue #1183) needs no Python at all on its fast path, so the record must
# STILL land (before #1183 this case asserted a silent drop — the python3 -c
# writer could not import the module). Fail-open here means exit 0, not
# record loss.
NOLIB_ROOT="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
mkdir -p "$NOLIB_ROOT/_lib" "$NOLIB_ROOT/role/name"
cp "$ROOT_DIR/hooks/_lib/record_fire.sh" "$NOLIB_ROOT/_lib/"
cp "$PROBE_ROOT/role/name/impl.sh" "$NOLIB_ROOT/role/name/impl.sh"
PRAXIS_FIRE_TELEMETRY_FILE="$NOLIB_ROOT/led.jsonl" sh "$NOLIB_ROOT/role/name/impl.sh"
rc=$?
if [ "$rc" -eq 0 ]; then
  assert_record "$NOLIB_ROOT/led.jsonl" "missing _fire_ledger still records (shell path)" \
    probe-hook pass sess-probe
else
  ko "missing _fire_ledger still records (shell path)" "rc=$rc"
fi

# fail-open: unwritable ledger path
PRAXIS_FIRE_TELEMETRY_FILE=/dev/null/nope sh "$PROBE_ROOT/role/name/impl.sh"
rc=$?
[ "$rc" -eq 0 ] && ok "unwritable ledger fails open" \
  || ko "unwritable ledger fails open" "rc=$rc"

# --- shell/python record parity (issue #1183) ------------------------------
# The pure-shell append must produce a record indistinguishable from what
# _fire_ledger.record_session_fire writes: same keys, same key ORDER, same
# values (timestamps differ by fire time only, and both must parse as
# ISO-8601). Byte-level, only the timestamp value may differ.
PARITY_LEDGER="$PROBE_ROOT/parity.jsonl"
rm -f "$PARITY_LEDGER"
PRAXIS_FIRE_TELEMETRY_FILE="$PARITY_LEDGER" python3 - "$ROOT_DIR" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/hooks/_lib")
import _fire_ledger
_fire_ledger.record_session_fire(
    hook="parity-hook", role="parity-role", decision="advise",
    session_id="sess-parity", tool="Bash")
PY
PRAXIS_LIB_DIR="$ROOT_DIR/hooks/_lib" PRAXIS_FIRE_TELEMETRY_FILE="$PARITY_LEDGER" sh -c '
  . "$1/hooks/_lib/record_fire.sh"
  praxis_record_fire parity-hook parity-role advise sess-parity Bash
' parity "$ROOT_DIR"
if python3 - "$PARITY_LEDGER" <<'PY'
import json, re, sys
from datetime import datetime
lines = [l.rstrip("\n") for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
assert len(lines) == 2, f"expected 2 records, got {len(lines)}"
py_line, sh_line = lines
py_rec, sh_rec = json.loads(py_line), json.loads(sh_line)
assert list(py_rec) == list(sh_rec), f"key order differs: {list(py_rec)} vs {list(sh_rec)}"
for key in py_rec:
    if key == "timestamp":
        continue
    assert py_rec[key] == sh_rec[key], f"{key}: {py_rec[key]!r} != {sh_rec[key]!r}"
datetime.fromisoformat(sh_rec["timestamp"])  # must stay parseable by readers
# Byte parity modulo the timestamp value: normalizing it must make the raw
# JSONL lines identical (pins separators/spacing, not just parsed content).
norm = lambda s: re.sub(r'("timestamp": ")[^"]*(")', r"\1T\2", s)
assert norm(py_line) == norm(sh_line), f"byte mismatch:\n{norm(py_line)}\n{norm(sh_line)}"
PY
then ok "shell record byte-matches python record"; else ko "shell record byte-matches python record" "see stderr"; fi

# Ledger creation mode (Codex review, PR #1207 round 2): under a permissive
# umask, `>>` alone creates a NEW file at 0666-masked while
# _fire_ledger._atomic_append explicitly requests 0o644
# (hooks/_lib/_fire_ledger.py:297) — so a shell-created daily ledger could
# land 0666/0664 (group/other-writable, i.e. tamperable by any other user on
# the box) where the Python-created one is 0644. Force a maximally permissive
# ambient umask so the divergence has nowhere to hide, then require BOTH
# writers to land at 0644.
MODE_LEDGER="$PROBE_ROOT/mode.jsonl"
rm -f "$MODE_LEDGER"
(
  umask 000
  PRAXIS_LIB_DIR="$ROOT_DIR/hooks/_lib" PRAXIS_FIRE_TELEMETRY_FILE="$MODE_LEDGER" sh -c '
    . "$1/hooks/_lib/record_fire.sh"
    praxis_record_fire mode-hook mode-role pass sess-mode ""
  ' mode "$ROOT_DIR"
)
# GNU stat's `-f` means "filesystem status" and SUCCEEDS on a plain file, so a
# BSD-first `||` chain never reaches the GNU form — it captures "  File: ..."
# instead of a mode. Probe `-c` first: BSD stat rejects it outright, so the
# fallback is the one that actually fires on the wrong platform.
file_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%OLp' "$1" 2>/dev/null
}

MODE_PERM=$(file_mode "$MODE_LEDGER")
if [ "$MODE_PERM" = "644" ]; then
  ok "ledger created 0644 under umask 000 (matches python writer)"
else
  ko "ledger created 0644 under umask 000 (matches python writer)" "mode=$MODE_PERM"
fi

# A STRICTER ambient umask must not be loosened by the fix above — 0077 must
# still land 0600, same as _fire_ledger's os.open(path, flags, 0o644) would
# under that same umask.
STRICT_LEDGER="$PROBE_ROOT/strict-mode.jsonl"
rm -f "$STRICT_LEDGER"
(
  umask 077
  PRAXIS_LIB_DIR="$ROOT_DIR/hooks/_lib" PRAXIS_FIRE_TELEMETRY_FILE="$STRICT_LEDGER" sh -c '
    . "$1/hooks/_lib/record_fire.sh"
    praxis_record_fire mode-hook mode-role pass sess-mode-strict ""
  ' mode "$ROOT_DIR"
)
STRICT_PERM=$(file_mode "$STRICT_LEDGER")
if [ "$STRICT_PERM" = "600" ]; then
  ok "stricter ambient umask (077) is preserved, not loosened"
else
  ko "stricter ambient umask (077) is preserved, not loosened" "mode=$STRICT_PERM"
fi

# Escape fallback: a field value outside the JSON-safe charset (here a
# session_id carrying a double quote and a space) must route through the
# Python writer and still land as ONE valid record with the exact value —
# never a corrupt line, never a drop.
ESC_LEDGER="$PROBE_ROOT/escape.jsonl"
rm -f "$ESC_LEDGER"
PRAXIS_LIB_DIR="$ROOT_DIR/hooks/_lib" PRAXIS_FIRE_TELEMETRY_FILE="$ESC_LEDGER" sh -c '
  . "$1/hooks/_lib/record_fire.sh"
  praxis_record_fire esc-hook esc-role pass "weird \"sess\" id" ""
' escape "$ROOT_DIR"
if python3 - "$ESC_LEDGER" <<'PY'
import json, sys
lines = [l for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
assert len(lines) == 1, f"expected 1 record, got {len(lines)}"
rec = json.loads(lines[0])
assert rec["session_id"] == 'weird "sess" id', rec
assert rec["hook"] == "esc-hook" and rec["granularity"] == "rich", rec
PY
then ok "unsafe field routes through escape fallback"; else ko "unsafe field routes through escape fallback" "$(cat "$ESC_LEDGER" 2>/dev/null)"; fi

# Env parity with _fire_ledger (review round 1): Python reads both env values
# stripped, so the shell writer must too.
# Padded disable value must still disable (Python: _disabled() strips).
rm -f "$LEDGER"
PRAXIS_FIRE_TELEMETRY_DISABLE='1 ' PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
  sh "$PROBE_ROOT/role/name/impl.sh"
if [ ! -f "$LEDGER" ]; then
  ok "padded disable value still disables"
else
  ko "padded disable value still disables" "$(cat "$LEDGER")"
fi

# Padded override path must be trimmed (Python: resolve_path() strips) — a
# verbatim use would split records across two files ('/x' vs ' /x ').
PAD_LEDGER="$PROBE_ROOT/padded.jsonl"
rm -f "$PAD_LEDGER"
PRAXIS_FIRE_TELEMETRY_FILE="  $PAD_LEDGER  " sh "$PROBE_ROOT/role/name/impl.sh"
assert_record "$PAD_LEDGER" "padded override path is trimmed" probe-hook pass sess-probe

# Physical (symlink-resolving) dev-checkout probe: _fire_ledger resolves the
# package root via Path.resolve() (physical), so the shell writer must too —
# a logical cd/pwd would probe the SYMLINK-side root. Layout: R1 holds the
# real _lib (no .git → not a checkout); R2 has a .git dir but only a symlink
# to R1's _lib. Physical resolution lands in R1 → the real-ledger path under
# HOME; logical resolution would land in R2 → R2/.praxis-dev-telemetry, a
# ledger split from where Python writes.
SYM_ROOT="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
mkdir -p "$SYM_ROOT/r1/_lib" "$SYM_ROOT/r2/.git" "$SYM_ROOT/r2/role/name" "$SYM_ROOT/home"
cp "$ROOT_DIR/hooks/_lib/record_fire.sh" "$SYM_ROOT/r1/_lib/"
ln -s "$SYM_ROOT/r1/_lib" "$SYM_ROOT/r2/_lib"
cp "$PROBE_ROOT/role/name/impl.sh" "$SYM_ROOT/r2/role/name/impl.sh"
# Empty overrides: this case exercises DEFAULT path resolution, and
# scripts/run-tests.sh exports PRAXIS_HOME suite-wide — since #1340 the
# default ledger sits under it, so an inherited value would relocate the
# record this assertion looks for (as an inherited PRAXIS_FIRE_TELEMETRY_FILE
# would swallow it). Empty means unset to both writers.
HOME="$SYM_ROOT/home" PRAXIS_HOME='' PRAXIS_FIRE_TELEMETRY_FILE='' sh "$SYM_ROOT/r2/role/name/impl.sh"
SYM_TODAY=$(date -u +%Y-%m-%d)
SYM_REAL="$SYM_ROOT/home/.praxis/telemetry/fire-events-$SYM_TODAY.jsonl"
if [ -s "$SYM_REAL" ] && [ ! -e "$SYM_ROOT/r2/.praxis-dev-telemetry" ]; then
  ok "symlinked _lib resolves physically (matches Python)"
else
  ko "symlinked _lib resolves physically (matches Python)" \
    "real=$(ls "$SYM_REAL" 2>&1) dev=$(ls "$SYM_ROOT/r2/.praxis-dev-telemetry" 2>&1)"
fi
rm -rf "$SYM_ROOT"

# PRAXIS_HOME relocation (issue #1340): the real-ledger branch is
# $PRAXIS_HOME/telemetry — the rule _paths.sh praxis_home() states (one
# trailing slash stripped, a leading tilde expanded against HOME, empty means
# unset), restated inline in record_fire.sh because this file is copied on
# its own into probe trees like this one. Non-checkout layout: no .git two
# levels above _lib, so the dev-checkout branch cannot be what diverts the
# record away from HOME/.praxis.
PH_ROOT="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
mkdir -p "$PH_ROOT/pkg/hooks/_lib" "$PH_ROOT/pkg/hooks/role/name" "$PH_ROOT/home"
cp "$ROOT_DIR/hooks/_lib/record_fire.sh" "$PH_ROOT/pkg/hooks/_lib/"
cp "$PROBE_ROOT/role/name/impl.sh" "$PH_ROOT/pkg/hooks/role/name/impl.sh"
PH_TODAY=$(date -u +%Y-%m-%d)
env -u PRAXIS_FIRE_TELEMETRY_FILE HOME="$PH_ROOT/home" PRAXIS_HOME="$PH_ROOT/relocated/" \
  sh "$PH_ROOT/pkg/hooks/role/name/impl.sh"
assert_record "$PH_ROOT/relocated/telemetry/fire-events-$PH_TODAY.jsonl" \
  "PRAXIS_HOME relocates the default ledger (trailing slash stripped)" probe-hook pass sess-probe
if [ -e "$PH_ROOT/home/.praxis" ]; then
  ko "PRAXIS_HOME relocation writes nothing under HOME/.praxis" "$(find "$PH_ROOT/home" -type f)"
else
  ok "PRAXIS_HOME relocation writes nothing under HOME/.praxis"
fi
# A quoted PRAXIS_HOME=~/x arrives literally; expanded against HOME as
# _paths.sh does (and _paths.py via expanduser), so both writers agree.
env -u PRAXIS_FIRE_TELEMETRY_FILE HOME="$PH_ROOT/home" PRAXIS_HOME='~/tilde-home' \
  sh "$PH_ROOT/pkg/hooks/role/name/impl.sh"
assert_record "$PH_ROOT/home/tilde-home/telemetry/fire-events-$PH_TODAY.jsonl" \
  "tilde PRAXIS_HOME expands against HOME" probe-hook pass sess-probe
# Tilde with HOME unset: _paths.py's expanduser consults the passwd database,
# so the shell writer does too (same stub technique as the HOME-unset case
# below). The bare tilde is the whole home directory.
mkdir -p "$PH_ROOT/bin" "$PH_ROOT/pwhome"
cat > "$PH_ROOT/bin/getent" <<EOF
#!/bin/sh
echo "stubuser:x:1000:1000:Stub User:$PH_ROOT/pwhome:/bin/sh"
EOF
cat > "$PH_ROOT/bin/dscl" <<EOF
#!/bin/sh
echo "NFSHomeDirectory: $PH_ROOT/pwhome"
EOF
chmod +x "$PH_ROOT/bin/getent" "$PH_ROOT/bin/dscl"
env -u HOME -u PRAXIS_FIRE_TELEMETRY_FILE PATH="$PH_ROOT/bin:$PATH" PRAXIS_HOME='~' \
  sh "$PH_ROOT/pkg/hooks/role/name/impl.sh"
assert_record "$PH_ROOT/pwhome/telemetry/fire-events-$PH_TODAY.jsonl" \
  "tilde PRAXIS_HOME with HOME unset expands against the passwd-db home" probe-hook pass sess-probe
rm -rf "$PH_ROOT"

# HOME-unset fallback (Codex review, PR #1207 round 2): with $HOME unset and
# no `.git` marker at the checkout root, the pre-fix code built
# "${HOME:-}/.praxis/telemetry" as "/.praxis/telemetry" — root-owned, so
# `mkdir -p` fails for a normal user and every fire record is silently lost.
# _fire_ledger.resolve_path() never hits this: Python's Path.home() falls
# back to the passwd-database home. Stub `dscl`/`getent` ahead of PATH so
# the shell writer's own passwd lookup is exercised against a controlled
# fake home, without touching the real invoking user's actual $HOME.
HOMEFB_ROOT="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
mkdir -p "$HOMEFB_ROOT/pkgroot/hooks/_lib" "$HOMEFB_ROOT/bin" "$HOMEFB_ROOT/fakehome"
cp "$ROOT_DIR/hooks/_lib/record_fire.sh" "$HOMEFB_ROOT/pkgroot/hooks/_lib/"
cat > "$HOMEFB_ROOT/bin/dscl" <<EOF
#!/bin/sh
echo "NFSHomeDirectory: $HOMEFB_ROOT/fakehome"
EOF
cat > "$HOMEFB_ROOT/bin/getent" <<EOF
#!/bin/sh
echo "stubuser:x:1000:1000:Stub User:$HOMEFB_ROOT/fakehome:/bin/sh"
EOF
chmod +x "$HOMEFB_ROOT/bin/dscl" "$HOMEFB_ROOT/bin/getent"
HOMEFB_TODAY=$(date -u +%Y-%m-%d)
# PRAXIS_HOME is taken out too: the default under test is HOME/.praxis, and
# scripts/run-tests.sh exports a throwaway PRAXIS_HOME that would relocate it
# (#1340).
env -u HOME -u PRAXIS_HOME -u PRAXIS_FIRE_TELEMETRY_FILE PATH="$HOMEFB_ROOT/bin:$PATH" \
  PRAXIS_LIB_DIR="$HOMEFB_ROOT/pkgroot/hooks/_lib" sh -c '
    . "$1/pkgroot/hooks/_lib/record_fire.sh"
    praxis_record_fire homefb-hook homefb-role pass sess-homefb ""
  ' homefb "$HOMEFB_ROOT"
HOMEFB_LEDGER="$HOMEFB_ROOT/fakehome/.praxis/telemetry/fire-events-$HOMEFB_TODAY.jsonl"
if [ -s "$HOMEFB_LEDGER" ] && [ ! -e "/.praxis" ]; then
  ok "HOME-unset falls back to passwd-db home (not /.praxis)"
else
  ko "HOME-unset falls back to passwd-db home (not /.praxis)" \
    "fallback_ledger=$(ls "$HOMEFB_LEDGER" 2>&1)"
fi
rm -rf "$HOMEFB_ROOT"

# Concurrency smoke: N parallel shell appends into one ledger must yield
# exactly N intact lines — the single-printf-under-O_APPEND contract (each
# line far below PIPE_BUF) means no torn/interleaved records. Bounded loop,
# no timing dependence: correctness is asserted on the surviving file.
CONC_LEDGER="$PROBE_ROOT/conc.jsonl"
rm -f "$CONC_LEDGER"
CONC_N=20
i=1
while [ "$i" -le "$CONC_N" ]; do
  PRAXIS_LIB_DIR="$ROOT_DIR/hooks/_lib" PRAXIS_FIRE_TELEMETRY_FILE="$CONC_LEDGER" sh -c '
    . "$1/hooks/_lib/record_fire.sh"
    praxis_record_fire conc-hook conc-role pass "sess-conc-$2" ""
  ' conc "$ROOT_DIR" "$i" &
  i=$((i + 1))
done
wait
if python3 - "$CONC_LEDGER" "$CONC_N" <<'PY'
import json, sys
path, n = sys.argv[1], int(sys.argv[2])
lines = [l for l in open(path, encoding="utf-8") if l.strip()]
assert len(lines) == n, f"expected {n} lines, got {len(lines)}"
sessions = set()
for line in lines:
    rec = json.loads(line)  # a torn/interleaved line fails to parse
    assert rec["hook"] == "conc-hook" and rec["decision"] == "pass", rec
    sessions.add(rec["session_id"])
assert len(sessions) == n, f"expected {n} distinct sessions, got {len(sessions)}"
PY
then ok "parallel appends stay intact (no torn lines)"; else ko "parallel appends stay intact (no torn lines)" "$(wc -l < "$CONC_LEDGER" 2>/dev/null) lines"; fi

# --- end-to-end: each instrumented impl.sh hook ----------------------------
TMP="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }

# completion-verify — claim without evidence blocks
python3 - "$TMP/cv.jsonl" <<'PY'
import json, sys
events = [
    {"message": {"role": "user", "content": "go"}},
    {"message": {"role": "assistant",
                 "content": [{"type": "text", "text": "모두 완료했습니다."}]}},
]
with open(sys.argv[1], "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
LEDGER="$TMP/cv-led.jsonl"
printf '{"transcript_path":"%s","session_id":"sess-cv"}' "$TMP/cv.jsonl" \
  | PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
    bash "$ROOT_DIR/hooks/completion-verify/completion-verify/impl.sh" >/dev/null
assert_record "$LEDGER" "completion-verify records block" completion-verify block sess-cv

# retrospect-mix-check — ordinary message passes
python3 - "$TMP/rm.jsonl" <<'PY'
import json, sys
events = [
    {"message": {"role": "user", "content": "go"}},
    {"message": {"role": "assistant",
                 "content": [{"type": "text", "text": "평범한 응답입니다."}]}},
]
with open(sys.argv[1], "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
LEDGER="$TMP/rm-led.jsonl"
printf '{"transcript_path":"%s","session_id":"sess-rm"}' "$TMP/rm.jsonl" \
  | PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
    bash "$ROOT_DIR/hooks/completion-verify/retrospect-mix-check/impl.sh" >/dev/null
assert_record "$LEDGER" "retrospect-mix-check records pass" retrospect-mix-check pass sess-rm

# codex-review-route — /codex:review advises.
#
# Ported from impl.sh to impl.py in #1304, so it no longer goes through
# record_fire.sh: the advising path calls `_fire_ledger.record_session_fire`
# directly and suppresses the coarse duplicate. The assertion is unchanged —
# exactly one RICH advise record keyed to the payload's session — which is
# what pins the port to the ledger shape the shell version wrote.
#
# The advisory fires on >= 2 non-bare worktrees, so it must run against a
# repository this test builds rather than whichever checkout happens to host
# the run: a developer machine mid-session has many worktrees and a CI runner
# has exactly one, which would make the same assertion pass locally and fail
# in CI.
CR_REPO="$TMP/cr-repo"
git init -q "$CR_REPO"
: > "$CR_REPO/seed"
git -C "$CR_REPO" add seed
git -C "$CR_REPO" -c user.name=t -c user.email=t@e commit -qm seed
git -C "$CR_REPO" worktree add -q -b second "$TMP/cr-wt2"
LEDGER="$TMP/cr-led.jsonl"
printf '{"prompt":"/codex:review","session_id":"sess-cr"}' \
  | (cd "$CR_REPO" && PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
      python3 "$ROOT_DIR/hooks/advisory-nudge/codex-review-route/impl.py") >/dev/null
assert_record "$LEDGER" "codex-review-route records advise" codex-review-route advise sess-cr

# An absent session_id must reach the ledger as "", never as the "unknown"
# placeholder those hooks use for their log lines and marker filenames.
# aggregate_fires adds any non-empty session string to its distinct-session
# set, so "unknown" would collapse every unattributed fire into one fake
# session — inflating the session count these records exist to measure.
for probe in \
  "completion-verify:completion-verify/completion-verify/impl.sh" \
  "retrospect-mix-check:completion-verify/retrospect-mix-check/impl.sh"
do
  probe_hook="${probe%%:*}"
  LEDGER="$TMP/${probe_hook}-nosess.jsonl"
  printf '{"transcript_path":"%s"}' "$TMP/rm.jsonl" \
    | PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
      bash "$ROOT_DIR/hooks/${probe#*:}" >/dev/null
  if python3 -c '
import json, sys
recs = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
sys.exit(0 if recs and all(r.get("session_id") == "" for r in recs) else 1)
' "$LEDGER" 2>/dev/null; then
    ok "$probe_hook records an absent session as empty, not \"unknown\""
  else
    ko "$probe_hook records an absent session as empty, not \"unknown\"" \
      "$(cat "$LEDGER" 2>/dev/null)"
  fi
done

# strike-counter — three strikes then a blocking stop
STATE="$TMP/state"
printf '{"session_id":"sess-sc"}' \
  | PRAXIS_STATE_DIR="$STATE" PRAXIS_FIRE_TELEMETRY_DISABLE=1 \
    bash "$ROOT_DIR/hooks/completion-verify/strike-counter/impl.sh" session-start >/dev/null
for i in 1 2 3; do
  CLAUDE_SESSION_ID=sess-sc PRAXIS_STATE_DIR="$STATE" PRAXIS_FIRE_TELEMETRY_DISABLE=1 \
    bash "$ROOT_DIR/hooks/completion-verify/strike-counter/impl.sh" strike "v$i" >/dev/null
done
LEDGER="$TMP/sc-led.jsonl"
printf '{"session_id":"sess-sc"}' \
  | PRAXIS_STATE_DIR="$STATE" PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
    bash "$ROOT_DIR/hooks/completion-verify/strike-counter/impl.sh" stop >/dev/null
assert_record "$LEDGER" "strike-counter records block" strike-counter block sess-sc

# slash modes are user commands, not hook engagements — they must stay out of
# the ledger, otherwise the fire rate this ledger measures is inflated.
LEDGER="$TMP/sc-slash.jsonl"
CLAUDE_SESSION_ID=sess-sc PRAXIS_STATE_DIR="$STATE" PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
  bash "$ROOT_DIR/hooks/completion-verify/strike-counter/impl.sh" status >/dev/null
if [ ! -f "$LEDGER" ]; then
  ok "strike-counter slash mode records nothing"
else
  ko "strike-counter slash mode records nothing" "$(cat "$LEDGER")"
fi

rm -rf "$PROBE_ROOT" "$NOLIB_ROOT" "$TMP"

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
