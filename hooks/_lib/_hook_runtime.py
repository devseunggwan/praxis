"""Fail-open entrypoint guard for praxis PreToolUse gate hooks (issue #498).

A gate that escapes with an uncaught exception exits non-zero, which the
runtime reads as a block — so a stray crash would block a legitimate
git commit / gh issue create. `fail_open` wraps the entrypoint: any uncaught
Exception returns 0 (allow); BaseException still propagates; a real block
(return 2) passes through.

Not fail-silent: each swallowed exception is logged as JSONL to
PRAXIS_HOOK_ERROR_LOG (default ~/.praxis/logs/hook-errors.jsonl, TMPDIR
fallback); PRAXIS_HOOK_ERROR_STDERR=1 also prints a one-line note. The
recorder never raises and never writes to stderr on its own errors.

The log rotates by size (issue #1282): past `_ERROR_LOG_MAX_BYTES` the file is
renamed to `<name>.1` and a fresh one started, keeping one predecessor. A hook
that crashes on every tool call writes a traceback per call and, because the
whole point of the guard is that the crash is silent, nobody notices the file
growing — the same shape that let the telemetry ledger reach 1.7 GB before it
got a sweep (#1078). `PRAXIS_HOOK_ERROR_LOG_MAX_BYTES` overrides the cap; 0
disables rotation.

    @fail_open
    def main() -> int: ...
"""
from __future__ import annotations

import functools
import json
import logging
import logging.handlers
import os
import sys
import time
import traceback
from typing import Callable, Optional

_LOGGER_NAME = "praxis.hook"


# ---------------------------------------------------------------------------
# Per-member wall-clock budget (issue #1167)
# ---------------------------------------------------------------------------
# The Bash dispatch group runs ~49 members SEQUENTIALLY in one process under a
# single host-side timeout (the max member timeout — see
# scripts/build-plugin-manifests.py `_dispatcher_node`). A member that spends
# its full standalone budget on network calls starves every later member: the
# host kills the dispatcher and the rest silently never run. The dispatcher
# therefore publishes each member's share of the remaining group budget here
# (module-level state — members run in-process, imported, so no IPC is
# needed), and budget-aware members size their subprocess timeouts from it.

_MEMBER_DEADLINE: Optional[float] = None


def set_member_deadline(deadline: Optional[float]) -> None:
    """Publish the current member's wall-clock deadline (`time.monotonic()`
    reference), or clear it with None. Called only by the dispatcher around
    each member invocation — hooks themselves only read via
    `remaining_budget`."""
    global _MEMBER_DEADLINE
    _MEMBER_DEADLINE = deadline


def remaining_budget(default_sec: float) -> float:
    """Seconds left in the calling hook's wall-clock budget.

    Under the dispatcher this is the time left until the deadline it set for
    the current member (never negative). Standalone — a per-process hook
    invocation, or a direct test call — no deadline is published and
    `default_sec` (the hook's own manifest-derived budget) is returned, so
    standalone behavior is unchanged.
    """
    if _MEMBER_DEADLINE is None:
        return default_sec
    return max(0.0, _MEMBER_DEADLINE - time.monotonic())


# Floor below which spawning a subprocess probe is pointless: the fork/exec is
# guaranteed dead on arrival and only burns what little budget remains. Shared
# by the dispatcher's skip floor (_dispatch._MEMBER_SKIP_FLOOR_SEC) and every
# budget-aware hook's own probe guards (issue #1167).
MIN_SUBPROC_BUDGET_SEC = 0.5


def budgeted_deadline(self_budget_sec: float) -> float:
    """Absolute `time.monotonic()` deadline for a hook that already knows its
    own budget.

    The one expression every subprocess-spawning hook uses, so the dispatcher
    can hand out a cap shorter than a manifest timeout and be sure nobody
    overshoots it. Standalone the self-budget wins unchanged; under the
    dispatcher the remaining member budget clamps it.

    Prefer `shared_probe_deadline` when the budget is the hook's manifest
    timeout minus a spawn margin; use this directly when the hook has picked a
    smaller internal budget of its own.
    """
    return time.monotonic() + min(remaining_budget(self_budget_sec), self_budget_sec)


def shared_probe_deadline(
    manifest_timeout_sec: float, margin_sec: float = 2.0
) -> float:
    """Absolute `time.monotonic()` deadline for a hook's external probes.

    One deadline shared by every subprocess probe a hook invocation spawns, so
    their SUM is bounded by the hook's budget. Standalone that budget is the
    hook's manifest timeout minus a margin (interpreter startup + process
    spawn); under the dispatcher it clamps to the remaining member budget
    published via `set_member_deadline` (issue #1167 — a member must not
    starve the rest of the Bash group's shared node timeout).
    """
    return budgeted_deadline(manifest_timeout_sec - margin_sec)


class _JsonlFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "hook": getattr(record, "hook", "<unknown>"),
            "pid": record.process,
            "exc_type": getattr(record, "exc_type", ""),
            "message": record.getMessage(),
            "traceback": getattr(record, "tb_text", ""),
        }, ensure_ascii=False)


class _StderrFormatter(logging.Formatter):
    """Terse one-line note for the opt-in stderr handler."""

    def format(self, record: logging.LogRecord) -> str:
        return (
            f"[praxis:hook-error] {getattr(record, 'hook', '?')} swallowed "
            f"{getattr(record, 'exc_type', '?')}: {record.getMessage()} "
            f"(fail-open; see {getattr(record, 'log_path', '')})"
        )


def _get_logger() -> logging.Logger:
    """Configure the praxis.hook logger once per process (crash path only)."""
    logger = logging.getLogger(_LOGGER_NAME)
    if getattr(logger, "_praxis_configured", False):
        return logger
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    logger.addHandler(logging.NullHandler())  # never handler-less -> no lastResort->stderr
    try:
        # RotatingFileHandler with maxBytes=0 never rotates, so the disable
        # value needs no second code path. backupCount=1: the previous file is
        # kept for the record that explains the current one, nothing older.
        fh = logging.handlers.RotatingFileHandler(
            _error_log_path(),
            maxBytes=_error_log_max_bytes(),
            backupCount=1,
            encoding="utf-8",
            delay=True,
        )
        fh.setFormatter(_JsonlFormatter())
        logger.addHandler(fh)
    except Exception:
        pass
    if os.environ.get("PRAXIS_HOOK_ERROR_STDERR"):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(_StderrFormatter())
        logger.addHandler(sh)
    logger._praxis_configured = True  # type: ignore[attr-defined]
    return logger


# Bytes past which the error log is rotated. Sized for the record it holds:
# a traceback record is 1-3 KB, so the cap keeps a couple of thousand of the
# most recent crashes — more than any diagnosis reads — while bounding a
# crash-per-call hook at a few MB instead of a few GB.
_ERROR_LOG_MAX_BYTES = 5 * 1024 * 1024
_ERROR_LOG_MAX_BYTES_ENV = "PRAXIS_HOOK_ERROR_LOG_MAX_BYTES"


def _error_log_max_bytes() -> int:
    """Rotation cap for the error log; the env override wins, 0 disables.

    A malformed or negative override falls back to the default rather than
    disabling rotation — a typo must not silently reintroduce the unbounded
    growth the cap exists to stop.
    """
    raw = os.environ.get(_ERROR_LOG_MAX_BYTES_ENV)
    if raw is None:
        return _ERROR_LOG_MAX_BYTES
    try:
        value = int(raw.strip())
    except ValueError:
        return _ERROR_LOG_MAX_BYTES
    return value if value >= 0 else _ERROR_LOG_MAX_BYTES


def _error_log_path() -> str:
    """PRAXIS_HOOK_ERROR_LOG override, else ~/.praxis/logs/hook-errors.jsonl."""
    override = os.environ.get("PRAXIS_HOOK_ERROR_LOG")
    if override:
        return override
    try:
        from _paths import resolve_writable
        return resolve_writable("logs", "hook-errors.jsonl")
    except Exception:
        return os.path.join(os.environ.get("TMPDIR") or "/tmp", "praxis-hook-errors.jsonl")


def _hook_identity(fn: Callable[[], int]) -> str:
    """Hook name = parent dir of the entrypoint's source file."""
    try:
        return os.path.basename(os.path.dirname(fn.__code__.co_filename)) or fn.__name__
    except Exception:
        return getattr(fn, "__name__", "<unknown>")


def _hook_role(fn: Callable[[], int]) -> str:
    """Hook role = grandparent dir of the entrypoint (hooks/<role>/<name>/impl.py)."""
    try:
        return os.path.basename(os.path.dirname(os.path.dirname(fn.__code__.co_filename)))
    except Exception:
        return ""


def _maybe_record_fire(fn: Callable[[], int], rc: object) -> None:
    """Record a coarse fire event for a standalone hook (issue #710 coverage).

    Best-effort and isolated: any failure (missing module, I/O) is swallowed so
    the fail-open guard's contract is never affected. The recorder itself no-ops
    inside the dispatcher process and when telemetry is disabled.
    """
    try:
        _lib = os.path.dirname(os.path.abspath(__file__))
        if _lib not in sys.path:
            sys.path.insert(0, _lib)
        import _fire_ledger  # type: ignore[import-not-found]
        _fire_ledger.record_standalone_fire(
            _hook_identity(fn), _hook_role(fn), rc if isinstance(rc, int) else 0
        )
    except Exception:
        pass


def _record_swallowed_exception(fn: Callable[[], int]) -> None:
    """Log a swallowed exception as JSONL. Never raises, never leaks to stderr."""
    try:
        logging.raiseExceptions = False  # handler errors must not hit stderr
        exc_type, exc, tb = sys.exc_info()
        _get_logger().error(str(exc), extra={
            "hook": _hook_identity(fn),
            "exc_type": getattr(exc_type, "__name__", str(exc_type)),
            "tb_text": "".join(traceback.format_exception(exc_type, exc, tb)),
            "log_path": _error_log_path(),
        })
    except Exception:
        pass


def fail_open(fn: Callable[[], int]) -> Callable[[], int]:
    """Return 0 on any uncaught Exception (recording it); else pass through.

    Side-effect (issue #710): after `fn()` resolves, records an observe-only
    coarse fire event via `_maybe_record_fire`. That call never alters the
    returned rc, never raises (fully swallowed), and never touches the hook's
    stdin/stdout/stderr — the fail-open contract is unchanged.
    """
    @functools.wraps(fn)
    def wrapper() -> int:
        try:
            rc = fn()
        except SystemExit as exc:
            # sys.exit() inside main() raises SystemExit (a BaseException), which
            # bypasses the Exception clause below. Record the fire from the exit
            # code, then re-raise to preserve the hook's exit semantics.
            _maybe_record_fire(fn, exc.code)
            raise
        except Exception:
            _record_swallowed_exception(fn)
            rc = 0
        # Coarse fire telemetry (issue #710 coverage). After fn() resolves, never
        # alters rc or raises. Skipped inside the dispatcher process.
        _maybe_record_fire(fn, rc)
        return rc

    return wrapper
