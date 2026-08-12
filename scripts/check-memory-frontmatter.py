#!/usr/bin/env python3
"""Lint session-memory frontmatter for taxonomy-field schema drift (issue #942).

Background: this repo's on-disk session memories
(`$CLAUDE_CONFIG_DIR/projects/<slug>/memory/*.md`) nest taxonomy fields
(`type`, `originSessionId`, `hookable`, `hookKeywords`, `hookEvents`,
`modified`, `node_type`) under a `metadata:` block by convention — see
`skills/retrospect/references/stage4-execution.md`. Nesting has NO runtime
effect on `hooks/advisory-nudge/memory-hint/impl.py` (its regexes match
either position); the only reason to enforce it is cross-entry consistency,
so a reader never has to check two positions per field.

`node_type` is included in the taxonomy set for *position* checking (if
present, it must be nested and not duplicated, same as any other taxonomy
field) but is never required to be present — no hook in this repo reads it
(grep-verified). A memory missing `node_type` entirely is not drift.

Two retrospect cycles (17 -> 18) flagged the same drift family without it
ever becoming a re-checkable artifact — the finding lived only in a carried
`cycle_note` string in `.omc/state/retrospect-hygiene-cursor.json`, which has
no CI and no issue to close against. This script is the structural fix: it
runs the same check every time instead of relying on a future retrospect
scan to notice again.

Checks (one entry = one `*.md` file in the memory dir, `MEMORY.md` excluded):
  1. no taxonomy field at the top level of the frontmatter — each must live
     under `metadata:`
  2. no taxonomy field duplicated (top-level + nested, or nested twice)
  3. `hookKeywords:` / `hookEvents:`, when present, use the single-line
     bracket form (`[a, b]`). The multi-line YAML-block form (`hookKeywords:`
     followed by `- item` lines) is a FUNCTIONAL bug, not just a style
     drift: `hooks/advisory-nudge/memory-hint/impl.py`'s KEYWORDS_RE/EVENTS_RE
     read the rest of the `key:` line only, find no `[`, and silently reject
     the whole memory — `hookable: true` then never fires. Two entries in the
     corpus shipped this way (issue #942 scan): the hint had been dark since
     they were authored.

Resolution: reuses `hooks/_lib/_memory_dir.py::resolve_memory_dir()`, so this
follows `PRAXIS_MEMORY_DIR` / `CLAUDE_CONFIG_DIR` exactly like the memory-hint
hook does — no separate path convention to drift from that one.

Exit codes: 0 clean (or N/A, unless PRAXIS_TESTS_STRICT=1), 1 on any
violation (or on N/A under PRAXIS_TESTS_STRICT=1, for standalone invocation).

CI reality: the memory directory is a local, gitignored, per-user store and
does not exist in CI at all — so in CI this script always prints N/A and
exits 0 (`scripts/run-tests.sh` strips PRAXIS_TESTS_STRICT for this one call,
so N/A can never fail the CI job either). This label is deliberately NOT
"SKIPPED": `scripts/run-tests.sh`'s header explains why a skip is not a pass
(#917) — a SKIPPED line names a *fixable* gap (install the tool) that a
strict rerun can close. This condition can never close; the directory is
structurally absent in CI by design, forever. Printing it as SKIPPED would
plant a permanently-unresolvable entry in exactly the noise this repo already
learned (via #917) drowns out the *actionable* skips — the same signal-loss
failure mode issue #942 itself was opened to stop recurring. N/A keeps it
readable as "nothing to check here", not "something you could fix but
didn't". It has enforcement value locally (a contributor normalizing memory
frontmatter) and as a component this repo's own tests exercise directly
(`tests/test_check_memory_frontmatter.py`, via `PRAXIS_MEMORY_DIR`).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks" / "_lib"))
from _memory_dir import resolve_memory_dir  # type: ignore[import-not-found]  # noqa: E402

FENCE_RE = re.compile(r"^---\s*$", re.MULTILINE)
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")

# Fields this repo's convention requires nested under `metadata:`.
TAXONOMY_FIELDS = (
    "node_type",
    "type",
    "originSessionId",
    "hookable",
    "hookKeywords",
    "hookEvents",
    "modified",
)
# Fields that must use single-line `[a, b]` form — the memory-hint parser
# rejects any other shape outright (see module docstring, check 3).
BRACKET_FIELDS = ("hookKeywords", "hookEvents")


def frontmatter_block(raw: str) -> str | None:
    """Return the text between the first two `---` fences, or None."""
    matches = list(FENCE_RE.finditer(raw))
    if len(matches) < 2:
        return None
    return raw[matches[0].end() : matches[1].start()]


def _indented_lines(block: str) -> list[tuple[int, str]]:
    out = []
    for line in block.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        out.append((indent, line.strip()))
    return out


def check_file(path: Path) -> list[str]:
    """Return a list of human-readable violations for one memory file."""
    raw = path.read_text(encoding="utf-8")
    block = frontmatter_block(raw)
    if block is None:
        return ["missing `---`-fenced frontmatter block"]

    errors: list[str] = []
    # field -> list of (nested: bool, raw_value: str)
    occurrences: dict[str, list[tuple[bool, str]]] = {}
    in_metadata = False

    for indent, line in _indented_lines(block):
        m = KEY_RE.match(line)
        if not m:
            continue  # e.g. a `- item` list line, or free-text
        key, value = m.group(1), m.group(2)
        if indent == 0:
            if key == "metadata":
                in_metadata = True
                continue
            in_metadata = False
        nested = in_metadata and indent > 0
        if key in TAXONOMY_FIELDS:
            occurrences.setdefault(key, []).append((nested, value))

    for field, occs in occurrences.items():
        if len(occs) > 1:
            errors.append(f"`{field}` appears {len(occs)} times — must appear exactly once, nested under `metadata:`")
        for nested, value in occs:
            if not nested:
                errors.append(f"`{field}` is at the top level — must nest under `metadata:`")
        if field in BRACKET_FIELDS:
            for _nested, value in occs:
                value = value.strip()
                if not value:
                    errors.append(
                        f"`{field}:` has no inline value (multi-line YAML-block `- item` form) — "
                        "the memory-hint parser rejects this shape and silently drops the entire "
                        "memory from the hint index; use single-line `[a, b]` form"
                    )
                elif not value.startswith("["):
                    errors.append(
                        f"`{field}: {value}` is scalar form, not `[a, b]` — the memory-hint parser "
                        "rejects this shape and silently drops the entire memory from the hint index"
                    )

    return errors


def iter_memory_files(memory_dir: str) -> list[Path]:
    return sorted(p for p in Path(memory_dir).glob("*.md") if p.name != "MEMORY.md")


def main() -> int:
    strict = os.environ.get("PRAXIS_TESTS_STRICT", "0") == "1"

    memory_dir = resolve_memory_dir()
    if not memory_dir:
        print("N/A: memory-frontmatter-lint (memory directory not found — expected in CI / a fresh checkout, not a fixable gap, see issue #942)")
        return 1 if strict else 0

    files = iter_memory_files(memory_dir)
    if not files:
        print(f"N/A: memory-frontmatter-lint (no memory entries under {memory_dir})")
        return 1 if strict else 0

    total_errors = 0
    for path in files:
        errors = check_file(path)
        if errors:
            total_errors += len(errors)
            print(f"FAIL: {path.name}")
            for err in errors:
                print(f"  - {err}")

    if total_errors:
        print(f"\n{total_errors} frontmatter schema violation(s) across {len(files)} memory entries in {memory_dir}")
        return 1

    print(f"OK: {len(files)} memory entries in {memory_dir} — frontmatter schema clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
