#!/usr/bin/env python3
"""Replay the merge gate's decision, ``origin/main`` vs a branch, over recorded merges.

Usage::

    scripts/replay-merge-gate-corpus.py <branch-root> [--base-ref REF | --base-root DIR]
                                        [--corpus GLOB ...]
    scripts/replay-merge-gate-corpus.py <branch-root> --cut <transcript.jsonl> <row> <pr>

Corpus mode extracts ``hooks/`` at ``--base-ref`` (default ``origin/main``) of
``<branch-root>`` into a temp dir (or uses ``--base-root``, an extracted copy),
loads ``momentum-rule-retrieval-gate/impl.py`` from both sides, and for every
assistant Bash ``gh pr merge`` call in the corpus writes the transcript lines
before it to a temp file and calls ``_merge_escalation_reason`` on both sides
with the gate's advisory env var unset. It prints
``{transcripts, calls, same, deny_to_allow, allow_to_deny, errors}``, then for
every changed row the last AskUserQuestion before it (briefing item count,
question head, answer tail), so each flip can be judged by hand: was a real
briefing shown and approved?

The default corpus is ``$CLAUDE_CONFIG_DIR/projects/*/*.jsonl`` (else
``~/.claude``) plus the transcripts a Hermes host rebuilds under
``$PRAXIS_HOME/cache/hermes-transcripts/`` (else ``~/.praxis``). Pass
``--corpus`` once per glob to replace it; when several config dirs link to the
same ``projects/`` dir, pass only one of them or every row counts N times.

``--cut`` replays one row of one transcript on the branch side only and prints
ALLOW or the deny reason head, with and without a ``# briefing-surfaced``
marker. It builds its own ``gh pr merge <pr> --squash``, so it cannot reproduce
a verdict that depends on ``--repo`` or an earlier ``cd``; use
``scripts/replay-session-merges.py`` for that.

Report-only; exits 0. Stdlib only.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

REL = "hooks/advisory-nudge/momentum-rule-retrieval-gate/impl.py"
ADVISORY_ENV = "PRAXIS_MOMENTUM_MERGE_ADVISORY"


def load_gate(root: str | Path, name: str) -> ModuleType:
    """Import the merge gate's impl.py from the checkout or hooks copy at ``root``.

    The ``_lib`` modules it imports are dropped from ``sys.modules`` afterwards,
    so loading the other side imports that side's copies instead of reusing
    these; the loaded gate keeps its own references.
    """
    path = os.path.join(root, REL)
    before = set(sys.modules)
    sys.path[:0] = [os.path.dirname(path), os.path.join(root, "hooks", "_lib")]
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del sys.path[:2]
        root_abs = os.path.abspath(root) + os.sep
        for added in set(sys.modules) - before:
            f = getattr(sys.modules[added], "__file__", None) or ""
            if f and os.path.abspath(f).startswith(root_abs):
                del sys.modules[added]
    return mod


def default_corpus() -> list[str]:
    claude = os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
    praxis = os.environ.get("PRAXIS_HOME") or "~/.praxis"
    return [os.path.join(claude, "projects", "*", "*.jsonl"),
            os.path.join(praxis, "cache", "hermes-transcripts", "*.jsonl")]


def read_lines(path: str) -> list[str]:
    # Split on "\n" only: str.splitlines() also breaks on U+2028/U+2029, which
    # JSON may carry unescaped inside a string.
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read().rstrip("\n").split("\n")


def reason(gate: ModuleType, lines: list[str], cmd: str, scratch: str) -> str | None:
    p = os.path.join(scratch, "merge_gate_prefix.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return gate._merge_escalation_reason({"tool_name": "Bash", "tool_input": {"command": cmd},
                                          "transcript_path": p})


def merge_commands(line: str) -> list[str]:
    """The ``gh pr merge`` Bash commands of one assistant transcript line."""
    if "gh pr merge" not in line:
        return []
    try:
        msg = json.loads(line).get("message") or {}
    except (ValueError, AttributeError):
        return []
    if msg.get("role") != "assistant" or not isinstance(msg.get("content"), list):
        return []
    out = []
    for b in msg["content"]:
        if isinstance(b, dict) and b.get("name") == "Bash":
            cmd = (b.get("input") or {}).get("command")
            if isinstance(cmd, str) and "gh pr merge" in cmd:
                out.append(cmd)
    return out


def last_ask(gate: ModuleType, lines: list[str]) -> tuple[str, str | None]:
    q = a = None
    for line in reversed(lines):
        try:
            content = (json.loads(line).get("message") or {}).get("content")
        except (ValueError, AttributeError):
            continue
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if a is None and b.get("type") == "tool_result":
                s = b.get("content")
                s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
                if "answered your questions" in s:
                    a = s[-160:]
            if b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                q = gate._ask_question_text(b)
        if q is not None:
            return q, a
    return "", a


def deny_head(r: str) -> str:
    rows = r.splitlines()
    return (rows[2] if len(rows) > 2 else rows[0] if rows else "")[:120]


def run_cut(branch: ModuleType, transcript: str, row: int, pr: str, scratch: str) -> None:
    lines = read_lines(os.path.expanduser(transcript))[:row]
    for suffix in ("", " # briefing-surfaced: replay"):
        r = reason(branch, lines, f"gh pr merge {pr} --squash{suffix}", scratch)
        print("marker" if suffix else "plain ", "ALLOW" if r is None else deny_head(r))


def run_corpus(base: ModuleType, branch: ModuleType, files: list[str], scratch: str) -> dict:
    stats = dict(transcripts=0, calls=0, same=0, deny_to_allow=0, allow_to_deny=0, errors=0)
    changed = []
    for f in files:
        lines = read_lines(f)
        hit = False
        for i, line in enumerate(lines):
            for cmd in merge_commands(line):
                hit = True
                stats["calls"] += 1
                try:
                    a = reason(base, lines[:i], cmd, scratch)
                    c = reason(branch, lines[:i], cmd, scratch)
                except Exception as exc:  # noqa: BLE001 - a replay tallies errors, never stops
                    stats["errors"] += 1
                    changed.append(("error", f, i, repr(exc)[:80]))
                    continue
                if (a is None) == (c is None):
                    stats["same"] += 1
                    continue
                kind = "deny_to_allow" if c is None else "allow_to_deny"
                stats[kind] += 1
                changed.append((kind, f, i, cmd[:90]))
        stats["transcripts"] += hit
    print(json.dumps(stats))
    for kind, f, i, cmd in changed:
        print("=" * 80)
        print(kind, os.path.basename(f), i, "|", cmd)
        if kind == "error":
            continue
        q, ans = last_ask(branch, read_lines(f)[:i])
        print("question_items", branch._briefing_item_count(q), "| Q:", q[:300].replace("\n", " / "))
        print("A:", (ans or "")[:200].replace("\n", " "))
    return stats


def extract_hooks(root: str, ref: str, dest: str) -> None:
    archive = subprocess.run(["git", "-C", root, "archive", ref, "hooks"],
                             check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", dest], input=archive, check=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    ap.add_argument("branch_root", help="praxis checkout holding the changed gate")
    ap.add_argument("--base-ref", default="origin/main", help="ref to compare against")
    ap.add_argument("--base-root", help="already extracted base copy (skips git archive)")
    ap.add_argument("--corpus", action="append", metavar="GLOB",
                    help="transcript glob; repeat to add more (default: see module doc)")
    ap.add_argument("--cut", nargs=3, metavar=("TRANSCRIPT", "ROW", "PR"))
    a = ap.parse_args(argv)
    os.environ.pop(ADVISORY_ENV, None)
    branch_root = os.path.expanduser(a.branch_root)
    branch = load_gate(branch_root, "gate_branch")
    with tempfile.TemporaryDirectory() as scratch:
        if a.cut:
            run_cut(branch, a.cut[0], int(a.cut[1]), a.cut[2], scratch)
            return 0
        base_root = a.base_root
        if base_root is None:
            base_root = os.path.join(scratch, "base")
            os.mkdir(base_root)
            extract_hooks(branch_root, a.base_ref, base_root)
        base = load_gate(os.path.expanduser(base_root), "gate_base")
        files = sorted({f for g in (a.corpus or default_corpus())
                        for f in glob.glob(os.path.expanduser(g))})
        run_corpus(base, branch, files, scratch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
