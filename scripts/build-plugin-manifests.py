#!/usr/bin/env python3
"""Generate platform-specific plugin manifests from a canonical base.

Reads:
  manifests/plugin.base.json     — shared metadata (name, description,
                                   author, repository, homepage, category,
                                   keywords)
  manifests/platforms/*.json     — per-platform output declarations
  VERSION                        — authoritative version string

Writes (generated artifacts, committed to the repo):
  .claude-plugin/plugin.json
  .claude-plugin/marketplace.json
  .agents/plugins/marketplace.json
  plugins/praxis/.codex-plugin/plugin.json

Also creates `plugins/praxis/{skills,hooks,scripts}` as symlinks into the
repo root so the Codex adapter shell forwards to the common runtime
directories without duplicating source.

Idempotent. Re-running on a clean tree produces no diff — that invariant
is what `scripts/check-plugin-manifests.py` verifies in CI.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFESTS_DIR = REPO_ROOT / "manifests"
PLATFORMS_DIR = MANIFESTS_DIR / "platforms"
ADAPTER_SHELL = REPO_ROOT / "plugins" / "praxis"
FORWARDED_DIRS = ("skills", "hooks", "scripts")


def load_base() -> dict:
    base = json.loads((MANIFESTS_DIR / "plugin.base.json").read_text())
    base["version"] = (REPO_ROOT / "VERSION").read_text().strip()
    return base


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


def render_output(base: dict, output: dict) -> dict:
    kind = output["kind"]
    if kind == "plugin":
        return render_plugin(base, output.get("plugin_overrides", {}))
    if kind == "marketplace":
        return render_marketplace(
            base,
            plugin_source=output["plugin_source"],
            extras=output.get("marketplace_overrides"),
        )
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
        for output in platform["outputs"]:
            out_path = REPO_ROOT / output["path"]
            rendered = render_output(base, output)
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
