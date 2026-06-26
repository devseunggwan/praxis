"""Single-process dispatch runner for same-(event, matcher) hook groups (ADR-0002).

Today each PreToolUse(Bash) hook is a separate `.sh` wrapper that
`exec python3 .../impl.py`, so one `Bash` tool call cold-starts ~35 python3
processes. ~99% of the latency is interpreter startup, not hook logic.

This module runs a whole `(event, matcher)` group inside one process:

  1. read the hook payload from stdin ONCE;
  2. import each member's `impl.py` (the `if __name__ == "__main__"` guard
     means importing does NOT run `main()`);
  3. for each member, re-point `sys.stdin` at a fresh `StringIO` of the payload
     and call its `fail_open`-wrapped `main()`, capturing stdout/stderr/exit;
  4. aggregate the per-hook results into ONE decision (most-restrictive wins),
     reproducing the semantics Claude Code applies across independent hook
     processes.

Member `impl.py` files are NOT modified — they keep reading stdin and returning
an int exactly as before. The dispatcher adapts around them.

Decision aggregation (ADR-0002 §2.2):
  - any hook -> deny  (exit 2, or `permissionDecision: "deny"` on stdout) -> deny
  - else any hook -> ask (`permissionDecision: "ask"` on stdout)          -> ask
  - else                                                                  -> allow
  stderr from every hook (advisory nudges AND deny reasons) is always
  preserved and forwarded.

Fail-open invariant (ETHOS.md): every `main()` runs through `_hook_runtime.fail_open`,
and the dispatcher's own `main()` swallows exceptions to 0 — a crash here must
never block a legitimate tool call.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Callable, Optional, cast

_HOOKS_DIR = Path(__file__).resolve().parent.parent  # hooks/
_LIB_DIR = _HOOKS_DIR / "_lib"
_MANIFEST = _HOOKS_DIR / "manifest.json"

# Member impl.py files do `sys.path.insert(0, .../_lib)` to import _hook_utils etc.
# Make _lib importable here too so an eager import resolves the same way.
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402

_ASK_MARKER = '"permissionDecision": "ask"'
_DENY_MARKER = '"permissionDecision": "deny"'


def group_members(
    event: str, matcher: str, host: Optional[str] = None
) -> list[tuple[str, str, Path]]:
    """Return `(role, name, impl_path)` for manifest entries matching (event, matcher).

    Order follows `manifest.json` array order, which is the declared run order.
    A multi-event hook contributes the entry whose event/matcher matches.

    `host` mirrors the per-platform filter `build-plugin-manifests.py` applies: a
    hook is kept iff its `hosts` whitelist is absent OR contains `host`. `host=None`
    is the canonical (unfiltered) view; the runtime passes its platform host so the
    dispatcher never re-includes a hook the build stripped for that platform (e.g.
    a Codex install must not run a `hosts: ["claude"]` guard). See main()/argv[3].
    """
    manifest = json.loads(_MANIFEST.read_text())
    members: list[tuple[str, str, Path]] = []
    for hook in manifest.get("hooks", []):
        hosts = hook.get("hosts")
        if host is not None and hosts is not None and host not in hosts:
            continue
        name = hook.get("name")
        role = hook.get("role")
        # `or` (not `.get(key, default)`): an explicit "body": null in a future
        # manifest entry has the key present, so `.get` would return None and
        # `Path / None` would raise. `or` treats both absent and null as default.
        body = hook.get("body") or "impl.py"
        entries = hook.get("entries") or [
            {"event": hook.get("event"), "matcher": hook.get("matcher"), "file": body}
        ]
        for entry in entries:
            if entry.get("event") == event and entry.get("matcher") == matcher:
                impl = _HOOKS_DIR / role / name / (entry.get("file") or body)
                members.append((role, name, impl))
    # Members are invoked with stdin only (see run_one). A manifest hook that
    # declares "args" (the per-process runtime forwards those as sys.argv) is NOT
    # supported in a dispatch group — and the dispatcher's own main() consumes
    # sys.argv for (event, matcher). None of the current exact-Bash members
    # declare args; PR3 adds a fail-loud guard when it wires the manifest schema.
    return members


def _load_main(role: str, name: str, impl: Path) -> Optional[Callable[[], int]]:
    """Import impl.py under a unique module name and return its `main` callable.

    Returns None if the module has no callable `main` (the dispatcher then
    treats it as a transparent pass). Import errors propagate to the caller,
    which isolates them per hook.
    """
    modname = f"_praxis_dispatch__{role}__{name}".replace("-", "_")
    spec = importlib.util.spec_from_file_location(modname, impl)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, "main", None)
    return cast("Callable[[], int]", fn) if callable(fn) else None


def run_one(role: str, name: str, impl: Path, payload_raw: str) -> tuple[int, str, str]:
    """Run a single member's `main()` in-process. Returns `(exit, stdout, stderr)`.

    `sys.stdin` is re-pointed at a fresh copy of the payload so the unmodified
    impl.py reads it as if it were the only process. `main()` is wrapped in
    `fail_open`, so a raising hook is isolated and reported as a pass (exit 0)
    — double-wrapping an already-`@fail_open` main is harmless.
    """
    try:
        fn = _load_main(role, name, impl)
    except Exception:
        # Import-time failure (missing dep, syntax error): fail OPEN — a broken
        # member must never block the tool — but NOT fail-silent. Forward the
        # traceback to stderr exactly as the per-process `python3 impl.py` wrapper
        # would (Python prints it unconditionally), so a disabled guard stays
        # visible instead of silently allowing.
        return 0, "", f"[dispatch] {role}/{name} import failed:\n{traceback.format_exc()}"
    if fn is None:
        return 0, "", ""

    wrapped = fail_open(fn)
    out, err = io.StringIO(), io.StringIO()
    saved_stdin = sys.stdin
    sys.stdin = io.StringIO(payload_raw)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = wrapped() or 0
    except SystemExit as exc:  # impl.py may `sys.exit(main())`-style internally
        rc = exc.code if isinstance(exc.code, int) else 0
    finally:
        sys.stdin = saved_stdin
    return rc, out.getvalue(), err.getvalue()


def _record_fires(members, results, payload_raw: str) -> None:
    """Log each member's fire decision (issue #710). Fail-open, never raises.

    Lazily imports `_fire_ledger` so a missing/broken module can never break the
    dispatcher — fire telemetry is observe-only.
    """
    try:
        import _fire_ledger  # type: ignore[import-not-found]
        _fire_ledger.record_group_fires(members, results, payload_raw)
    except Exception:
        pass


def run_group(
    event: str, matcher: str, payload_raw: str, host: Optional[str] = None
) -> int:
    """Run the whole (event, matcher) group; emit one decision; return its exit code."""
    # Mark this process as the dispatcher so the fail_open-level coarse recorder
    # (issue #710 coverage expansion) skips the Bash-group members run below —
    # they are recorded richly by _record_fires. Avoids double-counting.
    try:
        import _fire_ledger  # type: ignore[import-not-found]
        _fire_ledger.mark_dispatcher_process()
    except Exception:
        pass

    members = group_members(event, matcher, host)
    results = [run_one(role, name, impl, payload_raw) for role, name, impl in members]

    # Fire telemetry (issue #710): record each member's decision before the
    # aggregation below. Observe-only and fail-open — the dispatcher's decision
    # is unaffected.
    _record_fires(members, results, payload_raw)

    # Forward every hook's stderr (advisory nudges and deny reasons alike).
    for _rc, _so, se in results:
        if se:
            sys.stderr.write(se)

    # Most-restrictive decision wins: deny > ask > allow. The FIRST deny/ask JSON
    # on stdout is surfaced — concatenating two decision objects would be invalid
    # JSON, and Claude Code surfaces one decision anyway. Every member's stderr is
    # forwarded above regardless, so advisory nudges and later reasons are never
    # silently dropped. Detection is role-agnostic (exit 2 / marker), since some
    # advisory-nudge hooks also emit ask/deny — see ADR-0002 §2.2.
    deny = next(
        (so for rc, so, _se in results if rc == 2 or _DENY_MARKER in so), None
    )
    if deny is not None:
        if deny:
            sys.stdout.write(deny)
        return 2

    ask = next((so for _rc, so, _se in results if _ASK_MARKER in so), None)
    if ask is not None:
        sys.stdout.write(ask)
        return 0

    return 0


def main() -> int:
    """Entrypoint: stdin = hook payload; argv = [event, matcher, host?].

    Defaults to PreToolUse / Bash / no host filter. The build passes the platform
    host as argv[3] so host-restricted hooks are not re-included (see group_members).
    Wrapped in a top-level guard so a dispatcher fault fails open (return 0),
    never blocking the tool call.
    """
    try:
        payload_raw = sys.stdin.read()
        event = sys.argv[1] if len(sys.argv) > 1 else "PreToolUse"
        matcher = sys.argv[2] if len(sys.argv) > 2 else "Bash"
        host = sys.argv[3] if len(sys.argv) > 3 else None
        return run_group(event, matcher, payload_raw, host)
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
