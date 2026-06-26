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

    @fail_open
    def main() -> int: ...
"""
from __future__ import annotations

import functools
import json
import logging
import os
import sys
import traceback
from typing import Callable

_LOGGER_NAME = "praxis.hook"


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
        fh = logging.FileHandler(_error_log_path(), encoding="utf-8", delay=True)
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
