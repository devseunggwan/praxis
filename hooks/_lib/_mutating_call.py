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
from _shell_tokenize import SHELL_KEYWORDS  # type: ignore[import-not-found]  # noqa: E402

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
# `sort`, `uniq`, `find` and `sed` came back through `_ARG_CHECKED` below, the
# way `gh api` is admitted: the arguments are checked, so a write form falls
# through. A session replay showed their absence was not rare.
READONLY_ANY_ARGS = frozenset({
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "egrep", "fgrep",
    "echo", "printf", "jq", "cut", "tr", "column", "diff",
    "file", "stat", "du", "df", "which", "type", "pwd", "env", "ps",
    "whoami", "hostname", "uname", "id", "basename", "dirname", "realpath",
    "true", "false", ":",
})

READONLY_SUBCOMMANDS = {
    # `tag`, `remote`, `worktree` and `config` are absent on purpose: each has
    # a write form one flag away (`git remote add`), and falling through only
    # costs a question. `branch` is admitted in its listing forms only, below.
    "git": frozenset({"log", "status", "diff", "show", "rev-parse", "rev-list",
                      "describe", "blame", "shortlog", "ls-files", "ls-remote",
                      "cat-file", "grep", "branch"}),
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


def _flags_are_readonly(
    args: list[str], *, bool_short: str, value_short: str,
    bool_long: frozenset[str], value_long: frozenset[str], max_operands: int,
) -> bool:
    """True when every flag is a recognised read-only one and the operand count
    stays within `max_operands`. An unknown flag returns False, like `gh api`."""
    operands = 0
    i = 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok == "--":
            operands += len(args) - i
            break
        if tok == "-" or not tok.startswith("-"):
            operands += 1
            continue
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name in bool_long:
                continue
            if name in value_long:
                if "=" not in tok:
                    i += 1
                continue
            return False
        for pos, ch in enumerate(tok[1:], start=1):
            if ch in value_short:
                if pos == len(tok) - 1:
                    i += 1  # the value is the next token
                break
            if ch not in bool_short:
                return False
    return operands <= max_operands


def _sort_is_readonly(argv: list[str]) -> bool:
    # Absent on purpose: `-o`/`--output` write a file, `--compress-program`
    # runs one, `-T`/`--files0-from`/`--random-source` name paths.
    return _flags_are_readonly(
        argv[1:], bool_short="bcCdfghiMnrRsuVz", value_short="ktS",
        bool_long=frozenset({
            "--ignore-leading-blanks", "--check", "--dictionary-order",
            "--ignore-case", "--general-numeric-sort", "--human-numeric-sort",
            "--ignore-nonprinting", "--month-sort", "--numeric-sort",
            "--random-sort", "--reverse", "--stable", "--unique",
            "--version-sort", "--zero-terminated", "--debug",
        }),
        value_long=frozenset({"--key", "--field-separator", "--buffer-size"}),
        max_operands=len(argv),
    )


def _uniq_is_readonly(argv: list[str]) -> bool:
    # A second operand is the output file, so only one is read-only.
    return _flags_are_readonly(
        argv[1:], bool_short="cdDiuz", value_short="fsw",
        bool_long=frozenset({
            "--count", "--repeated", "--all-repeated", "--ignore-case",
            "--unique", "--zero-terminated", "--group",
        }),
        value_long=frozenset({"--skip-fields", "--skip-chars", "--check-chars"}),
        max_operands=1,
    )


_SET_OPTION_NAME_RE = re.compile(r"[a-z]+")


def _set_is_readonly(argv: list[str]) -> bool:
    """`set` with option flags only; operands would reset positional params."""
    i = 1
    while i < len(argv):
        tok = argv[i]
        i += 1
        if len(tok) < 2 or tok[0] not in "-+" or not tok[1:].isalpha():
            return False
        if tok.endswith("o"):  # `-o NAME`, `-euo NAME`: the next token is NAME
            if i >= len(argv) or not _SET_OPTION_NAME_RE.fullmatch(argv[i]):
                return False
            i += 1
    return True


def _cd_is_readonly(argv: list[str]) -> bool:
    # Moves only this shell's cwd; `-L`/`-P` pick how symlinks resolve.
    return _flags_are_readonly(
        argv[1:], bool_short="LP", value_short="", bool_long=frozenset(),
        value_long=frozenset(), max_operands=1,
    )


# The write primaries of BSD find(1) and GNU findutils. A denylist, unlike the
# flag sets above, because find's tests are open-ended while the primaries that
# write or run something are a closed set in both manuals.
_FIND_WRITE_PRIMARIES = frozenset({
    "-delete", "-exec", "-execdir", "-ok", "-okdir",
    "-fprint", "-fprint0", "-fprintf", "-fls",
})


def _find_is_readonly(argv: list[str]) -> bool:
    return not any(tok in _FIND_WRITE_PRIMARIES for tok in argv[1:])


_SED_ADDRESS = r"(?:\d+|\$)(?:,(?:\d+|\$))?"
_SED_PRINT_RE = re.compile(rf"(?:{_SED_ADDRESS})?[pdq=]")
_SED_SUBST_FLAGS_RE = re.compile(r"[gIip0-9]*")


def _sed_subst_is_readonly(script: str) -> bool:
    """`[addr]s<d>re<d>repl<d>flags` with no `w` (write) or `e` (exec) flag."""
    m = re.match(_SED_ADDRESS, script)
    body = script[m.end():] if m else script
    if len(body) < 2 or body[0] != "s" or body[1].isalnum() or body[1] in "\\\n":
        return False
    delim, cur, i = body[1], "", 2
    parts: list[str] = []
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            cur += body[i:i + 2]
            i += 2
            continue
        if ch == delim:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    return len(parts) == 2 and bool(_SED_SUBST_FLAGS_RE.fullmatch(cur))


def _sed_is_readonly(argv: list[str]) -> bool:
    """Only single print/delete/substitute scripts; `-i`, `-f` and the `w`/`e`
    commands write or run something, and a `;`-joined script is not parsed."""
    scripts: list[str] = []
    operands: list[str] = []
    i = 1
    while i < len(argv):
        tok = argv[i]
        i += 1
        if tok in ("-e", "--expression"):
            if i >= len(argv):
                return False
            scripts.append(argv[i])
            i += 1
        elif tok.startswith("--expression="):
            scripts.append(tok.split("=", 1)[1])
        elif tok in ("--quiet", "--silent", "--regexp-extended"):
            continue
        elif tok.startswith("-") and tok != "-":
            if not all(ch in "nEru" for ch in tok[1:]):
                return False
        else:
            operands.append(tok)
    if not scripts:
        if not operands:
            return False
        scripts.append(operands[0])
    return all(_SED_PRINT_RE.fullmatch(s) or _sed_subst_is_readonly(s) for s in scripts)


_ARG_CHECKED = {
    "sort": _sort_is_readonly, "uniq": _uniq_is_readonly, "set": _set_is_readonly,
    "cd": _cd_is_readonly, "find": _find_is_readonly, "sed": _sed_is_readonly,
}

# Listing forms only; any operand or other flag creates, renames or deletes.
_GIT_BRANCH_READ_FLAGS = frozenset({
    "--show-current", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose",
    "--no-color",
})


def _git_branch_is_readonly(argv: list[str]) -> bool:
    rest = argv[argv.index("branch") + 1:]
    return all(tok in _GIT_BRANCH_READ_FLAGS for tok in rest)


def _abbreviates(tok: str, long_flag: str) -> bool:
    # git accepts any unambiguous prefix of a long option (`--open` for
    # `--open-files-in-pager`); four characters clear `--o` and `--on`.
    name = tok.split("=", 1)[0]
    return len(name) >= 4 and long_flag.startswith(name)


def _git_options_are_readonly(argv: list[str], sub: str) -> bool:
    """`--output` writes a file for every admitted diff-family subcommand, and
    `git grep -O` runs its value as a pager command."""
    for tok in argv[argv.index(sub) + 1:]:
        if tok == "--":
            break
        if _abbreviates(tok, "--output"):
            return False
        if sub == "grep" and (
            _abbreviates(tok, "--open-files-in-pager")
            or (tok.startswith("-") and not tok.startswith("--") and "O" in tok[1:])
        ):
            return False
    return True


_ASSIGNMENT_RE = re.compile(r"[A-Za-z_]\w*=")
_LOOP_VAR_RE = re.compile(r"[A-Za-z_]\w*")


def _is_inert_segment(argv: list[str]) -> bool:
    """A segment that runs nothing: only assignments and shell keywords
    (`S=1`, `do`, `done`), or a `for NAME [in WORDS]` header. Substitutions in
    the words are refused before segmenting, so the words are literals."""
    if argv[0] == "for":
        return (len(argv) >= 2 and bool(_LOOP_VAR_RE.fullmatch(argv[1]))
                and (len(argv) == 2 or argv[2] == "in"))
    return all(tok in SHELL_KEYWORDS or _ASSIGNMENT_RE.match(tok) for tok in argv)


def _segment_is_readonly(argv: list[str]) -> bool:
    if argv and _is_inert_segment(argv):
        return True
    argv = strip_prefix(argv)
    if not argv:
        return False
    binary = argv[0].rsplit("/", 1)[-1]
    if binary in READONLY_ANY_ARGS:
        return True
    if binary in _ARG_CHECKED:
        return _ARG_CHECKED[binary](argv)

    allowed = READONLY_SUBCOMMANDS.get(binary)
    if allowed is None:
        return False
    sub = _subcommand(binary, argv)
    if sub is None or sub not in allowed:
        return False

    if binary == "gh" and sub == "api":
        return _gh_api_is_readonly(argv)
    if binary == "git" and sub == "branch":
        return _git_branch_is_readonly(argv)
    if binary == "git":
        return _git_options_are_readonly(argv, sub)
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

# Redirects that write no file: a descriptor duplication (`2>&1`, `>&2`,
# `2>&-`) or `/dev/null`. They are removed before classifying because the
# redirect check reads any `>` as a write and the tokenizer splits `2>&1` at
# the `&`. The lookarounds keep `>&file` and `/dev/null.bak` as writes.
_NONWRITING_REDIRECT_RE = re.compile(
    r"(?<![\w>&])(?:\d*|&)(?:>&(?:\d+|-)|>>?[ \t]*/dev/null)(?=$|[\s;|&)])"
)


def bash_is_readonly(command: str) -> bool:
    """True only when every segment is a recognised read-only invocation."""
    command = _NONWRITING_REDIRECT_RE.sub(" ", command)
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
