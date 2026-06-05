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
     target parity check). Dispatch-only members carry NO wrapper and are
     asserted absent from disk (Rule 6b, ADR-0002 Phase 4 / #618). Opt-in
     wrappers (OPT_IN_HOOKS, not in the manifest) are byte-identity checked
     too (Rule 6c, #605).
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
  13. Each manifest `dispatch_groups` (event, matcher) collapses to exactly
     one dispatcher node per platform hooks.json (no member silently left as
     its own node, no second dispatcher node), and the runtime resolver
     `_dispatch.group_members` resolves the same member set the build
     collapsed (ADR-0002, #617 — ties build path and runtime path together).

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

# ADR-0002 (#617): the runtime dispatch resolver. Rule 13 cross-checks that the
# build collapse (filter_hooks_for_host → committed hooks.json) and the runtime
# resolution (group_members) agree on each dispatch group's members.
_disp_spec = importlib.util.spec_from_file_location(
    "praxis_dispatch", REPO_ROOT / "hooks" / "_lib" / "_dispatch.py"
)
assert _disp_spec and _disp_spec.loader
_dispatch = importlib.util.module_from_spec(_disp_spec)
_disp_spec.loader.exec_module(_dispatch)

from constants import EXPECTED_SKILLS, OPT_IN_HOOKS, VALID_ROLES  # noqa: E402


def dispatch_node_drifts(
    hooks_json: dict,
    event: str,
    matcher: str | None,
    host_id: str,
    expected_members: set[str],
    dispatch_wrapper_name: str,
) -> list[str]:
    """Drift strings for one (event, matcher) group's node shape in a hooks.json.

    Pure function (no I/O) so the node-shape half of Rule 13 is unit-testable in
    isolation from the runtime resolver. `expected_members` is the host-kept
    member set: non-empty → the group must hold exactly ONE node and it must be
    the dispatcher wrapper carrying `event matcher host_id` args; empty (the host
    filtered every member) → the group must be absent (zero nodes).
    """
    groups = [
        g
        for g in hooks_json.get("hooks", {}).get(event, [])
        if g.get("matcher") == matcher
    ]
    nodes = [n for g in groups for n in g.get("hooks", [])]
    dispatch_nodes = [
        n for n in nodes if dispatch_wrapper_name in n.get("command", "")
    ]
    member_nodes = [
        n for n in nodes if dispatch_wrapper_name not in n.get("command", "")
    ]
    out: list[str] = []
    want = 1 if expected_members else 0
    if len(dispatch_nodes) != want:
        out.append(
            f"DISPATCH NODE COUNT {event}/{matcher} host={host_id}: expected "
            f"{want} dispatcher node(s), found {len(dispatch_nodes)}"
        )
    if member_nodes:
        out.append(
            f"DISPATCH MEMBER LEAK {event}/{matcher} host={host_id}: "
            f"{len(member_nodes)} non-dispatcher node(s) in a collapsed group: "
            f"{[n.get('command', '') for n in member_nodes]}"
        )
    for n in dispatch_nodes:
        cmd = n.get("command", "")
        if f"{event} {matcher} {host_id}" not in cmd:
            out.append(
                f"DISPATCH ARGS {event}/{matcher} host={host_id}: dispatcher "
                f"command {cmd!r} missing '{event} {matcher} {host_id}' args"
            )
    return out


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
    #
    # ADR-0002 Phase 4 (#618): a member whose every registration is collapsed
    # into a dispatch group has no wrapper (invoked via _dispatch.sh). Those
    # filenames are excluded from the expected set AND asserted absent from disk
    # below, so a re-emitted orphan wrapper fails CI.
    # ------------------------------------------------------------------
    dispatch_only = _build.dispatch_only_wrappers(manifest)
    expected_wrappers: dict[str, str] = {}
    for entry in manifest["hooks"]:
        fname = _build._wrapper_filename(entry)
        if fname in dispatch_only:
            continue
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

    # Rule 6c — opt-in hooks (#605): not in the manifest, but emit_wrappers still
    # generates hooks/<name>.sh for their documented invocation path
    # (OPT_IN_HOOKS). Mirror that emit so the existence + byte-identity loop below
    # covers the opt-in wrapper class too — otherwise a stale/missing opt-in
    # wrapper slips through CI (Rule 6 derived expected_wrappers only from manifest
    # entries). A manifest entry of the same name wins (matches emit_wrappers).
    for opt_in_name, opt_in_role in _build.OPT_IN_HOOKS.items():
        fname = f"{opt_in_name}.sh"
        if fname in expected_wrappers:
            continue
        expected_wrappers[fname] = _build.WRAPPER_PY_TEMPLATE.format(
            role=opt_in_role, name=opt_in_name, baked_args=""
        )

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

    # Rule 6b — dispatch-only members must NOT carry a wrapper on disk. The
    # dispatcher imports their impl.py directly; a lingering hooks/<name>.sh is
    # dead weight and re-introduces the Approach-A drift #618 removed.
    for fname in sorted(dispatch_only):
        if (_build.HOOKS_DIR / fname).exists():
            drifts.append(
                f"ORPHAN WRAPPER hooks/{fname}: dispatch-only member must not "
                "carry a wrapper — remove it (invoked via _dispatch.sh)"
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

    # ------------------------------------------------------------------
    # Rule 13 — dispatch-group ↔ build/runtime consistency (ADR-0002, #617)
    #
    # Each (event, matcher) in manifest.dispatch_groups is collapsed by the
    # build (filter_hooks_for_host) into ONE dispatcher node, and resolved at
    # runtime by _dispatch.group_members. Those are two independent readers of
    # the manifest; this rule ties them — and the committed hooks.json artifact —
    # together so a future manifest/schema edit cannot silently break the
    # collapse (drop a member, leave a stray per-member node, emit two dispatcher
    # nodes, or bake the wrong host into the dispatcher command). For every
    # platform that emits a hooks.json, per host:
    #   (a) the (event, matcher) group holds exactly ONE node and it is the
    #       dispatcher wrapper carrying `event matcher host` args when ≥1 member
    #       survives the host filter; ZERO nodes when the host filters all
    #       members — verified by dispatch_node_drifts() above;
    #   (b) group_members(event, matcher, host) equals the manifest-derived
    #       member set for that host, with no duplicates, and every resolved
    #       impl.py exists on disk.
    # ------------------------------------------------------------------
    def _manifest_members_for(event: str, matcher: str | None, host: str) -> set[str]:
        names: set[str] = set()
        for hook in manifest["hooks"]:
            hosts = hook.get("hosts")
            if hosts is not None and host not in hosts:
                continue
            entries = hook.get("entries") or [
                {"event": hook.get("event"), "matcher": hook.get("matcher")}
            ]
            if any(
                e.get("event") == event and e.get("matcher") == matcher
                for e in entries
            ):
                names.add(hook["name"])
        return names

    hooks_outputs: list[tuple[str, Path]] = []
    for platform_file in sorted(_build.PLATFORMS_DIR.glob("*.json")):
        platform = json.loads(platform_file.read_text())
        host_id = platform.get("host_id", platform["platform"])
        for output in platform["outputs"]:
            if output["kind"] == "hooks":
                hooks_outputs.append((host_id, REPO_ROOT / output["path"]))

    for event, matcher in sorted(dispatch_groups, key=lambda em: (em[0], em[1] or "")):
        for host_id, hooks_path in hooks_outputs:
            expected = _manifest_members_for(event, matcher, host_id)

            # (b) runtime resolution must match the manifest, with no dup, and
            #     every resolved impl on disk.
            resolved = _dispatch.group_members(event, matcher, host_id)
            resolved_names = [name for _role, name, _impl in resolved]
            if len(resolved_names) != len(set(resolved_names)):
                drifts.append(
                    f"DISPATCH DUP {event}/{matcher} host={host_id}: "
                    f"group_members resolves a hook more than once "
                    f"({sorted(resolved_names)})"
                )
            if set(resolved_names) != expected:
                drifts.append(
                    f"DISPATCH MEMBER DRIFT {event}/{matcher} host={host_id}: "
                    f"group_members={sorted(set(resolved_names))} != "
                    f"manifest={sorted(expected)}"
                )
            for _role, name, impl in resolved:
                if not impl.exists():
                    drifts.append(
                        f"DISPATCH IMPL MISSING {event}/{matcher} host={host_id}: "
                        f"{name} -> {impl} does not exist on disk"
                    )

            # (a) committed hooks.json node shape — exactly one dispatcher node,
            #     no leaked member node, correct host args.
            if not hooks_path.exists():
                drifts.append(
                    f"DISPATCH HOOKS MISSING {hooks_path}: cannot verify "
                    f"{event}/{matcher} collapse"
                )
                continue
            hooks_json = json.loads(hooks_path.read_text())
            drifts.extend(
                dispatch_node_drifts(
                    hooks_json,
                    event,
                    matcher,
                    host_id,
                    expected,
                    _build.DISPATCH_WRAPPER_NAME,
                )
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
