#!/usr/bin/env python3
"""PreToolUse(Bash) gate: force a duplicate-PR search before `gh pr create`.

Issue #234. An agent completed a full implement-and-PR cycle that turned out
to be byte-identical to a merged PR from another author 3 hours earlier; the
agent's pre-creation dedup check existed only as a prompt-layer reasoning
instruction. A contributing factor was searching the wrong repo — the tracking
issue's repo, not the PR target repo.

This hook converts the reasoning obligation into an execution-time tool call
whose output is unconditionally surfaced. The dedup search runs against the
*target* repo (resolved from `--repo`/`-R`, falling back to `git remote get-url
origin` only as a last resort).

Behavior:
  1. Match `gh pr create` / `gh pr new`. Skip --help, non-pr-create, non-Bash.
  2. Resolve target repo from --repo/-R/--repo= flag (incl. gh global flag form).
     Fall back to `git remote get-url origin`. If neither resolves → block.
  3. Extract title keywords from --title/-t/--title= (Conventional Commits
     prefix stripped, stop-words dropped, up to 6 tokens).
  4. Run `gh pr list --repo <r> --state all --search "<kw>" --limit 20
     --json number,title,state,author,url,mergedAt`. Surface JSON output to
     stderr unconditionally — the artifact must be visible whether matches
     are found or not.
  5. On `gh` failure (auth, repo not found, timeout) → block (exit 2) naming
     the unresolved repo.
  6. Match-found → advisory only (per issue spec leaning advisory; topic-
     keyword matching has false positives). The agent decides.

Fail-open contract:
  - Malformed stdin JSON → exit 0
  - `gh` binary missing → exit 0 (cannot enforce dedup search if gh is absent)
  - python3 missing → handled by .sh shim
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _git import origin_slug  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    fail_open,
    remaining_budget,
)
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]
    _is_gh_binary,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GH_GLOBAL_FLAGS_WITH_ARG = frozenset({"-R", "--repo", "--hostname", "--color"})

# Conventional Commits prefix: `type` or `type(scope)` followed by `:`.
CONVENTIONAL_PREFIX_RE = re.compile(r"^[a-z]+(?:\([^)]+\))?:\s*", re.IGNORECASE)

# Stop-words dropped from title before search — generic verbs and articles
# that produce noise in `gh pr list --search`.
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with",
    "add", "adds", "added", "adding",
    "fix", "fixes", "fixed", "fixing",
    "update", "updates", "updated", "updating",
    "remove", "removes", "removed", "removing",
    "make", "makes", "made", "making",
    "use", "uses", "used", "using",
    "set", "sets", "setting",
    "new",
    "wip",
})

MAX_KEYWORDS = 6
GH_TIMEOUT_SEC = 4
GIT_TIMEOUT_SEC = 2

# ---------------------------------------------------------------------------
# Argv inspection
# ---------------------------------------------------------------------------

def _is_pr_create(argv: list[str]) -> bool:
    """True if argv is `gh [global flags] pr create/new` (not --help)."""
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
        return False
    if any(t in ("--help", "-h") or t.startswith(("--help=", "-h="))
           for t in argv):
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
    return (
        i + 1 < len(argv)
        and argv[i] == "pr"
        and argv[i + 1] in ("create", "new")
    )


def _flag_value(argv: list[str], names: tuple[str, ...]) -> str | None:
    """Return the value of the first matching flag, or None.

    Handles `--flag value`, `-x value`, `--flag=value`, `-Rvalue` (for short
    flags only).
    """
    for i, t in enumerate(argv):
        if t in names and i + 1 < len(argv):
            return argv[i + 1]
        for name in names:
            if t.startswith(name + "="):
                return t.split("=", 1)[1]
        # `-Rvalue` short-flag concatenation
        for name in names:
            if len(name) == 2 and name.startswith("-") and t.startswith(name) and len(t) > 2 and not t.startswith("--"):
                return t[2:]
    return None


def _extract_repo(argv: list[str]) -> str | None:
    return _flag_value(argv, ("--repo", "-R"))


def _extract_title(argv: list[str]) -> str | None:
    return _flag_value(argv, ("--title", "-t"))


# ---------------------------------------------------------------------------
# Repo resolution
# ---------------------------------------------------------------------------

class BudgetExhausted(Exception):
    """The dispatcher budget ran out before a lookup could start.

    Not a lookup failure: the command itself is fine, so the gate must fail
    open. The helpers' ordinary failure values (`None`, `-1`) both reach block
    paths, which is how a slow dispatcher turns into a rejected `gh pr create`.
    """


def _resolve_origin_repo() -> str | None:
    """Parse owner/repo from `git remote get-url origin` in the process cwd.

    Returns None on any failure (no git, no origin, unparseable URL), which
    the caller blocks on. Raises BudgetExhausted when there is no runway to
    look, which the caller passes on instead.

    Delegates the subprocess to the shared resolver (hooks/_lib/_git.py,
    issue #1178), keeping this hook's 2s git timeout. The floor check stays
    here because the shared runner answers a dry budget with None, and this
    caller has to tell "no runway" apart from "no origin" — the two take
    different paths in main() (issue #1167).
    """
    if remaining_budget(GIT_TIMEOUT_SEC) < MIN_SUBPROC_BUDGET_SEC:
        raise BudgetExhausted("git remote lookup")
    return origin_slug(timeout=GIT_TIMEOUT_SEC)


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

def _extract_keywords(title: str) -> list[str]:
    """Return up to MAX_KEYWORDS lowercase tokens for the dedup search."""
    stripped = CONVENTIONAL_PREFIX_RE.sub("", title).strip()
    if not stripped:
        return []
    # Split on non-alphanumeric so `feat:` and parenthetical scopes don't bleed
    # into the keyword set after partial regex misses.
    raw = re.findall(r"[A-Za-z0-9]+", stripped)
    out: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        lower = tok.lower()
        if lower in STOPWORDS:
            continue
        if lower in seen:
            continue
        seen.add(lower)
        out.append(lower)
        if len(out) >= MAX_KEYWORDS:
            break
    return out


# ---------------------------------------------------------------------------
# gh dedup-search call
# ---------------------------------------------------------------------------

def _run_gh_search(repo: str, keywords: list[str]) -> tuple[int, str, str]:
    """Run `gh pr list --repo <r> --state all --search <kw> --json ...`.

    Returns (returncode, stdout, stderr). On infrastructure failure
    (binary missing, timeout) returns (-1, "", "<reason>") so callers can
    distinguish from real gh errors. Raises BudgetExhausted when there is no
    runway to search — that is not a failure of the search.
    """
    query = " ".join(keywords)
    cmd = [
        "gh", "pr", "list",
        "--repo", repo,
        "--state", "all",
        "--search", query,
        "--limit", "20",
        "--json", "number,title,state,author,url,mergedAt",
    ]
    budget = remaining_budget(GH_TIMEOUT_SEC)
    if budget < MIN_SUBPROC_BUDGET_SEC:
        raise BudgetExhausted("gh dedup search")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=min(GH_TIMEOUT_SEC, budget),
            check=False,
        )
    except FileNotFoundError:
        return -1, "", "gh binary not found"
    except subprocess.TimeoutExpired:
        return -1, "", f"gh pr list timed out after {GH_TIMEOUT_SEC}s"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _format_artifact(
    repo: str,
    keywords: list[str],
    rows: list[dict],
) -> str:
    """Render the unconditional artifact emitted to stderr.

    Always surfaces the search header (repo + query); rows table only if
    rows present; otherwise an explicit "no matches" line. The header
    presence under both branches is what makes the artifact "unconditional"
    per the issue spec.
    """
    header = (
        f"[pre-gh-pr-create-dedup-gate] Duplicate-PR search\n"
        f"  repo : {repo}\n"
        f"  query: {' '.join(keywords) if keywords else '(empty)'}\n"
    )
    if not rows:
        return header + "  result: no matches\n"

    lines = [header, f"  matches: {len(rows)} (review before creating PR)"]
    for r in rows:
        num = r.get("number", "?")
        state = (r.get("state") or "").lower()
        merged_at = r.get("mergedAt")
        if state == "merged" or merged_at:
            tag = "MERGED"
        elif state == "open":
            tag = "OPEN"
        elif state == "closed":
            tag = "CLOSED"
        else:
            tag = state.upper() or "?"
        author_field = r.get("author") or {}
        author = (
            author_field.get("login")
            if isinstance(author_field, dict)
            else None
        ) or "?"
        title = r.get("title") or ""
        url = r.get("url") or ""
        lines.append(f"  #{num:<5} [{tag:<6}] @{author}  {title}")
        if url:
            lines.append(f"         {url}")
    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

BLOCK_REPO_MSG = (
    "BLOCKED: [pre-gh-pr-create-dedup-gate] cannot resolve PR target repo.\n"
    "  Tried: --repo/-R flag (absent), `git remote get-url origin` (failed).\n"
    "  Pass --repo owner/name explicitly so the dedup search hits the\n"
    "  correct repo. A worktree's origin can differ from the PR target,\n"
    "  which is exactly how the bypass in praxis #234 happened.\n"
)


def _block_gh_error(repo: str, rc: int, stderr_text: str) -> str:
    return (
        "BLOCKED: [pre-gh-pr-create-dedup-gate] dedup search failed.\n"
        f"  repo: {repo}\n"
        f"  gh exit: {rc}\n"
        f"  gh stderr: {stderr_text.strip() or '(empty)'}\n"
        "  This is a hard block because a silently-failing dedup check is\n"
        "  the failure mode this hook exists to prevent. Verify auth /\n"
        "  repo name and retry.\n"
    )


def _budget_skip(exc: BudgetExhausted) -> int:
    """Report the skip and pass. Silence here would be indistinguishable from
    a dedup search that ran and found nothing."""
    sys.stderr.write(
        f"[pre-gh-pr-create-dedup-gate] group budget exhausted before {exc}; "
        "skipping the duplicate check (fail-open — not a block).\n"
        f"  {exc} 전에 그룹 예산이 소진되어 "
        "중복 검사를 건너뜁니다 (fail-open — 차단하지 않음).\n"
    )
    return 0


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

    for argv_raw in iter_command_starts(tokens):
        argv = list(argv_raw)
        if not _is_pr_create(argv):
            continue
        argv = strip_prefix(argv)

        # Fail-open when gh isn't installed — the hook cannot enforce a dedup
        # search without it, and blocking would penalize environments where
        # `gh pr create` itself would fail with a clearer error.
        if shutil.which("gh") is None:
            return 0

        try:
            repo = _extract_repo(argv) or _resolve_origin_repo()
        except BudgetExhausted as exc:
            return _budget_skip(exc)
        if not repo:
            sys.stderr.write(BLOCK_REPO_MSG)
            return 2

        title = _extract_title(argv) or ""
        keywords = _extract_keywords(title)
        if not keywords:
            sys.stderr.write(
                "[pre-gh-pr-create-dedup-gate] no usable --title keywords\n"
                f"  repo : {repo}\n"
                "  Dedup search skipped (no title or only stop-words).\n"
                "  Run `gh pr list --repo "
                f"{repo} --state all --search '<topic>'` manually before\n"
                "  creating the PR.\n"
            )
            return 0

        try:
            rc, stdout, stderr_text = _run_gh_search(repo, keywords)
        except BudgetExhausted as exc:
            return _budget_skip(exc)
        if rc != 0:
            sys.stderr.write(_block_gh_error(repo, rc, stderr_text))
            return 2

        try:
            rows = json.loads(stdout) if stdout.strip() else []
        except json.JSONDecodeError:
            sys.stderr.write(_block_gh_error(repo, rc, "unparseable gh JSON output"))
            return 2
        if not isinstance(rows, list):
            # gh returning a JSON object (error envelope, schema change, etc.)
            # would crash _format_artifact's row iteration. Treat as a hard
            # block — silent fail-through is exactly the failure mode this
            # hook exists to prevent.
            sys.stderr.write(_block_gh_error(
                repo, rc,
                f"gh returned JSON {type(rows).__name__} (expected list); "
                f"first 200 chars: {stdout[:200]!r}",
            ))
            return 2

        sys.stderr.write(_format_artifact(repo, keywords, rows))
        # Match-found is advisory only — agent must read the artifact and
        # decide. Hard-blocking would remove agency for legitimate same-topic
        # follow-up PRs (issue #234 open question).
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
