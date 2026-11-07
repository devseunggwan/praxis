"""Incremental transcript sync and the transcript cache sweep (#1633).

`sync_transcript` appends only the rows newer than its last sync when every
earlier row is unchanged, and rebuilds the file otherwise. The invariant every
test here checks: after any sequence of `state.db` changes, the synced file is
byte-for-byte what a fresh full rebuild of the same rows writes. The append
path must also keep the file's inode, so the gates' resumable scan cursors
stay valid, and a rebuild must change it, so no cursor survives a rewrite.

Run: python3 -m pytest tests/hooks/_lib/test_hermes_transcript_sync.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
LIB_DIR = REPO_ROOT / "hooks" / "_lib"
sys.path.insert(0, str(LIB_DIR))  # the bridge puts `_lib` on the path, which gives `_hermes` its real lock


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


hermes = _load("_hermes_transcript_sync", LIB_DIR / "_hermes.py")
base = _load("test_hermes_base_for_transcript_sync", HERE / "test_hermes.py")


def test_real_state_lock_is_loaded():
    assert hermes.state_lock.__module__ == "_state_lock"


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
                 "content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, active INTEGER DEFAULT 1, "
                 "display_metadata TEXT, display_kind TEXT)")
    conn.commit()
    conn.close()
    return db


def _sql(db: Path, sql: str, *params) -> None:
    conn = sqlite3.connect(db)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _add(db: Path, role: str, content: str = "", tool_call_id=None, tool_calls=None, tool_name=None,
         display_metadata=None, display_kind=None) -> None:
    _sql(db, "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls, tool_name, "
             "display_metadata, display_kind) VALUES ('s1', ?, ?, ?, ?, ?, ?, ?)",
         role, content, tool_call_id, tool_calls and json.dumps(tool_calls), tool_name, display_metadata,
         display_kind)


def _turn(db: Path, n: int, result: str = '{"output": "", "exit_code": 0}') -> None:
    """One user prompt, one assistant terminal call, its result."""
    _add(db, "user", f"prompt {n}")
    _add(db, "assistant", f"text {n}", tool_calls=[base._call(f"c{n}", "terminal", {"command": f"echo {n}"})])
    _add(db, "tool", result, tool_call_id=f"c{n}", tool_name="terminal")


_fresh_dirs = iter(range(10**6))


def _fresh(db: Path, tmp_path: Path) -> bytes:
    """What a sync with no prior state writes for the same rows."""
    out = hermes.sync_transcript("s1", tmp_path / f"fresh{next(_fresh_dirs)}", db)
    return out.read_bytes()


def _sync(db: Path, tmp_path: Path) -> tuple:
    out = hermes.sync_transcript("s1", tmp_path / "t", db)
    return out, out.stat().st_ino, out.read_bytes()


def test_new_rows_are_appended_to_the_same_file(tmp_path):
    db = _db(tmp_path)
    _turn(db, 1)
    out, ino, before = _sync(db, tmp_path)
    _turn(db, 2)
    _, ino2, after = _sync(db, tmp_path)
    assert ino2 == ino  # same inode: a resumable scan cursor still matches
    assert after.startswith(before) and len(after) > len(before)
    assert after == _fresh(db, tmp_path)
    _, ino3, again = _sync(db, tmp_path)  # nothing new: nothing written
    assert (ino3, again) == (ino, after)


def test_result_of_a_call_synced_earlier_renders_from_saved_state(tmp_path):
    # The call is synced before its result exists; the result's rendering
    # (its tool, and an error copied to every block of a two-file V4A patch)
    # comes from the names and widths saved by the earlier sync.
    db = _db(tmp_path)
    _add(db, "user", "go")
    patch = {"mode": "patch", "patch": "*** Begin Patch\n*** Update File: a.py\n-x\n+y\n"
                                       "*** Update File: b.py\n-x\n+y\n*** End Patch"}
    _add(db, "assistant", "", tool_calls=[base._call("p1", "patch", patch),
                                          base._call("q1", "clarify", {"questions": [{"question": "Merge?",
                                                                                      "choices": ["yes"]}]})])
    _, ino, _ = _sync(db, tmp_path)
    _add(db, "tool", json.dumps({"error": "blocked"}), tool_call_id="p1", tool_name="patch")
    _add(db, "tool", base._clarify_result("Merge?", "yes"), tool_call_id="q1", tool_name="clarify")
    _, ino2, after = _sync(db, tmp_path)
    assert ino2 == ino and after == _fresh(db, tmp_path)
    events = [json.loads(line) for line in after.decode().splitlines()]
    results = [b for e in events if e["type"] == "user" and isinstance(e["message"]["content"], list)
               for b in e["message"]["content"]]
    assert [b["tool_use_id"] for b in results if b.get("is_error")] == ["p1", "p1#1"]
    assert [b["tool_use_id"] for b in results if not b.get("is_error")] == ["q1"]


@pytest.mark.parametrize("change", [
    "UPDATE messages SET active = 0 WHERE id = 2",                       # rewind in the middle
    "UPDATE messages SET active = 0 WHERE id = 6",                       # the last synced row
    "UPDATE messages SET content = 'prompt X' WHERE id = 1",             # same-length in-place rewrite
    "UPDATE messages SET display_kind = 'process_complete' WHERE id = 4",
    "UPDATE messages SET display_metadata = '{\"_queued_prompt\": 1}' WHERE id = 4",
    "UPDATE messages SET tool_calls = NULL WHERE id = 5",
])
def test_a_changed_earlier_row_rebuilds_the_file(tmp_path, change):
    db = _db(tmp_path)
    _turn(db, 1)
    _turn(db, 2)
    _, ino, before = _sync(db, tmp_path)
    _sql(db, change)
    _turn(db, 3)  # a new row arrives in the same sync
    _, ino2, after = _sync(db, tmp_path)
    assert after == _fresh(db, tmp_path)
    assert after != before and not after.startswith(before)
    assert ino2 != ino  # a new inode: no reader keeps an offset into the old file


def test_a_deactivated_row_coming_back_rebuilds(tmp_path):
    db = _db(tmp_path)
    _turn(db, 1)
    _sql(db, "UPDATE messages SET active = 0 WHERE id = 2")
    _sync(db, tmp_path)
    # Undo: the row returns and another goes, so the count and the highest id match.
    _sql(db, "UPDATE messages SET active = 1 WHERE id = 2")
    _sql(db, "UPDATE messages SET active = 0 WHERE id = 3")
    _, _, after = _sync(db, tmp_path)
    assert after == _fresh(db, tmp_path)
    assert b"echo 1" in after


def test_a_queued_prompt_delivered_later_lands_where_a_full_rebuild_puts_it(tmp_path):
    db = _db(tmp_path)
    _turn(db, 1)
    _add(db, "user", "typed while busy", display_metadata='{"_queued_prompt": 1}')
    _, _, before = _sync(db, tmp_path)
    assert b"typed while busy" not in before
    # Hermes drains the queue: the marked row goes, an unmarked copy is placed after.
    _sql(db, "UPDATE messages SET active = 0 WHERE id = 4")
    _add(db, "user", "typed while busy")
    _, _, after = _sync(db, tmp_path)
    assert after == _fresh(db, tmp_path) and after.count(b"typed while busy") == 1


@pytest.mark.parametrize("damage", ["state-garbage", "state-foreign-version", "state-missing",
                                    "state-missing-key", "state-widths-not-int", "state-names-not-str",
                                    "file-missing", "file-appended", "file-truncated", "file-replaced",
                                    "file-rewritten-same-size"])
def test_a_state_that_does_not_describe_the_file_rebuilds(tmp_path, damage):
    db = _db(tmp_path)
    _turn(db, 1)
    out, _, _ = _sync(db, tmp_path)
    state = out.with_name(out.name + ".state")
    saved = json.loads(state.read_text())
    if damage == "state-garbage":
        state.write_text("{not json")
    elif damage == "state-foreign-version":
        state.write_text(json.dumps(dict(saved, version=99)))
    elif damage == "state-missing":
        state.unlink()
    elif damage == "state-missing-key":
        del saved["tail"]
        state.write_text(json.dumps(saved))
    elif damage == "state-widths-not-int":
        # The call is synced first; its failed result arrives later and is
        # rendered with the saved width.
        _add(db, "assistant", "", tool_calls=[base._call("c9", "terminal", {"command": "false"})])
        _sync(db, tmp_path)
        saved = json.loads(state.read_text())
        state.write_text(json.dumps(dict(saved, widths={"c9": "x"})))
        _add(db, "tool", json.dumps({"error": "blocked"}), tool_call_id="c9", tool_name="terminal")
    elif damage == "state-names-not-str":
        state.write_text(json.dumps(dict(saved, names={"c1": 1})))
    elif damage == "file-missing":
        out.unlink()
    elif damage == "file-appended":
        with out.open("a") as fh:
            fh.write('{"type": "user", "message": {"role": "user", "content": "injected"}}\n')
    elif damage == "file-truncated":
        out.write_bytes(out.read_bytes()[:10])
    elif damage == "file-replaced":
        # Same size, other bytes, at the same name. Some filesystems reuse the
        # freed inode for the new file, so the tail, not the inode, tells it apart.
        size = out.stat().st_size
        out.unlink()
        out.write_bytes(b"x" * (size - 1) + b"\n")
    elif damage == "file-rewritten-same-size":
        # The inode reuse above made deterministic: same inode, same size.
        size = out.stat().st_size
        with out.open("r+b") as fh:
            fh.write(b"x" * (size - 1) + b"\n")
    _turn(db, 2)
    _, _, after = _sync(db, tmp_path)
    assert after == _fresh(db, tmp_path)


def test_failed_append_write_rebuilds_in_the_same_call(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _turn(db, 1)
    _sync(db, tmp_path)
    _turn(db, 2)
    real_write = os.write
    calls = []

    def half_then_fail(fd, data):
        calls.append(fd)
        if len(calls) > 1:  # the rebuild that follows writes normally
            return real_write(fd, data)
        real_write(fd, bytes(data[: len(data) // 2]))
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(hermes.os, "write", half_then_fail)
    out = hermes.sync_transcript("s1", tmp_path / "t", db)
    monkeypatch.setattr(hermes.os, "write", real_write)
    assert out.read_bytes() == _fresh(db, tmp_path)


def test_without_the_lock_the_call_rebuilds(tmp_path, monkeypatch):
    import contextlib

    db = _db(tmp_path)
    _turn(db, 1)
    _, ino, _ = _sync(db, tmp_path)

    @contextlib.contextmanager
    def no_lock(path, timeout=None):
        yield False

    monkeypatch.setattr(hermes, "state_lock", no_lock)
    _turn(db, 2)
    _, ino2, after = _sync(db, tmp_path)
    assert ino2 != ino and after == _fresh(db, tmp_path)


def test_concurrent_syncs_never_duplicate_rows(tmp_path, monkeypatch):
    # Two calls of one session sync at once. Each render waits (briefly) for
    # the other, so without the lock both would pass the size check before
    # either writes and append the same rows twice.
    db = _db(tmp_path)
    _turn(db, 1)
    _sync(db, tmp_path)
    _turn(db, 2)
    barrier = threading.Barrier(2)
    real_render = hermes._render

    def render_together(*args):
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass  # the other call is held by the lock: render alone
        return real_render(*args)

    monkeypatch.setattr(hermes, "_render", render_together)
    errors = []

    def run():
        try:
            hermes.sync_transcript("s1", tmp_path / "t", db)
        except Exception as exc:  # noqa: BLE001 - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    monkeypatch.setattr(hermes, "_render", real_render)
    assert errors == []
    _, _, after = _sync(db, tmp_path)
    assert after == _fresh(db, tmp_path) and after.count(b'"prompt 2"') == 1


def test_empty_session_then_rows(tmp_path):
    db = _db(tmp_path)
    _add(db, "user", "other session row")
    _sql(db, "UPDATE messages SET session_id = 'other'")
    out, _, empty = _sync(db, tmp_path)
    assert empty == b""
    _turn(db, 1)
    _, _, after = _sync(db, tmp_path)
    assert after == _fresh(db, tmp_path) and after


# --- cache sweep -----------------------------------------------------------------

def test_bridge_sweeps_transcripts_past_the_cache_ttl(tmp_path, monkeypatch):
    praxis_home = tmp_path / "praxis"
    out_dir = praxis_home / "cache" / "hermes-transcripts"
    out_dir.mkdir(parents=True)
    old = time.time() - 8 * 86400
    stale = [out_dir / "gone-0000.jsonl", out_dir / "gone-0000.jsonl.state", out_dir / "x.jsonl1234.tmp"]
    for p in stale:
        p.write_text("{}\n")
        os.utime(p, (old, old))
    recent = out_dir / "kept-0000.jsonl"
    recent.write_text("{}\n")
    monkeypatch.setenv("PRAXIS_HOME", str(praxis_home))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("PRAXIS_CACHE_TTL_DAYS", raising=False)
    monkeypatch.delenv("PRAXIS_CACHE_PRUNE_INTERVAL_HOURS", raising=False)
    bridge = _load("hermes_bridge_transcript_sweep", base.BRIDGE)
    bridge._transcript("s1")  # no state.db: no transcript, the sweep still runs
    assert [p for p in stale if p.exists()] == []
    assert recent.exists() and (out_dir / ".prune-stamp").exists()
    # Throttled: a file that ages out after this sweep stays until the next day's.
    late = out_dir / "late-0000.jsonl"
    late.write_text("{}\n")
    os.utime(late, (old, old))
    bridge._transcript("s1")
    assert late.exists()
