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
sibling for the entrypoint guard: the contract now lives at a single edit site
with a single behavioral test (`tests/test_hook_runtime.sh`), instead of N
copies each carrying their own near-identical test.

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
"""
from __future__ import annotations

import functools
from typing import Callable


def fail_open(fn: Callable[[], int]) -> Callable[[], int]:
    """Wrap a hook entrypoint so any uncaught ``Exception`` returns 0 (allow).

    See the module docstring for the fail-open rationale. Only exceptions are
    intercepted; the wrapped function's own exit code (0 allow / 2 block) is
    returned unchanged, and ``BaseException`` (KeyboardInterrupt / SystemExit)
    still propagates.
    """
    @functools.wraps(fn)
    def wrapper() -> int:
        try:
            return fn()
        except Exception:
            return 0

    return wrapper
