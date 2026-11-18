"""Tests for hooks/_lib/_mutating_call.py — shared mutating-call classifier (#1490)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "hooks" / "_lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from _mutating_call import (  # noqa: E402
    bash_is_readonly,
    drop_prose_values,
    is_mutating_call,
    mcp_is_mutating,
)


@pytest.mark.parametrize("command", [
    "git status",
    "git log --oneline -5",
    "gh pr list --repo o/r",
    "gh api repos/o/r/pulls",
    "gh api --method GET search/issues -f q=x",
    "kubectl get pods -n x | grep Running",
])
def test_readonly_bash_is_not_mutating(command):
    assert bash_is_readonly(command)
    assert not is_mutating_call("Bash", {"command": command})


@pytest.mark.parametrize("command", [
    "git push origin HEAD",
    "gh pr create --title t --body b",
    "gh api -X POST repos/o/r/issues",
    "gh api repos/o/r/issues -f title=t",
    "echo hi > out.txt",
    "echo $(rm -rf x)",
    "orgctl token fetch x",
    "git status && git push",
])
def test_writing_or_unknown_bash_is_mutating(command):
    assert not bash_is_readonly(command)
    assert is_mutating_call("Bash", {"command": command})


@pytest.mark.parametrize("command", [
    # assignments, no-op builtins and `set` options change no state
    "S=1",
    "S=1 T=2; git status",
    "true",
    "false",
    ":",
    "set",
    "set +x",
    "set -o pipefail",
    "set -euo pipefail; git log -1",
    # redirects that write no file
    "git log 2>&1",
    "git log >&2",
    "git log 2>/dev/null",
    "git log 2> /dev/null | head",
    "git log >/dev/null 2>&1",
    "git log &>/dev/null",
    # a loop whose body is read-only
    "for r in a b; do echo $r; done",
    "for r in a; do\n gh pr list --repo o/$r\ndone",
    "if true; then git status; fi",
    # sort and uniq with read-only flags
    "sort f",
    "sort -k2,2n -t, f g",
    "sort -nru f",
    "sort --key=2 --reverse f",
    "uniq f",
    "uniq -c",
    "uniq -c -f 2 f",
    "grep x f | sort | uniq -c",
    # cwd moves, listing and search forms
    "cd /x && git status",
    "git status; cd -",
    "cd",
    "git branch",
    "git branch --show-current",
    "git -C /x branch -a -v",
    "git grep -n foo",
    "git grep --no-open-files-in-pager -e a",
    "git grep -e a -- -O",
    "git log --oneline -5",
    "sed -n 1,5p f",
    "sed -n '$p' f",
    "sed 's|^|  |' f",
    "sed -E -e 's/a\\/b/c/g' f g",
    "grep x f | sed 3q",
    "find . -name x",
    "find . -type f -name '*.py' -print",
])
def test_readonly_shell_forms_are_not_mutating(command):
    assert bash_is_readonly(command)


@pytest.mark.parametrize("command", [
    "S=1 git push",
    "S=$(rm -rf x)",
    "true && git push",
    "set x",
    "set -- a b",
    "set -o",
    "echo x >&out.txt",
    "git log 2>err.txt",
    "git log > /dev/null.bak",
    "git log 2>&1file",
    "for r in a; do git push; done",
    "for r in a; do echo x > $r; done",
    "for ((i=0;i<3;i++)); do echo; done",
    "done > f",
    "sort -o out f",
    "sort -no out f",
    "sort --output=out f",
    "sort --compress-program=gzip f",
    "sort -T /tmp f",
    "uniq a b",
    "uniq -c a b",
    "cd a b",
    "cd -x /y",
    "cd /x && rm f",
    "git branch new",
    "git branch -D old",
    "git branch -m a b",
    "git branch --list x",
    "sed -i s/a/b/ f",
    "sed -i.bak -n 1p f",
    "sed --in-place s/a/b/ f",
    "sed -f script f",
    "sed 's/a/b/w out' f",
    "sed 's/a/b/e' f",
    "sed '1w out' f",
    "sed 's/a/b/; 1d' f",
    "sed -n",
    "find . -delete",
    "find . -name x -exec rm {} +",
    "find . -execdir echo {} ;",
    "find . -fprint out",
    "find . -ok rm {} ;",
    "git grep -Ocmd -e a",
    "git grep -O cmd -e a",
    "git grep -nO -e a",
    "git grep --open-files-in-pager=cmd -e a",
    "git grep --open -e a",
    "git log -1 --output=o.txt",
    "git diff --output o.txt",
    "git show --outp=o HEAD",
    "git -C /x log --output=o",
])
def test_write_forms_beside_them_stay_mutating(command):
    assert not bash_is_readonly(command)


def test_empty_bash_command_is_mutating():
    # Nothing recognised is not a read-only shape; fail toward asking.
    assert is_mutating_call("Bash", {"command": ""})
    assert is_mutating_call("Bash", {})


@pytest.mark.parametrize("tool", ["Edit", "Write", "NotebookEdit"])
def test_file_edit_tools_are_mutating(tool):
    assert is_mutating_call(tool, {"file_path": "/x"})


@pytest.mark.parametrize("tool,expected", [
    ("mcp__github__create_pull_request", True),
    ("mcp__slack__slack_send_message", True),
    ("mcp__github__merge_pull_request", True),
    ("mcp__github__list_labels", False),
    ("mcp__trino__trino_query", False),
    ("mcp__s3__s3_count_records", False),
    # issue #1659: `import_errors` is a noun phrase, not the import verb
    ("mcp__airflow__airflow_import_errors", False),
    ("mcp__gitbook__gitbook_git_import", True),
    ("mcp__airflow__airflow_import_errors_clear", True),
    ("mcp__x__reimport_errors", False),
])
def test_mcp_by_write_verb(tool, expected):
    assert mcp_is_mutating(tool) is expected
    assert is_mutating_call(tool, {}) is expected


@pytest.mark.parametrize("tool", ["Read", "Grep", "Glob", "WebFetch"])
def test_non_mcp_read_tools_are_not_mutating(tool):
    assert not is_mutating_call(tool, {})


# issue #1659: kubectl `config` and `auth` are read-only only under their
# read verbs, and only when that verb directly follows the subcommand.
@pytest.mark.parametrize("command", [
    "kubectl config view --minify",
    "kubectl --context prod-x config get-contexts",
    "kubectl config current-context",
    "kubectl --context prod-x -n jobs auth can-i delete jobs",
    "kubectl auth whoami",
])
def test_kubectl_config_and_auth_reads(command):
    assert bash_is_readonly(command)


@pytest.mark.parametrize("command", [
    "kubectl config use-context prod-x",
    "kubectl config set-context --current --namespace a",
    "kubectl config",
    "kubectl auth reconcile -f rbac.yaml",
    "kubectl --context auth auth reconcile -f r.yaml",
    "kubectl config -h view",
])
def test_kubectl_config_and_auth_writes(command):
    assert not bash_is_readonly(command)


def test_prose_values_are_dropped_only_after_prose_flags():
    argv = ["tool", "--reason", "a prod-x b", "--context", "prod x", "--note=c d", "-m", "nospace",
            "-c", "rm -rf /"]
    assert drop_prose_values(argv) == ["tool", "--reason", "--context", "prod x", "-m", "nospace",
                                       "-c", "rm -rf /"]


@pytest.mark.parametrize("value", ["$(kubectl delete ns production)", "`kubectl delete ns x`"])
def test_a_prose_value_that_runs_a_command_is_kept(value):
    # The shell runs the substitution; only literal text is prose.
    argv = ["git", "commit", "-m", value]
    assert drop_prose_values(argv) == argv


def test_stderr_pipe_is_not_read_only():
    # `|&` pipes into the next command; it is not split by the segment walk.
    assert not bash_is_readonly("kubectl get configmap plan -o yaml |& kubectl apply -f -")
