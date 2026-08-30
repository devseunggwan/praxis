"""Manifest schema gate (#1173): hooks/manifest.json ↔ hooks/manifest.schema.json.

Three surfaces, tested independently:

  - ``schema_validation_errors()`` / ``manifest_schema_drifts()`` — the
    stdlib walker driven by the schema file, exercised with mutated copies
    of the real manifest (typo'd optional key, wrong type, bool-vs-integer,
    unknown top-level key, top-level non-object) — every case must come back
    as a diagnostic string naming file+entry+key, never as a raw traceback;
  - ``assert_schema_supported()`` — the fail-loud guard on the SCHEMA
    itself: an unsupported keyword or ``type`` value must raise ValueError
    up front instead of crashing mid-walk or being silently unenforced;
  - ``load_platform()`` host_id membership in the schema's closed hosts
    enum — a typo'd host_id must fail with a named diagnostic instead of
    silently dropping every hosts-restricted hook from that platform.

The gate itself is shared: build-plugin-manifests.py refuses to render from
a manifest with schema drifts, and check-plugin-manifests.py runs the same
``manifest_schema_drifts()`` before its numbered rules.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "build_plugin_manifests", REPO_ROOT / "scripts" / "build-plugin-manifests.py"
)
assert _spec and _spec.loader
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


@pytest.fixture()
def manifest() -> dict:
    return copy.deepcopy(build.load_manifest())


# ---------------------------------------------------------------------------
# manifest_schema_drifts — instance-side diagnostics
# ---------------------------------------------------------------------------

def test_clean_manifest_has_no_drifts(manifest):
    assert build.manifest_schema_drifts(manifest) == []


def test_top_level_non_object_is_a_diagnostic_not_a_crash():
    # Regression (review round 1): a top-level array used to raise
    # AttributeError while building the entry-name label map, before
    # validation ran — the exact naked-traceback class the gate exists
    # to eliminate.
    drifts = build.manifest_schema_drifts([])
    assert len(drifts) == 1
    assert "$: expected object, got list" in drifts[0]


def test_typoed_optional_key_names_file_entry_and_key(manifest):
    entry = manifest["hooks"][0]
    entry["wrapper_sufix"] = "-pre"
    drifts = build.manifest_schema_drifts(manifest)
    assert len(drifts) == 1
    assert "SCHEMA hooks/manifest.json" in drifts[0]
    assert "$.hooks[0]: unknown key 'wrapper_sufix'" in drifts[0]
    # Entry-name labeling: the hook's `name` is appended for locatability.
    assert f"(entry {entry['name']!r})" in drifts[0]


def test_unknown_top_level_key_fails(manifest):
    manifest["dispach_groups"] = manifest.pop("dispatch_groups")
    drifts = build.manifest_schema_drifts(manifest)
    assert len(drifts) == 1
    assert "$: unknown key 'dispach_groups'" in drifts[0]


def test_wrong_timeout_type_fails(manifest):
    manifest["hooks"][0]["timeout"] = "5"
    drifts = build.manifest_schema_drifts(manifest)
    assert len(drifts) == 1
    assert "$.hooks[0].timeout: expected integer, got str" in drifts[0]


def test_bool_timeout_is_not_an_integer(manifest):
    # bool is a subclass of int in Python; JSON Schema keeps them distinct.
    manifest["hooks"][0]["timeout"] = True
    drifts = build.manifest_schema_drifts(manifest)
    assert len(drifts) == 1
    assert "$.hooks[0].timeout: expected integer, got bool" in drifts[0]


def test_missing_required_key_fails(manifest):
    del manifest["hooks"][0]["role"]
    drifts = build.manifest_schema_drifts(manifest)
    assert len(drifts) == 1
    assert "$.hooks[0]: missing required key 'role'" in drifts[0]


def test_enum_violation_fails(manifest):
    manifest["hooks"][0]["role"] = "prefilght-gate"
    drifts = build.manifest_schema_drifts(manifest)
    assert len(drifts) == 1
    assert "$.hooks[0].role: 'prefilght-gate' is not one of" in drifts[0]


def test_retired_entries_key_fails(manifest):
    # ADR-0001 §2.5's nested `entries` form never gained a manifest use and
    # is retired (#1169); the schema models only the flat form.
    manifest["hooks"][0]["entries"] = [{"event": "Stop"}]
    drifts = build.manifest_schema_drifts(manifest)
    assert len(drifts) == 1
    assert "unknown key 'entries'" in drifts[0]


# ---------------------------------------------------------------------------
# assert_schema_supported — fail-loud guard on the schema itself
# ---------------------------------------------------------------------------

def test_real_schema_is_within_the_supported_subset():
    build.assert_schema_supported(build.load_schema())


def test_unsupported_schema_keyword_raises():
    with pytest.raises(ValueError, match=r"unsupported schema keyword\(s\) \['pattern'\]"):
        build.assert_schema_supported({"type": "string", "pattern": "^x"})


def test_unsupported_schema_keyword_in_nested_node_raises():
    schema = {
        "type": "object",
        "properties": {"x": {"type": "array", "items": {"uniqueItems": True}}},
    }
    with pytest.raises(ValueError, match="#/properties/x/items"):
        build.assert_schema_supported(schema)


def test_unsupported_type_value_raises():
    with pytest.raises(ValueError, match="unsupported type 'number'"):
        build.assert_schema_supported({"type": "number"})


def test_list_form_type_raises():
    with pytest.raises(ValueError, match="unsupported type"):
        build.assert_schema_supported({"type": ["string", "integer"]})


def test_subschema_additional_properties_raises():
    with pytest.raises(ValueError, match="additionalProperties must be"):
        build.assert_schema_supported(
            {"type": "object", "additionalProperties": {"type": "string"}}
        )


# ---------------------------------------------------------------------------
# load_platform — host_id membership in the schema's closed hosts enum
# ---------------------------------------------------------------------------

def _write_platform(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "claude.json"
    p.write_text(json.dumps(data))
    return p


def test_load_platform_rejects_typoed_host_id(tmp_path):
    p = _write_platform(
        tmp_path, {"platform": "claude", "host_id": "Claude", "outputs": []}
    )
    with pytest.raises(ValueError, match=r"claude\.json: host_id 'Claude' is not one of"):
        build.load_platform(p)


def test_load_platform_accepts_every_enum_host(tmp_path):
    for host in build.manifest_hosts_enum():
        p = _write_platform(
            tmp_path, {"platform": host, "host_id": host, "outputs": []}
        )
        assert build.load_platform(p)["host_id"] == host


def test_load_platform_missing_outputs_names_file_and_key(tmp_path):
    p = _write_platform(tmp_path, {"platform": "claude", "outpts": []})
    with pytest.raises(ValueError, match="missing or non-array key 'outputs'"):
        build.load_platform(p)


def test_schema_hosts_enum_matches_platform_files():
    platform_hosts = set()
    for f in sorted(build.PLATFORMS_DIR.glob("*.json")):
        p = build.load_platform(f)
        platform_hosts.add(p.get("host_id", p["platform"]))
    assert set(build.manifest_hosts_enum()) == platform_hosts
