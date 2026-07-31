"""Shared external-write body extraction for PreToolUse hooks.

Two hooks (`external-write-falsify-check`, `source-citation-probe-gate`)
each carried a byte-identical copy of this surface detection + body
extraction. Both specs recorded the repo's 2-copy convention with the
same directive: extract to `_lib/_external_write_body.py` at the third
consumer. This module is that extraction; the copies were semantically
identical (comment wording aside), so migration is behavior-preserving
and each hook's existing test suite is the parity oracle.

What lives here is only "which tool calls write to an external surface,
and what is the body text" — every claim-detection heuristic stays in the
consuming hook, since that is what distinguishes them.
"""
from __future__ import annotations

import re

from _hook_utils import strip_prefix  # type: ignore[import-not-found]

# ---------------------------------------------------------------------------
# Bash gh detection
# ---------------------------------------------------------------------------

GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})

# `gh <obj> <sub>` pairs that write to external surfaces.
# `pr review` accepts --body / -b / --body-file / -F and posts a public
# review comment, so it belongs here alongside the comment/create/edit forms.
GH_WRITE_SUBCOMMANDS = frozenset({
    ("issue", "comment"),
    ("pr", "comment"),
    ("issue", "create"),
    ("pr", "create"),
    ("issue", "edit"),
    ("pr", "edit"),
    ("pr", "review"),
})

GH_BODY_FLAGS_WITH_ARG = frozenset({"-b", "--body", "-F", "--body-file"})


def resolve_body(flag: str, value: str) -> str:
    """Read body content. For --body-file, read file contents (best effort).

    A non-UTF-8 file raises UnicodeDecodeError, which is not an OSError. Left
    uncaught it escapes to the hook's `fail_open` wrapper, so ONE undecodable
    body silently skips the whole call's scan — including sibling writes
    chained in the same command. Same category as an unreadable file, so it
    takes the same empty-body fallback and the other bodies still get scanned.
    """
    if flag in {"-F", "--body-file"}:
        try:
            with open(value, encoding="utf-8") as fh:
                return fh.read()
        except (OSError, UnicodeDecodeError):
            return ""  # treat unreadable file as empty body — advisory-only hooks
    return value


def extract_gh_body(argv: list[str]) -> str | None:
    """Pull body text from --body / --body-file in a gh argv. None if absent."""
    for i, tok in enumerate(argv):
        if "=" in tok:
            key, _, val = tok.partition("=")
            if key in GH_BODY_FLAGS_WITH_ARG:
                return resolve_body(key, val)
            continue
        if tok in GH_BODY_FLAGS_WITH_ARG and i + 1 < len(argv):
            return resolve_body(tok, argv[i + 1])
    return None


def is_gh_external_write(argv: list[str]) -> bool:
    """Return True iff argv invokes a gh subcommand that writes to a public surface."""
    argv = strip_prefix(argv)
    if not argv or argv[0] != "gh":
        return False

    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in GH_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            i += 1

    if i + 1 >= len(argv):
        return False
    obj, sub = argv[i], argv[i + 1]
    return (obj, sub) in GH_WRITE_SUBCOMMANDS


# ---------------------------------------------------------------------------
# MCP detection
# ---------------------------------------------------------------------------

MCP_EXTERNAL_WRITE_PATTERNS = (
    re.compile(r".*slack.*send.*", re.IGNORECASE),
    re.compile(r".*slack.*post.*", re.IGNORECASE),
    re.compile(r".*slack.*update.*", re.IGNORECASE),
    re.compile(r".*notion.*create.*page.*", re.IGNORECASE),
    re.compile(r".*notion.*update.*page.*", re.IGNORECASE),
    re.compile(r".*notion.*append.*block.*", re.IGNORECASE),
)


def is_mcp_external_write(tool_name: str) -> bool:
    return any(p.match(tool_name) for p in MCP_EXTERNAL_WRITE_PATTERNS)


# Leaf keys whose value is body content (collect descendant strings).
BODY_LEAF_KEYS = frozenset({
    "text", "content", "body", "message", "page_content",
})

# Container keys that wrap block/rich-text lists. Inside a container we
# traverse wrapper dicts (paragraph, heading_1, section, ...) until we hit
# a body leaf or another container.
BODY_CONTAINER_KEYS = frozenset({
    "children", "blocks", "rich_text",
})


def _collect_under_leaf(node, parts: list[str]) -> None:
    """Collect every string descendant. Called once a leaf key is entered."""
    if isinstance(node, str):
        parts.append(node)
    elif isinstance(node, list):
        for item in node:
            _collect_under_leaf(item, parts)
    elif isinstance(node, dict):
        for val in node.values():
            _collect_under_leaf(val, parts)


def _walk_in_container(node, parts: list[str]) -> None:
    """Inside `children` / `blocks` / `rich_text`: traverse wrapper dicts
    transparently, switching to leaf-collection only at body keys."""
    if isinstance(node, list):
        for item in node:
            _walk_in_container(item, parts)
    elif isinstance(node, dict):
        for key, val in node.items():
            if isinstance(key, str) and key.lower() in BODY_LEAF_KEYS:
                _collect_under_leaf(val, parts)
            else:
                _walk_in_container(val, parts)


def extract_mcp_body(tool_input: dict) -> str:
    """Body extraction from MCP tool_input gated by recognized entry points.

    Gating on recognized container/leaf entry points keeps property metadata
    (`properties.{name}.title[].text.content`) from surfacing as body.
    """
    parts: list[str] = []
    for key, val in tool_input.items():
        if not isinstance(key, str):
            continue
        kl = key.lower()
        if kl in BODY_LEAF_KEYS:
            _collect_under_leaf(val, parts)
        elif kl in BODY_CONTAINER_KEYS:
            _walk_in_container(val, parts)
    return "\n".join(parts)
