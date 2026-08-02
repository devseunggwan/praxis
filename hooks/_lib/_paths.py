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
empty so existing strike/phantom state survives the move.

#903 finished the move: the volatile ${TMPDIR}/praxis-* dedup files #527 left
behind now resolve through `resolve_writable("cache", ...)`, so PRAXIS_HOME
relocates every runtime file praxis writes. `prune_stale` supplies the janitor
TMPDIR used to provide for free. `_paths.sh` mirrors this module for pure-shell
hooks — keep the two in agreement.
"""
from __future__ import annotations

import os
import shutil
import time

_LEGACY_STATE_DIRNAME = ("~", ".claude", "state", "praxis")
_DEFAULT_CACHE_TTL_DAYS = 7.0


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


def resolve_writable(subdir: str, filename: str, session_id: str | None = None) -> str:
    """Path under ~/.praxis/<subdir>/, creating it; TMPDIR fallback if
    unwritable. Never raises.

    `session_id` names the session whose cache entries the opportunistic sweep
    must leave alone — see `prune_stale`.
    """
    try:
        d = os.path.join(praxis_home(), subdir)
        os.makedirs(d, exist_ok=True)
        if os.access(d, os.W_OK):
            if subdir == "cache":
                _ = prune_stale(d, session_id=session_id)
            return os.path.join(d, filename)
    except Exception:
        pass
    return os.path.join(_tmp_root(), "praxis-" + filename)


def legacy_tmp_path(filename: str) -> str:
    """The pre-#903 ${TMPDIR}/praxis-<filename> location for a cache entry."""
    return os.path.join(_tmp_root().rstrip("/") or "/", "praxis-" + filename)


def resolve_cache_file(filename: str, session_id: str | None = None) -> str:
    """Cache path for `filename`, adopting the pre-#903 ${TMPDIR} file if present.

    Session-scoped state (retrospect marker, session intent, dedup markers) is
    written mid-session. Upgrading praxis while such a session is live would
    otherwise strand the old file and read the new path as "no state" — for the
    retrospect marker that means the Stop gate silently stops firing, the exact
    failure #666 was opened for. The adoption is a one-time move, mirroring the
    #527 strike-state migration in strike-counter/impl.sh.

    Pass the caller's `session_id` (the same token its filename is keyed on) so
    the sweep this resolution triggers cannot delete the calling session's own
    entries — see `prune_stale`.
    """
    path = resolve_writable("cache", filename, session_id=session_id)
    legacy = legacy_tmp_path(filename)
    if legacy != path and not os.path.exists(path) and os.path.exists(legacy):
        try:
            _ = shutil.move(legacy, path)
        except (OSError, shutil.Error):
            # The move usually fails because a concurrent hook process won the
            # race and already moved it. Returning `legacy` unconditionally
            # would send this process on writing to the old location that
            # every later reader has stopped consulting.
            if os.path.exists(legacy) and not os.path.exists(path):
                return legacy
    return path


def cache_ttl_days() -> float:
    """Age past which a cache entry is swept. PRAXIS_CACHE_TTL_DAYS overrides;
    0 or a malformed value disables the sweep."""
    raw = os.environ.get("PRAXIS_CACHE_TTL_DAYS")
    if raw is None:
        return _DEFAULT_CACHE_TTL_DAYS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _belongs_to_session(name: str, session_id: str) -> bool:
    """True when cache entry `name` is keyed on `session_id`.

    Consumers all name their entries `<prefix>-<session_id><suffix>`, so the
    token is matched at a boundary rather than as a bare substring: a PPID
    fallback key like `123` would otherwise protect `md-read-history-1234.json`
    belonging to an unrelated session.
    """
    head, _sep, tail = name.rpartition("-" + session_id)
    return bool(head) and _sep != "" and (tail == "" or tail.startswith("."))


def prune_stale(
    directory: str, ttl_days: float | None = None, session_id: str | None = None
) -> int:
    """Delete entries in `directory` older than the cache TTL; return the count.

    TMPDIR had an OS janitor behind it. ~/.praxis/cache does not, and these
    files are session-keyed — one per session, forever — so the move off TMPDIR
    (#903) trades a leak-free location for one that needs sweeping. Called
    opportunistically from `resolve_writable`, which is already on the write
    path of every cache consumer. Never raises: a failed sweep must not take a
    hook down with it.

    Age is not ownership (#920). A marker is written once at the start of a
    session and then only read, so its mtime stops advancing while the session
    is very much alive; past the TTL the sweep would delete it and the gate that
    reads it goes silently quiet — the #666 failure. `session_id` is therefore
    excluded from the sweep: entries keyed on the calling session survive
    regardless of age, while other sessions' entries age out as before.
    """
    ttl = cache_ttl_days() if ttl_days is None else ttl_days
    if ttl <= 0:
        return 0

    cutoff = time.time() - ttl * 86400
    removed = 0
    try:
        entries = os.scandir(directory)
    except OSError:
        return 0

    with entries:
        for entry in entries:
            try:
                if session_id and _belongs_to_session(entry.name, session_id):
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                mtime = _newest_mtime(entry.path) if is_dir else entry.stat().st_mtime
                if mtime >= cutoff:
                    continue
                if is_dir:
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    os.unlink(entry.path)
                removed += 1
            except OSError:
                continue
    return removed


def _newest_mtime(directory: str) -> float:
    """Newest mtime among `directory` and everything below it.

    A directory's own mtime tracks only its direct entries, so the session
    trees the dedup hooks keep (`<cache>/path-probe-gate/<sid>/<key>`) look
    untouched to the parent while a live session writes into them. Judging the
    parent by its own mtime therefore deletes active state.
    """
    newest = 0.0
    try:
        newest = os.stat(directory).st_mtime
    except OSError:
        return newest
    for root, _dirs, files in os.walk(directory):
        for name in (root, *(os.path.join(root, f) for f in files)):
            try:
                newest = max(newest, os.stat(name).st_mtime)
            except OSError:
                continue
    return newest

