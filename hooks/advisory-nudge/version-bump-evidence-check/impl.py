#!/usr/bin/env python3
"""PreToolUse advisory: require changelog evidence before posting external version-bump issues/PRs.

Agents sometimes self-rationalize bypassing verification gates for "mechanical"
single-line version bumps (e.g. bumping an SDK constant from v24 to v25) and
create GitHub issues or PRs without fetching the official changelog or
breaking-changes docs. This hook catches that pattern at the last checkpoint
before shared-state mutation.

Trigger surface (PreToolUse(Bash)):
  - gh issue create (any flags)
  - gh issue edit ... --body ... / --body-file ...
  - gh pr create (any flags)

Detection (body content):
  Three signal types, each counted separately:
    S1. Version-pair regex: v\\d+\\.\\d+ (→|->|to) v\\d+\\.\\d+
    S2. Bump phrase:        bump (sdk|api|client|lib|library|version) from ... to ...
    S3. Deprecation keyword + version pair present: (deprecated / deprecation /
        sunset / EOL / end of life / breaking change)

  S3 alone (no version pair) does NOT trigger — reduces false positives on
  general retrospects or changelogs that mention "deprecated" in prose.

Required evidence (body must contain at least one of):
  E1. Changelog URL: https?://.../(releases|changelog|breaking|release-notes|...)
  E2. "Fetched:" line
  E3. "Cross-reference matrix" or "Inventory" heading
  E4. Bypass env var: PRAXIS_VERSION_BUMP_BYPASS=1

Modes:
  advisory (default): stderr warning, exit 0
  strict (PRAXIS_VERSION_BUMP_STRICT=1): exit 2 to hard-block

Fail-open on any infrastructure error (malformed stdin, unreadable body file).

Reuses body-extraction logic from external-write-falsify-check.py / sibling hooks.
"""
from __future__ import annotations

import json
import os
import re
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# gh subcommand pairs that accept a body and write to shared-state surfaces.
_GH_BODY_SUBCOMMANDS = frozenset({
    ("issue", "create"),
    ("issue", "edit"),
    ("pr", "create"),
    ("pr", "edit"),
})

_GH_GLOBAL_FLAGS_WITH_ARG = frozenset({"-R", "--repo", "--hostname", "--color"})
_GH_BODY_FLAGS = frozenset({"-b", "--body", "-F", "--body-file"})


# ---------------------------------------------------------------------------
# Signal patterns
# ---------------------------------------------------------------------------

# S1: explicit version-pair with arrow / "to".
# Matches: v24.0 → v25.0 / v1.2 -> v1.3 / v1.2-->v1.3 / v1.2 to v1.3
# Case-insensitive so "V1.2 → V1.3" matches too.
#
# Round-2 critic M1: arrow alternation accepts repeated dashes (`-+>`) so
# agent-authored bodies like `v1.2-->v1.3` without surrounding whitespace
# are caught.
#
# Round-2 critic M2: requires `v`/`V` prefix on at least one side to cut
# language-runtime false positives (`Python 3.11 to 3.12`, `Java 11 to 17`
# would otherwise trip strict-mode blocks on legit retrospect bodies).
# Library / SDK bumps in PR bodies near-universally carry the `v` prefix.
_VERSION_PAIR_RE = re.compile(
    r"(?:"
    # left side has v/V prefix; right side optional
    r"[Vv]\d+\.\d+\s*(?:→|-+>|to)\s*[Vv]?\d+\.\d+"
    r"|"
    # right side has v/V prefix; left side bare (numeric only)
    r"\d+\.\d+\s*(?:→|-+>|to)\s*[Vv]\d+\.\d+"
    r")",
    re.IGNORECASE,
)

# S2: "bump X from Y to Z" phrase (common in Dependabot / manual bump PRs).
_BUMP_PHRASE_RE = re.compile(
    r"\bbump\s+(?:sdk|api|client|lib|library|version)\s+from\s+\S+\s+to\s+\S+",
    re.IGNORECASE,
)

# S3: deprecation / sunset / EOL keywords.
_DEPRECATION_KEYWORDS = (
    "deprecation",
    "deprecated",
    "sunset",
    " eol",      # leading space avoids matching "protocol" etc.
    "end of life",
    "breaking change",
    "breaking-change",
)


def _has_version_pair(text: str) -> str | None:
    """Return the first version-pair match string, or None."""
    m = _VERSION_PAIR_RE.search(text)
    return m.group(0) if m else None


def _has_bump_phrase(text: str) -> str | None:
    """Return the first bump-phrase match string, or None."""
    m = _BUMP_PHRASE_RE.search(text)
    return m.group(0) if m else None


def _has_deprecation_keyword(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _DEPRECATION_KEYWORDS)


# ---------------------------------------------------------------------------
# Evidence patterns
# ---------------------------------------------------------------------------

# E1: Official changelog / release-notes / breaking-changes URL.
# Allow-list of recognizable URL shapes — grow conservatively to keep FP low.
_CHANGELOG_URL_RE = re.compile(
    r"https?://[a-z0-9A-Z._-]+/"  # scheme + host
    r"(?:"
    r"[^\s]*releases[^\s]*"       # .../releases (GitHub, PyPI, npm, etc.)
    r"|[^\s]*changelog[^\s]*"     # .../changelog
    r"|[^\s]*CHANGELOG[^\s]*"     # .../CHANGELOG (raw file links)
    r"|[^\s]*release-notes[^\s]*" # .../release-notes
    r"|[^\s]*breaking[^\s]*"      # .../breaking-changes
    r"|[^\s]*migration[^\s]*"     # .../migration-guide
    r"|[^\s]*upgrade[^\s]*"       # .../upgrade-guide
    r"|[^\s]*whats-new[^\s]*"     # .../whats-new
    r"|[^\s]*what-is-new[^\s]*"   # .../what-is-new
    r")",
    re.IGNORECASE,
)

# E2: "Fetched:" line (agent convention for documenting fetched sources).
# Match at real newline (MULTILINE ^) OR after literal \n (from shell single-quoted body).
_FETCHED_LINE_RE = re.compile(
    r"(?:^|\\n)\s*fetched\s*:",
    re.IGNORECASE | re.MULTILINE,
)

# E3: "Cross-reference matrix" or "Inventory" heading in markdown.
# Match at real newline OR after literal \n (shell single-quoted body passes \n literally).
_MATRIX_HEADING_RE = re.compile(
    r"(?:^|\\n)#+\s+(?:cross.?reference\s+matrix|inventory)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _has_evidence(body: str) -> bool:
    """Return True if the body contains at least one acceptable evidence signal."""
    if _CHANGELOG_URL_RE.search(body):
        return True
    if _FETCHED_LINE_RE.search(body):
        return True
    if _MATRIX_HEADING_RE.search(body):
        return True
    return False


# ---------------------------------------------------------------------------
# Body extraction (reused from external-write-falsify-check.py / sibling pattern)
# ---------------------------------------------------------------------------

def _resolve_body(flag: str, value: str) -> str:
    """Read body content. For --body-file, read file contents (fail-open)."""
    if flag in {"-F", "--body-file"}:
        if value == "-":
            return ""  # stdin — uninspectable at PreToolUse time
        try:
            with open(value, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""  # fail-open: missing / unreadable → empty
    return value


def _extract_gh_body(argv: list[str]) -> str | None:
    """Pull body text from --body / --body-file in a gh argv; None if absent.

    Matches the extraction approach in external-write-falsify-check.py to ensure
    consistent behaviour across the hook family.
    """
    for i, tok in enumerate(argv):
        if "=" in tok:
            key, _, val = tok.partition("=")
            if key in _GH_BODY_FLAGS:
                return _resolve_body(key, val)
            continue
        if tok in _GH_BODY_FLAGS and i + 1 < len(argv):
            return _resolve_body(tok, argv[i + 1])
    return None


def _is_gh_bump_target(argv: list[str]) -> bool:
    """Return True iff argv invokes a gh subcommand that is in scope for this hook."""
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
        if "=" not in tok and tok in _GH_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            i += 1

    if i + 1 >= len(argv):
        return False
    obj, sub = argv[i], argv[i + 1]
    return (obj, sub) in _GH_BODY_SUBCOMMANDS


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _detect_bump_signal(body: str) -> tuple[bool, str]:
    """Return (triggered, description) where description names the matched signal.

    Rules:
      - S1 alone (version pair)               → triggered
      - S2 alone (bump phrase)                → triggered
      - S1 + S2 combined                       → triggered
      - S3 alone (deprecation keyword only)    → NOT triggered (false-positive control)
      - S3 + S1 is implicitly covered by the S1 branch — the deprecation
        keyword adds no signal beyond the version pair itself.
    """
    version_pair = _has_version_pair(body)
    bump_phrase = _has_bump_phrase(body)

    if version_pair and bump_phrase:
        return True, f"version pair ({version_pair.strip()}) + bump phrase"
    if version_pair:
        return True, f"version pair ({version_pair.strip()})"
    if bump_phrase:
        return True, f"bump phrase ({bump_phrase.strip()})"
    return False, ""


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_PREFIX = "[praxis:version-bump-evidence]"

_ADVISORY_TEMPLATE = """\
{prefix} External version bump detected: {signal}
{prefix} Required evidence missing: official changelog URL, cross-reference matrix, or Fetched: lines
{prefix} Fix: fetch the official changelog/breaking-changes page first, then include
  a cross-reference matrix in the body before creating the issue/PR.
{prefix} Bypass: PRAXIS_VERSION_BUMP_BYPASS=1
"""

_STRICT_SUFFIX = (
    "{prefix} STRICT MODE: set PRAXIS_VERSION_BUMP_STRICT=1 is active — "
    "call blocked until evidence is present.\n"
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    # Bypass env var — intentional one-off skip.
    if os.environ.get("PRAXIS_VERSION_BUMP_BYPASS") == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    tool_name = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input", {}) or {}

    if tool_name != "Bash":
        return 0

    command = tool_input.get("command", "") or ""
    if not command.strip():
        return 0

    # Normalize line-continuations.
    command = command.replace("\\\n", " ")

    try:
        tokens = safe_tokenize(command)
    except Exception:
        return 0  # fail-open on tokenization error

    if not tokens:
        return 0

    # Collect bodies from all gh sub-commands in the Bash command.
    all_bodies: list[str] = []
    for argv in iter_command_starts(tokens):
        if _is_gh_bump_target(list(argv)):
            body = _extract_gh_body(list(argv))
            if body is not None:
                all_bodies.append(body)

    if not all_bodies:
        return 0

    combined = "\n".join(all_bodies)

    # Run detection on combined body.
    triggered, signal_desc = _detect_bump_signal(combined)
    if not triggered:
        return 0

    # Check for evidence.
    if _has_evidence(combined):
        return 0  # evidence present — no advisory needed

    # Emit advisory.
    msg = _ADVISORY_TEMPLATE.format(prefix=_PREFIX, signal=signal_desc)
    sys.stderr.write(msg)

    strict = os.environ.get("PRAXIS_VERSION_BUMP_STRICT") == "1"
    if strict:
        sys.stderr.write(_STRICT_SUFFIX.format(prefix=_PREFIX))
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
