#!/usr/bin/env python3
r"""PreToolUse gate: block Trino MCP queries with 3-part catalog references
when the session has not run SHOW CATALOGS.

Issue #321. Recurring failure mode: Trino MCP queries reference catalog names
(e.g., `mysql.auth.tb_users`, `hive.warehouse.orders`) that do not exist in
the deployment, producing `Catalog '<name>' not found` errors and wasted
retries. MEMORY-resident rules ("enumerate SHOW CATALOGS before assuming catalog
name") exist but are not retrieved at query-authoring time.

This hook acts as a structural gate at MCP query construction time:

  PreToolUse  → parse the outbound query for 3-part catalog references; block
                when the session has not run SHOW CATALOGS.
                SHOW CATALOGS queries themselves pass through and create the
                session marker so subsequent queries proceed.
                Multi-statement payloads (e.g., `SHOW CATALOGS; SELECT ...`)
                create the marker AND still check subsequent statements for
                3-part references.

Session state — design rationale
=================================

Claude Code PreToolUse hooks are invoked as independent processes; there is no
shared in-memory state across invocations. State must be persisted to a file.

Resolution order (mirrors trino-describe-first.py):

  1. `PRAXIS_TRINO_CATALOG_GATE_FILE` env var — explicit override, used by
     tests for isolation.
  2. `session_id` from the hook payload (primary key) →
     `${TMPDIR:-/tmp}/praxis-catalog-gate-<session_id>.enumerated`.
     This is the canonical praxis hook session key, consumed by
     `completion-verify.sh`, `retrospect-mix-check.sh`, etc.
  3. `${TMPDIR:-/tmp}/praxis-catalog-gate-${PPID}.enumerated` — last-resort
     back-compat fallback when the payload does not carry a `session_id`
     (e.g., direct CLI / test invocation).

MCP tool name detection
=======================

Default pattern: `^mcp__.*trino.*__(trino_)?query$` (same default as
trino-describe-first.py). Override via `PRAXIS_TRINO_TOOL_PATTERN`.

3-part catalog reference detection
====================================

Matches `FROM|JOIN|INSERT INTO|MERGE INTO|UPDATE|DELETE FROM|CREATE TABLE|
CREATE VIEW <catalog>.<schema>.<table>` patterns using a SQL context gate to
avoid firing on Python attribute chains.

SQL comments are stripped before catalog-reference detection (so
`-- FROM iceberg.foo.bar` does NOT trigger the gate). The inline bypass marker
is extracted from comment text only (see below).

Bypass paths
============

  1. Inline marker: `-- catalog-enumerated: verified` in a SQL **comment**
     (line comment `-- ...` or block comment `/* ... */`). The marker must
     appear in comment syntax — placing it inside a string literal or column
     alias does NOT bypass the gate.
  2. Env bypass: `PRAXIS_TRINO_CATALOG_GATE=skip` — one-off session override.

Multi-statement handling
========================

When a single query payload contains both `SHOW CATALOGS` and 3-part catalog
references (e.g., `SHOW CATALOGS; SELECT * FROM iceberg.foo.bar`):

  - If `SHOW CATALOGS` appears as a standalone leading statement (before any
    DML/DDL keywords in the comment-stripped text) → marker is created.
  - 3-part catalog reference check always runs after marker creation, so a
    payload that simultaneously runs SHOW CATALOGS and references a catalog
    passes only after the marker has been recorded.

Fail-open contract
==================

Any infrastructure error (missing jq, mkdir fail, file write error) falls
through to exit 0 (pass), never exit 2 (block). The gate is a nudge layer —
false negatives are tolerable; false positives that break the session are not.

Phase 2 — NOT yet covered (will silently pass through)
======================================================

The following DML shapes produce `CATALOG_NOT_FOUND` but are not yet detected:

  - Catalog references inside subquery `FROM` clauses nested more than one
    level deep when the outer query itself has no 3-part reference.
  - Dynamic SQL via `EXECUTE PREPARE <name> USING ...` (prepared statement
    reference, not the underlying SQL).
  - MERGE source `USING <catalog>.<schema>.<table>` (the USING clause target
    is not a FROM/INTO keyword context and is not currently detected).
  - `CREATE TABLE x (LIKE catalog.schema.t)` LIKE clause references.
  - `ALTER TABLE <catalog>.<schema>.<table> ...` DDL is incidentally covered
    via the TABLE keyword arm (ALTER TABLE → TABLE arm matches the ref).
"""
from __future__ import annotations

import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_TOOL_PATTERN = r"^mcp__.*trino.*__(trino_)?query$"
DEFAULT_QUERY_ARG = "query"

BLOCK_REASON = (
    "trino:catalog-gate — SQL references catalog(s) [{catalogs}] without a "
    "prior SHOW CATALOGS in this session. "
    "Resolve by one of: "
    "(1) Run SHOW CATALOGS first — the gate auto-passes on the next query; "
    "(2) Append `-- catalog-enumerated: verified` to the query body; "
    "(3) One-off env bypass: PRAXIS_TRINO_CATALOG_GATE=skip."
)

# ---------------------------------------------------------------------------
# String-literal masking + comment stripping
# ---------------------------------------------------------------------------

# Single-quoted SQL string literals: '...' with '' as an escape for '.
# DOTALL is not needed — newlines in strings are unusual but allowed.
_SQL_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'", re.DOTALL)

SQL_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
SQL_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

BYPASS_MARKER = "catalog-enumerated: verified"


def mask_string_literals(sql: str) -> str:
    """Replace each SQL single-quoted string literal with 'XXX...'.

    Preserves character positions so regex offsets remain consistent.
    Both the opening and closing quote are preserved; only the interior
    content is replaced with 'X' characters of equal length.

    Handles '' (escaped single-quote) inside literals correctly: the regex
    `'(?:[^']|'')*'` treats '' as a single token inside the string.

    The masking step is applied BEFORE line-comment and block-comment regex
    so that `--` or `/* */` sequences embedded inside string literals are not
    mistakenly identified as SQL comments (C1 fix).
    """
    return _SQL_STRING_LITERAL_RE.sub(
        lambda m: "'" + "X" * (len(m.group(0)) - 2) + "'", sql
    )


def strip_sql_comments(sql: str) -> str:
    """Drop `-- line comments` and `/* block comments */`.

    Applies string-literal masking first so that `--` or `/* */` sequences
    inside string values are not treated as comment delimiters (C1 fix).
    The catalog-reference search always uses the output of this function,
    so masking is transparent to the caller.
    """
    masked = mask_string_literals(sql)
    masked = SQL_BLOCK_COMMENT_RE.sub(" ", masked)
    masked = SQL_LINE_COMMENT_RE.sub("", masked)
    return masked


# ---------------------------------------------------------------------------
# Catalog reference detection
# ---------------------------------------------------------------------------

# SQL identifier segment: bare name or quoted.
_IDENT_SEG = r'(?:"[^"]+"|`[^`]+`|[A-Za-z_][\w$]*)'

# 3-part reference: <catalog>.<schema>.<table>, optionally quoted.
_CATALOG_REF = rf"({_IDENT_SEG}\.{_IDENT_SEG}\.{_IDENT_SEG})"

# Context gate: SQL keywords that can be followed by a 3-part catalog.schema.table
# reference. `INTO` covers both INSERT INTO and MERGE INTO. `TABLE` covers
# CREATE TABLE / CREATE OR REPLACE TABLE. `VIEW` covers CREATE VIEW.
# DELETE FROM is handled by the bare `FROM` arm (DELETE FROM <ref> matches).
# UPDATE without FROM is also covered by `UPDATE` arm.
CATALOG_REF_RE = re.compile(
    rf"\b(?:FROM|JOIN|INTO|UPDATE|TABLE|VIEW)\s+{_CATALOG_REF}",
    re.IGNORECASE,
)

# SHOW CATALOGS detection (bare keyword, no catalog argument).
SHOW_CATALOGS_RE = re.compile(r"\bSHOW\s+CATALOGS\b", re.IGNORECASE)


def extract_catalogs(sql: str) -> list[str]:
    """Return distinct catalog names from 3-part DML/DDL references in sql.

    Detects catalog.schema.table references in FROM, JOIN, INTO (INSERT/MERGE),
    UPDATE, TABLE (CREATE TABLE), and VIEW (CREATE VIEW) contexts.
    SQL comments are stripped before matching. Returns lowercase catalog names.
    """
    cleaned = strip_sql_comments(sql)
    found: set[str] = set()
    for m in CATALOG_REF_RE.finditer(cleaned):
        ref = m.group(1)
        # Extract the catalog (first segment) from catalog.schema.table.
        first_part = ref.split(".")[0].strip('"').strip("`").lower()
        if first_part:
            found.add(first_part)
    return sorted(found)


def is_show_catalogs_standalone(sql: str) -> bool:
    """Return True if the comment-stripped SQL starts with SHOW CATALOGS.

    Checks the leading non-empty token sequence to avoid treating
    `SELECT 'SHOW CATALOGS' AS msg` or a SHOW CATALOGS buried mid-statement
    as a standalone enumeration query.

    Pattern: optional whitespace / semicolons / leading comments removed,
    then the first keyword pair must be SHOW CATALOGS.
    """
    cleaned = strip_sql_comments(sql).strip().lstrip(";").strip()
    return bool(SHOW_CATALOGS_RE.match(cleaned))


def has_bypass_marker(sql: str) -> bool:
    """Return True if BYPASS_MARKER appears inside a SQL comment (line or block).

    The marker must be in comment syntax to be honoured:
      - Line comment:  `-- ... catalog-enumerated: verified ...`
      - Block comment: `/* ... catalog-enumerated: verified ... */`

    Marker text inside string literals, column aliases, or quoted identifiers
    does NOT bypass the gate (C1 fix: string literals are masked before the
    comment regexes are applied, so `'foo -- catalog-enumerated: verified'`
    is replaced with `'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX'` before
    the line-comment scan).
    """
    # Mask string literals first so embedded `--` sequences are not treated
    # as comment starters (C1 fix).
    masked = mask_string_literals(sql)
    # Collect all comment text from the masked query.
    comments = SQL_LINE_COMMENT_RE.findall(masked) + SQL_BLOCK_COMMENT_RE.findall(masked)
    return any(BYPASS_MARKER in c for c in comments)


# ---------------------------------------------------------------------------
# Tool pattern
# ---------------------------------------------------------------------------


def get_tool_pattern() -> re.Pattern:
    pattern = os.environ.get("PRAXIS_TRINO_TOOL_PATTERN", "").strip()
    if not pattern:
        pattern = DEFAULT_TOOL_PATTERN
    try:
        return re.compile(pattern)
    except re.error:
        return re.compile(DEFAULT_TOOL_PATTERN)


def tool_matches(tool_name: str) -> bool:
    if not tool_name:
        return False
    return bool(get_tool_pattern().match(tool_name))


def get_query_arg() -> str:
    arg = os.environ.get("PRAXIS_TRINO_QUERY_ARG", "").strip()
    return arg or DEFAULT_QUERY_ARG


# ---------------------------------------------------------------------------
# Session marker (SHOW CATALOGS enumerated flag)
# ---------------------------------------------------------------------------


def _extract_session_id(payload: dict) -> str | None:
    """Return the trimmed `session_id` from the hook payload, or None.

    Canonical praxis hook session key — same field consumed by
    `completion-verify.sh`, `retrospect-mix-check.sh`, `strike-counter.sh`,
    and `trino-describe-first.py`.
    """
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None


def resolve_marker_path(session_id: str | None = None) -> str:
    """Resolve the session-scoped catalog-enumerated marker file path.

    Resolution order:
      1. PRAXIS_TRINO_CATALOG_GATE_FILE env var (explicit override / test isolation).
      2. session_id from payload → ${TMPDIR:-/tmp}/praxis-catalog-gate-<sid>.enumerated.
      3. PPID fallback for direct CLI / test invocation.
    """
    override = os.environ.get("PRAXIS_TRINO_CATALOG_GATE_FILE", "").strip()
    if override:
        return override

    tmp = os.environ.get("TMPDIR", "/tmp").rstrip("/") or "/tmp"
    if session_id:
        return os.path.join(tmp, f"praxis-catalog-gate-{session_id}.enumerated")
    ppid = os.getppid()
    return os.path.join(tmp, f"praxis-catalog-gate-{ppid}.enumerated")


def marker_exists(path: str) -> bool:
    """Return True if the session's SHOW-CATALOGS marker file exists."""
    try:
        return os.path.isfile(path)
    except OSError:
        return False  # fail-open


def create_marker(path: str) -> None:
    """Create (or touch) the marker file. Silently fails on OS errors."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("")
    except OSError:
        pass  # fail-open; never raise from a hook


# ---------------------------------------------------------------------------
# Hook output
# ---------------------------------------------------------------------------


def emit_deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run() -> int:
    # Env bypass — one-off session override.
    if os.environ.get("PRAXIS_TRINO_CATALOG_GATE", "").strip().lower() == "skip":
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed input

    tool_name = payload.get("tool_name", "") or ""
    if not tool_matches(tool_name):
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    query = tool_input.get(get_query_arg(), "") or ""
    if not isinstance(query, str) or not query.strip():
        return 0

    session_id = _extract_session_id(payload)
    marker_path = resolve_marker_path(session_id)

    # Snapshot the marker state BEFORE any marker creation this invocation.
    # This prevents a multi-statement payload such as
    # `SHOW CATALOGS; SELECT * FROM iceberg.foo.bar` from self-authorising:
    # the SHOW CATALOGS branch creates the marker, but the 3-part reference
    # check must use the pre-creation state (M3 fix).
    marker_was_present = marker_exists(marker_path)

    # SHOW CATALOGS as standalone leading statement → create marker.
    # We do NOT return early here: the 3-part reference check below still
    # runs using the pre-creation marker snapshot.
    try:
        if is_show_catalogs_standalone(query):
            create_marker(marker_path)
            # A bare SHOW CATALOGS (no DML/DDL in the rest of the payload)
            # has no 3-part references — extract_catalogs will return [] and
            # we'll exit via the `not catalogs` branch below.
    except Exception:
        pass  # fail-open; marker creation failure is non-fatal

    # Inline bypass: marker must appear inside SQL comment syntax (M1 fix).
    # String literals / column aliases containing the marker text are NOT
    # honoured — this prevents `SELECT 'catalog-enumerated: verified' AS msg
    # FROM iceberg.foo.bar` from silently bypassing the gate.
    try:
        if has_bypass_marker(query):
            return 0
    except Exception:
        pass  # fail-open

    # Extract 3-part catalog references from the comment-stripped SQL.
    try:
        catalogs = extract_catalogs(query)
    except Exception:
        return 0  # fail-open on parse error

    if not catalogs:
        return 0  # no 3-part references → pass through

    # Use the pre-invocation marker snapshot (not the post-creation state).
    if marker_was_present:
        return 0

    # Block: emit deny with guidance.
    catalogs_str = ", ".join(catalogs)
    emit_deny(BLOCK_REASON.format(catalogs=catalogs_str))
    return 0


if __name__ == "__main__":
    sys.exit(run())
