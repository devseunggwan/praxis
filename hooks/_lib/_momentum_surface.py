"""Momentum rule surface shared by momentum-rule-retrieval-gate and merge-gate.

Both hooks print the same stderr block at a high-momentum command: the static
CLAUDE.md rule text for the trigger, then every memory entry whose frontmatter
`momentum:` list names that trigger. The merge trigger moved to merge-gate
(issue #1650); the block's shape and prefix stay as they were so the surface
reads the same wherever it comes from.
"""
from __future__ import annotations

import os
import re

PREFIX = "[praxis:momentum-gate]"
CLOSING_LINE = f"{PREFIX} ─────────────────────────────────────────────────────────────"

# ---------------------------------------------------------------------------
# Frontmatter parser — mirrors hooks/memory-hint.py shape; no PyYAML.
# ---------------------------------------------------------------------------

FRONTMATTER_FENCE = re.compile(r"^---\s*$", re.MULTILINE)
MOMENTUM_RE = re.compile(r"^\s*momentum\s*:\s*(.+)$", re.MULTILINE)
NAME_RE = re.compile(r"^\s*name\s*:\s*(.+)$", re.MULTILINE)
DESCRIPTION_RE = re.compile(r"^\s*description\s*:\s*(.+)$", re.MULTILINE)
INLINE_COMMENT_RE = re.compile(r"\s+#.*$")
CONTROL_BYTES_RE = re.compile(r"[\x00-\x1f\x7f]")


def _strip_inline_comment(value: str) -> str:
    return INLINE_COMMENT_RE.sub("", value)


def sanitize(text: str) -> str:
    return CONTROL_BYTES_RE.sub("?", text)


def _parse_momentum_frontmatter(raw: str) -> dict | None:
    """Extract the `---`-fenced frontmatter. Return None if absent or no momentum list."""
    matches = list(FRONTMATTER_FENCE.finditer(raw))
    if len(matches) < 2:
        return None
    block = raw[matches[0].end():matches[1].start()]

    momentum_match = MOMENTUM_RE.search(block)
    if not momentum_match:
        return None
    momentum_raw = momentum_match.group(1).strip()
    if not momentum_raw.startswith("["):
        return None
    close_idx = momentum_raw.find("]")
    if close_idx == -1:
        return None
    inner = momentum_raw[1:close_idx].strip()
    if not inner:
        return None
    triggers = [item.strip().strip('"\'') for item in inner.split(",")]
    triggers = [t for t in triggers if t]
    if not triggers:
        return None

    description = ""
    description_match = DESCRIPTION_RE.search(block)
    if description_match:
        description = (
            _strip_inline_comment(description_match.group(1))
            .strip()
            .strip('"\'')
        )

    return {"triggers": triggers, "description": description}


def _load_momentum_memories(directory: str, trigger: str) -> list[tuple[str, str, float]]:
    """Return [(filename, description, mtime), ...] for memories matching trigger."""
    out: list[tuple[str, str, float]] = []
    try:
        entries = os.listdir(directory)
    except OSError:
        return out
    for name in entries:
        if not name.endswith(".md"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        try:
            parsed = _parse_momentum_frontmatter(raw)
        except Exception:
            parsed = None
        if not parsed or trigger not in parsed["triggers"]:
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        out.append((name, parsed["description"], mtime))
    out.sort(key=lambda h: h[2], reverse=True)
    return out


def _format_memory_lines(memories: list[tuple[str, str, float]]) -> list[str]:
    """Render memory entries as PREFIX-prefixed lines mirroring the prior hardcoded shape."""
    lines: list[str] = []
    for name, description, _ in memories:
        safe_name = sanitize(name)
        # Strip ".md" suffix for parity with the prior hardcoded `Memory: feedback_*`
        # text (which referenced the slug without the file extension).
        display = safe_name[:-3] if safe_name.endswith(".md") else safe_name
        lines.append(f"{PREFIX} Memory: {display}")
        if description:
            lines.append(f"{PREFIX}   {sanitize(description)}")
        lines.append(PREFIX)
    return lines


def emit_surface(static: str, trigger: str, directory: str | None) -> str:
    """Compose the full surface for a single trigger (static rules + dynamic memories)."""
    parts = [static, PREFIX]
    if directory:
        memories = _load_momentum_memories(directory, trigger)
        if memories:
            parts.extend(_format_memory_lines(memories))
    parts.append(CLOSING_LINE)
    return "\n".join(parts)
