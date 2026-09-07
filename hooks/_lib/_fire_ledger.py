"""Fire-event telemetry: record which hooks fired and their decision (issue #710).

Companion to bypass-telemetry (which logs bypass *events*). This module logs
hook *fires* from the central PreToolUse(Bash) dispatcher (`_dispatch.py`) — the
single point that already runs every member of the hot Bash group and captures
each one's `(exit, stdout, stderr)`. One JSONL line per member per dispatched
tool call — except `pass`, which is counted instead of written (see `pass`
COUNTERS below).

`pass` COUNTERS (issue #1238): a `pass` decision was 99.2% of the ledger
(116,834 of 118,131 rows on 2026-09-05), and the read path paid for all of it
— `count_session_fires` scans today's whole plaintext file on every Stop.
A `pass` now goes to a per-session counter file (`fire-counts-<date>.<session
token>.jsonl`), same record shape plus a `count`, buffered in the process and
merged at exit. Every other decision keeps its own row, written straight
through and unbuffered, because those are what the gates and the audits read.
What the fold costs is ORDER: a counted pass no longer sits at a position in
the stream, so a metric defined over "what came next after an advise" cannot
be computed from it. `advise_ignored_rate` was that metric and was retired
with this change; see docs/hook-prune-audit.md's Axis 2 note.

COVERAGE (issue #710, two tiers):
  RICH   — the dispatched PreToolUse(Bash) group, recorded by
           `record_group_fires` from the dispatcher which captures each member's
           `(rc, stdout, stderr)` → full block/ask/advise/pass + session_id/tool.
  COARSE — every other hook that uses the @fail_open decorator (Stop,
           UserPromptSubmit, PostToolUse, SessionStart, non-Bash PreToolUse),
           recorded by `record_standalone_fire` from `_hook_runtime.fail_open`.
           That wrapper sees only the return code (no stdout/stdin capture — it
           must not interfere with a live hook process), so ask/advise/pass
           collapse to "pass" and session_id/tool are absent. "block" means
           exit-code 2 ONLY: Stop/UserPromptSubmit hooks block via a stdout JSON
           `decision` field while exiting 0, so THEIR blocks are invisible to the
           coarse path and recorded as "pass" (PreToolUse blocks do exit 2 and
           are captured). Treat coarse Block as a lower bound.
CEILING: a hook that reaches neither @fail_open nor an explicit
`record_session_fire` call is uninstrumented. The dispatcher process marks
itself (mark_dispatcher_process) so its Bash-group members are not
double-counted by the coarse path.

SHELL HOOKS (issue #848): `impl.sh` hooks run no Python `main()`, so
@fail_open never wraps them — all four at the time (strike-counter across
its three events, completion-verify, retrospect-mix-check, and
codex-review-route, since ported to Python in issue #1304) held zero records
while an audit reading this ledger scored their absence as "never fires".
They now source `_lib/record_fire.sh` and arm an EXIT trap
(`praxis_fire_arm`) that writes exactly one RICH record per invocation via
`record_session_fire`. Rich, not coarse: a shell hook has already parsed its
own stdin payload and holds a real session_id. Because there is no coarse
path for them, there is nothing to suppress on success and nothing to
restore on failure — the `suppress_coarse_duplicate` contract below does not
apply. Two deliberate gaps: guard clauses that exit BEFORE session_id is
parsed (missing jq, unparseable stdin) are unrecorded, since a record keyed
to an unknown session cannot be aggregated per-session; and strike-counter's
slash-command modes (strike/status/reset) are user invocations, not hook
engagements, so counting them would inflate the fire rate.

SINGLE-EVENT RICH (issue #740): a standalone hook outside the Bash dispatch
group that still needs real session_id/tool attribution (not the coarse
session_id="" shape) calls `record_session_fire` directly from its own
main() after parsing its own stdin payload. It still also goes through
@fail_open's coarse recording for the same hook name — see
`record_session_fire`'s docstring for the resulting dedup contract.

STOP-LANE BLOCK RECOVERY (issue #847): the five completion-verify Stop
hooks (completion-signal-gate, merge-state-claim-gate,
negative-existence-verdict-gate, runtime-state-claim-gate,
readonly-verify-deferral-gate) close the coarse block-invisibility gap
above by calling `record_session_fire` at their emit point with the real
decision (block under their strict env var, else advise), then
`suppress_coarse_duplicate()` — GATED on record_session_fire returning True —
to drop the redundant coarse "pass" that `aggregate_fires` would otherwise
sum into `fires=2, block=1, pass=1` for a single emit. Suppression is
conditional: on a failed rich append (returns False) the coarse fallback is
left in place, so a transient ledger-write error never drops the fire from
both streams. One rich record is written per genuine emit — no
session-level dedup (each hook's `stop_hook_active` early-return already
guards re-entrant re-fires, so distinct per-turn engagements are counted,
not collapsed). session_id attribution is kept when the payload carries
one and forgone otherwise (`record_session_fire` normalizes a missing id
to ""), so the decision is never dropped just because the id is absent.
Before #847 the Stop lane recorded structurally zero non-pass fires
(every block/advise collapsed to coarse "pass"), which mis-scored these
gates in the fire-ledger prune audit.

Record fields (JSONL, one line per hook fire):
  timestamp    UTC ISO-8601
  session_id   from payload (rich only; "" for coarse)
  tool         tool_name from payload (rich only; "" for coarse)
  hook         hook name (manifest `name`)
  role         hook role (manifest `role`)
  decision     "block" | "ask" | "advise" | "pass" | "skip"
               ("skip" = dispatcher budget-skip, issue #1167: the member was
               never run because the group budget could not cover it)
  granularity  "rich" | "coarse"

Storage (precedence order — see `resolve_path`):
  Override: PRAXIS_FIRE_TELEMETRY_FILE (full path, used by tests)
  Dev:      <checkout>/.praxis-dev-telemetry/fire-events-YYYY-MM-DD.jsonl when
            this module lives inside a git checkout (issue #934)
  Default:  ~/.praxis/telemetry/fire-events-YYYY-MM-DD.jsonl — under
            $PRAXIS_HOME/telemetry when PRAXIS_HOME relocates the tree
            (issue #1340, via `_paths.praxis_home`). Daily rotation; a
            finished day is gzipped to <name>.<token>.jsonl.gz by a detached
            child on the next day's first write, and swept after
            PRAXIS_TELEMETRY_RETENTION_DAYS — #1078, #1238.
  Opt-out:  PRAXIS_FIRE_TELEMETRY_DISABLE=1 → no-op

Fail-open: any error → silently no-op. Never raises into the dispatcher.
"""
from __future__ import annotations

import atexit
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from _state_lock import LOCK_SUFFIX, state_lock  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - `_lib` not on sys.path
    import contextlib

    # Same degradation `_state_lock` applies to a missing `fcntl`: the caller
    # keeps the racy behaviour it had before the lock existed. An unreachable
    # sibling module must not take down a hook via an import this module makes
    # at module scope, outside `@fail_open`'s reach.
    @contextlib.contextmanager
    def state_lock(path: str, timeout: float | None = None):  # type: ignore[misc]
        yield False

    LOCK_SUFFIX = ".lock"

DECISION_BLOCK = "block"
DECISION_ASK = "ask"
DECISION_ADVISE = "advise"
DECISION_PASS = "pass"
DECISION_SKIP = "skip"

# Decision markers — kept in sync with _dispatch.py run_group aggregation.
# (The invariant canary planned in issue #712 will pin this pairing.)
_ASK_MARKER = '"permissionDecision": "ask"'
_DENY_MARKER = '"permissionDecision": "deny"'
# Kept in sync with _dispatch._SKIP_MARKER: a member the dispatcher skipped
# because the group budget could not cover it (issue #1167). The skip must be
# recorded as its own decision — classifying its stderr note as "advise" would
# hide the starvation this record exists to surface.
_SKIP_MARKER = "[dispatch] budget-skip"

# Kept in sync with `_dispatch.STOP_LANE_EVENTS`: the events under which the
# dispatcher accepts a top-level `{"decision": "block"}` / `{"systemMessage":
# ...}` object as this member's decision. Classifying that shape under any
# other event would record a block the dispatcher never enforced (#1337).
STOP_LANE_EVENTS = ("Stop", "SubagentStop")


def _is_stop_block(stdout: str) -> bool:
    """True iff `stdout` is a Stop-lane block object (issue #1169 / PR #1199).

    A Stop hook blocks via a top-level `{"decision": "block", "reason": ...}`
    JSON at exit 0 — no exit-2, no permissionDecision marker — so without this
    shape the ledger records a grouped Stop block as "pass"/"advise",
    mis-scoring the completion gates in the fire-ledger prune audit. Shape
    recognition PARSES the JSON (kept in sync with
    `_dispatch._stop_block_reason`), never substring-matches, so prose that
    merely mentions the shape cannot register as a block.
    """
    # No substring pre-filter — see `_dispatch._stop_block_reason`, which this
    # is kept in sync with: an escaped `decision` key parses to the same object
    # and a literal probe would drop it (issue #1199 review).
    if not stdout:
        return False
    try:
        obj = json.loads(stdout)
    except ValueError:
        return False
    return isinstance(obj, dict) and obj.get("decision") == "block"


def _is_stop_advisory(stdout: str) -> bool:
    """True iff `stdout` is a Stop-lane advisory object (issue #1281).

    A Stop hook advises via a top-level `{"systemMessage": ...}` at exit 0
    (`_hook_io.emit_stop_advisory`) — on stdout, not stderr, so the
    stderr-based advise classification below never saw it and a grouped Stop
    advisory was recorded as "pass". Same parse-only recognition as
    `_is_stop_block`; a block object that also carries a systemMessage is a
    block, which `classify_decision` checks first.
    """
    if not stdout:
        return False
    try:
        obj = json.loads(stdout)
    except ValueError:
        return False
    if not isinstance(obj, dict):
        return False
    message = obj.get("systemMessage")
    return isinstance(message, str) and bool(message)


def classify_decision(
    rc: int, stdout: str, stderr: str, event: str | None = None
) -> str:
    """Map a member's `(exit, stdout, stderr)` to a fire decision.

    Mirrors `_dispatch.run_group`'s PER-MEMBER decision precedence (this is one
    member's own outcome, not the cross-member aggregate the dispatcher emits):
    block (exit 2 / deny marker / Stop-lane `{"decision": "block"}` JSON) >
    ask (ask marker) > advise (any stderr, or a Stop-lane `{"systemMessage":
    ...}` at exit 0) > pass.
    A dispatcher budget-skip record (never actually run) is decision "skip".

    `event` is the dispatcher's own event, not a payload field: run_group has it
    as a parameter, so passing it down cannot be defeated by a payload that
    omits `hook_event_name`. It gates the Stop-lane branch because the
    dispatcher accepts that shape only under `is_stop` and only at `rc == 0`
    (`_dispatch.run_group`); without both gates the ledger recorded a block the
    dispatcher never enforced — a member that printed the JSON and then died,
    or one that printed it under another event.
    The two SUBSTRING marker lanes carry the same gate for the same reason, at
    `PreToolUse`: the dispatcher probes them only under `is_pretooluse`
    (`_dispatch.run_group`), so a Stop or PostToolUse member whose stdout merely
    contains a marker was recorded as a block or an ask the dispatcher never
    enforced. Exit 2 stays event-agnostic, matching the dispatcher's own exit-2
    lane. None means "event unknown", and an unknown event is neither Stop nor
    PreToolUse.
    """
    is_pretooluse = event == "PreToolUse"
    if rc == 2 or (is_pretooluse and _DENY_MARKER in stdout):
        return DECISION_BLOCK
    if rc == 0 and event in STOP_LANE_EVENTS and _is_stop_block(stdout):
        return DECISION_BLOCK
    if is_pretooluse and _ASK_MARKER in stdout:
        return DECISION_ASK
    if stderr.startswith(_SKIP_MARKER):
        return DECISION_SKIP
    if stderr.strip():
        return DECISION_ADVISE
    if rc == 0 and event in STOP_LANE_EVENTS and _is_stop_advisory(stdout):
        return DECISION_ADVISE
    return DECISION_PASS


def _disabled() -> bool:
    return os.environ.get("PRAXIS_FIRE_TELEMETRY_DISABLE", "").strip() == "1"


# Set True in the dispatcher process so fail_open-level recording skips the
# Bash-group members — they are already recorded richly by record_group_fires.
# Process-local: standalone hook processes never import/run the dispatcher.
_DISPATCHER_PROCESS = False

# Set True ONLY in the dispatcher process. `_DISPATCHER_PROCESS` above cannot
# answer "am I the dispatcher": `suppress_coarse_duplicate` sets it too, from a
# standalone hook, to mean something else entirely (a rich record for this
# invocation already exists). Anything that must distinguish the two reads this
# one (issue #1199 review).
_IN_DISPATCHER = False


def mark_dispatcher_process() -> None:
    """Mark the current process as the dispatcher (suppresses coarse recording).

    Intentionally one-way and process-lifetime — there is no unmark. Production
    is safe because each hook (and the dispatcher) runs in its own fresh process,
    so the flag never leaks across roles. The only place it must be reset is a
    single-process test harness running both roles (see test helper).
    """
    global _DISPATCHER_PROCESS, _IN_DISPATCHER
    _DISPATCHER_PROCESS = True
    _IN_DISPATCHER = True


def suppress_coarse_duplicate() -> None:
    """Skip this process's automatic COARSE fire record (issue #787).

    A standalone hook that calls `record_session_fire` directly already has
    a RICH record for this invocation. Letting `@fail_open`'s coarse
    fallback also fire afterwards does not just double-count — when the
    hook's real decision is "block"/"ask" (signaled via a stdout JSON
    `permissionDecision`, not exit code 2), the coarse path only ever sees
    rc=0 and always records "pass". `aggregate_fires()` in
    skills/bypass-review/bypass-review sums both records unconditionally, so
    one deny becomes `fires=2, block=1, pass=1` — corrupting the exact
    per-hook block-rate count this telemetry exists to provide.

    Sets the coarse-suppression flag directly rather than going through
    `mark_dispatcher_process`: this process is NOT the dispatcher, and
    claiming so also suppressed its own rich write (issue #1199 review). Same
    process-local, one-shot, never-leaks-across-invocations guarantee. Call
    only after the RICH record has actually been written, not on every
    invocation of the hook.
    """
    global _DISPATCHER_PROCESS
    _DISPATCHER_PROCESS = True


# Days of daily telemetry files kept on disk. The ledger is append-only and had
# no sweep at all: 59 files over ~2.5 months reached 1.7 GB, growing ~680 MB a
# month (issue #1078). The sibling cache root under ~/.praxis already had a TTL
# sweep; only telemetry was missing one.
#
# 30 rather than `bypass-review`'s 7-day default window: that default is the
# report's convenience, not its limit (`-d N` takes any N), and the hook-prune
# audits in docs/ read 30-day windows. Retention shorter than the longest window
# anyone actually reads would delete the evidence those audits are scored from.
_DEFAULT_RETENTION_DAYS = 30

# All three families live in one telemetry_dir and `bypass-review fire-rate`
# joins them, so they age out together — sweeping one alone would leave the
# report showing fires with no bypasses, or fires whose `pass` counters have
# already been swept out from under them.
_SWEEPABLE_PREFIXES = ("fire-events-", "bypass-events-", "fire-counts-")
_DATED_SUFFIX = ".jsonl"


def retention_days() -> float:
    """Age past which a daily telemetry file is swept.

    `PRAXIS_TELEMETRY_RETENTION_DAYS` overrides; 0 or a malformed value keeps
    everything, which is the pre-#1078 behavior.
    """
    raw = os.environ.get("PRAXIS_TELEMETRY_RETENTION_DAYS")
    if raw is None:
        return _DEFAULT_RETENTION_DAYS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


# A day that has rolled over is compressed in place (issue #1238): the writers
# only ever append to today's file, and a finished day read back by
# `bypass-review` is 99.6% `pass` rows that gzip 20x. Archive names carry a
# hex token (`fire-events-YYYY-MM-DD.<token>.jsonl.gz`) so that a straggler
# hook recreating the plain file after the rollover gets its own archive
# instead of an append into an existing one. Every shape below is one dated
# family to the retention sweep.
_COMPRESSED_SUFFIX = ".jsonl.gz"
# Both writers open, append and close per record, so a writer that opened
# yesterday's file before the rename and appends after the copy hit EOF is a
# sub-millisecond window right at midnight. Leaving a file alone while its
# mtime is this fresh closes it: the first write of the new day that triggers
# the rollover lands within milliseconds of the last write to the old one.
_HOT_FILE_SEC = 60
_DATED_NAME = re.compile(
    r"^(?P<prefix>" + "|".join(re.escape(p) for p in _SWEEPABLE_PREFIXES) + r")"
    r"(?P<stamp>\d{4}-\d{2}-\d{2})(?P<token>\.[0-9A-Za-z_-]+)?\.jsonl(?P<gz>\.gz)?$"
)


def _file_date(name: str) -> str | None:
    """The date a sweepable telemetry file is named after, else None."""
    m = _DATED_NAME.match(name)
    if m is None:
        return None
    try:
        datetime.strptime(m.group("stamp"), "%Y-%m-%d")
    except ValueError:
        return None
    return m.group("stamp")


def _claim_for_compression(src: Path) -> Path | None:
    """Move the plain day file out from under its writers by an atomic rename.

    The tokened name is the whole synchronisation: two compressors racing on
    one day both try the rename and exactly one succeeds; the loser sees
    FileNotFoundError and skips. A hook from that day that opens the path
    *after* the rename recreates the plain file, which the next rollover
    archives under its own token — nothing is appended into a finished
    archive, so a crash at any point leaves files that are either complete
    or re-derivable.
    """
    m = _DATED_NAME.match(src.name)
    if m is None or m.group("gz"):
        return None
    if m.group("token"):
        return src  # already claimed by a compressor that did not finish
    token = f"{time.time_ns():x}{os.getpid():x}"
    claimed = src.with_name(f"{m.group('prefix')}{m.group('stamp')}.{token}.jsonl")
    try:
        st = os.lstat(src)
        if not stat.S_ISREG(st.st_mode):
            return None
        if time.time() - st.st_mtime < _HOT_FILE_SEC:
            return None  # a writer may still hold it open; tomorrow's rollover
        os.rename(src, claimed)
    except OSError:
        return None
    return claimed


def _compress_claimed(claimed: Path) -> bool:
    """`<claimed>` → `<claimed>.gz`, then remove the plain file; idempotent.

    A `.gz` that already exists was completed by an earlier run that died
    before the unlink (the archive is only ever exposed by `os.replace`), so
    the plain file is simply dropped. The archive is created with the plain
    file's mode, never the umask default.
    """
    dst = claimed.with_name(claimed.name + ".gz")
    tmp = dst.with_name(dst.name + f".{os.getpid()}.tmp")
    try:
        if not dst.exists():
            mode = stat.S_IMODE(os.lstat(claimed).st_mode) & 0o666
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            try:
                with open(claimed, "rb") as fin, os.fdopen(fd, "wb") as raw, \
                        gzip.GzipFile(fileobj=raw, mode="wb") as fout:
                    shutil.copyfileobj(fin, fout, 1024 * 1024)
                os.replace(tmp, dst)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        os.unlink(claimed)
        return True
    except OSError:
        return False


_TMP_NAME = re.compile(
    r"^(?:" + "|".join(re.escape(p) for p in _SWEEPABLE_PREFIXES) + r")"
    r"\d{4}-\d{2}-\d{2}\.[0-9a-f]+\.jsonl\.gz\.(?P<pid>\d+)\.tmp$"
)


def _reclaim_dead_tmp(entry: os.DirEntry) -> bool:
    """Remove a compressor's partial archive when its writer is gone.

    A child killed between creating the tmp and `os.replace` leaves a file no
    other name pattern matches, so nothing else would ever sweep it. The pid
    in the name says whether the owner is still writing.
    """
    m = _TMP_NAME.match(entry.name)
    if m is None:
        return False  # only this module's own tmp names, never a user's file
    pid = int(m.group("pid"))
    try:
        os.kill(pid, 0)
        return False  # still running; it will replace or unlink its own tmp
    except ProcessLookupError:
        pass
    except OSError:
        return False
    try:
        os.unlink(entry.path)
    except OSError:
        pass
    return True


def compress_telemetry(directory: Path, today: str | None = None) -> int:
    """gzip every dated telemetry file from a day before `today`; return the count.

    Today's file is left alone — it is the one every writer appends to and the
    one `count_session_fires` reads. Never raises: housekeeping must not break
    the hook that triggered it.
    """
    cutoff = today or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    done = 0
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return 0
    for entry in entries:
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            if _reclaim_dead_tmp(entry):
                continue
            stamp = _file_date(entry.name)
            if stamp is None or stamp >= cutoff or entry.name.endswith(".gz"):
                continue
            claimed = _claim_for_compression(Path(entry.path))
            if claimed is not None and _compress_claimed(claimed):
                done += 1
        except OSError:
            continue
    return done


_COMPRESS_CHILD = (
    "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
    "import _fire_ledger; _fire_ledger.compress_telemetry(Path(sys.argv[2]))"
)


def _compress_detached(directory: Path) -> bool:
    """Run `compress_telemetry` in a process the hook does not wait for.

    The first rollover after this lands has a whole retention window of plain
    files behind it (1.6 GB measured), and even a single 100 MB day costs
    0.4 s; both are more than a hook's budget. The child owns no hook fd — its
    stdio is /dev/null and it starts its own session — so the hook's exit is
    not tied to it.
    """
    try:
        subprocess.Popen(
            [sys.executable, "-c", _COMPRESS_CHILD, str(Path(__file__).parent), str(directory)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True,
        )
    except (OSError, ValueError):
        return False
    return True


_ROLLOVER_MARK = ".rollover-"
_ROLLOVER_NAME = re.compile(r"^\.rollover-\d{4}-\d{2}-\d{2}$")


def _claim_rollover(directory: Path, today: str) -> bool:
    """One compression child per directory per day.

    Every hook process that sees "today's file does not exist yet" reaches
    this edge at the same moment after midnight; without a marker each of
    them would spawn a child scanning the same directory and gzipping the
    same 100 MB files. `O_EXCL` on a dated marker makes the first one win.
    Yesterday's markers are dropped here, so the directory never accumulates
    them.
    """
    try:
        for entry in os.scandir(directory):
            if _ROLLOVER_NAME.fullmatch(entry.name) and entry.name != f"{_ROLLOVER_MARK}{today}":
                try:
                    os.unlink(entry.path)
                except OSError:
                    pass
        fd = os.open(directory / f"{_ROLLOVER_MARK}{today}", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    except OSError:
        return False
    os.close(fd)
    return True


def rotate_telemetry(directory: Path) -> tuple[bool, int]:
    """Day-rollover housekeeping: sweep old days now, compress finished days aside.

    Returns `(compression_started, removed)`. Every writer calls this at the
    same edge — the first write of a new UTC day — so the two families roll
    over together. The sweep is a handful of unlinks and stays inline; the
    compression is detached (see `_compress_detached`) and started by the
    first caller of the day only (see `_claim_rollover`).
    """
    removed = prune_telemetry(directory)
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    if not _claim_rollover(directory, today):
        return False, removed
    if _compress_detached(directory):
        return True, removed
    try:
        os.unlink(directory / f"{_ROLLOVER_MARK}{today}")  # let a later writer retry
    except OSError:
        pass
    return False, removed


def prune_telemetry(directory: Path, days: float | None = None) -> int:
    """Delete daily telemetry files older than the retention window.

    Returns the number removed. Never raises — telemetry housekeeping must not
    break the hook that triggered it, so every failure is a silent skip.

    Only files matching a known dated family are considered, so anything else a
    user or a future writer puts in this directory is left alone.
    """
    keep = retention_days() if days is None else days
    if keep <= 0:
        return 0
    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(days=keep)
    ).strftime("%Y-%m-%d")
    removed = 0
    try:
        entries = os.scandir(directory)
    except OSError:
        return 0
    with entries:
        for entry in entries:
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                # A counter file's `flock` sibling (`<name>.lock`, see
                # `_state_lock.lock_path_for`) is dated by the file it guards
                # and is swept with it. Matched here rather than in
                # `_file_date`, which the compressor also calls — a gzipped
                # lock file would be nonsense, and orphaning it is what makes
                # the sweep necessary: the counter file is renamed aside at
                # the day rollover, and its lock is left behind.
                name = entry.name
                if name.endswith(LOCK_SUFFIX):
                    name = name[: -len(LOCK_SUFFIX)]
                stamp = _file_date(name)
                if stamp is None or stamp >= cutoff:
                    continue
                os.unlink(entry.path)
                removed += 1
            except OSError:
                continue
    return removed


def _day_rollover_pending(path: Path) -> bool:
    """True while today's housekeeping has neither run nor been claimed.

    "Today's file does not exist yet" was the whole test until #1238. It is no
    longer sufficient on its own: a dispatched group whose members all `pass`
    writes no line, so the events file can stay absent for the whole day and
    every call would re-run the sweep. `rotate_telemetry` leaves a dated
    marker behind, and that marker is what says the day has already been
    handled — including by a writer of the other telemetry family.
    """
    if path.exists():
        return False
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return not (path.parent / f"{_ROLLOVER_MARK}{today}").exists()


def _atomic_append(path: Path, lines: list[str]) -> None:
    """Append `lines` as JSONL with per-line atomic writes; best-effort safe.

    - Per-line `os.write` under O_APPEND: each record (~150-250 B) is far under
      PIPE_BUF (>=4096), so concurrent writers can't tear a line (whole-line
      interleaving is harmless for line-oriented JSONL). A single joined write of
      all N members (~5 KB) WOULD exceed PIPE_BUF and risk torn lines.
    - Regular-file guard + O_NOFOLLOW: the universal fail_open path opens this on
      every hook invocation, so a FIFO/device/symlink at the target (planted, or
      via PRAXIS_FIRE_TELEMETRY_FILE) must not block or misdirect the guard.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Retention sweep (#1078), triggered by the day rolling over rather than by
    # a stamp file: the target is one file per UTC day, so "today's file does
    # not exist yet" IS the once-a-day edge, and it costs one stat on a path
    # this function is about to open anyway. Concurrent first-writers may each
    # sweep; unlink is idempotent and every error is swallowed, so a duplicate
    # sweep is wasted work, never damage.
    #
    # Ahead of the `lines` guard, because since #1238 a dispatched group whose
    # members all `pass` writes no line at all — its fires go to the counter
    # file — and that is the ordinary case, not a rare one. Leaving the sweep
    # behind the guard would hand the whole day's housekeeping to whichever
    # call happened to carry a block.
    if _day_rollover_pending(path):
        try:
            _ = rotate_telemetry(path.parent)
        except Exception:
            pass  # housekeeping never breaks the write that triggered it
    if not lines:
        return
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return  # FIFO / device / socket / symlink — refuse to write
    except FileNotFoundError:
        pass  # absent — created as a regular file below
    # O_NONBLOCK so opening a FIFO swapped in after the lstat fails fast (ENXIO)
    # instead of blocking; O_NOFOLLOW rejects a symlinked final component. Then
    # fstat the OPENED fd to close the lstat→open swap window before writing.
    flags = (os.O_WRONLY | os.O_APPEND | os.O_CREAT
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    fd = os.open(path, flags, 0o644)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return  # target swapped to a non-regular file after the lstat check
        for line in lines:
            os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


DEV_LEDGER_DIRNAME = ".praxis-dev-telemetry"


def _checkout_root() -> Path | None:
    """Return the git checkout this module lives in, or None when installed.

    Issue #934: isolation used to live only at the full-suite entrypoints
    (`scripts/run-tests.sh` exports the override, `tests/conftest.py` sets it
    per-test), so running a single shell test — `bash tests/hooks/.../test_x.sh`,
    an everyday thing while developing — wrote straight into the real ledger.
    105 of the 112 shell tests never set the override themselves, and CI always
    goes through `run-tests.sh`, so CI can never catch the leak.

    Anchoring on the module's own location fixes every entrypoint at once,
    including manual `python3 hooks/_lib/_dispatch.py` probes. An installed
    plugin is not a checkout — `installed_plugins.json` resolves praxis to
    `~/.claude/plugins/cache/praxis/praxis/<version>/`, which ships no `.git` —
    so real usage keeps writing to the real ledger, while running a hook out of
    a development checkout is by definition development and does not belong in
    production telemetry.

    Only the **package root** is inspected, never an arbitrary ancestor. This
    module sits at `<root>/hooks/_lib/_fire_ledger.py` in both layouts, so the
    root is exactly `parents[2]`. Walking further up would call an installed
    plugin a checkout whenever any ancestor happens to be a git repository —
    and `CONTRIBUTING.md` states the config dir is relocatable, so
    `~/.claude` is a default rather than a guarantee. A user whose config dir
    (or `$HOME`) lives inside a dotfiles repo would silently lose all live
    telemetry to a dev ledger.
    """
    try:
        here = Path(__file__).resolve()
    except Exception:
        return None
    parents = here.parents
    if len(parents) < 3:
        return None
    root = parents[2]
    # `.git` is a directory in a normal clone and a file in a linked worktree.
    return root if (root / ".git").exists() else None


def _praxis_home() -> Path:
    """The `~/.praxis` root, `PRAXIS_HOME`-relocated (issue #1340).

    Resolved through `_paths.praxis_home()` so the ledger follows the same
    root every other runtime file does — `PRIVACY.md` promises `PRAXIS_HOME`
    relocates the whole tree, and until #1340 this module was the one writer
    that ignored it. The import is lazy and fail-open, like the `_paths`
    import in `_hook_runtime._error_log_path`: `_paths` pulls in
    `_state_lock`, which is absent when only this file has been copied
    somewhere (the `record_fire.sh` escape-fallback probe does exactly that),
    and a telemetry write must never take a hook down. The fallback restates
    the same one-line rule rather than reverting to `~/.praxis`, so an import
    failure cannot quietly reintroduce the defect this function fixes.
    """
    try:
        from _paths import praxis_home  # type: ignore[import-not-found]
        return Path(praxis_home())
    except Exception:
        return Path(os.path.expanduser(os.environ.get("PRAXIS_HOME") or "~/.praxis"))


def resolve_telemetry_dir() -> Path:
    """Directory every praxis telemetry writer appends to.

    Shared by the fire ledger and `hooks/postuse-correction/bypass-telemetry`
    so the two families never split across directories. `bypass-review
    fire-rate` joins fire-events and bypass-events out of a *single*
    `telemetry_dir`, so a split would corrupt both sides of that report at
    once: the default view would mix production fires with development
    bypasses, and `--dir <dev>` would show fires with no bypasses at all.

    Precedence: dev checkout → `$PRAXIS_HOME/telemetry` → `~/.praxis/telemetry`.
    The checkout probe stays first: a development run is development wherever
    `PRAXIS_HOME` points, and `scripts/run-tests.sh` relies on that ordering.
    """
    checkout = _checkout_root()
    if checkout is not None:
        return checkout / DEV_LEDGER_DIRNAME
    return _praxis_home() / "telemetry"


def resolve_path() -> Path:
    """Resolve today's fire-events JSONL path.

    Precedence: `PRAXIS_FIRE_TELEMETRY_FILE` → dev checkout → real ledger
    (`$PRAXIS_HOME/telemetry`, else `~/.praxis/telemetry`).
    """
    override = os.environ.get("PRAXIS_FIRE_TELEMETRY_FILE", "").strip()
    if override:
        return Path(override)
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return resolve_telemetry_dir() / f"fire-events-{today}.jsonl"


def _extract_payload(payload_raw: str) -> tuple[str, str]:
    """Return `(session_id, tool)` from the raw JSON payload; `("", "")` on error."""
    try:
        data = json.loads(payload_raw)
    except Exception:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    sid = data.get("session_id")
    tool = data.get("tool_name")
    return (sid if isinstance(sid, str) else ""), (tool if isinstance(tool, str) else "")


# ---------------------------------------------------------------------------
# `pass` counters (issue #1238)
#
# A hook that decides `pass` is the overwhelming majority of the ledger — on
# 2026-09-05 it was 116,834 of 118,131 rows, and 100,567 of those came from
# the 55-member PreToolUse(Bash) dispatch group, which writes one row per
# member per tool call. The read path pays for all of it: `count_session_fires`
# scans today's whole plaintext file on every Stop, 115.7 ms against the
# 151 MB file the day ends at.
#
# So a `pass` is counted rather than written as its own row. The counter file
# is JSONL of the SAME record shape plus a `count` field, which is what lets
# `bypass-review` read it through the loader it already has. One file per
# (day, session): the key a reader needs is `(hook, role, session)`, and
# splitting on session means two concurrent sessions never contend for the
# same lock.
#
# Records are buffered in the process and merged once, at exit. The dispatcher
# calls `record_group_fires` one member at a time (issue #1167), so buffering
# is what turns 55 read-modify-write cycles per tool call into one. The cost
# is the crash bound: a process killed with SIGKILL loses its buffered passes.
# That is deliberate — a lost `pass` moves a fire-rate denominator, while the
# non-pass decisions every gate and audit actually reads keep writing straight
# through to the append-only ledger, one row each, unbuffered.
# ---------------------------------------------------------------------------

_COUNTS_PREFIX = "fire-counts-"
# A session id reaches this from a hook payload, so it is untrusted input on a
# path that becomes a filename. Strip it to the characters a UUID uses; an id
# that survives as empty shares one file rather than escaping the directory.
_SESSION_TOKEN_STRIP = re.compile(r"[^0-9A-Za-z_-]")
_NO_SESSION_TOKEN = "nosession"
_SESSION_TOKEN_HEAD = 40
_SESSION_TOKEN_DIGEST = 12

# session_id -> {(hook, role, granularity): {"count", "timestamp", "tool"}}
_pass_counts: dict[str, dict[tuple[str, str, str], dict]] = {}
_flush_registered = False


def _session_token(session_id: str) -> str:
    """Map a session id onto one filename component, injectively.

    The strip and the length cap are both lossy — `a/b` and `ab` reduce to the
    same head, and so do two ids sharing a 40-character prefix — while the
    file they name is what `count_session_fires` filters by `session_id`. Two
    sessions landing in one file therefore do not merely share it: the merge
    keeps the first writer's id, so the second session reads its own count as
    zero. The digest of the *original* id is what separates them; the readable
    head stays because the filename is what a human greps for.
    """
    if not session_id:
        return _NO_SESSION_TOKEN
    head = _SESSION_TOKEN_STRIP.sub("", session_id)[:_SESSION_TOKEN_HEAD]
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[
        :_SESSION_TOKEN_DIGEST
    ]
    return f"{head}-{digest}" if head else digest


def resolve_counts_path(session_id: str) -> Path:
    """Resolve today's `pass`-counter file for `session_id`.

    `PRAXIS_FIRE_TELEMETRY_FILE` redirects the *directory*, not the name: the
    counters are their own dated family, so a redirect that renamed them would
    put them outside what `bypass-review` globs and the report would read the
    non-pass rows alone. A test pointing the override at any file therefore
    gets the counters beside it, discoverable exactly as in production.
    """
    token = _session_token(session_id)
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    name = f"{_COUNTS_PREFIX}{today}.{token}.jsonl"
    override = os.environ.get("PRAXIS_FIRE_TELEMETRY_FILE", "").strip()
    if override:
        return Path(override).parent / name
    return resolve_telemetry_dir() / name


def _buffer_pass(
    hook: str, role: str, session_id: str, tool: str, granularity: str, ts: str
) -> None:
    """Accumulate one `pass` fire in the process-local buffer."""
    global _flush_registered
    bucket = _pass_counts.setdefault(session_id, {})
    row = bucket.get((hook, role, granularity))
    if row is None:
        bucket[(hook, role, granularity)] = {
            "count": 1, "timestamp": ts, "first_timestamp": ts, "tool": tool,
        }
    else:
        row["count"] += 1
        if ts > row["timestamp"]:
            row["timestamp"] = ts
        if ts < row["first_timestamp"]:
            row["first_timestamp"] = ts
        if tool:
            row["tool"] = tool
    if not _flush_registered:
        atexit.register(flush_pass_counts)
        _flush_registered = True


def _counts_key(rec: dict) -> tuple[str, str, str] | None:
    hook = rec.get("hook")
    if not isinstance(hook, str) or not hook:
        return None
    role = rec.get("role")
    gran = rec.get("granularity")
    return (
        hook,
        role if isinstance(role, str) else "",
        gran if isinstance(gran, str) else "",
    )


def read_pass_counts(path: Path) -> dict[tuple[str, str, str], dict]:
    """Load a counter file into `{(hook, role, granularity): record}`.

    Returns `{}` for a missing or unreadable file. A malformed line is dropped
    rather than aborting the load: the merge that follows would otherwise
    refuse to write, and a single torn line would then freeze every later
    count for that session.
    """
    out: dict[tuple[str, str, str], dict] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                key = _counts_key(rec)
                if key is None:
                    continue
                out[key] = rec
    except OSError:
        return out
    return out


def _merge_pass_counts(
    path: Path, session_id: str, bucket: dict[tuple[str, str, str], dict]
) -> None:
    """Add `bucket` into the counter file at `path`, atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # `_atomic_append` owns the daily sweep, and a day whose fires so far are
    # all folded passes never reaches it: `record_standalone_fire` and a folded
    # `record_session_fire` return straight after buffering. Housekeeping would
    # then wait for the first non-pass write of the day. The rollover marker is
    # shared across both telemetry families, so claiming it here is the same
    # claim that path makes — whichever family writes first does the sweep.
    if _day_rollover_pending(resolve_path()):
        try:
            _ = rotate_telemetry(path.parent)
        except Exception:
            pass  # housekeeping never breaks the write that triggered it
    with state_lock(str(path)):
        merged = read_pass_counts(path)
        for (hook, role, gran), row in bucket.items():
            rec = merged.get((hook, role, gran))
            if rec is None:
                merged[(hook, role, gran)] = {
                    "timestamp": row["timestamp"],
                    # Both ends, because a folded record is an interval, not a
                    # moment: the outcome-proxy heuristics correlate commits
                    # against when a session was active, and one timestamp for
                    # 120 folded fires would shrink a whole session's activity
                    # to an instant.
                    "first_timestamp": row["first_timestamp"],
                    "session_id": session_id,
                    "tool": row["tool"],
                    "hook": hook,
                    "role": role,
                    "decision": DECISION_PASS,
                    "granularity": gran,
                    "count": row["count"],
                }
                continue
            prior = rec.get("count")
            rec["count"] = (prior if isinstance(prior, int) else 0) + row["count"]
            if str(rec.get("timestamp") or "") < row["timestamp"]:
                rec["timestamp"] = row["timestamp"]
            earliest = str(rec.get("first_timestamp") or "")
            if not earliest or row["first_timestamp"] < earliest:
                rec["first_timestamp"] = row["first_timestamp"]
            if row["tool"]:
                rec["tool"] = row["tool"]
        payload = "".join(
            json.dumps(rec, ensure_ascii=False) + "\n" for rec in merged.values()
        )
        # os.replace, not an append: the whole file is the state, and a reader
        # holds no lock (see `_state_lock`), so it must see either every prior
        # count or every new one — never a half-rewritten file.
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise


def flush_pass_counts() -> None:
    """Merge every buffered `pass` counter into its session's file.

    Registered with `atexit` on the first buffered fire, and safe to call by
    hand — each bucket is cleared as it lands, so a manual flush followed by
    the exit-time one writes each count exactly once. Fail-open per session: a
    session whose merge raises loses only its own counts.
    """
    for session_id, bucket in list(_pass_counts.items()):
        if not bucket:
            continue
        try:
            _merge_pass_counts(resolve_counts_path(session_id), session_id, bucket)
        except Exception:
            pass  # fail-open — telemetry never breaks the hook that wrote it
        bucket.clear()


def record_group_fires(
    members, results, payload_raw: str, event: str | None = None
) -> None:
    """Append one fire record per `(member, result)` pair. Fail-open, batched.

    `members`   : list of `(role, name, impl)` from `group_members`.
    `results`   : list of `(rc, stdout, stderr)` from `run_one`, positionally aligned.
    `payload_raw`: the raw hook payload JSON (session_id / tool_name source).
    `event`     : the dispatcher's event, forwarded to `classify_decision` so
                  the Stop-lane branch is gated the same way the dispatcher
                  gates it. Omitting it reads as "unknown", never as Stop.

    One file open per call. The dispatcher calls this INCREMENTALLY — one
    member per call, right after that member resolves — so a host kill
    mid-group cannot erase the records of members already run or skipped
    (issue #1167 round-2 review; each append is O_APPEND-atomic per line).
    Passing the whole group in one call still works and batches into a single
    open.
    """
    if _disabled():
        return
    try:
        session_id, tool = _extract_payload(payload_raw)
        ts = datetime.now(tz=timezone.utc).isoformat()
        lines: list[str] = []
        for (role, name, _impl), (rc, stdout, stderr) in zip(members, results):
            decision = classify_decision(rc, stdout, stderr, event)
            if decision == DECISION_PASS:
                _buffer_pass(name, role, session_id, tool, "rich", ts)
                continue
            lines.append(json.dumps({
                "timestamp": ts,
                "session_id": session_id,
                "tool": tool,
                "hook": name,
                "role": role,
                "decision": decision,
                "granularity": "rich",
            }, ensure_ascii=False))
        _atomic_append(resolve_path(), lines)
    except Exception:
        pass  # fail-open — never break the dispatcher


def record_standalone_fire(hook: str, role: str, rc: int) -> None:
    """Append a COARSE fire record for a standalone (non-dispatched) hook.

    Called from `_hook_runtime.fail_open`, the universal decorator every hook's
    `main()` runs through — so this extends fire coverage to hooks outside the
    PreToolUse(Bash) dispatch group (Stop, UserPromptSubmit, PostToolUse,
    non-Bash PreToolUse, SessionStart).

    Deliberately NON-INVASIVE: it does not touch the hook's stdin/stdout/stderr
    (capturing them could break a live hook process), so the ONLY block signal it
    sees is the exit code. "block" here means exit-code 2.

    IMPORTANT block-coverage limitation: praxis Stop and UserPromptSubmit hooks
    block by emitting a `{"decision": "block"}` JSON on stdout while exiting 0
    (see _hook_io.py — "the caller owns the exit code"). Those blocks are
    INVISIBLE to this path and are recorded as "pass". PreToolUse standalone
    hooks do exit 2 on block, so their blocks ARE captured. ask/advise likewise
    collapse to "pass" (granularity="coarse"); session_id/tool are absent. The
    Bash group keeps full granularity via record_group_fires; this path is skipped
    inside the dispatcher process to avoid double-counting those members.
    """
    if _disabled() or _DISPATCHER_PROCESS:
        return
    try:
        ts = datetime.now(tz=timezone.utc).isoformat()
        if rc != 2:
            _buffer_pass(hook, role, "", "", "coarse", ts)
            return
        record = {
            "timestamp": ts,
            "session_id": "",
            "tool": "",
            "hook": hook,
            "role": role,
            "decision": DECISION_BLOCK,
            "granularity": "coarse",
        }
        _atomic_append(resolve_path(), [json.dumps(record, ensure_ascii=False)])
    except Exception:
        pass  # fail-open — never break the hook


def record_session_fire(
    hook: str, role: str, decision: str, session_id: str, tool: str,
    fold_pass: bool = True,
) -> bool:
    """Append a single RICH fire record with a caller-supplied session_id/tool.

    Returns True iff the rich record was actually appended, False otherwise
    (telemetry disabled, or a write error swallowed by the fail-open guard). A
    caller that suppresses its coarse @fail_open fallback after a rich record
    MUST gate the suppression on this return: suppressing after a FAILED rich
    append would drop the engagement from both streams (no rich, no coarse) —
    a silently-unrecorded fire. On False, leave the coarse fallback in place.

    `fold_pass=False` writes a `pass` as its own row instead of counting it
    (issue #1238). One caller sets it: `record_fire.sh`'s escape fallback. The
    shell writer's fast path appends the row itself with the shell's own
    `>>`, deliberately, because reaching the counter file means starting an
    interpreter — 926 shell `pass` fires a day, most of them on the Stop path
    this issue exists to make faster. The fallback must land the same record
    in the same place as the path it stands in for, or a session id carrying a
    quote would silently write to a different file than the same hook's other
    fires.

    Companion to record_standalone_fire (coarse, session_id/tool always "").
    For a standalone (non-Bash-dispatched) hook that has already parsed its own
    stdin payload and holds a real session_id, record_standalone_fire's coarse
    shape (session_id="") is unusable for per-session aggregation (e.g. issue
    #740's re-clarification-loop count, which must group fires by session).
    record_group_fires can't be reused either — it batches an entire dispatched
    Bash-group member list, which does not exist for a lone standalone hook.

    This mirrors record_group_fires' per-record shape (granularity="rich")
    without requiring dispatcher batching. A hook calling this directly still
    goes through the normal @fail_open decorator too, so a COARSE duplicate
    (session_id="") is also recorded for that hook name — callers that need
    per-session counts must filter to granularity=="rich" (see
    skills/bypass-review/bypass-review's compute_reclarification_loop_counts).

    Fail-open: any error → silently no-op (returns False), mirrors every other
    writer here.
    """
    if _disabled():
        return False
    if _IN_DISPATCHER:
        # A grouped member already gets its rich record from
        # record_group_fires, so writing a second one here counts one fire
        # twice in every per-session aggregate — and the two are identical,
        # so nothing downstream can tell them apart (issue #1199 review).
        # Returning False is safe: the coarse fallback this return gates is
        # suppressed in the dispatcher process too, so no stream loses the fire.
        # Gated on _IN_DISPATCHER, never _DISPATCHER_PROCESS: a standalone hook
        # that already called suppress_coarse_duplicate sets the latter, and
        # reading it here silently dropped that hook's own rich record.
        return False
    try:
        ts = datetime.now(tz=timezone.utc).isoformat()
        sid = session_id if isinstance(session_id, str) else ""
        tool_name = tool if isinstance(tool, str) else ""
        if decision == DECISION_PASS and fold_pass:
            # Buffered, not appended — but still True. The contract this
            # return gates is "the fire reached a stream", and a counted pass
            # has; answering False would leave the caller's coarse fallback in
            # place and record the same fire twice.
            _buffer_pass(hook, role, sid, tool_name, "rich", ts)
            return True
        record = {
            "timestamp": ts,
            "session_id": sid,
            "tool": tool_name,
            "hook": hook,
            "role": role,
            "decision": decision,
            "granularity": "rich",
        }
        _atomic_append(resolve_path(), [json.dumps(record, ensure_ascii=False)])
        return True
    except Exception:
        return False  # fail-open — never break the hook


def _raw_needle(value: str) -> str | None:
    """The quoted `value` as it appears in a record, or None if it cannot.

    Two things this must NOT do, both of which turn the prefilter from a
    necessary condition into a wrong one:

    - Include the key and its separator. `"session_id": "x"` assumes one
      writer's exact spacing, and a record written compactly (no space after
      the colon) is then skipped while it should have matched.
    - Assume the value survives JSON encoding unchanged. A quote or a
      backslash is escaped on the way in, so the raw needle misses every
      matching record. Such a value returns None and the caller parses every
      line — the slow path, never the wrong one.
    """
    if json.dumps(value, ensure_ascii=False) != f'"{value}"':
        return None
    return f'"{value}"'


def _counted_passes(hook: str, session_id: str, decision: str | None) -> int:
    """`pass` fires for `(hook, session_id)` that live in the counter file.

    Zero unless the caller is actually asking about passes — every production
    caller passes `decision="advise"` or `"block"`, which the counter file
    never holds, so the common path costs one comparison and no I/O.

    Adds this process's own unflushed buffer to the file's counts. The buffer
    is merged at exit, so a hook that counted a pass and then asked for the
    total in the same process would otherwise read a number that excludes the
    fire it had just recorded.
    """
    if decision is not None and decision != DECISION_PASS:
        return 0
    total = 0
    for rec in read_pass_counts(resolve_counts_path(session_id)).values():
        if rec.get("hook") != hook or rec.get("session_id") != session_id:
            continue
        if rec.get("granularity") != "rich":
            continue
        value = rec.get("count")
        total += value if isinstance(value, int) else 0
    for (buffered_hook, _role, gran), row in _pass_counts.get(session_id, {}).items():
        if buffered_hook == hook and gran == "rich":
            total += row["count"]
    return total


def count_session_fires(hook: str, session_id: str, decision: str | None = None) -> int:
    """Count today's RICH fire records for `(hook, session_id)`; the in-session
    read path this ledger previously lacked (issue #805).

    Every writer above is append-only: the ledger records that a hook fired but
    offers no way for a hook to ask, at its own fire time, "how many times have I
    already fired this session?" A preflight gate that repeats the same block on
    the same session is a strong signal the agent is bypassing rather than
    resolving — this read lets the gate consume that signal (escalate its
    message) instead of re-emitting an identical block. Counterpart to the
    record_* writers; read-only, never mutates the ledger.

    Filters to granularity=="rich" — only rich records carry a real session_id
    (coarse records always store ""), so a coarse record can never match a real
    session_id anyway; the explicit filter documents intent and guards a future
    coarse record that happens to carry one. When `decision` is given only
    records with that decision are counted (e.g. decision="block" to count prior
    blocks, ignoring pass/advise fires of the same hook); None counts every
    decision.

    Reads only today's file (resolve_path()) — the same daily-rotation file the
    writers append to. A session straddling UTC midnight loses its pre-midnight
    fires here; that is an accepted bound — an escalation counter resetting at
    midnight under-counts (a benign missed escalation), never over-counts (which
    would false-escalate a first-of-day block).

    Timing note for dispatched Bash-group members: the dispatcher records a
    member's fire AFTER running its main() (see _dispatch.run_group), so a gate
    calling this from inside its own main() sees only its PRIOR fires this
    session, not the in-flight one — exactly the "already blocked N times before
    now" count an escalation wants.

    Fail-open: any error (disabled, missing/unreadable file, non-regular target,
    malformed lines) → 0. A telemetry read must never break the calling hook,
    and 0 means "no escalation" — the safe default that leaves the base block
    message intact.
    """
    if _disabled():
        return 0
    if not isinstance(session_id, str) or not session_id:
        return 0
    counted = _counted_passes(hook, session_id, decision)
    try:
        path = resolve_path()
        # Mirror _atomic_append's regular-file guard: O_NONBLOCK so opening a
        # FIFO swapped in at the path fails fast (ENXIO) instead of blocking the
        # calling hook; O_NOFOLLOW rejects a symlinked final component. Then
        # fstat the opened fd to confirm a regular file before reading.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(path, flags)
        except OSError:
            return counted
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return counted
            with os.fdopen(fd, encoding="utf-8", errors="replace") as f:
                fd = -1  # ownership transferred to the file object
                count = 0
                # Substring prefilter before the parse (#1078). A matching
                # record must contain both literals, so rejecting on them is
                # exact — and it rejects in C. The ledger is overwhelmingly
                # coarse records carrying `"session_id": ""`, which is why
                # parsing every line to discard almost all of them cost 0.83s
                # against an 87 MB day file.
                # A matching record contains both values as quoted literals,
                # so rejecting on them is a necessary condition — and it
                # rejects in C. The ledger is overwhelmingly coarse records
                # carrying an empty session_id, which is why parsing every line
                # to discard almost all of them cost 0.83s against an 87 MB day
                # file. `_raw_needle` returns None for a value it cannot match
                # this way; that value falls through to the full parse.
                needle_hook = _raw_needle(hook)
                needle_session = _raw_needle(session_id)
                for line in f:
                    if needle_hook is not None and needle_hook not in line:
                        continue
                    if needle_session is not None and needle_session not in line:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(rec, dict):
                        continue
                    if rec.get("granularity") != "rich":
                        continue
                    if rec.get("hook") != hook or rec.get("session_id") != session_id:
                        continue
                    if decision is not None and rec.get("decision") != decision:
                        continue
                    count += 1
                return count + counted
        finally:
            if fd >= 0:
                os.close(fd)
    except Exception:
        return counted
