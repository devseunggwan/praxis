#!/usr/bin/env python3
"""PreToolUse guard: an irreversible production call re-reads its approval premise.

Two failures this gate exists for, both observed in one session minutes apart
(issue #1043), neither reachable by any existing hook:

1. PREMISE_DISSOLVED — approval was granted on a stated justification ("we have
   to run it to see whether the failing step passes"). Between the approval and
   the execution, a direct query showed the premise no longer held, and that
   observation was written up in the same turn. The call fired anyway.

2. COHORT_INHERITED — a blast radius measured empirically on the first target of
   a cohort was inherited by the rest without re-measurement. The third target
   was a different failure mode whose deletion steps had never executed, so two
   of the three axes were unmeasured for it.

Detection: fires on a mutating call (MCP tool classified as a mutation, or a
Bash segment invoking one of the mutating wrappers) whose arguments carry a
production phase marker. On a match it emits permissionDecision "ask" carrying
the two questions, rather than a hard block — the gate cannot decide whether the
premise still holds; only the operator can, and forcing that decision to be made
out loud at the call site is the whole mechanism.

Known ceiling, stated here rather than discovered later: this gate checks that an
approval record exists, names a justification, and names THIS target. It cannot
check that the justification is TRUE. Routing a guess through a schema check
converts it into something that reads like independent confirmation, which is its
own documented failure mode — see `Own-greencheck and SUT-comment are not
evidence` in the rules. The reach here is partial by construction.

Opt-out: embed `# approval-premise:ack <one-line premise re-read>` in the Bash
command, or pass `approval_premise_ack` in an MCP call's arguments. The marker is
not a bypass token — it asserts that the premise was re-read and states what it
now says. Attaching it without having done so is a false attestation.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    has_state_changing_redirect,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

ACK_MARKER = "# approval-premise:ack"
ACK_ARG = "approval_premise_ack"

# A production phase marker arrives as a flag value, an MCP argument, or a
# quoted identifier. The named flags absorb the spacing and casing variants a
# literal list cannot (`--phase=prod`, `--phase  prod`, `-p PROD`); the quoted
# forms catch a serialized MCP argument.
#
# Bare `production` is deliberately absent. The read-only filter below narrows
# the Bash branch but is not exhaustive, so a marker that common on read-only
# commands (`kubectl get pods -n production`) would lean on the filter for
# every namespace query and turn this gate into the noise it exists to replace.
PROD_MARKER_RE = re.compile(
    r"""(?ix)
      (?: --phase | --profile | --env(?:ironment)? | -p ) \s*[=\s]\s* prod\w*
    | \bphase \s*=\s* ["']? prod \b
    | ["'] prod ["']
    | \bprod-
    """
)

# Mutating MCP verbs, matched against the leaf name's `_`/`-` separated tokens
# rather than as substrings. Substring matching classified eight read-only tools
# as mutations on this session's 374-tool surface -- `list_labels` via "label",
# `s3_count_records` via "record", `figma_get_component_sets` via "set" -- and
# token matching removed all eight while losing no true positive.
#
# Read-only calls are out of scope entirely: a gate that fires on a query
# becomes the noise it is meant to replace. One known false positive survives,
# `airflow_import_errors`, which reads import errors rather than importing;
# dropping "import" to silence it would lose `gitbook_git_import`, a real
# mutation, so the miss is preferred over the drop.
MUTATING_MCP_VERBS = frozenset((
    "trigger", "clear", "mark", "unmark", "delete", "drop", "create", "update",
    "send", "upload", "invite", "kick", "archive", "unarchive", "rename", "set",
    "write", "insert", "append", "remove", "replace", "move", "copy", "edit",
    "add", "reply", "forward", "label", "unlabel", "trash", "untrash", "share",
    "duplicate", "finalize", "redline", "respond", "apply", "convert", "ingest",
    "prune", "cleanup", "cancel", "start", "record", "import", "style",
))


# A Bash command is left alone only when every segment of it is *provably*
# read-only. The direction matters more than the coverage: an unrecognised
# command falls through to "not read-only" and the gate asks, so a gap here
# costs one question, while a mutation-allowlist would have gone silent on the
# call it exists to catch.
#
# The entries mirror the sanctioned read-only production calls the rule set
# already names — `kubectl get|describe|logs`, `hubctl token fetch`, Trino
# SELECT, AWS describe/get/list — plus the inspection commands that carry no
# side effect. Bare names take any argument; a name mapped to a set is read-only
# only under those subcommands.
READONLY_ANY_ARGS = frozenset({
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "egrep", "fgrep", "find",
    "echo", "printf", "jq", "yq", "sort", "uniq", "cut", "tr", "column", "diff",
    "file", "stat", "du", "df", "which", "type", "pwd", "date", "env", "ps",
    "whoami", "hostname", "uname", "id", "basename", "dirname", "realpath",
})

READONLY_SUBCOMMANDS = {
    # `branch`, `tag`, `remote`, `worktree` and `config` are absent on purpose:
    # each has a write form one flag away (`git branch -D`, `git remote add`),
    # and falling through only costs a question.
    "git": frozenset({"log", "status", "diff", "show", "rev-parse", "rev-list",
                      "describe", "blame", "shortlog", "ls-files", "ls-remote",
                      "cat-file"}),
    "gh": frozenset({"view", "list", "status", "checks", "diff", "search", "api"}),
    "kubectl": frozenset({"get", "describe", "logs", "top", "explain", "api-resources"}),
    "aws": frozenset({"sts"}),
    "docker": frozenset({"ps", "images", "inspect", "logs"}),
    "hubctl": frozenset({"token"}),
}

# `gh api` and `aws` reach every verb the service has, so the subcommand alone
# does not settle them: a write is one flag away. Both are read-only only in
# their query shapes.
_GH_API_WRITE_FLAGS = frozenset({"-X", "--method", "-f", "-F", "--input"})
_AWS_READ_PREFIXES = ("describe-", "get-", "list-", "search-", "query-", "batch-get-")


def _subcommand(binary: str, argv: list[str]) -> str:
    """The token that decides what the invocation does.

    Most of these binaries are verb-first (`kubectl get`, `git log`), but `gh`
    is noun-verb (`gh pr view`), so reading position 1 for it would test the
    noun. Position matters rather than membership: scanning every token would
    read `gh pr create --title view` as read-only because `view` appears in it.
    """
    non_flags = [a for a in argv[1:] if not a.startswith("-")]
    if not non_flags:
        return ""
    if binary == "gh":
        if non_flags[0] in ("api", "search", "status"):
            return non_flags[0]
        return non_flags[1] if len(non_flags) > 1 else ""
    return non_flags[0]


def _segment_is_readonly(argv: list[str]) -> bool:
    argv = strip_prefix(argv)
    if not argv:
        return False
    binary = argv[0].rsplit("/", 1)[-1]
    if binary in READONLY_ANY_ARGS:
        return True

    allowed = READONLY_SUBCOMMANDS.get(binary)
    if allowed is None:
        return False
    sub = _subcommand(binary, argv)
    if sub not in allowed:
        return False

    if binary == "gh" and sub == "api":
        # A GET is the default; anything naming a method or a body is a write.
        for i, tok in enumerate(argv):
            if tok in _GH_API_WRITE_FLAGS:
                if tok in ("-X", "--method") and argv[i + 1:i + 2] == ["GET"]:
                    continue
                return False
            if tok.startswith("--method="):
                return tok == "--method=GET"
        return True
    if binary == "aws":
        # `aws sts get-caller-identity` and its siblings only.
        return any(a.startswith(_AWS_READ_PREFIXES) for a in argv[2:])
    return True


def _bash_is_readonly(command: str) -> bool:
    """True only when every segment is a recognised read-only invocation."""
    if has_state_changing_redirect(command):
        return False
    tokens = safe_tokenize(command)
    if not tokens:
        return False
    segments = list(iter_command_starts(tokens))
    return bool(segments) and all(_segment_is_readonly(s) for s in segments)


def _is_mutating_mcp(tool_name: str) -> bool:
    if not tool_name.startswith("mcp__"):
        return False
    leaf = tool_name.rsplit("__", 1)[-1]
    return bool(set(re.split(r"[_\-]", leaf.lower())) & MUTATING_MCP_VERBS)


def _carries_prod_marker(blob: str) -> bool:
    return PROD_MARKER_RE.search(blob) is not None


# The two questions are the whole point of the gate, so they live in the
# `correct_path` field the shared renderer prints under "Do this instead".
_ANSWER_BOTH = (
    "answer both before this executes -- "
    "(1) PREMISE: restate in one line the justification the approval was granted "
    "on, and say whether anything observed SINCE has made it false; if it has, "
    "this is not an approved action any more, so re-ask. "
    "(2) TARGET: was the blast radius measured on THIS target, or inherited from "
    "another member of the same cohort? An enumeration measured on target A is "
    "not evidence about target B, and it covers the outward side-effect axis "
    "(mail, webhook, channel post, customer notification), not only the data "
    "surfaces."
)


def _message(tool_name: str, target: str) -> str:
    return format_block(
        rule_name="approval premise re-read",
        why="an approval covers the option, not an option whose premise has "
            f"since dissolved -- {tool_name} names a production target "
            f"({target or 'unnamed'})",
        correct_path=_ANSWER_BOTH,
        # The acknowledgement comment is an attestation the agent writes after
        # actually re-reading, not an env-var bypass; an env var would let the
        # session disable the gate for itself.
        bypass_env=None,
        reference="hooks/preflight-gate/approval-premise-reread-gate/spec.md",
    )


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail open — a malformed payload must not block the session

    tool_name = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input", {}) or {}
    blob = json.dumps(tool_input, ensure_ascii=False)

    if tool_input.get(ACK_ARG) or ACK_MARKER in blob:
        return 0

    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
        if ACK_MARKER in command:
            return 0
        if not _carries_prod_marker(command):
            return 0
        if _bash_is_readonly(command):
            return 0
        target = command.strip().splitlines()[0][:120]
    elif _is_mutating_mcp(tool_name):
        if not _carries_prod_marker(blob):
            return 0
        target = str(tool_input.get("dag_id") or tool_input.get("conf") or "")[:120]
    else:
        return 0

    emit_decision("ask", _message(tool_name, target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
