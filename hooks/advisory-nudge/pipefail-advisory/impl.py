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

ADVISE-channel experiment (issue #874):

  `docs/hook-prune-audit.md` left the delivery-channel question open —
  stderr keeps 24% of this hook's advises recurring, and stderr at exit 0
  reaches only the debug log, never the model (canary #841;
  `_hook_io.py:88-92` states the same for the Stop lane). The audit asked
  for an experiment, not a larger window, and named this hook first: at
  250/1056 it carries the largest advisory load in the 30-day ledger.

  The treatment arm is `hookSpecificOutput.additionalContext`, the channel
  PR #1000 (commit 7262740) validated and shipped for `anchor-comment-gate`
  on this same problem. NOT `systemMessage`, which `_hook_io.py:88-92`
  documents as transcript-only and explicitly "NOT fed to the model".
  `PRAXIS_PIPEFAIL_ADVISORY_CONTEXT=1` turns the arm on; unset, this hook
  behaves byte-identically to its pre-#874 form. An env-var arm switch
  mirrors `PRAXIS_ANCHOR_GATE_ADVISORY` and the other five `*_ADVISORY`
  demotions.

  The stderr line is kept in BOTH arms, unlike #1000 which dropped it. Two
  reasons, both specific to this hook's event:
    1. `_fire_ledger.classify_decision` derives `advise` from *stderr*
       (`_fire_ledger.py:118-119`). Moving the text to stdout would
       reclassify every fire as `pass` and erase the recurrence metric the
       experiment compares the two arms with.
    2. `_dispatch.run_group` forwards every member's stderr unconditionally
       (`_dispatch.py:196-199`), so the control arm keeps working whatever
       the stdout path does. #1000's hook is PostToolUse and runs in its own
       process; this one is a member of the dispatched PreToolUse(Bash)
       group, so it depends on what the dispatcher chooses to forward.

  End-to-end delivery: `run_group` merges every member's non-decision
  `additionalContext` into one `hookSpecificOutput` and writes it, once deny
  and ask have both missed (`_dispatch.py:208-223`). Before that change the
  treatment arm was inert — a member's stdout was dropped inside the
  dispatcher on every platform, since all four generated `hooks.json` files
  route PreToolUse(Bash) through `_dispatch.sh` — so emitting it here was
  necessary but not sufficient. Both halves ship together in this PR; see
  spec.md § "ADVISE-channel experiment" for the measurement.

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
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    ENV_ASSIGN_RE,
    SHELL_KEYWORDS,
    SHELL_SEPARATORS,
    safe_tokenize,
    strip_prefix,
)


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

# gh api method flags that signal mutation when present. Mirrors
# session-intent's GH_API_MUTATING_METHODS — `gh api` bypasses the
# object/verb enum entirely, so a mutating REST call needs its own check.
_GH_API_MUTATING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})

# The three truncating sinks named in issue #788 (both observed generations
# used `| tail`; `head` / `grep` share the same truncate-then-hide-exit-code
# shape). Not exhaustive by design — see spec.md "Known limitations".
_TRUNCATING_BINS = frozenset({"tail", "head", "grep"})

# ADVISE-channel experiment arm switch (issue #874). Exact value "1" only,
# mirroring `PRAXIS_ANCHOR_GATE_ADVISORY`'s convention.
_CONTEXT_ENV = "PRAXIS_PIPEFAIL_ADVISORY_CONTEXT"


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
    target when its substitution never closes within the visible argv —
    i.e. cumulative paren-balance across subsequent tokens stays positive
    to the end (the substitution's closing `)` lives in a LATER pipe-chain
    segment, e.g. `OUT=$(gh` from a pipe that split the substitution's
    own internals). When the substitution closes within this argv — either
    in the same token (`FOO=$(date)`) or spanning a few more tokens when
    its own command has spaces (`STAMP=$(date +%s)` -> tokens
    `'STAMP=$(date'`, `'+%s)'`) — the assignment is a plain, self-contained
    `KEY=VALUE` prefix to a *separate* following command
    (`FOO=$(date) git commit ...`), so this function skips past the whole
    substitution run and keeps scanning for the real command instead of
    extracting from inside it. Shell keywords (`if`, `while`, `{`, ...)
    are skipped the same way, so a parenthesized pipeline after one
    (`if (git commit ... | tail); then ...`) still reaches the grouping-
    char check below instead of stopping at the keyword.
    """
    if not argv:
        return argv
    i = 0
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok in SHELL_KEYWORDS:
            i += 1
            continue
        if not ENV_ASSIGN_RE.match(tok):
            break
        if "$(" not in tok:
            i += 1
            continue
        depth = tok.count("(") - tok.count(")")
        j = i
        while depth > 0 and j + 1 < n:
            j += 1
            depth += argv[j].count("(") - argv[j].count(")")
        if depth > 0:
            head = tok.rsplit("$(", 1)[1]
            return argv[:i] + [head] + argv[i + 1 :]
        if j + 1 < n:
            i = j + 1
            continue
        return argv[i:]
    # Leading grouping / substitution chars: `(cmd`, `$(cmd`, `` `cmd ``.
    if i >= n:
        return argv[i:]
    head = argv[i]
    stripped = head.lstrip(_GROUP_PREFIX_CHARS)
    if stripped == head:
        return argv[i:] if i > 0 else argv
    if stripped:
        return [stripped] + argv[i + 1 :]
    # The whole token was pure grouping chars (e.g. a standalone `(` from
    # `( git commit ... )` with a space after the paren) — drop it and
    # recurse so the real command becomes argv[0], rather than leaving an
    # empty-string argv[0] that fails every basename check downstream.
    return _recover_command_argv(argv[i + 1 :])


def _gh_api_mutating_method(argv: list[str]) -> str | None:
    """Return the HTTP method if `argv` is a `gh api` call carrying a
    mutating method flag (`-X`/`--method`/`-XPOST`/`--method=POST`), else
    None. `gh api ... -X POST 2>&1 | tail -3` masks a failed REST mutation
    exactly like `gh pr merge`/`gh issue create` do, but `gh api` is a
    passthrough verb with no fixed object/verb pair — `_gh_object_verb`'s
    enum never sees it. Mirrors session-intent's `is_gh_mutating()` gh-api
    branch (`hooks/preflight-gate/session-intent/impl.py`).
    """
    argv = strip_prefix(argv)
    if not argv or os.path.basename(argv[0]) != "gh":
        return None
    n = len(argv)
    i = _skip_gh_global_flags(argv, 1)
    if i >= n or argv[i] != "api":
        return None
    j = i + 1
    while j < n:
        tok = argv[j]
        if tok in ("-X", "--method"):
            if j + 1 < n:
                method = argv[j + 1].upper()
                return method if method in _GH_API_MUTATING_METHODS else None
            return None
        if tok.startswith("--method="):
            method = tok.split("=", 1)[1].upper()
            return method if method in _GH_API_MUTATING_METHODS else None
        if tok.startswith("-X") and len(tok) > 2:
            method = tok[2:].upper()
            return method if method in _GH_API_MUTATING_METHODS else None
        j += 1
    return None


def _mutating_description(argv: list[str]) -> str | None:
    """Return a short description if argv is a mutating git/gh segment, else None."""
    argv = _recover_command_argv(argv)
    sub = _git_subcommand(argv)
    if sub is not None and sub in _GIT_MUTATING_SUB:
        return f"git {sub}"
    method = _gh_api_mutating_method(argv)
    if method is not None:
        return f"gh api --method {method}"
    gh = _gh_object_verb(argv)
    if gh is not None:
        obj, verb = gh
        if obj in _GH_MUTATING_VERBS and verb in _GH_MUTATING_VERBS[obj]:
            return f"gh {obj} {verb}"
    return None


# Irreversible-mutation enum for the `&&`-gating predicate (issue #1271).
# Mirrors `side-effect-scan`'s ASK tier rather than reusing
# `_GIT_MUTATING_SUB` / `_GH_MUTATING_VERBS` above, and the difference is
# the whole point: those cover every mutation, while this predicate's
# ground is that the gated command cannot be taken back. `git commit` /
# `merge` / `rebase` / `cherry-pick` / `revert` write only to this
# checkout's `.git` and are recoverable from the same shell, so a masked
# `git status | tail -1 && git commit ...` is noise, not a finding.
# `kubectl` sits in that ASK tier too but has no detector in this hook —
# see spec.md "Known limitations".
_IRREVERSIBLE_GIT_SUB = frozenset({"push"})

_IRREVERSIBLE_GH_VERBS: dict[str, frozenset[str]] = {
    "pr": frozenset({"merge", "create"}),
    "workflow": frozenset({"run"}),
}


def _irreversible_description(argv: list[str]) -> str | None:
    """Return a short description if argv publishes state another party can
    already be reading, else None."""
    argv = _recover_command_argv(argv)
    sub = _git_subcommand(argv)
    if sub is not None and sub in _IRREVERSIBLE_GIT_SUB:
        return f"git {sub}"
    method = _gh_api_mutating_method(argv)
    if method is not None:
        return f"gh api --method {method}"
    gh = _gh_object_verb(argv)
    if gh is not None:
        obj, verb = gh
        if obj in _IRREVERSIBLE_GH_VERBS and verb in _IRREVERSIBLE_GH_VERBS[obj]:
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


def _command_units(tokens: list[str]) -> list[tuple[list[list[str]], str | None]]:
    """Split `tokens` into pipeline units, each paired with the shell
    separator that terminated it (`None` for the last unit).

    A unit is the list of argv segments connected consecutively by `|` —
    a single-segment unit is a command with no pipe. Any segment that is a
    heredoc body line (between an opener like `cat <<EOF` and its matching
    `EOF` marker segment) is excluded from unit-building entirely — it
    never starts, extends, or ends a unit — so example text inside a
    heredoc body cannot be mistaken for a live pipeline.

    Two detectors consume this: `_pipe_chains` keeps only the multi-segment
    units (a mutating command piped into a truncating sink, issue #788),
    and `_masked_gating_advisory` needs the separators as well, because
    what it looks for is a masked unit sitting on the LEFT of `&&` from an
    irreversible one (issue #1271). Splitting once here is what keeps the
    heredoc rule from being written twice.
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

    units: list[tuple[list[list[str]], str | None]] = []
    current_chain: list[list[str]] = []

    def flush_chain(terminator: str | None) -> None:
        """Close the unit being built, recording the separator that ended it.

        `terminator` is the separator sitting between this unit's last
        segment and whatever follows — `None` at end of input. It is the
        field `_masked_gating_advisory` reads, so it must be the separator
        BEFORE the segment that broke the pipe run, not that segment's own
        trailing one.
        """
        nonlocal current_chain
        if current_chain:
            units.append((current_chain, terminator))
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
            # An empty segment appears when two separators land back to
            # back — most commonly the synthetic `;` `safe_tokenize`
            # inserts between physical lines, immediately after a `|`/`|&`
            # that wraps onto the next line (`gh pr merge ... |\n tail
            # -3`, valid bash — no trailing backslash needed after a pipe
            # operator). `cmd | ;` can never be real bash syntax, so a
            # `;` observed right after `|`/`|&` here is always that
            # synthetic artifact, not a real chain break. Preserve the
            # pipe-continuation status through the gap instead of letting
            # the synthetic separator overwrite it.
            if sep_before not in ("|", "|&"):
                sep_before = sep_after
            continue

        opens = _heredoc_open_marker(argv)
        if opens is not None:
            heredoc_end = opens
            flush_chain(sep_before)
            sep_before = sep_after
            continue

        if sep_before in ("|", "|&") and current_chain:
            current_chain.append(argv)
        else:
            flush_chain(sep_before)
            current_chain = [argv]
        sep_before = sep_after

    flush_chain(None)
    return units


def _pipe_chains(tokens: list[str]) -> list[list[list[str]]]:
    """Return only the multi-segment units — the pipe chains of issue #788."""
    return [unit for unit, _ in _command_units(tokens) if len(unit) >= 2]


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


def _masked_gating_text(masked: list[list[str]], gated_desc: str) -> str:
    binaries = []
    for seg in masked:
        stripped = strip_prefix(seg)
        binaries.append(os.path.basename(stripped[0]) if stripped else "?")
    masked_summary = " | ".join(binaries)
    return (
        "[pipefail-advisory] masked exit code gates an irreversible command\n"
        "\n"
        f"  Masked segment: {masked_summary}\n"
        f"  Gated by `&&`: {gated_desc}\n"
        "\n"
        "  The left side of `&&` is a pipeline, so its exit status is the\n"
        "  LAST command's — `tail`/`head`/`grep` return 0 even when the\n"
        "  command upstream of them failed. The `&&` therefore fires on a\n"
        "  precondition that was never actually checked.\n"
        "\n"
        "  Fix: run the left side as its own Bash call and read its result,\n"
        "  or prepend `set -o pipefail;` so the pipeline reports the real\n"
        "  exit code.\n"
        "\n"
        "  Reference: issue #1271 (`git switch main 2>&1 | tail -1 &&\n"
        "  gh pr merge ...` merged even when the branch switch failed)"
    )


def _masked_gating_advisory(tokens: list[str]) -> str | None:
    """Return advisory text when a pipeline whose exit code is masked sits on
    the LEFT of `&&` from an irreversible command, else None.

    The gap this closes (issue #1271): `pipefail-advisory`'s original
    predicate needs the MUTATING command to be the piped one, and
    `inspection-chain-advisory` is silent by spec on any `&&` chain that
    mixes inspection with state change. A chain whose non-mutating segment
    is piped and whose mutating segment is not therefore reaches neither.

    Only `&&` runs are walked. `;` sequences do not gate the command that
    follows them, so a masked exit code there changes nothing.

    Every segment of a gated unit is inspected, not just its first: `&&` gates
    the whole pipeline that follows it, so `… | tail -1 && echo x |
    gh pr merge` puts the irreversible command downstream of a pipe while
    leaving the gating relation exactly as it was.
    """
    units = _command_units(tokens)
    run: list[list[list[str]]] = []

    def scan(group: list[list[list[str]]]) -> str | None:
        masked: list[list[str]] | None = None
        for unit in group:
            if masked is not None:
                for segment in unit:
                    desc = _irreversible_description(segment)
                    if desc:
                        return _masked_gating_text(masked, desc)
            if masked is None and len(unit) >= 2 and _is_truncating_sink(unit[-1]):
                masked = unit
        return None

    for unit, sep in units:
        run.append(unit)
        if sep == "&&":
            continue
        found = scan(run)
        if found:
            return found
        run = []
    return scan(run) if run else None


def _scan_tokens_for_advisory(tokens: list[str]) -> str | None:
    """Return the advisory text for the first mutating-piped-to-sink chain
    found in `tokens`, else None."""
    tokens = _merge_fd_dup_redirects(tokens)
    for chain in _pipe_chains(tokens):
        if not _is_truncating_sink(chain[-1]):
            continue
        for seg in chain[:-1]:
            desc = _mutating_description(seg)
            if desc:
                return _advisory_text(chain, desc)
    return None


def _extract_substitution_bodies(tok: str) -> list[str]:
    """Return the content of every balanced top-level `$(...)` embedded in
    `tok`.

    A *quoted* command substitution (`OUT="$(gh pr merge 1 | tail -3)"`)
    dequotes to a single opaque token — the whole RHS, `|` included,
    joins the `OUT=` prefix as one shlex word instead of splitting into
    separate `|`-delimited tokens the way an *unquoted* substitution
    accidentally does. `_pipe_chains` has no `|` token to split on in
    that single-token case, so the pipeline hiding inside it is invisible
    to the normal scan. This extracts each substitution's inner text so it
    can be re-tokenized and scanned on its own.

    A single quoted token may embed more than one *sibling* substitution
    (`"$(date) $(gh pr merge 1 | tail -3)"` — two independent, adjacent
    `$(...)` runs). Stopping at the first `$(` found would silently skip
    any mutating pipeline hiding in a later sibling, so every top-level
    run is collected — but each one only one level deep: the extracted
    body is not itself searched for a further-nested embedded
    substitution, mirroring this codebase's one-level recursive-premise
    convention.
    """
    bodies: list[str] = []
    i = 0
    n = len(tok)
    while True:
        start = tok.find("$(", i)
        if start == -1:
            return bodies
        depth = 0
        j = start + 2
        closed = False
        while j < n:
            ch = tok[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    bodies.append(tok[start + 2 : j])
                    i = j + 1
                    closed = True
                    break
                depth -= 1
            j += 1
        if not closed:
            return bodies


def _emit_additional_context(advisory: str) -> None:
    """Write the advisory as `hookSpecificOutput.additionalContext` (issue #874).

    Mirrors `anchor-comment-gate`'s shipped non-blocking channel (PR #1000,
    commit 7262740) with two deliberate differences, both from the event:

      • `hookEventName` is `PreToolUse`, matching this hook's manifest entry.
        Claude Code keys the payload on it, so the PostToolUse literal cannot
        be copied over verbatim.
      • No top-level `"continue": True`. #1000 carries it because
        `builtin-task-postuse` established that shape on PostToolUse; on
        PreToolUse `continue` is a stop-processing switch whose default is
        already the behaviour wanted here, and asserting a field whose
        PreToolUse semantics this repo has not verified would add an
        unmeasured claim to an experiment about measurement.

    `ensure_ascii=False` matches #1000; this hook's text is ASCII today, so the
    kwarg only guards a future non-ASCII line from being escaped into noise.
    """
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": advisory,
            }
        },
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")


@fail_open
def main() -> int:
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed
    if not command.strip():
        return 0

    tokens = safe_tokenize(command.replace("\\\n", " "))
    if not tokens:
        return 0

    tokens = _merge_fd_dup_redirects(tokens)
    advisory = _scan_tokens_for_advisory(tokens)
    if advisory is None:
        advisory = _masked_gating_advisory(tokens)
    if advisory is None:
        for tok in tokens:
            for body in _extract_substitution_bodies(tok):
                inner_tokens = safe_tokenize(body)
                if not inner_tokens:
                    continue
                # Same three steps the outer command gets. A substitution body
                # is a command list of its own, so an `&&` chain written inside
                # one gates exactly as it would outside — what does not carry
                # across the boundary is the substitution's own exit code, and
                # that is a different case (see Known limitations in spec.md).
                inner_tokens = _merge_fd_dup_redirects(inner_tokens)
                advisory = _scan_tokens_for_advisory(inner_tokens)
                if advisory is None:
                    advisory = _masked_gating_advisory(inner_tokens)
                if advisory:
                    break
            if advisory:
                break

    if advisory:
        # Control arm, always on: stderr is what `_fire_ledger` reads to
        # classify this fire as `advise`, and the only channel the dispatcher
        # forwards unconditionally. See the module docstring (issue #874).
        sys.stderr.write(advisory + "\n")
        if os.environ.get(_CONTEXT_ENV, "").strip() == "1":
            _emit_additional_context(advisory)

    return 0


if __name__ == "__main__":
    sys.exit(main())
