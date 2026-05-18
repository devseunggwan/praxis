#!/usr/bin/env python3
"""PreToolUse advisory: warn when gh issue/pr body references repo paths that do not exist.

Inspects `gh issue create|edit` / `gh pr create|edit` / `gh issue|pr comment`
invocations that pass a `--body-file <path>` argument.  The hook reads the body
file and extracts markdown link targets (`[text](./target)`) that look like
relative repository paths.  For each candidate path it checks whether the path
exists under the repository root.  Any missing paths are reported to stderr as a
advisory nudge — the hook never blocks (exit 0 always).

Phase 1 scope (issue #324):
  - Trigger: `gh (issue|pr) (create|edit|comment)` with `--body-file <path>`
    or `--body-file=<path>` (and the `-F` alias).
  - Extraction: markdown link syntax `](<path>)` where <path> starts with
    one of the recognized repo-relative prefixes:
      `./`  `docs/`  `hooks/`  `skills/`  `tests/`  `scripts/`  `manifests/`
  - Existence check: `os.path.exists(repo_root / path)`.
  - Repo root: resolved via `git rev-parse --show-toplevel` from the directory
    containing the body file; falls back to `payload["cwd"]` if git is
    unavailable.
  - Dedup: per session_id + body-file path hash so repeated calls with the
    same file do not spam the same advisory.
  - Fail-open on any infrastructure error (malformed stdin, missing git,
    unreadable file, etc.).

Phase 2 (deferred): inline-code path tokens (`` `hooks/foo.sh` ``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Resolve sibling `_hook_utils.py` regardless of cwd at invocation time.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Repo-relative path prefixes that are in scope for Phase 1.
REPO_RELATIVE_PREFIXES = (
    "./",
    "docs/",
    "hooks/",
    "skills/",
    "tests/",
    "scripts/",
    "manifests/",
)

# Markdown link target: `](path)` — capture the path portion.
# Non-greedy so `](a.md) and ](b.md)` yields two separate matches.
_MD_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")

# gh subcommand pairs that accept --body-file and write to external surfaces.
_GH_BODY_FILE_SUBCOMMANDS = frozenset({
    ("issue", "create"),
    ("issue", "edit"),
    ("issue", "comment"),
    ("pr", "create"),
    ("pr", "edit"),
    ("pr", "comment"),
})

_GH_GLOBAL_FLAGS_WITH_ARG = frozenset({"-R", "--repo", "--hostname", "--color"})
_GH_BODY_FILE_FLAGS = frozenset({"-F", "--body-file"})

# State dir for session-scoped dedup.
_STATE_DIR = Path(
    os.environ.get("PRAXIS_STATE_DIR", os.path.expanduser("~/.claude/state/praxis"))
) / "phantom-path"


# ---------------------------------------------------------------------------
# gh command parsing
# ---------------------------------------------------------------------------

def _parse_gh_body_file(argv: list[str]) -> str | None:
    """Return the --body-file path from a gh argv, or None if not present.

    Only fires for gh (issue|pr) (create|edit|comment) subcommands.
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "gh":
        return None

    # Skip global flags to reach object/subcommand pair.
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in _GH_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            i += 1

    if i + 1 >= len(argv):
        return None
    obj, sub = argv[i], argv[i + 1]
    if (obj, sub) not in _GH_BODY_FILE_SUBCOMMANDS:
        return None

    # Scan remaining tokens for --body-file / -F.
    rest = argv[i + 2:]
    for j, tok in enumerate(rest):
        if "=" in tok:
            key, _, val = tok.partition("=")
            if key in _GH_BODY_FILE_FLAGS:
                return val
        elif tok in _GH_BODY_FILE_FLAGS and j + 1 < len(rest):
            return rest[j + 1]
    return None


# ---------------------------------------------------------------------------
# Repo root resolution
# ---------------------------------------------------------------------------

def _git_toplevel(start_dir: str) -> str | None:
    """Run `git rev-parse --show-toplevel` from start_dir.  Returns None on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _resolve_repo_root(body_file_path: str, cwd: str) -> str:
    """Determine the repository root relative to the body file's directory.

    Priority:
    1. git -C <dir-of-body-file> rev-parse --show-toplevel
    2. git -C <cwd> rev-parse --show-toplevel
    3. Fallback: cwd as-is (fail-open)
    """
    body_dir = os.path.dirname(os.path.abspath(body_file_path))
    root = _git_toplevel(body_dir) or _git_toplevel(cwd)
    return root or cwd


# ---------------------------------------------------------------------------
# Path extraction
# ---------------------------------------------------------------------------

def _extract_candidate_paths(body: str) -> list[str]:
    """Extract markdown link targets that look like repo-relative paths."""
    candidates: list[str] = []
    for m in _MD_LINK_RE.finditer(body):
        target = m.group(1)
        # Ignore http/https/mailto/anchor links.
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if any(target.startswith(pfx) for pfx in REPO_RELATIVE_PREFIXES):
            # Strip leading `./` so the path is always relative-to-root.
            candidates.append(target.lstrip("./") if target.startswith("./") else target)
    # Dedupe while preserving order.
    seen: set[str] = set()
    result: list[str] = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


# ---------------------------------------------------------------------------
# Session-scoped dedup
# ---------------------------------------------------------------------------

def _dedup_key(session_id: str, body_file_path: str) -> str:
    """Return a hash key combining session_id and body file path."""
    raw = f"{session_id}:{os.path.abspath(body_file_path)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _already_reported(key: str) -> bool:
    marker = _STATE_DIR / key
    return marker.exists()


def _mark_reported(key: str) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        (_STATE_DIR / key).touch()
    except OSError:
        pass  # fail-open — dedup is best-effort


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    if payload.get("tool_name") != "Bash":
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    command = tool_input.get("command", "") or ""
    if not command.strip():
        return 0

    cwd = payload.get("cwd", "") or os.getcwd()
    session_id = payload.get("session_id", "") or "unknown"

    command = command.replace("\\\n", " ")
    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    # Collect body files from all gh sub-commands in the Bash command.
    body_files: list[str] = []
    for argv in iter_command_starts(tokens):
        bf = _parse_gh_body_file(list(argv))
        if bf:
            body_files.append(bf)

    if not body_files:
        return 0

    for body_file in body_files:
        # Resolve path relative to cwd if not absolute.
        if not os.path.isabs(body_file):
            body_file = os.path.join(cwd, body_file)

        # Skip unreadable files — fail-open.
        if not os.path.isfile(body_file):
            continue

        # Session-scoped dedup.
        dk = _dedup_key(session_id, body_file)
        if _already_reported(dk):
            continue

        try:
            with open(body_file, encoding="utf-8") as fh:
                body = fh.read()
        except OSError:
            continue

        candidates = _extract_candidate_paths(body)
        if not candidates:
            continue

        repo_root = _resolve_repo_root(body_file, cwd)

        phantom: list[str] = [
            p for p in candidates
            if not os.path.exists(os.path.join(repo_root, p))
        ]

        if phantom:
            _mark_reported(dk)
            lines = "\n".join(f"  • {p}" for p in phantom)
            sys.stderr.write(
                f"[phantom-path] {len(phantom)} referenced path(s) do not exist "
                f"in repo root ({repo_root}):\n{lines}\n"
                "Verify paths before posting — phantom links confuse readers and "
                "review tools.\n"
                "Set PRAXIS_PHANTOM_PATH_STRICT=1 to convert this advisory into "
                "a hard block (exit 2).\n"
            )
            if os.environ.get("PRAXIS_PHANTOM_PATH_STRICT") == "1":
                return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
