"""Compound-Bash classification and the cascade-abort advisory.

`is_compound_command`, `has_state_changing_redirect`, and the
`compound_cascade_hint` text every rejecting PreToolUse(Bash) hook appends
(DESIGN.md → Compound-Bash cascade advisory, issue #229).

Split out of `_hook_utils.py` in issue #1305; the code moved verbatim.
Imports only `_shell_tokenize`; `_hook_utils` re-exports every name here.
"""
from __future__ import annotations

from _shell_tokenize import iter_command_starts, safe_tokenize, strip_prefix

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
