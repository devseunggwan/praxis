#!/usr/bin/env python3
"""Replay one session's recorded ``gh pr merge`` calls against a checkout's merge gate.

Usage::

    scripts/replay-session-merges.py <praxis-root> <transcript.jsonl>
    scripts/replay-session-merges.py <praxis-root> --session <hermes-session-id>
                                     [--db PATH] [--out DIR]

Unlike ``replay-merge-gate-corpus.py --cut``, which builds its own
``gh pr merge <pr> --squash``, this feeds each REAL recorded command,
``--repo`` included, so verdicts that depend on the repo or an earlier ``cd``
reproduce. For every merge ``tool_use`` it cuts the transcript right before the
call (keeping the earlier blocks of a Hermes message and their results) and
prints ``bulk_merge_approved`` and ``_merge_escalation_reason`` (ALLOW
or the deny line). Check that every replayed verdict matches the live block
before changing code.

``--session`` rebuilds the Claude-shaped transcript from a Hermes ``state.db``
(``--db``, default ``$HERMES_HOME/state.db``) with the checkout's own
``_hermes.sync_transcript`` and copies it to ``<out>/session.jsonl`` (default:
the current directory). Keep that copy: the bridge rewrites its cached
transcript after a context compaction, and the row numbers move.

To see which step drops a merge, load the gate the same way and call its
internal steps (``_latest_ask_answer``, ``_ask_decision``, ``_pr_sections``,
``_briefing_item_count``, ``_merge_target_repo``, ``_context_repo_from_window``,
``_changed_directory_after``) on the same cut; one deny message comes from
several steps.

Report-only; exits 0, or 2 when the session has no messages in the database. Stdlib only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType

REL = "hooks/advisory-nudge/momentum-rule-retrieval-gate/impl.py"
ADVISORY_ENV = "PRAXIS_MOMENTUM_MERGE_ADVISORY"


def _import(path: str, name: str, extra_paths: list[str]) -> ModuleType:
    sys.path[:0] = extra_paths
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del sys.path[:len(extra_paths)]
    return mod


def load_gate(root: str) -> ModuleType:
    path = os.path.join(root, REL)
    return _import(path, "gate", [os.path.dirname(path), os.path.join(root, "hooks", "_lib")])


def transcript_from_session(root: str, session_id: str, out_dir: str, db: Path | None) -> str | None:
    lib = os.path.join(root, "hooks", "_lib")
    hermes = _import(os.path.join(lib, "_hermes.py"), "_hermes_replay", [lib])
    with tempfile.TemporaryDirectory() as tmp:
        p = hermes.sync_transcript(session_id, Path(tmp), db)
        if p is None or p.stat().st_size == 0:
            return None
        os.makedirs(out_dir, exist_ok=True)
        dest = os.path.join(out_dir, "session.jsonl")
        shutil.copy(p, dest)
    return dest


def cut_before(lines: list[str], i: int, k: int) -> list[str]:
    """The transcript as the gate sees it at ``PreToolUse`` of block ``k`` of row ``i``.

    A Claude Code row carries one block, so this is ``lines[:i]``. A Hermes row
    carries the whole message (prose, then every call), so the blocks before
    the gated one are kept as a trimmed copy of the row, followed by the
    results of the calls among them, which the host flushes before the gated
    call runs. The gated block, later blocks and their results are dropped.
    """
    out = lines[:i]
    entry = json.loads(lines[i])
    prior = entry["message"]["content"][:k]
    if not prior:
        return out
    out.append(json.dumps(dict(entry, message=dict(entry["message"], content=prior)),
                          ensure_ascii=False))
    ids = {b.get("id") for b in prior if isinstance(b, dict) and b.get("type") == "tool_use"}
    for line in lines[i + 1:]:
        try:
            msg = json.loads(line).get("message") or {}
        except (ValueError, AttributeError):
            continue
        if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
            break
        if not all(isinstance(b, dict) and b.get("type") == "tool_result" for b in msg["content"]):
            break
        if any(b.get("tool_use_id") in ids for b in msg["content"]):
            out.append(line)
    return out


def replay(gate: ModuleType, transcript: str) -> list[str]:
    with open(transcript, encoding="utf-8") as fh:
        lines = fh.read().rstrip("\n").split("\n")
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "prefix.jsonl")
        for i, line in enumerate(lines):
            try:
                msg = json.loads(line).get("message") or {}
            except (ValueError, AttributeError):
                continue
            if msg.get("role") != "assistant" or not isinstance(msg.get("content"), list):
                continue
            for k, b in enumerate(msg["content"]):
                if not isinstance(b, dict) or b.get("name") != "Bash":
                    continue
                cmd = (b.get("input") or {}).get("command")
                # Anywhere in the command, so `cd repo && gh pr merge …` is
                # replayed too, unchanged.
                if not (isinstance(cmd, str) and "gh pr merge" in cmd):
                    continue
                with open(prefix, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(cut_before(lines, i, k)) + "\n")
                payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "transcript_path": prefix}
                r = gate._merge_escalation_reason(payload)
                rows = (r or "").splitlines()
                verdict = "ALLOW" if r is None else "DENY  " + (rows[2] if len(rows) > 2 else r)[:90]
                out.append(f"row {i:4d} bulk={gate.bulk_merge_approved(payload)!s:5} {verdict} | {cmd[:80]}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    ap.add_argument("root", help="praxis checkout whose gate to run")
    ap.add_argument("transcript", nargs="?", help="Claude-shaped transcript JSONL")
    ap.add_argument("--session", help="Hermes session id to rebuild the transcript from")
    ap.add_argument("--db", type=Path, help="Hermes state.db (default: $HERMES_HOME/state.db)")
    ap.add_argument("--out", default=os.getcwd(), help="where --session writes session.jsonl")
    a = ap.parse_args(argv)
    if bool(a.session) == bool(a.transcript):
        ap.error("pass exactly one of <transcript.jsonl> or --session")
    os.environ.pop(ADVISORY_ENV, None)
    root = os.path.expanduser(a.root)
    if a.session:
        tr = transcript_from_session(root, a.session, a.out, a.db)
        if tr is None:
            print(f"error: no transcript for session {a.session}", file=sys.stderr)
            return 2
        print("transcript ->", tr)
    else:
        tr = os.path.expanduser(a.transcript)
    for row in replay(load_gate(root), tr):
        print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
