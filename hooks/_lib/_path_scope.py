"""Lexical scope tests for a file path taken from a tool payload.

A hook that exempts scratch or planning paths has to answer two questions the
naive string test answers wrong, and two hooks answered them wrong the same
way (#1362, and CodeRabbit on #1356 before it):

  • **A relative path is not under an absolute prefix.** `tmp/.env` is an
    ordinary project path. Both callers used to write `"/" + path.lstrip("/")`
    before the prefix test, which handed the scratch exemption to every
    project `tmp/` directory.
  • **`..` has to collapse before the prefix is compared.** `/tmp/../repo/.env`
    starts with the characters `/tmp/` and resolves nowhere near it.

Lexical only — no filesystem access and no symlink resolution. These hooks run
`PreToolUse`, so the path they judge has not been written yet and need not
exist; resolving one that does would make the verdict depend on the runner's
filesystem rather than on the payload, and a hook must answer the same way in
CI, in a worktree, and on the author's machine.
"""
from __future__ import annotations

import posixpath


def normalized(path: str) -> str:
    """`path` with `\\` read as a separator and `.` / `..` collapsed lexically."""
    return posixpath.normpath(path.replace("\\", "/"))


def under_absolute_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    """True when `path` is absolute and lands under one of `prefixes` after
    normalization. Each prefix is written with its trailing slash (`"/tmp/"`),
    so the prefix directory itself does not match — only paths inside it.

    A relative path is never under an absolute prefix, whatever it looks like.
    """
    slashed = path.replace("\\", "/")
    if not slashed.startswith("/"):
        return False
    norm = posixpath.normpath(slashed)
    return any(norm.startswith(prefix) for prefix in prefixes)


def contains_fragment(path: str, fragments: tuple[str, ...]) -> bool:
    """True when the normalized `path` contains one of `fragments`, each
    written slash-bounded (`"/.omc/plans/"`) so it matches whole components.

    Unlike `under_absolute_prefix`, a relative path *can* match: a fragment
    names a directory anywhere in the path, so `.omc/plans/x.md` is as much a
    planning artifact as `/home/u/.omc/plans/x.md`. The leading slash is added
    for that reason — to let the first component match a slash-bounded
    fragment — and it is added after normalization, never before, so `..`
    cannot walk out of the named directory and keep the exemption.
    """
    norm = "/" + normalized(path).lstrip("/")
    return any(fragment in norm for fragment in fragments)
