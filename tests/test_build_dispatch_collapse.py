"""Tests for build-plugin-manifests.py dispatch collapse (ADR-0002, #613).

Focus: filter_hooks_for_host must emit exactly ONE dispatcher node per
(event, matcher), at the first fragment that still has a host-kept member.
When an earlier fragment is entirely host-filtered out, the node must land at
the later non-empty fragment's slot — not the empty one — so it keeps its
position relative to intervening non-dispatch groups.

Regression for the CodeRabbit finding on PR #616: the emit guard checked only
the cross-fragment dispatch_timeout, not whether THIS fragment kept a member.
"""

from __future__ import annotations

import importlib.util
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

DISPATCH = frozenset({("PreToolUse", "Bash")})


def _fragmented_hooks() -> dict:
    """Bash split across two non-consecutive fragments (group 0 claude-only,
    group 2 all-hosts) with an Edit group wedged between them."""
    return {
        "description": "fixture",
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "bash-claude-only",
                         "timeout": 5, "hosts": ["claude"]},
                    ],
                },
                {
                    "matcher": "Edit",
                    "hooks": [
                        {"type": "command", "command": "edit-hook", "timeout": 7},
                    ],
                },
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "bash-all-hosts",
                         "timeout": 9},
                    ],
                },
            ]
        },
    }


def _dispatcher_nodes(result: dict) -> list[dict]:
    out = []
    for group in result["hooks"].get("PreToolUse", []):
        for hook in group["hooks"]:
            if _build.DISPATCH_WRAPPER_NAME in hook.get("command", ""):
                out.append(hook)
    return out


def _pretooluse_command_order(result: dict) -> list[str]:
    """Flatten the PreToolUse groups to the command string of each node, in
    emission order, marking the dispatcher node as '<dispatch>'."""
    order = []
    for group in result["hooks"].get("PreToolUse", []):
        for hook in group["hooks"]:
            cmd = hook.get("command", "")
            order.append("<dispatch>" if _build.DISPATCH_WRAPPER_NAME in cmd else cmd)
    return order


def test_codex_empty_first_fragment_emits_at_later_slot():
    # group 0 (claude-only) empties for codex; node must land after the Edit
    # group, at group 2's slot — never at group 0's slot.
    result = _build.filter_hooks_for_host(_fragmented_hooks(), "codex", DISPATCH)
    nodes = _dispatcher_nodes(result)
    assert len(nodes) == 1, "exactly one dispatcher node per matcher"
    assert nodes[0]["timeout"] == 9, "max timeout across host-kept fragments"
    assert "PreToolUse Bash codex" in nodes[0]["command"]
    order = _pretooluse_command_order(result)
    # edit-hook survives; dispatcher comes AFTER it (group 0 was dropped empty)
    assert order == ["edit-hook", "<dispatch>"], order


def test_claude_keeps_first_fragment_emits_at_first_slot():
    # claude keeps group 0, so the node lands at the first slot, before Edit.
    result = _build.filter_hooks_for_host(_fragmented_hooks(), "claude", DISPATCH)
    nodes = _dispatcher_nodes(result)
    assert len(nodes) == 1
    assert nodes[0]["timeout"] == 9, "max(5, 9) across both kept fragments"
    assert "PreToolUse Bash claude" in nodes[0]["command"]
    order = _pretooluse_command_order(result)
    assert order == ["<dispatch>", "edit-hook"], order


def test_single_emit_when_both_fragments_kept():
    # Even when every fragment keeps a member, only one node is emitted.
    result = _build.filter_hooks_for_host(_fragmented_hooks(), "claude", DISPATCH)
    assert len(_dispatcher_nodes(result)) == 1


def _all_bash_claude_only() -> dict:
    """Both Bash fragments claude-only — a non-claude host keeps no Bash member."""
    data = _fragmented_hooks()
    data["hooks"]["PreToolUse"][2]["hooks"][0]["hosts"] = ["claude"]
    return data


def test_no_node_when_all_fragments_filtered_out():
    # codex matches no Bash member (both fragments claude-only): no dispatcher
    # node, matcher absent, only the surviving Edit group remains.
    result = _build.filter_hooks_for_host(_all_bash_claude_only(), "codex", DISPATCH)
    assert _dispatcher_nodes(result) == []
    assert _pretooluse_command_order(result) == ["edit-hook"]
