#!/usr/bin/env python3
"""PostToolBatch hook: block a parallel batch that repeats one external mutation.

Issue #1368 — two `gh issue create` calls emitted in ONE parallel batch both
run, and the result is two issues for one intent (praxis #98/#99). No earlier
event can see this: `PreToolUse` fires per call and cannot see its siblings,
and by `Stop` the mutations are long done. `PostToolBatch` is the first event
that receives the whole resolved batch, and it fires before the next model
call, so the block lands while the duplicate is still the top of the context.

What is gated
=============

Every `gh` write in the batch is reduced to `(noun, verb, targets)`. Two
invocations collide when they share a `(noun, verb)` and their target sets
intersect — or when both target sets are empty, which is what a creation
looks like.

- `gh issue create` twice -> both target sets empty -> BLOCK. A creation names
  no target, so two of them cannot be told apart by intent; that is exactly
  what produced #98/#99.
- `gh issue edit 23 34` and `gh issue edit 34 23` -> sets intersect -> BLOCK.
  Argument order is not identity.
- `gh pr comment 12` and `gh pr comment 34` -> disjoint -> PASS. Two comments
  on two PRs is ordinary parallel work.
- `gh issue create` and `gh pr create` -> different nouns -> PASS.

Three classification rules exist because each one was a silent pass
=================================================================

All three were found by review with executable probes against this file, and
each failed in the direction that makes a gate worthless: the call was not
classified, so nothing fired and the output was identical to a clean batch.

1. **Persistent flags precede the noun.** `gh` accepts `gh --repo o/r issue
   create` and `gh issue --repo o/r create`, so fixing on `argv[1]`/`argv[2]`
   missed both. `_split_gh` skips flags — and the value of the one persistent
   flag that takes one, `--repo`/`-R` — wherever they sit.

2. **`-h` is not reliably a help flag.** `gh pr merge 123 --subject -h
   --squash` passes `-h` as the *subject value*, and treating it as help
   dropped a real merge. Only the unambiguous long `--help` exempts a call
   now. That trades a possible false positive (two `... -h` calls blocked) for
   removing a false negative, and the false positive is the safe direction
   here: it is visible, and a hand-kept per-subcommand value-flag table would
   drift silently instead.

3. **A mutation allowlist fails open on every verb it has not heard of.**
   `gh pr revert`, `gh pr review`, `gh issue transfer` were all missing.
   Classification is now inverted: for a known noun, the READ-ONLY verbs are
   enumerated and everything else counts as a mutation. A verb added by a
   future `gh` release therefore defaults to "gated", which fails visibly,
   rather than to "ignored", which does not.

Scope: `issue`, `pr`, `release`. Other nouns (`repo`, `api`, `gist`, `secret`)
are not classified — a duplicate there passes. Widening the noun set is a
change to this hook's threat model and belongs in its own issue, not in a
silent constant edit.

Why exit 2 rather than an advisory
==================================

The mutations have already run, so the block prevents nothing. What it buys is
reconciliation *before* the model continues: the next turn starts from "two
issues exist, one was intended" instead of from a transcript in which the
duplicate scrolled past unremarked. Exit 2 is what puts the message in front of
the model on this event.
"""
from __future__ import annotations

import os
import sys

from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT.parent.parent / "_lib"))
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _shell_tokenize import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402

BYPASS_ENV = "PRAXIS_HOOK_BYPASS_PARALLEL_MUTATION"

# Read-only verbs per noun, enumerated from the installed `gh <noun> --help`
# (2026-09-07). Every other verb of these nouns is treated as a mutation, so a
# verb a future release adds is gated rather than ignored — see rule 3 above.
# `pr checkout` is read-only for this hook's purpose: it moves a LOCAL branch
# and creates no external record.
GH_READ_ONLY: dict[str, frozenset[str]] = {
    "issue": frozenset({"list", "status", "view"}),
    "pr": frozenset({"list", "status", "view", "diff", "checks", "checkout"}),
    "release": frozenset({
        "list", "view", "download", "verify", "verify-asset",
    }),
}

# The only `gh` persistent flag that consumes the following token. `--help`,
# `-h` and `--version` are boolean, so nothing else needs skipping to reach the
# noun and verb.
_GLOBAL_VALUE_FLAGS = frozenset({"--repo", "-R"})

# Long form only, deliberately — see rule 2 in the module docstring.
_HELP_FLAG = "--help"

_BATCH_FIELD = "tool_calls"
"""The measured field name.

The published hooks reference calls the array `tools` and its entries
`tool_output`/`error`; the runtime sends `tool_calls` with `tool_response`
(measured 2026-09-07, Claude Code 2.1.263 — RUNTIME_CONSTRAINTS.md entry 8).
Reading the documented name yields an empty list and the gate passes silently,
so the real name is pinned here with the divergence recorded beside it.
"""


def _bypassed() -> bool:
    return os.environ.get(BYPASS_ENV, "").strip() == "1"


def _batch_entries(payload: dict) -> list[dict]:
    """Return the batch's tool-call entries; [] for any unexpected shape."""
    entries = payload.get(_BATCH_FIELD)
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def _bash_command(entry: dict) -> str | None:
    """The shell command of a Bash entry, or None when the entry is not one."""
    if entry.get("tool_name") != "Bash":
        return None
    tool_input = entry.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    return command if isinstance(command, str) and command.strip() else None


def _is_gh(token: str) -> bool:
    """True for `gh` however it was written — bare, absolute, or via a path."""
    return token == "gh" or token.endswith("/gh")


def _normalize_target(token: str) -> tuple[str | None, str]:
    """Reduce an issue/PR reference to `(repository, identity)`.

    `https://github.com/o/r/issues/42`, `#42` and `42` all name the same
    record *within one repository*, and a batch mixing the forms is still a
    duplicate. The number alone is not an identity, though: issue 42 of one
    repository and issue 42 of another are different records, so a URL carries
    its `owner/repo` out with it. A bare number names whatever repository the
    call resolves to, which the payload does not say, so its repository is
    None — see `_repos_compatible`. A branch name (`gh pr merge my-branch`)
    has no numeric form and is returned as written.
    """
    token = token.rstrip("/")
    parts = token.split("/")
    repo: str | None = None
    if len(parts) >= 5 and parts[-2] in ("issues", "pull", "releases"):
        repo = f"{parts[-4]}/{parts[-3]}"
    tail = parts[-1].lstrip("#")
    return repo, (tail or token)


def _split_gh(argv: list[str]) -> tuple[str, str, list[str], str | None] | None:
    """`(noun, verb, targets, repository)` for a gh call, or None.

    Flags are skipped wherever they appear — before the noun, between noun and
    verb, or after — because `gh` accepts a persistent flag in all three
    places. `--repo`/`-R` is the one that takes a value, so its value is
    consumed rather than read as a target, and it is returned as the call's
    repository scope.

    Target collection stops at the first flag that is NOT `--repo`/`-R`,
    because an unknown flag's value is indistinguishable from a positional and
    reading it as a target would invent an identity. `--repo` is skipped
    instead of stopping the walk: `gh issue edit --repo o/r 1` puts the real
    target after it, and stopping there left the call looking untargeted.
    """
    i = 1
    words: list[str] = []
    repo: str | None = None
    while i < len(argv) and len(words) < 2:
        tok = argv[i]
        if tok.startswith("-"):
            if tok in _GLOBAL_VALUE_FLAGS:
                if i + 1 < len(argv):
                    repo = argv[i + 1]
                i += 2
                continue
            i += 1
            continue
        words.append(tok)
        i += 1
    if len(words) < 2:
        return None
    targets: list[str] = []
    url_repos: set[str] = set()
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("-"):
            if tok in _GLOBAL_VALUE_FLAGS:
                if i + 1 < len(argv):
                    repo = argv[i + 1]
                i += 2
                continue
            break
        target_repo, identity = _normalize_target(tok)
        if target_repo is not None:
            url_repos.add(target_repo)
        targets.append(identity)
        i += 1
    if repo is None and len(url_repos) == 1:
        repo = url_repos.pop()
    return words[0], words[1], targets, repo


def _mutations(command: str) -> list[tuple[str, str, frozenset[str], str | None]]:
    """Every `gh` mutation this command performs.

    One Bash call can hold several — `gh issue create … && gh issue create …`
    is the same duplicate shape written serially — so the walk covers each
    command start rather than only the first.
    """
    found: list[tuple[str, str, frozenset[str], str | None]] = []
    try:
        tokens = safe_tokenize(command)
    except Exception:
        return found
    for start in iter_command_starts(tokens):
        argv = strip_prefix(list(start))
        if len(argv) < 3 or not _is_gh(argv[0]):
            continue
        if _HELP_FLAG in argv:
            continue
        split = _split_gh(argv)
        if split is None:
            continue
        noun, verb, targets, repo = split
        read_only = GH_READ_ONLY.get(noun)
        if read_only is None or verb in read_only:
            continue
        found.append((noun, verb, frozenset(targets), repo))
    return found


def _repos_compatible(first: str | None, second: str | None) -> bool:
    """Whether two calls can be acting on the same repository.

    None means the call named no repository, so it resolves to whatever the
    working directory points at — unknowable from the payload. An unknown
    scope is therefore compatible with every scope rather than with none: a
    batch mixing `gh issue edit <url for 42>` with `gh issue edit 42` is the
    duplicate this gate exists for, and treating the unknown as its own
    distinct repository would let it through. Two explicitly different
    repositories are the one case that cannot collide.
    """
    return first is None or second is None or first == second


def _collision(payload: dict) -> tuple[str, str, int] | None:
    """`(noun, verb, count)` for the first colliding group, else None.

    Two invocations of one `(noun, verb)` collide when they could be acting on
    the same repository and their target sets intersect, or when both sets are
    empty — the creation case, where no target distinguishes them.
    """
    groups: dict[tuple[str, str], list[tuple[frozenset[str], str | None]]] = {}
    for entry in _batch_entries(payload):
        command = _bash_command(entry)
        if command is None:
            continue
        for noun, verb, targets, repo in _mutations(command):
            groups.setdefault((noun, verb), []).append((targets, repo))

    for (noun, verb), calls in groups.items():
        for a in range(len(calls)):
            for b in range(a + 1, len(calls)):
                (first, first_repo), (second, second_repo) = calls[a], calls[b]
                if not _repos_compatible(first_repo, second_repo):
                    continue
                if (not first and not second) or (first & second):
                    return noun, verb, len(calls)
    return None


@fail_open
def main() -> int:
    payload = read_payload()
    if not isinstance(payload, dict):
        return 0
    if _bypassed():
        return 0

    found = _collision(payload)
    if found is None:
        return 0
    noun, verb, count = found

    emit_block(
        rule_name="parallel gated mutation",
        why=(
            f"this batch ran `gh {noun} {verb}` {count} times in parallel on an "
            "overlapping target — one intent, more than one external record"
        ),
        correct_path=(
            "verify what actually landed before continuing "
            f"(`gh {noun} list --limit 5`), close or delete the surplus, then "
            "re-issue the remaining mutations one call per message"
        ),
        bypass_env=BYPASS_ENV,
        reference="CLAUDE.md → Bulk Operation Pre-Enumeration; issue #1368",
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
