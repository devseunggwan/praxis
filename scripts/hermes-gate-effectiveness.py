#!/usr/bin/env python3
"""Measure what praxis gates did on a Hermes host, from its ``state.db``.

Usage::

    scripts/hermes-gate-effectiveness.py [--since-days N] [--db PATH] [--out DIR]

Opens ``$HERMES_HOME/state.db`` (default: ``.hermes/state.db`` in the home
directory) read-only and
prints, for the window:

- total tool calls and the calls the bridge matcher covers (the denominator);
- praxis interventions, split into ``self-check`` and ``block``, per gate;
- per gate, what the agent did next with the SAME tool in a LATER message:

  - ``identical``  — re-issued byte-identical (ceremony, unless the gate reads
    a file the command names: an anchor body or a ``git commit -F`` message is
    fixed in the file, not in the call);
  - ``ack-only``   — identical after stripping ``# <x>:ack`` markers (ceremony);
  - ``minor-edit`` — over 0.9 similar (usually the fix the gate asked for);
  - ``changed``    — a different call (fixed, replaced, or moved on);
  - ``abandoned``  — no later call to that tool.

Only the ``error`` field of a tool row is read: matching the whole content
would pick up file text that merely quotes a gate. Writes ``events.json`` (one
row per intervention, blocked arguments included) to ``--out``. That file holds
real commands from real sessions: keep it local and do not paste it into an
issue or PR.

Report-only; exits 0. A missing database exits 2. Stdlib only.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

BRIDGE_MATCHER = re.compile(r"^(terminal|write_file|patch|clarify|skill_view|delegate_task|mcp__.*)$")
NAME_RES = [
    re.compile(r"\[praxis:([a-z0-9-]+)\]"),
    re.compile(r"⚠️ ([A-Z0-9-]+(?: [A-Z0-9-]+)*) blocked"),
    re.compile(r"^\[([a-z0-9-]+-(?:gate|check|guard))\]"),
    re.compile(r"^(BLOCKED: [A-Za-z -]+)"),
]
ACK = re.compile(r"\s*#\s*[a-z-]+:ack[^\n\"]*")


def default_db() -> Path:
    home = os.environ.get("HERMES_HOME")
    return (Path(home).expanduser() if home else Path.home() / ".hermes") / "state.db"


def classify(err: str) -> str | None:
    """``self-check``, ``block``, or None for an error no praxis gate wrote."""
    if "[praxis self-check]" in err:
        return "self-check"
    if "praxis" in err.lower() or re.search(r"⚠️ [A-Z-]+ blocked", err) or err.startswith("BLOCKED:"):
        return "block"
    return None


def gates(err: str) -> list[str]:
    names = sorted({n for r in NAME_RES for n in r.findall(err)})
    return names or ["?"]


def norm(args: str) -> str:
    """The call's arguments with ``# <gate>:ack`` markers removed from ``command``."""
    try:
        d = json.loads(args)
    except ValueError:
        return args
    if isinstance(d, dict) and isinstance(d.get("command"), str):
        d["command"] = ACK.sub("", d["command"]).rstrip()
    return json.dumps(d, sort_keys=True, ensure_ascii=False)


def next_action(args: str, later: list[tuple[int, str, str, str]]) -> str:
    if any(x[3] == args for x in later):
        return "identical"
    if any(norm(x[3]) == norm(args) for x in later):
        return "ack-only"
    if later:
        r = max(difflib.SequenceMatcher(None, args[:4000], x[3][:4000]).ratio() for x in later[:4])
        return "minor-edit" if r > 0.9 else "changed"
    return "abandoned"


def measure(db: sqlite3.Connection, since: float) -> tuple[int, int, list[dict]]:
    """Return ``(total_calls, bridge_matched, interventions)`` for rows at or after ``since``."""
    calls: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    total = gated = 0
    for mid, sid, tc in db.execute(
            "select id, session_id, tool_calls from messages where role='assistant' "
            "and tool_calls is not null and timestamp>=? order by id", (since,)):
        try:
            lst = json.loads(tc)
        except ValueError:
            continue
        for c in lst if isinstance(lst, list) else []:
            fn = c.get("function") or {}
            name = fn.get("name") or c.get("name") or ""
            total += 1
            gated += bool(BRIDGE_MATCHER.match(name))
            calls[sid].append((mid, c.get("id"), name, fn.get("arguments") or ""))

    events = {}
    for cid, content in db.execute(
            "select tool_call_id, content from messages where role='tool' "
            "and timestamp>=? and content like '{\"error\"%'", (since,)):
        try:
            err = json.loads(content).get("error") or ""
        except (ValueError, AttributeError):
            continue
        kind = classify(err) if isinstance(err, str) else None
        if kind:
            events[cid] = {"kind": kind, "gates": gates(err), "text": err[:600]}

    rows = []
    for sid, lst in calls.items():
        for i, (mid, cid, name, args) in enumerate(lst):
            ev = events.get(cid)
            if not ev:
                continue
            # Later messages only: parallel siblings in the same message are
            # not responses to the gate.
            later = [x for x in lst[i + 1:i + 40] if x[2] == name and x[0] > mid][:12]
            rows.append(dict(ev, sid=sid, tool=name, args=args[:1500], outcome=next_action(args, later)))
    return total, gated, rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    ap.add_argument("--since-days", type=float, default=14)
    ap.add_argument("--db", type=Path, default=None,
                    help="state.db to read (default: $HERMES_HOME/state.db)")
    ap.add_argument("--out", type=Path,
                    default=Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / "praxis_eval")
    a = ap.parse_args(argv)
    db_path = a.db or default_db()
    if not db_path.is_file():
        print(f"error: no database at {db_path}", file=sys.stderr)
        return 2
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        total, gated, rows = measure(db, time.time() - a.since_days * 86400)
    finally:
        db.close()

    per_gate: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        for g in r["gates"]:
            per_gate[f"{r['kind']}|{g}"][r["outcome"]] += 1
    kinds = Counter(r["kind"] for r in rows)
    print(f"window: last {a.since_days:g} days · tool calls {total} · bridge-matched {gated} · "
          f"interventions {len(rows)} {dict(kinds)} · sessions {len({r['sid'] for r in rows})}")
    for k, c in sorted(per_gate.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"{sum(c.values()):5d}  {k:55s} {dict(c)}")
    a.out.mkdir(parents=True, exist_ok=True)
    out = a.out / "events.json"
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print("events ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
