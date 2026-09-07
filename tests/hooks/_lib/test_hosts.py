"""Tests for the shared host resolver (`hooks/_lib/_hosts.py`, issue #1245).

Three hooks print text that names a sibling gate, and each has to know which
gates the running platform actually installs. This module is the one place
that answers it, so its two failure directions are pinned here:

  - a *narrower* answer than the truth drops a row the host does install,
    which hides the next hard block — the worse of the two failures, so every
    unusable input has to degrade to None ("print everything") rather than to
    an empty set;
  - a *wider* answer reprints the defect #1154 and #1245 exist to remove.

Run: python3 -m pytest tests/hooks/_lib/test_hosts.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_DIR = REPO_ROOT / "hooks" / "_lib"

_spec = importlib.util.spec_from_file_location("_hosts", LIB_DIR / "_hosts.py")
assert _spec is not None and _spec.loader is not None
hosts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hosts)  # type: ignore[union-attr]

MANIFEST = json.loads((REPO_ROOT / "hooks" / "manifest.json").read_text(encoding="utf-8"))


def _declared_hosts() -> set[str]:
    """Host ids the packaging manifests actually emit a hooks.json for."""
    ids = set()
    for path in (REPO_ROOT / "manifests" / "platforms").glob("*.json"):
        platform = json.loads(path.read_text(encoding="utf-8"))
        if any(o.get("kind") == "hooks" for o in platform.get("outputs", [])):
            ids.add(platform["host_id"])
    return ids


def test_schema_enum_covers_every_hook_installing_platform() -> None:
    """A platform missing from the enum is normalized to "unknown", so its
    hooks would silently print the unfiltered text forever."""
    assert _declared_hosts() <= set(hosts.schema_hosts())


def _entry_ships_on(entry: dict, host: str) -> bool:
    """The manifest's own rule: absent `hosts` means every host."""
    whitelist = entry.get("hosts")
    return whitelist is None or host in whitelist


def _registrations_by_name() -> dict[str, list[dict]]:
    by_name: dict[str, list[dict]] = {}
    for entry in MANIFEST["hooks"]:
        by_name.setdefault(entry["name"], []).append(entry)
    return by_name


def test_installed_names_apply_the_manifest_whitelist() -> None:
    """A NAME is installed on a host iff at least one of its registrations
    ships there. `hosts` is per registration, and since #1337 a name can mix
    them — `second-failure-advisory` ships `PostToolUse` everywhere and
    `PostToolUseFailure` to claude only, so the name IS installed on codex.
    A per-entry reading would call that a leak; the name-level one is what
    the checklist consumers need (the gate's text is present on that host)."""
    for host in sorted(_declared_hosts()):
        installed = hosts.installed_hook_names(host)
        assert installed is not None
        for name, entries in _registrations_by_name().items():
            expected = any(_entry_ships_on(e, host) for e in entries)
            assert (name in installed) is expected, (
                f"{name} on {host}: hosts={[e.get('hosts') for e in entries]}"
            )


def test_a_mixed_host_name_is_installed_wherever_any_leg_ships() -> None:
    """Pin the mixed case the aggregation above exists for: a name with one
    all-hosts registration and one claude-only registration is installed on
    every declared host, not just claude. Without such a name in the manifest
    the aggregation is untested, so its absence fails here too."""
    mixed = [
        name
        for name, entries in _registrations_by_name().items()
        if any(e.get("hosts") is None for e in entries)
        and any(e.get("hosts") == ["claude"] for e in entries)
    ]
    assert mixed, "no mixed-host hook left — this control measures nothing (#1337)"
    for host in sorted(_declared_hosts()):
        installed = hosts.installed_hook_names(host)
        assert installed is not None
        assert set(mixed) <= installed, f"{host}: mixed-host names missing"


def test_a_whitelisted_hook_is_absent_from_a_host_outside_its_list() -> None:
    """Positive control for the assertion above: the whitelist has to actually
    exclude something, or an `installed` set holding every name would pass.
    Only names whose EVERY registration is claude-only qualify — a name with
    an all-hosts leg is installed elsewhere by that leg (see the mixed-host
    test)."""
    claude_only = [
        name
        for name, entries in _registrations_by_name().items()
        if all(e.get("hosts") == ["claude"] for e in entries)
    ]
    assert claude_only, "no claude-only hook left — this control measures nothing"
    for other in sorted(_declared_hosts() - {"claude"}):
        installed = hosts.installed_hook_names(other)
        assert installed is not None
        assert not (set(claude_only) & installed)


def test_unfilterable_hosts_degrade_to_none() -> None:
    """None means "print everything". Every input that cannot name a real
    platform has to land here rather than on an empty set."""
    for host in (None, "", "not-a-host", "CLAUDE"):
        assert hosts.installed_hook_names(host) is None


def test_runtime_host_reads_argv_only_under_the_dispatcher() -> None:
    """argv is the sole carrier: the generated hooks.json invokes
    `_dispatch.sh <event> <matcher> <host>`, and a group member runs in-process
    inside it. A standalone run has no argv[3] and must yield None."""
    original = sys.argv
    try:
        sys.argv = ["/x/hooks/_lib/_dispatch.py", "PreToolUse", "Bash", "codex"]
        assert hosts.runtime_host() == "codex"
        sys.argv = ["/x/hooks/_lib/_dispatch.py", "PreToolUse", "Bash"]
        assert hosts.runtime_host() is None
        sys.argv = ["/x/hooks/preflight-gate/some-hook/impl.py", "a", "b", "codex"]
        assert hosts.runtime_host() is None
    finally:
        sys.argv = original
