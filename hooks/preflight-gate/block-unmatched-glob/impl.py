#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block a command whose glob matches nothing.

The Bash tool runs the user's login shell. When that is zsh, an unmatched glob
aborts the ENTIRE command at expansion time ("no matches found") instead of
falling back to the literal pattern the way bash does. Two consequences make
this a correctness hazard rather than noise:

  1. `2>/dev/null` does not suppress it — the error is emitted by the shell's
     expansion stage, not by the command, so the redirect never applies.
  2. The command never runs, so "no output" is indistinguishable from "ran and
     found nothing". A false negative then reads as an established fact.

Design: the verdict is delegated to zsh itself rather than re-implemented.
Each candidate pattern is replayed as `zsh -f -c 'setopt nomatch; : <span>'`,
where `:` is the no-op builtin — the only thing that happens is glob
expansion, and the answer comes from the real shell's semantics. A pure-Python
model was tried first and rejected: it disagreed with live zsh across seven
independent axes (`**`, brace expansion, mixed quoting inside one word,
qualifier placement, `noglob` / `setopt`, variable expansion, and shell syntax
words such as `[`), because those are shell semantics, not pattern syntax.

The gate judges only a **single simple command**. Everything else is passed
through, because a word's meaning there depends on grammar the hook does not
model:

  * a word holding a dynamic expansion (`$var`, `$(...)`, backticks) — its
    value is unknown before the shell resolves it, so the filesystem question
    is unanswerable for that word. Only that word is skipped; a literal glob
    beside it is still judged. A dynamic word in command position passes the
    whole command, since `$CMD` may be `noglob` or `cd`.
  * compound structure (`&&`, `||`, `&`, heredoc, backslash-newline) — a later
    segment may run in a different cwd, or not run at all, and a quoted heredoc
    body is never glob-expanded. `;`, `|`, and a plain newline are cut into
    segments instead, each judged alone.
  * everything from the first control-flow word or function body on, since
    that body may run zero times; and `cd` words, for the cwd reason above
  * assignment words (`FOO=*.x`), whose values zsh does not glob-expand
  * commands that disable the failure themselves (`noglob`, `setopt`,
    `unsetopt`, `eval`)
  * shell-syntax words (`[`, `[[`) and comment bodies, which are not pathname
    patterns at all
  * patterns carrying a zsh glob qualifier — `*(e:'cmd':)` executes `cmd`, and
    a preflight gate must never run the command it is inspecting
  * every command, when the executing shell is not zsh (`$SHELL`) — bash and
    fish pass the literal pattern to the command, which then runs
  * every command, when the executing shell has `nullglob` / `nonomatch` /
    `noglob` set, because then nothing aborts in the first place

This trades recall for precision deliberately: a blocking gate that halts a
valid command is worse than one that misses a case.

The candidate span keeps its original source text — quotes and any trailing
zsh qualifier included — so mixed quoting (`ARCH*".*"`) and per-occurrence
qualifiers (`*.x(N); *.x`) are judged exactly as written.

Exits 2 (PreToolUse blocking code) when a live command carries a zero-match
glob. Exits 0 otherwise (transparent pass-through).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys as _sys
import time
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    budgeted_deadline,
    fail_open,
)
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402

GLOB_CHARS = "*?["

# Stands in for a character the shell will not interpret. NUL cannot appear in
# a command string, so masking never collides with real input.
_MASK = "\x00"

# Word separators. `<` and `>` are included so a redirect never reaches the
# zsh probe as part of a candidate span — `: >out` would create a file.
# Parentheses are deliberately NOT separators: a trailing zsh glob qualifier
# (`*.log(N)`) must stay attached to its pattern, or the span replayed to zsh
# loses the very thing that makes zero matches legal. A subshell's `*)` reaches
# the probe as a syntax error, which is not a `no matches found` verdict.
_SEPARATORS = set("&|;\n<>")

# The characters zsh splits words on. `str.isspace` also accepts `\r`, NBSP,
# and vertical tab, which zsh keeps inside a word — splitting there cut the
# `(N)` qualifier off `*.x\r(N)` and probed a bare pattern zsh never sees.
_WORD_BLANKS = frozenset(" \t\n")

# Words that are shell syntax rather than pathname patterns. `[` carries a
# glob metacharacter but `[ -d . ]` is the test builtin, not a bracket range.
_SYNTAX_WORDS = {"[", "[[", "]", "]]"}

# Either of these outside single quotes makes the word it sits in dynamic: its
# expanded value is unknown from the text, so that word is never probed. The
# rest of the command is still judged, because zsh performs every substitution
# before any filename generation and a substitution cannot turn into a
# separator — the other words expand exactly as written.
_DYNAMIC_MARKERS = ("$", "`")

# Openers of an expansion whose body may hold whitespace, quotes, or separators
# (`$(a; b)`, `${x:-a b}`, `$((1 * 2))`), mapped to the closer that ends it.
# The body belongs to its word, so the scanners skip over it whole.
_BRACKETED_EXPANSIONS = {"$(": ")", "${": "}", "$[": "]"}

# Stands in, in the skeleton, for an expansion whose end the scanner cannot
# find — unterminated, spanning a newline, or holding a `case` whose `pat)`
# breaks paren counting. Listed among the unsplittable markers below, so such a
# command passes through whole.
_OPAQUE = "\x01"
_CASE_WORD = re.compile(r"(?<![\w-])case(?![\w-])")

# Commands that disable the failure themselves. Only meaningful in command
# position: `echo noglob *.missing` still aborts, `noglob echo *.missing` does not.
_NOMATCH_DISABLERS = {"noglob", "setopt", "unsetopt", "eval"}

# The commands whose effect OUTLIVES their own command. `noglob` is a prefix —
# it shields the words it is given and nothing else, so dropping its segment
# is the whole remedy. These change the running shell's options, aliases, or
# definitions, so `setopt nullglob; print *.x` leaves the second segment
# expanding under rules this hook cannot see from the text: `setopt` /
# `unsetopt` / `emulate` directly, `disable` / `enable` by switching pattern
# syntax or builtins off, `eval` / `source` / `.` through text the
# hook does not read, `alias` by rewriting a later command word, and `set`
# with an option flag (`set -o nullglob`, `set +o nomatch`). A line holding
# one in any segment's command position passes through whole.
_STATE_CHANGING_DISABLERS = {
    "setopt", "unsetopt", "emulate", "eval", "source", ".", "alias",
    "disable", "enable",
}

# An assignment word: `NAME=`, `NAME+=`, or an array element `NAME[sub]=`. The
# subscript runs to the first `]` followed by `=` / `+=`.
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*(?:\[[^\]]*\])?\+?=")

# Reserved words whose `NAME=value` arguments zsh treats as assignments, so
# their values are never glob-expanded (`local x=*.log` runs, with no match).
# Their other arguments still are (`typeset *.x` aborts).
_TYPESET_WORDS = {"typeset", "local", "export", "readonly", "declare", "integer", "float"}

# Precommand words that make a typeset-family word an ordinary builtin call:
# `builtin export x=*.x` globs `x=*.x` and aborts, unlike `export x=*.x`.
_BUILTIN_PREFIXES = {"builtin", "command", "exec"}

# Writing `options[name]=…` sets a shell option, so it outlives its segment
# the same way `setopt` does.
_OPTIONS_ASSIGNMENT = re.compile(r"options\[")

# Precommand words that leave the real command word after them. Skipped before
# a command word is classified, so `builtin setopt` and `time for …` read as
# the `setopt` and the `for` they run.
_PRECOMMAND_WORDS = {"!", "time", "nocorrect", "noglob", "builtin", "command", "exec", "-"}

# Commands that change the cwd for every later segment, so a glob there would be
# probed in the wrong directory — in either direction. Passed through whole.
_CWD_CHANGERS = {"cd", "pushd", "popd"}

# Executing-shell options under which an unmatched glob does not abort.
_NOMATCH_SUPPRESSORS = {"nullglob", "nonomatch", "noglob", "cshnullglob"}

# Executing-shell options that change *whether a pattern matches*. `zsh -f`
# drops them, so the probe would otherwise answer a different question than the
# shell that actually runs the command. Each is replayed into the probe verbatim
# (the `no...` form included, for a default-on option turned off).
_GLOB_OPTIONS = {
    "extendedglob", "kshglob", "nocaseglob", "globdots",
    "bareglobqual", "globstarshort", "globsubst",
}

# Structure markers. Their presence means a word's meaning depends on shell
# grammar the hook does not model — which branch runs, what the cwd is by the
# time a later segment executes, whether the text is a heredoc body. `&&` /
# `||` gate whether the next command runs at all, `&` detaches, `<<` starts a
# heredoc whose body is data, and `;;` ends a `case` arm (or is a parse error
# zsh rejects) — none of those survive being cut into pieces and judged piece
# by piece.
_UNSPLITTABLE_MARKERS = ("&&", "||", "&", "<<", ";;", _OPAQUE)

# A backslash-newline joins two lines into one word stream. Passed through
# rather than modelled; it also catches the newline a continuation would hide
# from the separator split.
_LINE_CONTINUATION = "\\\n"

# An `&` touching a redirect arrow (`2>&1`, `<&0`, `&>out`, `>&|out`) duplicates
# or merges a file descriptor; it does not detach anything. Left in the
# skeleton, the `&` marker above read `ls *.x 2>&1 | head` as a background job
# and passed through one abort in seven (79 of 559 in the local transcript
# corpus). `|&` is a pipe, not a redirect, so it is not matched and stays a
# pass-through even when a redirect follows it (`|&> out cmd`).
_REDIRECT_AMPERSAND = re.compile(r"(?<=[<>])&|(?<!\|)&(?=>)")

# A leading `cd <dir>` is the one cwd change whose outcome the text decides.
# Before `&&` the rest runs exactly when `<dir>` is a directory, and runs
# there; 167 of 229 `&&` pass-through aborts in the local transcript corpus had
# that shape. Before `;` or a newline the rest runs either way, but in `<dir>`
# whenever it is a directory — which is the only case the strip applies to;
# 45 of 124 newline pass-through aborts started with such a line. The target
# must be a plain word — no quoting, expansion, glob, or option.
_CD_PREFIX = re.compile(r"\s*cd[ \t]+([^\s;&|<>()$`'\"\\*?\[\]{}]+)[ \t]*(?:&&|;|\n)")

# Separators that DO survive it. Each of `a ; b`, `a | b`, and two plain lines
# is an ordinary simple command whose own words expand under the same
# `nomatch`, so a glob in either one aborts it exactly as it would alone.
# Ordering matters: `&&` and `||` contain `&` and `|`, so they are ruled out by
# the tuple above before anything is split. A newline can also open a
# construct (`for …⏎do`); `find_unmatched_globs` stops at the first segment led
# by a control word, so a body that may never run is never judged.
_SEGMENT_SEPARATORS = (";", "|", "\n")
_CONTROL_WORDS = {
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
    "case", "esac", "select", "repeat", "function", "{", "}", "cd",
}

# Words that open or continue a compound command in command position: the
# portable set plus zsh's `foreach … end`, `{ … } always { … }`, and `coproc`.
_CONSTRUCT_WORDS = (_CONTROL_WORDS - {"cd"}) | {"foreach", "end", "always", "coproc"}

# A function definition's head: `f()`, `f(){`, and the `(){` of `f (){`.
_FUNCTION_HEAD = re.compile(r".*\(\)\{?")

# Command words whose meaning the gate depends on. Written quoted or escaped
# (`\setopt`, `'cd'`) they still run as themselves, but the hook compares raw
# spans, so such a line passes through. A quoted path (`"/opt/my tool"`) is
# not one of these and is judged as usual.
_SIGNIFICANT_COMMAND_WORDS = (
    _NOMATCH_DISABLERS | _STATE_CHANGING_DISABLERS | _PRECOMMAND_WORDS
    | _CWD_CHANGERS | _CONSTRUCT_WORDS | {"set"}
)

# `set` flags that cannot change globbing: single letters for errexit,
# nounset, xtrace, verbose, noclobber, and `-o`/`+o` option names likewise
# (compared lowercase, `_` dropped, a leading `no` removed). Anything else
# passes the line through — zsh's single letters reach glob options too
# (`set -G` is nullglob, `set -3` is nonomatch, `set -F` is noglob).
_SET_SAFE_LETTERS = set("euxvCo")
_SET_SAFE_OPTIONS = {
    "pipefail", "errexit", "nounset", "unset", "xtrace", "verbose", "errreturn",
    "clobber", "monitor", "notify", "hup", "checkjobs", "printexitvalue",
}

_PROBE_TIMEOUT_SEC = 2
# One budget for every probe in a command — see find_unmatched_globs().
_TOTAL_BUDGET_SEC = 3


class Word:
    """One shell word, with the source span and the unquoted-only view.

    Attributes:
      span: the exact source text of the word, quotes and any trailing zsh
        qualifier included — this is what gets replayed to zsh.
      bare: only the characters written outside quotes and expansions.
        Deciding whether a word is even a glob candidate reads this, because
        quoted metacharacters are never expanded.
      dynamic: the word holds a `$` or backtick expansion, so its value is
        unknown until the shell resolves it.

    Deliberately a plain class, not a dataclass: the dispatcher (ADR-0002)
    imports member `impl.py` files via `exec_module` without registering them
    in `sys.modules`, and `@dataclass` under `from __future__ import
    annotations` resolves field types through `sys.modules[cls.__module__]` —
    which is None there, raising AttributeError at import time.
    """

    __slots__: tuple[str, ...] = ("span", "bare", "dynamic")

    def __init__(self, span: str, bare: str, dynamic: bool = False) -> None:
        self.span: str = span
        self.bare: str = bare
        self.dynamic: bool = dynamic


def _double_quote_end(command: str, start: int) -> int | None:
    """Offset just past the `"` region opening at `start`; None if unterminated."""
    i = start + 1
    while i < len(command):
        ch = command[i]
        if ch == "\\":
            i += 2
        elif ch == '"':
            return i + 1
        elif ch in _DYNAMIC_MARKERS:
            end = expansion_end(command, i, in_double_quotes=True)
            if end is None:
                return None
            i = end
        else:
            i += 1
    return None


def _bracketed_end(command: str, start: int, opener: str, closer: str) -> int | None:
    """Offset just past the `closer` that balances the `opener` before `start`.

    Quotes and nested expansions inside the body are skipped whole, so a
    closer inside them (`$(echo ")")`) does not end the body early.
    """
    depth = 1
    i = start
    while i < len(command):
        ch = command[i]
        if ch == "\n":
            return None  # a newline can open any construct, comments included
        if ch == "\\":
            i += 2
            continue
        if ch == "'":
            close = command.find("'", i + 1)
            if close < 0:
                return None
            i = close + 1
            continue
        if ch == '"':
            end = _double_quote_end(command, i)
            if end is None:
                return None
            i = end
            continue
        if ch in _DYNAMIC_MARKERS:
            end = expansion_end(command, i, in_double_quotes=False)
            if end is None:
                return None
            i = end
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def expansion_end(command: str, start: int, in_double_quotes: bool) -> int | None:
    """Offset just past the `$` / backtick expansion at `start`; None if unknown.

    Bracketed forms (`$(...)`, `$((...))`, `${...}`, `$[...]`), backticks, and
    outside double quotes `$'...'` / `$"..."` are consumed whole, so their
    bodies never reach the word or separator split. A plain `$name` consumes
    only the `$`; the name is ordinary word text. None means the end cannot be
    found safely, and the caller passes the whole command through.
    """
    if command[start] == "`":
        i = start + 1
        while i < len(command):
            ch = command[i]
            if ch == "\n":
                return None
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                return i + 1
            i += 1
        return None
    opener = command[start:start + 2]
    closer = _BRACKETED_EXPANSIONS.get(opener)
    if closer is not None:
        end = _bracketed_end(command, start + 2, opener[1], closer)
        if end is None:
            return None
        # `case x in pat) ...` closes no paren it opened, so the count above
        # may have ended inside the body. Quoted text (`$(: 'case')`) is
        # masked first, since only a bare `case` opens that construct.
        body = unquoted_skeleton(command[start + 2:end - 1])
        if opener == "$(" and _CASE_WORD.search(body):
            return None
        return end
    if in_double_quotes:
        return start + 1  # `$'` and `$"` are literal inside double quotes
    if opener == "$'":
        i = start + 2
        while i < len(command):
            ch = command[i]
            if ch == "\n":
                return None
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                return i + 1
            i += 1
        return None
    if opener == '$"':
        return _double_quote_end(command, start + 1)
    return start + 1


def scan_words(command: str) -> list[Word]:
    """Split `command` into words, keeping each word's exact source span.

    Comment bodies are dropped: an unquoted `#` that begins a word comments
    out the rest of the line, so `echo ok # *.missing` carries no pattern.
    An expansion is kept whole inside its word and marks the word dynamic.
    """
    words: list[Word] = []
    span: list[str] = []
    bare: list[str] = []
    quote: str | None = None
    dynamic = False
    at_word_start = True
    i = 0

    def flush() -> None:
        nonlocal dynamic
        if span:
            words.append(Word("".join(span), "".join(bare), dynamic))
        span.clear()
        bare.clear()
        dynamic = False

    def take_expansion(in_double_quotes: bool) -> int:
        """Append the expansion at `i` to the word; return the offset after it."""
        nonlocal dynamic
        end = expansion_end(command, i, in_double_quotes)
        end = len(command) if end is None else end
        span.append(command[i:end])
        dynamic = True
        return end

    while i < len(command):
        ch = command[i]
        if quote is not None:
            if quote == '"' and ch in _DYNAMIC_MARKERS:
                i = take_expansion(in_double_quotes=True)
                continue
            span.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                span.append(command[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "#" and at_word_start:
            newline = command.find("\n", i)
            if newline < 0:
                break
            flush()
            i = newline + 1
            at_word_start = True
            continue
        if ch in _DYNAMIC_MARKERS:
            i = take_expansion(in_double_quotes=False)
            at_word_start = False
            continue
        if ch in ("'", '"'):
            quote = ch
            span.append(ch)
            at_word_start = False
            i += 1
            continue
        if ch == "\\" and i + 1 < len(command):
            span.append(ch)
            span.append(command[i + 1])
            bare.append(command[i + 1])
            at_word_start = False
            i += 2
            continue
        if ch in _WORD_BLANKS:
            flush()
            at_word_start = True
            i += 1
            continue
        if ch in _SEPARATORS:
            flush()
            at_word_start = True
            i += 1
            continue
        span.append(ch)
        bare.append(ch)
        at_word_start = False
        i += 1

    flush()
    return words


def _is_assignment(span: str) -> bool:
    """True for a `NAME=value`, `NAME+=value`, or `NAME[sub]=value` word.

    zsh does not glob-expand an assignment's value, nor the subscript of an
    array element being assigned (`h[*.x]=v`).
    """
    return _ASSIGNMENT.match(span) is not None


def unquoted_skeleton(command: str) -> str:
    """`command` with every inert character masked, so operators can be found.

    Searching the raw string for `|` or `$` misreads `grep 'a|b' *.x` as a
    pipeline and `grep '$v' *.x` as an expansion — both are single simple
    commands that zsh does abort on. Masking preserves offsets while keeping
    only characters the shell actually interprets: nothing inside single quotes,
    nothing in a comment body, and of an expansion only its leading `$` or
    backtick — its body, separators included, belongs to one word. An
    expansion whose end cannot be found masks the rest as `_OPAQUE`.
    """
    out: list[str] = []
    quote: str | None = None
    at_word_start = True
    i = 0

    def mask_expansion(in_double_quotes: bool) -> int:
        """Append the masked expansion at `i`; return the offset after it."""
        end = expansion_end(command, i, in_double_quotes)
        if end is None:
            out.append(_OPAQUE * (len(command) - i))
            return len(command)
        out.append(command[i] + _MASK * (end - i - 1))
        return end

    while i < len(command):
        ch = command[i]
        if quote == "'":
            out.append("'" if ch == "'" else _MASK)
            if ch == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            if ch == "\\" and i + 1 < len(command):
                out.append(_MASK * 2)
                i += 2
                continue
            if ch == '"':
                quote = None
                out.append('"')
                i += 1
                continue
            if ch in _DYNAMIC_MARKERS:
                i = mask_expansion(in_double_quotes=True)
                continue
            out.append(_MASK)
            i += 1
            continue
        if ch == "#" and at_word_start:
            newline = command.find("\n", i)
            end = len(command) if newline < 0 else newline
            out.append(_MASK * (end - i))
            i = end
            at_word_start = True
            continue
        if ch in _DYNAMIC_MARKERS:
            i = mask_expansion(in_double_quotes=False)
            at_word_start = False
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            at_word_start = False
            i += 1
            continue
        if ch == "\\" and i + 1 < len(command):
            out.append(_MASK * 2)
            at_word_start = False
            i += 2
            continue
        out.append(ch)
        # Matches `scan_words`: a separator ends the word too, so the `#` in
        # `echo a;# it's` starts a comment, and its `'` opens no quote.
        at_word_start = ch in _WORD_BLANKS or ch in _SEPARATORS
        i += 1
    return "".join(out)


def should_pass_through(command: str) -> bool:
    """True when the command's glob behaviour is not statically decidable.

    The reason is grammar that cutting into segments cannot preserve
    (branches, backgrounding, heredocs, line continuations), or an expansion whose end
    the scanner cannot find. A resolvable expansion no longer lands here: it
    makes only its own word undecidable, which `candidate_spans` skips, and a
    dynamic command word is handled in `find_unmatched_globs`. Command-position
    words that disable the failure are handled in `candidate_spans`, where
    their position is known.

    Sequencing with `;` and piping with `|` used to land here too, and that is
    what made this gate silent on the shape it most needed to see: measured
    across the local transcript corpus, 129 of 144 `no matches found` aborts
    (90%) came from a compound command, because a chained investigation line is
    exactly what an agent writes. `segments()` now cuts those instead.
    """
    skeleton = _REDIRECT_AMPERSAND.sub(_MASK, unquoted_skeleton(command))
    if any(marker in skeleton for marker in _UNSPLITTABLE_MARKERS):
        return True
    if _LINE_CONTINUATION in command:
        return True
    # A `case` arm (`*.c|*.h) …;;`) and a glob group (`(a|*.c)`) use `|` and
    # `;` as pattern grammar, not as separators; cutting there probes a
    # pattern as if it were a command.
    if any(word.span == "case" for word in scan_words(command)):
        return True
    return _separator_inside_parens(skeleton)


def _separator_inside_parens(skeleton: str) -> bool:
    """True when an unquoted `;`, `|`, or newline sits inside `( … )` or `[[ … ]]`.

    Inside `[[ … ]]` a newline is whitespace and `|` is pattern grammar, so
    neither is a separator.
    """
    depth = 0
    in_condition = False
    for index, char in enumerate(skeleton):
        word_start = index == 0 or skeleton[index - 1] in _WORD_BLANKS or skeleton[index - 1] in _SEPARATORS
        if skeleton.startswith("[[", index) and word_start:
            in_condition = True
        elif skeleton.startswith("]]", index):
            in_condition = False
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        elif char in _SEGMENT_SEPARATORS and (depth > 0 or in_condition):
            return True
    return False


def segments(command: str) -> list[str]:
    """`command` cut into simple commands at unquoted `;`, `|`, and newline.

    The skeleton is index-aligned with the source — quoted regions are masked
    in place, never removed — so a separator found in it slices the original
    text at the same offset, and a `;` inside quotes is invisible here exactly
    as it is to the shell.

    Callers reach this only after `should_pass_through` has ruled the command
    splittable, so no separator here can be part of a `&&` or `||`.
    """
    skeleton = unquoted_skeleton(command)
    out: list[str] = []
    start = 0
    for index, char in enumerate(skeleton):
        if char in _SEGMENT_SEPARATORS:
            out.append(command[start:index])
            start = index + 1
    out.append(command[start:])
    return [segment for segment in out if segment.strip()]


def candidate_spans(command: str) -> list[str] | None:
    """Spans worth probing, or None when the command as a whole passes through.

    Word position decides meaning, so this walks the words in order:
    `LC_ALL=C ls *.missing` has an inert assignment *and* a live glob, and
    `echo noglob *.missing` aborts while `noglob echo *.missing` does not.
    """
    spans: list[str] = []
    in_condition = False
    saw_command_word = False
    typeset_args = False
    after_builtin_prefix = False
    for word in scan_words(command):
        # zsh does not pathname-expand inside `[[ ... ]]`; a pattern there is a
        # match operand, so `[[ x = *.missing ]]` is simply false, not an abort.
        if word.span == "[[":
            in_condition = True
            continue
        if word.span == "]]":
            in_condition = False
            continue
        if in_condition:
            continue
        if not saw_command_word:
            if _is_assignment(word.span):
                continue  # prefix assignment — zsh never glob-expands the value
            # `(( n * 2 ))` is arithmetic, so its words are never pathnames.
            if (
                word.span in _NOMATCH_DISABLERS
                or word.span in _CONTROL_WORDS
                or word.span.startswith("((")
            ):
                return None
            if word.span in _PRECOMMAND_WORDS:
                # `builtin export x=*.x` runs `export` as a plain builtin, whose
                # arguments zsh globs like any other command's.
                after_builtin_prefix = after_builtin_prefix or word.span in _BUILTIN_PREFIXES
                continue  # `time noglob ls *.x` — the next word decides
            saw_command_word = True
            typeset_args = word.span in _TYPESET_WORDS and not after_builtin_prefix
        if word.span in _SYNTAX_WORDS:
            continue
        if typeset_args and _is_assignment(word.span):
            continue  # `local x=*.log` — an assignment, not a pathname
        if word.dynamic:
            continue  # its value is unknown until the shell expands it
        if not any(c in word.bare for c in GLOB_CHARS):
            continue  # metacharacters were quoted → the shell never expands them
        if "(" in word.span:
            # A zsh glob qualifier can carry executable code — `*(e:'cmd':)`
            # runs `cmd` once per matching file. Probing such a span would run
            # it here, before the permission boundary, and the dispatcher
            # evaluates every member even when another gate denies the command.
            # No qualifier is worth that, so qualified patterns are never probed.
            continue
        spans.append(word.span)
    return spans


def executing_shell_is_zsh() -> bool:
    """True when the shell that will run the command is zsh.

    The whole premise is zsh-specific: bash and fish hand an unmatched pattern
    to the command literally, so the command *does* run and `2>/dev/null` *does*
    suppress the error. Blocking there would be a pure false positive. The Bash
    tool runs the user's login shell, which `$SHELL` names; unknown or non-zsh
    is treated as "not zsh" so the gate stays silent.
    """
    shell = os.environ.get("SHELL") or ""
    return bool(shell) and _Path(shell).name == "zsh"


def executing_shell_glob_state(timeout_sec: float) -> list[str] | None:
    """Glob options to replay in the probe, or None when the gate must not fire.

    The Bash tool invokes a login zsh, which sources the user's startup files.
    Two things follow. If those enable `nullglob` / `nonomatch` / `noglob`, an
    unmatched pattern never aborts and blocking would be pure false positive —
    hence None. Otherwise the options that decide *whether a pattern matches*
    (`extendedglob` above all) are handed to the probe, which runs under `-f`
    and would otherwise answer a different question than the real shell.
    """
    try:
        proc = subprocess.run(
            ["zsh", "-lc", "setopt"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except (OSError, subprocess.SubprocessError):
        return None  # cannot confirm the premise → never block
    if proc.returncode != 0:
        return None
    enabled = [line.strip().lower() for line in proc.stdout.splitlines()]
    enabled = [opt for opt in enabled if opt.isalpha()]
    if set(enabled) & _NOMATCH_SUPPRESSORS:
        return None
    return [
        opt for opt in enabled
        if opt in _GLOB_OPTIONS
        or (opt.startswith("no") and opt[2:] in _GLOB_OPTIONS)
    ]


def zsh_finds_no_match(
    span: str,
    cwd: str,
    timeout: float = _PROBE_TIMEOUT_SEC,
    options: tuple[str, ...] = (),
) -> bool:
    """True when zsh itself reports `no matches found` for this span.

    `zsh -f` skips every startup file, so the user's own `setopt nullglob`
    cannot mask the result; the glob options that survive that filter are
    replayed explicitly via `options`, and `nomatch` is set last so nothing can
    override it. `:` is the no-op builtin — expansion is the only effect.
    """
    prelude = f"setopt {' '.join(options)}; " if options else ""
    try:
        proc = subprocess.run(
            ["zsh", "-f", "-c", f"{prelude}setopt nomatch; : {span}"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return False  # zsh unavailable or probe failed → never block
    return proc.returncode != 0 and "no matches found" in proc.stderr


def strip_cd_prefix(command: str, cwd: str) -> tuple[str, str]:
    """`(rest, dir)` for a leading `cd <dir>` + `&&`/`;`/newline whose `<dir>` exists, else unchanged.

    Only a target zsh cannot redirect is judged: one starting with `/`, `~/`,
    `./`, or `../`, or exactly `~`, `.`, or `..`. A bare relative `<dir>` is
    looked up in `cdpath` first when `cdpath` lists another entry before `.` or
    `posixcd` is set, and the hook cannot see the shell's `cdpath`. The same
    rule passes `-` (previous directory), `+N` (directory stack), `-P`, `=cmd`,
    and `~user` / `~+`. `^`, `#`, and a non-leading `~` pass through too:
    `extendedglob` turns them into pattern syntax.

    zsh resolves `..` logically, so the path is normalised rather than
    resolved; where `..` follows a symlink, `chaselinks` / `chasedots` would
    resolve it physically instead, so that case passes through.
    """
    match = _CD_PREFIX.match(command)
    if not match:
        return command, cwd
    target = match.group(1)
    if not (target.startswith(("/", "~/", "./", "../")) or target in ("~", ".", "..")):
        return command, cwd
    if "^" in target or "#" in target or "~" in target[1:]:
        return command, cwd
    joined = os.path.join(cwd, os.path.expanduser(target))
    path = os.path.normpath(joined)
    if not os.path.isdir(path):
        return command, cwd  # `cd` fails: the rest never runs, or runs where it is
    if os.path.realpath(joined) != os.path.realpath(path):
        return command, cwd  # `..` after a symlink: `chaselinks` would land elsewhere
    return command[match.end():], path


def _unquoted_word(span: str) -> str:
    """`span` with quote characters and backslashes removed, as zsh looks it up."""
    return span.translate({ord(c): None for c in "\\'\""})


def _quoted_significant(span: str) -> bool:
    """True for a quoted or escaped spelling of a word the gate keys on."""
    bare = _unquoted_word(span)
    return bare != span and bare in _SIGNIFICANT_COMMAND_WORDS


def _command_words(segment: str) -> list[Word]:
    """The words of `segment` from its real command word on.

    Prefix assignments and precommand words (`!`, `time`, `builtin`, …) are
    skipped, so what remains starts with the word zsh actually runs.
    """
    words = scan_words(segment)
    index = 0
    while index < len(words) and (
        _is_assignment(words[index].span) or words[index].span in _PRECOMMAND_WORDS
    ):
        index += 1
    return words[index:]


def _opens_construct(segment: str) -> bool:
    """True when `segment` opens or continues a compound command or function.

    Its body may run zero times (`while false; do …`), once per item, or only
    when called (`f() { … }`), so neither it nor anything after it is judged.
    """
    words = [w.span for w in _command_words(segment)]
    if not words:
        return False
    return (
        words[0] in _CONSTRUCT_WORDS
        or _FUNCTION_HEAD.fullmatch(words[0]) is not None
        or any(w.startswith("()") for w in words)
        or "{" in words
    )


def _changes_shell_state(words: list[Word]) -> bool:
    """True when the command outlives itself: options, aliases, sourced text."""
    if not words:
        return False
    if words[0].span in _STATE_CHANGING_DISABLERS:
        return True
    return words[0].span == "set" and _set_may_change_globbing(
        [w.span for w in words[1:]]
    )


def _set_may_change_globbing(args: list[str]) -> bool:
    """True unless every option `set` is given is known not to touch globbing.

    `set --` ends the options; what follows only sets positionals.
    """
    expects_name = False
    for arg in args:
        if expects_name:
            name = arg.lower().replace("_", "")
            stripped = name[2:] if name.startswith("no") else name
            if name not in _SET_SAFE_OPTIONS and stripped not in _SET_SAFE_OPTIONS:
                return True
            expects_name = False
            continue
        if arg in ("--", "-"):
            return False
        if len(arg) < 2 or arg[0] not in "-+":
            return False  # positionals from here on
        letters = arg[1:]
        if not set(letters) <= _SET_SAFE_LETTERS:
            return True
        expects_name = "o" in letters
    return expects_name  # a bare `set -o` lists options; treat as unknown


def _before_first_construct(parts: list[str]) -> list[str]:
    """The segments that run unconditionally, before any compound command.

    Everything ahead of the first construct runs in order, so its globs are
    judged as before; from the construct on, which segments run depends on
    conditions and loop lists the hook does not evaluate. Dropping only the
    keyword segment (`do :`) and judging the next one used to block
    `while false; do :; echo *.x; done`, which zsh runs without error.
    """
    for index, part in enumerate(parts):
        if _opens_construct(part):
            return parts[:index]
    return parts


def find_unmatched_globs(command: str, cwd: str) -> list[str]:
    """Return the candidate spans that zsh would refuse to expand.

    A single deadline covers every probe in the command. Each candidate spawns
    its own zsh, and this hook is one member of a shared PreToolUse(Bash)
    dispatch group — letting per-candidate timeouts accumulate could blow the
    group's budget and discard *other* gates' verdicts along with this one.
    """
    if not executing_shell_is_zsh():
        return []  # free check, and the cheapest possible one — do it first

    command, cwd = strip_cd_prefix(command, cwd)
    if should_pass_through(command):
        return []

    parts = _before_first_construct(segments(command))
    commands = [_command_words(part) for part in parts]
    if any(words and words[0].dynamic for words in commands):
        return []  # `$CMD` may resolve to `noglob`, `setopt`, or `cd`
    if any(words and _quoted_significant(words[0].span) for words in commands):
        return []  # zsh unquotes `\setopt` / `'cd'` before the lookup; the hook does not
    if any(_changes_shell_state(words) for words in commands):
        return []  # a later segment expands under options set by an earlier one
    if any(
        _OPTIONS_ASSIGNMENT.match(word.span) and _is_assignment(word.span)
        for part in parts
        for word in scan_words(part)
    ):
        return []  # `options[nullglob]=on` is a `setopt` written as an assignment
    if any(words and words[0].span in _CWD_CHANGERS for words in commands):
        return []  # a later segment runs in a directory the probe would not use

    spans: list[str] = []
    for segment in parts:
        # A segment whose command word disables the failure (`noglob ls *.x`)
        # returns None, and only that segment is dropped — its neighbours in
        # the same line still expand under `nomatch`. Constructs never get
        # here: `_before_first_construct` already cut them off.
        segment_spans = candidate_spans(segment)
        if segment_spans:
            spans.extend(segment_spans)
    if not spans:
        return []

    # Clamped to the remaining group budget under the dispatcher. Created
    # BEFORE the login-shell probe, because that probe is this hook's FIRST
    # subprocess: a fixed timeout there spends budget the spans below were
    # counting on, and no later clamp can give it back.
    deadline = budgeted_deadline(_TOTAL_BUDGET_SEC)
    probe_budget = deadline - time.monotonic()
    if probe_budget < MIN_SUBPROC_BUDGET_SEC:
        return []  # too little runway to confirm the premise → never block

    # Only now — the login-shell probe costs ~40ms, and this hook runs on every
    # Bash call, the overwhelming majority of which carry no glob at all.
    options = executing_shell_glob_state(min(_PROBE_TIMEOUT_SEC, probe_budget))
    if options is None:
        return []
    offenders: list[str] = []
    for span in spans:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break  # budget spent → stop probing, report what is known
        if zsh_finds_no_match(
            span, cwd, min(_PROBE_TIMEOUT_SEC, remaining), tuple(options)
        ):
            offenders.append(span)
    return offenders


@fail_open
def main() -> int:
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed
    if not command:
        return 0

    cwd = payload.get("cwd") or "."
    if not _Path(cwd).is_dir():
        return 0

    offenders = find_unmatched_globs(command, cwd)
    if not offenders:
        return 0

    _sys.stderr.write(
        format_block(
            rule_name="unmatched glob",
            why=(
                f"zsh reports `no matches found` for `{offenders[0]}`, so it aborts "
                "the whole command at expansion — the command never runs, and "
                "`2>/dev/null` cannot suppress that. Empty output would be "
                "indistinguishable from a genuine 'not found'."
            ),
            correct_path=(
                "quote the pattern if a tool should receive it literally "
                "(`-name '*.log'`); or add the zsh nullglob qualifier "
                "(`*.log(N)`); or use `find <dir> -maxdepth 1 -name '*.log'`, "
                "which returns rc=0 on zero matches; or drop the glob and test "
                "the concrete path. If you are probing for existence, a negative "
                "result from a command that never ran is not evidence of absence."
            ),
            bypass_env=None,
            reference="AGENTS.md → Information Accuracy (probe validity before negative conclusions)",
        )
        + "\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
