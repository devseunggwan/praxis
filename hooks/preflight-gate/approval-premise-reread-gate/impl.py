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
command. An MCP call carries no comment surface and its arguments are the
server's schema to define, so its acknowledgement is written to a hook-owned
file instead, whose path the gate's own message prints, and consumed by
the next call that would
otherwise be asked about. The marker is not a bypass token — it asserts that the
premise was re-read and states what it now says. Attaching it without having
done so is a false attestation.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _paths import resolve_cache_file  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    has_state_changing_redirect,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

ACK_MARKER = "# approval-premise:ack"

# How to attest for a call that has no comment surface. The message prints the
# resolved path rather than the procedure: an opt-out nobody can find is an
# opt-out that does not exist.
def _ack_hint(path: str) -> str:
    return (
        "this call has no comment surface, so the acknowledgement goes in a "
        f"file: write {{\"premise\": \"<one line>\"}} to {path} and re-issue. "
        "It is consumed on read -- one acknowledgement covers one call."
    )

# The acknowledgement is an attestation that the premise was re-read and a
# statement of what it now says, so the statement is the part that matters: a
# bare marker attests nothing. Each surface is parsed only where it is declared
# -- the comment in the Bash command, the argument in the MCP input -- because a
# substring search over serialized input accepts the marker sitting inside an
# unrelated field, which no one wrote as an attestation.
ACK_WORD = ACK_MARKER.split("#", 1)[1].strip()

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
# becomes the noise it is meant to replace. Three known false positives
# survive, all preferred over the miss that silencing them would buy:
# `airflow_import_errors` reads import errors rather than importing, but
# dropping "import" loses `gitbook_git_import`; `mysql_resolve_user_connectors`
# is a query, but dropping "resolve" loses a review-thread resolve; and the
# `merge_readiness_*` state tools are local, but dropping "merge" loses
# `merge_pull_request`, which is irreversible.
#
# The second row is this repository's own write vocabulary rather than a fresh
# reading of the tool surface: `pr-claim-mutation-gate/spec.md` classifies
# `submit`/`resolve`/`dismiss`/`merge` as GitHub MCP writes and
# `pipefail-advisory` adds `close`/`reopen`. A verb this repo already calls a
# mutation must not read as read-only here.
MUTATING_MCP_VERBS = frozenset((
    "trigger", "clear", "mark", "unmark", "delete", "drop", "create", "update",
    "send", "upload", "invite", "kick", "archive", "unarchive", "rename", "set",
    "write", "insert", "append", "remove", "replace", "move", "copy", "edit",
    "add", "reply", "forward", "label", "unlabel", "trash", "untrash", "share",
    "duplicate", "finalize", "redline", "respond", "apply", "convert", "ingest",
    "prune", "cleanup", "cancel", "start", "record", "import", "style",
    "merge", "close", "reopen", "submit", "resolve", "dismiss", "comment",
    "approve", "revoke", "grant", "restore", "enable", "disable", "stop",
    "kill", "terminate", "truncate", "purge", "flush", "reset", "rollback",
    "promote", "backfill", "deploy", "publish", "unpublish", "patch", "upsert",
    "execute",
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
# `find`, `yq`, `sort`, `uniq` and `date` were here and were removed: each
# writes under a flag or a second positional (`find -delete`, `yq -i`,
# `sort -o`, `uniq in out`, `date -s`), so its read-onlyness is a property of
# the arguments rather than of the command. That is not a read-only *shape*,
# and admitting one costs the guarantee the whole allowlist exists to give.
# Their absence costs a question on a prod-marked `find`, which is rare.
READONLY_ANY_ARGS = frozenset({
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "egrep", "fgrep",
    "echo", "printf", "jq", "cut", "tr", "column", "diff",
    "file", "stat", "du", "df", "which", "type", "pwd", "env", "ps",
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
#
# The three sets below are the complete `gh api` flag surface, transcribed from
# `gh api --help`. Completeness is the point: the classifier admits a call only
# when it recognises *every* token, so a flag added by a future gh release falls
# through to ask rather than through the gate.
_GH_API_BODY_FLAGS = frozenset({"-f", "--raw-field", "-F", "--field", "--input"})
_GH_API_METHOD_FLAGS = frozenset({"-X", "--method"})
_GH_API_READ_FLAGS = frozenset({
    "--allow-escape-sequences", "--cache", "-H", "--header", "--hostname",
    "-i", "--include", "-q", "--jq", "--paginate", "-p", "--preview",
    "--silent", "--slurp", "-t", "--template", "--verbose",
})
# Flags that consume the following token when it is not joined with `=`.
_GH_API_VALUE_FLAGS = frozenset({
    "--cache", "-H", "--header", "--hostname", "-q", "--jq", "-p", "--preview",
    "-t", "--template",
})
_AWS_READ_PREFIXES = ("describe-", "get-", "list-", "search-", "query-", "batch-get-")


# Global flags that sit BEFORE the verb and consume the token after them, so
# that token is a value and not the subcommand. Transcribed per binary from
# `kubectl options`, `git --help`, `aws help`, `docker --help` and `gh --help`;
# the boolean set beside it is the flags that consume nothing. A flag in
# neither set is unrecognised, and then the verb's position is unknowable --
# `_subcommand` returns None and the caller asks, which is the safe direction.
_GLOBAL_VALUE_FLAGS = {
    "kubectl": frozenset({
        "--as", "--as-group", "--as-uid", "--cache-dir",
        "--certificate-authority", "--client-certificate", "--client-key",
        "--cluster", "--context", "--kubeconfig", "--kuberc",
        "--log-flush-frequency", "-n", "--namespace", "--password", "--profile",
        "--profile-output", "--request-timeout", "-s", "--server",
        "--tls-server-name", "--token", "--user", "--username", "--vmodule",
    }),
    # `--exec-path` is absent on purpose: `git --help` spells it
    # `--exec-path[=<path>]`, so its value only ever arrives attached.
    "git": frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                      "--config-env"}),
    "gh": frozenset({"-R", "--repo"}),
    "aws": frozenset({"--ca-bundle", "--cli-binary-format", "--cli-error-format",
                      "--color", "--endpoint-url", "--output", "--profile",
                      "--query", "--region"}),
    "docker": frozenset({"--config", "-c", "--context", "-H", "--host",
                         "-l", "--log-level", "--tlscacert", "--tlscert",
                         "--tlskey"}),
}

_GLOBAL_BOOL_FLAGS = {
    "kubectl": frozenset({"--disable-compression", "--insecure-skip-tls-verify",
                          "--match-server-version", "--warnings-as-errors",
                          "-h", "--help"}),
    "git": frozenset({"-v", "--version", "-h", "--help", "--exec-path",
                      "--html-path",
                      "--man-path", "--info-path", "-p", "--paginate", "-P",
                      "--no-pager", "--no-replace-objects", "--no-lazy-fetch",
                      "--no-optional-locks", "--no-advice", "--bare"}),
    "gh": frozenset({"-h", "--help", "--version"}),
    "aws": frozenset({"--cli-auto-prompt", "--no-cli-auto-prompt", "--debug",
                      "--no-cli-pager", "--no-paginate", "--no-sign-request",
                      "--no-verify-ssl", "--version"}),
    "docker": frozenset({"-D", "--debug", "--tls", "--tlsverify", "-v",
                         "--version", "-h", "--help"}),
}


def _subcommand(binary: str, argv: list[str]) -> str | None:
    """The token that decides what the invocation does, or None if unknowable.

    Most of these binaries are verb-first (`kubectl get`, `git log`), but `gh`
    is noun-verb (`gh pr view`), so reading position 1 for it would test the
    noun. Position matters rather than membership: scanning every token would
    read `gh pr create --title view` as read-only because `view` appears in it.

    A global flag before the verb shifts that position, and reading its value
    as the verb is wrong in both directions -- `kubectl --context prod-apne2
    get pods` asks for nothing, and `kubectl --context get delete pod` passes
    as read-only. Flag values are therefore skipped, and an unrecognised flag
    gives up rather than guessing where the verb landed.
    """
    value_flags = _GLOBAL_VALUE_FLAGS.get(binary, frozenset())
    bool_flags = _GLOBAL_BOOL_FLAGS.get(binary, frozenset())
    words: list[str] = []
    i = 1
    while i < len(argv):
        tok = argv[i]
        i += 1
        if not tok.startswith("-"):
            words.append(tok)
            if binary != "gh":
                return words[0]
            if len(words) == 1 and words[0] in ("api", "search", "status"):
                return words[0]
            if len(words) == 2:
                return words[1]
            continue
        name = tok.split("=", 1)[0]
        if name in bool_flags:
            continue  # it takes no value, so the verb is still ahead
        if name in value_flags:
            if "=" not in tok:
                i += 1  # its value is the next token, not the verb
            continue
        return None  # unrecognised, attached value or not
    return None


def _gh_api_is_readonly(argv: list[str]) -> bool:
    """True only for a `gh api` call recognised, token by token, as a query.

    The effective method decides, and it is computed before the body flags are
    judged. A body flag (`-f`, `-F`, `--input`) only *implies* POST, per
    `gh api --help`: with an explicit `--method GET` the same flag becomes a
    query parameter, which is how `gh api --method GET search/issues -f q=...`
    is written. Returning False on sight of the flag asked for approval on a
    read-only query.

    Long options accept `--name=value` and short ones accept an attached value
    (`-fkey=v`, `-XPOST`), so a plain membership test over raw tokens misses
    both. Every token is split before it is classified, and an unrecognised one
    returns False -- the gate then asks, which is the safe direction.
    """
    method = ""
    has_body = False
    i = 1
    while i < len(argv):
        tok = argv[i]
        i += 1
        if not tok.startswith("-"):
            continue  # the endpoint, or a value already consumed below
        name, sep, inline = tok.partition("=")
        if not sep and not tok.startswith("--") and len(tok) > 2:
            # a short flag carrying its value attached: -fkey=v, -XPOST
            name, inline, sep = tok[:2], tok[2:], "="
        if name in _GH_API_BODY_FLAGS:
            has_body = True
            continue  # its value is a non-flag token, skipped like the endpoint
        if name in _GH_API_METHOD_FLAGS:
            if sep:
                method = inline
            elif i < len(argv):
                method, i = argv[i], i + 1
            else:
                return False  # -X with nothing after it; do not guess
            continue
        if name in _GH_API_READ_FLAGS:
            if not sep and name in _GH_API_VALUE_FLAGS:
                i += 1  # its value is the next token, not an endpoint
            continue
        return False
    if not method:
        method = "POST" if has_body else "GET"
    return method.upper() == "GET"


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
    if sub is None or sub not in allowed:
        return False

    if binary == "gh" and sub == "api":
        return _gh_api_is_readonly(argv)
    if binary == "aws":
        # `aws sts get-caller-identity` and its siblings only.
        return any(a.startswith(_AWS_READ_PREFIXES) for a in argv[2:])
    return True


# A substitution runs a command the outer binary's name says nothing about:
# `echo "$(kubectl delete pod x -n prod-apne2)"` reads as an `echo`, and the
# shell still runs the delete. Recognising the inner command would mean parsing
# it, so the allowlist declines instead -- a command carrying any of these is
# not a read-only *shape*, whatever sits inside it, and falls through to ask.
# `$((` is arithmetic, not a substitution, and costs an ask for nothing.
_SUBSTITUTION_RE = re.compile(r"\$\((?!\()|`|<\(|>\(")


def _bash_is_readonly(command: str) -> bool:
    """True only when every segment is a recognised read-only invocation."""
    if has_state_changing_redirect(command):
        return False
    if _SUBSTITUTION_RE.search(command):
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


def _message(tool_name: str, target: str, session_id: str = "") -> str:
    answer = _ANSWER_BOTH
    if tool_name.startswith("mcp__") and session_id:
        answer = f"{_ANSWER_BOTH} {_ack_hint(_ack_file(session_id))}"
    return format_block(
        rule_name="approval premise re-read",
        why="an approval covers the option, not an option whose premise has "
            f"since dissolved -- {tool_name} names a production target "
            f"({target or 'unnamed'})",
        correct_path=answer,
        # The acknowledgement comment is an attestation the agent writes after
        # actually re-reading, not an env-var bypass; an env var would let the
        # session disable the gate for itself.
        bypass_env=None,
        reference="hooks/preflight-gate/approval-premise-reread-gate/spec.md",
    )


def _bash_ack(command: str) -> bool:
    """True when the command's FIRST comment is the marker plus a premise.

    Position decides, not presence, and three positions are wrong. A quoted
    occurrence is data: `safe_tokenize` keeps it inside the token it belongs
    to, so `-f body='# approval-premise:ack ...'` -- a request body -- never
    looks like a comment opener. A marker inside an *earlier* comment is prose
    about the marker (`# note # approval-premise:ack ...`), so only the first
    opener is read. And a marker on an earlier line attests for that line, not
    for a mutation below it, so nothing may follow the comment: the tokenizer
    ends a line with `;`, and a `;` after the marker means a command comes
    next.
    """
    tokens = safe_tokenize(command)
    idx = next((i for i, tok in enumerate(tokens) if tok.startswith("#")), None)
    if idx is None:
        return False
    opener = tokens[idx]
    if opener == "#":
        word, premise_at = (tokens[idx + 1] if idx + 1 < len(tokens) else ""), idx + 2
    else:
        word, premise_at = opener[1:], idx + 1
    if word != ACK_WORD:
        return False
    if ";" in tokens[premise_at:]:
        return False
    return bool(" ".join(tokens[premise_at:]).strip())


def _ack_file(session_id: str) -> str:
    return resolve_cache_file(f"approval-premise-ack-{session_id}.json",
                              session_id=session_id)


def _mcp_ack(session_id: str) -> bool:
    """True when this session left a premise in the hook-owned ack file.

    An MCP call has no comment surface, and its arguments belong to the
    server's schema: a synthetic `approval_premise_ack` field is rejected
    outright by a server that validates its input, so the quiet path it
    described was not reachable at runtime for any such tool.

    The file is consumed on read. One acknowledgement therefore covers one
    call, which is what keeps it an attestation rather than a session-wide
    off switch -- there is no shape of this file that disables the gate.
    """
    if not session_id:
        return False
    path = _ack_file(session_id)
    try:
        with open(path, encoding="utf-8") as fh:
            premise = (json.load(fh) or {}).get("premise")
    except Exception:
        return False  # absent, unreadable or malformed: the gate asks
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return isinstance(premise, str) and bool(premise.strip())


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail open — a malformed payload must not block the session

    session_id = str(payload.get("session_id") or "")
    tool_name = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input", {}) or {}
    blob = json.dumps(tool_input, ensure_ascii=False)

    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
        if _bash_ack(command):
            return 0
        if not _carries_prod_marker(command):
            return 0
        if _bash_is_readonly(command):
            return 0
        target = command.strip().splitlines()[0][:120]
    elif _is_mutating_mcp(tool_name):
        # Marker first: the ack file is consumed on read, so testing it before
        # this would let an unrelated non-production mutation eat the premise
        # written for the production call that is about to be re-issued.
        if not _carries_prod_marker(blob):
            return 0
        if _mcp_ack(session_id):
            return 0
        target = str(tool_input.get("dag_id") or tool_input.get("conf") or "")[:120]
    else:
        return 0

    emit_decision("ask", _message(tool_name, target, session_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
