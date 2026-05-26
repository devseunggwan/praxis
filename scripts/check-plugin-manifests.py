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
import re
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
                # (e.g. pre-edit-md-escape-advisory-pre → pre-edit-md-escape-advisory)
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

    # Rule 1 — Hook index drift
    # For each registered hook in hooks/hooks.json, verify it appears in both
    # docs/hook/INDEX.md and ARCHITECTURE.md Hook index table.
    index_md = (REPO_ROOT / "docs" / "hook" / "INDEX.md").read_text()
    arch_md = (REPO_ROOT / "ARCHITECTURE.md").read_text()
    seen_hook_names: set[str] = set()
    for event_groups in hooks_source.get("hooks", {}).values():
        for group in event_groups:
            for hook in group.get("hooks", []):
                cmd = hook.get("command", "")
                hook_filename = Path(cmd.split("/")[-1]) if "/" in cmd else Path(cmd)
                hook_name = hook_filename.stem
                hook_name = hook_name.split()[0] if " " in hook_name else hook_name
                for suffix in ("-pre", "-post"):
                    if hook_name.endswith(suffix):
                        hook_name = hook_name[: -len(suffix)]
                        break
                if hook_name in seen_hook_names:
                    continue
                seen_hook_names.add(hook_name)
                if hook_name not in index_md:
                    drifts.append(
                        f"MISSING INDEX docs/hook/INDEX.md: {hook_name} "
                        f"(registered in hooks.json but not in INDEX.md)"
                    )
                if hook_name not in arch_md:
                    drifts.append(
                        f"MISSING INDEX ARCHITECTURE.md: {hook_name} "
                        f"(registered in hooks.json but not in ARCHITECTURE.md)"
                    )

    # Rule 2 — Supported hosts ↔ hosts array cross-check
    # For each hook spec in docs/hook/*.md, compare the "Supported hosts:" line
    # to the actual hosts field in hooks/hooks.json.
    # Build a lookup: hook_name → hosts value from hooks.json (None = all hosts).
    hooks_json_hosts: dict[str, list[str] | None] = {}
    for event_groups in hooks_source.get("hooks", {}).values():
        for group in event_groups:
            for hook in group.get("hooks", []):
                cmd = hook.get("command", "")
                hook_filename = Path(cmd.split("/")[-1]) if "/" in cmd else Path(cmd)
                hook_name = hook_filename.stem
                hook_name = hook_name.split()[0] if " " in hook_name else hook_name
                for suffix in ("-pre", "-post"):
                    if hook_name.endswith(suffix):
                        hook_name = hook_name[: -len(suffix)]
                        break
                # First registration wins (multiple groups may share same hook name)
                if hook_name not in hooks_json_hosts:
                    hooks_json_hosts[hook_name] = hook.get("hosts")

    for spec_file in sorted(docs_dir.glob("*.md")):
        if spec_file.name == "INDEX.md":
            continue
        hook_name = spec_file.stem
        # Parse "Supported hosts:" from the spec (typically line 3, but scan first 10)
        spec_text = spec_file.read_text()
        hosts_value: str | None = None
        for line in spec_text.splitlines()[:10]:
            if line.strip().lower().startswith("supported hosts:"):
                hosts_value = line.split(":", 1)[1].strip()
                break
        if hosts_value is None:
            # No Supported hosts line — skip (spec not yet annotated)
            continue

        # Hooks not registered in hooks.json are opt-in — skip hosts cross-check
        if hook_name not in hooks_json_hosts:
            continue

        json_hosts = hooks_json_hosts[hook_name]

        if hosts_value.lower() == "all":
            # Spec says all → hooks.json must have NO hosts field
            if json_hosts is not None:
                drifts.append(
                    f"FAIL hosts mismatch {hook_name}: "
                    f"spec='all' hooks.json={json_hosts!r} "
                    f"(remove hosts array to mean all hosts)"
                )
        else:
            # Spec lists explicit hosts — parse comma-separated, compare as sets.
            # Strip parenthetical comments from each token (e.g. "claude (excludes …)")
            # so only the bare host identifier is retained.
            raw_tokens = hosts_value.split(",")
            spec_set = {
                t.split("(")[0].strip()
                for t in raw_tokens
                if t.split("(")[0].strip()
            }
            json_set = set(json_hosts) if json_hosts is not None else set()
            if spec_set != json_set:
                drifts.append(
                    f"FAIL hosts mismatch {hook_name}: "
                    f"spec={sorted(spec_set)!r} hooks.json={sorted(json_set)!r}"
                )

    # Rule 3 — Wrapper parallel-generation parity (ADR-0001 §5.1).
    # For each source wrapper hooks/<name>.sh that has a sibling .py impl,
    # confirm it ends up exec-ing the same .py file that the generator's
    # canonical template targets. ADR §5.1 calls this "byte-equivalent
    # (modulo header comment)"; strict line-by-line diff is impractical
    # because the 39 hand-maintained wrappers use two prelude styles
    # (`set +e` + inline exec vs `set -euo pipefail` + `PY=…` indirection),
    # so we compare the load-bearing detail — the .py target referenced
    # in the script body. Phase 2 will collapse both styles into the
    # generator output and let us tighten this to a strict diff.
    py_re = re.compile(r"([a-z0-9][a-z0-9_-]*\.py)")

    def extract_py_target(text: str) -> str | None:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = py_re.search(stripped)
            if m:
                return m.group(1)
        return None

    for name in _build.discover_wrapper_targets():
        source = REPO_ROOT / "hooks" / f"{name}.sh"
        if not source.exists():
            drifts.append(f"WRAPPER MISSING hooks/{name}.sh")
            continue
        source_target = extract_py_target(source.read_text())
        # Generator's expected target — discover_wrapper_targets() returns the
        # .sh stem (incl. -pre/-post variants); render_wrapper handles the
        # multi-event normalization to the shared .py.
        generated_target = extract_py_target(_build.render_wrapper(name))
        if source_target is None:
            drifts.append(
                f"WRAPPER PARSE hooks/{name}.sh: no .py target found in body "
                "(parity check cannot verify)"
            )
            continue
        if generated_target is None:
            drifts.append(
                f"WRAPPER GENERATOR template did not yield an exec target for "
                f"{name!r} — inspect render_wrapper()"
            )
            continue
        if source_target != generated_target:
            drifts.append(
                f"WRAPPER PARITY hooks/{name}.sh: source execs {source_target!r}, "
                f"generated execs {generated_target!r}"
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
