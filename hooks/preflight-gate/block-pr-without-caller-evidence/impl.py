#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `gh pr create` without caller-chain evidence.

Blocks `gh pr create/new` unless the effective PR body contains a
`Caller chain verified:` line, enforcing a pre-PR caller-chain grep habit.

Background:
  Five converging memory rules encode the same procedure (caller-chain grep
  before PR), but memory-only enforcement fails under cognitive load (praxis
  issue #158). This hook enforces the rule at the last checkpoint before
  shared-state mutation.

Tiering (issue #1186): deny only when PRAXIS_PR_EVIDENCE_STRICT is set
truthy; on shipped defaults the message ships as a stderr advisory and
the create proceeds (attested-convention principle, #1159).

Allow conditions:
  1. --help / -h invocation (read-only introspection)
  2. --repo/-R targets a different project (cross-project PR)
  3. --template/-T without --body/-b (interactive fill-in; body filled after)
  4. Effective body contains /^Caller chain verified:[ \\t]*\\S/i

Block conditions for --body-file:
  - `--body-file -` (stdin): the pipe content is uninspectable at PreToolUse
    time. Treated as empty body → block fires unless inline --body marker
    accompanies it. Allowing stdin bypassed the hard-gate (Codex round 3).
  - Path missing at PreToolUse time → treated as empty body so the marker
    check fires. This closes the `cat <<EOF > /tmp/body.md && gh pr create
    --body-file /tmp/body.md` cascade bypass, where the redirect side-effect
    has not run yet and the file does not exist at hook time.
  - Path readable but content has no marker → block (standard case).

Accepted line forms (all satisfy condition 4):
  Caller chain verified: grep found 3 callers in src/providers/
  Caller chain verified: new symbol, no caller expected
  Caller chain verified: planned caller in #<followup>
  Caller chain verified: N/A — docs-only change

Body sources resolved (in order):
  --body / -b  →  literal value or $VAR from earlier assignment
  --body-file PATH  →  file read if present; missing/unreadable → empty body
                       (falls through to marker check; blocks unless inline
                       marker also present)

Note: env/sudo/command prefix wrappers are transparent via strip_prefix().
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]
    _is_gh_binary,
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import pr_body_evidence_checklist  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Horizontal-whitespace-only after the colon to prevent newline bypass:
#   "Caller chain verified:\n## Next section" must NOT pass.
CALLER_CHAIN_RE = re.compile(
    r"(?m)^Caller chain verified:[ \t]*\S",
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
    """Read the file at p, returning empty string on any OS-level failure.

    Advisory contract: hook infrastructure errors must fail open (block
    check sees empty body, marker check fires unless inline marker exists).
    A bare p.read_text() would raise OSError on permission-denied / TOCTOU
    races / non-text encodings and crash the PreToolUse hook itself.
    """
    try:
        return p.read_text()
    except OSError:
        return ""


def _path_is_overwritten_in_raw(command: str, path: str) -> bool:
    """True if `path` appears as a redirect/write target in the raw command.

    Catches TOCTOU bypass where a pre-existing marker file is overwritten
    by the same Bash invocation before `gh pr create --body-file <path>`:

        echo no-marker > /tmp/body.md && gh pr create --body-file /tmp/body.md
        printf bad | tee /tmp/body.md && gh pr create --body-file /tmp/body.md

    PreToolUse reads the OLD file content (marker present, passes the gate),
    but bash then overwrites it before gh sees it. Treating these as empty
    body forces the marker check to fire on the inline portion only.
    """
    quoted = re.escape(path)
    patterns = (
        rf">>?\s*['\"]?{quoted}['\"]?(?:\s|$)",          # > path / >> path
        rf"\btee\b(?:\s+-a)?\s+['\"]?{quoted}['\"]?",     # tee path / tee -a path
    )
    return any(re.search(p, command) for p in patterns)


def _get_effective_body(argv: list[str], hmap: dict[str, str], command: str) -> str:
    """Return the effective PR body text for marker inspection.

    Uninspectable / untrustworthy sources (stdin, missing file, unreadable
    file, file overwritten in same command) contribute empty string so the
    marker check fires and the block path triggers. Closes hard-gate
    bypasses identified by Codex:
      - stdin (`--body-file -`) — pipe content unknowable at PreToolUse time
      - missing path — `cat <<EOF > /tmp/x && gh pr create --body-file /tmp/x`
        cascade where the redirect side-effect has not yet executed
      - same-command overwrite — `echo x > /tmp/x && gh pr create --body-file /tmp/x`
        where current file content is stale relative to what gh will see
    """
    parts: list[str] = []
    for i, t in enumerate(argv):
        if t in ("--body", "-b") and i + 1 < len(argv):
            parts.append(_resolve_vars(argv[i + 1], hmap))
        elif t.startswith("--body="):
            parts.append(_resolve_vars(t.split("=", 1)[1], hmap))
        elif t == "--body-file" and i + 1 < len(argv):
            path = argv[i + 1]
            if path == "-":
                continue  # stdin — empty contribution → fall through to block
            if _path_is_overwritten_in_raw(command, path):
                continue  # TOCTOU: current content is stale → trust nothing
            p = Path(path).expanduser()
            if p.is_file():
                parts.append(_safe_read(p))
            # missing file → empty contribution → falls through to marker check
        elif t.startswith("--body-file="):
            path = t.split("=", 1)[1]
            if path == "-":
                continue  # stdin — empty contribution → fall through to block
            if _path_is_overwritten_in_raw(command, path):
                continue  # TOCTOU: current content is stale → trust nothing
            p = Path(path).expanduser()
            if p.is_file():
                parts.append(_safe_read(p))
            # missing file → empty contribution → falls through to marker check
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _is_pr_create(argv: list[str]) -> bool:
    """True if argv is `gh pr create/new` (not --help)."""
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
        return False
    if any(t in ("--help", "-h") or t.startswith(("--help=", "-h="))
           for t in argv):
        return False
    try:
        pr_idx = argv.index("pr")
    except ValueError:
        return False
    return pr_idx + 1 < len(argv) and argv[pr_idx + 1] in ("create", "new")


def _has_repo_flag(argv: list[str]) -> bool:
    """True if --repo/-R is present (any value).

    NOTE: Does NOT compare the value against the current repo. Passing
    --repo with the current repo's owner/name also satisfies this gate.
    Treat the flag's presence as an explicit operator assertion that
    the caller-chain step was done out-of-band.
    """
    for i, t in enumerate(argv):
        if t in ("-R", "--repo") and i + 1 < len(argv):
            return True
        if t.startswith(("--repo=", "-R")) and len(t) > 2:
            return True
    return False


def _uses_template_without_body(argv: list[str]) -> bool:
    """True if --template/-T present but no explicit --body/-b."""
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

def _strict() -> bool:
    """Attested-convention tiering (issue #1186, principle from #1159).

    The marker line this gate demands is a praxis-invented convention with
    no other local attestation signal (no regex env, no detectable
    dependency), so the only attestation is the explicit
    PRAXIS_PR_EVIDENCE_STRICT env — shared by both PR-marker gates. Truthy
    (anything but "0"/empty) -> deny as before; unset/empty/"0" -> the same
    message ships as a stderr advisory and the create proceeds.
    """
    return os.environ.get("PRAXIS_PR_EVIDENCE_STRICT", "").strip() not in ("", "0")


_ADVISORY_HEADER = (
    "[advisory] marker convention not attested locally — gh pr create will "
    "proceed. Set PRAXIS_PR_EVIDENCE_STRICT=1 to enforce this gate as a "
    "deny (issue #1186).\n"
)

BLOCK_MSG = """\
❌ BLOCKED: `gh pr create` without caller-chain evidence.

Add a `Caller chain verified:` line to the PR body first:

  Caller chain verified: grep found N callers in <path>
  Caller chain verified: new symbol, no caller expected
  Caller chain verified: planned caller in #<followup>
  Caller chain verified: N/A -- docs-only change

Why (praxis #158):
  Five converging memory rules encode "grep caller chain before PR" but
  memory-only enforcement fails under cognitive load. This hook is the
  hard gate at the last checkpoint before shared-state mutation.

Cross-project PRs (--repo other/org) are not blocked.
"""


@fail_open
def main() -> int:
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed
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
        if _has_repo_flag(argv):
            continue  # cross-project PR — skip
        if _uses_template_without_body(argv):
            continue  # interactive template fill-in
        body = _get_effective_body(argv, hmap, command)
        if CALLER_CHAIN_RE.search(_strip_fenced_blocks(body)):
            continue  # evidence present
        if _strict():
            sys.stderr.write(
                BLOCK_MSG + pr_body_evidence_checklist()
                + compound_cascade_hint(command)
            )
            return 2
        # Advisory tier (#1186): tier-neutral first line (nothing was
        # blocked), NO cascade hint (it describes an abort that did not
        # happen), and `continue` so later segments of a compound command
        # are still scanned.
        sys.stderr.write(
            _ADVISORY_HEADER
            + BLOCK_MSG.replace("\u274c BLOCKED:", "\u26a0 missing:", 1)
            + pr_body_evidence_checklist()
        )
        continue

    return 0


if __name__ == "__main__":
    sys.exit(main())
