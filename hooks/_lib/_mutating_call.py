"""Classify a tool call as mutating or read-only.

Extracted from `preflight-gate/approval-premise-reread-gate` (issue #1490) so
that gates keyed on "a mutating call" share one answer instead of each carrying
its own copy. The direction is fail-closed toward "mutating": a Bash command is
read-only only when every segment is a recognised read-only invocation, so an
unrecognised command costs a question rather than slipping through.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent))
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    has_state_changing_redirect,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

FILE_EDIT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})

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
# already names — `kubectl get|describe|logs`, AWS describe/get/list — plus the
# inspection commands that carry no side effect. Bare names take any argument; a
# name mapped to a set is read-only only under those subcommands.
#
# Author- or org-internal CLIs are deliberately absent and get no extension env
# (issue #1470). The sibling `PRAXIS_SECRET_FETCH_CLIS` widens a *detector*, so
# an extra entry only adds advisories; widening THIS list removes questions, and
# the spec already refuses a bypass env on that ground. An internal CLI
# therefore falls through and costs one question, which is what the paragraph
# above says the fall-through is for.
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


def bash_is_readonly(command: str) -> bool:
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


def mcp_is_mutating(tool_name: str) -> bool:
    if not tool_name.startswith("mcp__"):
        return False
    leaf = tool_name.rsplit("__", 1)[-1]
    return bool(set(re.split(r"[_\-]", leaf.lower())) & MUTATING_MCP_VERBS)


def is_mutating_call(tool_name: str, tool_input: dict) -> bool:
    """True when the call can change state: a Bash command that is not provably
    read-only, a file edit, or an MCP tool whose name carries a write verb."""
    if tool_name == "Bash":
        return not bash_is_readonly(str(tool_input.get("command") or ""))
    if tool_name in FILE_EDIT_TOOLS:
        return True
    return mcp_is_mutating(tool_name)
