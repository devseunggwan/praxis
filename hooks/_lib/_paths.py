"""Host-neutral path resolver for praxis runtime files.

praxis is multi-platform, so durable files live under ~/.praxis (PRAXIS_HOME
override) rather than the Claude-nested legacy default. `resolve_writable`
falls back to ${TMPDIR}/praxis-<file> when the home dir is not writable.

Layout (#527):
  ~/.praxis/state/  — durable, cross-session state (strike counter, phantom-path
                      markers). PRAXIS_STATE_DIR overrides the base (back-compat).
  ~/.praxis/cache/  — regenerable, session-scoped caches / dedup markers.
  ~/.praxis/logs/   — diagnostics (hook-errors.jsonl, bypass telemetry).

Durable state migrated off the Claude-nested ${PRAXIS_STATE_DIR:-~/.claude/state/
praxis} default reads back from `legacy_state_dir()` when the new location is
empty so existing strike/phantom state survives the move. The volatile
${TMPDIR}/praxis-* dedup files are tracked separately in #527's follow-up.
"""
from __future__ import annotations

import os

_LEGACY_STATE_DIRNAME = ("~", ".claude", "state", "praxis")


def praxis_home() -> str:
    """PRAXIS_HOME override, else ~/.praxis (expanded, not created)."""
    return os.path.expanduser(os.environ.get("PRAXIS_HOME") or "~/.praxis")


def praxis_state_dir() -> str:
    """Durable, cross-session state root.

    An explicit PRAXIS_STATE_DIR override always wins (back-compat with the
    pre-#527 convention); otherwise the host-neutral default ~/.praxis/state
    (PRAXIS_HOME-aware). Not created here.
    """
    override = os.environ.get("PRAXIS_STATE_DIR")
    if override:
        return os.path.expanduser(override)
    return os.path.join(praxis_home(), "state")


def praxis_cache_dir() -> str:
    """Volatile, regenerable, session-scoped cache root (~/.praxis/cache)."""
    return os.path.join(praxis_home(), "cache")


def legacy_state_dir() -> str:
    """The pre-#527 Claude-nested durable state root (~/.claude/state/praxis)."""
    return os.path.expanduser(os.path.join(*_LEGACY_STATE_DIRNAME))


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

