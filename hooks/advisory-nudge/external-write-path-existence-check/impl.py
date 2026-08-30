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
  - Dedup: per session_id + body content SHA-256 so identical body content
    in different temp paths fires once, and an edited body re-fires.
  - Fail-open on any infrastructure error (malformed stdin, missing git,
    unreadable file, etc.).

Phase 2: inline-code path tokens (`` `hooks/foo.sh` ``) with repo-relative
prefix — extends extraction alongside Phase 1 markdown links.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    _is_gh_binary,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from _paths import praxis_state_dir  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402


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

# Inline code span: `hooks/foo.sh` — capture the content between backticks.
# Only matches single-backtick spans to avoid multi-backtick fenced code
# blocks.  The backtick cannot appear inside the span content.
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")

# Fenced code block (triple-backtick): strip before inline-code search to
# avoid false-positive phantom-path hits on sample snippets in PR bodies.
# Matches both labelled (```bash ... ```) and unlabelled (``` ... ```) fences.
_FENCED_BLOCK_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)

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

# State dir for session-scoped dedup. Durable root is ~/.praxis/state (#527);
# PRAXIS_STATE_DIR still overrides the base (back-compat). These markers are
# regenerable dedup state, so no legacy read-fallback is needed — a moved
# marker simply lets one advisory re-fire once.
_STATE_DIR = Path(praxis_state_dir()) / "phantom-path"


# ---------------------------------------------------------------------------
# gh command parsing
# ---------------------------------------------------------------------------

def _parse_gh_body_file(argv: list[str]) -> str | None:
    """Return the --body-file path from a gh argv, or None if not present.

    Only fires for gh (issue|pr) (create|edit|comment) subcommands.
    """
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
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
    """Extract repo-relative paths from markdown links and inline code spans.

    Sources:
    - Markdown link targets: ``](path)``
    - Inline code spans: `` `hooks/foo.sh` `` (Phase 2 — repo-relative prefix only)
    """
    candidates: list[str] = []

    for m in _MD_LINK_RE.finditer(body):
        target = m.group(1)
        # Ignore http/https/mailto/anchor-only links.
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        # Strip anchor fragment (#section) and query string (?q=1) — both are
        # not part of the filesystem path and would produce false-positive
        # phantom hits on otherwise-valid links like `docs/foo.md#section`.
        target = target.split("#", 1)[0].split("?", 1)[0]
        if not target:
            continue
        if any(target.startswith(pfx) for pfx in REPO_RELATIVE_PREFIXES):
            # Strip leading `./` so the path is always relative-to-root.
            # Use target[2:] rather than lstrip("./") to avoid mangling paths
            # like `./../escape.md` into `escape.md` (lstrip is greedy on
            # individual characters, not the literal prefix).
            candidates.append(target[2:] if target.startswith("./") else target)

    # Phase 2: inline code spans — `hooks/foo.sh` style references.
    # Strip fenced blocks first so unlabelled triple-backtick fences
    # (``` hooks/nonexistent.sh ```) don't produce phantom-path false positives.
    # Only repo-relative prefixes are in scope; `./` prefix is also accepted
    # and stripped consistently with the markdown-link handling above.
    body_no_fences = _FENCED_BLOCK_RE.sub("", body)
    for m in _INLINE_CODE_RE.finditer(body_no_fences):
        token = m.group(1).strip()
        # Take only the path component — stop at the first whitespace so that
        # inline command examples like `hooks/foo.py --help` do not produce a
        # phantom-path advisory for "hooks/foo.py --help" (which includes args
        # and would never match a real filesystem path).
        path_token = token.split()[0] if token else token
        # Strip anchor fragment and query string — mirrors Phase 1 markdown-link
        # handling so `docs/foo.md#section` and `hooks/bar.py?v=2` do not
        # produce a phantom hit on an otherwise-existing file.
        path_token = path_token.split("#", 1)[0].split("?", 1)[0]
        if not path_token:
            continue
        if any(path_token.startswith(pfx) for pfx in REPO_RELATIVE_PREFIXES):
            candidates.append(path_token[2:] if path_token.startswith("./") else path_token)

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

def _dedup_key(session_id: str, body_content: str) -> str:
    """Return a hash key combining session_id and body content SHA-256.

    Keyed on content rather than file path so that:
    - the same body in different temp paths (e.g. successive `gh pr edit`)
      only fires once, and
    - an edited body (phantom paths removed) generates a new key so the
      advisory fires again if new phantom paths are introduced.
    """
    raw = f"{session_id}:{hashlib.sha256(body_content.encode()).hexdigest()}"
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
    payload = read_payload(("Bash",))
    if payload is None:
        return 0  # non-Bash tool or malformed stdin — fail-open

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

        # Sniff head bytes for NUL (\x00) to detect binary files.  Binary
        # content is not valid markdown; skip to avoid false-positive matches
        # inside binary blobs (e.g. an accidentally passed ELF or image file).
        try:
            with open(body_file, "rb") as fh_bin:
                head = fh_bin.read(512)
            if b"\x00" in head:
                continue  # binary file — skip silently (fail-open)
        except OSError:
            continue  # fail-open — unreadable file

        try:
            with open(body_file, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except (OSError, UnicodeDecodeError):
            continue  # fail-open — unreadable file

        # Session-scoped dedup keyed on body content so that the same body
        # in a different temp path fires only once, and an edited body with
        # new phantom paths re-fires.
        dk = _dedup_key(session_id, body)
        if _already_reported(dk):
            continue

        candidates = _extract_candidate_paths(body)
        if not candidates:
            continue

        repo_root = _resolve_repo_root(body_file, cwd)
        real_repo_root = os.path.realpath(repo_root)

        def _is_phantom(p: str) -> bool:
            """Return True if path p should be treated as a phantom reference.

            A path is phantom when it does not exist inside the repo root.
            Paths that escape the repo root via `../` traversal are always
            phantom even when they happen to exist on the filesystem (e.g. a
            sibling worktree directory), because they reference content outside
            the repository boundary.
            """
            full = os.path.join(repo_root, p)
            if not os.path.exists(full):
                return True
            # Resolve symlinks/`../` so a path like `../sibling-worktree` that
            # physically exists outside the repo tree is still flagged.
            # Use separator-aware prefix comparison to avoid false negatives
            # when a sibling directory shares the repo root name as a prefix
            # (e.g. /tmp/praxis-wt-copy startswith /tmp/praxis-wt → True).
            resolved = os.path.realpath(full)
            return not (resolved == real_repo_root or resolved.startswith(real_repo_root + os.sep))

        phantom: list[str] = [p for p in candidates if _is_phantom(p)]

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
