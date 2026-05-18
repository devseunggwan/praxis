#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: warn when jq reads an empty or invalid JSON
config file.

Issue #323. Recurring failure mode: `jq` invocations against config files
(settings.json, hooks.json, ~/.claude/*.json, ~/.codex/*.json) silently
return `null` or `empty` when the target file is empty (size 0), and fail
with a parse error when the file contains invalid JSON. Downstream code
that pipes jq output may silently fall back to incorrect defaults, masking
the root cause.

Detection surface:
  • Bash tool calls containing `jq ... <path>.json`
  • <path> matches known config locations:
      - ~/.claude/*.json
      - ~/.codex/*.json
      - repo-root settings.json
      - repo-root hooks.json
      - any path ending in .json under .claude/ or .codex/ (relative or absolute)

Actions:
  • file missing → skip (out of scope; other hooks cover existence checks)
  • file empty (size 0) → stderr advisory [config-empty]
  • file present but invalid JSON (jq parse fail) → stderr advisory [config-invalid]
  • file present and valid JSON → silent pass

Deduplicated per session_id + path so the nudge fires at most once per
file per session. Session-state file:
  ${TMPDIR:-/tmp}/praxis-jq-config-advisory-<session_id>.json

Fail-open contract:
  • malformed JSON / non-Bash payload → exit 0
  • empty command → exit 0
  • any uncaught exception in the inner logic → swallowed, exit 0
  • python3 unavailable → shell wrapper exits 0
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)


# ---------------------------------------------------------------------------
# Config path patterns
# ---------------------------------------------------------------------------
#
# A path matches if it ends with .json AND satisfies at least one of:
#   1. Has .claude/ or .codex/ anywhere in it (absolute or relative)
#   2. Is literally "settings.json" or "hooks.json" (repo-root files)
#   3. Is under ~ expansion for ~/.claude/ or ~/.codex/
#
# We do NOT anchor on ~/.claude exactly — relative paths like
# .claude/settings.json are also config paths.

_CONFIG_PATH_RE = re.compile(
    r"""
    (?:
        (?:^|/)\.claude/   # under .claude/ dir
      | (?:^|/)\.codex/    # under .codex/ dir
      | (?:^|/)settings\.json$   # repo-root settings.json
      | (?:^|/)hooks\.json$      # repo-root hooks.json
    )
    """,
    re.VERBOSE,
)


def _is_config_path(token: str) -> bool:
    """Return True if `token` looks like a known config JSON path."""
    # Must end with .json
    if not token.endswith(".json"):
        return False
    return bool(_CONFIG_PATH_RE.search(token))


# ---------------------------------------------------------------------------
# Extract jq target path from a command segment argv
# ---------------------------------------------------------------------------
#
# jq syntax: jq [OPTIONS] FILTER [FILE...]
# We look for any POSITIONAL argument (non-flag, non-filter) that ends in
# .json. The filter is the first positional; subsequent positionals are
# input files. We skip the filter and look for file arguments.
#
# Heuristic: skip the first positional (the filter), then collect all
# remaining positionals that end in .json. Handles the common patterns:
#   jq '.' settings.json
#   jq '.hooks' ~/.claude/settings.json
#   cat settings.json | jq '.'   ← no file arg (piped), skip
#   jq -r '.foo' ~/.codex/config.json

def _extract_jq_config_paths(argv: list[str]) -> list[str]:
    """Return all config-matching .json positional arguments in a jq call.

    argv must already be stripped of env/wrapper prefixes. argv[0] == 'jq'.
    Returns an empty list if this is not a jq invocation or no config paths
    are found.
    """
    if not argv or argv[0] != "jq":
        return []

    # Known jq flags that take a separate-token value.
    # Source: jq --help (jq 1.6/1.7)
    jq_value_flags: frozenset[str] = frozenset({
        "--arg", "--argjson", "--slurpfile", "--rawfile", "--jsonargs",
        "--args", "-e", "--exit-status", "--indent", "--tab",
        "--from-file", "-f", "--sort-keys", "--stream",
        "-L", "--rawfile",
    })

    paths: list[str] = []
    filter_seen = False
    i = 1
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok == "--":
            # Everything after -- is a file argument
            for j in range(i + 1, n):
                if _is_config_path(argv[j]):
                    paths.append(argv[j])
            break
        if tok.startswith("-") and len(tok) > 1:
            # Flag — check if it consumes a value token
            bare = tok.split("=", 1)[0]
            if "=" not in tok and bare in jq_value_flags and i + 1 < n:
                i += 2
                continue
            i += 1
            continue
        # Positional
        if not filter_seen:
            # First positional is the filter expression
            filter_seen = True
        else:
            # Subsequent positionals are input files
            if _is_config_path(tok):
                paths.append(tok)
        i += 1
    return paths


# ---------------------------------------------------------------------------
# Session-scoped deduplication
# ---------------------------------------------------------------------------

def _extract_session_id(payload: dict) -> Optional[str]:
    """Return the trimmed session_id, or None."""
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None


def _resolve_dedup_path(session_id: Optional[str]) -> str:
    tmp = os.environ.get("TMPDIR", "/tmp").rstrip("/") or "/tmp"
    if session_id:
        return os.path.join(tmp, f"praxis-jq-config-advisory-{session_id}.json")
    ppid = os.getppid()
    return os.path.join(tmp, f"praxis-jq-config-advisory-{ppid}.json")


def _load_seen(path: str) -> set:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return set(data)
        return set()
    except (OSError, ValueError):
        return set()


def _save_seen(path: str, seen: set) -> None:
    try:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(sorted(seen), fh)
        os.replace(tmp_path, path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------

def _check_file(path: str) -> Optional[str]:
    """Return an advisory message if path is empty or invalid JSON, else None.

    Returns None (skip) when the file does not exist.
    """
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        return None  # out of scope
    if os.path.getsize(expanded) == 0:
        return (
            f"[config-empty] {path} is empty — "
            "jq will return 'empty', downstream may silently fallback"
        )
    # Validate JSON by invoking jq. If jq is absent or times out, skip
    # (fail-open) — the hook's job is to warn, not to enforce.
    try:
        result = subprocess.run(
            ["jq", ".", expanded],
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            return (
                f"[config-invalid] {path} is not parseable as JSON — "
                "jq will fail"
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None  # jq absent or timed out — skip advisory
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _main_inner() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    # Find all jq invocations with config-path arguments
    tokens = safe_tokenize(command.replace("\\\n", " "))
    if not tokens:
        return 0

    config_paths: list[str] = []
    for argv_raw in iter_command_starts(tokens):
        argv = strip_prefix(list(argv_raw))
        paths = _extract_jq_config_paths(argv)
        config_paths.extend(paths)

    if not config_paths:
        return 0

    session_id = _extract_session_id(payload)
    dedup_path = _resolve_dedup_path(session_id)
    seen = _load_seen(dedup_path)

    new_seen = set(seen)
    emitted = False

    for path in config_paths:
        # Canonicalize for dedup: expand user, but keep original for display
        key = os.path.normpath(os.path.expanduser(path))
        if key in seen:
            continue
        msg = _check_file(path)
        if msg:
            sys.stderr.write(msg + "\n")
            emitted = True
        new_seen.add(key)

    if new_seen != seen:
        _save_seen(dedup_path, new_seen)

    return 0


def main() -> int:
    """Advisory hook — must NEVER break tool execution."""
    try:
        return _main_inner()
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
