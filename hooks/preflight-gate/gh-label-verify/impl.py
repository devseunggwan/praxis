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
     (default 300) at <XDG_CACHE_HOME or ~/.cache>/claude-praxis/
     gh-label-cache.json. Cache corrupt / unwritable → in-memory only.
  6. Decide whether absence from that set is proof. The listing is row-
     limited, so when it comes back full the repo may hold labels we cannot
     see; each absent label is then re-checked via
     `gh api repos/<r>/labels/<name>` and only a 404 counts as absent
     (anything else → fail-open). A listing under the limit is complete, so
     absence is proof on its own and no re-check is needed — which also
     keeps the gate working offline off a warm cache.
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
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
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

_LABEL_FLAGS: frozenset[str] = frozenset({"--label", "-l", "--add-label"})
_REPO_FLAGS: frozenset[str] = frozenset({"--repo", "-R"})
_HELP_FLAGS: frozenset[str] = frozenset({"--help", "-h"})

_LABELED_SUBCMDS: frozenset[tuple[str, str]] = frozenset({
    ("issue", "create"),
    ("issue", "edit"),
    ("pr", "create"),
    ("pr", "edit"),
})

_GH_LABELED_CMD_RE = re.compile(r"\bgh\s+(?:issue|pr)\s+(?:create|edit)\b")
_LABEL_FLAG_RE = re.compile(r"(?:--label|--add-label|(?<!\S)-l)\b")
_REPO_URL_RE = re.compile(r"[:/]([^/:\s]+)/([^/\s]+?)(?:\.git)?/?$")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
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
        rc = _process_segment(argv, cwd)
        if rc != 0:
            return rc
    return 0




def _process_segment(argv: list[str], cwd: str) -> int:
    if len(argv) < 3 or argv[0] != "gh":
        return 0
    if (argv[1], argv[2]) not in _LABELED_SUBCMDS:
        return 0
    sub_args = argv[3:]
    if _HELP_FLAGS & set(sub_args):
        return 0

    labels = _extract_label_values(sub_args)
    if not labels:
        return 0

    repo = _resolve_repo(sub_args, cwd)
    if not repo:
        return 0

    got = _get_labels(repo)
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
        missing = [lbl for lbl in missing if _label_absent(repo, lbl)]
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


def _resolve_repo(args: list[str], cwd: str) -> str | None:
    i, n = 0, len(args)
    while i < n:
        tok = args[i]
        if tok in _REPO_FLAGS and i + 1 < n:
            return args[i + 1]
        if tok.startswith("--repo="):
            return tok[len("--repo="):]
        i += 1
    return _resolve_repo_from_git(cwd)


def _resolve_repo_from_git(cwd: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=_GIT_REMOTE_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    m = _REPO_URL_RE.search(r.stdout.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


# ---------------------------------------------------------------------------
# Label fetch + cache
# ---------------------------------------------------------------------------


def _cache_path() -> Path:
    override = os.environ.get("PRAXIS_GH_LABEL_CACHE_PATH")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        root = Path(base)
    else:
        root = Path.home() / ".cache"
    return root / "claude-praxis" / "gh-label-cache.json"


def _cache_ttl_sec() -> int:
    raw = os.environ.get("PRAXIS_GH_LABEL_CACHE_TTL_SEC")
    if raw is None:
        return _DEFAULT_CACHE_TTL_SEC
    try:
        v = int(raw)
        return v if v >= 0 else _DEFAULT_CACHE_TTL_SEC
    except ValueError:
        return _DEFAULT_CACHE_TTL_SEC


def _get_labels(repo: str) -> tuple[frozenset[str], bool] | None:
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
        if (
            isinstance(fetched_at, (int, float))
            and isinstance(labels, list)
            and (now - fetched_at) < _cache_ttl_sec()
        ):
            return frozenset(labels), bool(entry.get("truncated", True))

    fetched = _fetch_labels(repo)
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


def _fetch_labels(repo: str) -> tuple[set[str], bool] | None:
    """Return (labels, truncated) for `repo`, or None on any fetch failure.

    `truncated` is True when the listing filled the row limit — the repo may
    hold more labels than we can see, so absence from `labels` proves nothing.
    """
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
            timeout=_GH_LABEL_FETCH_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    labels = {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    return labels, len(labels) >= _GH_LABEL_LIMIT


def _label_absent(repo: str, label: str) -> bool:
    """True only when the API definitively reports `label` missing (404).

    Every other outcome — success, network/auth failure, an unexpected status —
    returns False so the gate fails open rather than blocking on an absence it
    could not prove.
    """
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{repo}/labels/{quote(label, safe='')}"],
            capture_output=True,
            text=True,
            timeout=_GH_LABEL_FETCH_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode == 0:
        return False
    return "404" in r.stderr


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
