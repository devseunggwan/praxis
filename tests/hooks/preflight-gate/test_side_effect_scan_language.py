"""side-effect-scan: the reason says each instruction once, in English.

Issue #1659: every ask and advisory repeated its instruction in Korean after
the English sentence, and the category reasons ended in Korean, so a
self-check host showed the model the same instruction twice on every commit
and push. The commands are shapes that asked on one install.
"""
from __future__ import annotations

import json
import re
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
HANGUL = re.compile(r"[\uac00-\ud7a3]")

COMMANDS = [
    # an ask: a fetch, a rev-parse and a refspec push in one chain
    "cd ~/w/repo && git fetch origin hub-1-feat-dev -q && git rev-parse origin/hub-1-feat-dev"
    " && git push origin HEAD:hub-1-feat-dev",
    # an ask with the prod tag
    "git commit -q -m 'fix: x' && git push -q origin prod",
    # an advisory on a self-check host: a routine own-branch push
    "git push origin issue-5-feat-sending-state",
    # a kubectl write
    "kubectl apply -f a.yaml -n prod",
]


@pytest.fixture(autouse=True)
def _default_branch_convention(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PRAXIS_BRANCH_NAME_REGEX", "PRAXIS_BRANCH_NAME_WHITELIST", "PRAXIS_WRAPPER_COMMIT_CMDS"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("host", [None, "hermes"])
def test_reason_is_english_and_says_it_once(command: str, host: str | None) -> None:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    _hook_runtime.set_member_host(host)
    try:
        rc, out, err = _dispatch.run_one("preflight-gate", "side-effect-scan", IMPL, payload)
    finally:
        _hook_runtime.set_member_host(None)
    assert rc == 0
    text = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] if out else err
    assert text, "the command must produce an ask or an advisory"
    assert not HANGUL.search(text), text
    assert text.count("If this run is intended") == 1, text
