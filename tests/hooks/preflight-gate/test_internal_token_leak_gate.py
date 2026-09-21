"""internal-token-leak-gate (issue #1470).

Every case runs the real hook as a subprocess against a real git repository.
`gh` is replaced on PATH by a stub that answers the one call the hook makes
(`gh api repos/<r> --jq .visibility`) from `FAKE_GH_VIS` and logs each call, so
visibility — the axis that decides block vs. silence — is set per case, and the
cache can be observed by counting calls.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / "hooks" / "preflight-gate" / "internal-token-leak-gate" / "impl.py"
TOKENS = "acme,zeta-corp"
ORIGIN = "https://github.com/example-org/pub.git"

FAKE_GH = """#!/bin/sh
echo "$*" >> "$FAKE_GH_LOG"
case "$FAKE_GH_VIS" in
  fail) exit 1 ;;
  *) echo "$FAKE_GH_VIS" ;;
esac
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo, check=True, capture_output=True,
    )


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "PRAXIS_INTERNAL_TOKENS": TOKENS,
        "FAKE_GH_VIS": "public",
        "FAKE_GH_LOG": str(tmp_path / "gh.log"),
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "remote", "add", "origin", ORIGIN)
    (r / "a.txt").write_text("keep\nold acme line\n")
    _git(r, "add", "a.txt")
    _git(r, "commit", "-q", "-m", "init")
    return r


def run(command: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd),
        "session_id": "test-internal-token",
    })
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True,
        text=True, timeout=20, cwd=str(cwd), env=env,
    )


def gh_calls(env: dict[str, str]) -> int:
    log = Path(env["FAKE_GH_LOG"])
    return len(log.read_text().splitlines()) if log.exists() else 0


def stage(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    _git(repo, "add", name)


# ---------------------------------------------------------------------------
# git commit
# ---------------------------------------------------------------------------

def test_commit_message_token_blocks_on_public(repo: Path, env: dict[str, str]) -> None:
    r = run('git commit -m "feat: wire the acme-wiki skill"', repo, env)
    assert r.returncode == 2, r.stderr
    assert "commit message:1" in r.stderr
    assert "example-org/pub is a public repository" in r.stderr


def test_staged_added_line_blocks_with_location(repo: Path, env: dict[str, str]) -> None:
    stage(repo, "b.txt", "one\ntwo uses ZETA-CORP-cli\n")
    r = run('git commit -m "chore: add b"', repo, env)
    assert r.returncode == 2, r.stderr
    assert "b.txt:2" in r.stderr


def test_removed_line_does_not_block(repo: Path, env: dict[str, str]) -> None:
    (repo / "a.txt").write_text("keep\n")
    _git(repo, "add", "a.txt")
    r = run('git commit -m "chore: drop the old line"', repo, env)
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""


def test_context_line_is_not_an_added_line(repo: Path, env: dict[str, str]) -> None:
    (repo / "a.txt").write_text("keep\nold acme line\nnew neutral line\n")
    _git(repo, "add", "a.txt")
    r = run('git commit -m "chore: append"', repo, env)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("vis", ["private", "internal"])
def test_non_public_repo_is_silent(repo: Path, env: dict[str, str], vis: str) -> None:
    env["FAKE_GH_VIS"] = vis
    r = run('git commit -m "feat: acme"', repo, env)
    assert r.returncode == 0
    assert r.stderr == ""


def test_unresolved_visibility_advises_without_blocking(repo: Path, env: dict[str, str]) -> None:
    env["FAKE_GH_VIS"] = "fail"
    r = run('git commit -m "feat: acme"', repo, env)
    assert r.returncode == 0
    assert "ADVISORY (visibility unresolved)" in r.stderr


def test_unset_env_is_inert(repo: Path, env: dict[str, str]) -> None:
    del env["PRAXIS_INTERNAL_TOKENS"]
    r = run('git commit -m "feat: acme"', repo, env)
    assert r.returncode == 0
    assert gh_calls(env) == 0


@pytest.mark.parametrize(
    ("message", "blocks"),
    [
        ("ACME upper case", True),
        ("acmectl is the cli", True),
        ("the acme-dev-hub plugin", True),
        ("xacme is another word", False),
        ("zeta alone is fine", False),
    ],
)
def test_left_boundary_and_case(repo: Path, env: dict[str, str], message: str, blocks: bool) -> None:
    r = run(f'git commit -m "chore: {message}"', repo, env)
    assert (r.returncode == 2) is blocks, r.stderr


def test_cd_into_repo_reads_that_index(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    stage(repo, "b.txt", "acme\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r = run(f'cd {repo} && git commit -m "chore: b"', elsewhere, env)
    assert r.returncode == 2, r.stderr
    assert "b.txt:1" in r.stderr


def test_git_dash_c_reads_that_index(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    stage(repo, "b.txt", "acme\n")
    r = run(f'git -C {repo} commit -m "chore: b"', tmp_path, env)
    assert r.returncode == 2, r.stderr


def test_opaque_cd_makes_cwd_unknown(repo: Path, env: dict[str, str]) -> None:
    r = run('cd "$TARGET" && git commit -m "feat: acme"', repo, env)
    assert r.returncode == 0
    assert gh_calls(env) == 0


def test_dry_run_commits_nothing(repo: Path, env: dict[str, str]) -> None:
    r = run('git commit --dry-run -m "feat: acme"', repo, env)
    assert r.returncode == 0


def test_commit_all_reads_unstaged_tracked_edits(repo: Path, env: dict[str, str]) -> None:
    (repo / "a.txt").write_text("keep\nold acme line\nzeta-corp added\n")
    r_plain = run('git commit -m "chore: edit"', repo, env)
    assert r_plain.returncode == 0, "unstaged edit is not in a plain commit"
    r_all = run('git commit -am "chore: edit"', repo, env)
    assert r_all.returncode == 2, r_all.stderr
    assert "a.txt:3" in r_all.stderr


def test_message_file_is_read(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    msg = tmp_path / "msg.txt"
    msg.write_text("chore: x\n\nbody names acme\n")
    r = run(f"git commit -F {msg}", repo, env)
    assert r.returncode == 2, r.stderr
    assert "commit message:3" in r.stderr


def test_strict_zero_downgrades_to_advisory(repo: Path, env: dict[str, str]) -> None:
    env["PRAXIS_INTERNAL_TOKEN_STRICT"] = "0"
    r = run('git commit -m "feat: acme"', repo, env)
    assert r.returncode == 0
    assert "ADVISORY (STRICT=0)" in r.stderr


def test_visibility_is_cached_per_repo(repo: Path, env: dict[str, str]) -> None:
    run('git commit -m "feat: acme"', repo, env)
    run('git commit -m "feat: acme again"', repo, env)
    assert gh_calls(env) == 1


def test_unresolved_is_not_cached(repo: Path, env: dict[str, str]) -> None:
    env["FAKE_GH_VIS"] = "fail"
    run('git commit -m "feat: acme"', repo, env)
    env["FAKE_GH_VIS"] = "public"
    r = run('git commit -m "feat: acme"', repo, env)
    assert r.returncode == 2
    assert gh_calls(env) == 2


def test_no_hit_makes_no_network_call(repo: Path, env: dict[str, str]) -> None:
    r = run('git commit -m "feat: neutral"', repo, env)
    assert r.returncode == 0
    assert gh_calls(env) == 0


# ---------------------------------------------------------------------------
# gh writes
# ---------------------------------------------------------------------------

def test_gh_pr_body_to_explicit_repo(repo: Path, env: dict[str, str]) -> None:
    r = run('gh pr create --repo other/public-thing --title "t" --body "see acme"', repo, env)
    assert r.returncode == 2, r.stderr
    assert "other/public-thing is a public repository" in r.stderr
    assert "repos/other/public-thing" in Path(env["FAKE_GH_LOG"]).read_text()


def test_gh_issue_title(repo: Path, env: dict[str, str]) -> None:
    r = run('gh issue create --title "acme leak" --body "neutral"', repo, env)
    assert r.returncode == 2, r.stderr
    assert "title:1" in r.stderr
    assert "example-org/pub" in r.stderr  # no --repo -> the checkout's origin


def test_gh_body_file(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("line one\nline two: zeta-corp\n")
    r = run(f"gh pr comment 1 --body-file {body}", repo, env)
    assert r.returncode == 2, r.stderr
    assert "body:2" in r.stderr


def test_gh_api_input_json_body(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    payload = tmp_path / "anchor.json"
    payload.write_text(json.dumps({"body": "### Verification\nuses acme\n"}))
    r = run(
        f"gh api -X PATCH repos/other/public-thing/issues/comments/5 --input {payload}",
        repo, env,
    )
    assert r.returncode == 2, r.stderr
    assert "body:2" in r.stderr


def test_gh_api_body_field(repo: Path, env: dict[str, str]) -> None:
    r = run("gh api repos/other/public-thing/issues/1/comments -f body='acme here'", repo, env)
    assert r.returncode == 2, r.stderr


def test_gh_api_placeholder_resolves_from_checkout(repo: Path, env: dict[str, str]) -> None:
    r = run("gh api 'repos/{owner}/{repo}/issues/1/comments' -f body='acme here'", repo, env)
    assert r.returncode == 2, r.stderr
    assert "example-org/pub" in r.stderr


@pytest.mark.parametrize(
    "command",
    [
        "gh pr view 1 --repo other/public-thing --comments  # acme",
        "echo acme",
        "grep -rn acme .",
    ],
)
def test_non_write_commands_pass(repo: Path, env: dict[str, str], command: str) -> None:
    r = run(command, repo, env)
    assert r.returncode == 0
    assert gh_calls(env) == 0


def test_gh_write_private_repo_is_silent(repo: Path, env: dict[str, str]) -> None:
    env["FAKE_GH_VIS"] = "private"
    r = run('gh pr create --repo acme/internal --title "acme" --body "acme"', repo, env)
    assert r.returncode == 0
    assert r.stderr == ""


# ---------------------------------------------------------------------------
# Negative control from #1470: names this repo keeps must pass
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message",
    [
        "feat(hooks): route example-dev-hub through orgctl",
        "chore: mcp__slack__slack_send_message fixture",
        "docs: oh-my-claudecode and codex namespaces",
    ],
)
def test_neutral_names_pass(repo: Path, env: dict[str, str], message: str) -> None:
    r = run(f'git commit -m "{message}"', repo, env)
    assert r.returncode == 0, r.stderr
