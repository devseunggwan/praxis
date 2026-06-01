#!/usr/bin/env python3
"""PreToolUse advisory: warn before leaking absolute home-dotfiles paths into
external-surface writes.

Scope (deliberately narrow — see spec.md "Scope and non-goals"):
  This hook is a *crude, deterministic backstop* for ONE leak form: an absolute
  machine path into a hidden home-config directory (`/Users/<name>/.claude/...`,
  `/home/<name>/.config/...`) pasted into a public/shared write. That path leaks
  the OS username and local machine layout and, once published, carries a
  retraction cost.

  It does NOT — and a regex cannot — catch the more damaging *semantic* leak
  form the global rule "No Personal-Asset Surface" targets: unsolicited routing
  recommendations toward personal assets ("X plugin fits better", "matches the
  sibling pattern", "create the issue in X"). Those are legitimate words in a
  legitimate sentence; only agent-side retrieval discipline catches them. This
  hook must not be treated as solving that — it is a backstop for the literal,
  detectable form only.

Detected write surface (mirrors external-write-falsify-check's gh pipeline):
  - Bash `gh issue/pr create|comment|edit|review` with a body flag
    (`--body` / `-b` / `--body-file` / `-F`)

  MCP slack/notion writes are intentionally OUT of scope: praxis has no wired
  MCP-matcher precedent and the `hosts: all` MCP-matcher behavior is unverified
  across the 5 target platforms. Wiring MCP is a follow-up once an MCP-matcher
  convention is established (see spec.md).

What is NOT flagged (false-positive boundary):
  - `~/.claude/...` tilde form — home-relative, exposes no username; it is the
    *recommended replacement* the advisory suggests.
  - `/Users/<name>/projects/...` worktree paths — no hidden-dir segment, so they
    do not match. (The user scoped this hook to dotfiles paths only.)

Exits 0 by default — advisory, not a block. Set
`PRAXIS_PERSONAL_LEAK_STRICT=1` to convert detection into a hard block (exit 2).
Fail-open on malformed stdin / unreadable body.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    _is_gh_binary,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)


# ---------------------------------------------------------------------------
# Personal-asset marker — absolute home path into a hidden config directory
# ---------------------------------------------------------------------------
# Matches /Users/<name>/.<hidden> and /home/<name>/.<hidden>. The leading
# /Users or /home + a username segment + a dot-prefixed directory is the
# signature of a machine-absolute dotfiles path. The dot-dir requirement is
# what excludes worktree paths like /Users/<name>/projects/... (no dot segment),
# and the absolute-prefix requirement is what excludes the portable ~/.claude
# tilde form (the safe replacement the advisory recommends).
#
# The username segment excludes angle brackets ([^/\s<>]+) so a documentation
# placeholder like `/Users/<name>/.claude` — which praxis specs and PR bodies
# write constantly — does NOT trip the advisory; only a concrete username leaks.
_DOTFILES_ABS_RE = re.compile(r"(?:/Users|/home)/[^/\s<>]+/\.[A-Za-z0-9._-]+")


def _find_leak_markers(body: str) -> list[str]:
    """Return de-duplicated absolute home-dotfiles paths found in body."""
    if not body:
        return []
    return list(dict.fromkeys(_DOTFILES_ABS_RE.findall(body)))


# ---------------------------------------------------------------------------
# Bash gh detection (mirrors external-write-falsify-check; 2nd occurrence —
# kept local per DRY-on-3rd, no shared-lib extraction yet)
# ---------------------------------------------------------------------------

GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})

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

# Heredoc-variable body resolution (mirrors block-pr-without-caller-evidence;
# 2nd occurrence, kept local per DRY-on-3rd). A common authored pattern is
# `BODY=$(cat <<EOF ... EOF)` in one segment then `gh issue create --body
# "$BODY"` — the tokenizer sees only the literal `$BODY`, so the heredoc
# content (where a leak would live) is never scanned. These two regexes + the
# resolver recover the heredoc body and substitute it before the marker scan.
# (cross-boundary-preflight hard-blocks a heredoc in the *same* segment as the
# gh call; the separate-segment assignment form passes it and reaches here.)
_VAR_RE = re.compile(r"\$(?:\{([A-Z_][A-Z0-9_]*)\}|([A-Z_][A-Z0-9_]*))")
_HEREDOC_ASSIGN_RE = re.compile(
    r"([A-Z_][A-Z0-9_]*)\s*=\s*\$\(\s*cat\s+<<\s*['\"]?(\w+)['\"]?\s*\n"
    r"(.*?)\n\2\s*\)",
    re.DOTALL,
)


def _build_heredoc_map(command: str) -> dict[str, str]:
    """Map VAR → heredoc body for each `VAR=$(cat <<TAG ... TAG)` in command."""
    return {m.group(1): m.group(3) for m in _HEREDOC_ASSIGN_RE.finditer(command)}


def _resolve_vars(s: str, hmap: dict[str, str]) -> str:
    """Substitute $VAR / ${VAR} with its heredoc body; leave unknown vars as-is."""
    def sub(m: re.Match[str]) -> str:
        name = m.group(1) or m.group(2)
        return hmap.get(name, m.group(0))
    return _VAR_RE.sub(sub, s)


def _resolve_body(flag: str, value: str, cwd: str, hmap: dict[str, str]) -> str:
    """Read body content. For --body-file, read file contents (best effort).

    Inline `--body` / `-b` values are var-resolved so a `--body "$BODY"`
    heredoc-variable body is scanned, not its literal `$BODY` token.

    A relative `--body-file` / `-F` path is resolved against the Bash tool's
    `cwd` (from the payload), not the hook process's own cwd — the two can
    differ, and resolving against the wrong directory would silently fail-open
    and miss a leak in the body file (mirrors external-write-path-existence-check).
    The path is var-resolved first so `-F "$BODYFILE"` also works.
    """
    value = _resolve_vars(value, hmap)
    if flag in {"-F", "--body-file"}:
        if not os.path.isabs(value):
            value = os.path.join(cwd, value)
        try:
            with open(value, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""  # unreadable file → empty body (advisory-only hook)
    return value


def _extract_gh_bodies(argv: list[str], cwd: str, hmap: dict[str, str]) -> list[str]:
    """Pull body text from EVERY --body / --body-file in a gh argv.

    A gh write command can carry more than one body source (`--body a --body b`,
    or `--body a --body-file f`). gh's own precedence (last-flag-wins for a
    repeated string flag) decides which is *published*, but as a leak detector
    we scan ALL provided body values: the leak must be caught regardless of
    which one gh ultimately uses, so collecting every value is premise-
    independent of gh's exact precedence and strictly safer than returning the
    first. Returns an empty list when no body flag is present.
    """
    bodies: list[str] = []
    for i, tok in enumerate(argv):
        if "=" in tok:
            key, _, val = tok.partition("=")
            if key in GH_BODY_FLAGS_WITH_ARG:
                bodies.append(_resolve_body(key, val, cwd, hmap))
            continue
        if tok in GH_BODY_FLAGS_WITH_ARG and i + 1 < len(argv):
            bodies.append(_resolve_body(tok, argv[i + 1], cwd, hmap))
    return bodies


def _is_gh_external_write(argv: list[str]) -> bool:
    """Return True iff argv invokes a gh subcommand that writes to a public surface."""
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
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
# Output
# ---------------------------------------------------------------------------

ADVISORY_TEMPLATE = (
    "REMINDER (Personal-Asset Leak / External-Surface Write): absolute "
    "home-dotfiles path(s) detected in body: {markers}\n"
    "An absolute path like `/Users/<name>/.claude/...` leaks your OS username "
    "and local machine layout into a published surface.\n"
    "  • Replace with the portable `~/.claude/...` form, or remove the path.\n"
    "  • This backstop only catches literal dotfiles paths — it does NOT catch "
    "semantic personal-asset surfacing (e.g. 'X plugin fits better'); that "
    "remains your responsibility.\n"
    "Set PRAXIS_PERSONAL_LEAK_STRICT=1 to convert this advisory into a hard "
    "block (exit 2).\n"
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    tool_name = payload.get("tool_name", "") or ""
    if tool_name != "Bash":
        return 0
    tool_input = payload.get("tool_input", {}) or {}
    command = tool_input.get("command", "") or ""
    if not command.strip():
        return 0
    command = command.replace("\\\n", " ")
    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    cwd = payload.get("cwd", "") or os.getcwd()
    hmap = _build_heredoc_map(command)
    all_bodies: list[str] = []
    for argv in iter_command_starts(tokens):
        if _is_gh_external_write(argv):
            all_bodies.extend(_extract_gh_bodies(argv, cwd, hmap))

    if not all_bodies:
        return 0

    markers: list[str] = []
    for b in all_bodies:
        markers.extend(_find_leak_markers(b))
    markers = list(dict.fromkeys(markers))

    if not markers:
        return 0

    sample = ", ".join(markers[:3])
    sys.stderr.write(ADVISORY_TEMPLATE.format(markers=sample))
    if os.environ.get("PRAXIS_PERSONAL_LEAK_STRICT") == "1":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
