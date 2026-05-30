"""Host-neutral path resolver for praxis runtime files.

praxis is multi-platform, so durable files live under ~/.praxis (PRAXIS_HOME
override) rather than the Claude-nested legacy default. `resolve_writable`
falls back to ${TMPDIR}/praxis-<file> when the home dir is not writable.

Only the hook-error log uses this today; migrating the other ${TMPDIR}/praxis-*
and ${PRAXIS_STATE_DIR} files is tracked in #527.
"""
from __future__ import annotations

import os


def praxis_home() -> str:
    """PRAXIS_HOME override, else ~/.praxis (expanded, not created)."""
    return os.path.expanduser(os.environ.get("PRAXIS_HOME") or "~/.praxis")


def _tmp_root() -> str:
    return os.environ.get("TMPDIR") or "/tmp"


def resolve_writable(subdir: str, filename: str) -> str:
    """Path under ~/.praxis/<subdir>/, creating it; TMPDIR fallback if
    unwritable. Never raises."""
    try:
        d = os.path.join(praxis_home(), subdir)
        os.makedirs(d, exist_ok=True)
        if os.access(d, os.W_OK):
            return os.path.join(d, filename)
    except Exception:
        pass
    return os.path.join(_tmp_root(), "praxis-" + filename)
