"""Tests for the `mode.if` dispatch partition (issue #1335).

A member declaring `mode.if` is filtered by the HOST before the hook process
starts, so it cannot share a dispatcher node with members that carry no filter —
the host would skip the whole node, taking the unfiltered members down with it.
`filter_hooks_for_host` therefore emits one node per distinct pattern.

Focus, in the order the failures cost most:
  - the node carries the pattern on BOTH channels (`if` for the host, argv[4]
    for the dispatcher). Only the first would start the right process and run
    the wrong members.
  - each node's timeout is the max over ITS OWN members, not the matcher's.
  - the untagged node keeps the slot and the exact 3-arg command it had before
    this change, so an untagged manifest stays byte-identical.
  - build and runtime read the pattern identically; a disagreement silently
    disables a gate.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "hooks" / "_lib"))

_spec = importlib.util.spec_from_file_location(
    "build_plugin_manifests_if", REPO_ROOT / "scripts" / "build-plugin-manifests.py"
)
assert _spec and _spec.loader
_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_build)

import _dispatch  # noqa: E402

DISPATCH = frozenset({("PreToolUse", "Bash")})
GIT_COMMIT = "Bash(git commit *)"
GH_PR = "Bash(gh pr *)"


def _manifest(*entries: dict) -> dict:
    return {"description": "fixture", "hooks": list(entries)}


def _entry(name: str, timeout: int, if_pattern: str | None = None, **extra) -> dict:
    entry = {
        "name": name,
        "role": "preflight-gate",
        "body": "impl.py",
        "event": "PreToolUse",
        "matcher": "Bash",
        "timeout": timeout,
    }
    if if_pattern is not None:
        entry["mode"] = {"if": if_pattern}
    entry.update(extra)
    return entry


def _nodes(manifest: dict, host: str = "claude") -> list[dict]:
    expanded = _build.expand_to_hooks_json(manifest)
    result = _build.filter_hooks_for_host(expanded, host, DISPATCH)
    out = []
    for group in result["hooks"].get("PreToolUse", []):
        for hook in group["hooks"]:
            out.append(hook)
    return out


def _dispatch_nodes(manifest: dict, host: str = "claude") -> list[dict]:
    return [
        n for n in _nodes(manifest, host)
        if _build.DISPATCH_WRAPPER_NAME in n.get("command", "")
    ]


# --------------------------------------------------------------------------- #
# partition
# --------------------------------------------------------------------------- #


def test_untagged_members_stay_on_one_unfiltered_node():
    nodes = _dispatch_nodes(_manifest(_entry("a", 5), _entry("b", 9)))
    assert len(nodes) == 1
    assert "if" not in nodes[0], "an untagged node must carry no host filter"
    assert nodes[0]["command"].endswith("PreToolUse Bash claude"), nodes[0]["command"]
    assert nodes[0]["timeout"] == 9


def test_one_node_per_distinct_pattern():
    nodes = _dispatch_nodes(_manifest(
        _entry("a", 5),
        _entry("b", 9, GIT_COMMIT),
        _entry("c", 7, GIT_COMMIT),
        _entry("d", 3, GH_PR),
    ))
    assert len(nodes) == 3, [n.get("if") for n in nodes]
    assert [n.get("if") for n in nodes] == [None, GIT_COMMIT, GH_PR], "first-appearance order"


def test_pattern_reaches_both_the_host_field_and_argv():
    nodes = _dispatch_nodes(_manifest(_entry("b", 9, GIT_COMMIT)))
    assert len(nodes) == 1
    node = nodes[0]
    assert node["if"] == GIT_COMMIT, "the host needs `if` to skip the process"
    # argv[4] is what tells the dispatcher which members are its own; shell-quoted
    # because the pattern carries spaces and parentheses.
    assert node["command"].endswith("PreToolUse Bash claude 'Bash(git commit *)'"), node["command"]


def test_each_node_gets_its_own_budget():
    """A node holding one 3 s member must not inherit a sibling's 15 s deadline."""
    nodes = _dispatch_nodes(_manifest(
        _entry("slow", 15),
        _entry("fast", 3, GIT_COMMIT),
    ))
    by_pattern = {n.get("if"): n["timeout"] for n in nodes}
    assert by_pattern == {None: 15, GIT_COMMIT: 3}, by_pattern


def test_host_filter_still_applies_inside_a_partition():
    nodes = _dispatch_nodes(
        _manifest(_entry("a", 5), _entry("b", 9, GIT_COMMIT, hosts=["claude"])),
        host="codex",
    )
    assert [n.get("if") for n in nodes] == [None], "the codex install has no tagged member"


def test_a_partition_of_only_host_filtered_members_emits_no_node():
    nodes = _dispatch_nodes(
        _manifest(_entry("b", 9, GIT_COMMIT, hosts=["claude"])),
        host="codex",
    )
    assert nodes == []


# --------------------------------------------------------------------------- #
# transient marker must not leak
# --------------------------------------------------------------------------- #


def test_the_transient_marker_never_reaches_the_output():
    for node in _nodes(_manifest(_entry("a", 5), _entry("b", 9, GIT_COMMIT))):
        assert "if_pattern" not in node, node


def test_a_standalone_member_carries_its_pattern_as_its_own_if():
    """An args-declaring member is never collapsed, so the pattern lands on it."""
    nodes = _nodes(_manifest(_entry("b", 9, GIT_COMMIT, args=["stop"])))
    standalone = [n for n in nodes if _build.DISPATCH_WRAPPER_NAME not in n["command"]]
    assert len(standalone) == 1
    assert standalone[0]["if"] == GIT_COMMIT
    assert "if_pattern" not in standalone[0]


# --------------------------------------------------------------------------- #
# build and runtime must read the pattern the same way
# --------------------------------------------------------------------------- #


def test_build_and_runtime_agree_on_the_pattern():
    cases = [
        ({"mode": {"if": GIT_COMMIT}}, GIT_COMMIT),
        ({"mode": {}}, None),
        ({}, None),
        ({"mode": {"if": ""}}, None),
        ({"mode": {"if": "   "}}, None),
        ({"mode": {"if": 7}}, None),
        ({"mode": "not-a-dict"}, None),
    ]
    for hook, expected in cases:
        assert _build._member_if_pattern(hook) == expected, hook
        assert _dispatch.member_if_pattern(hook) == expected, hook


def test_runtime_resolves_the_untagged_partition_by_default():
    """argv[4] absent — every node the build has ever emitted — selects the
    members that declare no pattern, which is what keeps this change inert."""
    untagged = _dispatch.group_members("PreToolUse", "Bash", "claude")
    tagged = _dispatch.group_members("PreToolUse", "Bash", "claude", GIT_COMMIT)
    assert untagged, "the real manifest has Bash members"
    assert tagged == [], "no member is tagged yet, so the tagged node is empty"
