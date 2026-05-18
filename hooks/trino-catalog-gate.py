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

Matches `FROM <catalog>.<schema>.<table>` and `JOIN <catalog>.<schema>.<table>`
patterns using the same approach as the external-api-literal-trigger: context
keyword gate avoids firing on Python attribute chains.

SQL comments are stripped before detection to prevent `-- catalog.schema.table`
false positives.

Bypass paths
============

  1. Inline marker: `-- catalog-enumerated: verified` anywhere in the SQL body.
  2. Env bypass: `PRAXIS_TRINO_CATALOG_GATE=skip` — one-off session override.

Fail-open contract
==================

Any infrastructure error (missing jq, mkdir fail, file write error) falls
through to exit 0 (pass), never exit 2 (block). The gate is a nudge layer —
false negatives are tolerable; false positives that break the session are not.
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
# Comment stripping
# ---------------------------------------------------------------------------

SQL_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
SQL_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def strip_sql_comments(sql: str) -> str:
    """Drop `-- line comments` and `/* block comments */`."""
    sql = SQL_BLOCK_COMMENT_RE.sub(" ", sql)
    sql = SQL_LINE_COMMENT_RE.sub("", sql)
    return sql


# ---------------------------------------------------------------------------
# Catalog reference detection
# ---------------------------------------------------------------------------

# SQL identifier segment: bare name or quoted.
_IDENT_SEG = r'(?:"[^"]+"|`[^`]+`|[A-Za-z_][\w$]*)'

# 3-part reference: <catalog>.<schema>.<table>, optionally quoted.
_CATALOG_REF = rf"({_IDENT_SEG}\.{_IDENT_SEG}\.{_IDENT_SEG})"

# Requires FROM or JOIN context to avoid Python attribute chains.
CATALOG_REF_RE = re.compile(
    rf"\b(?:FROM|JOIN)\s+{_CATALOG_REF}",
    re.IGNORECASE,
)

# SHOW CATALOGS detection (bare keyword, no catalog argument).
SHOW_CATALOGS_RE = re.compile(r"\bSHOW\s+CATALOGS\b", re.IGNORECASE)


def extract_catalogs(sql: str) -> list[str]:
    """Return distinct catalog names from 3-part FROM/JOIN references in sql.

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


def is_show_catalogs(sql: str) -> bool:
    """Return True if sql contains SHOW CATALOGS (comments stripped)."""
    cleaned = strip_sql_comments(sql)
    return bool(SHOW_CATALOGS_RE.search(cleaned))


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

    # SHOW CATALOGS itself: create marker + pass through.
    try:
        if is_show_catalogs(query):
            create_marker(marker_path)
            return 0
    except Exception:
        return 0  # fail-open

    # Inline bypass marker in SQL body (checked after comment strip in
    # extract_catalogs, but the bypass comment itself lives outside stripped
    # region — check on raw query for reliability).
    if "catalog-enumerated: verified" in query:
        return 0

    # Extract 3-part catalog references.
    try:
        catalogs = extract_catalogs(query)
    except Exception:
        return 0  # fail-open on parse error

    if not catalogs:
        return 0  # no 3-part references → pass through

    # Marker present → session already enumerated.
    if marker_exists(marker_path):
        return 0

    # Block: emit deny with guidance.
    catalogs_str = ", ".join(catalogs)
    emit_deny(BLOCK_REASON.format(catalogs=catalogs_str))
    return 0


if __name__ == "__main__":
    sys.exit(run())
