"""Role-aware token API for praxis Bash hooks (issue #263).

`tokenize_with_roles` turns a command into segments of typed `Token`
objects (`TokenRole`), and `filter_argv` drops the env/wrapper prefix so the
real command word lands at `argv[0]`.

Split out of `_hook_utils.py` in issue #1305; the code moved verbatim.
Imports only `_shell_tokenize`; `_hook_utils` re-exports every name here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from _shell_tokenize import (
    _GROUP_PREFIX_CHARS,
    _command_spec_key,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

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
    command_key is the bare name of argv[0] (e.g. "git"), normalized via
    `_command_spec_key` so a path-prefixed / wrapped form keys the same spec
    (#1099). subcommand_key is the joined form
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
    # Normalize to the bare command name so a path-prefixed / wrapped argv[0]
    # (`/usr/bin/gh`) keys the same subcommand spec as `gh` (#1099).
    command = _command_spec_key(argv[0])
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
            command_key_only = _command_spec_key(stripped[0])
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

    A token made only of shell grouping characters is skipped, so the real
    command word lands at `argv[0]`. `( gh pr merge 1 )` — with a space after
    the paren — types `(` as COMMAND and leaves `gh` POSITIONAL, so every gate
    keying on `argv[0]` silently missed it: measured on `origin/main`,
    `pre-merge-approval-gate` went from ask to silent, and both
    `block-pr-without-caller-evidence` and
    `block-pr-without-precommit-evidence` from exit-2 to silent (issue #1193).
    `(gh …` with no space already normalized, because `_is_gh_binary` strips
    the prefix off a token that still carries the name; a lone `(` strips to
    nothing and matches no binary at all. `{ …; }` was never affected — the
    tokenizer does not make `{` a COMMAND.
    """
    for i, tok in enumerate(seg):
        if tok.role != TokenRole.COMMAND:
            continue
        j = i
        while j < len(seg) and not seg[j].text.strip(_GROUP_PREFIX_CHARS):
            j += 1
        return seg[j:]
    return []
