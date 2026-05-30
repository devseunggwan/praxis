#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `gh issue create` on hub-mediated org child repos.

When a project uses a hub-mediated issue tracker (e.g. a dedicated hub repo for
cross-team issue routing), creating issues directly on child repos bypasses the
fan-out, label, and tracking conventions that the hub workflow enforces. This hook
blocks `gh issue create` calls targeting child repos of configured hub-mediated
orgs and points the agent to the correct creation skill instead.

Default behaviour: the hook is a NO-OP when `PRAXIS_HUB_MEDIATED_ORGS` is unset
or empty. This preserves backward compatibility — praxis itself is not a
hub-mediated org, so the hook must not block any existing workflow out-of-the-box.

Config env vars:
  PRAXIS_HUB_MEDIATED_ORGS
    Comma-separated list of org entries.  Each entry has the form:
      <org>:<hub_repo>:<creation_skill>
    where `creation_skill` may itself contain colons (e.g. `example-org:skill`).
    Parsing: split each entry on the first two colons only — the remainder is the
    creation_skill verbatim.  Example:
      "example-org:example-org/hub:example-org:create-hub-issue,other-org:other-org/hub:other-org:create-hub-issue"
    Malformed entries (< 3 colon-delimited parts or empty org/hub_repo) are
    silently skipped — fail-safe PASS.

  PRAXIS_HOOK_BYPASS_HUB_ENFORCE
    When set to any non-empty value, the hook exits 0 (pass) without checking.
    Use for one-off situations where a direct child-repo issue is intentional.

Block conditions (ALL must hold):
  1. The --repo / -R value's org matches a configured hub-mediated org.
  2. The --repo / -R value is NOT the hub repo itself for that org.
  3. PRAXIS_HOOK_BYPASS_HUB_ENFORCE is not set.

Pass conditions (any one sufficient):
  - PRAXIS_HUB_MEDIATED_ORGS unset / empty → NO-OP.
  - Command contains no `gh issue create` / `gh issue new` invocation (includes
    the official alias and `gh -R <repo> issue create` global-flag form).
  - No --repo / -R flag present in any create invocation → PASS (cannot resolve
    repo without shell env; keep simple — no-repo == no-block).
  - Compound commands (&&, ||, ;, |) are split; each segment is checked
    independently so a hub-repo invocation cannot mask a later child-repo one.
  - The --repo value uses [HOST/]OWNER/REPO form; the HOST prefix is stripped
    before org extraction and hub_repo comparison.
  - The repo's org is not in the allowlist.
  - The repo IS the hub repo.
  - PRAXIS_HOOK_BYPASS_HUB_ENFORCE is set (non-empty).
  - Malformed JSON stdin → fail-open (exit 0).
  - Malformed config entry → skip that entry (fail-safe PASS if no valid entry
    matches).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

class OrgConfig(NamedTuple):
    org: str           # lowercase org name, e.g. "example-org"
    hub_repo: str      # full "org/repo" of the hub, e.g. "example-org/hub"
    creation_skill: str  # skill name, may contain colons, e.g. "example-org:create-hub-issue"


def _parse_hub_mediated_orgs(raw: str) -> list[OrgConfig]:
    """Parse PRAXIS_HUB_MEDIATED_ORGS into a list of OrgConfig.

    Each comma-separated entry: <org>:<hub_repo>:<creation_skill>
    Split on first two colons only so creation_skill may contain colons.
    Malformed entries are silently skipped.
    """
    configs: list[OrgConfig] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        # Split on first colon → org
        first_colon = entry.find(":")
        if first_colon == -1:
            continue  # malformed — no colons
        org = entry[:first_colon].strip().lower()
        rest = entry[first_colon + 1:]

        # Split rest on first colon → hub_repo, creation_skill
        second_colon = rest.find(":")
        if second_colon == -1:
            continue  # malformed — only one colon
        hub_repo = rest[:second_colon].strip()
        creation_skill = rest[second_colon + 1:].strip()

        if not org or not hub_repo or not creation_skill:
            continue  # malformed — empty field

        # Normalise hub_repo to lowercase for comparison
        configs.append(OrgConfig(org=org, hub_repo=hub_repo.lower(), creation_skill=creation_skill))
    return configs


# ---------------------------------------------------------------------------
# Repo flag extraction
# ---------------------------------------------------------------------------

# Matches --repo X, --repo=X, -R X, -R=X, -R<value> (concatenated short form).
# gh -R accepts OWNER/REPO or HOST/OWNER/REPO; host stripping happens in main().
# Capture group 1: the repo value.
_REPO_FLAG_RE = re.compile(
    r"(?:--repo[ =]|-R[ =]?)([^\s]+)"
)

# Matches any of the four gh-issue-create invocation forms:
#   1. gh [global-flags] issue [flags] (create|new) ...  (global -R before command group)
#   2. gh issue [flags] (create|new) ...                 (standard, alias, flag-before-subcommand)
# "flags" = sequences of -X[=val] or --flag[=val]; non-flag tokens end the flag run.
# Pattern gates on subcommand keyword appearing as the first non-flag token after
# "issue" to avoid matching "create"/"new" as a flag value.
_GH_ISSUE_CREATE_RE = re.compile(
    r"\bgh(?:\s+(?:-\S+(?:\s+[^-\s]\S*)?))*\s+issue(?:\s+(?:-\S+(?:\s+[^-\s]\S*)?))*\s+(?:create|new)\b"
)

# Splits a shell command on pipeline/sequential operators to isolate each
# individual invocation.  Simple split — does not handle quoting around &&/;.
_CMD_SEP_RE = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")


def _extract_repos_for_creates(command: str) -> list[str]:
    """Return repo values for every gh-issue-create invocation in *command*.

    Splits on shell operators first so that each segment is checked
    independently, preventing a hub-repo invocation from masking a later
    child-repo invocation in a compound command.  Within each segment,
    searches for a repo flag if that segment contains a gh-issue-create call.
    """
    repos: list[str] = []
    for segment in _CMD_SEP_RE.split(command):
        if not _GH_ISSUE_CREATE_RE.search(segment):
            continue
        m = _REPO_FLAG_RE.search(segment)
        if m:
            repos.append(m.group(1).strip("'\""))
    return repos


def _repo_org(repo: str) -> str:
    """Return the org portion of 'org/repo' or 'host/org/repo', lowercased.

    gh -R accepts [HOST/]OWNER/REPO.  When three slash-segments are present
    the first is the host; the second is the owner/org.
    """
    parts = repo.split("/")
    # [HOST/]OWNER/REPO → parts has 3 elements when host is present
    if len(parts) >= 3:
        return parts[-2].lower()
    return parts[0].lower()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _main_inner() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0  # fail-open

    # Bypass env var — any non-empty value passes
    if os.environ.get("PRAXIS_HOOK_BYPASS_HUB_ENFORCE", ""):
        return 0

    # No config → NO-OP
    raw_config = os.environ.get("PRAXIS_HUB_MEDIATED_ORGS", "").strip()
    if not raw_config:
        return 0

    org_configs = _parse_hub_mediated_orgs(raw_config)
    if not org_configs:
        return 0  # all entries malformed → fail-safe pass

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")

    # Extract repos for every gh-issue-create invocation (handles compound
    # commands and global -R-before-command-group forms).
    repos = _extract_repos_for_creates(command)
    if not repos:
        # No gh-issue-create call or no --repo flag anywhere → pass.
        return 0

    # Check each repo independently; block on the first child-repo match.
    for repo in repos:
        repo_lower = repo.lower()
        # Normalise [HOST/]OWNER/REPO → OWNER/REPO for hub_repo comparison.
        repo_parts = repo_lower.split("/")
        repo_canonical = "/".join(repo_parts[-2:]) if len(repo_parts) >= 3 else repo_lower
        repo_org = _repo_org(repo_lower)

        # Find a matching org config
        matched: OrgConfig | None = None
        for cfg in org_configs:
            if cfg.org == repo_org:
                matched = cfg
                break

        if matched is None:
            continue  # org not in allowlist → pass this repo

        if repo_canonical == matched.hub_repo:
            continue  # this IS the hub repo → pass this repo

        # Block on the first child-repo found
        _emit_block(repo, matched)
        return 2

    return 0


def main() -> int:
    """Preflight gate — fail-open on uncaught exception."""
    try:
        return _main_inner()
    except Exception:
        return 0


def _emit_block(repo: str, cfg: OrgConfig) -> None:
    emit_block(
        rule_name="block-child-repo-issue-create",
        why=(
            f"`gh issue create --repo {repo}` targets a child repo of the "
            f"hub-mediated org '{cfg.org}'. Creating issues directly on child "
            "repos bypasses hub fan-out, label assignment, and cross-repo "
            "tracking conventions."
        ),
        correct_path=(
            f"Use Skill(\"{cfg.creation_skill}\") to create the issue through "
            f"the hub workflow (hub repo: {cfg.hub_repo})."
        ),
        bypass_env="PRAXIS_HOOK_BYPASS_HUB_ENFORCE",
        reference=(
            "CLAUDE.md → GitHub Issue Hygiene; "
            "docs/hub-mediated-orgs.md"
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
