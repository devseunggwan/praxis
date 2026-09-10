#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: an unquoted `$var` that zsh will NOT word-split.

Issue #1405.

`SH_WORD_SPLIT` is off by default in zsh, so `set -- $spec` passes ONE
argument where the author meant several, and `for x in $list` iterates once
over the whole string. bash does the opposite, which is where the habit comes
from — and the failure is silent at the shell: the command runs, the wrong
argv reaches the tool, and the error text that eventually appears names the
tool's own argument parser rather than the shell.

Measured across the local transcript corpus: 182 uses in 72 sessions, 82 of
them (45%) ending in an error whose text names the consuming CLI, never the
shell. The other 99 exited cleanly, which is the half with no signal at all.

Advisory, never a block. Passing a deliberately unsplit single argument is a
legitimate use of the same syntax and this hook cannot tell the two apart —
it can only make the fork visible while the command is still being written.
`${=var}` is the one-character opt-in to splitting.

Silent when:
  • the executing shell is not zsh — bash and fish split it as written, so
    the whole premise is absent and a warning there is a pure false positive
  • the expansion is quoted (`"$var"`) — one argument, unambiguously intended
  • the split form is already used (`${=var}`, `${(s: :)var}`, `$=var`)
  • the name is `$@` / `$*` / `$#` / `$?` / `$$` / a positional — those carry
    their own splitting rules and are not what was measured
  • a heredoc body — data, not words
  • the opt-out marker is present on the command
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _shell_tokenize import strip_heredoc_bodies  # type: ignore[import-not-found]  # noqa: E402

OPT_OUT_MARKER = "# word-split:ok"

# A parameter whose name is an identifier. `${=v}` / `$=v` (the split flag),
# `${(s: :)v}` (an explicit split), `$@` / `$*` / `$1` and the specials are all
# excluded by requiring an identifier head with no leading `=` or `(`.
_PARAM = r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"

# `set -- $var` — the shape the corpus measured most.
_SET_ARGS = re.compile(r"(?:^|[;&|]|\s)set\s+(?:--|-)\s+(?P<rest>[^;&|\n]*)")

# `for x in $var` / `while ... in $var`. zsh's `for x ($var)` form too.
_FOR_IN = re.compile(r"(?:^|[;&|]|\s)for\s+\w+\s+in\s+(?P<rest>[^;&|\n]*)")

_PARAM_RE = re.compile(_PARAM)


def _mask_quoted(text: str) -> str:
    """`text` with quoted runs blanked out, length preserved.

    A quoted expansion is one argument on purpose, so it must not match; the
    length is preserved so a caller can still slice the source by index.
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
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        out.append(ch)
    return "".join(out)


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


def executing_shell_is_zsh() -> bool:
    """True when the shell that will run the command is zsh.

    Same premise check `block-unmatched-glob` makes, for the same reason: the
    behaviour this warns about does not exist under bash or fish, so an
    unknown or non-zsh `$SHELL` keeps the hook silent.
    """
    shell = os.environ.get("SHELL") or ""
    return bool(shell) and Path(shell).name == "zsh"


def advisory_text(names: list[str]) -> str:
    first = names[0]
    listed = ", ".join(f"`${n}`" for n in names)
    return (
        f"[zsh-word-split-advisory] zsh does not word-split {listed} here.\n"
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
        f"Silence this on a command that means one argument: {OPT_OUT_MARKER}"
    )


@fail_open
def main() -> int:
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    _payload, command = parsed
    if not isinstance(command, str) or not command.strip():
        return 0
    if OPT_OUT_MARKER in command:
        return 0
    if not executing_shell_is_zsh():
        return 0

    names = unsplit_params(command)
    if not names:
        return 0

    sys.stderr.write(advisory_text(names) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
