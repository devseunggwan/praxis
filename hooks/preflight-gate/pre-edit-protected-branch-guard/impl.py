#!/usr/bin/env python3
"""PreToolUse guard: block edits on protected branches without a worktree.

Fires on Edit / Write / NotebookEdit tool calls. Two independent deny paths:

  Dirty-tree path (existing) — blocks when ALL three are true:
    (a) current branch ∈ protected set (main, dev, prod, master — configurable)
    (b) git status --porcelain is non-empty (dirty working tree)
    (c) the edit target path is NOT already part of the existing dirty diff
        (allows continuing in-flight work on files already being edited)

  PR-workflow path (issue #231) — blocks when ALL three are true:
    (a) current branch ∈ protected set
    (b) git status --porcelain is empty (clean working tree)
    (c) `git log --oneline -3` contains a `(#NNN)` PR-suffix on any line
        (signals the repo uses a PR workflow; direct main edits violate it)

False-positive skip rules (pass-through, no block — apply to both paths):
  - Self-editing: paths inside the praxis plugin directory (CLAUDE_PLUGIN_ROOT)
  - Planning artifacts: /tmp/, .omc/plans/, .claude/projects/ paths
  - Docs-only: README*, CHANGELOG*, CONTRIBUTING*, /docs/ directory
    (disable per-repo via PRAXIS_PBGUARD_BLOCK_DOCS=1)
  - Full opt-out: PRAXIS_PBGUARD_SKIP=1
  - PR-workflow path only: PRAXIS_PBGUARD_SKIP_PR_CHECK=1

Customization (env vars or .claude/hook-config.json):
  - PRAXIS_ISSUE_TRACKER_URL: URL shown in the block message
  - PRAXIS_PROTECTED_BRANCHES: comma-separated branch list
  - hook-config.json keys: "issue_tracker_url", "protected_branches"

Test overrides (for isolation without a real git repo):
  - PRAXIS_PBGUARD_TEST_REPO_ROOT: override repo root ("NONE" = not a git repo)
  - PRAXIS_PBGUARD_TEST_BRANCH: override current branch name
  - PRAXIS_PBGUARD_TEST_STATUS: override `git status --porcelain` output
    (empty string = clean tree; not set = call real git)
  - PRAXIS_PBGUARD_TEST_LOG: override `git log --oneline -3` output
    (empty string = no commits / no PR signal; not set = call real git)

Fail-open on all infrastructure errors:
  - Missing git binary, subprocess timeout, malformed stdin JSON → exit 0.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PROTECTED_BRANCHES = frozenset({"main", "dev", "prod", "master"})

DEFAULT_ISSUE_TRACKER_URL = (
    "<set via PRAXIS_ISSUE_TRACKER_URL env var "
    "or .claude/hook-config.json 'issue_tracker_url'>"
)

TARGET_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})

# /tmp/ scratch files are handled by get_repo_root fail-open (non-repo paths
# return None). Patterns here cover project-internal planning paths only.
PLANNING_PATH_PATTERNS = (".omc/plans/", ".claude/projects/")

DOCS_FILENAMES = frozenset({
    "readme", "changelog", "contributing", "license",
    "authors", "history", "news", "roadmap", "todo",
})

DENY_REASON_TEMPLATE = (
    "[pre-edit:protected-branch-guard] Edit blocked: you are on protected "
    "branch '{branch}' with a dirty working tree, and the edit target is not "
    "yet part of the dirty diff.\n"
    "\n"
    "The Issue-Driven Worktree Workflow requires a dedicated issue + branch + "
    "worktree before editing code on protected branches.\n"
    "\n"
    "Required steps:\n"
    "  1. Create an issue: {issue_tracker_url}\n"
    "  2. Create a branch + worktree:\n"
    "       git worktree add <path> -b <new-branch>\n"
    "  3. Work inside that worktree, then create a PR.\n"
    "\n"
    "Bypasses:\n"
    "  • Already editing a file on this branch? That file is already in the\n"
    "    dirty diff — this guard fires only on NEW file targets.\n"
    "  • Full opt-out for this session: set PRAXIS_PBGUARD_SKIP=1.\n"
    "  • Docs edits are skipped by default; set PRAXIS_PBGUARD_BLOCK_DOCS=1\n"
    "    to also guard doc files."
)

DENY_REASON_PR_WORKFLOW_TEMPLATE = (
    "[pre-edit:protected-branch-guard] Edit blocked: you are on protected "
    "branch '{branch}' and recent commits show a `(#NNN)` PR-suffix pattern, "
    "signaling this repo uses a PR workflow.\n"
    "\n"
    "Recent commit signal:\n"
    "{log_excerpt}\n"
    "\n"
    "Direct edits on '{branch}' violate the PR workflow. Create an issue + "
    "branch + worktree before editing.\n"
    "\n"
    "Required steps:\n"
    "  1. Create an issue: {issue_tracker_url}\n"
    "  2. Create a branch + worktree:\n"
    "       git worktree add <path> -b <new-branch>\n"
    "  3. Work inside that worktree, then create a PR.\n"
    "\n"
    "Bypasses:\n"
    "  • Skip just the PR-workflow check (keep dirty-tree check):\n"
    "       PRAXIS_PBGUARD_SKIP_PR_CHECK=1\n"
    "  • Full opt-out for this session: PRAXIS_PBGUARD_SKIP=1.\n"
    "  • Docs edits are skipped by default; set PRAXIS_PBGUARD_BLOCK_DOCS=1\n"
    "    to also guard doc files."
)

# Acceptance criteria from issue #231: regex \(#\d+\)$ on `git log --oneline -3`.
# Trailing \s* tolerates incidental whitespace from pagers / wrappers.
PR_SUFFIX_RE = re.compile(r"\(#\d+\)\s*$")


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: str) -> tuple[int, str]:
    """Run a git command in cwd. Returns (returncode, stdout). Fail-open on error."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return -1, ""


def get_repo_root(path: str) -> str | None:
    """Return the git repo root for path, or None if not in a repo / on error.

    Uses PRAXIS_PBGUARD_TEST_REPO_ROOT env var for test isolation.
    Special value "NONE" simulates a non-git-repo path (returns None).
    """
    override = os.environ.get("PRAXIS_PBGUARD_TEST_REPO_ROOT", "").strip()
    if override:
        return None if override == "NONE" else override

    # Walk up to the nearest existing ancestor directory so that paths like
    # "repo/src/new_dir/new_file.py" (where new_dir doesn't exist yet) still
    # resolve to the repo root instead of failing-open.
    cwd = path if os.path.isdir(path) else os.path.dirname(path)
    while cwd and not os.path.isdir(cwd):
        parent = os.path.dirname(cwd)
        if parent == cwd:  # reached filesystem root
            break
        cwd = parent
    if not cwd or not os.path.isdir(cwd):
        return None
    rc, out = _run_git(["rev-parse", "--show-toplevel"], cwd)
    if rc != 0:
        return None
    root = out.strip()
    return root if root else None


def get_current_branch(repo_root: str) -> str | None:
    """Return the current branch name, or None for detached HEAD / failure.

    Uses PRAXIS_PBGUARD_TEST_BRANCH env var for test isolation.
    Special value "HEAD" simulates detached HEAD (returns None).
    """
    override = os.environ.get("PRAXIS_PBGUARD_TEST_BRANCH", "").strip()
    if override:
        return None if override == "HEAD" else override

    rc, out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    if rc != 0:
        return None
    branch = out.strip()
    return branch if branch and branch != "HEAD" else None


def _parse_status_porcelain(status_output: str) -> set[str]:
    """Extract file paths from `git status --porcelain` output."""
    files: set[str] = set()
    for line in status_output.splitlines():
        if len(line) < 3:
            continue
        path_part = line[3:].strip().strip('"')
        # Handle rename notation "new-name -> old-name" (porcelain v1)
        for sep in [" -> ", "\t"]:
            if sep in path_part:
                for part in path_part.split(sep, 1):
                    p = part.strip().strip('"')
                    if p:
                        files.add(p)
                break
        else:
            if path_part:
                files.add(path_part)
    return files


def get_dirty_files(repo_root: str) -> set[str]:
    """Return relative paths of all dirty files in the repo.

    Uses PRAXIS_PBGUARD_TEST_STATUS env var for test isolation.
    Set to empty string to simulate a clean working tree.
    """
    if "PRAXIS_PBGUARD_TEST_STATUS" in os.environ:
        return _parse_status_porcelain(os.environ["PRAXIS_PBGUARD_TEST_STATUS"])

    rc, out = _run_git(["status", "--porcelain"], repo_root)
    if rc != 0:
        return set()
    return _parse_status_porcelain(out)


def get_recent_log(repo_root: str) -> str:
    """Return `git log --oneline -3` output, or empty string on failure.

    Uses PRAXIS_PBGUARD_TEST_LOG env var for test isolation.
    Empty string = no commits / no PR signal; not set = call real git.
    """
    if "PRAXIS_PBGUARD_TEST_LOG" in os.environ:
        return os.environ["PRAXIS_PBGUARD_TEST_LOG"]

    rc, out = _run_git(["log", "--oneline", "-3"], repo_root)
    if rc != 0:
        return ""
    return out


def has_pr_workflow_signal(repo_root: str) -> tuple[bool, str]:
    """Detect PR-workflow signal in recent commits (issue #231).

    Runs `git log --oneline -3` and matches each line against `\\(#\\d+\\)$`.
    Returns (True, log_excerpt) if any line matches, else (False, "").
    """
    log = get_recent_log(repo_root)
    if not log:
        return False, ""
    matched_lines: list[str] = []
    for line in log.splitlines():
        if PR_SUFFIX_RE.search(line):
            matched_lines.append(line)
    if not matched_lines:
        return False, ""
    return True, "\n".join(f"    {ln}" for ln in matched_lines)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_config_file(repo_root: str) -> dict:
    config_path = os.path.join(repo_root, ".claude", "hook-config.json")
    try:
        with open(config_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_protected_branches(config: dict) -> frozenset[str]:
    env_val = os.environ.get("PRAXIS_PROTECTED_BRANCHES", "").strip()
    if env_val:
        return frozenset(b.strip() for b in env_val.split(",") if b.strip())
    cfg_val = config.get("protected_branches")
    if isinstance(cfg_val, list):
        return frozenset(b for b in cfg_val if isinstance(b, str) and b.strip())
    return DEFAULT_PROTECTED_BRANCHES


def get_issue_tracker_url(config: dict) -> str:
    env_val = os.environ.get("PRAXIS_ISSUE_TRACKER_URL", "").strip()
    if env_val:
        return env_val
    cfg_val = config.get("issue_tracker_url")
    if isinstance(cfg_val, str) and cfg_val.strip():
        return cfg_val.strip()
    return DEFAULT_ISSUE_TRACKER_URL


# ---------------------------------------------------------------------------
# Skip-rule helpers
# ---------------------------------------------------------------------------


def get_file_path(tool_name: str, tool_input: dict) -> str:
    """Extract the edit target path from tool_input."""
    if tool_name == "NotebookEdit":
        return (tool_input.get("notebook_path") or "").strip()
    return (tool_input.get("file_path") or "").strip()


def is_planning_artifact(path: str) -> bool:
    """True for .omc/plans/ or .claude/projects/ paths.

    /tmp/ scratch files are intentionally NOT checked here: a non-repo /tmp/
    file will fail-open at get_repo_root (returns None → exit 0). Only
    project-internal planning paths need an explicit skip rule.
    """
    norm = path.replace("\\", "/")
    return any(pat in norm for pat in PLANNING_PATH_PATTERNS)


def is_docs_file(path: str) -> bool:
    """True if the path looks like a docs-only file (README, CHANGELOG, /docs/)."""
    norm = path.replace("\\", "/")
    if "/docs/" in norm or "/documentation/" in norm:
        return True
    basename = os.path.basename(norm).lower()
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    return stem in DOCS_FILENAMES


def is_self_edit(path: str) -> bool:
    """True if path is inside the praxis plugin directory (CLAUDE_PLUGIN_ROOT)."""
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
    if not plugin_root:
        # Fallback: parent of the hooks/ directory this script lives in.
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not plugin_root:
        return False
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(plugin_root))
        return not rel.startswith("..")
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def emit_deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _should_skip_edit(file_path: str) -> bool:
    """Return True if this edit target is exempt from the guard."""
    if os.environ.get("PRAXIS_PBGUARD_SKIP", "").strip() == "1":
        return True
    if is_self_edit(file_path):
        return True
    if is_planning_artifact(file_path):
        return True
    if is_docs_file(file_path) and os.environ.get("PRAXIS_PBGUARD_BLOCK_DOCS", "") != "1":
        return True
    return False


def _is_inflight_edit(file_path: str, repo_root: str, dirty_files: set[str]) -> bool:
    """Return True if the edit target is already in the dirty diff (in-flight)."""
    try:
        rel = os.path.relpath(os.path.abspath(file_path), repo_root)
    except ValueError:
        return True  # cross-drive (Windows) — fail-open, treat as in-flight
    return rel in dirty_files


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed input

    tool_name = payload.get("tool_name", "") or ""
    if tool_name not in TARGET_TOOLS:
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = get_file_path(tool_name, tool_input)
    if not file_path:
        return 0  # no path — fail-open

    if _should_skip_edit(file_path):
        return 0

    repo_root = get_repo_root(file_path)
    if not repo_root:
        return 0  # not in a git repo — fail-open

    config = _load_config_file(repo_root)
    branch = get_current_branch(repo_root)
    if not branch or branch not in get_protected_branches(config):
        return 0  # not a protected branch (or detached HEAD)

    dirty_files = get_dirty_files(repo_root)
    if not dirty_files:
        # Clean tree: fall through to PR-workflow signal check (issue #231).
        if os.environ.get("PRAXIS_PBGUARD_SKIP_PR_CHECK", "").strip() == "1":
            return 0
        has_signal, log_excerpt = has_pr_workflow_signal(repo_root)
        if has_signal:
            reason = DENY_REASON_PR_WORKFLOW_TEMPLATE.format(
                branch=branch,
                log_excerpt=log_excerpt,
                issue_tracker_url=get_issue_tracker_url(config),
            )
            emit_deny(reason)
        return 0

    if _is_inflight_edit(file_path, repo_root, dirty_files):
        return 0  # continuing in-flight work — allow

    reason = DENY_REASON_TEMPLATE.format(
        branch=branch,
        issue_tracker_url=get_issue_tracker_url(config),
    )
    emit_deny(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
