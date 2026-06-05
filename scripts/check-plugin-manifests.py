#!/usr/bin/env python3
"""Verify generated plugin manifests are in sync with the canonical source.

Runs the build logic in dry mode: re-render every output, compare to the
committed file, and exit non-zero on any drift. Also validates the
Phase 2 (ADR-0001) invariants:

  1. Every hooks/<role>/<name>/ directory has ≥1 manifest entry
     (opt-in carve-out: external-write-falsify-check).
  2. Every manifest entry's `role` field matches the parent directory name.
  3. The impl file (`impl.py` or the body-as-sh `impl.sh`) exists on disk.
  4. completion-verify Stop ordering matches manifest array order.
  5. Generated `.claude-plugin/hooks/hooks.json` (+ peers) are byte-
     identical to what `build-plugin-manifests.py` would emit (this is
     the runtime contract — Claude Code reads the generated file).
  6. Generated runtime wrappers under hooks/*.sh are byte-identical to
     the generator output (Phase 2 strict diff replaces Phase 1's exec-
     target parity check).
  7. INDEX.md ↔ manifest entry cross-check.
  8. Spec `Supported hosts:` ↔ manifest `hosts` cross-check.
  9. Version consistency across versioned artifacts.
  10. spec.md exists at hooks/<role>/<name>/spec.md for every registered
      hook (Phase 3, ADR-0001 §5.3 — specs collocated with impl).
  11. skills/<skill-name>/ on disk matches the EXPECTED_SKILLS frozen set
     (issue #465 — surface freeze gate against silent skill proliferation).
  12. AGENTS.md "## Skills (N)" count and per-skill backtick tokens, and
     README.md per-skill backtick tokens, all match EXPECTED_SKILLS
     (issue #498 — doc drift invariant).

CI invokes this; developers can too, via `./scripts/check-plugin-manifests.py`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "build_plugin_manifests", REPO_ROOT / "scripts" / "build-plugin-manifests.py"
)
assert _spec and _spec.loader
_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_build)

from constants import EXPECTED_SKILLS, OPT_IN_HOOKS, VALID_ROLES  # noqa: E402


def _skill_dirs() -> list[Path]:
    """Return all skill directories under skills/<skill-name>/.

    Mirrors `_hook_dirs()` convention: files (SKILL.md.tmpl) and underscore-
    prefixed entries (future internal layout) are excluded automatically.

    A directory counts as a skill only if it carries a `SKILL.md`. Binary-only
    dirs (e.g. `bypass-review`, which ships a CLI under `skills/` but has no
    `SKILL.md` and cannot be invoked as `/praxis:*`) are excluded so they are
    not double-counted against the skill surface (issue #582).
    """
    skills_root = REPO_ROOT / "skills"
    dirs: list[Path] = []
    if not skills_root.is_dir():
        return dirs
    for entry in sorted(skills_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("_") or entry.name == "__pycache__":
            continue
        if not (entry / "SKILL.md").exists():
            continue
        dirs.append(entry)
    return dirs


def _hook_dirs() -> list[Path]:
    """Return all per-hook directories under hooks/<role>/<name>/."""
    dirs: list[Path] = []
    hooks_root = _build.HOOKS_DIR
    for role_dir in sorted(hooks_root.iterdir()):
        if not role_dir.is_dir():
            continue
        if role_dir.name in {"_lib", "_generated", "__pycache__"}:
            continue
        if role_dir.name not in VALID_ROLES:
            continue
        for hook_dir in sorted(role_dir.iterdir()):
            if hook_dir.is_dir() and not hook_dir.name.startswith("_"):
                dirs.append(hook_dir)
    return dirs


def main() -> int:
    base = _build.load_base()
    manifest = _build.load_manifest()
    hooks_source = _build.expand_to_hooks_json(manifest)
    # ADR-0002: must mirror build-plugin-manifests.main() so the expected
    # hooks.json collapses dispatch groups identically to the committed output.
    dispatch_groups = frozenset(
        (g["event"], g.get("matcher")) for g in manifest.get("dispatch_groups", [])
    )
    drifts: list[str] = []

    # ------------------------------------------------------------------
    # Drift check (rule 5) — generated artifacts byte-identical
    # ------------------------------------------------------------------
    for platform_file in sorted(_build.PLATFORMS_DIR.glob("*.json")):
        platform = json.loads(platform_file.read_text())
        host_id = platform.get("host_id", platform["platform"])
        for output in platform["outputs"]:
            out_path = REPO_ROOT / output["path"]
            expected = (
                json.dumps(
                    _build.render_output(
                        base, output, hooks_source, host_id, dispatch_groups
                    ),
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

    # ------------------------------------------------------------------
    # Rule 1 — directory ↔ manifest cross-check
    # Rule 2 — role field matches parent directory name
    # Rule 3 — impl file exists on disk
    # ------------------------------------------------------------------
    manifest_by_name: dict[str, list[dict]] = {}
    for entry in manifest["hooks"]:
        manifest_by_name.setdefault(entry["name"], []).append(entry)

    on_disk: dict[str, Path] = {}
    for hook_dir in _hook_dirs():
        on_disk[hook_dir.name] = hook_dir
        role_on_disk = hook_dir.parent.name
        if hook_dir.name in OPT_IN_HOOKS:
            continue  # opt-in: no manifest entry expected
        if hook_dir.name not in manifest_by_name:
            drifts.append(
                f"UNREGISTERED hooks/{role_on_disk}/{hook_dir.name}: "
                f"directory exists but no manifest.json entry"
            )
            continue
        for entry in manifest_by_name[hook_dir.name]:
            if entry["role"] != role_on_disk:
                drifts.append(
                    f"ROLE MISMATCH {hook_dir.name}: directory says "
                    f"{role_on_disk!r}, manifest says {entry['role']!r}"
                )
            impl_file = "impl.sh" if entry.get("body") == "impl.sh" else "impl.py"
            if not (hook_dir / impl_file).exists():
                drifts.append(
                    f"MISSING IMPL hooks/{role_on_disk}/{hook_dir.name}/{impl_file}"
                )

    # Reverse: every manifest entry must have a directory
    for name in manifest_by_name:
        if name not in on_disk:
            drifts.append(
                f"MANIFEST GHOST {name}: manifest.json entry has no "
                f"hooks/<role>/{name}/ directory"
            )

    # ------------------------------------------------------------------
    # Rule 4 — completion-verify Stop ordering
    # ------------------------------------------------------------------
    expected_stop = ["completion-verify", "retrospect-mix-check",
                     "completion-signal-gate", "merge-state-claim-gate",
                     "strike-counter"]
    actual_stop: list[str] = []
    for entry in manifest["hooks"]:
        if entry["event"] == "Stop":
            actual_stop.append(entry["name"])
    if actual_stop != expected_stop:
        drifts.append(
            f"STOP ORDERING: manifest Stop order {actual_stop!r} != "
            f"expected {expected_stop!r}"
        )

    # ------------------------------------------------------------------
    # Rule 6 — runtime wrapper byte-identity
    # ------------------------------------------------------------------
    expected_wrappers: dict[str, str] = {}
    for entry in manifest["hooks"]:
        fname = _build._wrapper_filename(entry)
        body = _build._wrapper_body(entry)
        if fname in expected_wrappers and expected_wrappers[fname] != body:
            drifts.append(
                f"WRAPPER BODY CONFLICT {fname}: manifest yields two "
                "different bodies for the same wrapper"
            )
            continue
        expected_wrappers[fname] = body

    # ADR-0002: the dispatch runner wrapper is emitted by emit_wrappers outside
    # the manifest hook loop (it has no manifest entry), so add it here or a
    # stale/missing hooks/_dispatch.sh slips through CI.
    expected_wrappers[_build.DISPATCH_WRAPPER_NAME] = _build.WRAPPER_DISPATCH_TEMPLATE

    for fname, expected_body in expected_wrappers.items():
        wrapper_path = _build.HOOKS_DIR / fname
        if not wrapper_path.exists():
            drifts.append(
                f"WRAPPER MISSING hooks/{fname}: run "
                "./scripts/build-plugin-manifests.py"
            )
            continue
        actual_body = wrapper_path.read_text()
        if actual_body != expected_body:
            drifts.append(
                f"WRAPPER DRIFT hooks/{fname}: regenerate with "
                "./scripts/build-plugin-manifests.py"
            )

    # ------------------------------------------------------------------
    # Spec existence check + hosts shape validation
    # ------------------------------------------------------------------
    for entry in manifest["hooks"]:
        hosts = entry.get("hosts")
        if hosts is not None:
            if not isinstance(hosts, list):
                drifts.append(
                    f"INVALID hosts {entry['name']}: must be a list of strings, "
                    f"got {type(hosts).__name__}"
                )
            elif len(hosts) == 0:
                drifts.append(
                    f"INVALID hosts {entry['name']}: empty list drops the hook "
                    "from every platform — omit the field to mean 'all hosts'"
                )
            elif not all(isinstance(h, str) for h in hosts):
                drifts.append(
                    f"INVALID hosts {entry['name']}: every entry must be a string"
                )
        spec = REPO_ROOT / "hooks" / entry["role"] / entry["name"] / "spec.md"
        if not spec.exists():
            drifts.append(
                f"MISSING SPEC hooks/{entry['role']}/{entry['name']}/spec.md "
                "(hook registered in manifest.json)"
            )

    # ------------------------------------------------------------------
    # Codex adapter symlinks
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Rule 7 — INDEX.md + ARCHITECTURE.md cross-check
    # ------------------------------------------------------------------
    index_md = (REPO_ROOT / "docs" / "hook" / "INDEX.md").read_text()
    arch_md = (REPO_ROOT / "ARCHITECTURE.md").read_text()
    seen_names: set[str] = set()
    for entry in manifest["hooks"]:
        name = entry["name"]
        if name in seen_names:
            continue
        seen_names.add(name)
        if name not in index_md:
            drifts.append(
                f"MISSING INDEX docs/hook/INDEX.md: {name} "
                "(registered in manifest.json but not in INDEX.md)"
            )
        if name not in arch_md:
            drifts.append(
                f"MISSING INDEX ARCHITECTURE.md: {name} "
                "(registered in manifest.json but not in ARCHITECTURE.md)"
            )

    # ------------------------------------------------------------------
    # Rule 8 — Supported hosts cross-check
    # ------------------------------------------------------------------
    manifest_hosts: dict[str, list[str] | None] = {}
    for entry in manifest["hooks"]:
        # First registration wins (multi-event entries share the same hosts)
        if entry["name"] not in manifest_hosts:
            manifest_hosts[entry["name"]] = entry.get("hosts")

    for hook_dir in _hook_dirs():
        spec_file = hook_dir / "spec.md"
        if not spec_file.exists():
            continue
        hook_name = hook_dir.name
        if hook_name not in manifest_hosts:
            continue
        spec_text = spec_file.read_text()
        hosts_value: str | None = None
        for line in spec_text.splitlines()[:10]:
            if line.strip().lower().startswith("supported hosts:"):
                hosts_value = line.split(":", 1)[1].strip()
                break
        if hosts_value is None:
            continue
        json_hosts = manifest_hosts[hook_name]
        if hosts_value.lower() == "all":
            if json_hosts is not None:
                drifts.append(
                    f"FAIL hosts mismatch {hook_name}: spec='all' "
                    f"manifest={json_hosts!r} (remove hosts to mean all)"
                )
        else:
            raw_tokens = hosts_value.split(",")
            spec_set = {
                t.split("(")[0].strip()
                for t in raw_tokens
                if t.split("(")[0].strip()
            }
            json_set = set(json_hosts) if json_hosts is not None else set()
            if spec_set != json_set:
                drifts.append(
                    f"FAIL hosts mismatch {hook_name}: spec={sorted(spec_set)!r} "
                    f"manifest={sorted(json_set)!r}"
                )

    # ------------------------------------------------------------------
    # Rule 9 — Version consistency
    # ------------------------------------------------------------------
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
    if len(set(seen.values())) > 1:
        drifts.append(
            "VERSION DRIFT across artifacts: "
            + ", ".join(f"{k}={v}" for k, v in seen.items())
        )

    # ------------------------------------------------------------------
    # Rule 11 — Skill surface freeze (#465)
    # ------------------------------------------------------------------
    on_disk_skills = {d.name for d in _skill_dirs()}
    unexpected = on_disk_skills - EXPECTED_SKILLS
    removed = EXPECTED_SKILLS - on_disk_skills
    if unexpected:
        drifts.append(
            f"UNEXPECTED SKILL(S): {sorted(unexpected)!r} — present on disk "
            "but not declared in EXPECTED_SKILLS. If intentional, update "
            "EXPECTED_SKILLS in scripts/constants.py."
        )
    if removed:
        drifts.append(
            f"REMOVED SKILL(S): {sorted(removed)!r} — declared in "
            "EXPECTED_SKILLS but missing on disk. If intentional, update "
            "EXPECTED_SKILLS in scripts/constants.py."
        )

    # ------------------------------------------------------------------
    # Rule 12 — Doc skill-count invariant (#498)
    #
    # AGENTS.md carries an explicit "## Skills (N)" header whose count must
    # equal len(EXPECTED_SKILLS).  Both AGENTS.md and README.md embed skill
    # names inside table cells as `backtick` tokens; every EXPECTED_SKILLS
    # member must appear at least once in each document.
    #
    # Parsing is intentionally coarse — we match the pattern
    # `skill-name` (backtick-delimited) so the check is robust to table
    # reformatting while still catching silent drift.
    # ------------------------------------------------------------------
    import re as _re  # local import — avoids polluting module scope

    agents_md_path = REPO_ROOT / "AGENTS.md"
    readme_md_path = REPO_ROOT / "README.md"

    agents_text = agents_md_path.read_text()
    readme_text = readme_md_path.read_text()

    # Rule 12a — AGENTS.md "## Skills (N)" count header must match
    count_match = _re.search(r"^##\s+Skills\s+\((\d+)\)", agents_text, _re.MULTILINE)
    if count_match is None:
        drifts.append(
            "DOC SKILL COUNT MISSING AGENTS.md: expected '## Skills (N)' header — "
            "add it to keep the count in sync with EXPECTED_SKILLS"
        )
    else:
        declared_count = int(count_match.group(1))
        expected_count = len(EXPECTED_SKILLS)
        if declared_count != expected_count:
            drifts.append(
                f"DOC SKILL COUNT AGENTS.md: header says {declared_count} but "
                f"EXPECTED_SKILLS has {expected_count} — update the header"
            )

    # Rule 12b — every EXPECTED_SKILLS member appears in AGENTS.md
    agents_backtick_skills = set(_re.findall(r"`([^`]+)`", agents_text))
    missing_in_agents = EXPECTED_SKILLS - agents_backtick_skills
    if missing_in_agents:
        drifts.append(
            f"DOC SKILL LIST AGENTS.md: {sorted(missing_in_agents)!r} declared in "
            "EXPECTED_SKILLS but not found as `backtick` tokens — add them to "
            "the skill table"
        )

    # Rule 12c — every EXPECTED_SKILLS member appears as the first column of
    # a README.md skill table row.  We match `| \`skill-name\` |` so that a
    # skill name mentioned only in a description cell (cross-reference noise)
    # does not satisfy the check.
    readme_table_skills = set(
        _re.findall(r"^\|\s*`([^`]+)`\s*\|", readme_text, _re.MULTILINE)
    )
    missing_in_readme = EXPECTED_SKILLS - readme_table_skills
    if missing_in_readme:
        drifts.append(
            f"DOC SKILL LIST README.md: {sorted(missing_in_readme)!r} declared in "
            "EXPECTED_SKILLS but not found as a first-column `backtick` token in a "
            "table row — add them to the skill table"
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
