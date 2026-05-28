#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: nudge "split inspection commands" when 2+
inspection-only commands are chained with `&&`.

Why this exists (issue #469):

A `cmd1 && cmd2 && ... && cmdN` chain stops at the first non-zero exit,
so a probe that returns "no match" (grep exit 1, test exit 1, ls of an
absent path) silently drops every later step. The 2026-05-28 retrospect
session hit this with a 6-step probe chain where step 4 returned no
match and steps 5–6 never ran; the agent then assumed they had been
verified.

Memory entry `feedback_bash_exit_code_if_trap` already documents the
rule ("검사용 명령은 `&&` 체인 금지·별도 호출 split") and was loaded
into context during the failing session, but retrieval at
command-composition time failed. This hook moves a fragment of the
enforcement to the Bash boundary so the agent sees a nudge regardless
of which root cause (retrieval discipline / working-memory bandwidth)
applies in any given session.

Design:

  • **Advisory only** — writes to stderr, exits 0. Never blocks.
  • **Inspection-only chain detection** — the chain triggers ONLY when
    every segment in the chain is an inspection-only command. A mixed
    chain (e.g., `mkdir foo && ls foo`) is the intended pattern of
    "make state, then verify it" and is left silent.
  • **`&&`-only** — `||`, `;`, `|`, `&` are different control-flow
    intents and are not chained for this analysis. `||` is the "fall
    back on failure" pattern, `;` is unconditional sequencing, `|` is
    a pipe, `&` is backgrounding.
  • **Plugin-style binary lookup** — `_segment_is_inspection_only`
    returns True/False for each segment. New inspection commands are
    added by extending `_PURE_INSPECTION_BINS`, `_GIT_INSPECTION_SUB`,
    or `_GH_INSPECTION_SUB`.

Fail-open contract:

  • malformed JSON / non-Bash payload → exit 0
  • empty command → exit 0
  • any uncaught exception in the inner logic → swallowed, exit 0
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOK_DIR.parent.parent / "_lib"))
from _hook_utils import safe_tokenize, strip_prefix  # type: ignore[import-not-found]  # noqa: E402


# Pure inspection binaries: read-only AND capable of returning a non-zero
# exit code on "no match" / "missing" — the precondition for the silent-
# cascade defect this hook nudges against. Constant-zero-exit commands
# (echo / printf / true) and constant-non-zero (false) are intentionally
# excluded because chaining them with `&&` cannot mask a probe failure.
_PURE_INSPECTION_BINS = frozenset({
    "grep", "egrep", "fgrep", "rgrep", "zgrep",
    "find",
    "ls",
    "wc",
    "head", "tail",
    "cat",
    "stat",
    "file",
    "which", "whereis", "type", "command",
    "test", "[",
    "du", "df",
    "basename", "dirname", "realpath", "readlink",
    "tr",
    "sort", "uniq", "cut", "awk",
    "sed",
    "jq",
    "yq",
    "diff", "cmp",
    "date",
    "hostname",
    "uname",
    "pwd",
    "env", "printenv",
})

_GIT_INSPECTION_SUB = frozenset({
    "log", "status", "diff", "show",
    "branch",  # bare or `--list` form; `branch -d/-D/-m` are mutations
    "tag",     # bare form; `tag <name>` creates
    "worktree",  # `worktree list/lock/unlock/prune` — mostly read; conservatively allow
    "rev-parse", "ls-files", "ls-tree", "cat-file", "blame",
    "grep", "describe", "reflog", "shortlog",
    "name-rev", "merge-base", "show-ref",
    "diff-tree", "diff-files", "diff-index",
    "rev-list",
    "config",  # `config --get` is read; `config <key> <value>` is write — conservatively allow (the chain failure mode dominates)
    "fsck", "count-objects",
})

_GH_INSPECTION_TOP_LEVEL = frozenset({
    "search",
    "status",
    "auth",
    "alias",
    "config",
    "browse",
    "help",
    "extension",  # `gh extension list` etc. — mostly read; `install` is mutation but rare in chains
})
# gh's verb-after-noun form: `gh <noun> <verb> …`. Inspection verbs that
# apply across nouns (`pr`, `issue`, `repo`, `release`, …).
_GH_INSPECTION_VERBS = frozenset({
    "view",
    "list",
    "status",
    "diff",
    "checks",
})
_GH_NOUNS = frozenset({
    "pr",
    "issue",
    "repo",
    "release",
    "gist",
    "workflow",
    "run",
    "label",
    "secret",
    "variable",
    "ssh-key",
    "gpg-key",
    "codespace",
    "org",
    "project",
})

# Mutation-flagging tokens for `find` (POSIX exec callouts + -delete).
_FIND_MUTATION_TOKENS = frozenset({
    "-delete", "--delete", "-exec", "-execdir", "-ok", "-okdir",
})


def _is_sed_in_place(tok: str) -> bool:
    """True iff `tok` is a sed in-place flag in either GNU or BSD form.

    GNU sed: `-i`, `--in-place`, `--in-place=EXT`
    BSD sed (macOS): `-i.bak` (extension glued — single token), or
    `-i EXT` (extension as next token; the bare `-i` form is caught
    by the equality check). The BSD glued form is the one the GNU
    `tok == "-i"` check misses.
    """
    if tok in {"-i", "--in-place"}:
        return True
    if tok.startswith("--in-place="):
        return True
    # BSD glued form: -i<EXT>. Avoid colliding with other -i* flags
    # by requiring the suffix to look like a file extension (starts
    # with `.` or is a plain word).
    if tok.startswith("-i") and len(tok) > 2 and tok[2] != "-":
        return True
    return False


def _segment_is_inspection_only(seg: list[str]) -> bool:
    """True iff every binary invocation in `seg` is read-only.

    `seg` is a single command segment (already stripped of preceding
    env-var assignments and wrapper tokens like `time`, `nohup`). The
    function returns False for any segment that:
      - has an empty argv (defensive)
      - invokes a binary not in the inspection allowlist
      - invokes a mutation-form of an inspection tool:
        - `find ... -delete / -exec <mutator> / -execdir <mutator> / -ok / -okdir`
        - `sed -i` (GNU), `sed -i.bak` (BSD), `sed --in-place[=ext]`
        - `awk ... -i inplace` (gawk in-place extension)
    """
    argv = strip_prefix(seg)
    if not argv:
        return False

    bare = os.path.basename(argv[0])

    # Per-binary mutation guards. Each one is scoped to the binary it
    # applies to so the global token set cannot bleed across CLIs.
    if bare == "find":
        if any(tok in _FIND_MUTATION_TOKENS for tok in argv[1:]):
            return False
    if bare == "sed":
        if any(_is_sed_in_place(tok) for tok in argv[1:]):
            return False
    if bare == "awk":
        # gawk: `-i inplace` toggles the in-place library. The token
        # following `-i` carries the library name.
        for i, tok in enumerate(argv[1:], start=1):
            if tok == "-i" and i + 1 < len(argv) and argv[i + 1] == "inplace":
                return False

    if bare in _PURE_INSPECTION_BINS:
        return True

    if bare == "git":
        # `git -C dir <subcmd> ...` — skip git-globals to find subcmd.
        i = 1
        n = len(argv)
        while i < n:
            tok = argv[i]
            if tok == "-C" and i + 1 < n:
                i += 2
                continue
            if tok in {"-c"} and i + 1 < n:
                i += 2
                continue
            if tok.startswith("--git-dir") or tok.startswith("--work-tree") or tok.startswith("--namespace"):
                # both `--git-dir=val` and `--git-dir val` forms — peek
                if "=" in tok:
                    i += 1
                else:
                    i += 2
                continue
            if tok.startswith("-"):
                # unknown git-global flag; skip one token and hope for
                # the best (this is advisory only; false negative is
                # cheaper than a false positive).
                i += 1
                continue
            break
        if i < n:
            return argv[i] in _GIT_INSPECTION_SUB
        return False

    if bare == "gh":
        # gh has two surface shapes:
        #   - top-level inspection: `gh <subcmd>` where <subcmd> is a
        #     verb-only command (`search`, `status`, `auth`, …)
        #   - noun-verb form: `gh <noun> <verb>` (`gh pr view`,
        #     `gh issue list`, `gh repo view`)
        if len(argv) < 2:
            return False
        if argv[1] in _GH_INSPECTION_TOP_LEVEL:
            return True
        if (
            argv[1] in _GH_NOUNS
            and len(argv) >= 3
            and argv[2] in _GH_INSPECTION_VERBS
        ):
            return True
        return False

    return False


def _and_chains(tokens: list[str]) -> list[list[list[str]]]:
    """Split `tokens` into chains, where each chain is a list of segments
    connected by `&&`.

    `&&` continues the current chain; any other shell separator
    (`||`, `;`, `|`, `&`, newline) terminates the chain. Empty segments
    are dropped. Chains of length < 2 are dropped — the analysis only
    cares about actual chains.
    """
    chains: list[list[list[str]]] = []
    current_chain: list[list[str]] = []
    current_segment: list[str] = []

    def flush_segment() -> None:
        nonlocal current_segment
        if current_segment:
            current_chain.append(current_segment)
            current_segment = []

    def flush_chain() -> None:
        nonlocal current_chain
        flush_segment()
        if len(current_chain) >= 2:
            chains.append(current_chain)
        current_chain = []

    for tok in tokens:
        if tok == "&&":
            flush_segment()
        elif tok in {"||", ";", "|", "|&", "&"}:
            flush_chain()
        else:
            current_segment.append(tok)

    flush_chain()
    return chains


def _advisory_text(chain: list[list[str]]) -> str:
    binaries = []
    for seg in chain:
        argv = strip_prefix(seg)
        if argv:
            binaries.append(os.path.basename(argv[0]))
    chain_summary = " && ".join(binaries) or "(unknown)"
    return (
        "[inspection-chain-advisory] `&&`-chained inspection commands\n"
        "\n"
        f"  Detected: {chain_summary}\n"
        "\n"
        "  Inspection commands chained with `&&` silently drop every step\n"
        "  after the first non-zero exit (e.g. `grep` no-match → exit 1\n"
        "  → later steps never run, output looks like \"all passed\").\n"
        "\n"
        "  Fix: split into separate Bash tool calls so each probe's exit\n"
        "  code surfaces independently. Use `;` only if you genuinely\n"
        "  want unconditional sequencing.\n"
        "\n"
        "  Reference: feedback_bash_exit_code_if_trap.md"
    )


def _main_inner() -> int:
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

    for chain in _and_chains(tokens):
        if all(_segment_is_inspection_only(seg) for seg in chain):
            sys.stderr.write(_advisory_text(chain) + "\n")
            return 0  # advisory — first matching chain suffices

    return 0


def main() -> int:
    """Advisory hook — must NEVER break tool execution."""
    try:
        return _main_inner()
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
