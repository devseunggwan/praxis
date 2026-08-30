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

  * dynamic expansion (`$var`, `$(...)`, backticks) — the prefix is unknown
    before the shell resolves it, so the filesystem question is unanswerable
  * compound structure (`&&`, `||`, `|`, `;`, `&`, newline, heredoc) — a later
    segment may run in a different cwd, or not run at all, and a quoted heredoc
    body is never glob-expanded
  * control-flow and `cd` words, for the same reason
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
import subprocess
import sys as _sys
import time
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
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

# Words that are shell syntax rather than pathname patterns. `[` carries a
# glob metacharacter but `[ -d . ]` is the test builtin, not a bracket range.
_SYNTAX_WORDS = {"[", "[[", "]", "]]"}

# Any of these *unquoted* makes the outcome unpredictable from the outside, so
# the whole command is passed through. Matched against the unquoted skeleton —
# `grep 'a|b' *.x` is a single simple command, not a pipeline.
_DYNAMIC_MARKERS = ("$", "`")

# Commands that disable the failure themselves. Only meaningful in command
# position: `echo noglob *.missing` still aborts, `noglob echo *.missing` does not.
_NOMATCH_DISABLERS = {"noglob", "setopt", "unsetopt", "eval"}

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
# time a later segment executes, whether the text is a heredoc body. The gate
# only judges a single simple command; anything else passes through.
_COMPOUND_MARKERS = ("&&", "||", "|", ";", "&", "\n", "<<")
_CONTROL_WORDS = {
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
    "case", "esac", "select", "repeat", "function", "{", "}", "cd",
}

_PROBE_TIMEOUT_SEC = 2
# One budget for every probe in a command — see find_unmatched_globs().
_TOTAL_BUDGET_SEC = 3


class Word:
    """One shell word, with the source span and the unquoted-only view.

    Attributes:
      span: the exact source text of the word, quotes and any trailing zsh
        qualifier included — this is what gets replayed to zsh.
      bare: only the characters written outside quotes. Deciding whether a
        word is even a glob candidate reads this, because quoted
        metacharacters are never expanded.

    Deliberately a plain class, not a dataclass: the dispatcher (ADR-0002)
    imports member `impl.py` files via `exec_module` without registering them
    in `sys.modules`, and `@dataclass` under `from __future__ import
    annotations` resolves field types through `sys.modules[cls.__module__]` —
    which is None there, raising AttributeError at import time.
    """

    __slots__: tuple[str, ...] = ("span", "bare")

    def __init__(self, span: str, bare: str) -> None:
        self.span: str = span
        self.bare: str = bare


def scan_words(command: str) -> list[Word]:
    """Split `command` into words, keeping each word's exact source span.

    Comment bodies are dropped: an unquoted `#` that begins a word comments
    out the rest of the line, so `echo ok # *.missing` carries no pattern.
    """
    words: list[Word] = []
    span: list[str] = []
    bare: list[str] = []
    quote: str | None = None
    at_word_start = True
    i = 0

    def flush() -> None:
        if span:
            words.append(Word("".join(span), "".join(bare)))
        span.clear()
        bare.clear()

    while i < len(command):
        ch = command[i]
        if quote is not None:
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
        if ch.isspace():
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
    """True for a `NAME=value` word — zsh does not glob-expand assignment values."""
    name, sep, _ = span.partition("=")
    if not sep or not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in name)


def unquoted_skeleton(command: str) -> str:
    """`command` with every inert character masked, so operators can be found.

    Searching the raw string for `|` or `$` misreads `grep 'a|b' *.x` as a
    pipeline and `grep '$v' *.x` as an expansion — both are single simple
    commands that zsh does abort on. Masking preserves offsets while keeping
    only characters the shell actually interprets: nothing inside single quotes,
    `$` and backtick inside double quotes, nothing in a comment body.
    """
    out: list[str] = []
    quote: str | None = None
    at_word_start = True
    i = 0
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
            out.append(ch if ch in _DYNAMIC_MARKERS else _MASK)
            i += 1
            continue
        if ch == "#" and at_word_start:
            newline = command.find("\n", i)
            end = len(command) if newline < 0 else newline
            out.append(_MASK * (end - i))
            i = end
            at_word_start = True
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
        at_word_start = ch.isspace()
        i += 1
    return "".join(out)


def should_pass_through(command: str) -> bool:
    """True when the command's glob behaviour is not statically decidable.

    Two reasons, both of which make a word-level verdict unsound: unresolved
    expansion, and any grammar beyond a single simple command (branches,
    sequencing, heredocs). Command-position words that disable the failure are
    handled in `candidate_spans`, where their position is known.
    """
    skeleton = unquoted_skeleton(command)
    if any(marker in skeleton for marker in _DYNAMIC_MARKERS):
        return True
    return any(marker in skeleton for marker in _COMPOUND_MARKERS)


def candidate_spans(command: str) -> list[str] | None:
    """Spans worth probing, or None when the command as a whole passes through.

    Word position decides meaning, so this walks the words in order:
    `LC_ALL=C ls *.missing` has an inert assignment *and* a live glob, and
    `echo noglob *.missing` aborts while `noglob echo *.missing` does not.
    """
    spans: list[str] = []
    in_condition = False
    saw_command_word = False
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
            saw_command_word = True
            if word.span in _NOMATCH_DISABLERS or word.span in _CONTROL_WORDS:
                return None
        if word.span in _SYNTAX_WORDS:
            continue
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


def executing_shell_glob_state() -> list[str] | None:
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
            timeout=_PROBE_TIMEOUT_SEC,
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


def find_unmatched_globs(command: str, cwd: str) -> list[str]:
    """Return the candidate spans that zsh would refuse to expand.

    A single deadline covers every probe in the command. Each candidate spawns
    its own zsh, and this hook is one member of a shared PreToolUse(Bash)
    dispatch group — letting per-candidate timeouts accumulate could blow the
    group's budget and discard *other* gates' verdicts along with this one.
    """
    if not executing_shell_is_zsh():
        return []  # free check, and the cheapest possible one — do it first

    if should_pass_through(command):
        return []

    spans = candidate_spans(command)
    if not spans:
        return []

    # Only now — the login-shell probe costs ~40ms, and this hook runs on every
    # Bash call, the overwhelming majority of which carry no glob at all.
    options = executing_shell_glob_state()
    if options is None:
        return []

    deadline = time.monotonic() + _TOTAL_BUDGET_SEC
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
