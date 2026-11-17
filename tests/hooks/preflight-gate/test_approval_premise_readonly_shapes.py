"""approval-premise-reread-gate: calls that name production but act on nothing there.

Each REAL case is a call that asked on one install (shape kept, internal names
replaced): a read-only query payload run through a local wrapper, a tracker
whose prose option quotes a production name, `kubectl config` / `auth can-i`,
a `--help` call, and the `airflow_import_errors` MCP read. Each STILL_ASKS case
is the nearest shape that does reach production and must keep asking.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
IMPL = REPO_ROOT / "hooks" / "preflight-gate" / "approval-premise-reread-gate" / "impl.py"


def _verdict(tool_name: str, tool_input: Mapping[str, object], tmp_path: Path) -> str:
    payload = json.dumps({"session_id": "t", "tool_name": tool_name, "tool_input": dict(tool_input)})
    proc = subprocess.run(
        [sys.executable, str(IMPL)], input=payload, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PRAXIS_HOME": str(tmp_path)}, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return "ask" if '"permissionDecision": "ask"' in proc.stdout else "quiet"


def _bash(command: str) -> tuple[str, dict[str, object]]:
    return "Bash", {"command": command}


SELECT_PAYLOAD = '{"phase":"prod","query":"SELECT data_source_id, count(*) AS n FROM cat.vendor.ad_asset_src WHERE data_source_id = 5250 GROUP BY 1 ORDER BY 1"}'


def _wrapper(payload: str, tool: str = "lakehouse_query") -> str:
    return f"python3 lh.py {tool} '{payload}'"


REAL = {
    "select payload through a wrapper, piped to tee": _bash(
        f"cd ~/work/v1613 && {_wrapper(SELECT_PAYLOAD)} | tee q1.txt; echo; "
        + _wrapper('{"phase":"prod","query":"SELECT count(*) AS n FROM iceberg.t1.commerce_item_order"}')
        + " | tee pos.txt"),
    "select payload with IN list": _bash(
        _wrapper('{"phase":"prod","query":"SELECT id, user_id FROM mysql_ro.app.data_source_v2 WHERE id IN (5250, 7347)"}')
        + " | tee uid.txt"),
    "tracker --reason quoting prod- names": _bash(
        "~/bin/tracker verify rearm v1776 --not-before \"11/19 10:00\" --reason "
        "'0 targets: connections 19129 and 19130 came from the admin path (secret prod-19129 05:29Z, prod-19130 05:40Z)'"
        "; ~/bin/tracker show 1776 | grep -E 'start|rearm' | head -2"),
    "tracker --evidence twice, then a gh api read": _bash(
        "~/bin/tracker verify result v2117 --pass --report ~/work/result.md --evidence "
        "'Deploy Prod run 44015347623 success, image prod-53e5c54 pushed' | head -1; "
        "~/bin/tracker done 2117 --evidence 'PASS: image prod-53e5c54 pushed 13:25' | head -1; "
        "gh api repos/o/r/issues/1/comments --jq '.[].id'"),
    "tracker --note with prose": _bash(
        '~/bin/notify --to "pr:https://github.com/o/r/pull/1" --link https://github.com/o/r/pull/1 '
        '--note "rep check: the prod-x kubeconfig uses a profile env, not a grant change"'),
    "assignment naming a prod doc, grep, then cli --help": _bash(
        'F=~/docs/airflow-prod-operations.md; grep -n "preview-delete\\|preview-dry-run" $F | head; '
        "ls ~/go/bin/orgctl && ~/go/bin/orgctl preview --help 2>&1 | head -30"),
    "kubectl config view on prod names": _bash(
        "kubectl config view -o jsonpath='{.users[*].name}' 2>&1 | grep -iE \"prod\" | head -5; "
        "kubectl config get-contexts 2>&1 | grep -E prod-apne2"),
    "kubectl auth can-i on a prod context": _bash(
        "kubectl --context prod-apne2 -n jobs auth can-i delete jobs; kubectl --context prod-apne2 auth whoami"),
    "mcp airflow_import_errors on prod": ("mcp__airflow__airflow_import_errors", {"phase": "prod"}),
}

STILL_ASKS = {
    "delete payload": _bash(_wrapper('{"phase":"prod","query":"DELETE FROM t WHERE id = 1"}')),
    "select into": _bash(_wrapper('{"phase":"prod","query":"SELECT * INTO t2 FROM t"}')),
    "second statement": _bash(_wrapper('{"phase":"prod","query":"SELECT 1; DROP TABLE t"}')),
    "explain analyze": _bash(_wrapper('{"phase":"prod","query":"EXPLAIN ANALYZE INSERT INTO t VALUES (1)"}')),
    "with ... delete": _bash(_wrapper('{"phase":"prod","query":"WITH x AS (SELECT 1) DELETE FROM t"}')),
    "extra payload key": _bash(_wrapper('{"phase":"prod","query":"SELECT 1","action":"purge"}')),
    "nested payload value": _bash(_wrapper('{"phase":"prod","query":"SELECT 1","limit":{"x":1}}')),
    "write verb in the wrapper's tool word": _bash(_wrapper('{"phase":"prod","query":"SELECT 1"}', "lakehouse_delete")),
    "unterminated literal": _bash(_wrapper('{"phase":"prod","query":"SELECT 1 WHERE a = ' + "'\\''" + 'x"}')),
    "prose, then a real prod target": _bash("tracker note --reason 'x y' && kubectl --context prod-x delete pod p"),
    "quoted --context value": _bash("kubectl --context 'prod-x' delete pod p"),
    "ssh remote program": _bash("ssh bastion 'kubectl --context prod-x delete pod p'"),
    "sh -c program": _bash("sh -c 'kubectl --context prod-x delete pod p'"),
    "--command program": _bash("gcloud compute ssh vm --command 'orgctl deploy --phase prod'"),
    "prose flag value without whitespace": _bash("orgctl deploy --note prod-x"),
    "--help as a flag value": _bash("orgctl deploy --phase prod --message --help"),
    "--help not last": _bash("orgctl deploy --help --phase prod"),
    "kubectl config use-context": _bash("kubectl config use-context prod-apne2"),
    "kubectl config set-context": _bash("kubectl --context prod-x config set-context --current --namespace a"),
    "kubectl auth reconcile": _bash("kubectl --context prod-x auth reconcile -f rbac.yaml"),
    "flag value spelled like the subcommand": _bash("kubectl --context auth auth reconcile -f r.yaml --profile prod"),
    "mcp gitbook_git_import": ("mcp__gitbook__gitbook_git_import", {"phase": "prod"}),
    "mcp verb after the phrase": ("mcp__airflow__airflow_import_errors_clear", {"phase": "prod"}),
}

# A payload whose literal holds a write word is still a read: the literal is
# removed before the keyword scan.
LITERAL_WRITE_WORD = _bash(
    _wrapper('{"phase":"prod","query":"SELECT id FROM t WHERE note = ' + "'\\''drop'\\''" + ' -- delete"}'))


@pytest.mark.parametrize("name", sorted(REAL))
def test_real_read_only_calls_are_quiet(name: str, tmp_path: Path) -> None:
    assert _verdict(*REAL[name], tmp_path) == "quiet"


@pytest.mark.parametrize("name", sorted(STILL_ASKS))
def test_nearest_production_shapes_still_ask(name: str, tmp_path: Path) -> None:
    assert _verdict(*STILL_ASKS[name], tmp_path) == "ask"


def test_write_word_inside_a_literal_is_a_read(tmp_path: Path) -> None:
    assert _verdict(*LITERAL_WRITE_WORD, tmp_path) == "quiet"


def test_message_covers_a_call_without_an_approval(tmp_path: Path) -> None:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "orgctl deploy --phase prod"}})
    out = subprocess.run([sys.executable, str(IMPL)], input=payload, capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "PRAXIS_HOME": str(tmp_path)}, check=False).stdout
    assert "If no approval was given" in out
