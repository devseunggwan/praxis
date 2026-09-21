#!/usr/bin/env python3
"""PreToolUse(Bash) gate: block org-internal identifiers headed for a PUBLIC repo.

Rule (CLAUDE.md `Cross-boundary repo writes` — "include no internal identifiers";
"Visibility decides the gate, not org membership"): a public repository must not
receive an organization's internal names. Issue #376 swept them out once and
nothing stopped them coming back; issue #1470 found them re-introduced through
new hooks and fixtures, the way an author's real environment follows them into
every example they write.

The token list comes from `PRAXIS_INTERNAL_TOKENS` (comma-separated) and from
nowhere else. Shipping the list in this public repo would publish the very
names it protects, so unset means the hook is inert.

Surfaces scanned (one live command start at a time):
  git commit ...                 -> commit message (-m / -F) and the ADDED lines
                                    of the staged diff (`-a` adds tracked edits)
  gh <noun> create|edit|comment  -> --title and the body (--body / --body-file)
  gh api <comment endpoint> ...  -> the `body` field, or `--input` JSON `body`

Only a hit whose target repo GitHub reports as `public` blocks. The target is
`--repo` / the `repos/<owner>/<repo>` endpoint for gh, and the `origin` remote
of the directory the command runs in otherwise. The visibility answer is
cached per repo (24h). Private and internal repos are silent — an org's own
name belongs there.

Fail-open (ETHOS: infrastructure failure degrades to "no hook"):
  - visibility cannot be resolved (gh missing, offline, 404, no budget) —
    a hit then prints an advisory and exits 0 instead of blocking
  - the working directory is unknowable (`cd` this hook cannot model) or has
    no GitHub `origin` — nothing to decide against, silent
  - an unreadable body file or undecodable JSON contributes no text

`PRAXIS_INTERNAL_TOKEN_STRICT=0` downgrades a block to an advisory.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _external_write_body import (  # type: ignore[import-not-found]  # noqa: E402
    extract_gh_body,
    is_gh_external_write,
    parse_gh_api,
    read_body_file,
    split_gh_flag,
)
from _git import origin_slug, run_git  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    fail_open,
    remaining_budget,
)
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    compound_cascade_hint,
    iter_command_starts,
    iter_command_texts,
    safe_tokenize,
    strip_prefix,
)
from _paths import resolve_cache_file  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402
from git_commit_titles import (  # type: ignore[import-not-found]  # noqa: E402
    extract_git_message_texts,
    strip_git_global_flags,
)

HOOK_NAME = "internal-token-leak-gate"
TOKENS_ENV = "PRAXIS_INTERNAL_TOKENS"
STRICT_ENV = "PRAXIS_INTERNAL_TOKEN_STRICT"
CACHE_FILE = "internal-token-visibility.json"
CACHE_PATH_ENV = "PRAXIS_INTERNAL_TOKEN_CACHE_PATH"
CACHE_TTL_SEC = 24 * 3600
_GH_TIMEOUT_SEC = 3.0
_MAX_SHOWN = 5

_CHDIR_WORDS = frozenset({"cd", "pushd", "popd"})
_GH_REPO_FLAGS = frozenset({"--repo", "-R"})
_GH_TITLE_FLAGS = frozenset({"--title", "-t"})
_API_REPO_RE = re.compile(r"^/?repos/([A-Za-z0-9_.{}-]+)/([A-Za-z0-9_.{}-]+)(?:/|$)")
_REPO_SLUG_RE = re.compile(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Visibility outcomes. Only PUBLIC blocks; UNRESOLVED advises.
PUBLIC = "public"
UNRESOLVED = "UNRESOLVED"
_KNOWN_VISIBILITY = frozenset({"public", "private", "internal"})


# ---------------------------------------------------------------------------
# Token matching
# ---------------------------------------------------------------------------

def load_tokens() -> list[str]:
    raw = os.environ.get(TOKENS_ENV, "")
    return sorted({t.strip() for t in raw.split(",") if t.strip()}, key=len, reverse=True)


def token_pattern(tokens: list[str]) -> re.Pattern[str]:
    # Left boundary only: an org name is usually a prefix (`<org>-wiki`,
    # `<org>ctl`), so a right boundary would miss the forms that actually leak.
    alternation = "|".join(re.escape(t) for t in tokens)
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alternation})", re.IGNORECASE)


def text_hits(label: str, text: str, pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    """(location, line) for every line of `text` carrying a token."""
    return [
        (f"{label}:{n}", line.strip())
        for n, line in enumerate(text.splitlines(), 1)
        if pattern.search(line)
    ]


def diff_hits(diff: str, pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    """(path:line, line) for every ADDED line of a `-U0` diff carrying a token.

    Removed and context lines are skipped on purpose: a PR that deletes a
    leaked name must not be blocked for containing it.
    """
    hits: list[tuple[str, str]] = []
    path = "?"
    lineno = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            path = target[2:] if target.startswith("b/") else target
            continue
        m = _HUNK_RE.match(line)
        if m:
            lineno = int(m.group(1))
            continue
        if line.startswith("+"):
            if pattern.search(line[1:]):
                hits.append((f"{path}:{lineno}", line[1:].strip()))
            lineno += 1
    return hits


# ---------------------------------------------------------------------------
# Working directory
# ---------------------------------------------------------------------------

def cd_intent(argv: list[str]) -> tuple[str, str | None]:
    """("none", None) | ("literal", path) | ("opaque", None) for one argv.

    Mirrors `cross-boundary-preflight`: exactly `cd <literal>` is modelled, and
    any other directory change makes the working directory unknown.
    """
    if not argv or argv[0].lstrip("({") not in _CHDIR_WORDS:
        return ("none", None)
    if len(argv) == 2 and argv[0] == "cd":
        path = argv[1]
        if path and path != "-" and not path.startswith("~") and not any(
            ch in path for ch in "$`*?(){}"
        ):
            return ("literal", path)
    return ("opaque", None)


def join_dir(base: str, sub: str | None) -> str:
    if not sub:
        return base
    return os.path.normpath(os.path.join(base, os.path.expanduser(sub)))


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------

def _is_git(token: str) -> bool:
    return token == "git" or token.endswith("/git")


def _commit_all(args: list[str]) -> bool:
    for tok in args[1:]:
        if tok == "--":
            break
        if tok == "--all":
            return True
        if tok.startswith("-") and not tok.startswith("--") and "a" in tok[1:]:
            # `-a`, `-am` — stop at a value-taking short flag so `-m` text is not read.
            for ch in tok[1:]:
                if ch == "a":
                    return True
                if ch in "mFCcSt":
                    break
    return False


def git_commit_surface(
    argv: list[str], command: str, cwd: str, pattern: re.Pattern[str]
) -> tuple[str, list[tuple[str, str]]] | None:
    """(repo_dir, hits) for a live `git commit`, else None."""
    if not argv or not _is_git(argv[0]):
        return None
    rest, c_dir = strip_git_global_flags(argv)
    if not rest or rest[0] != "commit":
        return None
    if any(t in ("--dry-run", "--help", "-h") for t in rest[1:]):
        return None
    repo_dir = join_dir(cwd, c_dir)

    hits: list[tuple[str, str]] = []
    for text in extract_git_message_texts(argv, command):
        hits += text_hits("commit message", text, pattern)
    diff_args = ["diff", "--no-color", "--no-ext-diff", "-U0"]
    diff = run_git(
        diff_args + (["HEAD"] if _commit_all(rest) else ["--cached"]),
        cwd=repo_dir,
    )
    if diff:
        hits += diff_hits(diff, pattern)
    return repo_dir, hits


def _flag_values(argv: list[str], names: frozenset[str]) -> list[str]:
    values: list[str] = []
    i = 0
    while i < len(argv):
        tok, inline = split_gh_flag(argv[i])
        if tok in names:
            if inline is not None:
                values.append(inline)
            elif i + 1 < len(argv):
                values.append(argv[i + 1])
                i += 1
        i += 1
    return values


def _api_input_body(argv: list[str]) -> str | None:
    """`body` of the JSON file handed to `gh api --input`, else its raw text.

    The shared extractor treats `--input` as an unknown body, but it is the
    form a verification-anchor revision takes (`PATCH …/comments/<id>
    --input body.json`), so leaving it unread exempts the most common edit.
    """
    for path in _flag_values(argv, frozenset({"--input"})):
        if path == "-":
            return None
        raw = read_body_file(path)
        try:
            data = json.loads(raw)
        except ValueError:
            return raw
        if isinstance(data, dict) and isinstance(data.get("body"), str):
            return data["body"]
        return raw
    return None


def _slug_from_repo_flag(value: str) -> str | None:
    m = _REPO_SLUG_RE.search(value.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def gh_write_surface(
    argv: list[str], pattern: re.Pattern[str]
) -> tuple[str | None, bool, list[tuple[str, str]]] | None:
    """(explicit_repo, from_checkout, hits) for a gh external write, else None.

    `from_checkout` is True when the target is whatever repo the checkout
    resolves to — no `--repo`, or gh's `{owner}/{repo}` placeholder pair.
    """
    argv = strip_prefix(argv)
    if not is_gh_external_write(argv):
        return None
    hits: list[tuple[str, str]] = []
    api = parse_gh_api(argv)
    if api is not None:
        body = _api_input_body(argv) if api.has_input else extract_gh_body(argv)
        if body:
            hits += text_hits("body", body, pattern)
        m = _API_REPO_RE.match(api.path or "")
        if m and (m.group(1), m.group(2)) == ("{owner}", "{repo}"):
            return None, True, hits
        if m and "{" not in m.group(0):
            return f"{m.group(1)}/{m.group(2)}", False, hits
        return None, False, hits

    for title in _flag_values(argv, _GH_TITLE_FLAGS):
        hits += text_hits("title", title, pattern)
    body = extract_gh_body(argv)
    if body:
        hits += text_hits("body", body, pattern)
    repos = _flag_values(argv, _GH_REPO_FLAGS)
    if repos:
        return _slug_from_repo_flag(repos[-1]), False, hits
    return None, True, hits


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------

def _read_cache(path: str) -> dict[str, object]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(path: str, data: dict[str, object]) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _gh_visibility(repo: str) -> str | None:
    budget = remaining_budget(_GH_TIMEOUT_SEC)
    if budget < MIN_SUBPROC_BUDGET_SEC:
        return None
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}", "--jq", ".visibility"],
            capture_output=True, text=True, timeout=min(_GH_TIMEOUT_SEC, budget),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip() if out.returncode == 0 else ""
    return value if value in _KNOWN_VISIBILITY else None


def repo_visibility(repo: str, session_id: str | None) -> str:
    """`public` / `private` / `internal`, or UNRESOLVED. Cached per repo."""
    key = repo.lower()
    path = os.environ.get(CACHE_PATH_ENV) or resolve_cache_file(
        CACHE_FILE, session_id=session_id
    )
    cache = _read_cache(path)
    entry = cache.get(key)
    now = time.time()
    if (
        isinstance(entry, dict)
        and entry.get("vis") in _KNOWN_VISIBILITY
        and isinstance(entry.get("ts"), (int, float))
        and now - entry["ts"] < CACHE_TTL_SEC
    ):
        return entry["vis"]
    vis = _gh_visibility(repo)
    if vis is None:
        return UNRESOLVED  # never cached: the next call gets a fresh try
    cache[key] = {"vis": vis, "ts": now}
    _write_cache(path, cache)
    return vis


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _listing(hits: list[tuple[str, str]]) -> str:
    shown = "\n".join(f"  {loc}: {line[:160]}" for loc, line in hits[:_MAX_SHOWN])
    more = "" if len(hits) <= _MAX_SHOWN else f"\n  … and {len(hits) - _MAX_SHOWN} more"
    return shown + more


def block_text(repo: str, hits: list[tuple[str, str]], command: str) -> str:
    base = format_block(
        rule_name=HOOK_NAME,
        why=(
            f"{repo} is a public repository, and this write carries a token listed "
            f"in {TOKENS_ENV} — an organization-internal name. Once pushed or "
            "posted it cannot be taken back without rewriting history (issue #1470).\n"
            f"{_listing(hits)}"
        ),
        correct_path=(
            "replace each name with a neutral one (`example-*`, `orgctl`) or a "
            "placeholder (`<org>`), then retry"
        ),
        bypass_env=STRICT_ENV,
        bypass_reason_hint=(
            "is the default; set it to 0 for advisory mode (exit 0, stderr "
            "warning only)"
        ),
        reference=f"hooks/preflight-gate/{HOOK_NAME}/spec.md",
    )
    return base + compound_cascade_hint(command)


def advisory_text(repo: str, hits: list[tuple[str, str]], reason: str) -> str:
    return (
        f"[{HOOK_NAME}] ADVISORY ({reason}): this write to {repo} carries a token "
        f"listed in {TOKENS_ENV}. Check the target is not public before it lands.\n"
        f"{_listing(hits)}\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _decide(
    repo: str | None, hits: list[tuple[str, str]], command: str, session_id: str | None
) -> int:
    if not hits or not repo:
        return 0
    vis = repo_visibility(repo, session_id)
    if vis == UNRESOLVED:
        sys.stderr.write(advisory_text(repo, hits, "visibility unresolved"))
        return 0
    if vis != PUBLIC:
        return 0
    if os.environ.get(STRICT_ENV, "1") == "0":
        sys.stderr.write(advisory_text(repo, hits, "STRICT=0"))
        return 0
    sys.stderr.write(block_text(repo, hits, command) + "\n")
    return 2


@fail_open
def main() -> int:
    tokens = load_tokens()
    if not tokens:
        return 0
    parsed = read_bash_payload()
    if parsed is None:
        return 0
    payload, command = parsed
    if not command.strip():
        return 0
    pattern = token_pattern(tokens)
    session_id = payload.get("session_id")
    cwd: str | None = payload.get("cwd") or os.getcwd()

    for segment in iter_command_texts(command):
        for raw_argv in iter_command_starts(safe_tokenize(segment)):
            argv = strip_prefix(raw_argv)
            kind, target = cd_intent(argv)
            if kind == "literal" and cwd is not None:
                candidate = join_dir(cwd, target)
                if os.path.isdir(candidate):
                    cwd = candidate
                continue
            if kind == "opaque":
                cwd = None
                continue

            commit = git_commit_surface(argv, command, cwd, pattern) if cwd else None
            if commit is not None:
                repo_dir, hits = commit
                rc = _decide(origin_slug(cwd=repo_dir) if hits else None, hits, command, session_id)
                if rc:
                    return rc
                continue

            gh = gh_write_surface(argv, pattern)
            if gh is not None:
                repo, from_checkout, hits = gh
                if hits and from_checkout:
                    repo = origin_slug(cwd=cwd) if cwd else None
                rc = _decide(repo, hits, command, session_id)
                if rc:
                    return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
