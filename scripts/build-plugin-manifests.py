#!/usr/bin/env python3
"""Generate platform-specific plugin manifests from a canonical base.

Reads:
  manifests/plugin.base.json     — shared metadata (name, description,
                                   author, repository, homepage, category,
                                   keywords)
  manifests/platforms/*.json     — per-platform output declarations
  VERSION                        — authoritative version string
  hooks/hooks.json               — canonical hook registry (used when
                                   generating filtered-hooks outputs)

Writes (generated artifacts, committed to the repo):
  .claude-plugin/plugin.json
  .claude-plugin/marketplace.json
  .agents/plugins/marketplace.json
  plugins/praxis/.codex-plugin/plugin.json
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
  excluded_hooks    list  — hook script names (without .sh) to omit from
                            filtered-hooks outputs; documentation for
                            gemini-extension outputs
  excluded_skills   list  — reserved for future per-platform skill filtering
  outputs           list  — output declarations (see output kinds below)

Output kinds:
  plugin            — plugin.json with base metadata + plugin_overrides
  marketplace       — marketplace catalog JSON
  filtered-hooks    — hooks/hooks.json with excluded_hooks entries removed
  gemini-extension  — gemini-extension.json in Gemini CLI format
"""
from __future__ import annotations

import json
import os
import re
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


def _extract_hook_name(command: str) -> str:
    """Return the hook script basename (no .sh) from a hook command string."""
    m = re.search(r"/hooks/([^/\s]+?)\.sh", command)
    return m.group(1) if m else ""


def filter_hooks(hooks_config: dict, excluded: list[str]) -> dict:
    """Return a new hooks config with entries for excluded scripts removed.

    An entry is removed when its ``command`` field references a script whose
    basename (without .sh) appears in *excluded*.  Events and groups that
    become empty after filtering are also removed.
    """
    if not excluded:
        return hooks_config
    excluded_set = set(excluded)
    result: dict = {"description": hooks_config.get("description", ""), "hooks": {}}
    for event, groups in hooks_config.get("hooks", {}).items():
        filtered_groups = []
        for group in groups:
            kept = [
                entry
                for entry in group.get("hooks", [])
                if _extract_hook_name(entry.get("command", "")) not in excluded_set
            ]
            if kept:
                new_group = {k: v for k, v in group.items() if k != "hooks"}
                new_group["hooks"] = kept
                filtered_groups.append(new_group)
        if filtered_groups:
            result["hooks"][event] = filtered_groups
    return result


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


def render_output(
    base: dict,
    output: dict,
    excluded_hooks: list[str] | None = None,
) -> dict:
    kind = output["kind"]
    if kind == "plugin":
        return render_plugin(base, output.get("plugin_overrides", {}))
    if kind == "marketplace":
        return render_marketplace(
            base,
            plugin_source=output["plugin_source"],
            extras=output.get("marketplace_overrides"),
        )
    if kind == "filtered-hooks":
        hooks_config = load_hooks()
        return filter_hooks(hooks_config, excluded_hooks or [])
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

    changed_paths: list[str] = []

    for platform_file in sorted(PLATFORMS_DIR.glob("*.json")):
        platform = json.loads(platform_file.read_text())
        excluded_hooks = platform.get("excluded_hooks", [])
        for output in platform["outputs"]:
            out_path = REPO_ROOT / output["path"]
            rendered = render_output(base, output, excluded_hooks)
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
