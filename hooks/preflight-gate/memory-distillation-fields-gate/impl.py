#!/usr/bin/env python3
"""PreToolUse(Write) gate: a session memory must carry its distillation fields.

Issue #1405.

The distillation convention nests three fields under `metadata:` in every
session memory — `recurrence` (how many times the pattern recurred while a rule
for it already existed), `enforcement` (`none|hook|skill|claudemd|declined|n-a`),
and `escalated_to` (where it was promoted, or `none`). The promotion queue is a
two-stage grep keyed on the nested form:

    grep -l '^  enforcement: none$' *.md | xargs -r grep -l '^  recurrence: [3-9]'

So an entry missing any of the three, or carrying them flat at the top level,
never reaches stage one. It is not rejected — it is invisible, while looking
perfectly well-formed on disk. A user-designated top-priority item sat outside
the queue by exactly this route.

Measured on one local corpus of 543 entries: 170 carry all three nested, 368
are missing at least one, 5 have them flat — 68% invisible to the queue.
Compliance over the last 7 days is 96%, so this closes a residual leak rather
than stemming a flood, and the two recent misses had none of the three fields,
meaning the convention was skipped wholesale rather than partially.

Why a block and not an advisory: the check is a presence test on three literal
keys, so it has no judgement to get wrong and costs a compliant write nothing.
The failure it prevents is silent and only observable much later, from the
absence of something in a queue nobody re-derives.

Relationship to `scripts/check-memory-frontmatter.py`: that lint checks the
*position* of a different field set (the taxonomy fields — `type`, `hookable`,
`hookKeywords`, …), never the *presence* of these three, and its own docstring
records that the memory directory is structurally absent in CI, so it prints
N/A and exits 0 there. The two do not overlap.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _memory_dir import resolve_memory_dir  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402

BYPASS_ENV = "PRAXIS_HOOK_BYPASS_MEMORY_FIELDS"

TARGET_TOOLS = frozenset({"Write"})

REQUIRED_FIELDS = ("recurrence", "enforcement", "escalated_to")

# The index files are not entries — they carry no frontmatter of their own.
EXEMPT_BASENAMES = frozenset({"MEMORY.md", "MEMORY-reference.md"})

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)


def frontmatter(text: str) -> str | None:
    """The YAML frontmatter block, or None when the file opens without one."""
    match = _FRONTMATTER_RE.match(text)
    return match.group(1) if match else None


def missing_fields(block: str) -> list[str]:
    """Required fields absent in the two-space-nested form, in canonical order."""
    return [
        field
        for field in REQUIRED_FIELDS
        if not re.search(rf"^  {field}:", block, re.MULTILINE)
    ]


def flat_fields(block: str) -> list[str]:
    """Required fields written at the top level, where the queue cannot see them."""
    return [
        field
        for field in REQUIRED_FIELDS
        if re.search(rf"^{field}:", block, re.MULTILINE)
    ]


def is_memory_entry(file_path: str) -> bool:
    """True when this Write targets an entry in the resolved memory directory.

    Resolution is by directory identity rather than by a path substring: a
    repository file that merely happens to live under some `memory/` folder is
    not governed by this convention.
    """
    if not file_path.endswith(".md"):
        return False
    if os.path.basename(file_path) in EXEMPT_BASENAMES:
        return False
    memory_dir = resolve_memory_dir()
    if not memory_dir:
        return False
    try:
        return os.path.samefile(os.path.dirname(os.path.abspath(file_path)), memory_dir)
    except OSError:
        return os.path.dirname(os.path.abspath(file_path)) == os.path.abspath(memory_dir)


def _why(missing: list[str], flat: list[str]) -> str:
    if flat:
        return (
            f"{', '.join(flat)} sits at the top level of the frontmatter. The "
            "promotion queue greps for the two-space-nested form under "
            "`metadata:`, so a flat field is invisible to it — the entry is not "
            "rejected, it simply never appears."
        )
    return (
        f"the distillation field(s) {', '.join(missing)} are absent. The "
        "promotion queue greps `^  enforcement: none$` and then "
        "`^  recurrence: [3-9]`, so an entry missing either drops out at stage "
        "one and is never reconsidered."
    )


@fail_open
def main() -> int:
    if os.environ.get(BYPASS_ENV, "").strip():
        return 0

    payload = read_payload(tool_names=TARGET_TOOLS)
    if payload is None:
        return 0  # malformed stdin or a tool this gate does not read

    tool_input = payload.get("tool_input") or {}
    file_path = (tool_input.get("file_path") or "").strip()
    content = tool_input.get("content")
    if not file_path or not isinstance(content, str):
        return 0
    if not is_memory_entry(file_path):
        return 0

    block = frontmatter(content)
    if block is None:
        missing, flat = list(REQUIRED_FIELDS), []
    else:
        missing, flat = missing_fields(block), flat_fields(block)
    if not missing and not flat:
        return 0

    emit_block(
        rule_name="memory distillation fields",
        why=_why(missing, flat),
        correct_path=(
            "put all three under `metadata:`, indented two spaces:\n"
            "    metadata:\n"
            "      type: feedback\n"
            "      recurrence: 1\n"
            "      enforcement: none\n"
            "      escalated_to: none\n"
            "  `recurrence` counts recurrences that happened while a rule "
            "already existed — the writer raises it, never a later grep. "
            "`enforcement` is one of none|hook|skill|claudemd|declined|n-a."
        ),
        bypass_env=BYPASS_ENV,
        reference=f"{Path(__file__).parent.name}/spec.md",
    )
    sys.stderr.write(f"\nBlocked file: {file_path}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
