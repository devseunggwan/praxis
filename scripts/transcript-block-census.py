#!/usr/bin/env python3
"""Print the assistant content-block census for a Claude Code transcript.

Re-measurement helper for ``RUNTIME_CONSTRAINTS.md`` entry 11 (issue #1502):
several hooks read assistant prose by keeping only ``type == "text"`` blocks,
so whether the host records a mid-turn note as ``text`` or as a ``thinking``
block with non-empty text decides whether those hooks see it. Run this on a
fresh transcript after a host upgrade and compare with the entry.

Usage::

    scripts/transcript-block-census.py [--include-sidechain] <transcript.jsonl>

Reads the JSONL transcript line by line and counts the content blocks of every
``type == "assistant"`` line. Claude Code writes one content block per line,
with the lines of one API message sharing ``message.id``; each line is counted
on its own, with no dedupe. A line whose ``message.content`` is a plain string
counts as one ``text`` block.

Sidechain lines (``isSidechain: true``, written by subagents) are skipped by
default, matching the ``isSidechain`` filter most of the hook readers listed in
entry 11 apply; the report prints how many were skipped. Pass
``--include-sidechain`` to count them too.

A missing or ``null`` ``message.stop_reason`` is folded into the ``none`` key
of both ``stop_reason`` breakdowns.

Report-only: prints the census and exits 0. An unreadable file exits 2.
Stdlib only.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path


def census(lines: Iterable[str], *, include_sidechain: bool = False) -> dict:
    """Count assistant content blocks in transcript ``lines``.

    ``lines`` is any iterable of JSONL lines, such as an open text file. Do not
    build it with ``str.splitlines()``: that also splits on U+2028, U+2029 and
    U+0085, which JSON may carry unescaped inside a string, and would cut one
    record into unparseable pieces.

    Assistant lines with a truthy ``isSidechain`` are skipped and counted in
    ``sidechain_lines`` unless ``include_sidechain`` is true. A missing or
    ``null`` ``stop_reason`` is keyed as ``"none"``.
    """
    by_type: Counter[str] = Counter()
    text_by_stop: Counter[str] = Counter()
    thinking_nonempty_by_stop: Counter[str] = Counter()
    thinking_nonempty = 0
    assistant_lines = 0
    sidechain_lines = 0
    skipped = 0
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(ev, dict) or ev.get("type") != "assistant":
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        if ev.get("isSidechain") and not include_sidechain:
            sidechain_lines += 1
            continue
        assistant_lines += 1
        stop = str(msg.get("stop_reason") or "none")
        content = msg.get("content")
        if isinstance(content, str):
            blocks: list = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = content
        else:
            continue
        for b in blocks:
            if not isinstance(b, dict):
                continue
            btype = str(b.get("type") or "unknown")
            by_type[btype] += 1
            if btype == "text":
                text_by_stop[stop] += 1
            elif btype == "thinking":
                body = b.get("thinking")
                if isinstance(body, str) and body.strip():
                    thinking_nonempty += 1
                    thinking_nonempty_by_stop[stop] += 1
    return {
        "assistant_lines": assistant_lines,
        "sidechain_lines": sidechain_lines,
        "skipped_lines": skipped,
        "blocks_by_type": dict(sorted(by_type.items())),
        "thinking_nonempty": thinking_nonempty,
        "thinking_nonempty_by_stop_reason": dict(sorted(thinking_nonempty_by_stop.items())),
        "text_by_stop_reason": dict(sorted(text_by_stop.items())),
    }


def render(result: dict) -> str:
    def pairs(d: dict) -> str:
        return ", ".join(f"{k}={v}" for k, v in d.items()) or "(none)"

    thinking_total = result["blocks_by_type"].get("thinking", 0)
    return "\n".join([
        f"assistant lines: {result['assistant_lines']}",
        f"sidechain lines skipped: {result['sidechain_lines']}",
        f"skipped (unparseable) lines: {result['skipped_lines']}",
        f"blocks by type: {pairs(result['blocks_by_type'])}",
        f"thinking with non-empty text: {result['thinking_nonempty']} of {thinking_total}",
        "thinking with non-empty text by stop_reason: "
        f"{pairs(result['thinking_nonempty_by_stop_reason'])}",
        f"text blocks by stop_reason: {pairs(result['text_by_stop_reason'])}",
    ])


def main(argv: list[str]) -> int:
    args = argv[1:]
    include_sidechain = "--include-sidechain" in args
    args = [a for a in args if a != "--include-sidechain"]
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0])
        print(f"usage: {Path(argv[0]).name} [--include-sidechain] <transcript.jsonl>")
        print("  --include-sidechain  also count assistant lines with isSidechain: true"
              " (skipped by default)")
        return 0 if len(args) == 1 else 2
    try:
        with open(args[0], encoding="utf-8", errors="replace") as fh:
            result = census(fh, include_sidechain=include_sidechain)
    except OSError as exc:
        print(f"error: cannot read {args[0]}: {exc}", file=sys.stderr)
        return 2
    print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
