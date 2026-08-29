#!/usr/bin/env python3
"""Invariant canary: the sibling `git commit` gate list must be derived, not copied.

`side-effect-scan` demotes its `git-commit` category to ADVISE on one load-bearing
premise: other `PreToolUse(Bash)` hooks already gate a `git commit` argv, so the
ask this hook used to raise was redundant (issue #874). That premise is only as
good as the enumeration behind it — and the enumeration was hand-copied into
three prose surfaces with nothing tying them to the hook registry.

It drifted exactly the way a hand-copied list does. PR #1123 retired one of the
enumerated hooks, edited the count word and the name list, and left the table it
shrank un-re-derived — the commit's own `Not-tested:` trailer says so:
"whether a hook outside the six-row sibling table also keys on the commit
subcommand - the table was shrunk, not re-derived". It did: `block-rename-sweep-
survivors` gates a `git commit` argv and was never in the table.

The fix is a machine-readable source of truth (issue #1127). Each manifest entry
that gates a commit carries::

    "gates": ["git-commit"]

and this canary derives the list from that field, then diffs it — **in both
directions** — against every prose enumeration, and checks the count words in
the prose against ``len(derived)``. A name dropped from a table, a name left in
a table after its hook was retired, and a stale "Six sibling …" all fail here.

What this canary does NOT do, deliberately: decide *membership*. Structurally
parsing each hook's source for commit-subcommand detection was considered and
rejected as brittle — the detectors differ (`argv[i] != "commit"`, a shared
`git_commit_titles` extractor, a token walk past global options), and a string
scan for "commit" is not an oracle: `pipefail-advisory` names the subcommand but
only fires on a pipeline, and `branch-name-check` mentions commits while gating
branch creation. Membership stays a human judgement, recorded once in the
manifest and enforced everywhere from there.

The PROSE table below is the pin point (a third, intentional copy — the same
model as ``scripts/check-hook-token-invariants.py``): if a surface is reworded
into a shape the extractor no longer recognizes, the extraction fails loudly
instead of silently verifying nothing.

Run standalone or via ``scripts/run-tests.sh``. Exit 0 + a verified count on a
clean tree; exit 1 listing each drift on failure.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

MANIFEST = "hooks/manifest.json"

# The gate label carried in a manifest entry's `gates` array. One label today;
# the field is a list so a future category (`git-push`, …) can reuse it.
GATE = "git-commit"

SPEC = "hooks/preflight-gate/side-effect-scan/spec.md"
IMPL = "hooks/preflight-gate/side-effect-scan/impl.py"
TEST = "tests/hooks/preflight-gate/test_side_effect_scan.sh"

# Number words the prose may spell the sibling count with. The prose writes it
# out ("Seven sibling …"), so a digit alone would not catch the drift; both
# spellings are accepted on the reading side and compared as an integer.
_NUMBER_WORDS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}

# Every "<count> sibling(s)" phrase in these files must agree with the derived
# count. A non-numeric qualifier ("no sibling hook gates kubectl apply") is not
# a count claim and is skipped.
_COUNT_RE = re.compile(r"\b(\w+)\s+siblings?\b", re.IGNORECASE)

# spec.md carries the list as a markdown table; the header row is the anchor.
_SPEC_TABLE_HEADER = "| Sibling hook |"
_SPEC_ROW_RE = re.compile(r"^\s*\|\s*`([a-z0-9][a-z0-9-]*)`\s*\|")

# impl.py carries it as a comma-separated run in the module docstring, opened by
# the "subcommand:" lead-in and closed by the sentence's period. Hook names hold
# no periods, so the first "." after the lead-in is the end of the list.
_IMPL_LIST_RE = re.compile(r"subcommand:(.*?)\.(?:\s|$)", re.DOTALL)
_HOOK_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


def _read(repo: Path, rel: str) -> str | None:
    try:
        return (repo / rel).read_text(encoding="utf-8")
    except OSError:
        # FileNotFoundError and IsADirectoryError are both OSError subclasses;
        # a moved surface is reported as drift below rather than crashing.
        return None


def derive(repo: Path = REPO) -> tuple[list[str], list[str]]:
    """Return (sorted hook names carrying the gate, drift messages).

    Only `PreToolUse` / `Bash` entries count: the premise being pinned is that a
    *sibling Bash gate* sees the argv, so a `gates` label on any other event is
    itself drift and is reported rather than silently folded in.
    """
    drifts: list[str] = []
    raw = _read(repo, MANIFEST)
    if raw is None:
        return [], [f"manifest missing on disk: {MANIFEST}"]
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], [f"manifest is not valid JSON: {exc}"]

    names: set[str] = set()
    for entry in manifest.get("hooks", []):
        gates = entry.get("gates") or []
        if GATE not in gates:
            continue
        name = entry.get("name", "<unnamed>")
        if entry.get("event") != "PreToolUse" or entry.get("matcher") != "Bash":
            drifts.append(
                f"{name}: carries gates {GATE!r} on a "
                f"{entry.get('event')}/{entry.get('matcher')} entry — the premise "
                "only holds for PreToolUse(Bash)"
            )
            continue
        role = entry.get("role", "")
        if not (repo / "hooks" / role / name).is_dir():
            drifts.append(
                f"{name}: carries gates {GATE!r} but hooks/{role}/{name}/ "
                "is not on disk"
            )
            continue
        names.add(name)
    return sorted(names), drifts


def _spec_names(text: str) -> list[str] | None:
    """Names in the spec.md sibling table, or None if the table is unrecognizable."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _SPEC_TABLE_HEADER not in line:
            continue
        names: list[str] = []
        for row in lines[i + 1:]:
            if not row.strip().startswith("|"):
                break
            match = _SPEC_ROW_RE.match(row)
            if match:
                names.append(match.group(1))
        return names
    return None


def _impl_names(text: str) -> list[str] | None:
    """Names in the impl.py docstring run, or None if the lead-in is gone."""
    match = _IMPL_LIST_RE.search(text)
    if not match:
        return None
    return _HOOK_NAME_RE.findall(match.group(1))


def _normalize(text: str) -> str:
    """Flatten a prose surface so a count phrase split across lines still reads.

    The same sentence is a `#`-prefixed shell comment, a `•`-bulleted Python
    docstring line and markdown prose; without stripping the leaders, "the six\\n
    # sibling gates" would hide a drifted count from the count regex.
    """
    stripped = re.sub(r"(?m)^[ \t]*(?:#+|[-*•])[ \t]*", " ", text)
    return re.sub(r"\s+", " ", stripped)


def _counts(text: str) -> list[int]:
    """Every numeric "<n> sibling(s)" claim in the (normalized) text."""
    found: list[int] = []
    for match in _COUNT_RE.finditer(_normalize(text)):
        word = match.group(1).lower()
        if word in _NUMBER_WORDS:
            found.append(_NUMBER_WORDS[word])
        elif word.isdigit():
            found.append(int(word))
    return found


# Each entry is one prose surface that restates the derived list. `names`
# extracts the enumeration (None = this surface only carries the count).
_SURFACES: list[dict] = [
    {"label": "spec.md sibling table", "path": SPEC, "names": _spec_names},
    {"label": "impl.py docstring enumeration", "path": IMPL, "names": _impl_names},
    {"label": "test file comment", "path": TEST, "names": None},
]


def check(repo: Path = REPO) -> list[str]:
    """Return a list of drift messages; empty list means every surface agrees."""
    derived, drifts = derive(repo)
    expected = set(derived)

    for surface in _SURFACES:
        label, rel = surface["label"], surface["path"]
        text = _read(repo, rel)
        if text is None:
            drifts.append(f"{label}: file missing on disk: {rel}")
            continue

        extractor = surface["names"]
        if extractor is not None:
            found = extractor(text)
            if found is None:
                drifts.append(
                    f"{label} ({rel}): enumeration not found — the surface was "
                    "reworded out of the shape this checker reads, so nothing "
                    "was verified"
                )
            else:
                actual = set(found)
                for missing in sorted(expected - actual):
                    drifts.append(
                        f"{label} ({rel}): {missing} carries "
                        f"gates {GATE!r} in the manifest but is missing from "
                        "the enumeration"
                    )
                for extra in sorted(actual - expected):
                    drifts.append(
                        f"{label} ({rel}): {extra} is enumerated but carries no "
                        f"gates {GATE!r} entry in the manifest"
                    )

        counts = _counts(text)
        if not counts:
            drifts.append(
                f"{label} ({rel}): no '<n> sibling' count claim found — the "
                "count sentence this checker pins was removed or reworded"
            )
        for count in counts:
            if count != len(expected):
                drifts.append(
                    f"{label} ({rel}): prose says {count} siblings, manifest "
                    f"derives {len(expected)}"
                )
    # A file usually restates the same count two or three times; collapsing the
    # identical messages keeps the report one line per distinct drift. Messages
    # carry their file, so this never merges two surfaces' findings.
    return list(dict.fromkeys(drifts))


def main() -> int:
    # REPO is read at call time, not bound as a default, so a test can point
    # main() at a fixture tree.
    drifts = check(REPO)
    if drifts:
        print("sibling-commit-gate check FAILED:")
        for drift in drifts:
            print(f"  - {drift}")
        return 1
    derived, _ = derive(REPO)
    print(
        f"sibling-commit-gate check OK ({len(derived)} gates derived from "
        f"{MANIFEST}, {len(_SURFACES)} surfaces verified)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
