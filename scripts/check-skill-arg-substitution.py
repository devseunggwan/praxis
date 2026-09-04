#!/usr/bin/env python3
"""Invariant canary: no ``$<digit>`` in a SKILL.md body (issue #1259).

Claude Code substitutes ``$<digit>`` in a SKILL.md with the skill invocation's
arguments at load time. Disk keeps the literal; the body the model reads does
not. So a shell snippet written as ``awk '{print $1}'`` arrives as
``awk '{print claude-2}'``, and awk reads that as *variable ``claude`` minus 2*
— an undefined variable is 0, so the result is ``-2``. A non-empty wrong value
is the worst outcome available: an ``if [ -z "$VAR" ]`` guard passes it
straight through to the next command.

Scope is ``skills/*/SKILL.md`` only. Executable scripts under ``skills/`` —
``claude-recover``, ``cmux-save-sessions`` and friends — are run by the shell
and never loaded as model context, so their positional parameters are correct
and must not be flagged.

``$0`` is a reference like any other. The loader's own expression, read out of
the installed runtime, is::

    e.replace(/\\$(\\d+)(?!\\w)/g, (N, F) => {
      let U = parseInt(F, 10);
      if (_[U] === void 0) return N;
      return D = !0, f(_[U]);
    })

``\\d+`` matches a zero and ``_`` is an ordinary zero-indexed array, so ``$0``
resolves to the *first* argument rather than to nothing — and the whole scale
is shifted with it, ``$1`` being the second. Anything the loader leaves alone
is a safe form here: ``${0}`` (the brace stops ``\\d+`` from matching), ``$@``,
``$*``, ``$#``, and a trailing bare ``$``. Multi-digit ``$10`` is substituted
once eleven arguments are present, and this scan sees it through its ``$1``
prefix.

Known gaps, both false positives rather than misses: ``$0x`` is flagged though
the loader's ``(?!\\w)`` spares it, and ``\\$0`` is flagged though the escape
survives (the loader consumes the backslash and restores a literal ``$``).

Run standalone or via ``scripts/run-tests.sh``. Exit 0 plus a scanned-file
count on a clean tree; exit 1 listing every ``file:line  text`` hit on drift.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_HIT_RE = re.compile(r"\$[0-9]")

# What to write instead, quoted in the failure output so the fix does not need
# a second lookup. Held here rather than in the message string because both the
# check and its test assert on it.
REMEDY = (
    "pass the field through a variable — awk -v f=1 '{print $f}' — or restate "
    "the line without a $<digit> reference"
)


def skill_docs() -> list[Path]:
    """Tracked ``skills/*/SKILL.md`` paths, repo-relative.

    Reads ``git ls-files`` rather than globbing the tree: an untracked scratch
    copy is not what any host loads, so flagging one would be a false failure.
    """
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        p
        for p in (Path(x) for x in out.split("\0") if x)
        if p.name == "SKILL.md" and p.parts[:1] == ("skills",)
    ]


def check() -> list[str]:
    hits: list[str] = []
    for rel in skill_docs():
        try:
            text = (REPO / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable — nothing loadable to substitute
        for lineno, line in enumerate(text.splitlines(), 1):
            if _HIT_RE.search(line):
                hits.append(f"{rel}:{lineno}  {line.strip()}")
    return hits


def main() -> int:
    hits = check()
    if hits:
        print("skill-arg-substitution check FAILED — the loader rewrites these:")
        for h in hits:
            print(f"  - {h}")
        print(f"  fix: {REMEDY}")
        return 1
    print(f"skill-arg-substitution check OK ({len(skill_docs())} SKILL.md scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
