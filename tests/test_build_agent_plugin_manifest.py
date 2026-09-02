"""Tests for the Agent Plugins portable manifest output (#1219).

The manifest goes to the repo root, which is the plugin root for Claude,
Cursor, and the spec-only clients. Codex's plugin root is plugins/praxis/,
so this output does not reach Codex — that is a known, tracked gap, pinned
below by test_output_path_is_the_repo_root so a later edit cannot quietly
claim Codex coverage.

Two properties decide whether the manifest loads at all, and neither is
visible to the byte-identity drift check — a wrong manifest renders
reproducibly and diffs clean:

  * `$schema` pinned to 1.0.0, the only published spec version. Clients find
    this file by path and read `$schema` only to pick local validation rules,
    so an unsupported version is fatal on its own — "A missing or unsupported
    `$schema` rejects the plugin" — with no fallback defined.
  * no host-specific component keys. This one is repo policy, not spec: the
    spec says to "report and ignore each unknown top-level field, then
    continue if the manifest is otherwise valid", which is why such a key is
    worse than a hard error — it loads clean and does nothing.

Spec quotes: agent-plugins.org/client-implementers/loading-and-discovery
"""

from __future__ import annotations

import importlib.util
import json
import string
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

# Spec-defined manifest members (§5.3 required, §5.4 metadata). A conformant
# client reports and ignores anything outside this set, so an extra member
# does not fail the load — it just never does anything.
SPEC_MEMBERS = {
    "$schema", "name", "version", "description", "author",
    "homepage", "repository", "license", "keywords", "extensions",
}
HOST_SPECIFIC_KEYS = ("skills", "hooks", "mcpServers", "apps", "interface")


def _rendered() -> dict:
    return _build.render_agent_plugin(_build.load_base())


def test_schema_is_pinned_to_the_only_published_version():
    assert _rendered()["$schema"] == (
        "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    )


def test_required_fields_present():
    manifest = _rendered()
    for field in ("$schema", "name"):
        assert manifest.get(field), f"{field} is required by spec §5.3"


def test_name_satisfies_the_spec_charset():
    """Mirrors codex-rs is_valid_agent_plugin_name: lowercase ascii, digits,
    '.' and '-'; no '--' or '..'; first and last byte alphanumeric.

    Membership is spelled out rather than delegated to str.islower/isdigit/
    isalnum, which are Unicode-wide: 'é'.islower(), '٣'.isdigit() and
    'ｎ'.isalnum() are all True, so those predicates admit names the Rust
    validator rejects.
    """
    alnum = set(string.ascii_lowercase) | set(string.digits)
    name = _rendered()["name"]
    assert 0 < len(name) <= 64
    assert "--" not in name and ".." not in name
    assert all(c in alnum or c in ".-" for c in name)
    assert name[0] in alnum and name[-1] in alnum


def test_carries_no_host_specific_component_keys():
    manifest = _rendered()
    for key in HOST_SPECIFIC_KEYS:
        assert key not in manifest, (
            f"{key!r} is not a spec member, so conformant clients ignore it "
            "and the path it names is never read. Host paths belong in the "
            "host's own manifest."
        )


def test_every_member_is_spec_defined():
    unexpected = set(_rendered()) - SPEC_MEMBERS
    assert not unexpected, f"non-spec members are ignored, not read: {unexpected}"


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


def test_output_path_is_the_repo_root_and_codex_is_not_covered():
    """Pins the known scope gap so it cannot be silently reinterpreted.

    A host reads the manifest at `plugin.json` in ITS plugin root. Codex's is
    `plugins/praxis/` (`.agents/plugins/marketplace.json` source), not the repo
    root, so the single repo-root output reaches Claude, Cursor, and the
    spec-only clients — never Codex. Covering Codex means a second output at
    plugins/praxis/plugin.json; adding it should update this test deliberately,
    not trip over it.
    """
    platform = json.loads(
        (REPO_ROOT / "manifests" / "platforms" / "agent-plugins.json").read_text()
    )
    paths = [output["path"] for output in platform["outputs"]]
    assert paths == ["plugin.json"], "repo-root output only"

    codex_marketplace = json.loads(
        (REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text()
    )
    codex_root = codex_marketplace["plugins"][0]["source"].rstrip("/")
    assert codex_root != ".", "Codex plugin root is nested, not the repo root"
    assert not (REPO_ROOT / codex_root / "plugin.json").exists(), (
        "a manifest appeared in the Codex plugin root — that is Codex coverage, "
        "so update this test and ARCHITECTURE.md's scope note together"
    )
