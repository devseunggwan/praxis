#!/usr/bin/env python3
"""PreToolUse(Bash) guard: reject a commit message release-please cannot parse.

release-please parses a commit with `@conventional-commits/parser`, and that
parser tries EVERY LINE of the message as a `type(scope): summary` header. A
line whose leading non-space run is immediately followed by `(` opens that
paren as a *scope*, and inside a scope only `)` is valid — so the line is
rejected when the paren does not close cleanly:

  nested    another `(` opens first      -> unexpected token '('
  unclosed  the line ends first          -> unexpected token '\\n'

A rejected commit is SKIPPED, and the release workflow still ends
`completed/success`, so the commit silently loses its CHANGELOG entry. Three
commits were dropped from two releases that way before anyone read the log
(issue #1228). The loss is permanent: release-please computes the next release
from the previous tag, so a commit skipped once never returns.

Detection paths (message text, not just the title):
  git commit -m/-F ...                     -> `extract_git_message_texts`
  git commit -m "$(cat <<'EOF' … EOF)"     -> the heredoc body, since the
                                              tokenizer only ever sees `$(cat`

Config:
  PRAXIS_COMMIT_PAREN_STRICT — default "1" (block, exit 2); "0" -> advisory
                               (exit 0, message to stderr)
"""
from __future__ import annotations

import os
import sys
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    _quote_open_at_eol,
    compound_cascade_hint,
    has_shell_expansion,
    heredoc_delimiters,
    heredoc_sources,
    iter_command_starts,
    iter_command_texts,
    safe_tokenize,
    strip_heredoc_bodies,
)
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402
from git_commit_titles import (  # type: ignore[import-not-found]  # noqa: E402
    _scan_commit_message_values,
    extract_git_message_texts,
    title_is_unresolved_substitution,
)

STRICT_ENV = "PRAXIS_COMMIT_PAREN_STRICT"


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

def offending_lines(message: str) -> list[tuple[int, str, str]]:
    """Every `(lineno, kind, line)` the parser would reject in `message`.

    Each condition below is a PASS the real parser was measured to grant, not a
    guess about its grammar — see spec.md for the probe table:

      no `(`, or `(` at column 1   the scope position is never entered
      whitespace before the `(`    the word ends, so no scope opens
      `!` or `:` before the `(`    the separator was already consumed
      the `(` closes first         the scope closed; later parens are free text
    """
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(message.split("\n"), 1):
        idx = line.find("(")
        if idx <= 0:
            continue
        prefix = line[:idx]
        if any(ch.isspace() for ch in prefix):
            continue
        if "!" in prefix or ":" in prefix:
            continue
        kind = "unclosed"
        for ch in line[idx + 1:]:
            if ch == ")":
                kind = ""
                break
            if ch == "(":
                kind = "nested"
                break
        if kind:
            hits.append((lineno, kind, line))
    return hits


# ---------------------------------------------------------------------------
# Message sources
# ---------------------------------------------------------------------------

def _splice_continuations(command: str) -> str:
    """Apply bash line continuation, and only where bash applies it.

    A backslash-newline is REMOVED, not turned into a separator: bash reads
    `-m "word\\<newline>(a(b))"` as the single word `word(a(b))`, so replacing
    the pair with a space handed the tokenizer `word (a(b))` — a line whose
    leading word is not glued to `(`, which is exactly the shape this gate
    passes. A malformed message written that way was invisible to the gate
    (issue #1228 round 2).

    Two regions are left alone because bash does not splice them either:

      inside a single-quoted run   nothing is an escape there, so the
                                   backslash and the newline are both message
                                   text — joining them fabricates a glued word
                                   and blocks a commit whose real message has
                                   the paren at column 1
      inside a heredoc body        the body is read separately from the raw
                                   command, and joining lines here would shift
                                   every line number after it, putting the
                                   body scan's terminator search on the wrong
                                   line

    An unquoted heredoc body IS spliced by bash; `_heredoc_text_for` does that
    on the body it reads, where no line numbering depends on it.
    """
    if "\\\n" not in command:
        return command
    lines = command.split("\n")
    blanked = strip_heredoc_bodies(command).split("\n")
    out: list[str] = []
    held = ""
    quote = ""
    for pos, line in enumerate(lines):
        in_body = line != "" and pos < len(blanked) and blanked[pos] == ""
        trailing = len(line) - len(line.rstrip("\\"))
        # The state the trailing backslash sits in is the state at the END of
        # the line, not the one carried in: the quote that makes the backslash
        # literal is routinely the one this same line opened, as in
        # `git commit -m 'word\`.
        if not in_body:
            quote = _quote_open_at_eol(line, quote)
        splices = not in_body and quote != "'" and trailing % 2 == 1
        if splices:
            held += line[:-1]
            continue
        out.append(held + line)
        held = ""
    if held:
        out.append(held)
    return "\n".join(out)

def _heredoc_text_for(delimiters: list[str], command: str) -> list[str]:
    """The bodies `delimiters` name, when each name identifies exactly one.

    A delimiter word is how an unresolved message source says which heredoc it
    reads from, and it is only an identifier while it is unique in the command.
    `cat <<EOF … EOF; git commit -F - <<EOF … EOF` reuses the word, so nothing
    in the token stream separates the notes body from the message body — the
    ambiguous name yields no text and the gate stays silent, which is the
    fail-open direction this repo takes for a gate.

    An UNQUOTED delimiter (`<<EOF`, against `<<'EOF'`) leaves the body subject
    to expansion, so the raw lines here are not the ones git receives. Bash
    splices the body's line continuations and substitutes its expansions
    first: `word($(printf x))` arrives as `word(x)`, which parses, while the
    source reads as a nested paren. The splice is applied and any line still
    carrying an expansion is blanked, so what is graded is the delivered text
    and nothing else (issue #1228 round 2).
    """
    sources = heredoc_sources(command)
    texts: list[str] = []
    for name in delimiters:
        matches = [(body, quoted) for delim, body, quoted in sources if delim == name]
        if len(matches) != 1:
            continue
        body, quoted = matches[0]
        if not quoted:
            body = "\n".join(
                "" if has_shell_expansion(line) else line
                for line in body.replace("\\\n", "").split("\n")
            )
        texts.append(body)
    return texts


def _redirected_delimiters(argv: list[str]) -> list[str]:
    """Every heredoc delimiter the redirections in one segment's argv open.

    The operator and its delimiter are one token in `<<EOF` and two in the
    equally valid `<< EOF`, because the tokenizer splits on the whitespace
    bash allows there. Reading each token on its own therefore found nothing
    in the spaced form, and a malformed message fed to `git commit -F -` went
    through unseen (issue #1228 round 2). The delimiter is recovered from the
    following token instead, and its quotes are already gone — the tokenizer
    strips them, exactly as `_read_heredoc_delim` does for the attached form.
    """
    out: list[str] = []
    for pos, tok in enumerate(argv):
        if tok in ("<<", "<<-"):
            if pos + 1 < len(argv):
                out.append(argv[pos + 1])
            continue
        out.extend(heredoc_delimiters(tok))
    return out


def _unresolved_source_texts(argv: list[str], command: str) -> list[str]:
    """Heredoc bodies belonging to the message sources this argv cannot resolve.

    Two shapes reach the parser as an unreadable value, and each names its own
    heredoc rather than the command's heredocs at large:

      -m "$(cat <<'EOF' … EOF)"   the delimiter sits inside the `-m` value, so
                                  the value itself says which body is its own
      -F -                        the message arrives on stdin, fed by a
                                  redirection on THIS argv — `<<EOF` survives
                                  tokenization as a token of the segment

    Binding by name is what keeps an unrelated `cat > notes <<'NOTE'` in the
    same chain out of the grade: reading every heredoc blocked a valid commit
    whenever some other command in the chain carried prose the parser would
    reject, and returning early on the first readable `-m` let a malformed
    body ride along behind a well-formed subject.

    Each source names exactly ONE heredoc — the last it opened. Both shapes
    read stdin, and redirections apply left to right, so in
    `-m "$(cat <<A <<B …)"` the body `cat` receives is `B` and `A` is opened
    and discarded. Taking every delimiter the source opened graded that
    discarded body too, which blocked a commit whose real message was clean
    (issue #1228 round 2).
    """
    scanned = _scan_commit_message_values(argv)
    if scanned is None:
        return []
    values, _c_dir = scanned

    wanted: list[str] = []
    for kind, raw in values:
        if kind == "message":
            if title_is_unresolved_substitution(raw.split("\n")[0], command):
                opened = heredoc_delimiters(raw)
                if opened:
                    wanted.append(opened[-1])
        elif raw == "-":
            opened = _redirected_delimiters(argv)
            if opened:
                wanted.append(opened[-1])
    return _heredoc_text_for(wanted, command)


def _message_texts(argv: list[str], command: str) -> list[str]:
    """Every commit message text this argv contributes."""
    return extract_git_message_texts(argv, command) + _unresolved_source_texts(
        argv, command
    )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _build_message(hits: list[tuple[int, str, str]], command: str) -> str:
    lineno, kind, line = hits[0]
    detail = "\n".join(
        f"  line {n}: [{k}] {ln!r}" for n, k, ln in hits[:5]
    )
    more = "" if len(hits) <= 5 else f"\n  … and {len(hits) - 5} more"
    base = format_block(
        rule_name="commit-message-paren-check",
        why=(
            "release-please cannot parse this commit message, so the commit is "
            "skipped and loses its CHANGELOG entry while the release workflow "
            "still reports success (issue #1228). Every line is parsed as a "
            "`type(scope):` header, and a leading word glued to `(` opens a "
            "scope that must close on the same line.\n"
            f"{detail}{more}"
        ),
        correct_path=(
            "put a space before the `(`, move the parenthesised text off the "
            f"start of line {lineno}, or close the paren on that line"
        ),
        bypass_env=STRICT_ENV,
        bypass_reason_hint="=0 to switch to advisory mode (exit 0, stderr warning only)",
        reference="hooks/preflight-gate/commit-message-paren-check/spec.md",
    )
    return base + compound_cascade_hint(command)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

@fail_open
def main() -> int:
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, raw_command = parsed
    if not raw_command.strip():
        return 0

    # Only the tokenizer gets the spliced form; heredoc bodies are read from
    # the raw command, where their line numbering is still intact.
    command = _splice_continuations(raw_command)

    # `safe_tokenize` coalesces a `$( … )` run into ONE token, so a commit
    # written as `MSG=$(git commit -m '…')` has no command start at the top
    # level at all. `iter_command_texts` yields the outer text and then the
    # inner text of every ACTIVE substitution, which is where the sibling
    # gates on this surface already look (issues #1032, #1035).
    for segment in iter_command_texts(command):
        for argv in iter_command_starts(safe_tokenize(segment)):
            for text in _message_texts(argv, raw_command):
                hits = offending_lines(text)
                if not hits:
                    continue
                reason = _build_message(hits, command)
                if os.environ.get(STRICT_ENV, "1") != "0":
                    sys.stderr.write(reason + "\n")
                    return 2
                sys.stderr.write(
                    f"[commit-message-paren-check] ADVISORY (STRICT=0):\n{reason}\n"
                )
                return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
