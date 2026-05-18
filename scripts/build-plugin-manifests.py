#!/usr/bin/env python3
"""Generate platform-specific plugin manifests from a canonical base.

Reads:
  manifests/plugin.base.json     — shared metadata (name, description,
                                   author, repository, homepage, category,
                                   keywords)
  manifests/platforms/*.json     — per-platform output declarations
  VERSION                        — authoritative version string
  hooks/hooks.json               — canonical hook registry; each hook entry
                                   may carry a `hosts` whitelist that is
                                   filtered per platform host_id at build

Writes (generated artifacts, committed to the repo):
  .claude-plugin/plugin.json
  .claude-plugin/marketplace.json
  .claude-plugin/hooks/hooks.json
  .agents/plugins/marketplace.json
  plugins/praxis/.codex-plugin/plugin.json
  plugins/praxis/.codex-plugin/hooks/hooks.json
  .cursor-plugin/plugin.json
  .cursor-plugin/hooks/hooks.json
  gemini-extension.json
  .opencode/plugin.json
  .opencode/hooks/hooks.json

Also creates `plugins/praxis/{skills,hooks,scripts}` as symlinks into the
repo root so the Codex adapter shell forwards to the common runtime
directories without duplicating source.

Idempotent. Re-running on a clean tree produces no diff — that invariant
is what `scripts/check-plugin-manifests.py` verifies in CI.

Platform manifest schema:
  platform          str   — platform identifier
  host_id           str   — host token matched against each hook's `hosts`
                            whitelist (defaults to platform when omitted)
  outputs           list  — output declarations (see output kinds below)

Output kinds:
  plugin            — plugin.json with base metadata + plugin_overrides
  marketplace       — marketplace catalog JSON
  hooks             — hooks/hooks.json filtered to host_id via the per-hook
                      `hosts` whitelist (entries with no `hosts` are kept
                      for every platform)
  gemini-extension  — gemini-extension.json in Gemini CLI format
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFESTS_DIR = REPO_ROOT / "manifests"
PLATFORMS_DIR = MANIFESTS_DIR / "platforms"
ADAPTER_SHELL = REPO_ROOT / "plugins" / "praxis"
FORWARDED_DIRS = ("skills", "hooks", "scripts")
HOOKS_JSON_PATH = REPO_ROOT / "hooks" / "hooks.json"


def load_base() -> dict:
    base = json.loads((MANIFESTS_DIR / "plugin.base.json").read_text())
    base["version"] = (REPO_ROOT / "VERSION").read_text().strip()
    return base


def load_hooks() -> dict:
    return json.loads(HOOKS_JSON_PATH.read_text())


def filter_hooks_for_host(hooks_data: dict, host_id: str) -> dict:
    """Return a copy of hooks_data with each entry filtered to host_id.

    An entry is kept iff its `hosts` field is absent (= all hosts) OR contains
    host_id. The `hosts` field itself is stripped from the output so generated
    files contain only the standard hook schema. Empty groups/events are
    removed so the rendered file omits surfaces with zero hooks for the host.
    """
    filtered = copy.deepcopy(hooks_data)
    result_hooks: dict = {}
    for event_name, event_groups in filtered.get("hooks", {}).items():
        kept_groups = []
        for group in event_groups:
            kept = []
            for hook in group.get("hooks", []):
                hosts = hook.get("hosts")
                if hosts is None or host_id in hosts:
                    entry = {k: v for k, v in hook.items() if k != "hosts"}
                    kept.append(entry)
            if kept:
                new_group = {k: v for k, v in group.items() if k != "hooks"}
                new_group["hooks"] = kept
                kept_groups.append(new_group)
        if kept_groups:
            result_hooks[event_name] = kept_groups
    out = {k: v for k, v in filtered.items() if k != "hooks"}
    out["hooks"] = result_hooks
    return out


def render_plugin(base: dict, overrides: dict) -> dict:
    manifest = {
        "name": base["name"],
        "description": base["description"],
        "version": base["version"],
        "author": base["author"],
        "repository": base["repository"],
        "keywords": base["keywords"],
    }
    manifest.update(overrides)
    return manifest


def render_marketplace(base: dict, plugin_source: str, extras: dict | None) -> dict:
    manifest = {}
    if extras:
        manifest.update(extras)
    manifest.update({
        "name": base["name"],
        "description": base["description"],
        "owner": base["author"],
        "version": base["version"],
        "plugins": [
            {
                "name": base["name"],
                "description": base["description"],
                "version": base["version"],
                "author": base["author"],
                "source": plugin_source,
                "category": base["category"],
                "homepage": base["homepage"],
                "tags": base["keywords"],
            }
        ],
    })
    return manifest


def render_gemini_extension(base: dict) -> dict:
    author_name = (
        base["author"]["name"]
        if isinstance(base["author"], dict)
        else base["author"]
    )
    return {
        "name": base["name"],
        "description": base["description"],
        "version": base["version"],
        "author": author_name,
        "repository": base["repository"],
        "keywords": base["keywords"],
    }


def render_output(base: dict, output: dict, hooks_source: dict, host_id: str) -> dict:
    kind = output["kind"]
    if kind == "plugin":
        return render_plugin(base, output.get("plugin_overrides", {}))
    if kind == "marketplace":
        return render_marketplace(
            base,
            plugin_source=output["plugin_source"],
            extras=output.get("marketplace_overrides"),
        )
    if kind == "hooks":
        return filter_hooks_for_host(hooks_source, host_id)
    if kind == "gemini-extension":
        return render_gemini_extension(base)
    raise ValueError(f"unknown output kind: {kind}")


def write_json(path: Path, data: dict) -> bool:
    """Write JSON with deterministic formatting. Return True if content changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    existing = path.read_text() if path.exists() else None
    if existing == payload:
        return False
    path.write_text(payload)
    return True


def ensure_symlink(link: Path, target_relative: str) -> bool:
    """Create or repair `link` to point at `target_relative`. Return True on change."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        if os.readlink(link) == target_relative:
            return False
        link.unlink()
    elif link.exists():
        raise SystemExit(
            f"refusing to replace real path with symlink: {link}. "
            "Resolve manually before re-running."
        )
    link.symlink_to(target_relative)
    return True


def main() -> int:
    base = load_base()
    hooks_source = load_hooks()

    changed_paths: list[str] = []

    for platform_file in sorted(PLATFORMS_DIR.glob("*.json")):
        platform = json.loads(platform_file.read_text())
        host_id = platform.get("host_id", platform["platform"])
        for output in platform["outputs"]:
            out_path = REPO_ROOT / output["path"]
            rendered = render_output(base, output, hooks_source, host_id)
            if write_json(out_path, rendered):
                changed_paths.append(output["path"])

    # Adapter shell symlinks — Codex plugin forwards to repo-root runtime dirs.
    for name in FORWARDED_DIRS:
        link = ADAPTER_SHELL / name
        target = f"../../{name}"  # relative from plugins/praxis/<name>
        if ensure_symlink(link, target):
            changed_paths.append(str(link.relative_to(REPO_ROOT)))

    if changed_paths:
        print("wrote:")
        for p in changed_paths:
            print(f"  {p}")
    else:
        print("clean — no changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
