#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: zsh dialect shapes that fail where bash does not.

Issues #1405 (word split), #1425 (shapes 1-3) and #1526 (shape 5).

Five shapes, measured on this machine's zsh 5.9 rather than read from a manual:

  1. `echo ======` → `zsh:1: ===== not found`. A word starting with `=` is a
     command path lookup (EQUALS expansion), so `[ "$x" == y ]` fails too —
     `==` outside `[[ ]]` is such a word.
  2. `${w#[[}` → `zsh:1: bad pattern: [[`. An unmatched `[` inside a pattern
     operator is a glob, and double quotes do not protect it.
  3. A heredoc opener inside an open heredoc body reusing the OUTER delimiter:
     the first terminator closes the outer body, and the remaining lines run as
     commands (`command not found: EOF`). The rest of the compound command —
     often an edit — silently never runs.
  4. `for f in $list` → one iteration over the whole string, because
     `SH_WORD_SPLIT` is off by default in zsh. bash splits it, which is where
     the habit comes from.
  5. `status=$?` → `zsh:1: read-only variable: status`. `status` is zsh's
     alias of `$?`, so every assignment to it fails.

Shapes 1, 2, 3 and 5 are deterministic: the command as written cannot do what
it says, whatever the author meant, so they return `ask` — a correction point
while the command is still being written. Shape 4 is not: passing a
deliberately unsplit single argument uses identical syntax, and a gate that
cannot tell intent apart must not block. It stays an advisory, on
`additionalContext` (reaches the actor) and stderr (grades the fire).

Silent when:
  • the executing shell is not zsh — shapes 1, 2, 4 and 5 do not exist under
    bash or fish. Shape 3 is shell-general and fires regardless.
  • the text is quoted in a way that actually protects it — which differs per
    shape: both quotes protect shape 1, only single quotes protect shape 2
  • the split form is already used (`${=var}`, `${(s: :)var}`, `$=var`)
  • `$@` / `$*` / `$#` / `$?` / `$$` / a positional — own splitting rules
  • a heredoc body — data, not words (shape 3 reads the bodies on purpose)
  • an opt-out marker is present on the command
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_additional_context, emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _shell_tokenize import strip_heredoc_bodies  # type: ignore[import-not-found]  # noqa: E402

# `# word-split:ok` predates the other three shapes (#1405) and stays valid.
OPT_OUT_MARKERS = ("# zsh-dialect:ok", "# word-split:ok")

# A parameter whose name is an identifier. `${=v}` / `$=v` (the split flag),
# `${(s: :)v}` (an explicit split), `$@` / `$*` / `$1` and the specials are all
# excluded by requiring an identifier head with no leading `=` or `(`.
_PARAM = r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"

# `set -- $var` — the shape the corpus measured most.
_SET_ARGS = re.compile(r"(?:^|[;&|]|\s)set\s+(?:--|-)\s+(?P<rest>[^;&|\n]*)")

# `for x in $var` / `while ... in $var`. zsh's `for x ($var)` form too.
_FOR_IN = re.compile(r"(?:^|[;&|]|\s)for\s+\w+\s+in\s+(?P<rest>[^;&|\n]*)")

_PARAM_RE = re.compile(_PARAM)

# A word the shell hands to EQUALS expansion: `=` plus at least one more
# character. A bare `=` (as in `test 1 = 1`) is left alone — verified, it is
# not expanded. An optional `NAME=` head is peeled first, because `V==foo`
# expands its value the same way.
_EQUALS_WORD_RE = re.compile(r"(?:^|(?<=\s))(?:[A-Za-z_][A-Za-z0-9_]*=)?(=[^\s;&|]+)")

# `${name#pat}` and friends. Only these operators take a glob pattern;
# `${name:-[[}` is a default value and never parsed as one.
_PATTERN_EXPANSION_RE = re.compile(
    r"\$\{[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?(?P<op>##?|%%?|//?)(?P<pat>[^}]*)\}"
)

# `<<EOF`, `<<-'EOF'`, `<< "EOF"` — the opener, wherever it appears.
_HEREDOC_OPENER_RE = re.compile(
    r"<<-?\s*(?P<q>['\"]?)(?P<delim>[A-Za-z_][A-Za-z0-9_]*)(?P=q)"
)


def _mask_quoted(text: str, quotes: str = "'\"") -> str:
    """`text` with quoted runs blanked out, length preserved.

    `quotes` says which quote characters actually protect the shape being
    scanned. Both protect a word from EQUALS expansion; only a single quote
    protects a pattern operator's `[`, since `"${w#[[}"` still globs.
    """
    out: list[str] = []
    quote = ""
    escaped = False
    for ch in text:
        if escaped:
            out.append(" ")
            escaped = False
            continue
        if ch == "\\":
            out.append(" ")
            escaped = True
            continue
        if quote:
            out.append(" " if ch != quote else ch)
            if ch == quote:
                quote = ""
            continue
        if ch in quotes:
            quote = ch
            out.append(ch)
            continue
        out.append(ch)
    return "".join(out)


def _mask_double_bracket(text: str) -> str:
    """`text` with `[[ … ]]` conditions blanked, length preserved.

    zsh parses a `[[` condition itself, so `==` inside one is an operator and
    never a command path lookup. `[ … ]` is the ordinary `test` command and
    gets no such treatment — which is exactly why `[ "$x" == y ]` fails.
    """
    out = list(text)
    depth = 0
    i = 0
    while i < len(text) - 1:
        pair = text[i:i + 2]
        if pair == "[[":
            depth += 1
            i += 2
            continue
        if pair == "]]" and depth:
            depth -= 1
            out[i] = out[i + 1] = " "
            i += 2
            continue
        if depth:
            out[i] = " "
        i += 1
    return "".join(out)


def _mask_arithmetic(text: str) -> str:
    """`text` with `(( … ))` and `$(( … ))` blanked, length preserved.

    Arithmetic is parsed by the shell itself, so `==` there is an operator —
    `(( x == y ))` runs cleanly and must not be reported as a path lookup.
    """
    out = list(text)
    depth = 0
    i = 0
    while i < len(text) - 1:
        pair = text[i:i + 2]
        if pair == "((":
            depth += 1
            i += 2
            continue
        if pair == "))" and depth:
            depth -= 1
            i += 2
            continue
        if depth:
            out[i] = " "
        i += 1
    return "".join(out)


_COMMENT_RE = re.compile(r"(?:^|(?<=\s))#[^\n]*", re.MULTILINE)


def _mask_comments(text: str) -> str:
    """`text` with shell comments blanked, length preserved.

    A comment starts at an unquoted `#` that begins a word; run this after
    `_mask_quoted`, so a `#` inside quotes is already gone. `${w#pat}` and `$#`
    are untouched because their `#` does not begin a word.
    """
    return _COMMENT_RE.sub(lambda m: " " * len(m.group(0)), text)


def unsplit_params(command: str) -> list[str]:
    """Names of the parameters zsh would pass unsplit, in source order."""
    text = _mask_quoted(strip_heredoc_bodies(command))
    found: list[str] = []
    for pattern in (_SET_ARGS, _FOR_IN):
        for match in pattern.finditer(text):
            for param in _PARAM_RE.finditer(match.group("rest")):
                name = param.group("braced") or param.group("bare")
                if name not in found:
                    found.append(name)
    return found


def equals_words(command: str) -> list[str]:
    """Words zsh will try to resolve as a command path, in source order."""
    text = _mask_quoted(strip_heredoc_bodies(command))
    text = _mask_arithmetic(_mask_double_bracket(_mask_comments(text)))
    found: list[str] = []
    for match in _EQUALS_WORD_RE.finditer(text):
        word = match.group(1)
        if word not in found:
            found.append(word)
    return found


def _has_unmatched_bracket(pattern: str) -> bool:
    """True when `pattern` opens a `[` class it never closes.

    Inside a class `[` is an ordinary member, and a `]` right after the opener
    (or after `!`/`^`) is a member too — so `[[]` is a closed class matching a
    literal `[`, while `[[` is still left open. `[:alpha:]` is skipped whole.
    An open class whose first member is that `]` (`[]`, `[!]`) is not reported:
    zsh 5.9 treats it as a no-match rather than a bad pattern (measured).
    """
    in_class = False
    class_start = 0
    leading_bracket = False
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if not in_class:
            if ch == "[":
                in_class = True
                class_start = i + 1
                if class_start < len(pattern) and pattern[class_start] in "!^":
                    class_start += 1
                leading_bracket = pattern[class_start:class_start + 1] == "]"
            i += 1
            continue
        if ch == "[" and pattern.startswith("[:", i):
            end = pattern.find(":]", i + 2)
            if end != -1:
                i = end + 2
                continue
        if ch == "]" and i != class_start:
            in_class = False
        i += 1
    return in_class and not leading_bracket


def bad_patterns(command: str) -> list[str]:
    """`${…}` expansions whose pattern zsh will reject, in source order."""
    text = _mask_quoted(strip_heredoc_bodies(command), quotes="'")
    found: list[str] = []
    for match in _PATTERN_EXPANSION_RE.finditer(text):
        if not _has_unmatched_bracket(match.group("pat")):
            continue
        snippet = command[match.start():match.end()]
        if snippet not in found:
            found.append(snippet)
    return found


def shadowed_heredocs(command: str) -> list[str]:
    """Delimiters an inner opener re-uses while the outer body is still open.

    The inner opener is data, so the first terminator line closes the OUTER
    heredoc and every line after it runs as a command. Reusing a *different*
    delimiter is fine and stays silent — that is the normal nested form.

    A body that merely *mentions* the opener — a script being written that
    itself contains a heredoc — is not this bug: the mention is data and the
    single terminator still ends the body where the author meant it to. What
    separates the two is the terminator count, so a delimiter is reported only
    when the body carries its own closing line on top of the outer one.
    """
    if "<<" not in command:
        return []
    lines = command.split("\n")
    found: list[str] = []
    open_delims: list[str] = []
    for index, line in enumerate(lines):
        if open_delims:
            if line.strip() == open_delims[0]:
                del open_delims[0]
                continue
            inner = _HEREDOC_OPENER_RE.search(line)
            delim = inner.group("delim") if inner else ""
            if delim and delim == open_delims[0] and delim not in found:
                closings = sum(
                    1 for rest in lines[index + 1:] if rest.strip() == delim
                )
                if closings > 1:
                    found.append(delim)
            continue
        open_delims.extend(
            match.group("delim") for match in _HEREDOC_OPENER_RE.finditer(line)
        )
    return found


# Words after which the next word is again in command position.
_LEADING_KEYWORDS = frozenset(
    {"if", "then", "else", "elif", "do", "while", "until", "!", "time",
     "nocorrect", "coproc"}
)
_ASSIGNMENT_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?\+?=")
_STATUS_ASSIGNMENT_RE = re.compile(r"status(?:\[[^\]]*\])?\+?=")
# A redirection word. Operator-only (`>`, `2>&`) takes the next word as its
# target; `>out` carries it. Neither ends the assignment prefix.
_REDIRECT_RE = re.compile(r"(?:\d*|&)[<>]")
_REDIRECT_OPERATOR_RE = re.compile(r"(?:\d*|&)[<>]+[&|]?")
# Builtins whose `NAME=value` arguments are assignments. `local status` with no
# value is a declaration and succeeds (measured), so only `status=` counts.
_DECLARATION_BUILTINS = frozenset(
    {"local", "typeset", "declare", "export", "readonly", "integer", "float"}
)
# Stands in for an expansion inside a word, so `$(x)status=1` is not read as
# starting with `status=`.
_EXPANSION_PLACEHOLDER = "x"


class _Frame:
    """One nesting level of the scan.

    `cmd` holds commands (the top level, `( … )`, `$( … )`, `` ` … ` ``,
    `<( … )`, `=( … )`). `array`, `param` and `pattern` hold values, whose words
    are never assignments.
    """

    def __init__(self, kind: str, closer: str, opened_mid_word: bool = False):
        self.kind = kind
        self.closer = closer
        self.opened_mid_word = opened_mid_word
        self.has_content = False
        self.word: list[str] = []
        self.prefix = True  # the next word may be an assignment or the command
        self.declaration = False  # arguments are assignments (`local x=1`)
        self.skip_next = False  # the next word is a redirect target or a count

    def reset(self) -> None:
        self.prefix = True
        self.declaration = False
        self.skip_next = False


def _finish_word(frame: _Frame, found: list[str]) -> None:
    word = "".join(frame.word)
    frame.word.clear()
    if not word or frame.kind != "cmd":
        return
    if frame.skip_next:
        frame.skip_next = False
        return
    status = _STATUS_ASSIGNMENT_RE.match(word)
    if frame.declaration:
        if status and status.group(0) not in found:
            found.append(status.group(0))
        return
    if not frame.prefix:
        return
    if _REDIRECT_RE.match(word):
        frame.skip_next = bool(_REDIRECT_OPERATOR_RE.fullmatch(word))
        return
    if word in _LEADING_KEYWORDS:
        return
    if word in ("repeat", "function"):
        # The count or the function name; the body after it is in command
        # position again (`function f { status=1; }`).
        frame.skip_next = True
        return
    if _ASSIGNMENT_WORD_RE.match(word):
        if status and status.group(0) not in found:
            found.append(status.group(0))
        return
    frame.prefix = False
    frame.declaration = word in _DECLARATION_BUILTINS


def _close_frame(stack: list[_Frame], found: list[str]) -> None:
    closed = stack.pop()
    _finish_word(closed, found)
    outer = stack[-1]
    if closed.kind != "pattern":
        outer.word.append(_EXPANSION_PLACEHOLDER)
        return
    if not closed.opened_mid_word:
        # `(pat)` of a case branch: the branch body follows.
        outer.reset()
    elif not closed.has_content and outer.prefix:
        # `f()`: a function definition, whose body follows.
        outer.word.clear()
        outer.reset()
    else:
        # A glob qualifier (`*(N)`): the word goes on.
        outer.word.append(_EXPANSION_PLACEHOLDER)


def status_assignments(command: str) -> list[str]:
    """`status=` / `status+=` assignment words, in source order.

    `status` is read-only in zsh, so any assignment to it fails with
    `read-only variable: status`. An assignment is a leading `NAME=` word of a
    simple command, or a `NAME=` argument of a declaration builtin; the same
    text as an ordinary argument (`echo status=1`) is not one. The scan keeps a
    stack of nesting levels so a command inside `$( … )` has its own prefix,
    and the words of an array, a `${ … }` or a pattern are skipped.
    """
    text = _mask_quoted(strip_heredoc_bodies(command))
    text = _mask_arithmetic(_mask_double_bracket(_mask_comments(text)))
    found: list[str] = []
    stack = [_Frame("cmd", closer="")]
    i = 0
    while i < len(text):
        ch = text[i]
        pair = text[i:i + 2]
        frame = stack[-1]
        if ch == frame.closer and len(stack) > 1:
            _close_frame(stack, found)
            i += 1
            continue
        if frame.kind != "cmd" and not ch.isspace():
            frame.has_content = True
        if ch == "`":
            stack.append(_Frame("cmd", closer="`"))
            i += 1
            continue
        if pair in ("$(", "<(", ">("):
            stack.append(_Frame("cmd", closer=")"))
            i += 2
            continue
        if pair == "${":
            stack.append(_Frame("param", closer="}"))
            i += 2
            continue
        if frame.kind != "cmd":
            if ch == "(":
                stack.append(_Frame("pattern", closer=")", opened_mid_word=True))
            i += 1
            continue
        # `frame.word` is read in place: joining it per character is quadratic
        # on a long unbroken token.
        if ch == "(":
            word = "".join(frame.word)
            if word == "=":
                frame.word.clear()
                stack.append(_Frame("cmd", closer=")"))
            elif word.endswith("="):
                stack.append(_Frame("array", closer=")"))
            elif not word and frame.prefix:
                stack.append(_Frame("cmd", closer=")"))
            else:
                stack.append(
                    _Frame("pattern", closer=")", opened_mid_word=bool(word))
                )
            i += 1
            continue
        if ch == ")":
            # Unmatched: the end of a case pattern, and the branch body follows.
            frame.word.clear()
            frame.reset()
            i += 1
            continue
        # `&>` / `&>>` redirect both streams; unlike `&`, `&|` or `&!` they do
        # not end the command.
        if (ch in "&|" and frame.word and frame.word[-1] in "<>") or pair == "&>":
            frame.word.append(ch)
            i += 1
            continue
        if ch in ";&|\n":
            _finish_word(frame, found)
            frame.reset()
            i += 1
            continue
        if ch.isspace():
            _finish_word(frame, found)
            i += 1
            continue
        if (ch == "{" and not frame.word and frame.prefix
                and text[i + 1:i + 2].isspace()):
            i += 1  # a `{ … }` group: the next word is still in command position
            continue
        if ch == "}" and not frame.word:
            frame.prefix = False
            i += 1
            continue
        frame.word.append(ch)
        i += 1
    while stack:
        _finish_word(stack.pop(), found)
    return found


def executing_shell_is_zsh() -> bool:
    """True when the shell that will run the command is zsh.

    Same premise check `block-unmatched-glob` makes, for the same reason: the
    behaviour this warns about does not exist under bash or fish, so an
    unknown or non-zsh `$SHELL` keeps the zsh-only shapes silent.
    """
    shell = os.environ.get("SHELL") or ""
    return bool(shell) and Path(shell).name == "zsh"


def word_split_text(names: list[str]) -> str:
    first = names[0]
    listed = ", ".join(f"`${n}`" for n in names)
    return (
        f"[zsh-dialect-advisory] zsh does not word-split {listed} here.\n"
        "\n"
        "`SH_WORD_SPLIT` is off by default in zsh, so this passes ONE argument "
        "containing the whole string — not one argument per word. bash splits "
        "it, which is where the habit comes from.\n"
        f"  split:     ${{={first}}}   (or `${{(s: :){first}}}` for an explicit separator)\n"
        f"  one arg:   \"${first}\"    (quote it, if that is what you meant)\n"
        "\n"
        "The shell reports nothing either way. The error, when it comes, names "
        f"the receiving command's argument parser — so `${first}` is not where "
        "you will look.\n"
        f"Silence this on a command that means one argument: {OPT_OUT_MARKERS[0]}"
    )


def deterministic_text(
    equals: list[str],
    patterns: list[str],
    heredocs: list[str],
    statuses: list[str],
) -> str:
    lines = ["⚠️ 이 명령은 zsh 에서 쓴 대로 실행되지 않습니다", ""]
    if equals:
        listed = ", ".join(f"`{word}`" for word in equals)
        lines += [
            f"- {listed} — `=` 로 시작하는 낱말은 zsh 의 EQUALS 확장이라 명령 "
            "경로를 찾습니다(`zsh:1: … not found`).",
            "  고치기: 따옴표로 감싸거나(`'=='`), `[ … ]` 대신 `[[ … ]]` 를 쓰세요 "
            "— `[[ ]]` 안의 `==` 는 연산자입니다.",
        ]
    if patterns:
        listed = ", ".join(f"`{pattern}`" for pattern in patterns)
        lines += [
            f"- {listed} — 패턴 연산자(`#`/`%`/`/`) 안의 닫히지 않은 `[` 는 glob "
            "이라 `bad pattern` 으로 죽습니다. 큰따옴표는 막아주지 않습니다.",
            '  고치기: 패턴만 따로 따옴표로 감싸세요 — `${w#"[["}`.',
        ]
    if heredocs:
        listed = ", ".join(f"`{delim}`" for delim in heredocs)
        lines += [
            f"- heredoc {listed} — 열린 heredoc 본문 안에서 같은 구분자로 다시 "
            "열었습니다. 첫 종료 줄이 바깥 heredoc 을 닫고 그 뒤 줄들은 명령으로 "
            "실행됩니다(`command not found`). 같은 명령의 뒷부분이 조용히 "
            "실행되지 않습니다.",
            "  고치기: 안쪽 구분자를 다른 이름으로 바꾸거나, 파일을 먼저 쓰고 "
            "호출을 나누세요.",
        ]
    if statuses:
        listed = ", ".join(f"`{word}`" for word in statuses)
        lines += [
            f"- {listed} — zsh 에서 `status` 는 `$?` 와 같은 읽기 전용 "
            "변수라 할당하면 `read-only variable: status` 로 실패합니다.",
            "  고치기: 다른 이름을 쓰세요 — `rc=$?`.",
        ]
    lines +=["", f"의도한 대로라면 이 표지로 끕니다: {OPT_OUT_MARKERS[0]}"]
    return "\n".join(lines)


@fail_open
def main() -> int:
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    _payload, command = parsed
    if not isinstance(command, str) or not command.strip():
        return 0
    if any(marker in command for marker in OPT_OUT_MARKERS):
        return 0

    zsh = executing_shell_is_zsh()
    equals = equals_words(command) if zsh else []
    patterns = bad_patterns(command) if zsh else []
    heredocs = shadowed_heredocs(command)
    statuses = status_assignments(command) if zsh else []

    if equals or patterns or heredocs or statuses:
        emit_decision(
            "ask", deterministic_text(equals, patterns, heredocs, statuses)
        )
        return 0

    names = unsplit_params(command) if zsh else []
    if not names:
        return 0

    text = word_split_text(names)
    emit_additional_context(text)
    sys.stderr.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
