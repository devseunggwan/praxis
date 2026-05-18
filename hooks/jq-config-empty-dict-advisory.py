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

Handled patterns:
  • jq '.' ~/.claude/settings.json         — direct invocation
  • jq -r '.foo' ~/.codex/config.json      — raw-output flag
  • cat ~/.claude/settings.json | jq '.'   — piped; prior segment file
  • jq '.' < ~/.claude/settings.json       — stdin redirect
  • threshold=$(jq -r '.foo' settings.json) — command substitution

Not detected (by design):
  • jq invocations against non-config .json files (out of scope)
  • jq -n (null input — no file arg by design)

Actions:
  • file missing → skip (out of scope; other hooks cover existence checks)
  • file empty (size 0) → stderr advisory [config-empty]
  • file present but invalid JSON (jq parse fail) → stderr advisory [config-invalid]
  • file present and valid JSON → silent pass

Deduplicated per session_id + path so the nudge fires at most once per
advisory-emitting invocation per session. Session-state file:
  ${TMPDIR:-/tmp}/praxis-jq-config-advisory-<session_id>.json

Fail-open contract:
  • malformed JSON / non-Bash payload → exit 0
  • empty command → exit 0
  • any uncaught exception in the inner logic → swallowed, exit 0
  • python3 unavailable → shell wrapper exits 0
  • jq binary absent or timeout → skip advisory, exit 0
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
    _coalesce_subst_runs,
)


# ---------------------------------------------------------------------------
# Config path patterns
# ---------------------------------------------------------------------------
#
# A path matches if it ends with .json AND satisfies at least one of:
#   1. Has .claude/ or .codex/ anywhere in it (absolute or relative)
#   2. Is literally "settings.json" or "hooks.json" (repo-root files)
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
    if not token.endswith(".json"):
        return False
    return bool(_CONFIG_PATH_RE.search(token))


# ---------------------------------------------------------------------------
# jq flag classification (jq 1.7.1, verified via `jq --help`)
# ---------------------------------------------------------------------------
#
# Only flags that consume exactly ONE separate next token are listed here.
# Boolean flags (--sort-keys, --stream, --tab, -e, --exit-status, etc.) are
# NOT listed — they do not consume a value token.
#
# Note: --arg, --argjson, --slurpfile, --rawfile each consume TWO tokens
# (name + value). We model this as consuming 2 tokens when bare.
# --from-file/-f and -L each consume ONE token.
#
# --args / --jsonargs: consume ALL remaining argv as positional strings —
# once encountered, no further jq file arguments appear. We break on them.

# Single-token consuming flags (name-only, one following token)
_JQ_SINGLE_VALUE_FLAGS: frozenset[str] = frozenset({
    "--from-file", "-f",    # load filter from file
    "-L",                   # module search directory
    "--indent",             # indentation spaces
})

# Two-token consuming flags: each consumes TWO separate following tokens.
# Parsing: when bare (no `=`), the scanner increments `i` by 3 total —
# 1 for the flag itself plus 2 for (name, value). These are the only jq
# flags with a two-token (name + value/file) operand shape.
_JQ_DOUBLE_VALUE_FLAGS: frozenset[str] = frozenset({
    "--arg",        # --arg name value  → $name = string
    "--argjson",    # --argjson name value  → $name = JSON-decoded value
    "--slurpfile",  # --slurpfile name file  → $name = parsed JSON array of file
    "--rawfile",    # --rawfile name file  → $name = raw string contents of file
})

# Boolean flags that terminate further file argument scanning.
# After --args or --jsonargs, all remaining tokens are positional string
# values passed to the program, NOT file arguments. We treat them as a
# hard stop for file-path extraction.
_JQ_STOP_FLAGS: frozenset[str] = frozenset({"--args", "--jsonargs"})

# Pattern to extract config paths from a raw coalesced SUBST_RUN token.
# Looks for any token matching the config path shape in the substitution body.
_CONFIG_PATH_IN_TEXT_RE = re.compile(
    r"""
    (?:
        (?:^|[\s(])                 # preceded by start/space/open-paren
        (
            ~?/[^\s)'"]*\.json      # absolute path (possibly tilde)
          | \.claude/[^\s)'"]*\.json   # relative .claude/...
          | \.codex/[^\s)'"]*\.json    # relative .codex/...
          | settings\.json             # bare repo-root name
          | hooks\.json                # bare repo-root name
        )
    )
    """,
    re.VERBOSE,
)


# Matches double-value flags (--arg/--argjson name value) in a substitution
# text so their value tokens can be stripped before null-input detection.
_SUBST_DOUBLE_VALUE_FLAG_RE = re.compile(
    r'(?:--arg|--argjson)\s+\S+\s+\S+'
)

_SUBST_NULL_INPUT_RE = re.compile(
    r'\bjq\b'
    r'(?:.*?)'
    r'(?<=\s)'                         # flag must be preceded by whitespace
    r'(?:'
    r'--null-input'                    # long form
    r'|'
    r'-[a-zA-Z]*n[a-zA-Z]*'           # short form: -n, -rn, -nr, etc.
    r')'
    r'(?=\s|[)\'"]|\Z)',               # flag must be followed by whitespace, closing char, or end
    re.DOTALL,
)


def _scan_subst_for_config_paths(token: str) -> list[str]:
    """Extract config-matching paths from a coalesced SUBST_RUN token.

    A coalesced token like:
      'threshold=$(jq -r .ambiguity_threshold ~/.claude/settings.json)'
    contains a jq invocation. We extract any config-path candidates from the
    raw substitution text.

    Returns an empty list if no jq invocation or config path is detected.
    Returns an empty list when jq is called with -n/--null-input because jq
    does not read input files in null-input mode, so no advisory is warranted.
    """
    if "jq" not in token or "$(" not in token:
        return []
    # Only bother if there's a jq somewhere in the substitution.
    if not re.search(r'\bjq\b', token):
        return []
    # jq -n / --null-input reads no files — skip advisory to avoid false positive.
    # Strip double-value flag pairs (--arg name value, --argjson name value)
    # before checking so that `--arg myvar -n` is not misidentified as null-input.
    scrubbed = _SUBST_DOUBLE_VALUE_FLAG_RE.sub("", token)
    if _SUBST_NULL_INPUT_RE.search(scrubbed):
        return []
    paths = []
    for m in _CONFIG_PATH_IN_TEXT_RE.finditer(token):
        candidate = m.group(1)
        if _is_config_path(candidate):
            paths.append(candidate)
    return paths


# ---------------------------------------------------------------------------
# Extract jq target paths from a command segment
# ---------------------------------------------------------------------------
#
# jq syntax: jq [OPTIONS] FILTER [FILE...]
# We skip the filter (first positional) and collect subsequent .json positionals.
# Special handling:
#   • `<` redirect token followed by a config path
#   • piped segments: if prev segment had a config path via cat/< , passed
#     via caller-level cross-segment logic

def _extract_jq_config_paths(argv: list[str]) -> list[str]:
    """Return config-matching .json paths in a jq call argv.

    argv must already be stripped of env/wrapper prefixes (argv[0] == 'jq').
    Returns an empty list if this is not a jq invocation or no config
    paths are found.

    Multi-file support: jq accepts multiple FILE arguments after the filter
    expression (`jq '.' a.json b.json`). All positional tokens after the
    first (the filter) are treated as input files and each is checked
    independently — every config-matching path in the list is returned.
    """
    if not argv or argv[0] != "jq":
        return []

    paths: list[str] = []
    filter_seen = False
    i = 1
    n = len(argv)
    while i < n:
        tok = argv[i]

        # -- terminates options; everything after is a file path
        if tok == "--":
            for j in range(i + 1, n):
                if _is_config_path(argv[j]):
                    paths.append(argv[j])
            break

        # --args / --jsonargs: remaining argv are positional strings, not files
        if tok in _JQ_STOP_FLAGS:
            break

        # stdin redirect: `< path` — the `<` token is followed by the file
        if tok == "<":
            if i + 1 < n and _is_config_path(argv[i + 1]):
                paths.append(argv[i + 1])
            i += 2
            continue

        if tok.startswith("-") and len(tok) > 1:
            bare = tok.split("=", 1)[0]
            if "=" in tok:
                # Value is embedded (--flag=value) — skip this token only
                i += 1
                continue
            if bare in _JQ_DOUBLE_VALUE_FLAGS and i + 2 < n:
                # name value — skip two following tokens
                i += 3
                continue
            if bare in _JQ_SINGLE_VALUE_FLAGS and i + 1 < n:
                # value — skip one following token
                i += 2
                continue
            # Boolean flag — check for null-input at option position, then skip
            # NOTE: check here (not before the loop) so that `--arg name -n`
            # is not misidentified: the `-n` value token is consumed by the
            # _JQ_DOUBLE_VALUE_FLAGS branch above, never reaching this path.
            # Combined short flags (-rn, -nr, etc.) are single-dash tokens
            # without `=`; check for `n` inside them too.
            if bare == "--null-input" or (
                bare.startswith("-") and not bare.startswith("--") and "n" in bare[1:]
            ):
                return []
            i += 1
            continue

        # Positional
        if not filter_seen:
            # First positional is the jq filter expression
            filter_seen = True
        else:
            # Subsequent positionals are input files
            if _is_config_path(tok):
                paths.append(tok)
        i += 1
    return paths


def _extract_config_paths_from_non_jq_segment(argv: list[str]) -> list[str]:
    """Return config-matching .json paths from a non-jq segment.

    Used for M4: `cat ~/.claude/settings.json | jq '.'` — we need to
    detect the config path in the `cat` segment and correlate it with the
    subsequent `jq` segment.

    Scans all positional tokens (non-flags) for config paths.
    """
    paths: list[str] = []
    if not argv:
        return paths
    # Only correlate for known pipe-source commands or redirect-style patterns.
    # cat, head, tail, less, python3, etc. — any command may pipe to jq.
    # We conservatively detect config paths in any non-jq segment and let
    # the caller decide.
    i = 1
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok == "<":
            # redirect: the next token is the source file
            if i + 1 < n and _is_config_path(argv[i + 1]):
                paths.append(argv[i + 1])
            i += 2
            continue
        if tok.startswith("-") and len(tok) > 1:
            i += 1
            continue
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

    Broken-symlink detection: `os.path.exists()` returns False for broken
    symlinks (dangling links where the target is missing), so they would be
    silently skipped as "not found". We use `os.path.lexists()` — which
    returns True for any existing path entry including dangling symlinks — to
    distinguish and emit a distinct advisory instead of treating them as
    missing files.
    """
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        # Distinguish broken symlink from genuinely missing file.
        if os.path.lexists(expanded):
            return (
                f"[config-broken-symlink] {path} is a broken symlink — "
                "jq will fail; the symlink target does not exist"
            )
        return None  # genuinely absent — out of scope
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

def _collect_config_paths(command: str) -> list[str]:
    """Extract all config-matching jq target paths from a Bash command string.

    Handles four patterns:
      1. Direct: `jq '.' ~/.claude/settings.json`
      2. Stdin redirect: `jq '.' < ~/.claude/settings.json`
      3. Pipe: `cat ~/.claude/settings.json | jq '.'`
      4. Substitution: `threshold=$(jq -r '.foo' ~/.claude/settings.json)`
    """
    normalized = command.replace("\\\n", " ")
    raw_tokens = safe_tokenize(normalized)
    if not raw_tokens:
        return []

    config_paths: list[str] = []
    segments = list(iter_command_starts(raw_tokens))

    # Track config paths seen in the previous segment (for pipe correlation).
    prev_segment_config_paths: list[str] = []

    for seg_raw in segments:
        seg = list(seg_raw)

        # --- C1: SUBST_RUN scan ---
        # Coalesce to find $(…) tokens and scan them for embedded jq calls.
        coalesced = _coalesce_subst_runs(seg)
        for tok in coalesced:
            if "$(" in tok and "jq" in tok:
                subst_paths = _scan_subst_for_config_paths(tok)
                config_paths.extend(subst_paths)

        # Standard segment analysis (stripped argv)
        argv = strip_prefix(seg)
        if not argv:
            prev_segment_config_paths = []
            continue

        cmd = argv[0].rsplit("/", 1)[-1]  # basename, handle /usr/bin/jq

        if cmd == "jq":
            # Pattern 1 & 2: direct jq invocation (includes < redirect in argv)
            direct_paths = _extract_jq_config_paths(argv)
            config_paths.extend(direct_paths)

            # Pattern 3 (M4): if previous segment contained config paths,
            # and this is a jq segment with no file args, correlate.
            if not direct_paths and prev_segment_config_paths:
                config_paths.extend(prev_segment_config_paths)

            prev_segment_config_paths = []
        else:
            # Non-jq segment: collect its config paths for pipe correlation.
            prev_segment_config_paths = _extract_config_paths_from_non_jq_segment(argv)

    return config_paths


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

    config_paths = _collect_config_paths(command)
    if not config_paths:
        return 0

    session_id = _extract_session_id(payload)
    dedup_path = _resolve_dedup_path(session_id)
    seen = _load_seen(dedup_path)

    new_seen = set(seen)

    for path in config_paths:
        # Canonicalize for dedup: expand user + normalize
        key = os.path.normpath(os.path.expanduser(path))
        if key in seen:
            continue
        msg = _check_file(path)
        if msg:
            sys.stderr.write(msg + "\n")
            # M3 fix: only record in dedup state when advisory was emitted,
            # so a file that later becomes empty is re-checked.
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
