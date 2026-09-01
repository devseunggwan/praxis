"""Tests for the Agent Plugins portable manifest output (#1219).

Focus: the root `plugin.json` that Codex selects ahead of
`.codex-plugin/plugin.json`. Two properties decide whether that manifest
loads at all, and neither is visible to the byte-identity drift check —
a wrong manifest renders reproducibly and diffs clean:

  * `$schema` pinned to 1.0.0. Codex's SUPPORTED_AGENT_PLUGIN_SCHEMA_URIS
    holds that URI alone, and find_plugin_manifest_path() still selects any
    `https://agent-plugins.org/schemas/` URI over the `.codex-plugin/`
    fallback — so a newer pin makes the plugin vanish rather than degrade.
  * no host-specific component keys. The spec's manifest schema is closed,
    so one `skills` or `hooks` key invalidates the document; Codex takes
    `paths.hooks` from the overlay file instead.
"""

from __future__ import annotations

import importlib.util
import json
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

# Spec-defined manifest members (§5.3 required, §5.4 metadata). Anything
# outside this set is rejected by the closed schema.
SPEC_MEMBERS = {
    "$schema", "name", "version", "description", "author",
    "homepage", "repository", "license", "keywords", "extensions",
}
HOST_SPECIFIC_KEYS = ("skills", "hooks", "mcpServers", "apps", "interface")


def _rendered() -> dict:
    return _build.render_agent_plugin(_build.load_base())


def test_schema_is_pinned_to_the_one_version_codex_supports():
    assert _rendered()["$schema"] == (
        "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    )


def test_required_fields_present():
    manifest = _rendered()
    for field in ("$schema", "name"):
        assert manifest.get(field), f"{field} is required by spec §5.3"


def test_name_satisfies_the_spec_charset():
    """Mirrors codex-rs is_valid_agent_plugin_name: lowercase ascii, digits,
    '.' and '-'; no '--' or '..'; first and last byte alphanumeric."""
    name = _rendered()["name"]
    assert 0 < len(name) <= 64
    assert "--" not in name and ".." not in name
    assert all(c.islower() or c.isdigit() or c in ".-" for c in name)
    assert name[0].isalnum() and name[-1].isalnum()


def test_carries_no_host_specific_component_keys():
    manifest = _rendered()
    for key in HOST_SPECIFIC_KEYS:
        assert key not in manifest, (
            f"{key!r} is not a spec member; the closed schema rejects the "
            "whole manifest. Host paths belong in the host's own manifest."
        )


def test_every_member_is_spec_defined():
    unexpected = set(_rendered()) - SPEC_MEMBERS
    assert not unexpected, f"non-spec members would invalidate it: {unexpected}"


def test_version_tracks_the_VERSION_file():
    assert _rendered()["version"] == (REPO_ROOT / "VERSION").read_text().strip()


def test_committed_manifest_matches_a_fresh_render():
    """The generated artifact is committed, so a hand edit must be caught."""
    committed = json.loads((REPO_ROOT / "plugin.json").read_text())
    assert committed == _rendered()


def test_release_please_tracks_the_manifest_version():
    """Without the extra-files entry the manifest ships a stale version on
    the first release after merge, and no other gate notices."""
    config = json.loads((REPO_ROOT / "release-please-config.json").read_text())
    paths = {
        entry["path"]
        for entry in config["packages"]["."]["extra-files"]
        if isinstance(entry, dict)
    }
    assert "plugin.json" in paths


def test_platform_declares_the_output():
    platform = json.loads(
        (REPO_ROOT / "manifests" / "platforms" / "agent-plugins.json").read_text()
    )
    kinds = {output["kind"]: output["path"] for output in platform["outputs"]}
    assert kinds == {"agent-plugin": "plugin.json"}
