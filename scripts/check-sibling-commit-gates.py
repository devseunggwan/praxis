#!/usr/bin/env python3
"""Invariant canary: the sibling `git commit` gate list must be derived, not copied.

`side-effect-scan`'s spec states that other `PreToolUse(Bash)` hooks also gate a
`git commit` argv, and enumerates them. That enumeration does **not** justify the
`git-commit` ADVISE tier — issue #1153 removed it from the rationale, which now
rests on reversibility alone, a property of the command rather than of the
installed hook set. What the enumeration still is: a factual claim about what
each platform ships, hand-copied into three prose surfaces with nothing tying
them to the hook registry. This canary ties them.

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

Three things the canary verifies beyond that bare name diff:

1. **Per-host coverage.** `side-effect-scan` itself carries no `hosts` key, so
   its ADVISE tier ships to every platform that installs hooks — but four
   of the eight siblings carry `hosts: ["claude"]`, and
   ``_dispatch.group_members("PreToolUse", "Bash", host)`` applies that
   whitelist at runtime (the generated plugin invokes
   ``_dispatch.sh PreToolUse Bash <host>``). A count derived without the host
   filter is therefore right on `claude` and wrong on `codex` / `cursor`,
   which is the same class of drift this canary exists to catch. `derive()` takes a `host`, the host list is read from
   ``manifests/platforms/*.json`` (only platforms that emit a ``hooks``
   output; every shipped platform does today), and the spec's host table is checked
   row by row, in both directions, against every one of them. A bare
   "<n> sibling" claim in the prose is the canonical, host-unfiltered count;
   the per-host numbers live only in that table.
2. **`gates` / `hosts` field shape.** ``hooks/manifest.schema.json`` types
   both fields, but it is not enforced at read time here — nothing validates
   the manifest before this module indexes it. Left unguarded, ``GATE not in
   gates`` degrades to a *substring* test when the value is a bare string:
   ``"gates": "not-git-commit"`` on `branch-name-check` made `derive()` return
   it, and `check()` then told the maintainer to add a branch-creation gate to
   the commit sibling table. Same hazard on ``hosts``. Both are now required to
   be JSON arrays of strings, and a malformed value is a loud drift rather than
   a silent mis-read.
3. **The second count.** The prose also asserts how many of the siblings appear
   in `verify-commit-flag-override`'s printed deny checklist ("Four of the
   seven …", issue #941). That number was hand-copied on two surfaces with
   nothing pinning it — an eighth gate that also joined the checklist would
   have left both saying "Four" with this canary green. It is now derived from
   the ``← <hook>`` rows of ``GIT_COMMIT_GATE_CHECKLIST`` itself.

   The per-host half of that number is a claim about the *runtime*, not about
   the literal: the checklist prints only the rows the running host installs
   (``render_gate_checklist``, issue #1154). Before that filter existed this
   canary was green while the runtime printed all four rows on `codex` and
   `cursor` — the host table said two. The renderer's *wiring* is therefore
   read structurally here (``_wiring_drifts``): it must be defined, ``main``
   must call it, and ``main`` must not touch the raw literal. A substring
   search for the ``def`` line would pass a renderer nothing calls, which
   prints every row again. What no text-level check can see — a wired
   renderer that returns every row anyway — is pinned behaviourally by
   ``tests/hooks/preflight-gate/test_verify_commit_flag_override.sh``, which
   runs the renderer and the dispatcher once per host.

What this canary does NOT do, deliberately: decide *membership*. Structurally
parsing each hook's source for commit-subcommand detection was considered and
rejected as brittle — the detectors differ (`argv[i] != "commit"`, a shared
`git_commit_titles` extractor, a token walk past global options), and a string
scan for "commit" is not an oracle: `pipefail-advisory` names the subcommand but
only fires on a pipeline, and `branch-name-check` mentions commits while gating
branch creation. Membership stays a human judgement, recorded once in the
manifest and enforced everywhere from there.

It also does not validate the ADVISE tier, on any host. That grade is argued in
the spec from reversibility, which this canary cannot measure and does not try
to; a green run here says only that the prose states the same coverage the
manifest ships. The per-host gap it does surface — the commit-intent siblings
are `claude`-only — is why the coverage claim was retired as a justification
(issue #1153), not a reason to re-tier.

The PROSE table below is the pin point (a third, intentional copy — the same
model as ``scripts/check-hook-token-invariants.py``): if a surface is reworded
into a shape the extractor no longer recognizes, the extraction fails loudly
instead of silently verifying nothing.

Run standalone or via ``scripts/run-tests.sh``. Exit 0 + a verified count on a
clean tree; exit 1 listing each drift on failure.
"""

from __future__ import annotations

import ast
import json
from functools import lru_cache
import re
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]

MANIFEST = "hooks/manifest.json"

# Per-platform packaging manifests. Each carries a `host_id`; only the ones with
# an `outputs` entry of kind "hooks" actually install this hook suite, so only
# those are hosts the demotion has to hold on.
PLATFORMS = "manifests/platforms"

# The gate label carried in a manifest entry's `gates` array. One label today;
# the field is a list so a future category (`git-push`, …) can reuse it.
GATE = "git-commit"

SPEC = "hooks/preflight-gate/side-effect-scan/spec.md"
IMPL = "hooks/preflight-gate/side-effect-scan/impl.py"
TEST = "tests/hooks/preflight-gate/test_side_effect_scan.sh"

# The hook whose deny output reprints part of the sibling list (issue #941). The
# "<n> of the <m> siblings … the checklist" claim is derived from this file.
CHECKLIST = "hooks/preflight-gate/verify-commit-flag-override/impl.py"

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
# a count claim and is skipped. This is the CANONICAL (host-unfiltered) count —
# per-host numbers are pinned by the spec's host table, not by this phrase.
_COUNT_RE = re.compile(r"\b(\w+)\s+siblings?\b", re.IGNORECASE)

# The second, previously unpinned count: how many siblings `verify-commit-flag-
# override` reprints in its own deny checklist. Anchored on the word "checklist"
# so it can never collide with the plain "<n> sibling" phrase above.
_CHECKLIST_COUNT_RE = re.compile(
    r"\b(\w+)\s+of\s+the\s+(\w+)\s+siblings?\s+(?:are|is)\s+(?:also\s+)?the\s+checklist\b",
    re.IGNORECASE,
)

# The checklist itself, in verify-commit-flag-override's source. Each row ends
# in `← <hook-name>`, which is the structural anchor read here.
_CHECKLIST_BLOCK_RE = re.compile(
    r'GIT_COMMIT_GATE_CHECKLIST\s*=\s*"""(.*?)"""', re.DOTALL
)
_CHECKLIST_ARROW_RE = re.compile(r"←\s*([a-z0-9][a-z0-9-]*)")

# The renderer that drops rows the running host does not install (issue #1154),
# and the name of the raw literal it wraps. Without the renderer wired into
# `main`, the checklist prints all four rows everywhere, which makes the
# per-host "in the deny checklist" column below describe behaviour the runtime
# does not have — the state this checker was green through once already.
_RENDERER = "render_gate_checklist"
_CHECKLIST_CONST = "GIT_COMMIT_GATE_CHECKLIST"

# spec.md carries the list as a markdown table; the header row is the anchor.
# Column 2 is the hook's `hosts` whitelist (`all`, or the host names).
_SPEC_TABLE_HEADER = "| Sibling hook |"
_SPEC_ROW_RE = re.compile(r"^\s*\|\s*`([a-z0-9][a-z0-9-]*)`\s*\|([^|]*)\|")

# …and the per-host coverage table: `| <host> | <gates> | <in checklist> |`.
_SPEC_HOST_TABLE_HEADER = "| Host | Sibling commit gates |"
_SPEC_HOST_ROW_RE = re.compile(
    r"^\s*\|\s*`([a-z0-9][a-z0-9-]*)`\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
)

# impl.py carries it as a comma-separated run in the module docstring, opened by
# the "subcommand:" lead-in and closed by the sentence's period. Hook names hold
# no periods, so the first "." after the lead-in is the end of the list.
_IMPL_LIST_RE = re.compile(r"subcommand:(.*?)\.(?:\s|$)", re.DOTALL)
_HOOK_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]*")

# The token the spec's Hosts column uses for "no `hosts` key = every platform".
# Same spelling `scripts/build-plugin-manifests.py:_hook_hosts` writes.
_ALL_HOSTS = "all"


def _read(repo: Path, rel: str) -> str | None:
    try:
        return (repo / rel).read_text(encoding="utf-8")
    except OSError:
        # FileNotFoundError and IsADirectoryError are both OSError subclasses;
        # a moved surface is reported as drift below rather than crashing.
        return None


def _is_str_list(value: object) -> bool:
    """True for a JSON array of strings — the only shape `gates`/`hosts` may take.

    A bare string passes `in` and `not in` as a SUBSTRING test, which is how
    `"gates": "not-git-commit"` used to derive as a `git-commit` gate.
    """
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


@lru_cache(maxsize=8)
def _load_manifest_cached(
    repo_key: str, stamp: tuple[int, int]
) -> tuple[Optional[dict], tuple[str, ...]]:
    """Parse `hooks/manifest.json` once per repo path.

    `derive()` is called once per hook-installing host and `checklist_names()`
    once more, so an uncached read re-parsed the same 101-entry manifest a
    dozen times per run (measured: 12 `openat` calls on it). The parsed value
    is treated as read-only by every caller in this module — nothing here
    mutates the manifest or the entries inside it — so one shared object is
    safe. Drifts are returned as a tuple because `lru_cache` values must not
    be a mutable list a caller could append to. `stamp` is part of the key so
    a rewritten manifest is re-read rather than served stale — see
    `_load_manifest`.
    """
    repo = Path(repo_key)
    raw = _read(repo, MANIFEST)
    if raw is None:
        return None, (f"manifest missing on disk: {MANIFEST}",)
    try:
        return json.loads(raw), ()
    except json.JSONDecodeError as exc:
        return None, (f"manifest is not valid JSON: {exc}",)


def _load_manifest(repo: Path) -> tuple[Optional[dict], list[str]]:
    """Parse the manifest, reusing the parse when the file has not changed.

    The cache key carries the file's mtime and size, not just its path. A
    fixture that rewrites the manifest under one repo path and re-runs
    `check()` — which this module's own test suite does throughout — must see
    the new bytes, and a path-only key would hand it the previous parse.
    A stat failure degrades to an uncacheable key so the read still happens.
    """
    try:
        st = (repo / MANIFEST).stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = (-1, -1)
    manifest, drifts = _load_manifest_cached(str(repo.resolve()), stamp)
    return manifest, list(drifts)


def hook_hosts(repo: Path = REPO) -> tuple[list[str], list[str]]:
    """Return (hosts that install hooks, drift messages).

    Read from `manifests/platforms/*.json` rather than hard-coded, so adding a
    platform automatically widens what the host table has to account for. A
    platform with no `hooks` output (none today) ships skills only and
    never runs `side-effect-scan`, so it is not a host the demotion applies to.
    """
    drifts: list[str] = []
    directory = repo / PLATFORMS
    try:
        files = sorted(p for p in directory.iterdir() if p.suffix == ".json")
    except OSError:
        return [], [f"platform manifests missing on disk: {PLATFORMS}/"]

    hosts: list[str] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            drifts.append(f"{PLATFORMS}/{path.name}: unreadable platform manifest ({exc})")
            continue
        host = data.get("host_id")
        if not isinstance(host, str) or not host:
            drifts.append(f"{PLATFORMS}/{path.name}: no usable 'host_id'")
            continue
        outputs = data.get("outputs") or []
        if any(isinstance(o, dict) and o.get("kind") == "hooks" for o in outputs):
            hosts.append(host)
    if not files:
        drifts.append(
            f"{PLATFORMS}/: no platform manifests found — the per-host table "
            "would verify nothing"
        )
    elif not hosts:
        drifts.append(
            f"{PLATFORMS}/: no platform declares a 'hooks' output — the per-host "
            "table would verify nothing"
        )
    return sorted(set(hosts)), drifts


def derive(repo: Path = REPO, host: Optional[str] = None) -> tuple[list[str], list[str]]:
    """Return (sorted hook names carrying the gate on `host`, drift messages).

    Only `PreToolUse` / `Bash` entries count: the premise being pinned is that a
    *sibling Bash gate* sees the argv, so a `gates` label on any other event is
    itself drift and is reported rather than silently folded in.

    `host=None` is the canonical, unfiltered view. A host name applies the same
    filter `_dispatch.group_members` and `build-plugin-manifests.py` apply: an
    entry is kept iff its `hosts` whitelist is absent OR contains `host`. The
    shape checks below run before the host filter, so the drift list a caller
    gets back is identical for every host.
    """
    drifts: list[str] = []
    manifest, load_drifts = _load_manifest(repo)
    if manifest is None:
        return [], load_drifts

    names: set[str] = set()
    for entry in manifest.get("hooks", []):
        name = entry.get("name", "<unnamed>")

        # Shape first: an unguarded `in` on a bare string is a substring test.
        gates = entry.get("gates")
        if gates is not None and not _is_str_list(gates):
            drifts.append(
                f"{name}: 'gates' must be a JSON array of strings, got "
                f"{type(gates).__name__} {gates!r} — any other shape makes "
                f"`{GATE!r} in gates` a substring test instead of a membership test"
            )
            continue
        hosts = entry.get("hosts")
        if hosts is not None and not _is_str_list(hosts):
            drifts.append(
                f"{name}: 'hosts' must be a JSON array of strings, got "
                f"{type(hosts).__name__} {hosts!r} — any other shape makes the "
                "per-host filter a substring test instead of a membership test"
            )
            continue

        if GATE not in (gates or []):
            continue
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
        if host is not None and hosts is not None and host not in hosts:
            continue
        names.add(name)
    return sorted(names), drifts


def gate_hosts(repo: Path = REPO) -> tuple[dict[str, list[str]], list[str]]:
    """Return `{hook name: hosts whitelist}` for gated entries, `["all"]` if absent.

    Mirrors the spelling `build-plugin-manifests.py:_hook_hosts` writes into the
    generated hook inventory, so the spec's Hosts column reads the same either way.
    """
    manifest, drifts = _load_manifest(repo)
    if manifest is None:
        return {}, drifts
    out: dict[str, list[str]] = {}
    for entry in manifest.get("hooks", []):
        gates = entry.get("gates")
        if not _is_str_list(gates) or GATE not in gates:
            continue
        hosts = entry.get("hosts")
        out[entry.get("name", "<unnamed>")] = (
            list(hosts) if _is_str_list(hosts) and hosts else [_ALL_HOSTS]
        )
    return out, []


def checklist_names(repo: Path = REPO) -> tuple[Optional[list[str]], list[str]]:
    """Hook names `verify-commit-flag-override` reprints in its deny checklist."""
    text = _read(repo, CHECKLIST)
    if text is None:
        return None, [f"deny-checklist source missing on disk: {CHECKLIST}"]
    match = _CHECKLIST_BLOCK_RE.search(text)
    if match is None:
        return None, [
            f"{CHECKLIST}: GIT_COMMIT_GATE_CHECKLIST assignment not found — the "
            "'<n> of the <m> siblings … the checklist' claim cannot be derived, "
            "so nothing was verified"
        ]
    names = sorted(set(_CHECKLIST_ARROW_RE.findall(match.group(1))))
    if not names:
        return None, [
            f"{CHECKLIST}: GIT_COMMIT_GATE_CHECKLIST carries no '← <hook>' rows — "
            "the checklist count cannot be derived, so nothing was verified"
        ]
    return names, _wiring_drifts(text)


def _wiring_drifts(text: str) -> list[str]:
    """Check the deny checklist is emitted THROUGH the host filter, not raw.

    Read structurally rather than by substring: a `def render_gate_checklist`
    that nothing calls satisfies a name search while the runtime prints every
    row again. The two failures this catches are the whole reason the filter
    exists — `main` re-concatenating the literal, and the renderer being
    defined but unwired.

    What it does NOT catch is a renderer that is wired but returns every row
    anyway; no text-level check can. That half is pinned behaviourally, by
    `tests/hooks/preflight-gate/test_verify_commit_flag_override.sh` (T03-T09),
    which runs the renderer and the dispatcher once per host.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [
            f"{CHECKLIST}: could not be parsed ({exc}) — the deny-checklist "
            "wiring was not verified"
        ]

    if not any(
        isinstance(node, ast.FunctionDef) and node.name == _RENDERER
        for node in ast.walk(tree)
    ):
        return [
            f"{CHECKLIST}: no `{_RENDERER}` — the deny checklist is printed "
            "unfiltered, so the per-host 'in the deny checklist' column in "
            f"{SPEC} describes coverage the runtime does not have"
        ]

    main = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        ),
        None,
    )
    if main is None:
        return [
            f"{CHECKLIST}: no `main` — the deny-checklist call site could not "
            "be located, so nothing was verified"
        ]

    names_in_main = {
        node.id for node in ast.walk(main) if isinstance(node, ast.Name)
    }
    drifts: list[str] = []
    if _RENDERER not in names_in_main:
        drifts.append(
            f"{CHECKLIST}: `main` never calls `{_RENDERER}` — the renderer is "
            "defined but unwired, so the deny checklist is printed unfiltered"
        )
    if _CHECKLIST_CONST in names_in_main:
        drifts.append(
            f"{CHECKLIST}: `main` references `{_CHECKLIST_CONST}` directly — "
            f"the raw literal bypasses `{_RENDERER}`, so every host gets every "
            "row again"
        )
    return drifts


def _spec_rows(text: str) -> Optional[list[tuple[str, str]]]:
    """`(name, hosts cell)` per row of the spec.md sibling table, or None."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _SPEC_TABLE_HEADER not in line:
            continue
        rows: list[tuple[str, str]] = []
        for row in lines[i + 1:]:
            if not row.strip().startswith("|"):
                break
            match = _SPEC_ROW_RE.match(row)
            if match:
                rows.append((match.group(1), match.group(2)))
        return rows
    return None


def _spec_names(text: str) -> Optional[list[str]]:
    """Names in the spec.md sibling table, or None if the table is unrecognizable."""
    rows = _spec_rows(text)
    return None if rows is None else [name for name, _hosts in rows]


def _spec_host_rows(text: str) -> Optional[dict[str, tuple[int, int]]]:
    """`{host: (sibling gates, in deny checklist)}` from the per-host table."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _SPEC_HOST_TABLE_HEADER not in line:
            continue
        rows: dict[str, tuple[int, int]] = {}
        for row in lines[i + 1:]:
            if not row.strip().startswith("|"):
                break
            match = _SPEC_HOST_ROW_RE.match(row)
            if match:
                rows[match.group(1)] = (int(match.group(2)), int(match.group(3)))
        return rows
    return None


def _impl_names(text: str) -> Optional[list[str]]:
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


def _word_to_int(word: str) -> Optional[int]:
    lowered = word.lower()
    if lowered in _NUMBER_WORDS:
        return _NUMBER_WORDS[lowered]
    return int(word) if word.isdigit() else None


def _counts(text: str) -> list[int]:
    """Every numeric "<n> sibling(s)" claim in the (normalized) text."""
    found: list[int] = []
    for match in _COUNT_RE.finditer(_normalize(text)):
        value = _word_to_int(match.group(1))
        if value is not None:
            found.append(value)
    return found


def _checklist_counts(text: str) -> list[tuple[int, int]]:
    """Every numeric "<n> of the <m> siblings … the checklist" claim."""
    found: list[tuple[int, int]] = []
    for match in _CHECKLIST_COUNT_RE.finditer(_normalize(text)):
        part, whole = _word_to_int(match.group(1)), _word_to_int(match.group(2))
        if part is not None and whole is not None:
            found.append((part, whole))
    return found


def _host_cell_tokens(cell: str) -> set[str]:
    """Normalize a spec Hosts cell (`` `claude`, `codex` `` / `all`) to a token set."""
    return {tok for tok in re.split(r"[,\s`]+", cell.strip()) if tok}


# Each entry is one prose surface that restates the derived list. `names`
# extracts the enumeration (None = this surface only carries the count);
# `checklist` marks the surfaces that also restate the deny-checklist count.
_SURFACES: list[dict] = [
    {
        "label": "spec.md sibling table",
        "path": SPEC,
        "names": _spec_names,
        "checklist": True,
    },
    {
        "label": "impl.py docstring enumeration",
        "path": IMPL,
        "names": _impl_names,
        "checklist": True,
    },
    {"label": "test file comment", "path": TEST, "names": None, "checklist": False},
]


def _check_host_table(
    repo: Path, text: str, expected: set[str], checklist: Optional[list[str]]
) -> list[str]:
    """Verify the spec's per-host table against a per-host re-derivation."""
    drifts: list[str] = []
    hosts, host_drifts = hook_hosts(repo)
    drifts.extend(host_drifts)

    rows = _spec_host_rows(text)
    if rows is None:
        drifts.append(
            f"spec.md host table ({SPEC}): the '{_SPEC_HOST_TABLE_HEADER} …' table "
            "was not found — the per-host coverage claim this checker pins was "
            "removed or reworded, so nothing was verified"
        )
        return drifts

    for extra in sorted(set(rows) - set(hosts)):
        drifts.append(
            f"spec.md host table ({SPEC}): row for host {extra!r} but no platform "
            f"under {PLATFORMS}/ with that host_id installs hooks"
        )
    for host in hosts:
        derived_here, _ = derive(repo, host)
        in_checklist = (
            len(set(checklist) & set(derived_here)) if checklist is not None else None
        )
        if host not in rows:
            drifts.append(
                f"spec.md host table ({SPEC}): host {host!r} installs hooks but has "
                f"no row — it ships {len(derived_here)} sibling commit gates"
            )
            continue
        said_gates, said_checklist = rows[host]
        if said_gates != len(derived_here):
            drifts.append(
                f"spec.md host table ({SPEC}): host {host!r} row says {said_gates} "
                f"sibling commit gates, manifest derives {len(derived_here)} "
                f"({', '.join(derived_here) or 'none'})"
            )
        if in_checklist is not None and said_checklist != in_checklist:
            drifts.append(
                f"spec.md host table ({SPEC}): host {host!r} row says "
                f"{said_checklist} of them are in the deny checklist, "
                f"{CHECKLIST} derives {in_checklist}"
            )
    # `expected` is the canonical union; a host can never ship more than it.
    for host, (said_gates, _said_checklist) in rows.items():
        if said_gates > len(expected):
            drifts.append(
                f"spec.md host table ({SPEC}): host {host!r} row says {said_gates} "
                f"sibling commit gates, more than the {len(expected)} the manifest "
                "carries in total"
            )
    return drifts


def _check_spec_host_cells(repo: Path, text: str) -> list[str]:
    """Verify the Hosts column of the sibling table against the manifest."""
    drifts: list[str] = []
    rows = _spec_rows(text)
    if rows is None:
        # The unreadable-table drift is already reported by the shared name path.
        return drifts
    declared, gate_drifts = gate_hosts(repo)
    drifts.extend(gate_drifts)
    for name, cell in rows:
        if name not in declared:
            # Reported as "enumerated but carries no gates" by the shared path.
            continue
        said = _host_cell_tokens(cell)
        want = set(declared[name])
        if not said:
            drifts.append(
                f"spec.md sibling table ({SPEC}): {name} has an empty Hosts cell — "
                f"the manifest declares {sorted(want)}"
            )
        elif said != want:
            drifts.append(
                f"spec.md sibling table ({SPEC}): {name} Hosts cell says "
                f"{sorted(said)}, manifest declares {sorted(want)}"
            )
    return drifts


def check(repo: Path = REPO) -> list[str]:
    """Return a list of drift messages; empty list means every surface agrees."""
    derived, drifts = derive(repo)
    expected = set(derived)

    checklist, checklist_drifts = checklist_names(repo)
    drifts.extend(checklist_drifts)
    if checklist is not None:
        for stray in sorted(set(checklist) - expected):
            drifts.append(
                f"deny checklist ({CHECKLIST}): {stray} is listed as a gate that "
                f"also fires on `git commit` but carries no gates {GATE!r} entry "
                "in the manifest"
            )
        checklist_total = len(set(checklist) & expected)
    else:
        checklist_total = None

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

        if surface["checklist"] and checklist_total is not None:
            claims = _checklist_counts(text)
            if not claims:
                drifts.append(
                    f"{label} ({rel}): no '<n> of the <m> siblings … the "
                    "checklist' claim found — the deny-checklist count sentence "
                    "this checker pins was removed or reworded"
                )
            for part, whole in claims:
                if part != checklist_total:
                    drifts.append(
                        f"{label} ({rel}): prose says {part} of the siblings are "
                        f"in the deny checklist, {CHECKLIST} derives "
                        f"{checklist_total}"
                    )
                if whole != len(expected):
                    drifts.append(
                        f"{label} ({rel}): prose says {whole} siblings, manifest "
                        f"derives {len(expected)}"
                    )

    spec_text = _read(repo, SPEC)
    if spec_text is not None:
        drifts.extend(_check_spec_host_cells(repo, spec_text))
        drifts.extend(_check_host_table(repo, spec_text, expected, checklist))

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
    checklist, _ = checklist_names(REPO)
    hosts, _ = hook_hosts(REPO)
    print(
        f"sibling-commit-gate check OK ({len(derived)} gates derived from "
        f"{MANIFEST}, {len(_SURFACES)} surfaces verified, "
        f"{len(hosts)} hook-installing hosts checked)"
    )
    for host in hosts:
        per_host, _ = derive(REPO, host)
        in_checklist = len(set(checklist or []) & set(per_host))
        print(f"    {host:<10} {len(per_host)} sibling gates, {in_checklist} in the deny checklist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
