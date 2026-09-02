#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `gh (issue|pr) (create|edit)` invocations
that pass a `--label` / `-l` / `--add-label` value not present in the
target repository's label set.

Concrete recurring case (issue #385):
  AI pattern-matches a label naming convention from one repo (e.g. a
  prefixed `area:foo`) onto a different repo whose label namespace lacks
  it (e.g. only bare `foo` exists). `gh pr create --label area:foo` then
  fails with "label not found" after the command has already been issued
  — one wasted round-trip per occurrence, recurring across sessions.

Pipeline:
  1. Fast prefilter: command must contain `gh (issue|pr) (create|edit)`
     AND at least one label-flag token (`--label`, `-l`, `--add-label`).
  2. Structural tokenize (safe_tokenize → iter_command_starts →
     strip_prefix) so chained segments and env-prefixed invocations are
     covered uniformly.
  3. Extract label values: `--label x`, `--label=x`, `--label x,y`,
     repeated `--label x --label y` (gh accepts both forms).
  4. Resolve target repo:
       a. `--repo <r>` / `-R <r>` / `--repo=<r>` in argv → use as-is.
       b. Else parse cwd's `git remote get-url origin` → owner/repo.
       c. If both fail → fail-open (cannot validate without a key).
  5. Fetch label set via `gh label list --repo <r> --limit 300 --json
     name -q '.[].name'`, cached per-repo for PRAXIS_GH_LABEL_CACHE_TTL_SEC
     (default 300) at ~/.praxis/cache/gh-label-cache.json (PRAXIS_HOME-
     relocated via _paths.resolve_writable, TTL-swept with the rest of
     the cache root; PRAXIS_GH_LABEL_CACHE_PATH overrides). Cache corrupt
     / unwritable → in-memory only.
  6. Decide whether absence from that set is proof. The listing is row-
     limited, so when it comes back full the repo may hold labels we cannot
     see; each absent label is then re-checked via
     `gh api repos/<r>/labels/<name>` and only a 404 counts as absent
     (anything else → fail-open). A listing under the limit is complete, so
     absence is proof on its own and no re-check is needed — which also
     keeps the gate working offline off a warm cache. Listing and re-checks
     share one deadline drawn from the gate's manifest timeout; once it
     passes, remaining re-checks fail open rather than overrun the budget.
  7. If any label is absent → exit 2 with a stderr block listing the
     missing labels and a sample of valid labels for the repo.

Fail-open in every infrastructure-error path: gh missing, network fail,
auth fail, shlex parse error, transcript missing, cwd not a git repo
without `--repo`. Better silent pass than blocking the session.

Skip conditions:
  - `--help` / `-h` present (introspection, not execution)
  - No label flag in argv (nothing to validate)
  - `tool_name` != "Bash"
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    fail_open,
    shared_probe_deadline,
)
from _paths import resolve_writable  # type: ignore[import-not-found]  # noqa: E402
from _git import origin_slug  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    _is_gh_binary,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_CACHE_TTL_SEC = 300
_GH_LABEL_FETCH_TIMEOUT_SEC = 10
_GIT_REMOTE_TIMEOUT_SEC = 2
_GH_LABEL_LIMIT = 300

# hooks/manifest.json gives this gate a 15s timeout. The listing fetch and the
# per-label re-checks share that one budget, so they need a common deadline —
# left to their own 10s each they would overrun it and the host would kill the
# hook mid-session. The margin covers interpreter startup and process spawn.
# Under the Bash dispatch group the standalone budget is further clamped to
# `remaining_budget` (issue #1167): the group shares ONE node timeout across
# ~49 sequential members, so spending the full 13s here would starve every
# later member.
# Manifest-timeout parity is pinned by tests/hooks/preflight-gate/
# test_gh_label_verify.sh against hooks/manifest.json — bump both together.
_HOOK_TIMEOUT_SEC = 15
_HOOK_TIMEOUT_MARGIN_SEC = 2

_LABEL_FLAGS: frozenset[str] = frozenset({"--label", "-l", "--add-label"})
_REPO_FLAGS: frozenset[str] = frozenset({"--repo", "-R"})
_HELP_FLAGS: frozenset[str] = frozenset({"--help", "-h"})

_LABELED_SUBCMDS: frozenset[tuple[str, str]] = frozenset({
    ("issue", "create"),
    ("issue", "edit"),
    ("pr", "create"),
    ("pr", "edit"),
})

# Session id from the hook payload. Cache resolution threads it into the
# opportunistic prune_stale sweep so the sweep never deletes the calling
# session's own live session-keyed markers (whose mtime stops advancing while
# the session runs — the #920/#666 invariant every other resolver call site
# already honours).
_session_id: str | None = None

_GH_LABELED_CMD_RE = re.compile(r"\bgh\s+(?:issue|pr)\s+(?:create|edit)\b")
_LABEL_FLAG_RE = re.compile(r"(?:--label|--add-label|(?<!\S)-l)\b")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    # Self-budget: the standalone 13s (manifest timeout minus margin), clamped
    # to the remaining group budget when running inside the dispatcher — see
    # _hook_runtime.shared_probe_deadline.
    deadline = shared_probe_deadline(_HOOK_TIMEOUT_SEC, _HOOK_TIMEOUT_MARGIN_SEC)
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed

    global _session_id
    sid = payload.get("session_id")
    _session_id = sid if isinstance(sid, str) and sid else None
    if not _GH_LABELED_CMD_RE.search(command):
        return 0
    if not _LABEL_FLAG_RE.search(command):
        return 0

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    cwd = payload.get("cwd") or os.getcwd()

    for raw_argv in iter_command_starts(tokens):
        argv = strip_prefix(list(raw_argv))
        rc = _process_segment(argv, cwd, deadline)
        if rc != 0:
            return rc
    return 0




def _process_segment(argv: list[str], cwd: str, deadline: float) -> int:
    if len(argv) < 3 or not _is_gh_binary(argv[0]):
        return 0
    if (argv[1], argv[2]) not in _LABELED_SUBCMDS:
        return 0
    sub_args = argv[3:]
    if _HELP_FLAGS & set(sub_args):
        return 0

    labels = _extract_label_values(sub_args)
    if not labels:
        return 0

    repo = _resolve_repo(sub_args, cwd, deadline)
    if not repo:
        return 0

    got = _get_labels(repo, deadline)
    if got is None:
        return 0
    valid, truncated = got

    missing = [lbl for lbl in labels if lbl not in valid]
    if not missing:
        return 0

    # A truncated listing hides the repo's tail, so absence from it proves
    # nothing — confirm each candidate against the API and block only on a
    # definite 404. A complete listing is proof on its own, which also keeps
    # the gate working offline off a warm cache.
    if truncated:
        missing = [lbl for lbl in missing if _label_absent(repo, lbl, deadline)]
        if not missing:
            return 0

    _emit_block(missing, repo, sorted(valid)[:20])
    return 2


# ---------------------------------------------------------------------------
# argv extraction
# ---------------------------------------------------------------------------


def _extract_label_values(args: list[str]) -> list[str]:
    """Extract all label values from --label / -l / --add-label flags.

    Supports both forms:
      --label x
      --label=x
      --label x,y      (comma-separated multi-value)
      -l x --label y   (repeated flag aggregation)
    """
    out: list[str] = []
    i, n = 0, len(args)
    while i < n:
        tok = args[i]
        if tok.startswith("--") and "=" in tok:
            flag, _, val = tok.partition("=")
            if flag in _LABEL_FLAGS:
                out.extend(_split_csv(val))
            i += 1
            continue
        if tok in _LABEL_FLAGS:
            if i + 1 < n:
                out.extend(_split_csv(args[i + 1]))
            i += 2
            continue
        i += 1
    return out


def _split_csv(s: str) -> list[str]:
    return [v.strip() for v in s.split(",") if v.strip()]


def _resolve_repo(args: list[str], cwd: str, deadline: float) -> str | None:
    i, n = 0, len(args)
    while i < n:
        tok = args[i]
        if tok in _REPO_FLAGS and i + 1 < n:
            return args[i + 1]
        if tok.startswith("--repo="):
            return tok[len("--repo="):]
        i += 1
    return _resolve_repo_from_git(cwd, deadline)


def _resolve_repo_from_git(cwd: str, deadline: float) -> str | None:
    """owner/repo from the origin remote, or None.

    Delegates to the shared resolver (hooks/_lib/_git.py, issue #1178) and
    passes the deadline through: this probe runs BEFORE the label listing, so
    taking its own full slice would push the member past the group deadline
    before the budgeted call even starts (issue #1167).

    The shared `origin_slug` regex is host-anchored to github.com, unlike the
    looser pre-#1178 local copy that also matched gitlab/self-hosted origins.
    A non-GitHub origin now fails open (None -> no validation) instead of
    validating `gh` labels against a slug from the wrong host — a deliberate
    tightening.
    """
    return origin_slug(
        cwd=cwd, timeout=_GIT_REMOTE_TIMEOUT_SEC, deadline=deadline
    )


# ---------------------------------------------------------------------------
# Label fetch + cache
# ---------------------------------------------------------------------------


def _cache_path() -> Path:
    override = os.environ.get("PRAXIS_GH_LABEL_CACHE_PATH")
    if override:
        return Path(override)
    # Shared cache root (issue #1182): PRAXIS_HOME relocates the file and the
    # opportunistic prune_stale sweep covers it, like the other cache entries.
    # resolve_writable, not resolve_cache_file: the latter's legacy-TMPDIR
    # adoption would let a pre-seeded world-writable ${TMPDIR}/praxis-gh-label-
    # cache.json be promoted into the trusted label cache. This file never
    # lived in TMPDIR, and entries expire after ~5 minutes anyway, so there is
    # nothing worth adopting. The pre-#1182 <XDG_CACHE_HOME|~/.cache>/
    # claude-praxis/gh-label-cache.json is likewise not migrated: a stranded
    # file only costs one refetch and rots harmlessly.
    return Path(resolve_writable("cache", "gh-label-cache.json", session_id=_session_id))


def _cache_ttl_sec() -> int:
    raw = os.environ.get("PRAXIS_GH_LABEL_CACHE_TTL_SEC")
    if raw is None:
        return _DEFAULT_CACHE_TTL_SEC
    try:
        v = int(raw)
        return v if v >= 0 else _DEFAULT_CACHE_TTL_SEC
    except ValueError:
        return _DEFAULT_CACHE_TTL_SEC


def _get_labels(repo: str, deadline: float) -> tuple[frozenset[str], bool] | None:
    """Return (label names defined in `repo`, truncated), with caching.

    Returns None when the label set cannot be determined (gh missing,
    network failure, auth failure) — caller treats this as fail-open.

    A cache entry written before `truncated` was tracked is read as truncated,
    so a stale entry can never justify a block on its own.
    """
    now = time.time()
    cache_path = _cache_path()
    cache = _load_cache(cache_path)
    entry = cache.get(repo)
    if isinstance(entry, dict):
        fetched_at = entry.get("fetched_at", 0)
        labels = entry.get("labels")
        # A future fetched_at is rejected, not treated as fresh: a tampered or
        # clock-skewed timestamp far in the future would otherwise make the
        # entry immortal (`now - fetched_at` stays negative forever).
        if (
            isinstance(fetched_at, (int, float))
            and isinstance(labels, list)
            and 0 <= (now - fetched_at) < _cache_ttl_sec()
        ):
            return frozenset(labels), bool(entry.get("truncated", True))

    fetched = _fetch_labels(repo, deadline)
    if fetched is None:
        return None
    names, truncated = fetched
    cache[repo] = {
        "labels": sorted(names),
        "fetched_at": now,
        "truncated": truncated,
    }
    _save_cache(cache_path, cache)
    return frozenset(names), truncated


def _load_cache(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(path: Path, cache: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass


def _fetch_labels(repo: str, deadline: float) -> tuple[set[str], bool] | None:
    """Return (labels, truncated) for `repo`, or None on any fetch failure.

    `truncated` is True when the listing filled the row limit — the repo may
    hold more labels than we can see, so absence from `labels` proves nothing.

    The fetch shares the gate's deadline with the per-label re-checks: its
    subprocess timeout is the remaining budget capped at the standalone 10s,
    and with no budget left it fails open without spawning gh at all.
    """
    remaining = deadline - time.monotonic()
    if remaining < MIN_SUBPROC_BUDGET_SEC:
        return None
    try:
        r = subprocess.run(
            [
                "gh", "label", "list",
                "--repo", repo,
                "--limit", str(_GH_LABEL_LIMIT),
                "--json", "name",
                "-q", ".[].name",
            ],
            capture_output=True,
            text=True,
            timeout=min(remaining, _GH_LABEL_FETCH_TIMEOUT_SEC),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    labels = {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    return labels, len(labels) >= _GH_LABEL_LIMIT


def _split_host(repo: str) -> tuple[str | None, str]:
    """Split gh's `[HOST/]OWNER/REPO` into (host, `OWNER/REPO`).

    `gh label list --repo` resolves the optional host itself; `gh api` does not,
    so the host has to move to `--hostname` or the request goes to github.com
    with the host stuck in the path.
    """
    parts = repo.split("/")
    if len(parts) == 3:
        return parts[0], f"{parts[1]}/{parts[2]}"
    return None, repo


def _label_absent(repo: str, label: str, deadline: float) -> bool:
    """True only when the API definitively reports `label` missing (404).

    Every other outcome — success, network/auth failure, an unexpected status,
    no time left in the hook's budget — returns False so the gate fails open
    rather than blocking on an absence it could not prove.
    """
    remaining = deadline - time.monotonic()
    if remaining < MIN_SUBPROC_BUDGET_SEC:
        return False

    host, owner_repo = _split_host(repo)
    cmd = ["gh", "api"]
    if host:
        cmd += ["--hostname", host]
    cmd.append(f"repos/{owner_repo}/labels/{quote(label, safe='')}")
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=min(remaining, _GH_LABEL_FETCH_TIMEOUT_SEC),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode == 0:
        return False
    # Match the status marker, not a bare "404": gh echoes the request URL on
    # connection errors, so a label like `hub-issue-404` would otherwise read
    # its own name back as proof of its absence.
    return "(HTTP 404)" in r.stderr


# ---------------------------------------------------------------------------
# Block output
# ---------------------------------------------------------------------------


def _emit_block(missing: list[str], repo: str, sample: list[str]) -> None:
    lines = [
        f"BLOCKED: gh label(s) not found in {repo}: {', '.join(missing)}",
        "",
        "Rule: probe target-repo label set before applying a label convention.",
        "Label namespaces differ across repos — a name that exists in one repo",
        "may not exist in another. Verify before passing --label.",
        "",
        f"Verify the repo's label set: gh label list --repo {repo}",
        "",
    ]
    if sample:
        lines.append(f"Labels in {repo} (showing {len(sample)}):")
        lines.extend(f"  - {lbl}" for lbl in sample)
        lines.append("")
    lines.extend([
        "Resolve by one of:",
        "  1. Use an existing label name from the list above.",
        f"  2. Create the missing label first: gh label create '<name>' --repo {repo}",
        "  3. Drop the --label flag if it is optional for this command.",
    ])
    sys.stderr.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
