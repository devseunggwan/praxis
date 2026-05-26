#!/usr/bin/env python3
"""PreToolUse advisory: nudge wrapper/client signature verification.

Issue #235. Recurring failure mode (4+ occurrences across sessions): when
writing wrapper/client classes that delegate to underlying functions,
parameter names and return types are inferred from function names rather
than verified by reading the actual source. Result: multiple wrong
signatures per session (non-existent params, wrong return types, name
typos like ``hours`` vs ``days``).

This hook fires on ``Write`` / ``Edit`` to file paths that match the
wrapper-shape heuristic AND whose body contains delegation patterns. It
emits an advisory reminder to stderr and exits 0 (never blocks).

Memory entries and CLAUDE.md rules alone failed to prevent recurrence —
the retrieval trigger does not fire at the specific moment the wrapper
body is authored. Moving the gate to the tool-call use-site adds a
structural reminder.

Fail-open contract (project hook design):

* Malformed / missing stdin JSON  → exit 0
* Unknown ``tool_name``           → exit 0
* Missing ``file_path``           → exit 0
* No matching wrapper pattern     → exit 0
* Any uncaught exception          → exit 0
"""
from __future__ import annotations

import json
import re
import sys


# ---------------------------------------------------------------------------
# Advisory message
# ---------------------------------------------------------------------------

ADVISORY_HEADER = (
    "[advisory-wrapper-signature-verify] Wrapper/client write detected"
)
ADVISORY_BODY = (
    "Before writing, verify actual function signatures:\n"
    "  grep -n '^def ' <wrapped_module>.py\n"
    "  or use Read tool to inspect the module directly\n"
    "\n"
    "Common mistake patterns:\n"
    "  - Adding non-existent parameters\n"
    "  - Wrong return type (list[TypedObject] vs list[dict])\n"
    "  - Parameter name typo (e.g., hours vs days, id vs data_id)"
)


# ---------------------------------------------------------------------------
# File-path heuristic: wrapper / client shape
# ---------------------------------------------------------------------------

def _is_wrapper_shape_path(file_path: str) -> bool:
    """True if the path looks like a wrapper/client *Python* file.

    Two heuristics, matching the issue spec:

    * ends with ``client.py`` (e.g. ``foo_client.py``, ``bar/client.py``)
    * contains ``_wrapper`` anywhere in the path *and* ends with ``.py``

    The hook is specifically aimed at Python wrapper code (function signatures,
    return types). Non-Python files that incidentally contain ``_wrapper`` in
    their name (e.g. ``foo_wrapper.md`` notes) are out of scope.

    Test-file paths are excluded to suppress the most common false-positive
    surface: real codebases routinely write wrapper assertions inside
    ``tests/`` or ``test_*.py`` files, where the delegation-pattern reminder
    is noise rather than signal.
    """
    if not file_path:
        return False
    if not file_path.endswith(".py"):
        return False
    if _is_test_path(file_path):
        return False
    if file_path.endswith("client.py"):
        return True
    if "_wrapper" in file_path:
        return True
    return False


# Test-file path heuristic. Matches both pytest-style (``test_foo.py``,
# ``foo_test.py``) and directory-style (``/tests/``, ``/test/``) locations.
_TEST_PATH_PATTERN = re.compile(
    r"(?:/|^)tests?/"            # /tests/, /test/, tests/foo.py, test/foo.py
    r"|/test_[^/]*\.py$"         # /test_foo.py
    r"|_test\.py$"               # foo_test.py
)


def _is_test_path(file_path: str) -> bool:
    """True if the path is recognisably a test file/dir."""
    return bool(_TEST_PATH_PATTERN.search(file_path))


# ---------------------------------------------------------------------------
# Content patterns: delegation shape
# ---------------------------------------------------------------------------

# Patterns indicating the new code delegates to another module's function.
# Conservative set — only fires on common wrapper shapes.
_DELEGATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"return\s+get_\w+\s*\("),
    re.compile(r"return\s+create_\w+\s*\("),
    re.compile(r"from\s+[\w.]+\.queries\s+import"),
    re.compile(r"from\s+[\w.]+\.client\s+import"),
)


def _has_delegation_pattern(content: str) -> bool:
    """True if any delegation pattern appears in ``content``."""
    if not content:
        return False
    for pattern in _DELEGATION_PATTERNS:
        if pattern.search(content):
            return True
    return False


# ---------------------------------------------------------------------------
# Tool input extraction
# ---------------------------------------------------------------------------

def _extract_content(tool_name: str, tool_input: dict) -> str:
    """Return the body to scan for the given Write/Edit tool input.

    * ``Write`` → ``tool_input.content``
    * ``Edit``  → ``tool_input.new_string`` (the proposed replacement)
    """
    if tool_name == "Write":
        value = tool_input.get("content")
    elif tool_name == "Edit":
        value = tool_input.get("new_string")
    else:
        return ""
    return value if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _main_inner() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    if not isinstance(payload, dict):
        return 0

    tool_name = payload.get("tool_name") or ""
    if tool_name not in ("Write", "Edit"):
        return 0

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not _is_wrapper_shape_path(file_path):
        return 0

    content = _extract_content(tool_name, tool_input)
    if not _has_delegation_pattern(content):
        return 0

    sys.stderr.write(
        f"{ADVISORY_HEADER}\nFile: {file_path}\n\n{ADVISORY_BODY}\n"
    )
    return 0


def main() -> int:
    """Advisory hook — must NEVER break tool execution. Any uncaught
    exception in the inner logic is swallowed and the hook fails open
    (exit 0)."""
    try:
        return _main_inner()
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
