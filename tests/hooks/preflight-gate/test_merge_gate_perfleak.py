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
  "pr view") [ -f "$state/fail" ] && exit 1
             case "$*" in *baseRefName*) base=" main" ;; *) base="" ;; esac
             echo "$(cat "$state/url") $(cat "$state/head")$base" ;;
  "pr diff") if [ -f "$state/diff_error" ]; then cat "$state/diff_error" >&2; exit 1; fi
             cat "$state/diff" ;;
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
    result = _hook(env, f"git fetch && {MERGE}")
    assert result.returncode == 2
    assert "runs after another command" in result.stderr


def test_chained_merge_pinned_to_reviewed_head_passes(env):
    assert _review(env).returncode == 0
    assert _hook(env, f"cd /x && {MERGE} --match-head-commit {HEAD}").returncode == 0


def test_pin_to_unreviewed_head_is_denied(env):
    assert _review(env).returncode == 0
    result = _hook(env, f"{MERGE} --match-head-commit {NEW_HEAD}")
    assert result.returncode == 2
    assert NEW_HEAD[:7] in result.stderr


def test_merge_without_repo_is_denied(env):
    assert _review(env).returncode == 0
    for command in ("gh pr merge 7 --squash", "GH_REPO=acme/other gh pr merge 7 --squash"):
        result = _hook(env, command)
        assert result.returncode == 2, command
        assert "names no repository" in result.stderr


def test_pr_url_names_the_repo(env):
    assert _review(env).returncode == 0
    assert _hook(env, f"gh pr merge {URL} --squash").returncode == 0


def test_gh_host_needs_a_pr_url(env):
    assert _review(env).returncode == 0
    result = _hook(env, f"GH_HOST=ghe.example {MERGE}")
    assert result.returncode == 2
    assert "GH_HOST" in result.stderr
    assert _hook(env, f"GH_HOST=ghe.example gh pr merge {URL} --squash").returncode == 0


def test_disable_auto_merges_nothing_and_passes(env):
    assert _hook(env, "gh pr merge 7 --repo acme/widgets --disable-auto").returncode == 0
    assert not (env["state"] / "calls").exists()


def test_trailing_comment_keeps_the_merge_alone(env):
    assert _review(env).returncode == 0
    assert _hook(env, f"{MERGE} # side-effect:ack").returncode == 0


def test_truncated_record_does_not_count(env):
    assert _review(env).returncode == 0
    record = next((env["tmp"] / "home" / "state" / "perf-leak-review").glob(f"*__{HEAD}.json"))
    record.write_text('{"url": ')
    assert _hook(env, MERGE).returncode == 2


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


TOO_LARGE = ("could not find pull request diff: HTTP 406: Sorry, the diff exceeded the maximum number "
             "of files (300).\nPullRequest.diff too_large\n")


def _git(env, cwd, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                          env=env["env"]).stdout.strip()


@pytest.fixture
def large(env):
    """A PR GitHub will not diff, plus a remote the recorder can fetch it from instead.

    `insteadOf` points the PR's https remote at a local bare repo, so the real `git fetch` runs.
    """
    tmp = env["tmp"]
    origin, work, clone = tmp / "origin.git", tmp / "work", tmp / "clone"
    git_cfg = tmp / "gitconfig"
    git_cfg.write_text(f'[url "file://{origin}"]\n\tinsteadOf = https://github.com/acme/widgets.git\n'
                       "[user]\n\tname = t\n\temail = t@example.com\n[init]\n\tdefaultBranch = main\n")
    env["env"].update({"GIT_CONFIG_GLOBAL": str(git_cfg), "GIT_CONFIG_NOSYSTEM": "1"})

    def run(*args: str, cwd: Path = work) -> str:
        return subprocess.run(["git", *args], cwd=cwd, env=env["env"], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit_file(name: str, body: str, message: str) -> None:
        (work / name).write_text(body)
        run("add", ".")
        run("commit", "-q", "-m", message)

    subprocess.run(["git", "init", "--bare", "-q", str(origin)], env=env["env"], check=True)
    work.mkdir()
    run("init", "-q")
    commit_file("base.py", "x = 1\n", "base")
    run("push", "-q", str(origin), "HEAD:refs/heads/main")
    run("checkout", "-q", "-b", "pr")
    commit_file("pr.py", "for i in items:\n    fetch(i)\n", "pr")
    head = run("rev-parse", "HEAD")
    run("push", "-q", str(origin), "HEAD:refs/pull/7/head")
    run("checkout", "-q", "main")
    commit_file("later_on_base.py", "y = 2\n", "base moves on")
    run("push", "-q", str(origin), "HEAD:refs/heads/main")
    clone.mkdir()
    run("init", "-q", cwd=clone)
    (env["state"] / "head").write_text(head)
    (env["state"] / "diff_error").write_text(TOO_LARGE)
    env.update({"clone": clone, "head": head})
    return env


def test_too_large_diff_without_repo_says_how_to_continue(large):
    result = _record(large, "prepare", "--pr", URL)
    assert result.returncode == 1
    assert "too_large" in result.stderr
    assert "--repo <local clone" in result.stderr


def test_too_large_diff_is_rebuilt_from_local_clone_and_recorded(large):
    clone = str(large["clone"])
    prepared = _record(large, "prepare", "--pr", URL, "--repo", clone)
    assert prepared.returncode == 0, prepared.stderr
    diff_path = prepared.stdout.split("diff ", 1)[1].strip()
    diff = Path(diff_path).read_text()
    assert "+++ b/pr.py" in diff and "+    fetch(i)" in diff
    assert "later_on_base.py" not in diff  # three-dot: base commits after the branch point stay out
    committed = _record(large, "commit", "--pr", URL, "--repo", clone, "--envelope",
                        _envelope(large, json.dumps({"repo_root": "/r", "findings": []})))
    assert committed.returncode == 0, committed.stderr
    assert _hook(large, MERGE).returncode == 0
    assert _git(large, clone, "for-each-ref") == ""  # only FETCH_HEAD was written


def test_other_diff_errors_do_not_fall_back(large):
    (large["state"] / "diff_error").write_text("HTTP 502: Bad Gateway\n")
    result = _record(large, "prepare", "--pr", URL, "--repo", str(large["clone"]))
    assert result.returncode == 1
    assert "502" in result.stderr


def test_local_rebuild_refuses_a_head_github_does_not_report(large):
    (large["state"] / "head").write_text(NEW_HEAD)
    result = _record(large, "prepare", "--pr", URL, "--repo", str(large["clone"]))
    assert result.returncode == 1
    assert "the fetch returned" in result.stderr
