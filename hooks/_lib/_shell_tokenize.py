"""Shell tokenization for praxis PreToolUse(Bash) hooks.

Text-level primitives — heredoc bodies, quote folding across physical lines,
comment stripping, `safe_tokenize` — plus the argv-level helpers that consume
its output: `iter_command_starts`, `strip_prefix`, the `gh` binary check.
`safe_tokenize` → `iter_command_starts` → `strip_prefix` is the pipeline
every Bash hook shares (DESIGN.md → Structural tokenization).

Split out of `_hook_utils.py` in issue #1305; the code moved verbatim, so the
per-issue history in the docstrings below still applies. Standard library
only: `_subst`, `_compound`, and `_roles` import from here, never the
reverse, and `_hook_utils` re-exports every name for the older
`from _hook_utils import …` preamble.
"""
from __future__ import annotations

import re
import shlex

SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}

ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Shell keywords that appear at the start of a command segment but are purely
# syntactic. `if true; then git push; fi` segments as `['if','true']`,
# `['then','git','push']`, `['fi']` — we peel the keyword so argv[0] becomes
# the real executable.
SHELL_KEYWORDS = {
    "if", "then", "elif", "else", "fi",
    "while", "until", "do", "done",
    "case", "esac", "in", "for",
    "{", "}", "!", "function",
}

# Prefix wrappers that execute the following command as a new process. The
# scanner looks past them to find the real argv[0]. Per-wrapper option
# dictionaries list *only* flags that take a separate-token argument so that
# `sudo --user admin kubectl ...` peels both `--user` and `admin`. Bare flags
# (with no arg) and `--long=value` forms are handled generically below.
#
# `command` / `builtin` are bash shell wrappers that run the following word as
# a command (`command gh pr merge` executes `gh pr merge`). Without peeling
# them, an `argv[0] == "gh"` gate is silently bypassed via `command gh ...`.
# Their flags (`-p`, `-v`, `-V`) take no separate-token argument, so the
# generic bare-flag peel below handles them.
PREFIX_WRAPPERS = {
    "env", "sudo", "nice", "time", "stdbuf", "ionice", "command", "builtin",
}
WRAPPER_OPTS_WITH_ARG = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "sudo": {
        "-u", "-g", "-p", "-C", "-D", "-r", "-t", "-T", "-U", "-h",
        "--user", "--group", "--prompt", "--close-from", "--chdir",
        "--role", "--type", "--host", "--other-user",
    },
    "nice": {"-n", "--adjustment"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "time": {"-f", "--format", "-o", "--output"},
    "ionice": {
        "-c", "--class", "-n", "--classdata",
        "-p", "--pid", "-P", "--pgid", "-u", "--uid",
    },
}


# bash's full metacharacter set minus the newline, which line splitting already
# handles. `)`, `<` and `>` were missing while `(`, `;`, `|` and `&` were
# present, so a comment opening right after a closing paren or a redirection
# operator was read as ordinary text — and an apostrophe inside it opened a
# quote that swallowed the next line's command.
_WORD_BOUNDARY_CHARS = " \t;|&()<>"


def _starts_unquoted_comment(line: str, i: int) -> bool:
    """True when `line[i]` is a `#` that bash would read as opening a comment.

    A `#` only starts a comment at the start of a word, so the character before
    it must be a metacharacter that ended the previous token. Both scanners in
    this module ask this question and they must never answer it differently:
    `_heredoc_starts_on_line` uses it to stop reading operators, and
    `_quote_open_at_eol` uses it to stop tracking quote state.
    """
    return line[i] == "#" and (i == 0 or line[i - 1] in _WORD_BOUNDARY_CHARS)


def _heredoc_starts_on_line(
    line: str, quote: str = ""
) -> tuple[list[tuple[str, bool, bool]], str]:
    """`(openers, quote_open_at_eol)` for one physical line.

    `openers` is `(delimiter, dash_form, quoted)` for every heredoc opened on
    the line. Scans character-wise rather than by regex because the three
    constructs that must NOT be read as a heredoc all look like `<<` to a
    pattern match: a here-string (`<<<`), an arithmetic left-shift
    (`$((1 << 3))`), and a literal `<<` inside quotes. Order is preserved —
    bash reads the bodies of `cat <<A <<B` in the order the operators appear.

    `quoted` says whether the delimiter word carried quoting (`<<'EOF'`,
    `<<"EOF"`, `<<\\EOF`). Bash expands the body of an UNQUOTED heredoc before
    the reading command ever sees it, so a consumer that grades body text needs
    to know which of the two it holds: the raw text of an unquoted body is not
    the text bash delivers (issue #1228 round 2).

    A command substitution re-opens shell parsing even inside double quotes, so
    `-m "$(cat <<'EOF'`  — the exact shape of a commit-message payload — really
    does open a heredoc. `subst` stacks the suspended quote state so that one is
    seen while a plain `"…<<EOF…"` string stays inert. Backticks are the older
    spelling of the same construct and re-enter parsing identically, so
    ``-m "`cat <<'EOF' … `"`` opens a heredoc too; reading the `<<` there as
    string data let a malformed message through the gate unseen. Each stacked
    entry carries the character that closes it, because `)` must not close a
    backtick run and vice versa.

    `quote` is the quote character still open from the *previous* physical line;
    the returned second element is the quote still open at this line's end.
    Carrying it lets `strip_heredoc_bodies` mirror `_logical_lines`' fold so a
    `<<WORD` sitting inside a multi-line quoted string is read as data, not as a
    heredoc opener (issue #1091, extending the #972/#987 quote-fold). A group
    still open inside a `$(…)` is reported as its outermost suspended quote,
    exactly as `_quote_open_at_eol` does.
    """
    out: list[tuple[str, bool, bool]] = []
    i, n = 0, len(line)
    arith = 0
    subst: list[tuple[str, str]] = []
    while i < n:
        ch = line[i]
        if quote == '"' and line.startswith("$(", i) and not line.startswith("$((", i):
            subst.append((quote, ")"))
            quote = ""
            i += 2
            continue
        if quote == '"' and ch == "`":
            subst.append((quote, "`"))
            quote = ""
            i += 1
            continue
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if _starts_unquoted_comment(line, i):
            break  # unquoted comment — bash reads no operator past it
        if ch == "\\":
            i += 2
            continue
        if line.startswith("$((", i):
            arith += 1
            i += 3
            continue
        if arith and line.startswith("))", i):
            arith -= 1
            i += 2
            continue
        if subst and not arith and ch == subst[-1][1]:
            quote = subst.pop()[0]
            i += 1
            continue
        if line.startswith("<<", i) and not arith:
            if line.startswith("<<<", i):  # here-string: no body follows
                i += 3
                continue
            j = i + 2
            dash = j < n and line[j] == "-"
            if dash:
                j += 1
            while j < n and line[j] in " \t":
                j += 1
            delim, j, delim_quoted = _read_heredoc_delim(line, j)
            if delim:
                out.append((delim, dash, delim_quoted))
                i = j
                continue
            i += 2
            continue
        i += 1
    # Report the outermost still-open quote so the caller can carry it into the
    # next physical line — a `$(…)` open at EOL keeps the command going, so its
    # suspended quote is what bash is still inside (issue #1091).
    return out, quote or (subst[0][0] if subst else "")


_HEREDOC_WORD_CHARS = re.compile(r"[A-Za-z0-9_.~\-/]")


def _read_heredoc_delim(line: str, j: int) -> tuple[str, int, bool]:
    """`(delimiter, index just past it, quoted)` for the word starting at `j`.

    `<<EOF`, `<<'EOF'`, `<<"EOF"`, and `<<\\EOF` all name the same delimiter,
    so the word itself is returned with its quoting removed. The quoting is
    reported separately rather than discarded because it decides whether bash
    expands the body: any quoting at all — a quoted run or a single backslash
    anywhere in the word — makes the body literal, and an unquoted word leaves
    it subject to command, parameter, and arithmetic expansion. Returns
    `("", j, False)` when no delimiter word is present, which is what keeps
    `1 << 3` from being read as a heredoc named `3`.
    """
    n = len(line)
    if j < n and line[j] in "'\"":
        q = line[j]
        end = line.find(q, j + 1)
        return (line[j + 1:end], end + 1, True) if end != -1 else ("", j, False)
    out: list[str] = []
    escaped = False
    while j < n:
        if line[j] == "\\" and j + 1 < n:
            out.append(line[j + 1])
            escaped = True
            j += 2
            continue
        if not _HEREDOC_WORD_CHARS.match(line[j]):
            break
        out.append(line[j])
        j += 1
    return "".join(out), j, escaped


def strip_heredoc_bodies(command: str) -> str:
    """Blank out every heredoc body line, keeping the line count intact.

    A heredoc body is data, not script: `git commit -m "$(cat <<'EOF' … EOF)"`
    puts arbitrary prose where the per-line tokenizer expects commands, so a
    commit message that merely *quotes* `gh pr merge` used to be tokenized as a
    merge invocation and blocked by every merge gate (issue #985). Bodies are
    replaced by empty lines rather than dropped so the operator line and the
    commands after the terminator keep their positions.

    The terminator line is kept. Several hooks run their own heredoc scan on
    top of this one (`pytest-direct-exec-advisory`, `foreground-poll-loop-guard`,
    `bash-worktree-existence-advisory`) and each waits for that line to resume
    scanning — blanking it leaves them inside a body that never closes, so
    everything after the heredoc goes silent. That is a bigger hole than the
    false positive being closed here.

    An unterminated heredoc suppresses everything to the end of the command,
    which is what bash does with it too.

    Quote state is carried across physical lines (issue #1091, mirroring the
    #972/#987 fold in `_logical_lines`): the per-line heredoc scan used to reset
    to an unquoted state each line, so a `<<WORD` occurring on a later physical
    line *inside* an open multi-line quoted string was misread as a heredoc
    opener — blanking the closing-quote line and everything chained after it
    (`… "notes\nsee <<EOF …\n" && gh pr merge`). Threading the open quote makes
    that `<<WORD` read as the string data it is.
    """
    if "<<" not in command:
        return command
    out: list[str] = []
    pending: list[tuple[str, bool, bool]] = []
    quote = ""  # #1091: open quote carried in from the previous physical line
    for line in command.split("\n"):
        if pending:
            delim, dash, _quoted = pending[0]
            probe = line.lstrip("\t") if dash else line
            if probe == delim:  # exact — `EOF ` does not close a heredoc
                del pending[0]
                out.append(line)  # terminator survives — see the docstring
            else:
                out.append("")
            continue
        out.append(line)
        openers, quote = _heredoc_starts_on_line(line, quote)
        pending.extend(openers)
    return "\n".join(out)


def heredoc_bodies(command: str) -> list[str]:
    """Every heredoc body in `command`, in source order, terminator excluded.

    The delimiter-dropping view of `heredoc_bodies_by_delimiter`, for a caller
    that wants every body regardless of which heredoc produced it.
    """
    return [body for _delim, body, _quoted in heredoc_sources(command)]


def heredoc_sources(command: str) -> list[tuple[str, str, bool]]:
    """`(delimiter, body, quoted)` for every heredoc in `command`, source order.

    The delimiter is what lets a caller say *which* heredoc it means. A command
    can hold several — an unrelated `cat > notes <<'NOTE'` beside the
    `git commit -F - <<'EOF'` whose body is the message — and grading them all
    reads someone else's prose as the commit message (issue #1228 follow-up).
    Nothing here decides that binding; it only stops discarding the one field
    the caller needs to make it.

    The counterpart of `strip_heredoc_bodies`: that one blanks a body because
    it is data rather than script, and this one returns it for exactly the
    same reason. `git commit -m "$(cat <<'EOF' … EOF)"` puts the commit
    message in a heredoc, so tokenizing the command yields `$(cat <<'EOF'` and
    the message itself is reachable only from the raw string — a gate that
    grades the message body has nowhere else to read it from.

    The scan mirrors the stripper line for line, including the quote state
    carried across physical lines (issue #1091), so the two cannot disagree on
    where a body begins or ends. An unterminated heredoc yields the body it
    opened, matching the stripper's decision to treat the rest of the command
    as that body.

    `<<-` strips leading tabs from EVERY body line, not only from the
    terminator — `bash(1)`, "Here Documents": *all leading tab characters are
    stripped from input lines and the line containing the delimiter*. Returning
    the raw line instead handed a consumer text the shell never produces: a
    body line `\\tword(a(b))` reads as indented prose to
    `commit-message-paren-check`, which skips any line whose leading run holds
    whitespace, while the commit message release-please parses starts at
    `word(`. The stripper needs no such fold because it blanks the line either
    way.

    `quoted` rides along from the opener: an unquoted `<<EOF` body reaches the
    reading command only after bash has expanded it, so a caller that grades
    body text has to know the raw lines here are not the delivered ones.
    """
    if "<<" not in command:
        return []
    bodies: list[tuple[str, str, bool]] = []
    pending: list[tuple[str, bool, bool]] = []
    current: list[str] = []
    quote = ""
    for line in command.split("\n"):
        if pending:
            delim, dash, delim_quoted = pending[0]
            probe = line.lstrip("\t") if dash else line
            if probe == delim:
                del pending[0]
                bodies.append((delim, "\n".join(current), delim_quoted))
                current = []
            else:
                current.append(probe)
            continue
        openers, quote = _heredoc_starts_on_line(line, quote)
        pending.extend(openers)
    if pending:
        bodies.append((pending[0][0], "\n".join(current), pending[0][2]))
    return bodies


def heredoc_delimiters(text: str) -> list[str]:
    """Every heredoc delimiter word `text` opens, in source order.

    Scans with the same opener reader `heredoc_sources` uses, so
    the two agree on what counts as an opener — a here-string, an arithmetic
    shift, and a quoted `<<` are none of them. `text` is a fragment rather than
    a whole command: one shell token (`<<EOF`, or the `$(cat <<'EOF' …)` a
    `-m` value arrives as) is the case this exists for, which is how a caller
    names the heredoc a particular argv position reads from.
    """
    if "<<" not in text:
        return []
    out: list[str] = []
    quote = ""
    for line in text.split("\n"):
        openers, quote = _heredoc_starts_on_line(line, quote)
        out.extend(delim for delim, _dash, _quoted in openers)
    return out


_EXPANSION_START = re.compile(r"\$[({A-Za-z_0-9@*?#!-]|`")


def has_shell_expansion(text: str) -> bool:
    """True when bash would substitute something into `text`.

    Covers command substitution (`$(…)`, backticks) and parameter expansion
    (`${…}`, `$NAME`, `$1`, `$@`), which is every construct that can put
    characters into a string the hook cannot predict. A gate that grades text
    for a *shape* — a paren that must close on its line — has to treat such a
    line as unreadable rather than grade the pre-expansion source: `word($(printf
    x))` reaches the reading command as `word(x)`, which is well-formed, while
    the raw text reads as a nested paren and blocks a valid commit (issue #1228
    round 2).

    Deliberately over-broad in the fail-open direction. Quoting is already gone
    by the time a tokenized value reaches here, so a literal `\\$` cannot be
    told from a live `$`, and calling both an expansion loses detection on a
    line rather than blocking a message nobody can read. A caller that knows
    the text is literal — a single-quoted run, a quoted heredoc body — must not
    consult this at all.
    """
    return bool(_EXPANSION_START.search(text))


def has_unclosed_expansion(text: str) -> bool:
    """True when a `$(…)` or backtick run in `text` never closes.

    Such a command is not something bash runs at all — it is a syntax error, so
    no commit happens and there is nothing for a gate to grade. The text after
    the opener is also not what it appears to be: it is the inside of an
    unfinished substitution, which is how `$(x` followed by prose came to be
    read as message lines (CodeRabbit, issue #1228 round 2).

    Parens are counted only once inside a substitution, so ordinary prose
    parentheses in a message cannot make it look unbalanced.
    """
    depth = 0
    backtick = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "`":
            backtick = not backtick
            i += 1
            continue
        if text.startswith("$(", i):
            depth += 1
            i += 2
            continue
        if depth:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        i += 1
    return depth > 0 or backtick


HELP_FLAGS = frozenset({"-h", "--help"})

# `gh pr merge` flags that consume a following value token. Shared so that a
# help scan and a positional scan agree on which tokens are values — `--subject
# -h` must stay a subject, not a help request.
GH_MERGE_VALUE_FLAGS = frozenset({
    "-b", "--body", "-F", "--body-file", "-t", "--subject",
    "--match-head-commit", "--author-email",
    "-R", "--repo", "--hostname", "--color",
})


def is_help_invocation(argv: list[str], value_flags: frozenset[str]) -> bool:
    """True when the segment asks for help instead of doing anything.

    `gh pr merge --help` prints usage and exits — it merges nothing, so a gate
    that treats it as a merge blocks the one command an agent runs to learn the
    flags it is being asked to get right (issue #985). `value_flags` names the
    flags that consume the next token, so `--subject -h` stays a subject value
    and still counts as a real merge.
    """
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            return False  # everything after is positional, never a help flag
        if tok in HELP_FLAGS:
            return True
        if tok.startswith("-") and "=" not in tok and tok in value_flags:
            i += 2
            continue
        i += 1
    return False


def _quote_open_at_eol(line: str, quote: str) -> str:
    """The quote character still open after consuming `line` from state `quote`.

    Models *shlex*'s quote rules rather than bash's, because shlex is the
    tokenizer this scanner feeds: a single quote escapes everything, a backslash
    escapes inside double quotes and outside quotes, and `$(…)` is opaque text
    rather than a re-entrant parse. Modelling bash here instead would let the
    line grouping and the tokenizer disagree about where a token ends.

    The one deliberate divergence is the unquoted `#`. `safe_tokenize` runs with
    `commenters = ""` so the `# side-effect:ack` marker survives as tokens, but
    bash reads nothing past an unquoted `#` — so the apostrophe in
    `git status # don't do this` must not be taken for an opening quote that
    swallows the next line's real command. The word-boundary rule lives in
    `_starts_unquoted_comment` so this scanner and `_heredoc_starts_on_line`
    cannot answer the same question differently. The
    divergence only ever ends a group *earlier*, never merges more lines into
    one, so it cannot hide a command that the old per-line split saw.
    """
    i, n = 0, len(line)
    arith = 0
    subst: list[str] = []
    while i < n:
        ch = line[i]
        # A command substitution re-opens shell parsing even inside double
        # quotes, so the quotes within it are their own. Without this, the inner
        # `'"'` of `echo "$(printf '"')"` was read as closing the outer double
        # quote and opening a new one, leaving a quote apparently open at EOL —
        # the next line was folded in and shlex dropped the pair. `subst` stacks
        # the suspended outer quote, mirroring `_heredoc_starts_on_line`, which
        # already had to solve this for the same reason.
        if quote == '"' and line.startswith("$(", i) and not line.startswith("$((", i):
            subst.append(quote)
            quote = ""
            i += 2
            continue
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if _starts_unquoted_comment(line, i):
            return ""  # unquoted comment — bash opens no quote past it
        if ch == "\\":
            i += 2
            continue
        if line.startswith("$((", i):
            arith += 1
            i += 3
            continue
        if arith and line.startswith("))", i):
            arith -= 1
            i += 2
            continue
        if ch == ")" and subst and not arith:
            quote = subst.pop()
            i += 1
            continue
        i += 1
    # A substitution still open at EOL means the command continues, so report
    # the outermost suspended quote and let the caller keep folding. A `$(`
    # opened *outside* any quote suspends `""`, which reports closed — that is
    # the pre-existing behaviour, unchanged here rather than improved.
    return quote or (subst[0] if subst else "")


def _logical_lines(command: str) -> list[str]:
    """Physical lines folded into logical lines across an unclosed quote.

    A newline inside a quote is *data*, not a command separator — bash keeps
    reading the same word. Splitting there gave the opening and the closing line
    their own shlex pass, both raised ``ValueError: No closing quotation``, and
    the caller's fail-open arm dropped them: that took `gh pr comment`'s argv[0]
    with it (issue #972) and, when a real command rode on the closing line, the
    entire command (issue #987 — all three merge gates went silent on
    ``git commit -m "$(cat <<'EOF' … EOF)" && gh pr merge``).

    Folded lines are rejoined with ``\\n`` so the newline stays *inside* the
    resulting token, which is what bash hands the process. A group still open at
    the end of the command is emitted as-is, so shlex raises exactly as it did
    before and the caller's ``except ValueError`` arm keeps its behavior.
    """
    if "\n" not in command:
        return [command]
    out: list[str] = []
    group: list[str] = []
    quote = ""
    for line in command.split("\n"):
        group.append(line)
        quote = _quote_open_at_eol(line, quote)
        if not quote:
            out.append("\n".join(group))
            group = []
    if group:  # unterminated at end of command — hand it to shlex unchanged
        out.append("\n".join(group))
    return out


# `# <marker>:ack` opt-out markers that gates read out of the token stream
# (`# side-effect:ack`, `# title-length:ack`, `# cross-boundary:ack`, …). These
# must keep tokenizing even though `safe_tokenize` now strips ordinary trailing
# comments (issue #1091), so the comment strip below skips any comment carrying
# one. `commenters = ""` on the shlex pass then turns it into `#` + `<marker>`
# tokens exactly as before.
_ACK_MARKER_RE = re.compile(r"#\s*[A-Za-z0-9][A-Za-z0-9-]*:ack\b")


def _unquoted_comment_start(line: str) -> int:
    """Index of the `#` opening a genuine unquoted comment in `line`, or -1.

    Shares the exact quote-state walk of `_quote_open_at_eol` — the two must
    never disagree on where a comment begins, so both defer to
    `_starts_unquoted_comment` for the word-boundary rule. `safe_tokenize` uses
    this to strip a real trailing comment before the shlex pass: with
    `commenters = ""`, an apostrophe inside a same-line comment
    (`gh pr merge 9 --squash # don't`) otherwise opens a quote shlex never
    closes, `ValueError` fires, and the fail-open arm dropped the whole line's
    argv (issue #1091, extending the #972/#987 quote-fold). A `#` inside a quote
    or after a non-boundary char (`--format=%h#%s`) is not a comment and returns
    -1 there.
    """
    i, n = 0, len(line)
    quote = ""
    arith = 0
    subst: list[str] = []
    while i < n:
        ch = line[i]
        if quote == '"' and line.startswith("$(", i) and not line.startswith("$((", i):
            subst.append(quote)
            quote = ""
            i += 2
            continue
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if _starts_unquoted_comment(line, i):
            return i
        if ch == "\\":
            i += 2
            continue
        if line.startswith("$((", i):
            arith += 1
            i += 3
            continue
        if arith and line.startswith("))", i):
            arith -= 1
            i += 2
            continue
        if ch == ")" and subst and not arith:
            quote = subst.pop()
            i += 1
            continue
        i += 1
    return -1


def _strip_trailing_comment(line: str) -> str:
    """Drop a genuine unquoted trailing comment, preserving `:ack` markers.

    Bash reads nothing past an unquoted `#`, so the comment is never a command —
    but `safe_tokenize` runs shlex with `commenters = ""` (to keep the `:ack`
    opt-out markers as tokens), which means an apostrophe or unbalanced quote in
    the comment text crashes the whole line's parse. Stripping the comment first
    lets the argv before it survive, while a comment that carries an `:ack`
    marker is left intact so it still tokenizes (issue #1091).
    """
    idx = _unquoted_comment_start(line)
    if idx < 0:
        return line
    if _ACK_MARKER_RE.search(line[idx:]):
        return line  # opt-out marker must still reach the token stream
    return line[:idx]


def safe_tokenize(command: str) -> list[str]:
    """Tokenize with shell operators and line breaks split into tokens.

    Uses shlex.shlex(punctuation_chars=';|&') so that `git push&&echo` and
    `git push;echo` split into `['git', 'push', '&&', 'echo']` etc. Plain
    shlex.split keeps operators glued to adjacent words, which would let a
    whitespace-free one-liner bypass detection entirely.

    Newlines are a command separator in Bash but shlex's whitespace_split
    consumes them as generic whitespace, flattening multi-line scripts into
    one token stream. We pre-split the raw command on `\\n` and insert a
    synthetic `;` between line tokens so iter_command_starts sees the break.
    Lines that fail to parse (unmatched quote, runaway heredoc, etc.) are
    skipped — better a silent pass than a crashed hook.

    Heredoc bodies are blanked out first (`strip_heredoc_bodies`): they hold
    data, not commands, so without that pass a commit message quoting a gated
    command is tokenized as that command (issue #985).

    A bash line continuation (a `\\` immediately followed by a newline) is
    *not* a command separator — it splices the two physical lines into one
    logical line. We rejoin those before the newline split so argv[0] on the
    leading line is preserved. Without this, the dangling `\\` left on the
    first line makes shlex raise ``ValueError: No escaped character`` and the
    ``except ValueError`` arm below would silently drop the entire first line
    (issue #510 — neutralised dozens of hooks).

    A newline *inside* a quote is data, not a separator, so physical lines are
    folded into logical lines first (`_logical_lines`) and the newline is kept
    inside the resulting token. Without that fold, the line holding the opening
    quote and the line holding the closing one each raised ``ValueError: No
    closing quotation`` and were dropped by the arm above: a multi-line
    ``gh pr comment --body '…'`` lost its ``gh`` argv[0] (issue #972) and a real
    command riding on the closing line vanished outright, blinding all three
    merge gates (issue #987). A newline *outside* a quote is still a separator
    and still becomes a synthetic ``;``.

    ``commenters = ""`` is unchanged, so the ``# side-effect:ack`` opt-out
    marker still tokenizes. Comments are excluded from quote-state tracking
    only — an unquoted ``#`` never opens a quote, so an apostrophe in
    ``git status # don't do this`` cannot swallow the next line's command.

    A *same-line* trailing comment is a second face of that apostrophe hazard:
    with no newline to fold, ``gh pr merge 9 --squash # don't`` handed the
    apostrophe-bearing comment straight to shlex, which raised ``ValueError: No
    closing quotation`` and the ``except ValueError`` arm dropped the entire
    line's argv (issue #1091, extending #972/#987). Each logical line therefore
    has its genuine unquoted trailing comment stripped first
    (``_strip_trailing_comment``) — except a comment carrying a ``:ack`` marker,
    which is left intact so the opt-out still tokenizes.
    """
    # Splice bash line continuations (`\` + newline) into one logical line so
    # the leading line's argv[0] survives the per-line shlex pass below. A
    # backslash that is itself escaped (`\\` then newline) is a literal
    # backslash followed by a real newline separator, so only collapse an
    # odd-length run of trailing backslashes.
    command = re.sub(r"(?<!\\)((?:\\\\)*)\\\n", r"\1 ", command)
    command = strip_heredoc_bodies(command)
    # Strip a genuine unquoted trailing comment from each logical line before
    # the shlex pass so an apostrophe in it can't crash the parse (issue #1091);
    # `:ack` opt-out markers are preserved by `_strip_trailing_comment`.
    lines = [
        stripped
        for ln in _logical_lines(command)
        if (stripped := _strip_trailing_comment(ln)).strip()
    ]
    if not lines:
        return []
    tokens: list[str] = []
    for idx, line in enumerate(lines):
        if idx > 0:
            tokens.append(";")
        try:
            lex = shlex.shlex(line, posix=True, punctuation_chars=";|&")
            lex.whitespace_split = True
            lex.commenters = ""  # raw `#` is not a comment here; opt-out marker
            tokens.extend(list(lex))
        except ValueError:
            continue
    return tokens


def iter_command_starts(tokens: list[str]):
    """Yield argv slices at each command start across shell separators."""
    start = 0
    for i, tok in enumerate(tokens):
        if tok in SHELL_SEPARATORS:
            if start < i:
                yield tokens[start:i]
            start = i + 1
    if start < len(tokens):
        yield tokens[start:]


_GROUP_PREFIX_CHARS = "(){}$`"


def strip_prefix(argv: list[str]) -> list[str]:
    """Peel shell keywords, `KEY=VAL` assignments, and wrapper commands off
    the front so argv[0] becomes the real executable.

    Handles (in any order, iteratively):
    - shell keywords (`if`, `then`, `do`, `while`, etc.) — pure syntax, drop
    - env assignments (`FOO=1`) — drop
    - wrapper commands (`env`, `sudo`, `nice`, `time`, `stdbuf`, `ionice`) —
      drop the wrapper plus its option flags. Option flags are peeled
      generically: any `-*` token is consumed, and if it's a known arg-taking
      flag for this wrapper the following value token is peeled too. The
      `--long=value` form counts as a single token and is handled naturally.
    """
    i = 0
    n = len(argv)
    while i < n:
        tok = argv[i]
        # A token made only of shell grouping characters is syntax, not a
        # command. `( gh pr merge 1 )` — with a space after the paren — left
        # `(` at argv[0], and every gate keying on argv[0] went silent:
        # measured on origin/main, `pre-merge-approval-gate` from ask to
        # silent, `block-pr-without-caller-evidence` and
        # `block-pr-without-precommit-evidence` from exit-2 to silent
        # (issue #1193). `(gh …` with no space already normalized because the
        # binary helpers strip the prefix off a token that still carries the
        # name; a lone `(` strips to nothing and matches nothing.
        if tok and not tok.strip(_GROUP_PREFIX_CHARS):
            i += 1
            continue
        if tok in SHELL_KEYWORDS:
            i += 1
            continue
        if ENV_ASSIGN_RE.match(tok):
            i += 1
            continue
        if tok in PREFIX_WRAPPERS:
            wrapper = tok
            i += 1
            opts_with_arg = WRAPPER_OPTS_WITH_ARG.get(wrapper, set())
            while i < n:
                nxt = argv[i]
                if ENV_ASSIGN_RE.match(nxt):
                    i += 1
                    continue
                if not nxt.startswith("-"):
                    break
                if "=" in nxt:
                    # --long=value — value embedded; peel this token only
                    i += 1
                    continue
                if nxt in opts_with_arg and i + 1 < n:
                    # --user admin / -u admin — peel pair
                    i += 2
                    continue
                # bare flag (-E, -i, -oL, etc.) — peel single token
                i += 1
            continue
        break
    return argv[i:]


# Shell grouping / command-substitution chars that may prefix a binary token
# when it sits inside a subshell or substitution (`(gh …)`, `$(gh …)`,
# `` `gh …` ``). Stripped before the basename comparison so the binary-name
# check is not fooled by the wrapper syntax. Mirrors the same constant in the
# commit-gate hooks' `_is_git_binary`.


def _is_gh_binary(token: str) -> bool:
    """True iff `token` names the GitHub CLI binary `gh`.

    Symmetric with `_is_git_binary` in the commit-gate hooks: accepts the bare
    name (`gh`) and any path-prefixed form (`/usr/bin/gh`, `./gh`). gh-based
    PreToolUse gates previously compared `argv[0] == "gh"` exactly, which a
    path prefix (`/usr/bin/gh pr merge`) bypassed silently. Strip a leading run
    of shell grouping / command-substitution chars first so a subshell-wrapped
    form (`(gh …)`, `$(gh …)`) normalizes too — matching the `_is_git_binary`
    pattern.
    """
    stripped = token.lstrip(_GROUP_PREFIX_CHARS)
    return stripped == "gh" or stripped.endswith("/gh")


def _command_spec_key(token: str) -> str:
    """Normalize a command token to its bare name for flag_value_spec lookup.

    Strips a leading run of shell grouping / substitution wrappers (as
    `_is_gh_binary` does) and any path prefix, so a path-prefixed or
    subshell-wrapped invocation (`/usr/bin/gh`, `(gh`) keys the same
    flag_value_spec entry as the bare name. Without this the spec lookup in
    `_resolve_subcommand` / `tokenize_with_roles` was keyed on the literal
    `argv[0]`, so `/usr/bin/gh search … --state all` never resolved the
    `gh search` value-flag spec and the separated `--state all` value went
    unclassified — a path-prefix bypass of the argv[0] fix in #1092 (#1099).
    """
    return token.lstrip(_GROUP_PREFIX_CHARS).rsplit("/", 1)[-1]
