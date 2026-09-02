"""Rule 14 (ADR-0002, #617): dispatch-group ↔ build/runtime consistency.

Two halves, tested independently:

  - ``dispatch_node_drifts()`` — the pure node-shape checker, exercised with
    crafted hooks.json fragments (exactly-one-dispatcher, member-leak, node
    count, wrong-host args, host-filtered-empty);
  - the runtime cross-check inside ``main()`` — exercised end-to-end by
    monkeypatching ``_dispatch.group_members`` to drop a resolved member and
    asserting ``main()`` reports ``DISPATCH MEMBER DRIFT`` while the unmodified
    tree is otherwise green.

The node-shape half can be checked against a committed-file tamper too, but that
also trips Rule 5 (drift); these unit tests isolate Rule 14's own signal so a
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


def test_args_member_standalone_node_is_not_a_leak():
    # An args-declaring member is kept STANDALONE by the build (the dispatcher
    # cannot forward argv; the runtime excludes it too — issue #1199 review),
    # so its node beside the dispatcher node is expected, not a member leak.
    hj = _hooks_json([_disp_node("claude"), _member_node("strike-counter")])
    out = check.dispatch_node_drifts(
        hj, "PreToolUse", "Bash", "claude", {"x"}, WRAP,
        args_wrappers={"strike-counter.sh"},
    )
    assert out == [], out
    # ...but a node NOT in args_members is still flagged, and the message names
    # args as the one legitimate standalone cause.
    out = check.dispatch_node_drifts(
        hj, "PreToolUse", "Bash", "claude", {"x"}, WRAP
    )
    assert any("DISPATCH MEMBER LEAK" in d and "args-declaring" in d for d in out), out


def test_wrapper_suffix_node_is_not_a_leak():
    # A `wrapper_suffix` member's node is `<name>-pre.sh`, which never contains
    # `/<name>.sh` — the substring probe called a correct node a leak, and
    # pre-edit-md-escape-advisory (args + "-pre"/"-post") is a live example.
    hj = _hooks_json([_disp_node("claude"), _member_node("md-escape-pre")])
    out = check.dispatch_node_drifts(
        hj, "PreToolUse", "Bash", "claude", {"x"}, WRAP,
        args_wrappers={"md-escape-pre.sh"},
    )
    assert out == [], out


def test_missing_args_node_is_reported():
    # The half a leak check structurally cannot see: the args member's node is
    # GONE, so there is no node left to flag and the count of dispatcher nodes
    # is still right. Silence here means the hook is disabled and nothing says so.
    hj = _hooks_json([_disp_node("claude")])
    out = check.dispatch_node_drifts(
        hj, "PreToolUse", "Bash", "claude", {"x"}, WRAP,
        args_wrappers={"strike-counter.sh"},
    )
    assert any("DISPATCH ARGS NODE MISSING" in d for d in out), out
    assert any("strike-counter.sh" in d for d in out), out
    # Control: the same call with the node present says nothing.
    hj_ok = _hooks_json([_disp_node("claude"), _member_node("strike-counter")])
    assert check.dispatch_node_drifts(
        hj_ok, "PreToolUse", "Bash", "claude", {"x"}, WRAP,
        args_wrappers={"strike-counter.sh"},
    ) == []


def test_args_entry_in_the_group_still_counts_as_standalone():
    # The runtime excludes an args-declaring member from the group, so the hook
    # runs on its own and needs @fail_open. Judged by (event, matcher) alone it
    # reads as dispatch-wrapped and the requirement was skipped.
    dispatched = [{"event": "PreToolUse", "matcher": "Bash"}]
    assert check.runs_standalone(dispatched) is False
    assert check.runs_standalone(
        [{"event": "PreToolUse", "matcher": "Bash", "args": ["--flag"]}]
    ) is True
    # An entry outside the group is standalone whether or not it has args.
    assert check.runs_standalone([{"event": "Stop"}]) is True
    # One dispatched entry does not launder a second, args-bearing one.
    assert check.runs_standalone(
        dispatched + [{"event": "PreToolUse", "matcher": "Bash", "args": ["-x"]}]
    ) is True
    # No entries at all: nothing runs standalone.
    assert check.runs_standalone([]) is False


def _matcherless_hooks_json(nodes: list[dict]) -> dict:
    return {"hooks": {"Stop": [{"hooks": nodes}]}}


def test_matcherless_group_expects_sentinel_args():
    # A (Stop, None) dispatcher node must carry the no-matcher SENTINEL in the
    # matcher argv slot; a literal "None" render resolves zero members at
    # runtime and must be flagged.
    sentinel = check._build.DISPATCH_NO_MATCHER_ARG
    good = _matcherless_hooks_json([
        {
            "type": "command",
            "command": f"${{CLAUDE_PLUGIN_ROOT}}/hooks/{WRAP} Stop {sentinel} claude",
            "timeout": 10,
        }
    ])
    out = check.dispatch_node_drifts(good, "Stop", None, "claude", {"x"}, WRAP)
    assert out == [], out

    bad = _matcherless_hooks_json([
        {
            "type": "command",
            "command": f"${{CLAUDE_PLUGIN_ROOT}}/hooks/{WRAP} Stop None claude",
            "timeout": 10,
        }
    ])
    out = check.dispatch_node_drifts(bad, "Stop", None, "claude", {"x"}, WRAP)
    assert any("DISPATCH ARGS" in d for d in out), out


def test_no_matcher_sentinel_pinned_across_build_and_runtime():
    # The build renders DISPATCH_NO_MATCHER_ARG; the runtime maps
    # NO_MATCHER_ARG back to None. Divergence silently resolves empty groups —
    # Rule 14 reports it, and this pins the pairing at unit level too.
    assert check._build.DISPATCH_NO_MATCHER_ARG == check._dispatch.NO_MATCHER_ARG


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
