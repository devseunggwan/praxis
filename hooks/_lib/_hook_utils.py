"""Shared tokenization helpers for praxis PreToolUse(Bash) hooks.

Three hooks reuse the exact same `safe_tokenize` / `strip_prefix` /
`iter_command_starts` pipeline plus the constants they consume:

- `block-gh-state-all.py` — blocks invalid `gh search ... --state all`
- `side-effect-scan.py` — surfaces collateral side effects via `ask`
- `memory-hint.py` — emits stderr hints for `hookable: true` memories

Per the TODO that lived at `block-gh-state-all.py:25`, this module is the
extraction triggered by the third consumer (issue #139). The helpers move
verbatim — see git history for the prior duplicated copies.

Issue #263 adds the role-aware token API (`tokenize_with_roles`, `Token`,
`TokenRole`) which centralizes role-state reconstruction that each caller
was previously reimplementing — see PR #251 / #252 codex review rounds 1-3
for the 7 defects that converged on this root cause.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum
from typing import Optional


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

    A bash line continuation (a `\\` immediately followed by a newline) is
    *not* a command separator — it splices the two physical lines into one
    logical line. We rejoin those before the newline split so argv[0] on the
    leading line is preserved. Without this, the dangling `\\` left on the
    first line makes shlex raise ``ValueError: No escaped character`` and the
    ``except ValueError`` arm below would silently drop the entire first line
    (issue #510 — neutralised dozens of hooks).

    Caller note: a multi-line value inside a quoted flag (e.g.
    ``gh pr create --body "line1\\nline2"``) is split at the unescaped
    newline, separating ``--body`` from its value. In test payloads, use a
    heredoc-assigned variable instead::

        BODY=$(cat <<'EOF'
        Caller chain verified: ...
        EOF
        )
        gh pr create --body "$BODY"
    """
    # Splice bash line continuations (`\` + newline) into one logical line so
    # the leading line's argv[0] survives the per-line shlex pass below. A
    # backslash that is itself escaped (`\\` then newline) is a literal
    # backslash followed by a real newline separator, so only collapse an
    # odd-length run of trailing backslashes.
    command = re.sub(r"(?<!\\)((?:\\\\)*)\\\n", r"\1 ", command)
    lines = [ln for ln in command.split("\n") if ln.strip()]
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
_GROUP_PREFIX_CHARS = "(){}$`"


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


# ---------------------------------------------------------------------------
# Compound-Bash cascade advisory (issue #229)
# ---------------------------------------------------------------------------
#
# When a PreToolUse(Bash) hook rejects a compound command (block, or denied
# ask), bash never runs ANY part — including state-changing redirects, mkdir,
# download-to-file, etc. The classic failure mode is
# `cat <<EOF > /tmp/body.md && gh pr create --body-file /tmp/body.md` where
# the agent assumes the file was written by the rejected redirect and retries
# the second half, hitting `No such file or directory`.

STATE_CHANGING_COMMANDS = frozenset({
    "mkdir", "tee", "cp", "mv", "ln", "rm", "touch",
    "rsync", "dd", "install",
})

# curl: -o/-O/--output/--remote-name write the response body to a file.
# wget: -O/--output-document write the response body. wget's -o is the log
# file, NOT a download target — excluded to avoid false positives on
# `wget -o log.txt URL` (which writes only the log, not a file the agent
# would assume exists at the URL's name).
_CURL_OUTPUT_FLAGS = frozenset({"-o", "-O", "--output", "--remote-name"})
_WGET_OUTPUT_FLAGS = frozenset({"-O", "--output-document"})


def _segment_has_redirect(argv: list[str]) -> bool:
    """True iff argv contains an unquoted `>`, `>>`, or `<<` redirect.

    safe_tokenize strips quotes but preserves internal whitespace, so a token
    containing whitespace cannot be an unquoted shell word — any redirect-like
    substring is literal (e.g. `--body "<<EOF foo"` tokenizes to `<<EOF foo`,
    which is a quoted string, not a heredoc). Tokens without whitespace that
    start with or embed `<<` / `>` / `>>` are treated as redirects, covering
    standalone (`>`, `<<EOF`), attached (`>file`, `<<EOF`), and embedded
    (`foo<<EOF`, `foo>file`) forms.
    """
    for tok in argv:
        if " " in tok or "\t" in tok:
            continue
        if "<<" in tok or ">" in tok:
            return True
    return False


def _segment_has_state_change(argv: list[str]) -> bool:
    argv = strip_prefix(argv)
    if not argv:
        return False
    cmd = argv[0].rsplit("/", 1)[-1]
    if cmd in STATE_CHANGING_COMMANDS:
        return True
    if cmd == "curl":
        for tok in argv[1:]:
            if tok in _CURL_OUTPUT_FLAGS or tok.startswith("--output="):
                return True
    if cmd == "wget":
        for tok in argv[1:]:
            if tok in _WGET_OUTPUT_FLAGS or tok.startswith("--output-document="):
                return True
    return _segment_has_redirect(argv)


def is_compound_command(command: str) -> bool:
    """True iff `command` tokenizes into ≥2 command segments.

    Compound means at least one shell separator (`&&`, `||`, `;`, `|`, or a
    newline-induced synthetic `;`) splits the command into multiple segments.
    Separators inside quoted strings do NOT split (shlex preserves quoting).
    """
    tokens = safe_tokenize(command)
    if not tokens:
        return False
    segments = list(iter_command_starts(tokens))
    return len(segments) >= 2


def has_state_changing_redirect(command: str) -> bool:
    """True iff any segment of `command` performs a file/dir mutation.

    Detects the side-effects that vanish silently when the parent compound
    invocation is rejected at PreToolUse: redirects (`> file`, `>> file`,
    `<<EOF > file`), `mkdir`, `tee`, `cp`/`mv`/`ln`/`rm`/`touch`/`rsync`,
    `curl -o`, `wget -O`.
    """
    tokens = safe_tokenize(command)
    if not tokens:
        return False
    for argv in iter_command_starts(tokens):
        if _segment_has_state_change(argv):
            return True
    return False


COMPOUND_CASCADE_HINT = (
    "\n"
    "Note: this command chains a state-changing step (`> file`, `mkdir`, "
    "`curl -o`, `<<EOF > file`, `tee file`) with another step. PreToolUse "
    "rejection (block or denied ask) aborts ALL parts atomically — files the "
    "redirect/mkdir/download would have created do NOT exist. Before retrying, "
    "use the Write tool to materialize the file first, then issue a separate "
    "Bash call.\n"
)


def compound_cascade_hint(command: str) -> str:
    """Return the canonical cascade-abort hint when applicable, else `""`.

    Called by every PreToolUse(Bash) hook that may reject a command, so the
    same advisory text appears consistently across the chain (issue #229).
    Returns empty string for single commands or compound commands without a
    state-changing segment — the hint is only useful when the cascade was
    likely to leave the agent's mental model out of sync with disk state.
    """
    if not command or not command.strip():
        return ""
    if not is_compound_command(command):
        return ""
    if not has_state_changing_redirect(command):
        return ""
    return COMPOUND_CASCADE_HINT


# ---------------------------------------------------------------------------
# Role-aware token API (issue #263)
# ---------------------------------------------------------------------------
#
# `safe_tokenize` returns a flat token stream; every caller previously had
# to reconstruct role state (flag vs flag-value vs positional vs `--`
# boundary vs `$()` coalesce) ad-hoc. PR #251 / #252 codex review surfaced
# 7 defects converging on this root cause:
#
#   R1: -c value / --merge-base A bypass / false positive  (flag-value set)
#   R2: unquoted $() advisory / -c $(echo k=v) bypass      ($() coalesce)
#   R3: kubectl exec -- mytool --flag false positive        (-- boundary)
#
# `tokenize_with_roles` centralizes these so per-caller logic can iterate
# typed Token objects instead of re-parsing the flat stream.


class TokenRole(Enum):
    """Role of a token within a single command segment.

    Resolution order:
      1. SUBST_RUN — coalesced `$(...)` substitution (one logical token)
      2. SEPARATOR_DD — the literal `--` argv separator
      3. POST_DD — every token after SEPARATOR_DD in the same segment
      4. COMMAND — argv[0] after strip_prefix (first non-env-prefix token)
      5. FLAG — `-X`, `--long`, or `--long=value`
      6. FLAG_VALUE — separate-token value following a value-taking FLAG
      7. POSITIONAL — anything else before SEPARATOR_DD
    """
    COMMAND = "command"
    FLAG = "flag"
    FLAG_VALUE = "flag_value"
    POSITIONAL = "positional"
    SEPARATOR_DD = "separator_dd"
    POST_DD = "post_dd"
    SUBST_RUN = "subst_run"


@dataclass(frozen=True)
class Token:
    """A typed token within a command segment.

    Attributes:
      text: original token text (after shlex unquoting; SUBST_RUN tokens
        contain the joined run, e.g. `"$(git merge-base HEAD origin/main)"`).
      role: the TokenRole.
      flag_name: when role is FLAG_VALUE, the bare flag name this value
        belongs to (e.g. value of `--merge-base A` carries
        flag_name="--merge-base"). None for every other role.
    """
    text: str
    role: TokenRole
    flag_name: Optional[str] = None


def _coalesce_subst_runs(tokens: list[str]) -> list[str]:
    """Collapse unquoted `$(...)` command-substitution runs into single tokens.

    safe_tokenize uses shlex with whitespace_split=True, which is unaware
    of POSIX command substitution. An unquoted `$(git merge-base HEAD x)`
    therefore splits into `['$(git', 'merge-base', 'HEAD', 'x)']` — four
    tokens — and any flag-value-skip logic only consumes the first token.

    This helper walks the token list and merges any run that starts with a
    token containing an *unclosed* `$(` and ends with a token containing
    the matching `)` into a single logical token. Quoted substitutions
    (e.g. `"$(...)"`) are already a single token at this layer.

    The merge is conservative — `$(` must occur with no matching `)` after
    it in the same token to start a run, so balanced single-token forms
    (`"$(...)"` or `--flag=$(short)`) pass through unchanged when already
    balanced. Tokens like `--merge-base=$(git` ... `merge-base` ... `x)`
    are joined into a single SUBST_RUN logical token.

    Unbalanced runs (`$(` with no closing `)`) fall through to the end of
    argv and are treated as a single token — the caller still sees them
    as one element rather than several spurious positionals.
    """
    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if "$(" in tok and ")" not in tok[tok.index("$(") + 2:]:
            j = i + 1
            parts = [tok]
            while j < n:
                parts.append(tokens[j])
                if ")" in tokens[j]:
                    break
                j += 1
            out.append(" ".join(parts))
            i = j + 1
        else:
            out.append(tok)
            i += 1
    return out


def _resolve_subcommand(
    argv: list[str],
    command_global_value_flags: Optional[set[str]] = None,
) -> tuple[str, str]:
    """Return (command_key, subcommand_key) for flag_value_spec lookup.

    argv must already be stripped of env/wrapper prefixes via strip_prefix.
    command_key is argv[0] (e.g. "git"). subcommand_key is the joined form
    "<cmd> <subcmd>" when a non-flag positional token follows the command
    (possibly after some command-global flags). Otherwise subcommand_key
    equals command_key.

    When `command_global_value_flags` is supplied (e.g. `{'-C', '-c'}` for
    git), tokens matching that set consume the following token as their
    value when scanning for the subcommand — this lets `git -C /tmp
    merge-tree ...` resolve to `("git", "git merge-tree")` instead of
    `("git", "git")`.

    Example:
      ["git", "merge-tree", "--name-only", "A"]    → ("git", "git merge-tree")
      ["git", "-C", "/tmp", "merge-tree", ...]     → ("git", "git merge-tree")
                                                      (when command_global_value_flags includes "-C")
      ["git", "status"]                            → ("git", "git status")
      ["kubectl", "--use-protocol-buffers", "get"] → ("kubectl", "kubectl get")
    """
    if not argv:
        return ("", "")
    command = argv[0]
    value_flags = command_global_value_flags or set()
    i = 1
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok == "--":
            return (command, command)
        if "$(" in tok:
            # Only bare `$(...)` (not embedded in `--flag=$(...)`)
            # terminates subcommand parsing. Flag-embedded substitution
            # fills the flag's value slot and shouldn't prevent
            # subcommand detection (e.g., `git --git-dir=$(pwd)/.git
            # merge-tree ...` must still resolve `git merge-tree`).
            if not (tok.startswith("-") and "=" in tok.split("$(", 1)[0]):
                return (command, command)
        if not tok.startswith("-"):
            return (command, f"{command} {tok}")
        # Token is a flag. Skip its value if applicable.
        bare_flag = tok.split("=", 1)[0]
        if "=" not in tok and bare_flag in value_flags and i + 1 < n:
            i += 2
            continue
        i += 1
    return (command, command)


def tokenize_with_roles(
    command: str,
    flag_value_spec: dict[str, set[str]],
) -> list[list[Token]]:
    """Return list of command segments, each a list of typed Token objects.

    Args:
      command: raw bash command string (may contain newlines, &&, ||, ;, |).
      flag_value_spec: maps command/subcommand key to the set of bare flags
        that consume a separate-token value. Keys are either the bare
        command (`"git"`, `"kubectl"`) or `"<cmd> <subcmd>"` form
        (`"git merge-tree"`). Subcommand spec takes precedence over command
        spec when both are present — they are merged so callers can declare
        global flags under the command key and subcommand-specific flags
        under the subcommand key.

    Returns:
      A list of segments. Each segment is the typed Token list for one
      command separated by `&&`, `||`, `;`, `|`, or newline.

    Resolution algorithm (per segment):
      1. safe_tokenize the whole command, split into segments via
         iter_command_starts.
      2. _coalesce_subst_runs to merge unquoted `$(...)` runs.
      3. Determine command + subcommand keys, merge flag_value_spec sets.
      4. Walk tokens:
         - If token contains `$(`, role=SUBST_RUN.
         - Else if token == `--`, role=SEPARATOR_DD; everything after
           becomes POST_DD.
         - Else if this is argv[0] (after strip_prefix conceptually), the
           first non-env/non-wrapper token: role=COMMAND.
         - Else if token starts with `-` (length > 1), role=FLAG. If the
           bare flag (split on `=`) is in the merged value_flags set AND
           the token has no `=`, the next non-SUBST_RUN token is consumed
           as FLAG_VALUE with flag_name set to the bare flag.
         - Else role=POSITIONAL.

    Note on `strip_prefix`: the role API only marks COMMAND on the first
    non-wrapper/non-env token, but it does NOT drop wrapper tokens — they
    appear in the output as POSITIONAL preceding the COMMAND. Callers that
    want a strict argv view can filter by `tok.role in {COMMAND, FLAG,
    FLAG_VALUE, POSITIONAL}` and apply strip_prefix conceptually via the
    COMMAND token's index. For the proof migration (cli-flag-incompat-
    advisory.py) the existing strip_prefix call still drives subcommand
    resolution; the role API is layered on top.
    """
    raw_tokens = safe_tokenize(command)
    if not raw_tokens:
        return []

    segments_out: list[list[Token]] = []
    for raw_argv in iter_command_starts(raw_tokens):
        argv = _coalesce_subst_runs(list(raw_argv))
        if not argv:
            continue

        # Resolve command + subcommand from the stripped-prefix view, then
        # merge flag-value sets from both keys. Pass the command-level
        # value flags to _resolve_subcommand so `git -C /tmp merge-tree`
        # correctly identifies merge-tree as the subcommand.
        stripped = strip_prefix(argv)
        if stripped:
            command_key_only = stripped[0]
            command_global = flag_value_spec.get(command_key_only, set())
        else:
            command_global = set()
        command_key, subcommand_key = _resolve_subcommand(stripped, command_global)
        value_flags: set[str] = set()
        if command_key and command_key in flag_value_spec:
            value_flags |= flag_value_spec[command_key]
        if subcommand_key and subcommand_key != command_key and subcommand_key in flag_value_spec:
            value_flags |= flag_value_spec[subcommand_key]

        # Locate the COMMAND token index (first token after stripped prefix
        # within the original argv). strip_prefix returns a suffix slice;
        # the command token is the first token of that suffix, which equals
        # argv[len(argv) - len(stripped)] when stripped is non-empty.
        command_idx = len(argv) - len(stripped) if stripped else len(argv)

        seg_tokens: list[Token] = []
        post_dd = False
        i = 0
        n = len(argv)
        while i < n:
            tok = argv[i]

            # POST_DD — once we passed `--`, everything is post-dd. The
            # API contract guarantees no SUBST_RUN / FLAG / FLAG_VALUE
            # roles after SEPARATOR_DD, so this check runs BEFORE the
            # $() branch below — `kubectl exec pod -- $(echo --flag)`
            # must classify the substitution as POST_DD, not SUBST_RUN.
            if post_dd:
                seg_tokens.append(Token(text=tok, role=TokenRole.POST_DD))
                i += 1
                continue

            # SUBST_RUN vs FLAG with embedded $() — when a coalesced token
            # starts with `-` and contains `=` (i.e. equals-form flag with
            # an embedded command substitution like `--merge-base=$(...)`),
            # it is semantically a FLAG (value already embedded), not a
            # free positional substitution. Only free `$(...)` runs (the
            # token itself starts with the substitution) become SUBST_RUN.
            if "$(" in tok:
                if (
                    len(tok) > 1
                    and tok.startswith("-")
                    and "=" in tok.split("$(", 1)[0]
                ):
                    seg_tokens.append(Token(text=tok, role=TokenRole.FLAG))
                    i += 1
                    continue
                seg_tokens.append(Token(text=tok, role=TokenRole.SUBST_RUN))
                i += 1
                continue

            # SEPARATOR_DD — the literal `--` argv separator. We require
            # the literal exact match; `--name-only` and `--=` do not
            # qualify. A `--` before command_idx is the wrapper option
            # terminator form (`env -- cmd`, `sudo -- cmd`) — strip_prefix
            # has already consumed it from `stripped`, so within the
            # wrapper region it is POSITIONAL, not the argv separator.
            if tok == "--":
                if i < command_idx:
                    seg_tokens.append(Token(text=tok, role=TokenRole.POSITIONAL))
                    i += 1
                    continue
                seg_tokens.append(Token(text=tok, role=TokenRole.SEPARATOR_DD))
                post_dd = True
                i += 1
                continue

            # COMMAND — first non-prefix token (only one per segment).
            if i == command_idx:
                seg_tokens.append(Token(text=tok, role=TokenRole.COMMAND))
                i += 1
                continue

            # FLAG / FLAG_VALUE — anything starting with `-` (length >1)
            # other than `--` (handled above).
            if len(tok) > 1 and tok.startswith("-"):
                seg_tokens.append(Token(text=tok, role=TokenRole.FLAG))
                # Determine if this flag consumes the next token as a value.
                # Equals form (`--long=value`) already embeds the value
                # within the FLAG token itself — never consume the next
                # token in that case.
                if "=" not in tok:
                    bare_flag = tok
                    if bare_flag in value_flags and i + 1 < n:
                        next_tok = argv[i + 1]
                        # The value role is FLAG_VALUE regardless of `$()`
                        # content — a `$(...)` substitution that fills a
                        # flag value is semantically a value, not a free
                        # SUBST_RUN. We mark it FLAG_VALUE with the
                        # bare_flag attribution but keep the substitution
                        # text intact.
                        seg_tokens.append(Token(
                            text=next_tok,
                            role=TokenRole.FLAG_VALUE,
                            flag_name=bare_flag,
                        ))
                        i += 2
                        continue
                i += 1
                continue

            # POSITIONAL — fall-through (pre-command env assignments and
            # wrapper tokens land here too; callers can ignore them or
            # use strip_prefix indirectly via the COMMAND token).
            seg_tokens.append(Token(text=tok, role=TokenRole.POSITIONAL))
            i += 1

        segments_out.append(seg_tokens)

    return segments_out


def filter_argv(seg: list[Token]) -> list[Token]:
    """Convenience: drop env/wrapper prefix tokens before the COMMAND.

    Returns the typed Token slice starting at the COMMAND token. Callers
    that previously called strip_prefix on a flat token list can use this
    to get the equivalent typed view in one step.

    If the segment has no COMMAND token (empty or all-env), returns an
    empty list.
    """
    for i, tok in enumerate(seg):
        if tok.role == TokenRole.COMMAND:
            return seg[i:]
    return []
