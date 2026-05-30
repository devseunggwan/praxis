"""Host-neutral path resolver for praxis runtime files.

praxis is multi-platform (Claude, Codex, Cursor, Gemini, OpenCode), so its
durable runtime files should live under a host-neutral home rather than nested
in any single host's directory (the legacy `~/.claude/state/praxis` default of
`PRAXIS_STATE_DIR` assumes Claude). This module pins that home in one place:

    ~/.praxis/
      ├── logs/    durable diagnostics (e.g. hook-errors.jsonl)
      ├── state/   durable cross-session state (e.g. strike counter)
      └── cache/   regenerable per-session caches (gh-json, dedup markers)

`PRAXIS_HOME` overrides the root. `resolve_writable` is best-effort: if the
home dir cannot be created or written (locked-down sandbox / read-only HOME),
it falls back to `${TMPDIR:-/tmp}/praxis-<filename>` so a caller never loses
its file — important for the fail-open error log, whose whole purpose is to
stay writable when things are going wrong.

Scope note: only the hook-error log is wired to this module today. Migrating
the existing `${TMPDIR}/praxis-*` ephemeral files and the
`${PRAXIS_STATE_DIR:-~/.claude/state/praxis}` persistent files onto
`~/.praxis` is tracked separately (cross-cutting, conflicts with in-flight
PRs). `PRAXIS_STATE_DIR` and per-file `PRAXIS_*` overrides remain honored for
backward compatibility until that sweep.
"""
from __future__ import annotations

import os


def praxis_home() -> str:
    """Resolve the praxis home directory (``PRAXIS_HOME`` override, else
    ``~/.praxis``). Path is expanded but NOT created here."""
    override = os.environ.get("PRAXIS_HOME")
    if override:
        return os.path.expanduser(override)
    return os.path.expanduser("~/.praxis")


def _tmp_root() -> str:
    return os.environ.get("TMPDIR") or "/tmp"


def resolve_writable(subdir: str, filename: str) -> str:
    """Return a writable path for ``<praxis_home>/<subdir>/<filename>``.

    Creates the parent directory best-effort. If the home dir cannot be
    created or is not writable (read-only HOME, locked-down sandbox), falls
    back to ``${TMPDIR:-/tmp}/praxis-<filename>`` so the caller always gets a
    usable path. Never raises.
    """
    try:
        d = os.path.join(praxis_home(), subdir)
        os.makedirs(d, exist_ok=True)
        if os.access(d, os.W_OK):
            return os.path.join(d, filename)
    except Exception:
        pass
    return os.path.join(_tmp_root(), "praxis-" + filename)
