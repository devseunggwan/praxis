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
import io
import json
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "build_plugin_manifests", REPO_ROOT / "scripts" / "build-plugin-manifests.py"
)
assert _spec and _spec.loader
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

_check_spec = importlib.util.spec_from_file_location(
    "check_plugin_manifests", REPO_ROOT / "scripts" / "check-plugin-manifests.py"
)
assert _check_spec and _check_spec.loader
check = importlib.util.module_from_spec(_check_spec)
_check_spec.loader.exec_module(check)


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


def test_integral_float_timeout_is_accepted(manifest):
    # Review round 2, Codex finding 3: draft 2020-12 "integer" accepts any
    # JSON number with zero fractional part; json.loads("30.0") decodes to
    # a Python float, and the walker used to reject it as manifest drift.
    manifest["hooks"][0]["timeout"] = 30.0
    assert build.manifest_schema_drifts(manifest) == []


def test_fractional_float_timeout_is_still_rejected(manifest):
    manifest["hooks"][0]["timeout"] = 30.5
    drifts = build.manifest_schema_drifts(manifest)
    assert len(drifts) == 1
    assert "$.hooks[0].timeout: expected integer, got float" in drifts[0]


def test_integral_float_below_minimum_still_fails(manifest):
    # The minimum check must still fire for a float that took the widened
    # integer branch — widening acceptance must not widen away validation.
    manifest["hooks"][0]["timeout"] = 0.0
    drifts = build.manifest_schema_drifts(manifest)
    assert len(drifts) == 1
    assert "$.hooks[0].timeout: 0.0 is below minimum 1" in drifts[0]


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


def test_explicit_null_type_raises():
    # Review round 2, Codex finding 2: schema.get("type") returned None for
    # BOTH an absent key and an explicit `"type": null`, so a schema node
    # carrying a literal JSON null used to slip past as "no type
    # constraint" instead of failing loud.
    with pytest.raises(ValueError, match="unsupported type None"):
        build.assert_schema_supported({"type": None})


def test_explicit_null_type_is_not_skipped_by_the_instance_walker():
    # Defense in depth on the instance-side walker too, even though
    # assert_schema_supported() already rejects this schema before
    # schema_validation_errors() ever sees it.
    with pytest.raises(KeyError):
        build.schema_validation_errors({"anything": 1}, {"type": None})


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


def test_load_platform_rejects_empty_output_path(tmp_path):
    # Review round 2, Codex finding 4: "" is a str, so it used to pass the
    # isinstance check; REPO_ROOT / "" resolves to the repo root and crashes
    # a caller with IsADirectoryError instead of a named diagnostic.
    p = _write_platform(
        tmp_path,
        {
            "platform": "claude",
            "host_id": "claude",
            "outputs": [{"kind": "plugin", "path": ""}],
        },
    )
    with pytest.raises(ValueError, match=r"outputs\[0\] key 'path' must not be empty"):
        build.load_platform(p)


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


# ---------------------------------------------------------------------------
# manifest_schema_drifts — dispatch-group member semantic gate (review
# round 2, Codex finding #1). A dispatch-group member's (event, matcher)
# match against `dispatch_groups` is structural, not a schema keyword, so
# the JSON-Schema subset can never express a cross-array rule on a member —
# the second pass in manifest_schema_drifts() is tested directly here. Since
# issue #1281 neither "args" nor "body" is rejected there (see the tests).
# ---------------------------------------------------------------------------

def _first_dispatch_member_name(manifest: dict) -> str:
    pairs = {
        (g["event"], g.get("matcher")) for g in manifest["dispatch_groups"]
    }
    for hook in manifest["hooks"]:
        if (hook.get("event"), hook.get("matcher")) in pairs:
            return hook["name"]
    raise AssertionError("fixture manifest has no dispatch-group member")


def test_dispatch_member_with_args_is_allowed(manifest):
    # Rejecting `args` here stopped the build (exit 1) before the two halves
    # that actually handle such a member could act: the build keeps it as a
    # standalone node, the runtime excludes it from the group. Both were
    # unreachable while this gate fired (issue #1199 review).
    name = _first_dispatch_member_name(manifest)
    entry = next(h for h in manifest["hooks"] if h["name"] == name)
    entry["args"] = ["--foo"]
    drifts = build.manifest_schema_drifts(manifest)
    assert not any("declares 'args'" in d for d in drifts), drifts
    # Control: the sentinel-matcher rejection on the same manifest still
    # fires, so the semantic pass as a whole is alive and this is not an
    # empty assertion (the `body` rejection that used to serve as the control
    # was removed in issue #1281 — see test_dispatch_member_with_body_is_allowed).
    manifest["dispatch_groups"].append(
        {"event": "Stop", "matcher": build.DISPATCH_NO_MATCHER_ARG}
    )
    assert any(
        "reserved as the matcher-less argv sentinel" in d
        for d in build.manifest_schema_drifts(manifest)
    )


def test_dispatch_group_may_omit_its_matcher(manifest):
    # Stop / SessionStart / UserPromptSubmit carry no matcher. The key is
    # OMITTED rather than null: the validator subset has no null type, and
    # .get("matcher") answers None for an absent key either way.
    # The real manifest already declares the Stop group (issue #1281); a
    # second identical entry is still a schema-legal, matcher-less group.
    manifest["dispatch_groups"].append({"event": "Stop"})
    assert not any(
        "dispatch_groups" in d and "matcher" in d
        for d in build.manifest_schema_drifts(manifest)
    )
    # Control: an explicit null is still a type error, so "omit it" is a real
    # instruction and not a distinction the schema fails to draw.
    manifest["dispatch_groups"][-1] = {"event": "Stop", "matcher": None}
    assert any(
        "matcher" in d and "expected string" in d
        for d in build.manifest_schema_drifts(manifest)
    )


def test_sentinel_is_rejected_as_a_matcher(manifest):
    # The argv sentinel is a legal matcher string, and a group whose matcher IS
    # the sentinel renders the argv of a matcher-less one — main() maps it to
    # None and resolves zero members, disabling every hook in it silently.
    sentinel = build.DISPATCH_NO_MATCHER_ARG
    manifest["dispatch_groups"].append({"event": "Stop", "matcher": sentinel})
    drifts = build.manifest_schema_drifts(manifest)
    assert any("reserved as the matcher-less argv sentinel" in d for d in drifts), drifts


def test_dispatch_member_with_body_is_allowed(manifest):
    # Rejected until issue #1281: the dispatcher imported every member as
    # Python. `_dispatch.run_one` now runs a shell body as a subprocess under
    # the member deadline, so a `body: impl.sh` member collapses into its
    # group like any other (the Stop group's completion-verify and
    # retrospect-mix-check are the live cases).
    name = _first_dispatch_member_name(manifest)
    entry = next(h for h in manifest["hooks"] if h["name"] == name)
    entry["body"] = "impl.sh"
    drifts = build.manifest_schema_drifts(manifest)
    assert not any("declares 'body'" in d for d in drifts), drifts


def test_non_dispatch_member_with_args_is_fine(manifest):
    # Same field, but on a hook whose (event, matcher) is NOT a dispatch
    # group — the gate is scoped to actual members, not a blanket ban.
    pairs = {
        (g["event"], g.get("matcher")) for g in manifest["dispatch_groups"]
    }
    entry = next(
        h for h in manifest["hooks"]
        if (h.get("event"), h.get("matcher")) not in pairs
    )
    entry["args"] = ["--foo"]
    drifts = build.manifest_schema_drifts(manifest)
    assert not any("declares 'args'" in d for d in drifts), drifts


# ---------------------------------------------------------------------------
# Rule 26 (#1300) — review_by sunset review: schema shape + checker rule
# ---------------------------------------------------------------------------

# Pinned so the committed manifest's dates are judged against a fixed day,
# not the wall clock — the OVERDUE branch is exercised by mutation below.
TODAY = date(2026, 9, 5)


def _registrations(manifest: dict, name: str) -> list[dict]:
    return [e for e in manifest["hooks"] if e["name"] == name]


def _multi_event_name(manifest: dict) -> str:
    counts: dict[str, int] = {}
    for e in manifest["hooks"]:
        counts[e["name"]] = counts.get(e["name"], 0) + 1
    return next(name for name, n in counts.items() if n > 1)


def _set_review_by(manifest: dict, name: str, value) -> None:
    for e in _registrations(manifest, name):
        e.pop("review_by", None)
    _registrations(manifest, name)[0]["review_by"] = value


def test_review_by_wrong_type_is_a_schema_drift(manifest):
    entry = manifest["hooks"][0]
    entry["review_by"] = 20261204
    drifts = build.manifest_schema_drifts(manifest)
    assert len(drifts) == 1
    assert "review_by" in drifts[0]
    assert "expected string" in drifts[0]


def test_observe_only_must_be_boolean(manifest):
    entry = manifest["hooks"][0]
    entry.setdefault("mode", {})["observe_only"] = "yes"
    drifts = build.manifest_schema_drifts(manifest)
    assert len(drifts) == 1
    assert "observe_only" in drifts[0]


def test_observe_only_true_is_accepted(manifest):
    entry = manifest["hooks"][0]
    entry.setdefault("mode", {})["observe_only"] = True
    assert build.manifest_schema_drifts(manifest) == []


def test_committed_manifest_passes_rule_26_on_the_pinned_day(manifest):
    assert check.review_by_drifts(manifest, TODAY) == []


def test_every_hook_name_carries_a_review_by(manifest):
    names = {e["name"] for e in manifest["hooks"]}
    missing = [
        n for n in names
        if not any("review_by" in e for e in _registrations(manifest, n))
    ]
    assert missing == []


def test_missing_review_by_fails(manifest):
    name = manifest["hooks"][0]["name"]
    for e in _registrations(manifest, name):
        e.pop("review_by", None)
    drifts = check.review_by_drifts(manifest, TODAY)
    assert len(drifts) == 1
    assert drifts[0].startswith(f"REVIEW_BY MISSING {name}:")
    assert "90 days" in drifts[0]


@pytest.mark.parametrize(
    "bad",
    ["soon", "2026-13-45", "2026-02-30", "2026/12/04", "20261204", "2026-12-4", ""],
)
def test_malformed_review_by_fails(manifest, bad):
    name = manifest["hooks"][0]["name"]
    _set_review_by(manifest, name, bad)
    drifts = check.review_by_drifts(manifest, TODAY)
    assert len(drifts) == 1
    assert drifts[0].startswith(f"REVIEW_BY MALFORMED {name}: {bad!r}")
    assert "YYYY-MM-DD" in drifts[0]


def test_overdue_review_by_fails_and_names_the_remedy(manifest):
    name = manifest["hooks"][0]["name"]
    _set_review_by(manifest, name, "2026-09-04")  # the day before TODAY
    drifts = check.review_by_drifts(manifest, TODAY)
    assert len(drifts) == 1
    assert drifts[0].startswith(f"REVIEW_BY OVERDUE {name}: 2026-09-04")
    # The message must tell the author what to do, not just that it failed.
    assert "re-audit" in drifts[0]
    assert "bump review_by" in drifts[0]
    assert "docs/hook-prune-audit.md" in drifts[0]


def test_review_by_due_today_is_not_overdue(manifest):
    name = manifest["hooks"][0]["name"]
    _set_review_by(manifest, name, TODAY.isoformat())
    assert check.review_by_drifts(manifest, TODAY) == []


def test_valid_future_review_by_passes(manifest):
    name = manifest["hooks"][0]["name"]
    _set_review_by(manifest, name, "2027-01-15")
    assert check.review_by_drifts(manifest, TODAY) == []


def test_multi_event_sibling_may_omit_review_by(manifest):
    # First-registration-wins, like `hosts`: moving the field to the second
    # registration still counts as declared — the NAME carries it.
    name = _multi_event_name(manifest)
    regs = _registrations(manifest, name)
    value = regs[0].pop("review_by")
    regs[1]["review_by"] = value
    assert check.review_by_drifts(manifest, TODAY) == []


def test_conflicting_review_by_across_registrations_fails(manifest):
    name = _multi_event_name(manifest)
    regs = _registrations(manifest, name)
    regs[1]["review_by"] = "2030-01-01"
    drifts = check.review_by_drifts(manifest, TODAY)
    assert len(drifts) == 1
    assert drifts[0].startswith(f"REVIEW_BY CONFLICT {name}:")


def test_main_reports_overdue_once_the_clock_passes_review_by(monkeypatch):
    # End-to-end wiring: the committed tree is green today, and the only thing
    # that changes here is the checker's notion of "today". A far-future clock
    # must turn every backfilled 2026-12-04 into a REVIEW_BY OVERDUE line.
    class FarFuture(date):
        @classmethod
        def today(cls):
            return date(2099, 1, 1)

    monkeypatch.setattr(check, "date", FarFuture)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = check.main()
    out = buf.getvalue()
    assert rc == 1
    assert "REVIEW_BY OVERDUE" in out
    assert "docs/hook-prune-audit.md" in out


def test_review_by_and_observe_only_never_reach_hooks_json(manifest):
    rendered = json.dumps(build.expand_to_hooks_json(manifest))
    assert "review_by" not in rendered
    assert "observe_only" not in rendered
