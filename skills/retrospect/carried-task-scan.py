#!/usr/bin/env python3
"""Stage 1.5 carried-task scan for the retrospect skill (issue #1427).

The hygiene cursor's carry-forward note is read into Stage 3 for display. A
line written there as the next pass's task therefore has no path to becoming a
finding: cycle 82 (2026-08-05) recorded `hookable:true 인 다른 메모리 전수
점검이 다음 패스 과제`, and thirteen cycles later the same defect class was
re-discovered from scratch and reported as pre-existing.

This script is the consuming step. It parses the cursor's note lines and prints
the carried tasks that carry no disposition yet, as Stage 1.5 finding
candidates. It decides nothing else: promotion, dedup against this cycle's own
findings, and the disposition write-back stay with the agent (see
`references/stage1-2-analysis.md` and `references/stage4-execution.md`).

Usage:
  carried-task-scan.py [--cursor <path>] [--format json|text]

Default cursor path: `.omc/state/retrospect-hygiene-cursor.json` under the cwd.

Output: JSON list (or one text line per task) of
  {"cycle": "82nd 2026-08-05" | null, "text": ..., "field": "note"|"cycle_note"}

Exit codes: 0 = scan ran (zero candidates is a normal 0), 2 = cursor unreadable
or malformed. A missing cursor is NOT an error — the first cycle in a project
has none — and prints an empty list.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_CURSOR = Path(".omc/state/retrospect-hygiene-cursor.json")

# Fields that hold carry-forward prose. Both shapes are in the wild: praxis
# writes `note` as a newline-joined string and `cycle_note` as this cycle's
# summary; another project's cursor has neither. A list is accepted because the
# write mandate speaks of "note lines".
NOTE_FIELDS = ("note", "cycle_note")

# A line claims the next pass. `carried:` is the explicit marker; the two
# phrases are what real cursors actually wrote before any marker existed.
_CARRIED_RE = re.compile(r"^\s*(?:[-*]\s*)?carried:|다음\s*패스|next\s+pass", re.IGNORECASE)

# The PR-anchor convention writes `Carried: #123` / `Carried: none — <reason>`
# for a merge gap. That is a different vocabulary that happens to share the
# word, and it names no task, so it never becomes a candidate.
_ANCHOR_CARRIED_RE = re.compile(r"^\s*(?:[-*]\s*)?carried:\s*(?:#\d+|none)\b", re.IGNORECASE)

# A disposition written back by Stage 4. Its presence is what stops a task from
# resurfacing; the line itself stays in the note as the record.
_DISPOSITION_RE = re.compile(r"\[carried-(?:done|skipped)\b", re.IGNORECASE)

# `[82nd 2026-08-05]` heads a cycle block; the tasks under it are sub-bullets,
# so the tag has to be carried down rather than read per line.
_CYCLE_TAG_RE = re.compile(r"^\s*\[([^\]]{3,60})\]")

MIN_TASK_CHARS = 8


def note_lines(cursor: dict[str, object]) -> list[tuple[str, str]]:
    """Every carry-forward line as `(field, line)`, in file order."""
    lines: list[tuple[str, str]] = []
    for field in NOTE_FIELDS:
        value = cursor.get(field)
        if isinstance(value, str):
            parts = value.split("\n")
        elif isinstance(value, list):
            parts = [part for part in value if isinstance(part, str)]
        else:
            continue
        lines.extend((field, part) for part in parts)
    return lines


def carried_tasks(cursor: dict[str, object]) -> list[dict[str, str | None]]:
    """Carried tasks with no disposition yet, oldest first."""
    found: list[dict[str, str | None]] = []
    cycle: str | None = None
    for field, line in note_lines(cursor):
        tag = _CYCLE_TAG_RE.match(line)
        if tag:
            cycle = tag.group(1).strip()
        text = line.strip()
        if len(text) < MIN_TASK_CHARS:
            continue
        if not _CARRIED_RE.search(text):
            continue
        if _ANCHOR_CARRIED_RE.match(text) or _DISPOSITION_RE.search(text):
            continue
        found.append({"cycle": cycle, "text": text.lstrip("-* ").strip(), "field": field})
    return found


def load_cursor(path: Path) -> dict[str, object] | None:
    """The cursor as a dict, or None when there is none to read."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cursor unreadable: {path} — {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if not isinstance(data, dict):
        print(f"cursor is not a JSON object: {path}", file=sys.stderr)
        raise SystemExit(2)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cursor", default=str(DEFAULT_CURSOR))
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args()

    cursor = load_cursor(Path(args.cursor))
    tasks = carried_tasks(cursor) if cursor else []

    if args.format == "json":
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
    else:
        for task in tasks:
            print(f"[{task['cycle'] or 'cycle unknown'}] {task['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
