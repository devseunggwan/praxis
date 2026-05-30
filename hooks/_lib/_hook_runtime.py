"""Fail-open entrypoint guard for praxis PreToolUse gate hooks (issue #498).

Every blocking PreToolUse gate must honor the fail-open contract documented in
ETHOS.md ("Fail-open on infrastructure errors") and `_hook_io.py`: *a hook
never breaks a normal session.* A hook that escapes with an uncaught exception
exits non-zero, which the Claude Code runtime treats as a block — so a stray
``MemoryError``, a ``RecursionError`` from catastrophic regex backtracking, or
an unexpected ``UnicodeError`` deep in the gate logic would silently block a
legitimate ``git commit`` / ``gh issue create``.

Before this module the guard was hand-rolled as a byte-identical
``def main(): try: return _main_inner() except Exception: return 0`` wrapper in
each gate. That is the exact duplication issue #470 eliminated for the
decision-emit shape (and #439 for the block message) — `fail_open` is its
sibling for the entrypoint guard: the contract lives at a single edit site with
a single behavioral test (`tests/test_hook_runtime.sh`).

**Fail-open is NOT fail-silent.** Returning 0 on an unexpected exception keeps
the session alive, but a swallowed crash with no trace is undiagnosable — the
hook would appear to "work" while silently never enforcing its rule. So every
swallowed exception is recorded to an append-only JSONL error log
(``PRAXIS_HOOK_ERROR_LOG`` env override; default the host-neutral
``~/.praxis/logs/hook-errors.jsonl`` via ``_paths``, which itself falls back to
``${TMPDIR:-/tmp}/praxis-hook-errors.jsonl`` when the home dir is not writable)
with timestamp, hook identity, exception type/message, full traceback, and pid.
The recorder is itself fully self-guarded — a logging failure can never
re-break the fail-open contract. Set ``PRAXIS_HOOK_ERROR_STDERR=1`` to
additionally surface a terse one-line note on stderr (off by default so normal
sessions stay quiet).

Usage::

    from _hook_runtime import fail_open

    @fail_open
    def main() -> int:
        ...            # real gate logic; return 2 to block, 0 to allow

    if __name__ == "__main__":
        sys.exit(main())

Contract details:

- ``except Exception`` deliberately does NOT catch ``BaseException``, so
  ``KeyboardInterrupt`` / ``SystemExit`` propagate normally — only
  programming / runtime errors fail open. ``MemoryError`` and
  ``RecursionError`` are ``Exception`` subclasses and ARE intercepted.
- The wrapped function's return value is passed through untouched, so a real
  block (``return 2``) is never swallowed — only exceptions become ``0``.
- ``functools.wraps`` is applied, so a decorated entrypoint exposes
  ``main.__wrapped__``; a hook's own test asserts it opted into the guard via
  that attribute rather than re-testing the (centrally tested) behavior.

Privacy note: a traceback may incidentally contain argument values (e.g. a
command string) from local frames. The log is a local file under
``~/.praxis/logs`` (or an operator-chosen path) and is not committed — treat it
like the sibling bypass-telemetry log.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import sys
import traceback
from typing import Callable


# ---------------------------------------------------------------------------
# Error logging (stdlib `logging` with a JSONL formatter)
# ---------------------------------------------------------------------------
#
# A dedicated, non-propagating logger writes one JSON object per line to the
# error log. The setup is hardened so logging can NEVER pollute the session or
# break fail-open:
#   - ``logging.raiseExceptions = False`` so a handler error (disk full, etc.)
#     is swallowed instead of dumping a traceback to stderr.
#   - ``logger.propagate = False`` so records never bubble to the root logger.
#   - a baseline ``NullHandler`` so the logger is never "handler-less" — that
#     would otherwise trigger ``logging.lastResort`` which writes to stderr.
#   - the whole record path is additionally wrapped in ``try/except: pass``.
_LOGGER_NAME = "praxis.hook"


class _JsonlFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object (JSONL)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "hook": getattr(record, "hook", "<unknown>"),
            "pid": record.process,
            "exc_type": getattr(record, "exc_type", ""),
            "message": record.getMessage(),
            "traceback": getattr(record, "tb_text", ""),
        }
        return json.dumps(payload, ensure_ascii=False)


class _StderrFormatter(logging.Formatter):
    """Terse one-line human-readable form for the opt-in stderr handler."""

    def format(self, record: logging.LogRecord) -> str:
        return (
            f"[praxis:hook-error] {getattr(record, 'hook', '?')} swallowed "
            f"{getattr(record, 'exc_type', '?')}: {record.getMessage()} "
            f"(fail-open; see {getattr(record, 'log_path', '')})"
        )


def _get_logger() -> logging.Logger:
    """Lazily configure and return the praxis hook-error logger.

    Configured once per process; only invoked on the crash path (inside the
    fail-open ``except``), so a normal run pays no logging cost.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    if getattr(logger, "_praxis_configured", False):
        return logger
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    logger.addHandler(logging.NullHandler())  # never handler-less → no lastResort
    try:
        fh = logging.FileHandler(_error_log_path(), encoding="utf-8", delay=True)
        fh.setFormatter(_JsonlFormatter())
        logger.addHandler(fh)
    except Exception:
        pass  # unwritable log must not re-break fail-open
    if os.environ.get("PRAXIS_HOOK_ERROR_STDERR"):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(_StderrFormatter())
        logger.addHandler(sh)
    logger._praxis_configured = True  # type: ignore[attr-defined]
    return logger


def _error_log_path() -> str:
    """Resolve the JSONL error-log path.

    ``PRAXIS_HOOK_ERROR_LOG`` wins (explicit operator override). Otherwise the
    host-neutral ``~/.praxis/logs/hook-errors.jsonl`` via ``_paths`` (which
    falls back to ``${TMPDIR}/praxis-hook-errors.jsonl`` when the home dir is
    not writable). The import is guarded so a missing/broken ``_paths`` can
    never break the recorder.
    """
    override = os.environ.get("PRAXIS_HOOK_ERROR_LOG")
    if override:
        return override
    try:
        from _paths import resolve_writable
        return resolve_writable("logs", "hook-errors.jsonl")
    except Exception:
        tmp = os.environ.get("TMPDIR") or "/tmp"
        return os.path.join(tmp, "praxis-hook-errors.jsonl")


def _hook_identity(fn: Callable[[], int]) -> str:
    """Best-effort hook name from the wrapped function's source file.

    Hook entrypoints live at ``hooks/<role>/<name>/impl.py``, so the parent
    directory name is the stable hook identifier.
    """
    try:
        path = fn.__code__.co_filename
        parent = os.path.basename(os.path.dirname(path))
        return parent or fn.__name__
    except Exception:
        return getattr(fn, "__name__", "<unknown>")


def _record_swallowed_exception(fn: Callable[[], int]) -> None:
    """Record a swallowed exception so the crash stays diagnosable.

    Emits one JSONL record via the dedicated ``praxis.hook`` logger (plus a
    terse stderr line when ``PRAXIS_HOOK_ERROR_STDERR`` is set). Fully
    self-guarded: a handler error is swallowed by ``raiseExceptions = False``
    and the whole body by ``try/except: pass`` — observability must NEVER
    re-break the fail-open contract.
    """
    try:
        logging.raiseExceptions = False  # handler errors must not hit stderr
        exc_type, exc, tb = sys.exc_info()
        _get_logger().error(
            str(exc),
            extra={
                "hook": _hook_identity(fn),
                "exc_type": getattr(exc_type, "__name__", str(exc_type)),
                "tb_text": "".join(traceback.format_exception(exc_type, exc, tb)),
                "log_path": _error_log_path(),
            },
        )
    except Exception:
        # Last-resort: the recorder itself never propagates.
        pass


def fail_open(fn: Callable[[], int]) -> Callable[[], int]:
    """Wrap a hook entrypoint so any uncaught ``Exception`` returns 0 (allow).

    On a swallowed exception the error is recorded (see
    ``_record_swallowed_exception``) before returning 0, so fail-open does not
    become fail-silent. Only exceptions are intercepted; the wrapped function's
    own exit code (0 allow / 2 block) is returned unchanged, and
    ``BaseException`` (KeyboardInterrupt / SystemExit) still propagates.
    """
    @functools.wraps(fn)
    def wrapper() -> int:
        try:
            return fn()
        except Exception:
            _record_swallowed_exception(fn)
            return 0

    return wrapper
