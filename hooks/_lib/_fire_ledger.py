"""Fire-event telemetry: record which hooks fired and their decision (issue #710).

Companion to bypass-telemetry (which logs bypass *events*). This module logs
hook *fires* from the central PreToolUse(Bash) dispatcher (`_dispatch.py`) — the
single point that already runs every member of the hot Bash group and captures
each one's `(exit, stdout, stderr)`. One JSONL line per member per dispatched
tool call.

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
@fail_open never wraps them — all four (strike-counter across its three
events, completion-verify, retrospect-mix-check, codex-review-route) held
zero records while an audit reading this ledger scored their absence as
"never fires". They now source `_lib/record_fire.sh` and arm an EXIT trap
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
  Default:  ~/.praxis/telemetry/fire-events-YYYY-MM-DD.jsonl  (daily rotation)
  Opt-out:  PRAXIS_FIRE_TELEMETRY_DISABLE=1 → no-op

Fail-open: any error → silently no-op. Never raises into the dispatcher.
"""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def classify_decision(rc: int, stdout: str, stderr: str) -> str:
    """Map a member's `(exit, stdout, stderr)` to a fire decision.

    Mirrors `_dispatch.run_group`'s PER-MEMBER decision precedence (this is one
    member's own outcome, not the cross-member aggregate the dispatcher emits):
    deny (exit 2 / deny marker) > ask (ask marker) > advise (any stderr) > pass.
    A dispatcher budget-skip record (never actually run) is decision "skip".
    """
    if rc == 2 or _DENY_MARKER in stdout:
        return DECISION_BLOCK
    if _ASK_MARKER in stdout:
        return DECISION_ASK
    if stderr.startswith(_SKIP_MARKER):
        return DECISION_SKIP
    if stderr.strip():
        return DECISION_ADVISE
    return DECISION_PASS


def _disabled() -> bool:
    return os.environ.get("PRAXIS_FIRE_TELEMETRY_DISABLE", "").strip() == "1"


# Set True in the dispatcher process so fail_open-level recording skips the
# Bash-group members — they are already recorded richly by record_group_fires.
# Process-local: standalone hook processes never import/run the dispatcher.
_DISPATCHER_PROCESS = False


def mark_dispatcher_process() -> None:
    """Mark the current process as the dispatcher (suppresses coarse recording).

    Intentionally one-way and process-lifetime — there is no unmark. Production
    is safe because each hook (and the dispatcher) runs in its own fresh process,
    so the flag never leaks across roles. The only place it must be reset is a
    single-process test harness running both roles (see test helper).
    """
    global _DISPATCHER_PROCESS
    _DISPATCHER_PROCESS = True


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

    Reuses the dispatcher-process flag: same process-local, one-shot,
    never-leaks-across-invocations guarantee `mark_dispatcher_process`
    documents, applied here for the same reason (a richer record for this
    invocation already exists — skip the redundant coarse fallback). Call
    only after the RICH record has actually been written, not on every
    invocation of the hook.
    """
    mark_dispatcher_process()


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

# Both families live in one telemetry_dir and `bypass-review fire-rate` joins
# them, so they age out together — sweeping one alone would leave the report
# showing fires with no bypasses, or the reverse.
_SWEEPABLE_PREFIXES = ("fire-events-", "bypass-events-")
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


def _file_date(name: str) -> str | None:
    """The YYYY-MM-DD a daily telemetry filename carries, or None.

    Read from the NAME, not from mtime: these files are the record of what a
    given day did, and an mtime is changed by anything that touches the file.
    """
    for prefix in _SWEEPABLE_PREFIXES:
        if name.startswith(prefix) and name.endswith(_DATED_SUFFIX):
            stamp = name[len(prefix):-len(_DATED_SUFFIX)]
            try:
                datetime.strptime(stamp, "%Y-%m-%d")
            except ValueError:
                return None
            return stamp
    return None


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
                stamp = _file_date(entry.name)
                if stamp is None or stamp >= cutoff:
                    continue
                os.unlink(entry.path)
                removed += 1
            except OSError:
                continue
    return removed


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
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Retention sweep (#1078), triggered by the day rolling over rather than by
    # a stamp file: the target is one file per UTC day, so "today's file does
    # not exist yet" IS the once-a-day edge, and it costs one stat on a path
    # this function is about to open anyway. Concurrent first-writers may each
    # sweep; unlink is idempotent and every error is swallowed, so a duplicate
    # sweep is wasted work, never damage.
    first_write_of_the_day = not path.exists()
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
    if first_write_of_the_day:
        try:
            _ = prune_telemetry(path.parent)
        except Exception:
            pass  # housekeeping never breaks the write that triggered it


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


def resolve_telemetry_dir() -> Path:
    """Directory every praxis telemetry writer appends to.

    Shared by the fire ledger and `hooks/postuse-correction/bypass-telemetry`
    so the two families never split across directories. `bypass-review
    fire-rate` joins fire-events and bypass-events out of a *single*
    `telemetry_dir`, so a split would corrupt both sides of that report at
    once: the default view would mix production fires with development
    bypasses, and `--dir <dev>` would show fires with no bypasses at all.
    """
    checkout = _checkout_root()
    if checkout is not None:
        return checkout / DEV_LEDGER_DIRNAME
    return Path.home() / ".praxis" / "telemetry"


def resolve_path() -> Path:
    """Resolve today's fire-events JSONL path.

    Precedence: `PRAXIS_FIRE_TELEMETRY_FILE` → dev checkout → real ledger.
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


def record_group_fires(members, results, payload_raw: str) -> None:
    """Append one fire record per `(member, result)` pair. Fail-open, batched.

    `members`   : list of `(role, name, impl)` from `group_members`.
    `results`   : list of `(rc, stdout, stderr)` from `run_one`, positionally aligned.
    `payload_raw`: the raw hook payload JSON (session_id / tool_name source).

    Batched into ONE file open per dispatched tool call (the dispatcher is the
    hot path — one open for ~N members, not N opens).
    """
    if _disabled():
        return
    try:
        session_id, tool = _extract_payload(payload_raw)
        ts = datetime.now(tz=timezone.utc).isoformat()
        lines: list[str] = []
        for (role, name, _impl), (rc, stdout, stderr) in zip(members, results):
            lines.append(json.dumps({
                "timestamp": ts,
                "session_id": session_id,
                "tool": tool,
                "hook": name,
                "role": role,
                "decision": classify_decision(rc, stdout, stderr),
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
        record = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "session_id": "",
            "tool": "",
            "hook": hook,
            "role": role,
            "decision": DECISION_BLOCK if rc == 2 else DECISION_PASS,
            "granularity": "coarse",
        }
        _atomic_append(resolve_path(), [json.dumps(record, ensure_ascii=False)])
    except Exception:
        pass  # fail-open — never break the hook


def record_session_fire(hook: str, role: str, decision: str, session_id: str, tool: str) -> bool:
    """Append a single RICH fire record with a caller-supplied session_id/tool.

    Returns True iff the rich record was actually appended, False otherwise
    (telemetry disabled, or a write error swallowed by the fail-open guard). A
    caller that suppresses its coarse @fail_open fallback after a rich record
    MUST gate the suppression on this return: suppressing after a FAILED rich
    append would drop the engagement from both streams (no rich, no coarse) —
    a silently-unrecorded fire. On False, leave the coarse fallback in place.

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
    try:
        record = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "session_id": session_id if isinstance(session_id, str) else "",
            "tool": tool if isinstance(tool, str) else "",
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
            return 0
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return 0
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
                return count
        finally:
            if fd >= 0:
                os.close(fd)
    except Exception:
        return 0
