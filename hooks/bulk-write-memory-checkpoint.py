#!/usr/bin/env python3
"""PreToolUse(Edit|Write|NotebookEdit) advisory: bulk-authoring loop checkpoint.

Failure mode this guards against: bulk authoring loops (N file writes to a
shared SOT directory) collapse N decisions into 1, so memory-based retrieval
rules (e.g., "before authoring, read vault CLAUDE.md SOT") fire at task entry
but do NOT re-fire per file. Result: rule loaded, but bypassed N times.

Documented recurrences of the same Loaded-not-Retrieved pattern across the
SB-vs-team-shared-vault separation rule and the external-SOT-direct-read
protocol (3 occurrences within 5 days):
  - rule loaded in MEMORY.md USER tier (highest salience)
  - bulk write collapsed N decisions into 1 → retrieval gate fired once for the
    whole task, not per file
  - result: enum-value drift and source-attribution leak across N files

Soft advisory only — exits 0 always. Goal is to surface the reminder at the
moment a Write/Edit to a vault/SOT-flagged path occurs, so the bulk loop has
at least one retrieval surface per file. Stateful per-session counting is a
follow-up enhancement once the noise profile is observed.
"""
from __future__ import annotations

import json
import sys


# SOT-flagged path patterns (case-sensitive substring match).
# Trigger only on paths likely to be vault/skill/rule SOT.
_FLAGGED_PATTERNS: tuple[str, ...] = (
    "/laplace-wiki/",
    "/second-brain/",
    "/wiki/wiki/",
    "/wiki/entities/",
    "/wiki/concepts/",
    "/wiki/summaries/",
    "/wiki/analyses/",
    "/wiki/recipes/",
    "/wiki/playbooks/",
    "/skills/",
    "/SKILL.md",
    "/AGENTS.md",
    "/.claude/CLAUDE.md",
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Fail-open on parse failure.
        return 0

    tool_name = payload.get("tool_name", "")
    if tool_name not in {"Write", "Edit", "NotebookEdit"}:
        return 0

    file_path = payload.get("tool_input", {}).get("file_path", "")
    if not isinstance(file_path, str) or not file_path:
        return 0

    if not any(p in file_path for p in _FLAGGED_PATTERNS):
        return 0

    sys.stderr.write(
        "[advisory] Bulk-write checkpoint — SOT-flagged path detected.\n"
        f"  Path: {file_path}\n"
        "  Reminder: if this is part of an N-file authoring loop, ensure relevant\n"
        "  memory entries are retrieved per file, not just at task entry.\n"
        "  Patterns to consider:\n"
        "    feedback_external_sot_direct_read_*\n"
        "    user_*_team_shared_vs_personal*\n"
        "    feedback_inline_rules_*\n"
        "  This is a soft advisory — write proceeds normally.\n"
    )
    # Exit 0 — advisory only, never block.
    return 0


if __name__ == "__main__":
    sys.exit(main())
