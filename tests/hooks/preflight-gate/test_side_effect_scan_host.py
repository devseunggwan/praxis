"""side-effect-scan on a self-check host (issue #1635).

On Hermes an `ask` is a self-check the agent answers. The routine publish
steps of the issue workflow (a push of the session's own work branch, a
`gh pr create`) advise there instead; every other shape keeps the ask, and
on any other host (or standalone) nothing changes.

The member runs in-process through `_dispatch.run_one`, the path the
dispatcher uses, with the host published the way `run_group` publishes it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "hooks" / "_lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import _dispatch  # noqa: E402
import _hook_runtime  # noqa: E402

IMPL = REPO_ROOT / "hooks" / "preflight-gate" / "side-effect-scan" / "impl.py"


def _verdict(command: str, host: str | None) -> str:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    _hook_runtime.set_member_host(host)
    try:
        rc, out, err = _dispatch.run_one("preflight-gate", "side-effect-scan", IMPL, payload)
    finally:
        _hook_runtime.set_member_host(None)
    assert rc == 0
    if '"permissionDecision": "ask"' in out:
        return "ask"
    assert out == ""
    return "advise" if err else "pass"


@pytest.fixture(autouse=True)
def _default_branch_convention(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PRAXIS_BRANCH_NAME_REGEX", "PRAXIS_BRANCH_NAME_WHITELIST", "PRAXIS_WRAPPER_COMMIT_CMDS"):
        monkeypatch.delenv(name, raising=False)


ROUTINE = [
    "git push -u origin hub-12-feat-add-manifest",
    "cd ~/w/repo && git push -u origin hub-34-chore-remove-unused-modules",
    "git push --set-upstream origin issue-5-feat-sending-state",
    "git push origin issue-5-feat-sending-state",
    "git push -q -u origin issue-5-feat-sending-state",
    "GIT_TRACE=1 git push origin issue-5-feat-sending-state",
    "git push origin issue-5-feat-sending-state # cross-boundary:ack",
    "/usr/bin/git push origin issue-5-feat-sending-state",
    "git fetch origin && git rebase origin/main && git push -u origin issue-5-feat-sending-state",
    "gh pr create --repo o/r --base main --head hub-1-feat-x --title t --body-file b.md",
    "gh --repo o/r pr create --title t --body-file b.md",
    "gh pr create --repo o/r --base prod --head hub-1-fix-x --title t --body-file b.md # cross-boundary:ack",
    "git push -u origin issue-1-fix-x && gh pr create --title t --body-file b.md",
]

STILL_ASKS = [
    # the ref that moves is not named, or is not a work branch
    "git push",
    "git push origin",
    "git push origin HEAD",
    "git push origin main",
    "git push origin dev",
    "git push origin prod",
    "git push origin feature/login",
    "git push origin refs/heads/issue-1-fix-x",
    'git push origin "$(git branch --show-current)"',
    "git push origin $BRANCH",
    "git push origin issue-1-fix-x issue-2-fix-y",
    # how it moves
    "git push -f origin issue-1-fix-x",
    "git push --force origin issue-1-fix-x",
    "git push --force-with-lease origin issue-1-fix-x",
    "git push --force-with-lease=issue-1-fix-x:abc origin issue-1-fix-x",
    "git push origin +issue-1-fix-x",
    "git push -uf origin issue-1-fix-x",
    "git push --no-verify origin issue-1-fix-x",
    "git push -o ci.skip origin issue-1-fix-x",
    # refs other than the branch itself
    "git push origin issue-1-fix-x:main",
    "git push origin issue-1-fix-x:issue-1-fix-x",
    "git push origin :issue-1-fix-x",
    "git push --delete origin issue-1-fix-x",
    "git push -d origin issue-1-fix-x",
    "git push --mirror origin",
    "git push --all origin",
    "git push --tags origin",
    "git push origin v1.2.3",
    # another remote
    "git push upstream issue-1-fix-x",
    "git push https://github.com/o/r issue-1-fix-x",
    # words the tokenizer cannot place: arguments after a redirection, and a
    # quoted or escaped `#` that the shell passes to git as an argument
    "git push origin issue-1-fix-x > /dev/null --force",
    "git push origin issue-1-fix-x 2>&1 --delete",
    "git push origin issue-1-fix-x 2>&1 | tail -3",
    "git push origin issue-1-fix-x '#' --force",
    'git push origin issue-1-fix-x "#" --force',
    "git push origin issue-1-fix-x \\# --force",
    # a routine step chained with a non-routine one
    "git push origin issue-1-fix-x && git push origin main",
    "gh pr create --title t --body-file b.md && gh pr merge 3 --squash",
    'gh pr create --title t --body "$(gh pr merge 3 --squash)"',
    "gh pr create --title t --body `gh pr merge 3`",
    "git push origin issue-1-fix-x && kubectl apply -f x.yaml",
    # other gh writes
    "gh pr merge 7 --repo o/r --squash # briefing-surfaced: approved",
    "gh workflow run deploy.yml",
    "kubectl apply -f x.yaml",
]


@pytest.mark.parametrize("command", ROUTINE)
def test_routine_publish_advises_on_hermes(command):
    assert _verdict(command, "hermes") == "advise"


@pytest.mark.parametrize("command", ROUTINE)
@pytest.mark.parametrize("host", [None, "claude", "codex", "cursor"])
def test_routine_publish_still_asks_elsewhere(command, host):
    assert _verdict(command, host) == "ask"


@pytest.mark.parametrize("command", STILL_ASKS)
def test_other_publishes_still_ask_on_hermes(command):
    assert _verdict(command, "hermes") == "ask"


def test_plain_commands_stay_silent_on_hermes():
    assert _verdict("git status && gh pr view 3", "hermes") == "pass"


def test_custom_branch_convention_is_honoured(monkeypatch):
    monkeypatch.setenv("PRAXIS_BRANCH_NAME_REGEX", r"^feature/[a-z-]+$")
    assert _verdict("git push origin feature/login", "hermes") == "advise"
    assert _verdict("git push origin issue-1-fix-x", "hermes") == "ask"


def test_base_branch_never_counts_as_a_work_branch(monkeypatch):
    monkeypatch.setenv("PRAXIS_BRANCH_NAME_REGEX", r"^.+$")
    assert _verdict("git push origin main", "hermes") == "ask"
    assert _verdict("git push origin release-1", "hermes") == "advise"


@pytest.mark.parametrize("regex, ref", [
    (r"^.+$", "+main"),
    (r"^.+$", ":main"),
    (r"^.+$", "issue-1-fix-x:main"),
    (r"^.+$", "+release-1"),
    (r"^.+$", "HEAD"),
    (r"^.+$", "HEAD:main"),
    (r"^.+$", "ORIG_HEAD"),
    (r"^.+$", "refs/heads/main"),
    (r"^.+$", "$BRANCH"),
    (r"^.+$", "release-*"),
    (r"^.+$", "a..b"),
    (r"^.+$", "-"),
    (r"^feature/.+$", "feature/login:main"),
    (r"^feature/.+$", "feature/login:refs/heads/main"),
])
def test_refspec_syntax_asks_whatever_the_convention_allows(monkeypatch, regex, ref):
    monkeypatch.setenv("PRAXIS_BRANCH_NAME_REGEX", regex)
    assert _verdict(f"git push origin '{ref}'", "hermes") == "ask"


def test_wrapper_cli_on_the_same_argv_still_asks(monkeypatch, capsys):
    """A wrapper configured under `git push` adds an ask category the demotion does not cover."""
    from importlib import util
    import io
    monkeypatch.setenv("PRAXIS_WRAPPER_COMMIT_CMDS", "git push")
    spec = util.spec_from_file_location("side_effect_scan_wrapped", IMPL)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)  # CATEGORIES read the env at import
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin issue-1-fix-x"}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    _hook_runtime.set_member_host("hermes")
    try:
        assert module.main() == 0
    finally:
        _hook_runtime.set_member_host(None)
    out = capsys.readouterr().out
    assert '"permissionDecision": "ask"' in out and "[wrapper-commit]" in out


def test_prod_advisory_keeps_its_warning():
    payload = json.dumps({"tool_name": "Bash", "tool_input": {
        "command": "gh pr create --base prod --head hub-1-fix-x --title t --body-file b.md"}})
    _hook_runtime.set_member_host("hermes")
    try:
        _rc, _out, err = _dispatch.run_one("preflight-gate", "side-effect-scan", IMPL, payload)
    finally:
        _hook_runtime.set_member_host(None)
    assert err.startswith("[side-effect-scan]") and "PROD scope" in err
