#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `gh pr create` without pre-commit evidence.

Blocks `gh pr create/new` unless the effective PR body contains ONE of:

  Pre-commit verified: <free text>
  Pre-commit: verified by CI (<free text>)
  Pre-commit: n/a (<free text reason>)

Background:
  `verify-commit-flag-override` blocks `--no-verify` bypass, but only
  *if* the repo has a pre-commit hook to be bypassed. Repos without any
  pre-commit configuration (or with placeholder hooks) have no
  structural guard. prompt-layer `Verification Before Completion` /
  `Evidence-Based Delivery` rules cover this gap nominally but retrieval
  fails under task momentum — same failure family as praxis #158 / #234.

  This hook closes the gap by requiring the PR author to declare the
  pre-commit verification state explicitly in the PR body. Free-form
  reason text — the audit trail is the persistence, not the format.

Allow conditions:
  1. --help / -h invocation
  2. --template/-T without --body/-b (interactive fill-in)
  3. Effective body contains a Pre-commit evidence marker line

Block conditions for --body-file (mirror sibling
block-pr-without-caller-evidence):
  - `--body-file -` (stdin): uninspectable at PreToolUse time
  - Missing path: redirect side-effect not yet executed
  - Same-command overwrite (TOCTOU): file content is stale

Differs from sibling:
  - `--repo`/`-R` does NOT bypass. The --repo value IS the verification
    target repo, so bypass would defeat the hook's purpose.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Three accepted evidence patterns. Horizontal-whitespace-only after the
# colon to prevent newline bypass (sibling-mirror): "Pre-commit verified:\n
# ## Next section" must NOT pass. Case-insensitive.
_PRECOMMIT_VERIFIED_RE = re.compile(
    r"(?m)^Pre-commit verified:[ \t]*\S",
    re.IGNORECASE,
)
_PRECOMMIT_CI_RE = re.compile(
    r"(?m)^Pre-commit:[ \t]*verified by CI[ \t]*\S",
    re.IGNORECASE,
)
_PRECOMMIT_NA_RE = re.compile(
    r"(?m)^Pre-commit:[ \t]*n/a[ \t]*\S",
    re.IGNORECASE,
)

# Closed fenced code blocks (``` or ~~~) — strip so markers inside don't count.
_FENCED_CLOSED_RE = re.compile(
    r"^(`{3,}|~{3,}).*?^\1[ \t]*$", re.DOTALL | re.MULTILINE
)
# Unclosed fence — strip from opener to end of body.
_FENCED_OPEN_RE = re.compile(r"^(`{3,}|~{3,}).*\Z", re.DOTALL | re.MULTILINE)

# $VAR or ${VAR} references (uppercase-snake convention).
_VAR_RE = re.compile(r"\$(?:\{([A-Z_][A-Z0-9_]*)\}|([A-Z_][A-Z0-9_]*))")

# VAR=$(cat <<TAG ... TAG) assignment heredoc extraction.
_HEREDOC_ASSIGN_RE = re.compile(
    r"([A-Z_][A-Z0-9_]*)\s*=\s*\$\(\s*cat\s+<<\s*['\"]?(\w+)['\"]?\s*\n"
    r"(.*?)\n\2\s*\)",
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# Body helpers
# ---------------------------------------------------------------------------

def _strip_fenced_blocks(body: str) -> str:
    body = _FENCED_CLOSED_RE.sub("", body)
    return _FENCED_OPEN_RE.sub("", body)


def _build_heredoc_map(command: str) -> dict[str, str]:
    return {m.group(1): m.group(3) for m in _HEREDOC_ASSIGN_RE.finditer(command)}


def _resolve_vars(s: str, hmap: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        name = m.group(1) or m.group(2)
        return hmap.get(name, m.group(0))
    return _VAR_RE.sub(sub, s)


def _safe_read(p: Path) -> str:
    try:
        return p.read_text()
    except OSError:
        return ""


def _path_is_overwritten_in_raw(command: str, path: str) -> bool:
    quoted = re.escape(path)
    patterns = (
        rf">>?\s*['\"]?{quoted}['\"]?(?:\s|$)",
        rf"\btee\b(?:\s+-a)?\s+['\"]?{quoted}['\"]?",
    )
    return any(re.search(p, command) for p in patterns)


def _get_effective_body(argv: list[str], hmap: dict[str, str], command: str) -> str:
    parts: list[str] = []
    for i, t in enumerate(argv):
        if t in ("--body", "-b") and i + 1 < len(argv):
            parts.append(_resolve_vars(argv[i + 1], hmap))
        elif t.startswith("--body="):
            parts.append(_resolve_vars(t.split("=", 1)[1], hmap))
        elif t == "--body-file" and i + 1 < len(argv):
            path = argv[i + 1]
            if path == "-":
                continue
            if _path_is_overwritten_in_raw(command, path):
                continue
            p = Path(path).expanduser()
            if p.is_file():
                parts.append(_safe_read(p))
        elif t.startswith("--body-file="):
            path = t.split("=", 1)[1]
            if path == "-":
                continue
            if _path_is_overwritten_in_raw(command, path):
                continue
            p = Path(path).expanduser()
            if p.is_file():
                parts.append(_safe_read(p))
    return "\n".join(parts)


def _has_precommit_marker(body: str) -> bool:
    return (
        _PRECOMMIT_VERIFIED_RE.search(body) is not None
        or _PRECOMMIT_CI_RE.search(body) is not None
        or _PRECOMMIT_NA_RE.search(body) is not None
    )


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _is_pr_create(argv: list[str]) -> bool:
    argv = strip_prefix(argv)
    if not argv or argv[0] != "gh":
        return False
    if any(t in ("--help", "-h") or t.startswith(("--help=", "-h="))
           for t in argv):
        return False
    try:
        pr_idx = argv.index("pr")
    except ValueError:
        return False
    return pr_idx + 1 < len(argv) and argv[pr_idx + 1] in ("create", "new")


def _uses_template_without_body(argv: list[str]) -> bool:
    has_template = any(
        t in ("--template", "-T") or t.startswith("--template=")
        for t in argv
    )
    if not has_template:
        return False
    return not any(
        t in ("--body", "-b", "--body-file")
        or t.startswith(("--body=", "--body-file="))
        for t in argv
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

BLOCK_MSG = """\
❌ BLOCKED: `gh pr create` without pre-commit evidence.

Add ONE of these lines to the PR body:

  Pre-commit verified: <command + result>
  Pre-commit: verified by CI (<workflow path or url>)
  Pre-commit: n/a (<reason>)

Reason / evidence text is free-form — describe the actual state.

Why (praxis #406):
  `verify-commit-flag-override` blocks --no-verify bypass but only when
  the repo HAS a pre-commit hook. Repos with no pre-commit config (or
  placeholder hooks) have no structural guard, and Verification Before
  Completion / Evidence-Based Delivery rules fail to retrieve under
  task momentum. This hook converts the rule into a hard gate at the
  last checkpoint before shared-state mutation.

Note: --repo does NOT bypass this hook (differs from
block-pr-without-caller-evidence). The --repo value IS the verification
target, so bypass would defeat the purpose.
"""


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    command = command.replace("\\\n", " ")
    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    hmap = _build_heredoc_map(command)

    for argv in iter_command_starts(tokens):
        argv = list(argv)
        if not _is_pr_create(argv):
            continue
        if _uses_template_without_body(argv):
            continue
        body = _get_effective_body(argv, hmap, command)
        if _has_precommit_marker(_strip_fenced_blocks(body)):
            continue
        sys.stderr.write(BLOCK_MSG + compound_cascade_hint(command))
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
