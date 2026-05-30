#!/usr/bin/env python3
"""PreToolUse advisory: nudge retrieval when external API literals are detected.

Issue #202. Recurring failure mode: enum values, catalog names, and date-range
literals are written into tool calls or file edits based on naming-pattern
recall (plausibility from a related vendor family) rather than verified
retrieval (vendor doc, SHOW CATALOGS, --help).

Three observed instances in 30 days:
  1. Trino catalog name guessed → 6× CATALOG_NOT_FOUND retries
  2. Vendor API enum values from recall → 3 of 5 rejected by vendor
  3. Date-range literal style guessed → INVALID_DATE_RANGE_FORMAT

This hook fires on Write / Edit / Bash PreToolUse events and scans the
relevant content field for external API enum / literal patterns:

  - ALL_CAPS_WITH_UNDERSCORES tokens of length ≥ 6 (likely enum candidates)
  - 3-part SQL identifiers (<catalog>.<schema>.<table>) appearing in strings

When detected, it emits an advisory stderr reminder — it does NOT block.
Advisory mode is the only mode: false-positive cost is low and enforcement
is not the goal. The goal is to shift retrieval earlier in the conversation.

Fail-open contract (project hook design):
  - Malformed / missing stdin JSON → exit 0
  - Unknown tool_name → exit 0
  - Missing target field (content / new_string / command) → exit 0
  - Any uncaught exception → exit 0

Stop-words: obvious code-file constants (TODO, FIXME, README, LICENSE, MIT,
BSD, GNU, API, URL, URI, SQL, JSON, CSV, XML, HTML, HTTP, HTTPS, UTF, ASCII)
are excluded to avoid noise on internal source-code literals.
"""
from __future__ import annotations

import json
import os
import re
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_utils import safe_tokenize  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# ALL_CAPS_WITH_UNDERSCORES tokens of length ≥ 6.
# Requires at least one underscore or digit (pure ALL_CAPS words like "SELECT"
# are excluded by requiring the compound structure).
# ASCII right-boundary lookahead instead of a trailing `\b`: Python's `\b` is
# Unicode-aware, so Hangul counts as a word char and `AUTH_TOKEN이` /
# `VENDOR_API_KEY를` would be missed because the trailing `\b` does not see a
# boundary between the ASCII token and the adjacent Hangul (issue #513, 결함4).
# Mirrors the output-block-falsify-advisory `_ANCHORING_EN_PATTERN` right
# anchor. The leading `\b` is kept so we still avoid matching inside longer
# mixed-case identifiers (e.g. `myAUTH_TOKEN`).
ALLCAPS_RE = re.compile(r"\b([A-Z][A-Z0-9_]{5,})(?![A-Z0-9_])")

# 3-part SQL identifiers: catalog.schema.table (all lowercase / underscore).
# Matches only when all three parts follow SQL identifier conventions AND the
# match is not immediately followed by `(` (which would indicate a method /
# function call like `os.environ.get(...)` rather than a table reference).
# Each part must be at least 2 characters to avoid matching short Python
# attribute chains (e.g., `os.path.sep` where `os` is only 2 chars but
# `environ.get` is a common accessor, not a catalog name).
# Additional context gate: must appear after FROM/JOIN keywords (case-insensitive)
# OR be surrounded by quotes — this avoids firing on Python attribute chains.
SQL_3PART_RE = re.compile(
    r"\b([a-z_][a-z0-9_]{1,}\.[a-z_][a-z0-9_]{1,}\.[a-z_][a-z0-9_]{1,})\b(?!\s*\()"
)

# SQL context keywords: the 3-part identifier must be preceded by one of these
# within a short window to qualify as a SQL table reference.
# Three identifier shapes are recognized after the keyword:
#   bare:        catalog.schema.table
#   double-quoted: "catalog"."schema"."table"     (ANSI SQL quoted form)
#   backticked:  `catalog`.`schema`.`table`       (MySQL / Hive form)
SQL_CONTEXT_RE = re.compile(
    r"""\b(?:FROM|JOIN|TABLE|INTO|UPDATE)\s+
        (?:
            [a-z_][a-z0-9_]{1,}\.[a-z_][a-z0-9_]{1,}\.[a-z_][a-z0-9_]{1,}\b
          | "[^"]+"\."[^"]+"\."[^"]+"
          | `[^`]+`\.`[^`]+`\.`[^`]+`
        )""",
    re.IGNORECASE | re.VERBOSE,
)

# Stop-words: common code constants that are NOT external API literals.
# Matched against the full token (case-sensitive after uppercasing).
STOP_WORDS: frozenset[str] = frozenset({
    # Code markers
    "TODO", "FIXME", "HACK", "NOTE", "WARN", "WARNING", "DEBUG",
    # Licenses
    "LICENSE", "README", "AUTHORS", "CHANGELOG", "CONTRIBUTORS",
    "MIT", "BSD", "GNU", "GPL", "APACHE", "MPL", "AGPL",
    # Format names
    "JSON", "XML", "CSV", "YAML", "TOML", "SQL", "HTML",
    "UTF8", "ASCII", "UNICODE",
    # Protocol / network
    "HTTP", "HTTPS", "FTP", "SSH", "TCP", "UDP",
    "URL", "URI", "UUID", "GUID",
    # Generic programming
    "API", "SDK", "CLI", "GUI", "EOF",
    "NULL", "NONE", "TRUE", "FALSE",
    # Shell / env
    "PATH", "HOME", "USER", "SHELL", "TERM",
    # Python / general
    "PYTHONPATH", "VIRTUAL", "STDERR", "STDOUT", "STDIN",
})

# Minimum length for ALL_CAPS token to trigger (6 = already baked into regex,
# this is a secondary guard in case the regex is adjusted).
MIN_TOKEN_LEN = 6

# Advisory message template. {kind} = "ALL_CAPS enum" or "3-part SQL identifier".
ADVISORY_TEMPLATE = (
    "⚠ External API literal detected: `{token}` ({kind})\n"
    "   Has this value been verified against an authoritative source\n"
    "   (vendor doc, SHOW CATALOGS, --help) in this session?\n"
    "   If yes, proceed. If no, retrieve before write.\n"
)


# ---------------------------------------------------------------------------
# Scanning helpers
# ---------------------------------------------------------------------------

def _is_stop_word(token: str) -> bool:
    """True if the token is a known non-external-API constant."""
    return token.upper() in STOP_WORDS


def _has_compound_structure(token: str) -> bool:
    """True if the ALL_CAPS token contains at least one underscore or digit.

    Pure letter words like SELECT, INSERT, UPDATE, DELETE are common SQL
    keywords and should not fire. Compound tokens like LAST_365_DAYS,
    SHOPBY_AUTH_TOKEN, AUTH_SERVICE_URL are the target pattern.
    """
    return "_" in token or any(c.isdigit() for c in token)


def find_allcaps_literals(text: str) -> list[str]:
    """Return ALL_CAPS_WITH_UNDERSCORES tokens that are likely external literals."""
    found: list[str] = []
    seen: set[str] = set()
    for m in ALLCAPS_RE.finditer(text):
        token = m.group(1)
        if token in seen:
            continue
        seen.add(token)
        if len(token) < MIN_TOKEN_LEN:
            continue
        if _is_stop_word(token):
            continue
        if not _has_compound_structure(token):
            continue
        found.append(token)
    return found


def find_sql_3part_identifiers(text: str) -> list[str]:
    """Return 3-part SQL identifiers (catalog.schema.table).

    Only fires when the 3-part identifier appears in a SQL context
    (preceded by FROM / JOIN / TABLE / INTO / UPDATE). This avoids
    false positives on Python attribute chains like `os.environ.get`,
    `module.sub.method`, etc.

    Supports three identifier shapes:
      bare:           catalog.schema.table
      double-quoted:  "catalog"."schema"."table"
      backticked:     `catalog`.`schema`.`table`
    Quoted forms are returned with their delimiters stripped so the
    advisory message displays the logical name only.
    """
    found: list[str] = []
    seen: set[str] = set()
    for m in SQL_CONTEXT_RE.finditer(text):
        # The match includes the keyword (FROM/JOIN/...) followed by the
        # identifier; the identifier is the last whitespace-separated token.
        full = m.group(0)
        parts = full.split()
        identifier = parts[-1] if parts else ""
        if not identifier:
            continue
        # Normalize quoted forms by stripping wrapping `"` or `` ` ``.
        normalized = identifier.replace('"', "").replace("`", "")
        dot_parts = normalized.split(".")
        if len(dot_parts) != 3 or not all(dot_parts):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        found.append(normalized)
    return found


def scan_content(text: str) -> list[tuple[str, str]]:
    """Return a list of (token, kind) advisory pairs from `text`.

    kind is one of: "ALL_CAPS enum candidate", "3-part SQL identifier".
    Returns at most 3 findings to avoid advisory overload.
    """
    if not text:
        return []
    findings: list[tuple[str, str]] = []

    for token in find_allcaps_literals(text):
        findings.append((token, "ALL_CAPS enum candidate"))
        if len(findings) >= 3:
            return findings

    for token in find_sql_3part_identifiers(text):
        findings.append((token, "3-part SQL identifier"))
        if len(findings) >= 3:
            return findings

    return findings


# ---------------------------------------------------------------------------
# Tool input extraction
# ---------------------------------------------------------------------------

# Map tool_name → field to scan.
TOOL_FIELD_MAP: dict[str, str] = {
    "Write": "content",
    "Edit": "new_string",
    "Bash": "command",
}


def extract_scan_target(tool_name: str, tool_input: dict) -> str | None:
    """Return the text to scan for the given tool, or None if not applicable."""
    field = TOOL_FIELD_MAP.get(tool_name)
    if field is None:
        return None
    value = tool_input.get(field)
    if not isinstance(value, str):
        return None
    # Bash commands are shell syntax: tokenize first so that quoted argument
    # values are unquoted and shell operators (;|&) are stripped as separators.
    # Rejoin preserves the sequential ordering that SQL context detection needs.
    if tool_name == "Bash":
        tokens = safe_tokenize(value)
        return " ".join(tokens) if tokens else value
    return value


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    if not isinstance(payload, dict):
        return 0

    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}

    if not isinstance(tool_input, dict):
        return 0

    text = extract_scan_target(tool_name, tool_input)
    if text is None:
        return 0  # unknown tool or missing field — pass through

    findings = scan_content(text)
    if not findings:
        return 0

    for token, kind in findings:
        sys.stderr.write(ADVISORY_TEMPLATE.format(token=token, kind=kind))

    return 0


if __name__ == "__main__":
    sys.exit(main())
