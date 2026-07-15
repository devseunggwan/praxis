#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: nudge `set -o pipefail` when a mutating
git/gh command's output is piped into `tail` / `head` / `grep`.

Why this exists (issue #788):

Without `pipefail`, a shell pipeline's exit code is the LAST command's
exit code, not the first. `git commit ... | tail -N` or
`gh pr merge ... 2>&1 | tail -3` therefore reports success even when the
mutating command itself failed — the non-zero exit is masked by `tail`'s
own exit 0.

Two generations of the same mechanism have been observed:

  gen-1 (2026-05-23): `git commit ... | tail` swallowed a pre-commit
    hook's exit 1; the chained `&&` push then no-op'd against a stale
    HEAD ("Everything up-to-date").
  gen-2 (2026-07-15): `gh pr merge ... 2>&1 | tail -3` masked a network
    error's non-zero exit; the failure was only caught by reading the
    truncated output text.

Per issue #788's 3-generation threshold, gen-2 promotes this from a
memory-layer reminder (`feedback_bash_exit_code_if_trap`, scoped to
`&&` chains) to a structural PreToolUse advisory scoped to `|` pipes.

Design:

  • **Advisory only** — writes to stderr, exits 0. Never blocks.
  • **Pipe-chain scoped** — only `|`-connected segments are inspected.
    `&&` / `||` / `;` / `&` chains are a different defect class, already
    covered by `inspection-chain-advisory` (`&&`) and out of scope here.
  • **Mutating-command enum** — the pipeline must contain a git
    subcommand from {commit, push, merge, rebase, cherry-pick, revert}
    or a mutating `gh <object> <verb>` (mirrors `side-effect-scan`'s
    git-commit / git-push / gh-merge categories and `session-intent`'s
    `GH_MUTATING_VERBS`) in any non-last segment.
  • **Truncating-sink scoped** — the pipeline's LAST segment must be
    `tail`, `head`, or `grep` — the three sinks named in issue #788 and
    observed in both generations. A pipe ending in something else (e.g.
    `git commit ... | cat`) is silent — `cat` does not truncate, so the
    mutating command's exit code, while still masked by pipefail's
    absence, was not the trigger pattern this hook targets.
  • **`2>&1` fd-dup normalization** — `safe_tokenize`'s shlex
    punctuation_chars=";|&" does not include `>`, so `2>&1` tokenizes as
    three tokens (`2>`, `&`, `1`) with the bare `&` misread as a
    backgrounding separator. Both generations' trigger commands use
    `2>&1`, so this hook merges `<fd>>&<fd-or-dash>` runs back into one
    non-separator token before chain analysis.
  • **Heredoc-body skip** — a heredoc body line containing example text
    like "git commit -m x | tail -3" (the exact false positive hit while
    filing issue #788 itself) must not be mistaken for a live pipeline.
    Mirrors `bash-worktree-existence-advisory`'s heredoc marker
    tracking, adapted to a plain argv (list[str]) walk since this hook
    does not need role-typed tokens.

Fail-open contract:

  • malformed JSON / non-Bash payload → exit 0
  • empty command → exit 0
  • any uncaught exception in the inner logic → swallowed, exit 0
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOK_DIR.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import ENV_ASSIGN_RE, SHELL_SEPARATORS, safe_tokenize, strip_prefix  # type: ignore[import-not-found]  # noqa: E402


# ---------------------------------------------------------------------------
# Mutating-command enum (mirrors side-effect-scan's git-commit / git-push /
# gh-merge categories and session-intent's GH_MUTATING_VERBS)
# ---------------------------------------------------------------------------

_GIT_MUTATING_SUB = frozenset({"commit", "push", "merge", "rebase", "cherry-pick", "revert"})

# Shell grouping / command-substitution chars that may prefix a binary token
# when it sits inside a subshell or substitution (`(git …)`, `$(gh …)`).
# Mirrors `_GROUP_PREFIX_CHARS` in `_hook_utils._is_gh_binary`.
_GROUP_PREFIX_CHARS = "(){}$`"

_GIT_GLOBAL_FLAGS_WITH_ARG = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})

_GH_GLOBAL_FLAGS_WITH_ARG = frozenset({"-R", "--repo", "--hostname", "--color"})

_GH_MUTATING_VERBS: dict[str, frozenset[str]] = {
    "issue": frozenset({"close", "comment", "create", "edit", "delete", "reopen", "lock", "unlock", "transfer"}),
    "pr": frozenset({"create", "comment", "edit", "merge", "close", "reopen", "ready", "review"}),
    "release": frozenset({"create", "edit", "delete", "upload"}),
    "label": frozenset({"create", "edit", "delete"}),
    "workflow": frozenset({"run"}),
}

# The three truncating sinks named in issue #788 (both observed generations
# used `| tail`; `head` / `grep` share the same truncate-then-hide-exit-code
# shape). Not exhaustive by design — see spec.md "Known limitations".
_TRUNCATING_BINS = frozenset({"tail", "head", "grep"})


def _git_subcommand(argv: list[str]) -> str | None:
    """Return the git subcommand token, skipping global flags, else None."""
    argv = strip_prefix(argv)
    if not argv or os.path.basename(argv[0]) != "git":
        return None
    i = 1
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok == "--":
            return None
        if not tok.startswith("-"):
            return tok
        if "=" not in tok and tok in _GIT_GLOBAL_FLAGS_WITH_ARG and i + 1 < n:
            i += 2
            continue
        i += 1
    return None


def _skip_gh_global_flags(argv: list[str], i: int) -> int:
    """Advance `i` past `--` or global gh flags (`-R x`, `--repo x`, ...),
    stopping at the next non-flag token. `gh` accepts global flags both
    before the object (`gh -R x issue create`) and between the object and
    verb (`gh issue -R x create`), so callers invoke this at both points."""
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in _GH_GLOBAL_FLAGS_WITH_ARG and i < n:
            i += 1
    return i


def _gh_object_verb(argv: list[str]) -> tuple[str, str] | None:
    """Return (object, verb) for a `gh <object> <verb>` invocation, else None."""
    argv = strip_prefix(argv)
    if not argv or os.path.basename(argv[0]) != "gh":
        return None
    n = len(argv)
    i = _skip_gh_global_flags(argv, 1)
    if i >= n:
        return None
    obj = argv[i]
    i = _skip_gh_global_flags(argv, i + 1)
    if i >= n:
        return None
    return (obj, argv[i])


def _recover_command_argv(argv: list[str]) -> list[str]:
    """Recover the real argv[0] when fused with an env-assignment and/or
    subshell/command-substitution prefix, so the mutating-command matchers
    are not blinded by forms like `OUT=$(gh pr merge ... | tail -3)` or
    `(git commit ... | tail)`.

    `strip_prefix` treats `OUT=$(gh` as a plain `KEY=VALUE` env assignment
    (matches `ENV_ASSIGN_RE`) and drops the whole token — including the
    fused `gh` — rather than recognizing the assignment carries a command
    substitution. `(git` is not an env assignment and has no basename
    match against `"git"` either, so it slips past both `strip_prefix` and
    the plain `os.path.basename(argv[0]) == "git"` check untouched. A plain
    env assignment may also precede the capture assignment (`LANG=C
    RESULT=$(gh ...)`), so the `$(` search walks past any number of
    leading plain assignments rather than only checking argv[0].

    A `$(` inside an assignment token is only an unresolved recovery
    target when it is *unbalanced* within that token (the substitution's
    closing `)` lives in a later token, e.g. `OUT=$(gh` from a pipe that
    split the substitution's own internals). When the token already
    closes its own `(...)` (`FOO=$(date)`), the assignment is a plain,
    self-contained `KEY=VALUE` prefix to a *separate* following command
    (`FOO=$(date) git commit ...`) — `strip_prefix` already peels those
    correctly, so this function must not touch them.
    """
    if not argv:
        return argv
    for i, tok in enumerate(argv):
        if ENV_ASSIGN_RE.match(tok):
            if "$(" in tok and tok.count("(") > tok.count(")"):
                head = tok.rsplit("$(", 1)[1]
                return argv[:i] + [head] + argv[i + 1 :]
            continue
        break
    # Leading grouping / substitution chars: `(cmd`, `$(cmd`, `` `cmd ``.
    head = argv[0]
    stripped = head.lstrip(_GROUP_PREFIX_CHARS)
    if stripped == head:
        return argv
    if stripped:
        return [stripped] + argv[1:]
    # The whole token was pure grouping chars (e.g. a standalone `(` from
    # `( git commit ... )` with a space after the paren) — drop it and
    # recurse so the real command becomes argv[0], rather than leaving an
    # empty-string argv[0] that fails every basename check downstream.
    return _recover_command_argv(argv[1:])


def _mutating_description(argv: list[str]) -> str | None:
    """Return a short description if argv is a mutating git/gh segment, else None."""
    argv = _recover_command_argv(argv)
    sub = _git_subcommand(argv)
    if sub is not None and sub in _GIT_MUTATING_SUB:
        return f"git {sub}"
    gh = _gh_object_verb(argv)
    if gh is not None:
        obj, verb = gh
        if obj in _GH_MUTATING_VERBS and verb in _GH_MUTATING_VERBS[obj]:
            return f"gh {obj} {verb}"
    return None


def _is_truncating_sink(argv: list[str]) -> bool:
    stripped = strip_prefix(argv)
    if not stripped:
        return False
    # A compact-subshell close paren fuses onto the pipeline's final token
    # (`(git commit ... | tail)` tokenizes the sink as a bare `tail)` when
    # it has no trailing args — `tail -3)` instead fuses onto `-3)`,
    # leaving stripped[0] == "tail" already clean). Strip one trailing `)`
    # from stripped[0] before the basename comparison so the bare-command
    # case is still recognized. None of the truncating binaries end in `)`
    # themselves, so this cannot introduce a false match.
    last = stripped[0]
    if last.endswith(")"):
        last = last[:-1]
    return os.path.basename(last) in _TRUNCATING_BINS


# ---------------------------------------------------------------------------
# `2>&1` fd-dup normalization
# ---------------------------------------------------------------------------

_FD_REDIRECT_RE = re.compile(r"^[0-9]*>>?$")
_FD_TARGET_RE = re.compile(r"^[0-9]+$|^-$")


def _merge_fd_dup_redirects(tokens: list[str]) -> list[str]:
    """Merge `N>&M` / `>&-` fd-duplication redirects split by safe_tokenize
    into a single non-separator token.

    `safe_tokenize` uses shlex punctuation_chars=";|&" — `>` is not
    punctuation, so `2>&1` tokenizes as three tokens: `2>`, `&`, `1`. The
    lone `&` would otherwise be read as a backgrounding SHELL_SEPARATOR,
    splitting a single pipeline (`gh pr merge ... 2>&1 | tail -3` — the
    exact gen-2 trigger command) into two spurious chains and hiding the
    mutating command from the chain this hook actually inspects.
    """
    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        if (
            i + 2 < n
            and _FD_REDIRECT_RE.match(tokens[i])
            and tokens[i + 1] == "&"
            and _FD_TARGET_RE.match(tokens[i + 2])
        ):
            out.append(tokens[i] + tokens[i + 1] + tokens[i + 2])
            i += 3
            continue
        out.append(tokens[i])
        i += 1
    return out


# ---------------------------------------------------------------------------
# Heredoc-body-aware pipe-chain extraction
# ---------------------------------------------------------------------------


def _heredoc_open_marker(argv: list[str]) -> str | None:
    """Return the heredoc end-marker word if `argv` opens a heredoc, else None.

    Handles both the space-separated form (`cat << EOF` -> tokens
    `['cat', '<<', 'EOF']`) and the fused form (`cat <<EOF`, `cat <<-EOF`
    -> a single token `<<EOF` / `<<-EOF`, shlex-dequoted so `<<'EOF'` and
    `<<"EOF"` collapse to the same bare form). `<<<` (here-string, e.g.
    `read x <<< value`) is a distinct bash operator — not a heredoc — and
    is excluded before the generic `<<`-prefix check below, which would
    otherwise match it too (`"<<<".startswith("<<")`).
    """
    for i, tok in enumerate(argv):
        if tok in ("<<", "<<-"):
            if i + 1 < len(argv):
                return argv[i + 1]
            return None
        if tok.startswith("<<<"):
            continue
        if tok.startswith("<<"):
            remainder = tok[2:]
            if remainder.startswith("-"):
                remainder = remainder[1:]
            if remainder:
                return remainder
    return None


def _pipe_chains(tokens: list[str]) -> list[list[list[str]]]:
    """Split `tokens` into pipe chains, skipping heredoc body segments.

    A chain is a list of argv segments connected consecutively by `|`.
    Chains of length < 2 are dropped. Any segment that is a heredoc body
    line (between an opener like `cat <<EOF` and its matching `EOF`
    marker segment) is excluded from chain-building entirely — it never
    starts, extends, or ends a chain — so example text inside a heredoc
    body cannot be mistaken for a live pipeline.
    """
    # Re-split on shell separators ourselves (rather than reusing
    # iter_command_starts) because we need to know WHICH separator
    # followed each segment to tell a `|`-continuation apart from a
    # `&&`/`;`/`&`-terminated new chain. `|&` (pipe stdout+stderr) is not
    # in SHELL_SEPARATORS — safe_tokenize returns it as its own token
    # (punctuation_chars includes `|` and `&`, and shlex coalesces the
    # adjacent pair) — treated as a pipe-continuation separator here since
    # it has the same masked-exit-code shape as a plain `|`.
    raw_segments: list[tuple[list[str], str | None]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in SHELL_SEPARATORS or tok == "|&":
            raw_segments.append((current, tok))
            current = []
        else:
            current.append(tok)
    raw_segments.append((current, None))

    chains: list[list[list[str]]] = []
    current_chain: list[list[str]] = []

    def flush_chain() -> None:
        nonlocal current_chain
        if len(current_chain) >= 2:
            chains.append(current_chain)
        current_chain = []

    heredoc_end: str | None = None
    sep_before: str | None = None  # separator immediately preceding this segment

    for argv, sep_after in raw_segments:
        if heredoc_end is not None:
            # The closing delimiter must be alone on its line (POSIX
            # heredoc semantics) — checking only argv[0] would let a body
            # line like "EOF extra text" close the heredoc early and
            # expose the next body line (which may itself contain a
            # mutating+pipe example) to live chain analysis.
            if argv and len(argv) == 1 and argv[0] == heredoc_end:
                heredoc_end = None
            sep_before = sep_after
            continue

        if not argv:
            sep_before = sep_after
            continue

        opens = _heredoc_open_marker(argv)
        if opens is not None:
            heredoc_end = opens
            flush_chain()
            sep_before = sep_after
            continue

        if sep_before in ("|", "|&") and current_chain:
            current_chain.append(argv)
        else:
            flush_chain()
            current_chain = [argv]
        sep_before = sep_after

    flush_chain()
    return chains


def _advisory_text(chain: list[list[str]], mutating_desc: str) -> str:
    binaries = []
    for seg in chain:
        stripped = strip_prefix(seg)
        binaries.append(os.path.basename(stripped[0]) if stripped else "?")
    chain_summary = " | ".join(binaries)
    return (
        "[pipefail-advisory] mutating command piped without `set -o pipefail`\n"
        "\n"
        f"  Detected: {chain_summary}\n"
        f"  Mutating segment: {mutating_desc}\n"
        "\n"
        "  Without `set -o pipefail`, a pipeline's exit code is the LAST\n"
        "  command's exit code — a failing mutating command upstream of\n"
        "  `tail`/`head`/`grep` reports success even when it failed.\n"
        "\n"
        "  Fix: prepend `set -o pipefail;` to the command, or drop the pipe\n"
        "  and inspect the full output directly.\n"
        "\n"
        "  Reference: issue #788 (gen-2: `gh pr merge ... 2>&1 | tail -3`\n"
        "  masked a network-error exit)"
    )


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    tokens = safe_tokenize(command.replace("\\\n", " "))
    if not tokens:
        return 0
    tokens = _merge_fd_dup_redirects(tokens)

    for chain in _pipe_chains(tokens):
        if not _is_truncating_sink(chain[-1]):
            continue
        for seg in chain[:-1]:
            desc = _mutating_description(seg)
            if desc:
                sys.stderr.write(_advisory_text(chain, desc) + "\n")
                return 0  # advisory — first matching chain suffices

    return 0


if __name__ == "__main__":
    sys.exit(main())
