#!/usr/bin/env python3
"""Verify generated plugin manifests are in sync with the canonical source.

Runs the build logic in a dry mode: re-render every output, compare to the
committed file, and exit non-zero on any drift. Also validates that the
Codex adapter shell's symlinks point at the right relative targets.

CI invokes this; developers can too, via `./scripts/check-plugin-manifests.py`.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Reuse renderers from the build script (dynamic import — filename has a hyphen)
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "build_plugin_manifests", REPO_ROOT / "scripts" / "build-plugin-manifests.py"
)
assert _spec and _spec.loader
_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_build)


def main() -> int:
    base = _build.load_base()
    hooks_source = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())
    drifts: list[str] = []

    for platform_file in sorted(_build.PLATFORMS_DIR.glob("*.json")):
        platform = json.loads(platform_file.read_text())
        host_id = platform.get("host_id", platform["platform"])
        for output in platform["outputs"]:
            out_path = REPO_ROOT / output["path"]
            expected = (
                json.dumps(
                    _build.render_output(base, output, hooks_source, host_id),
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n"
            )
            actual = out_path.read_text() if out_path.exists() else ""
            if expected != actual:
                drifts.append(
                    f"DRIFT {output['path']}: regenerate with "
                    "./scripts/build-plugin-manifests.py"
                )

    # Spec existence check: every hook entry in hooks/hooks.json must have
    # a corresponding docs/hook/<name>.md file. Also validate the optional
    # `hosts` field shape — empty list or non-list silently drops the hook
    # from every platform, so block at CI rather than ship a footgun.
    docs_dir = REPO_ROOT / "docs" / "hook"
    for event_groups in hooks_source.get("hooks", {}).values():
        for group in event_groups:
            for hook in group.get("hooks", []):
                cmd = hook.get("command", "")
                hosts = hook.get("hosts")
                if hosts is not None:
                    if not isinstance(hosts, list):
                        drifts.append(
                            f"INVALID hosts in {cmd}: must be a list of strings, "
                            f"got {type(hosts).__name__}"
                        )
                    elif len(hosts) == 0:
                        drifts.append(
                            f"INVALID hosts in {cmd}: empty list drops the hook "
                            f"from every platform — omit the field to mean 'all hosts'"
                        )
                    elif not all(isinstance(h, str) for h in hosts):
                        drifts.append(
                            f"INVALID hosts in {cmd}: every entry must be a string"
                        )
                # Extract hook name: basename minus extension
                hook_filename = Path(cmd.split("/")[-1]) if "/" in cmd else Path(cmd)
                hook_name = hook_filename.stem
                # Strip argument suffixes (e.g. "strike-counter.sh session-start" → "strike-counter")
                hook_name = hook_name.split()[0] if " " in hook_name else hook_name
                # Normalize -pre / -post variant scripts to their shared spec name
                # (e.g. trino-describe-first-pre → trino-describe-first)
                for suffix in ("-pre", "-post"):
                    if hook_name.endswith(suffix):
                        hook_name = hook_name[: -len(suffix)]
                        break
                spec = docs_dir / f"{hook_name}.md"
                if not spec.exists():
                    drifts.append(
                        f"MISSING SPEC docs/hook/{hook_name}.md for hook: {cmd}"
                    )

    for name in _build.FORWARDED_DIRS:
        link = _build.ADAPTER_SHELL / name
        if not link.is_symlink():
            drifts.append(
                f"MISSING plugins/praxis/{name}: expected symlink → ../../{name}"
            )
            continue
        target = os.readlink(link)
        if target != f"../../{name}":
            drifts.append(
                f"BAD LINK plugins/praxis/{name}: points at {target!r}, "
                f"expected '../../{name}'"
            )

    # Version consistency: collect versioned plugin artifacts from platform manifests.
    versioned_kinds = {"plugin", "gemini-extension"}
    seen: dict[str, str] = {}
    for platform_file in sorted(_build.PLATFORMS_DIR.glob("*.json")):
        platform = json.loads(platform_file.read_text())
        for output in platform["outputs"]:
            if output["kind"] not in versioned_kinds:
                continue
            p = REPO_ROOT / output["path"]
            if not p.exists():
                continue
            data = json.loads(p.read_text())
            v = data.get("version") or (data.get("plugins") or [{}])[0].get("version")
            if v:
                seen[output["path"]] = v
    unique = set(seen.values())
    if len(unique) > 1:
        drifts.append(
            "VERSION DRIFT across artifacts: "
            + ", ".join(f"{k}={v}" for k, v in seen.items())
        )

    if drifts:
        print("plugin-manifest check FAILED:")
        for d in drifts:
            print(f"  - {d}")
        return 1
    print("plugin-manifest check OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
