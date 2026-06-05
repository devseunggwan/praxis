"""Rule 13 (ADR-0002, #617): dispatch-group ↔ build/runtime consistency.

Two halves, tested independently:

  - ``dispatch_node_drifts()`` — the pure node-shape checker, exercised with
    crafted hooks.json fragments (exactly-one-dispatcher, member-leak, node
    count, wrong-host args, host-filtered-empty);
  - the runtime cross-check inside ``main()`` — exercised end-to-end by
    monkeypatching ``_dispatch.group_members`` to drop a resolved member and
    asserting ``main()`` reports ``DISPATCH MEMBER DRIFT`` while the unmodified
    tree is otherwise green.

The node-shape half can be checked against a committed-file tamper too, but that
also trips Rule 5 (drift); these unit tests isolate Rule 13's own signal so a
regression in the new invariant is unambiguous.
"""
from __future__ import annotations

import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "check_plugin_manifests", REPO_ROOT / "scripts" / "check-plugin-manifests.py"
)
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

WRAP = check._build.DISPATCH_WRAPPER_NAME  # "_dispatch.sh"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _hooks_json(nodes: list[dict]) -> dict:
    return {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": nodes}]}}


def _disp_node(host: str, *, event: str = "PreToolUse", matcher: str = "Bash") -> dict:
    return {
        "type": "command",
        "command": (
            f"${{CLAUDE_PLUGIN_ROOT}}/hooks/{WRAP} {event} {matcher} {host}"
        ),
        "timeout": 15,
    }


def _member_node(name: str) -> dict:
    return {
        "type": "command",
        "command": f"${{CLAUDE_PLUGIN_ROOT}}/hooks/{name}.sh",
        "timeout": 5,
    }


# ---------------------------------------------------------------------------
# dispatch_node_drifts — pure node-shape checker
# ---------------------------------------------------------------------------

def test_one_dispatcher_node_is_clean():
    hj = _hooks_json([_disp_node("claude")])
    out = check.dispatch_node_drifts(
        hj, "PreToolUse", "Bash", "claude", {"side-effect-scan"}, WRAP
    )
    assert out == [], out


def test_two_dispatcher_nodes_flagged():
    hj = _hooks_json([_disp_node("claude"), _disp_node("claude")])
    out = check.dispatch_node_drifts(
        hj, "PreToolUse", "Bash", "claude", {"x"}, WRAP
    )
    assert any("DISPATCH NODE COUNT" in d for d in out), out


def test_member_leak_flagged():
    hj = _hooks_json([_disp_node("claude"), _member_node("side-effect-scan")])
    out = check.dispatch_node_drifts(
        hj, "PreToolUse", "Bash", "claude", {"x"}, WRAP
    )
    assert any("DISPATCH MEMBER LEAK" in d for d in out), out


def test_zero_nodes_with_expected_members_flagged():
    hj = _hooks_json([])
    out = check.dispatch_node_drifts(
        hj, "PreToolUse", "Bash", "claude", {"x"}, WRAP
    )
    assert any("DISPATCH NODE COUNT" in d for d in out), out


def test_zero_nodes_when_host_filters_all_is_clean():
    # No member survives the host filter → matcher group absent → no drift.
    hj = {"hooks": {"PreToolUse": []}}
    out = check.dispatch_node_drifts(
        hj, "PreToolUse", "Bash", "codex", set(), WRAP
    )
    assert out == [], out


def test_wrong_host_args_flagged():
    # A dispatcher node baked with the codex host, checked under claude.
    hj = _hooks_json([_disp_node("codex")])
    out = check.dispatch_node_drifts(
        hj, "PreToolUse", "Bash", "claude", {"x"}, WRAP
    )
    assert any("DISPATCH ARGS" in d for d in out), out


# ---------------------------------------------------------------------------
# Runtime cross-check — full main() with a monkeypatched resolver
# ---------------------------------------------------------------------------

def _run_main() -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = check.main()
    return rc, buf.getvalue()


def test_baseline_check_clean():
    rc, out = _run_main()
    assert rc == 0, out


def test_member_drop_detected(monkeypatch):
    orig = check._dispatch.group_members

    def fake(event, matcher, host=None):
        members = orig(event, matcher, host)
        return members[:-1] if members else members  # drop one resolved member

    monkeypatch.setattr(check._dispatch, "group_members", fake)
    rc, out = _run_main()
    assert rc == 1, out
    assert "DISPATCH MEMBER DRIFT" in out, out
