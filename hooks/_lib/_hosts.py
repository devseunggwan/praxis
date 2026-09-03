"""Runtime host resolution and the manifest's per-host install set (issue #1245).

A hook's printed text routinely names a *sibling* gate — the token it wants, the
remedy that clears it, the next block the author will hit. That reads as
authoritative, so naming a gate the running host does not install is worse than
saying nothing: it asks for a token no gate requires and offers a bypass that
does not exist there. Issue #1154 hit it once (`verify-commit-flag-override`'s
deny checklist, fixed in PR #1236 by deriving the rows from the manifest and
filtering by host); #1245 classified the rest and found two more surfaces.

Three call sites now need the same two facts, so the logic moved here rather
than being copied a third time. It stays deliberately small — the host, and the
set of names that host installs. Deciding *which* rows to drop is each caller's
own concern, because the row shapes differ (a `← <hook>` anchor, a numbered
block, a prose clause).

Fail-open, in the direction that keeps text: every unreadable input degrades to
`None`, and `None` means "print everything". Naming a gate the host does not
install wastes a reader's time; dropping one it *does* install hides the next
block entirely, so wrong-but-complete is the cheaper of the two failures.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HOOKS_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST_PATH = _HOOKS_ROOT / "manifest.json"
_SCHEMA_PATH = _HOOKS_ROOT / "manifest.schema.json"


def runtime_host() -> str | None:
    """Platform host this hook is executing on, or None when it cannot be known.

    Dispatch-group members run *in-process* inside `hooks/_lib/_dispatch.py`
    (`run_one` imports impl.py instead of spawning it), so the dispatcher's own
    argv is still `sys.argv` here — and the generated hooks.json bakes the
    platform into it as `_dispatch.sh <event> <matcher> <host>`. Nothing else
    carries the host down to a member, so argv is the only source available.
    A standalone `python3 impl.py` run has no argv[3] and yields None.
    """
    if len(sys.argv) < 4 or Path(sys.argv[0]).name != "_dispatch.py":
        return None
    return sys.argv[3]


def schema_hosts() -> frozenset[str]:
    """Host ids `hooks/manifest.schema.json` declares, or empty when unreadable.

    Empty degrades every host to "unknown", i.e. the unfiltered text.
    """
    try:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    return frozenset(_find_hosts_enum(schema))


def _find_hosts_enum(node: object) -> list[str]:
    """Depth-first search for the `hosts` property's item enum in the schema."""
    if isinstance(node, dict):
        hosts = node.get("hosts")
        if isinstance(hosts, dict):
            items = hosts.get("items")
            if isinstance(items, dict) and isinstance(items.get("enum"), list):
                return [h for h in items["enum"] if isinstance(h, str)]
        for value in node.values():
            found = _find_hosts_enum(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_hosts_enum(value)
            if found:
                return found
    return []


def installed_hook_names(host: str | None) -> set[str] | None:
    """Hook names the manifest installs on `host`, or None when unfilterable.

    Mirrors the whitelist `_dispatch.group_members` and
    `build-plugin-manifests.py` apply: an entry ships iff its `hosts` field is
    absent or contains `host`.

    A host outside `manifest.schema.json`'s enum — an absent value, or a typo in
    a hand-edited hooks.json — is normalized to None here rather than at each
    call site, so a caller cannot filter against a host the packaging never
    emits and silently drop every row.
    """
    if host is None or host not in schema_hosts():
        return None
    try:
        manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    installed: set[str] = set()
    for entry in manifest.get("hooks", []):
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        hosts = entry.get("hosts")
        if isinstance(hosts, list) and host not in hosts:
            continue
        installed.add(name)
    return installed
