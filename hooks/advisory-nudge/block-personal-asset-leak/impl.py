#!/usr/bin/env python3
"""PreToolUse advisory: warn before leaking personal-asset markers into
external-surface writes.

Two marker classes (see spec.md "Scope and non-goals"):

1. **Absolute home-dotfiles path** — always active. An absolute machine path
   into a hidden home-config directory (`/Users/<name>/.claude/...`,
   `/home/<name>/.config/...`) pasted into a public/shared write leaks the OS
   username and local machine layout and, once published, carries a retraction
   cost.

2. **Personal-repo reference** — opt-in via `PRAXIS_PERSONAL_REPO_OWNERS`
   (comma-separated owner handles). Matches `<owner>/<repo>` and
   `<owner>/<repo>#N` slugs for the declared owners. Fires only when the write
   TARGET is not itself one of those personal repos — referencing your own
   repo inside your own repo is fine; referencing it in a team surface
   violates the "Personal repo content isolation" rule. With the env var
   unset, this class AND the Write/Edit surface below are fully inactive —
   behavior is byte-identical to the dotfiles-only hook.

Detected write surfaces:
  - Bash `gh issue/pr create|comment|edit|review` with a body flag
    (`--body` / `-b` / `--body-file` / `-F`) — both marker classes. The
    personal-repo class resolves the write target from `--repo`/`-R` when
    present, else from the payload cwd's `git remote get-url origin`.
  - Write `content` / Edit `new_string` — only when
    `PRAXIS_PERSONAL_REPO_OWNERS` is set. The file write counts as a
    team-surface write when `file_path` resolves to a git worktree whose
    `origin` owner is NOT a declared personal owner and the path is not
    gitignored (scratch areas like `.omc/plans/` pass silently).

It does NOT — and a regex cannot — catch the more damaging *semantic* leak
form the global rule "No Personal-Asset Surface" targets: unsolicited routing
recommendations toward personal assets ("X plugin fits better", "matches the
sibling pattern", "create the issue in X"). Those are legitimate words in a
legitimate sentence; only agent-side retrieval discipline catches them. This
hook must not be treated as solving that — it is a backstop for the literal,
detectable forms only.

MCP slack/notion writes are intentionally OUT of scope: praxis has no wired
MCP-matcher precedent and the `hosts: all` MCP-matcher behavior is unverified
across the 5 target platforms. Wiring MCP is a follow-up once an MCP-matcher
convention is established (see spec.md).

What is NOT flagged (false-positive boundary):
  - `~/.claude/...` tilde form — home-relative, exposes no username; it is the
    *recommended replacement* the advisory suggests.
  - `/Users/<name>/projects/...` worktree paths — no hidden-dir segment, so the
    dotfiles class does not match. (Scoped to dotfiles paths by user decision.)
  - Writes whose target IS a declared personal repo (gh `--repo <owner>/...`,
    cwd origin, or file_path origin owned by a personal owner).
  - Targets that cannot be resolved (no git repo, no origin remote, git error)
    — fail-open: the personal-repo class stays silent.

Exits 0 by default — advisory, not a block. Set
`PRAXIS_PERSONAL_LEAK_STRICT=1` to convert detection into a hard block (exit 2).
Fail-open on malformed stdin / unreadable body / unresolvable target.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    _is_gh_binary,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)


# ---------------------------------------------------------------------------
# Marker class 1 — absolute home path into a hidden config directory
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
# Marker class 2 — personal-repo reference (opt-in)
# ---------------------------------------------------------------------------

def _personal_owners() -> frozenset[str]:
    """Lowercased owner handles from PRAXIS_PERSONAL_REPO_OWNERS; empty = off."""
    raw = os.environ.get("PRAXIS_PERSONAL_REPO_OWNERS", "")
    return frozenset(o.strip().lower() for o in raw.split(",") if o.strip())


def _owner_ref_re(owners: frozenset[str]) -> re.Pattern[str]:
    """Compile `<owner>/<repo-slug>(#N)?` matcher for the declared owners.

    The lookbehind rejects a word-ish char ([A-Za-z0-9._-]) immediately before
    the owner so `xtestowner/foo` does not match. A `/` before the owner is
    allowed at the regex level so URL forms (`github.com/<owner>/<repo>#9`)
    match; _find_owner_markers then filters the slash-preceded candidates so
    filesystem path segments (`/Users/<owner>/projects/...`) do NOT count as
    repo references (Codex P2: with the owner handle equal to the OS username,
    ordinary worktree paths would otherwise trip the advisory/strict block).
    """
    alt = "|".join(re.escape(o) for o in sorted(owners))
    return re.compile(
        r"(?<![A-Za-z0-9._-])(?:%s)/[A-Za-z0-9._-]+(?:#[0-9]+)?" % alt,
        re.IGNORECASE,
    )


# A slash-preceded owner match is a repo reference only when the text right
# before that slash ends in a dotted hostname (github.com/<owner>/..., or any
# host with a TLD). Anything else (/Users/<owner>/..., ./<owner>/...) is a
# filesystem path segment, not a repo slug.
_HOST_BEFORE_SLASH_RE = re.compile(r"[A-Za-z0-9-]+\.[A-Za-z]{2,}$")


def _find_owner_markers(body: str, owner_re: re.Pattern[str]) -> list[str]:
    """Return de-duplicated personal-repo references found in body."""
    if not body:
        return []
    found: list[str] = []
    for m in owner_re.finditer(body):
        start = m.start()
        if start > 0 and body[start - 1] == "/" and not _HOST_BEFORE_SLASH_RE.search(
            body[: start - 1]
        ):
            continue  # path segment like /Users/<owner>/... — not a repo ref
        found.append(m.group(0))
    return list(dict.fromkeys(found))


# ---------------------------------------------------------------------------
# Write-target resolution (best effort, fail-open)
# ---------------------------------------------------------------------------

# One local git call's timeout. The file-write path can run two git calls
# (remote get-url + check-ignore) inside this hook's 5s manifest timeout, so
# at 3s each their sum (6s) would let the host kill the hook mid-scan
# (issue #1167); 2s each keeps the worst case (4s) inside the budget while
# staying generous for a local, non-network git query.
_GIT_TIMEOUT_SEC = 2


def _git_output(args: list[str], cwd: str) -> str | None:
    """Run a git command; return stripped stdout, or None on any failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _repo_owner(spec: str) -> str | None:
    """Extract the owner segment from a repo spec or remote URL.

    Handles `owner/repo`, `host/owner/repo`, `https://github.com/owner/repo(.git)`,
    `git@github.com:owner/repo.git` (incl. TLD-less internal hosts),
    `ssh://git@github.com/owner/repo.git`. Unparsable specs (file://, missing
    repo segment) return None → the caller fail-opens to silent.
    """
    spec = (spec or "").strip()
    if not spec:
        return None
    # scp-style / ssh URL: [ssh://]user@host[:/]owner/repo — the host may be a
    # TLD-less internal name (GHE), so match on the user@host shape, not a TLD.
    m = re.match(r"(?:ssh://)?[^/\s@]+@[^/:\s]+[:/]([^/\s:]+)/[^/\s]+", spec)
    if m:
        return m.group(1)
    m = re.search(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}[:/]([^/\s:]+)/[^/\s]+", spec)
    if m:
        return m.group(1)
    parts = spec.split("/")
    if len(parts) >= 2 and parts[0] and ":" not in parts[0]:
        return parts[0]
    return None


def _gh_repo_flag(argv: list[str]) -> str | None:
    """Return the value of `-R`/`--repo` in a gh argv, if present."""
    argv = strip_prefix(argv)
    for i, tok in enumerate(argv):
        if tok in {"-R", "--repo"} and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith("--repo="):
            return tok.partition("=")[2]
    return None


def _bash_target_owner(argv: list[str], cwd: str) -> str | None:
    """Resolve the gh write target's owner: --repo flag first, else cwd origin."""
    spec = _gh_repo_flag(argv)
    if spec:
        return _repo_owner(spec)
    origin = _git_output(["remote", "get-url", "origin"], cwd)
    if origin is None:
        return None
    return _repo_owner(origin)


def _nearest_existing_dir(path: str) -> str | None:
    """Walk up from path's parent to the nearest existing directory."""
    d = os.path.dirname(path) or "."
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return d or None


def _file_write_is_team_surface(file_path: str, owners: frozenset[str]) -> bool:
    """True iff file_path lands in a git worktree whose origin owner is NOT
    personal and the path is not gitignored. Any resolution failure → False
    (fail-open: the write is not treated as a team surface)."""
    base = _nearest_existing_dir(file_path)
    if base is None:
        return False
    origin = _git_output(["remote", "get-url", "origin"], base)
    if origin is None:
        return False  # not a git repo / no origin remote → fail-open
    owner = _repo_owner(origin)
    if owner is None or owner.lower() in owners:
        return False  # personal repo target (or unparsable owner) → pass
    try:
        proc = subprocess.run(
            ["git", "-C", base, "check-ignore", "-q", "--", file_path],
            capture_output=True,
            timeout=_GIT_TIMEOUT_SEC,
        )
        if proc.returncode == 0:
            return False  # gitignored scratch path (.omc/plans/ etc.) → pass
    except Exception:
        pass  # check-ignore failure → keep team-surface verdict (markers real)
    return True


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
# Scanners — return (dotfiles_markers, owner_markers) per surface
# ---------------------------------------------------------------------------

def _scan_bash(payload: dict, tool_input: dict, owners: frozenset[str]) -> tuple[list[str], list[str]]:
    command = tool_input.get("command", "") or ""
    if not command.strip():
        return [], []
    command = command.replace("\\\n", " ")
    tokens = safe_tokenize(command)
    if not tokens:
        return [], []

    cwd = payload.get("cwd", "") or os.getcwd()
    hmap = _build_heredoc_map(command)
    owner_re = _owner_ref_re(owners) if owners else None

    dot_markers: list[str] = []
    owner_markers: list[str] = []
    for argv in iter_command_starts(tokens):
        if not _is_gh_external_write(argv):
            continue
        bodies = _extract_gh_bodies(argv, cwd, hmap)
        if not bodies:
            continue
        for b in bodies:
            dot_markers.extend(_find_leak_markers(b))
        if owner_re is None:
            continue
        found: list[str] = []
        for b in bodies:
            found.extend(_find_owner_markers(b, owner_re))
        if not found:
            continue
        # Target discrimination — resolved lazily, only when a marker exists.
        # Personal-repo target (or unresolvable target) → class 2 stays silent.
        target = _bash_target_owner(argv, cwd)
        if target is not None and target.lower() not in owners:
            owner_markers.extend(found)
    return (
        list(dict.fromkeys(dot_markers)),
        list(dict.fromkeys(owner_markers)),
    )


def _scan_file_write(tool_input: dict, owners: frozenset[str]) -> tuple[list[str], list[str]]:
    """Scan a Write/Edit payload. Caller guarantees owners is non-empty —
    the whole file-write surface is opt-in via PRAXIS_PERSONAL_REPO_OWNERS."""
    file_path = tool_input.get("file_path", "") or ""
    content = tool_input.get("content") or tool_input.get("new_string") or ""
    if not file_path or not content:
        return [], []

    dot_markers = _find_leak_markers(content)
    owner_markers = _find_owner_markers(content, _owner_ref_re(owners))
    if not dot_markers and not owner_markers:
        return [], []
    # Target discrimination — resolved lazily, only when a marker exists.
    if not _file_write_is_team_surface(file_path, owners):
        return [], []
    return dot_markers, owner_markers


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

OWNER_ADVISORY_TEMPLATE = (
    "REMINDER (Personal-Asset Leak / External-Surface Write): personal-repo "
    "reference(s) detected in a team-surface write: {markers}\n"
    "A personal owner/repo reference (slug, issue number, URL, path) must not "
    "enter an org/team surface (rule: Personal repo content isolation) — "
    "teammates cannot access it, so it has no traceability value, only "
    "leakage cost.\n"
    "  • Inline the evidence (query, matrix, snapshot) so the artifact is "
    "self-contained, then drop the personal reference.\n"
    "  • Writes targeting a repo owned by PRAXIS_PERSONAL_REPO_OWNERS are "
    "exempt — this fired because the target is NOT a personal repo.\n"
    "Set PRAXIS_PERSONAL_LEAK_STRICT=1 to convert this advisory into a hard "
    "block (exit 2).\n"
)


def _render_advisory(dot_markers: list[str], owner_markers: list[str]) -> str:
    parts: list[str] = []
    if dot_markers:
        parts.append(ADVISORY_TEMPLATE.format(markers=", ".join(dot_markers[:3])))
    if owner_markers:
        parts.append(
            OWNER_ADVISORY_TEMPLATE.format(markers=", ".join(owner_markers[:3]))
        )
    return "".join(parts)


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    tool_name = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input", {}) or {}
    owners = _personal_owners()

    if tool_name == "Bash":
        dot_markers, owner_markers = _scan_bash(payload, tool_input, owners)
    elif tool_name in {"Write", "Edit"}:
        if not owners:
            return 0  # file-write surface is opt-in via PRAXIS_PERSONAL_REPO_OWNERS
        dot_markers, owner_markers = _scan_file_write(tool_input, owners)
    else:
        return 0

    if not dot_markers and not owner_markers:
        return 0

    sys.stderr.write(_render_advisory(dot_markers, owner_markers))
    if os.environ.get("PRAXIS_PERSONAL_LEAK_STRICT") == "1":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
