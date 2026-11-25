"""merge-gate `perfleak` check and the perf-leak-review recorder (issue #1667).

Both run as subprocesses against a fake `gh` on PATH, so the real argv walking,
record paths and validator call are what is exercised.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECK = REPO_ROOT / "hooks" / "preflight-gate" / "merge-gate" / "perfleak.py"
IMPL = REPO_ROOT / "hooks" / "preflight-gate" / "merge-gate" / "impl.py"
RECORDER = REPO_ROOT / "skills" / "perf-leak-review" / "record_review.py"

URL = "https://github.com/acme/widgets/pull/7"
HEAD = "a" * 40
NEW_HEAD = "b" * 40
DIFF = "diff --git a/x.py b/x.py\n+for i in items:\n+    fetch(i)\n"

FAKE_GH = """#!/usr/bin/env bash
state="$FAKE_GH_STATE"
echo "$*" >> "$state/calls"
case "$1 $2" in
  "pr view") [ -f "$state/fail" ] && exit 1; echo "$(cat "$state/url") $(cat "$state/head")" ;;
  "pr diff") cat "$state/diff" ;;
  *) exit 1 ;;
esac
"""


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    state = tmp_path / "gh"
    state.mkdir()
    (state / "url").write_text(URL)
    (state / "head").write_text(HEAD)
    (state / "diff").write_text(DIFF)
    base = {k: v for k, v in os.environ.items() if not k.startswith(("PRAXIS_", "GH_"))}
    base.update({
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_GH_STATE": str(state),
        "PRAXIS_HOME": str(tmp_path / "home"),
        "PRAXIS_FIRE_TELEMETRY_DISABLE": "1",
        "PRAXIS_MERGE_PERF_LEAK_REVIEW": "1",
    })
    return {"env": base, "state": state, "tmp": tmp_path}


def _hook(env, command: str, script: Path = CHECK) -> subprocess.CompletedProcess:
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}, "session_id": "s1", "cwd": str(env["tmp"])}
    return subprocess.run([sys.executable, str(script)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env["env"], check=False)


def _record(env, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RECORDER), *args], capture_output=True, text=True,
                          env=env["env"], check=False)


def _envelope(env, body: str) -> str:
    path = env["tmp"] / "envelope.json"
    path.write_text(body)
    return str(path)


def _review(env) -> subprocess.CompletedProcess:
    assert _record(env, "prepare", "--pr", URL).returncode == 0
    return _record(env, "commit", "--pr", URL, "--envelope",
                   _envelope(env, json.dumps({"repo_root": "/r", "findings": []})))


MERGE = "gh pr merge 7 --repo acme/widgets --squash"


def test_merge_without_record_is_denied(env):
    result = _hook(env, MERGE)
    assert result.returncode == 2
    assert "no perf-leak-review record" in result.stderr
    assert "record_review.py prepare --pr " + URL in result.stderr


def test_merge_with_record_for_head_passes(env):
    assert _review(env).returncode == 0
    assert _hook(env, MERGE).returncode == 0


def test_record_for_old_head_does_not_cover_new_head(env):
    assert _review(env).returncode == 0
    (env["state"] / "head").write_text(NEW_HEAD)
    result = _hook(env, MERGE)
    assert result.returncode == 2
    assert NEW_HEAD[:7] in result.stderr


def test_check_is_off_without_env(env):
    del env["env"]["PRAXIS_MERGE_PERF_LEAK_REVIEW"]
    assert _hook(env, MERGE).returncode == 0
    assert not (env["state"] / "calls").exists()  # no gh call at all


def test_unresolvable_target_is_denied(env):
    (env["state"] / "fail").write_text("")
    result = _hook(env, MERGE)
    assert result.returncode == 2
    assert "could not resolve" in result.stderr


def test_repo_flag_is_forwarded_and_non_merge_ignored(env):
    assert _hook(env, "gh pr view 7 --repo acme/widgets").returncode == 0
    assert _hook(env, "gh pr merge --help").returncode == 0
    assert not (env["state"] / "calls").exists()
    _hook(env, MERGE)
    assert "-R acme/widgets" in (env["state"] / "calls").read_text()


def test_chained_merge_is_checked(env):
    assert _hook(env, f"git fetch && {MERGE}").returncode == 2


def test_impl_denies_before_approval(env):
    result = _hook(env, MERGE, IMPL)
    assert result.returncode == 2
    assert "no perf-leak-review record" in result.stderr


def test_commit_prints_anchor_row(env):
    result = _review(env)
    assert result.returncode == 0, result.stderr
    assert "| P | perf-leak-review on `aaaaaaa`: 0 candidate(s) (C1:0 C2:0 C3:0 C4:0 C5:0)" in result.stdout
    assert result.stdout.rstrip().endswith("| PASS(mock) |")


def test_commit_accepts_fenced_envelope_and_counts_classes(env):
    assert _record(env, "prepare", "--pr", URL).returncode == 0
    finding = {"class": "C1", "file": "x.py", "line": 2, "evidence": "fetch(i)",
               "confidence": "med", "grade": "candidate"}
    body = "```json\n" + json.dumps({"repo_root": "/r", "findings": [finding]}) + "\n```\n"
    result = _record(env, "commit", "--pr", URL, "--envelope", _envelope(env, body))
    assert result.returncode == 0, result.stderr
    assert "1 candidate(s) (C1:1 C2:0" in result.stdout
    assert "listed under Open items" in result.stdout


def test_commit_refuses_invalid_envelope(env):
    assert _record(env, "prepare", "--pr", URL).returncode == 0
    bad = json.dumps({"repo_root": "/r", "findings": [{"class": "C9"}]})
    result = _record(env, "commit", "--pr", URL, "--envelope", _envelope(env, bad))
    assert result.returncode == 1
    assert "rejected by validate_findings.py" in result.stderr
    assert _hook(env, MERGE).returncode == 2


def test_commit_refuses_moved_head(env):
    assert _record(env, "prepare", "--pr", URL).returncode == 0
    (env["state"] / "head").write_text(NEW_HEAD)
    result = _record(env, "commit", "--pr", URL, "--envelope",
                     _envelope(env, json.dumps({"repo_root": "/r", "findings": []})))
    assert result.returncode == 1
    assert "run prepare again" in result.stderr


def test_commit_refuses_changed_diff(env):
    assert _record(env, "prepare", "--pr", URL).returncode == 0
    (env["state"] / "diff").write_text(DIFF + "+more\n")
    result = _record(env, "commit", "--pr", URL, "--envelope",
                     _envelope(env, json.dumps({"repo_root": "/r", "findings": []})))
    assert result.returncode == 1
    assert "changed since prepare" in result.stderr


def test_prepare_refuses_empty_diff_and_bad_url(env):
    (env["state"] / "diff").write_text("")
    assert _record(env, "prepare", "--pr", URL).returncode == 1
    assert _record(env, "prepare", "--pr", "7").returncode == 1
