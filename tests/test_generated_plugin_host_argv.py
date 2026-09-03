"""The generated plugin, not a hand-passed literal, is where the host argv comes from (#1246).

`hooks/preflight-gate/verify-commit-flag-override` drops the checklist rows a
host does not install, and it learns the host from `sys.argv` — the dispatcher
is invoked as `_dispatch.sh <event> <matcher> <host>`. The tests that pinned
that behaviour so far (`tests/hooks/preflight-gate/test_verify_commit_flag_
override.sh`, T03-T09) type `claude` / `codex` in by hand, which is the same
value the build bakes in. They pin "given this argument, the filter behaves
like this" — never "the runtime supplies this argument". Let the two diverge
and the suite stays green while the filter is switched off in production.

The divergence has a concrete mechanism, and it is one edit away: the host
argument exists only because `{"event": "PreToolUse", "matcher": "Bash"}` is
in the manifest's `dispatch_groups`. Drop that entry and
`build-plugin-manifests.py` emits per-hook wrappers instead of one dispatcher
node — `argv[0]` becomes the hook's own `impl.py`, no host is passed, and
every checklist row prints on every host again.

Nothing that watches the host filter notices. Measured on a mutated copy of
the tree with that entry deleted and the artifacts regenerated:
`scripts/check-sibling-commit-gates.py` exits 0 and still reports "codex 2 in
the deny checklist", and T03-T09 of that shell test all pass — they read the
manifest's `gates`/`hosts` fields, the renderer's wiring, and a hand-typed
host, never the argv the plugin ships. `check-plugin-manifests.py` does exit 1
on that tree, but incidentally and for another reason: hooks that were
dispatch-wrapped become standalone, and five of them carry no `@fail_open`
decorator. Not one of its lines names a host. Give every hook the decorator
and that catch is gone while the filter is just as dead.

**Reach limit, stated rather than papered over.** CI cannot install and run
the codex or cursor plugin under its real host, so this file does not claim
to observe a live plugin. It reads the *committed generated* `hooks.json` for
every platform that declares a `hooks` output and derives the argv from
there — the fallback the issue named. What that leaves unverified: whether
each host actually executes the command string it was handed. Everything from
the command string inward is covered here; the host's own hook runner is not.

Nothing below hardcodes a platform, a host id, a hook name, or a count. The
platform list comes from `manifests/platforms/*.json`, the expected checklist
rows from `hooks/manifest.json` — sibling sessions add hooks to that manifest
routinely, and a hand-copied list is the drift this repo has already paid for
twice (see `scripts/check-sibling-commit-gates.py`'s docstring).
"""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLATFORMS_DIR = REPO_ROOT / "manifests" / "platforms"

# `← <hook>` is the structural anchor of a checklist row, the same one
# scripts/check-sibling-commit-gates.py reads.
_ROW_RE = re.compile(r"←\s*([a-z0-9][a-z0-9-]*)")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(REPO_ROOT / "scripts"))
_build = _load("build_plugin_manifests", REPO_ROOT / "scripts" / "build-plugin-manifests.py")
_gates = _load("check_sibling_commit_gates", REPO_ROOT / "scripts" / "check-sibling-commit-gates.py")


def hook_installing_platforms() -> list[tuple[str, Path]]:
    """(host_id, generated hooks.json path) for every platform that ships hooks.

    Same derivation `main()` in the build script runs, so a platform added or
    retired shows up here with no edit — 7.14.0 retired two of them.
    """
    out: list[tuple[str, Path]] = []
    for path in sorted(PLATFORMS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        host = data.get("host_id", data.get("platform"))
        for output in data.get("outputs", []):
            if output.get("kind") == "hooks":
                out.append((host, REPO_ROOT / output["path"]))
    return out


PLATFORMS = hook_installing_platforms()


def dispatch_commands(hooks_data: dict, event: str, matcher: str) -> list[list[str]]:
    """Full argv of every dispatcher node in `hooks_data` under (event, matcher).

    Returns one list per node — a list, not an Optional, so "no node" and "two
    nodes" are both visible to the caller instead of collapsing into the same
    failure. The command is parsed with `shlex`, mirroring how a host shell
    reads it, so a quoted matcher (`'Edit|Write'`) tokenizes the same way.
    Element 0 is the wrapper path the plugin ships (`${CLAUDE_PLUGIN_ROOT}/...`);
    the rest is the argv tail.
    """
    found: list[list[str]] = []
    for group in hooks_data.get("hooks", {}).get(event, []):
        if group.get("matcher") != matcher:
            continue
        for hook in group.get("hooks", []):
            command = hook.get("command", "")
            if _build.DISPATCH_WRAPPER_NAME not in command:
                continue
            found.append(shlex.split(command))
    return found


# The one token in a shipped command that cannot resolve outside an installed
# plugin. Everything after it is a real path, relative to the plugin root — and
# this checkout IS that root, so only the prefix is rewritten.
_PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}/"


def local_wrapper(argv0: str) -> Path:
    """This checkout's copy of the wrapper the shipped command names.

    Every path component after the plugin-root variable is kept. Taking only
    the basename would resolve a command pointing at `hooks/sub/_dispatch.sh`,
    or at a same-named wrapper somewhere else, back onto `hooks/_dispatch.sh`
    — so a build that moved the wrapper would ship a path nothing can execute
    while this file stayed green. Reading the name from the command rather
    than from a constant likewise means a rename is followed, not missed.
    """
    assert argv0.startswith(_PLUGIN_ROOT_VAR), (
        f"shipped command {argv0!r} does not start with {_PLUGIN_ROOT_VAR!r} — "
        "the path cannot be resolved against this checkout"
    )
    return REPO_ROOT / argv0[len(_PLUGIN_ROOT_VAR):]


def test_platform_list_is_not_empty():
    """A derivation that silently found nothing would make every test below vacuous."""
    assert PLATFORMS, f"no platform under {PLATFORMS_DIR} declares a 'hooks' output"


@pytest.mark.parametrize("host,hooks_path", PLATFORMS, ids=[h for h, _ in PLATFORMS])
def test_generated_plugin_bakes_its_own_host_into_the_bash_dispatch_argv(host, hooks_path):
    """The shipped command string carries the host — not just the test's literal."""
    assert hooks_path.exists(), f"{hooks_path} is not committed; run scripts/build-plugin-manifests.py"
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    nodes = dispatch_commands(data, "PreToolUse", "Bash")
    assert len(nodes) == 1, (
        f"{hooks_path.relative_to(REPO_ROOT)}: expected exactly one PreToolUse/Bash "
        f"dispatcher node, got {len(nodes)} — no node means the group left "
        "`dispatch_groups` and the host argument is gone with it"
    )
    assert nodes[0][1:] == ["PreToolUse", "Bash", host], (
        f"{hooks_path.relative_to(REPO_ROOT)}: dispatcher argv {nodes[0][1:]} does not "
        f"end in this platform's own host_id {host!r}"
    )


def expected_rows(host: str) -> list[str]:
    """Checklist rows the deny message must print on `host`, derived from the manifest.

    The checklist names come from `verify-commit-flag-override`'s own literal;
    which of them survive is `hooks/manifest.json`'s `hosts` whitelist, read
    through the same `derive()` the sibling-gate canary uses.
    """
    names, drifts = _gates.checklist_names(REPO_ROOT)
    assert not drifts, drifts
    assert names, "the deny checklist carries no rows — nothing to filter"
    installed, drifts = _gates.derive(REPO_ROOT, host)
    assert not drifts, drifts
    return sorted(set(names) & set(installed))


@pytest.mark.parametrize("host,hooks_path", PLATFORMS, ids=[h for h, _ in PLATFORMS])
def test_dispatcher_run_with_the_shipped_argv_prints_that_hosts_rows(host, hooks_path):
    """Run the command the plugin ships — the wrapper too, not just its argv.

    This is the half the argv assertion above cannot reach: a correct command
    string handed to a runner that ignores it would still pass that test. So
    the whole shipped invocation is executed, `_dispatch.sh` included. Calling
    `_dispatch.py` directly would leave the wrapper's own `exec python3 "$IMPL"
    "$@"` unpinned — drop or reorder those arguments there and every installed
    plugin loses host filtering with this file still green.
    """
    nodes = dispatch_commands(json.loads(hooks_path.read_text(encoding="utf-8")), "PreToolUse", "Bash")
    assert len(nodes) == 1, (
        f"{hooks_path.relative_to(REPO_ROOT)}: no single PreToolUse/Bash dispatcher "
        f"node to take the command from (got {len(nodes)})"
    )
    wrapper = local_wrapper(nodes[0][0])
    assert wrapper.is_file(), f"{wrapper} — the shipped command names a wrapper this checkout lacks"
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": 'git commit -n -m "fix: probe title"  # side-effect:ack'},
        }
    )
    run = subprocess.run(
        [str(wrapper), *nodes[0][1:]],
        input=payload,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    decision = json.loads(run.stdout)["hookSpecificOutput"]
    reason = decision["permissionDecisionReason"]
    assert "BLOCKED: Commit-flag override" in reason, "another gate won the decision"
    assert sorted(set(_ROW_RE.findall(reason))) == expected_rows(host), reason


def _regenerate_without_bash_dispatch(host: str) -> dict:
    """Re-render this host's hooks.json with PreToolUse/Bash out of `dispatch_groups`.

    Faithful to the real mutation: `main()` builds its `dispatch_groups` set
    from exactly that manifest field, so dropping the pair here renders what
    deleting the entry would have written to disk. Rendering in-process keeps
    the working tree untouched — the committed artifacts stay as they are.
    """
    manifest = _build.load_manifest()
    hooks_source = _build.expand_to_hooks_json(manifest)
    groups = frozenset(
        (g["event"], g.get("matcher"))
        for g in manifest.get("dispatch_groups", [])
        if not (g["event"] == "PreToolUse" and g.get("matcher") == "Bash")
    )
    return _build.filter_hooks_for_host(hooks_source, host, groups)


@pytest.mark.parametrize("host,_path", PLATFORMS, ids=[h for h, _ in PLATFORMS])
def test_positive_control_dropping_the_dispatch_group_loses_the_host_argument(host, _path):
    """The control the two tests above are worthless without.

    Without it, "this pins the premise" and "this is green no matter what" are
    the same observation. The premise is that `PreToolUse/Bash` sits in
    `dispatch_groups`; remove it and the assertions above must break — no
    dispatcher node, so no argv, so no host anywhere in the Bash group.
    """
    mutated = _regenerate_without_bash_dispatch(host)
    assert dispatch_commands(mutated, "PreToolUse", "Bash") == [], (
        "dispatcher node survived the mutation — the control proves nothing"
    )
    commands = [
        hook.get("command", "")
        for group in mutated.get("hooks", {}).get("PreToolUse", [])
        if group.get("matcher") == "Bash"
        for hook in group.get("hooks", [])
    ]
    assert commands, "the Bash group emptied entirely — the wrong mutation"
    assert not any(host in shlex.split(c)[1:] for c in commands), (
        f"a per-hook wrapper still carries {host!r}: {commands}"
    )
