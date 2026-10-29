#!/usr/bin/env python3
"""Replay every recorded Hermes ``clarify`` call through an AskUserQuestion hook.

Usage::

    scripts/replay-clarify-corpus.py [--days N] [--hermes-home DIR] <hooks/<role>/<name>/impl.py>

Reads ``<hermes-home>/state.db`` and every ``<hermes-home>/profiles/*/state.db``
read-only (``--hermes-home`` defaults to ``$HERMES_HOME``, else ``.hermes`` in the
home directory).
Each ``clarify`` question becomes the PreToolUse ``AskUserQuestion`` payload the
Hermes bridge builds (``choices`` -> ``options[].label``) and is piped into the
impl; a fire is rc 2 or any stdout/stderr. Prints the totals, then one JSON
line per fire with the question tail, the labels and the user's answer (when
answered), for hand-labelling precision.

The per-fire lines quote real questions and answers: keep them local and
publish only the totals and your labels.

Exits 0 after a replay, 2 when the impl is missing. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

CLARIFY_TOOLS = ("clarify", "mcp__clarify")


def state_dbs(hermes_home: Path) -> list[Path]:
    dbs = [hermes_home / "state.db"]
    profiles = hermes_home / "profiles"
    if profiles.is_dir():
        dbs += [p / "state.db" for p in sorted(profiles.iterdir())]
    return [d for d in dbs if d.is_file()]


def payload(question: dict, cwd: str) -> dict:
    labels = [x for x in (question.get("choices") or []) if isinstance(x, str)]
    return {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
            "session_id": "replay", "cwd": cwd,
            "tool_input": {"questions": [{"question": question.get("question") or "",
                                          "options": [{"label": x} for x in labels]}]}}


def replay_db(db_path: Path, impl: str, since: float, cwd: str) -> tuple[int, list[dict]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    total, fired = 0, []
    try:
        answers: dict[str, dict] = {}
        for cid, content in con.execute(
                "select tool_call_id, content from messages where role='tool' "
                "and tool_name in (?, ?) and timestamp>?", (*CLARIFY_TOOLS, since)):
            try:
                answers[cid] = {r.get("question"): r.get("user_response")
                                for r in json.loads(content).get("responses") or []}
            except (ValueError, AttributeError, TypeError):
                pass
        for (tc,) in con.execute("select tool_calls from messages where role='assistant' "
                                 "and tool_calls like '%clarify%' and timestamp>?", (since,)):
            try:
                calls = json.loads(tc)
            except ValueError:
                continue
            for c in calls if isinstance(calls, list) else []:
                fn = c.get("function") or {}
                if fn.get("name") not in CLARIFY_TOOLS:
                    continue
                try:
                    # `arguments` is a JSON string inside the JSON row.
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    continue
                for q in args.get("questions") or []:
                    if not isinstance(q, dict):
                        continue
                    total += 1
                    pl = payload(q, cwd)
                    p = subprocess.run([sys.executable, impl], input=json.dumps(pl), text=True,
                                       capture_output=True, timeout=10)
                    if p.returncode == 2 or p.stderr.strip() or p.stdout.strip():
                        labels = [o["label"] for o in pl["tool_input"]["questions"][0]["options"]]
                        fired.append({"rc": p.returncode, "q": (q.get("question") or "")[-160:],
                                      "labels": [x[:60] for x in labels],
                                      "answer": (answers.get(c.get("id")) or {}).get(q.get("question"))})
    finally:
        con.close()
    return total, fired


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    ap.add_argument("impl", help="hook impl.py to replay against")
    ap.add_argument("--days", type=float, default=28)
    ap.add_argument("--hermes-home", type=Path, default=None,
                    help="Hermes home (default: $HERMES_HOME, else .hermes in the home directory)")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.impl):
        print(f"error: no impl at {a.impl}", file=sys.stderr)
        return 2
    env = os.environ.get("HERMES_HOME")
    home = (a.hermes_home or (Path(env) if env else Path.home() / ".hermes")).expanduser()
    since = time.time() - a.days * 86400
    total, fired = 0, []
    for db in state_dbs(home):
        t, f = replay_db(db, a.impl, since, str(Path.home()))
        total += t
        fired += f
    print(f"questions={total} fired={len(fired)} ({len(fired) / max(total, 1):.1%})")
    for row in fired:
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
