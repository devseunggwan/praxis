"""Active command-substitution walking for praxis Bash hooks.

`iter_command_texts` yields a command and the inner text of every `$( … )` /
backtick span bash would actually expand, so a gate keying on `argv[0]` can
see the command inside `WS=$(cmux workspace create …)`.

Split out of `_hook_utils.py` in issue #1305; the code moved verbatim.
Imports only `_shell_tokenize`; `_hook_utils` re-exports every name here.
"""
from __future__ import annotations

from _shell_tokenize import strip_heredoc_bodies

# ---------------------------------------------------------------------------
# Active command substitutions (issues #1032, #1035)
# ---------------------------------------------------------------------------
#
# `safe_tokenize` coalesces a `$( … )` / backtick run into one token, so any
# gate that reads `argv[0]` is blind to the command INSIDE the substitution.
# `fan-out-scope-gate` hit that first; `rejected-mutation-reconsent-gate`'s
# dispatch surface hits the identical shape, so the recursion lives here
# rather than in either hook.


MAX_SUBST_DEPTH = 4


def iter_command_texts(command: str, depth: int = 0):
    """Yield `command` and the inner text of every ACTIVE `$( ... )` / backtick.

    A dispatch is routinely written as `WS=$(cmux workspace create ...)`, and
    `safe_tokenize` coalesces a substitution run into ONE token — so a scan of
    the outer text alone sees no command start inside it at all, and every
    gate keying on `argv[0]` goes silent on the substituted form.

    Quoting decides whether a substitution is a substitution. Inside single
    quotes, and after a backslash, `$(` and a backtick are literal text that
    starts no process, so recursing into them would count a workspace that
    never gets created — `printf '%s' '$(cmux workspace create ...)'` prints a
    string. Double quotes do not disable either form, so those are followed.

    A heredoc body is data for the same reason. A script written with
    `python3 - <<'PY' ... PY` puts arbitrary text where a per-line scan
    expects commands, so a fixture that merely spells a command out would be
    read as that command.
    """
    command = strip_heredoc_bodies(command)
    yield command
    if depth >= MAX_SUBST_DEPTH:
        return
    for inner in _active_substitutions(command):
        yield from iter_command_texts(inner, depth + 1)


def _active_substitutions(command: str) -> list[str]:
    """Inner text of each `$( ... )` / backtick span that shell would expand."""
    spans: list[str] = []
    i, n = 0, len(command)
    in_single = in_double = False
    while i < n:
        ch = command[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = not in_double
            i += 1
            continue
        if command.startswith("$(", i):
            end = _closing_paren(command, i + 2)
            if end is not None:
                spans.append(command[i + 2:end])
                i = end + 1
                continue
        elif ch == "`":
            end = _closing_backtick(command, i + 1)
            if end is not None:
                spans.append(command[i + 1:end])
                i = end + 1
                continue
        i += 1
    return spans


def _closing_paren(command: str, start: int) -> int | None:
    """Index of the `)` closing a `$(` opened before `start`, or None.

    Quote state is tracked here too, so a parenthesis inside a quoted string
    (`$(echo ")")`) does not close the span early.
    """
    level = 1
    i, n = start, len(command)
    in_single = in_double = False
    while i < n:
        ch = command[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = True
        elif ch == '"':
            in_double = not in_double
        elif not in_double:
            if command.startswith("$(", i):
                level += 1
                i += 2
                continue
            if ch == "(":
                level += 1
            elif ch == ")":
                level -= 1
                if not level:
                    return i
        i += 1
    return None


def _closing_backtick(command: str, start: int) -> int | None:
    i, n = start, len(command)
    while i < n:
        if command[i] == "\\":
            i += 2
            continue
        if command[i] == "`":
            return i
        i += 1
    return None
