#!/usr/bin/env python3
"""PreToolUse advisory: warn (or block in strict mode) on edits to sensitive
files — `.env`, private keys, `~/.ssh/`, `credentials`, `.netrc`, `.npmrc`.

Issue #464. Source pattern: `Yeachan-Heo/gajae-code`
`examples/hooks/protected-paths.ts` (MIT) — adapted to the praxis
PreToolUse(Edit|NotebookEdit|Write) shape with path-component-exact matching
(NOT substring — `node_modules_backup/` style false positives are explicitly
disallowed).

Default behaviour: advisory only (stderr nudge, exit 0). The agent sees the
warning and decides whether to proceed. Strict mode (`PRAXIS_PROTECTED_PATHS_STRICT=1`)
escalates to exit 2 so Claude Code blocks the call. Bypass
(`PRAXIS_HOOK_BYPASS_PROTECTED_PATHS=1`) skips all checks.

Detection model:

  • **Basename exact** — `.env`, `.netrc`, `.npmrc`, `credentials`, `id_rsa`,
    `id_dsa`, `id_ecdsa`, `id_ed25519`.
  • **`.env.<env>` prefix** — `.env.production`, `.env.local` etc. Allowlist:
    `.env.example`, `.env.sample`, `.env.template` (exact basename, not
    substring).
  • **Extension** — `*.pem`, `*.key`, `*.p12`, `*.keystore`.
  • **Directory component** — any path containing a `.ssh`, `.aws`, `.kube`,
    or `.gnupg` directory component matches (e.g. `.ssh/config`,
    `.aws/credentials`, `.kube/config`, `.gnupg/secring.gpg` — public keys
    included under `.ssh/` because the directory itself is the trust anchor).

Skip / allowlist:

  • Test fixtures — paths containing a `/fixtures/`, `/__fixtures__/`,
    `/test-data/` or `/testdata/` directory component (allows checked-in
    `.env.example` -style fixtures and seed `.pem` test certificates).
  • Planning artifacts — `/tmp/`, `.omc/plans/`, `.claude/projects/`.
  • Self-edit — paths inside `CLAUDE_PLUGIN_ROOT` (so this very hook can be
    edited).
  • Public-key explicit allow — `id_rsa.pub`, `id_dsa.pub`, `id_ecdsa.pub`,
    `id_ed25519.pub` basename when the file is NOT under a `.ssh/` directory
    (e.g. `pubkeys/id_rsa.pub` in a config repo). Under `.ssh/` the
    directory-component rule still fires.

Fail-open contract:

  • Malformed JSON stdin → exit 0.
  • Non-Edit/Write/NotebookEdit tool → exit 0.
  • Missing / empty `file_path` → exit 0.
  • Any uncaught exception in the inner logic → swallowed, exit 0.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _path_scope import (  # type: ignore[import-not-found]  # noqa: E402
    contains_fragment,
    under_absolute_prefix,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402

TARGET_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})

# The leading dot is significant — `dummy.env` does NOT match `.env` because
# the basename differs.
_PROTECTED_BASENAMES = frozenset({
    ".env",
    ".netrc",
    ".npmrc",
    ".git-credentials",  # plain-text GitHub token store (git-credential-store)
    ".pgpass",           # PostgreSQL password file
    "credentials",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
})

# `.env.<env>` prefix family. Basename must start with literal `.env.` AND
# have at least one suffix character. `.env` alone is matched by
# `_PROTECTED_BASENAMES` above, not here.
_ENV_PREFIX = ".env."

# Exact-match allowlist for env templates — `.env.example.local` is NOT
# exempt (it is a project-specific override of the template, not a template).
_ENV_ALLOWLIST_BASENAMES = frozenset({
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.defaults",
})

_PROTECTED_EXTENSIONS = frozenset({
    "pem",
    "key",
    "p12",
    "keystore",
})

_FIXTURE_DIRECTORIES = frozenset({
    "fixtures",
    "__fixtures__",
    "test-data",
    "testdata",
    "test_data",
})

# macOS resolves `/tmp` to `/private/tmp` under `os.path.realpath`; explicitly
# cover both so a Claude Code call that already realpath'd the path doesn't
# slip through.
_PLANNING_TMP_PREFIXES = ("/tmp/", "/private/tmp/")
_PLANNING_FRAGMENTS = (
    "/.omc/plans/",
    "/.claude/projects/",
)

# Directory components that act as trust anchors — any file nested inside
# these directories inherits the sensitivity of the credential store.
# `.ssh/id_rsa.pub` matches even though the basename is otherwise allow-listed.
_PROTECTED_DIR_COMPONENTS = frozenset({
    ".ssh",
    ".aws",    # AWS CLI credentials and config
    ".kube",   # Kubernetes kubeconfig (service-account tokens, cluster certs)
    ".gnupg",  # GnuPG key material and trustdb
})

# Under `.ssh/`, the directory-component rule fires first (the directory IS
# the trust anchor); the public-key allow only applies when the file is
# elsewhere (e.g. `pubkeys/id_rsa.pub` in a config repo).
_PUBKEY_BASENAMES = frozenset({
    "id_rsa.pub",
    "id_dsa.pub",
    "id_ecdsa.pub",
    "id_ed25519.pub",
})


def _normalize(path: str) -> PurePosixPath:
    """Posix-normalize the path (handles backslashes, redundant separators)."""
    return PurePosixPath(path.replace("\\", "/"))


def _path_components(path: str) -> tuple[str, ...]:
    """Return the path's components (skipping the `/` root marker if present)."""
    parts = _normalize(path).parts
    if parts and parts[0] == "/":
        return parts[1:]
    return parts


def _file_path_from_payload(tool_name: str, tool_input: dict) -> str:
    if tool_name == "NotebookEdit":
        return (tool_input.get("notebook_path") or "").strip()
    return (tool_input.get("file_path") or "").strip()


def _self_plugin_root() -> str:
    """Walk up from this file looking for the `hooks/manifest.json` marker so
    moves of this file don't silently break the self-edit fallback.
    """
    here = os.path.abspath(__file__)
    cur = os.path.dirname(here)
    while cur and cur != os.path.dirname(cur):
        if os.path.isfile(os.path.join(cur, "hooks", "manifest.json")):
            return cur
        cur = os.path.dirname(cur)
    return ""


def _is_self_edit(path: str) -> bool:
    """True if path is inside CLAUDE_PLUGIN_ROOT (the praxis plugin itself).

    Relative paths are rejected unconditionally: `os.path.abspath` resolves
    them against the hook process's cwd, so when cwd happens to be inside the
    plugin root a bare `.env` would resolve to a plugin-root path and trigger
    a false-positive self-edit exemption — bypassing the check entirely in
    strict mode.  Only absolute paths carry enough location signal to be
    trusted.
    """
    if not os.path.isabs(path):
        return False
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
    if not plugin_root:
        plugin_root = _self_plugin_root()
    if not plugin_root:
        return False
    try:
        rel = os.path.relpath(os.path.normpath(path), os.path.abspath(plugin_root))
    except ValueError:
        return False
    # rel == ".." means `path` IS the parent of plugin_root (outside it), so it
    # must not be exempted; `rel == "."` (path == plugin_root) is inside.
    return rel != ".." and not rel.startswith(".." + os.sep)


def _is_planning_artifact(path: str) -> bool:
    """True for a scratch or planning path the advisory does not apply to.

    Scratch is an **absolute** path under `/tmp/` or `/private/tmp/` after
    lexical normalization: a relative `tmp/.env` is a project path, and
    `/tmp/../repo/.env` resolves outside `/tmp/` (#1362). The planning
    fragments match anywhere in the path, relative paths included, because a
    fragment names a directory rather than a root.
    """
    if under_absolute_prefix(path, _PLANNING_TMP_PREFIXES):
        return True
    return contains_fragment(path, _PLANNING_FRAGMENTS)


def _is_test_fixture(components: tuple[str, ...]) -> bool:
    return any(comp in _FIXTURE_DIRECTORIES for comp in components)


def _matches_protected_basename(basename: str) -> bool:
    if basename in _PROTECTED_BASENAMES:
        return True
    if basename.startswith(_ENV_PREFIX) and len(basename) > len(_ENV_PREFIX):
        return basename not in _ENV_ALLOWLIST_BASENAMES
    return False


def _matches_protected_extension(basename: str) -> bool:
    if "." not in basename:
        return False
    ext = basename.rsplit(".", 1)[-1].lower()
    return ext in _PROTECTED_EXTENSIONS


def _classify(path: str) -> str | None:
    """Return a one-line reason string if `path` matches a protected pattern,
    else None. The classification result determines the advisory text.
    """
    components = _path_components(path)
    if not components:
        return None
    basename = components[-1]

    # Directory-component rules fire first — the directory itself is the trust
    # anchor.  `.ssh/id_rsa.pub` matches even though `id_rsa.pub` is otherwise
    # allow-listed elsewhere.  `.aws/`, `.kube/`, `.gnupg/` follow the same
    # pattern: any file inside the directory inherits the sensitivity of the
    # credential store.
    for _dir in _PROTECTED_DIR_COMPONENTS:
        if _dir in components:
            return f"path contains a protected directory component (`{_dir}/`)"

    if basename in _PUBKEY_BASENAMES:
        return None

    if _matches_protected_basename(basename):
        return f"basename `{basename}` is a sensitive credential file"

    if _matches_protected_extension(basename):
        ext = basename.rsplit(".", 1)[-1].lower()
        return f"extension `.{ext}` is a sensitive key / certificate format"

    return None


_ADVISORY_HEADER = "[protected-paths-guard] sensitive file write detected"


def _advisory_text(path: str, reason: str, strict: bool) -> str:
    mode = "BLOCKED (strict mode)" if strict else "ADVISORY"
    return (
        f"{_ADVISORY_HEADER} — {mode}\n"
        "\n"
        f"  Path: {path}\n"
        f"  Reason: {reason}\n"
        "\n"
        "  Sensitive files leak credentials when committed. Verify the write\n"
        "  is intentional and that the path is git-ignored.\n"
        "\n"
        "  Bypass options:\n"
        "    • Skip this single call: PRAXIS_HOOK_BYPASS_PROTECTED_PATHS=1\n"
        "    • Allow test fixtures: place under a /fixtures/, /__fixtures__/,\n"
        "      /test-data/, or /testdata/ directory component\n"
        "    • Allow .env templates: name the file .env.example / .env.sample\n"
        "      / .env.template / .env.defaults\n"
        "\n"
        "  Reference: issue #464"
    )


@fail_open
def main() -> int:
    if os.environ.get("PRAXIS_HOOK_BYPASS_PROTECTED_PATHS", "").strip():
        return 0

    payload = read_payload()
    if payload is None:
        return 0

    tool_name = payload.get("tool_name", "") or ""
    if tool_name not in TARGET_TOOLS:
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = _file_path_from_payload(tool_name, tool_input)
    if not file_path:
        return 0

    if _is_self_edit(file_path):
        return 0

    if _is_planning_artifact(file_path):
        return 0

    components = _path_components(file_path)
    if _is_test_fixture(components):
        return 0

    reason = _classify(file_path)
    if reason is None:
        return 0

    strict = os.environ.get("PRAXIS_PROTECTED_PATHS_STRICT", "").strip() == "1"
    sys.stderr.write(_advisory_text(file_path, reason, strict) + "\n")
    return 2 if strict else 0




if __name__ == "__main__":
    sys.exit(main())
