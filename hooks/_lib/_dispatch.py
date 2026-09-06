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
  4. aggregate the per-hook results into ONE decision (most-restrictive wins)
     for the (event, lane) pairs listed below.

Member `impl.py` files are NOT modified — they keep reading stdin and returning
an int exactly as before. The dispatcher adapts around them.

Implemented (event, lane) semantics — the dispatcher is NOT generic across
every hook event; exactly these lanes are aggregated:

  PreToolUse permission lane (ADR-0002 §2.2):
    - any hook -> deny  (exit 2, or `permissionDecision: "deny"` on stdout) -> deny
    - else any hook -> ask (`permissionDecision: "ask"` on stdout)          -> ask
    (the substring marker probes run on PreToolUse groups only, so quoted
    marker text in another event's context cannot fake a decision; exit-2
    denies are event-agnostic)
  Stop decision lane (issue #1169):
    - any hook -> block (top-level `{"decision": "block", "reason": ...}` JSON
      at exit 0) -> ONE merged block: every blocking member's reason is kept,
      blank-line joined, each attributed to its hook name
  Stop advisory lane (issue #1281):
    - any hook -> top-level `{"systemMessage": ...}` at exit 0 -> the messages
      merge into ONE `systemMessage`, blank-line joined and attributed like
      the block reasons; it rides on the block object when a sibling blocks
      (`systemMessage` is a field common to every hook event, so the host
      reads both from the one JSON object it accepts)
  additionalContext lane (issue #874, any event, event-name-checked):
    - non-decision `hookSpecificOutput.additionalContext` objects merge into
      ONE `hookSpecificOutput` object
  - else -> allow (silent pass; no JSON written)

  stderr from every hook (advisory nudges AND deny/block reasons) is always
  preserved and forwarded.

Shell members (issue #1281). A member whose manifest `body` is `impl.sh` has
no Python `main()` to import, so `run_one` runs it as a subprocess: the payload
on stdin, the member deadline as the subprocess timeout, and the same
`(exit, stdout, stderr)` triple back into the lanes above. The child's own
fire-ledger arming (`_lib/record_fire.sh`) is switched off through
`PRAXIS_FIRE_TELEMETRY_DISABLE` so the group records it exactly once, the way
it records every Python member. A timed-out or unstartable shell member fails
open with a stderr note, mirroring `fail_open` for a raising Python member.

Fail-open invariant (ETHOS.md): every `main()` runs through `_hook_runtime.fail_open`,
and the dispatcher's own `main()` swallows exceptions to 0 — a crash here must
never block a legitimate tool call.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Callable, Iterator, Optional, cast

_HOOKS_DIR = Path(__file__).resolve().parent.parent  # hooks/
_LIB_DIR = _HOOKS_DIR / "_lib"
_MANIFEST = _HOOKS_DIR / "manifest.json"

# Member impl.py files do `sys.path.insert(0, .../_lib)` to import _hook_utils etc.
# Make _lib importable here too so an eager import resolves the same way.
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    fail_open,
    set_member_deadline,
)

_ASK_MARKER = '"permissionDecision": "ask"'
_DENY_MARKER = '"permissionDecision": "deny"'

# Events whose members carry a decision as a top-level `{"decision": "block"}`
# / `{"systemMessage": ...}` JSON at exit 0 rather than in a
# `hookSpecificOutput` (`_hook_io.emit_stop_block` / `emit_stop_advisory`).
# SubagentStop "use[s] the same decision control format as Stop hooks"
# (hooks reference, read 2026-09-06), so the Stop aggregation lane below
# covers it verbatim — issue #1337. Kept in sync with
# `_fire_ledger.STOP_LANE_EVENTS`, which gates the matching ledger branch.
STOP_LANE_EVENTS = ("Stop", "SubagentStop")

# Skip-record marker — kept in sync with _fire_ledger._SKIP_MARKER so a
# budget-skipped member is classified as decision "skip" (not "advise").
_SKIP_MARKER = "[dispatch] budget-skip"

# argv protocol sentinel for "this group has no matcher" (issue #1199 review).
# Non-tool events (Stop, SessionStart, UserPromptSubmit) carry no matcher key in
# the manifest, so their group identity is (event, None). argv can only carry
# strings — the build renders this sentinel into the dispatcher node's command
# (`build-plugin-manifests.py DISPATCH_NO_MATCHER_ARG`, kept in sync by
# check-plugin-manifests.py Rule 14) and main() maps it back to None. Without
# the mapping an f-string render would pass the literal "None", which matches
# no manifest entry and silently resolves an empty group.
NO_MATCHER_ARG = "-"

# Group budget bookkeeping (issue #1167). The host kills the dispatcher node
# at the group budget (= max member timeout, see load_group), so the
# in-process deadline keeps a margin under it: enough to record skips, run
# fire telemetry, and emit the aggregate decision before the host's own cut.
_GROUP_BUDGET_MARGIN_SEC = 1.0
# A member is skipped (fail-open, recorded) when less than this remains —
# below the floor even a healthy member could push past the host's kill line.
_MEMBER_SKIP_FLOOR_SEC = MIN_SUBPROC_BUDGET_SEC
# Fallback when the manifest yields no member timeouts (unreadable manifest,
# empty group under a patched roster): today's Bash-group node budget.
_DEFAULT_GROUP_BUDGET_SEC = 15.0

# Every member that spawns a subprocess sizes its timeouts from
# `_hook_runtime.remaining_budget` (usually via `shared_probe_deadline`), so a
# cap SHORTER than the manifest timeout is safe for all of them: they degrade
# inside it rather than overshooting the node budget. Members that spawn
# nothing cost an import plus a text scan, which the same cap already bounds.
# That is what lets the loop below run every member while the floor holds,
# instead of skipping the ones whose manifest timeout no longer fits.
#
# The invariant is enforced, not maintained by hand:
# `tests/hooks/_lib/test_dispatch.py::test_every_subprocess_member_is_budget_aware`
# fails if a group member gains a subprocess call without a budget reference.


def _iter_group_entries(
    event: str, matcher: Optional[str], host: Optional[str]
) -> Iterator[tuple[dict, dict]]:
    """Yield `(hook, entry)` manifest pairs matching (event, matcher, host).

    `host` mirrors the per-platform filter `build-plugin-manifests.py` applies: a
    hook is kept iff its `hosts` whitelist is absent OR contains `host`. `host=None`
    is the canonical (unfiltered) view; the runtime passes its platform host so the
    dispatcher never re-includes a hook the build stripped for that platform (e.g.
    a Codex install must not run a `hosts: ["claude"]` guard). See main()/argv[3].

    `entry` mirrors `hook` — multi-event hooks are flat sibling entries in the
    manifest (one object per event); an earlier hypothetical nested "entries"
    form had zero manifest uses and was removed (issue #1169). The pair shape
    is kept so `load_group` does not need a separate single-entry code path.
    """
    manifest = json.loads(_MANIFEST.read_text())
    for hook in manifest.get("hooks", []):
        hosts = hook.get("hosts")
        if host is not None and hosts is not None and host not in hosts:
            continue
        if hook.get("event") != event or hook.get("matcher") != matcher:
            continue
        # Members are invoked with stdin only (see run_one). A manifest hook
        # that declares "args" (the per-process runtime forwards those as
        # sys.argv) is NOT supported in a dispatch group — the dispatcher's own
        # main() consumes sys.argv for (event, matcher), so the member would
        # silently run with its args dropped. It is excluded here, and the
        # build keeps it as its own standalone node beside the dispatcher
        # node (`build-plugin-manifests.filter_hooks_for_host`, issue #1199),
        # so the hook still runs — check Rule 14 fails the build if that node
        # is missing. The exclusion used to be reported on stderr on every
        # dispatch (issue #1169); with the standalone node guaranteed it is
        # the designed arrangement, not a fault, and under the Stop group the
        # line would have reached the user on every turn (issue #1281).
        # (`body: impl.sh` members are fine: run_one runs them as a
        # subprocess, stdin-only like every other member — issue #1281.)
        if hook.get("args"):
            continue
        yield hook, hook


def load_group(
    event: str, matcher: Optional[str], host: Optional[str] = None
) -> tuple[list[tuple[str, str, Path]], float, dict[tuple[str, str], float]]:
    """Read the manifest ONCE; return `(members, budget_sec, member_timeouts)`.

    `members` is `(role, name, impl_path)` in `manifest.json` array order (the
    declared run order). `budget_sec` is the MAX member timeout across the
    group — the same derivation `build-plugin-manifests.py` uses for the
    dispatcher node's host-side timeout (`filter_hooks_for_host`'s
    `dispatch_timeout` pre-pass), so the in-process deadline tracks the budget
    the host actually enforces; the manifest is the single source of truth for
    both. `member_timeouts` maps `(role, name)` to that member's own timeout.

    A single read (not one per derived view) also closes the window where the
    manifest changes on disk between reading the roster and reading the
    budget, which would pair members from one version with timeouts from
    another. A group with no usable timeouts falls back to
    `_DEFAULT_GROUP_BUDGET_SEC` (members then get `min(remaining, budget)` as
    their cap).
    """
    members: list[tuple[str, str, Path]] = []
    timeouts: dict[tuple[str, str], float] = {}
    for hook, entry in _iter_group_entries(event, matcher, host):
        role = hook.get("role")
        name = hook.get("name")
        # A member's impl path is `<role>/<name>/...`, so both must be strings.
        # The manifest checker rejects an entry without them, but this is the
        # runtime side: one malformed entry must not take the whole group down
        # (a raise here fails main() open with nothing on stderr, hiding every
        # other member). Exclude it loudly instead, mirroring the args case.
        if not isinstance(role, str) or not isinstance(name, str):
            sys.stderr.write(
                f"[dispatch] manifest entry {hook.get('role')!r}/"
                f"{hook.get('name')!r} lacks a string role/name — member "
                "excluded from the group (fail-open)\n"
            )
            continue
        # `or` (not `.get(key, default)`): an explicit "body": null in a future
        # manifest entry has the key present, so `.get` would return None and
        # `Path / None` would raise. `or` treats both absent and null as default.
        body = hook.get("body") or "impl.py"
        impl = _HOOKS_DIR / role / name / (entry.get("file") or body)
        members.append((role, name, impl))
        raw = hook.get("timeout")
        if isinstance(raw, (int, float)) and raw > 0:
            timeouts[(role, name)] = float(raw)
    budget = max(timeouts.values(), default=_DEFAULT_GROUP_BUDGET_SEC)
    return members, budget, timeouts


def group_members(
    event: str, matcher: Optional[str], host: Optional[str] = None
) -> list[tuple[str, str, Path]]:
    """Return `(role, name, impl_path)` for manifest entries matching (event, matcher).

    Thin view over `load_group` — see its docstring for ordering and host
    filtering semantics.
    """
    return load_group(event, matcher, host)[0]


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


def is_shell_member(impl: Path) -> bool:
    """True for a `body: impl.sh` member — run as a subprocess, never imported."""
    return impl.suffix == ".sh"


def run_shell_member(
    role: str,
    name: str,
    impl: Path,
    payload_raw: str,
    deadline: Optional[float] = None,
) -> tuple[int, str, str]:
    """Run a shell member as a subprocess. Returns `(exit, stdout, stderr)`.

    The impl is exec'd directly (not via `sh`) so its own `#!/bin/bash`
    shebang is honored, exactly as the generated per-hook wrapper did. The
    timeout is the member deadline the dispatcher publishes (issue #1167), so
    a shell member is deadline-clamped by construction — it needs no budget
    API of its own — and a direct/test call without a deadline is capped at
    the default group budget rather than left unbounded.

    Fail-open, both halves: a missing or non-executable impl passes silently
    (the launcher's `[ -f "$IMPL" ] || exit 0` contract, issue #1053), and a
    timeout or spawn failure passes with a stderr note, mirroring what
    `fail_open` does for a raising Python member — never a block nobody
    decided, never a silent disappearance.

    `PRAXIS_FIRE_TELEMETRY_DISABLE=1` in the child's environment switches off
    the impl's own `record_fire.sh` arming: the group records this member's
    fire from the returned triple (`_record_fires`), the same rich record the
    shell writer would have produced, and a second record per fire would
    double-count it in the audit that reads the ledger (issue #848).
    """
    if not impl.is_file():
        return 0, "", ""
    timeout = (
        _DEFAULT_GROUP_BUDGET_SEC
        if deadline is None
        else max(0.0, deadline - time.monotonic())
    )
    env = dict(os.environ)
    env["PRAXIS_FIRE_TELEMETRY_DISABLE"] = "1"
    try:
        proc = subprocess.run(
            [str(impl)],
            input=payload_raw,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return (
            0,
            "",
            f"[dispatch] {role}/{name}: shell member exceeded its "
            f"{timeout:.1f}s deadline; killed (fail-open)\n",
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return 0, "", f"[dispatch] {role}/{name}: shell member failed to start: {exc} (fail-open)\n"
    return proc.returncode, proc.stdout, proc.stderr


def run_one(
    role: str,
    name: str,
    impl: Path,
    payload_raw: str,
    deadline: Optional[float] = None,
) -> tuple[int, str, str]:
    """Run a single member's `main()` in-process. Returns `(exit, stdout, stderr)`.

    A `body: impl.sh` member has no `main()` to import and is delegated to
    `run_shell_member` (a subprocess under the same deadline) — the triple it
    returns feeds the same lanes.

    `sys.stdin` is re-pointed at a fresh copy of the payload so the unmodified
    impl.py reads it as if it were the only process. `main()` is wrapped in
    `fail_open`, so a raising hook is isolated and reported as a pass (exit 0)
    — double-wrapping an already-`@fail_open` main is harmless.

    `deadline` (issue #1167) is the member's absolute wall-clock cap (a
    `time.monotonic()` value, already clamped to the group deadline by
    `run_group`). It is published via `_hook_runtime.set_member_deadline`
    BEFORE the cold `_load_main` import, so import time erodes the member's
    own budget rather than the group's post-loop margin (round-2 review).
    Budget-aware members size their subprocess timeouts from
    `remaining_budget`; the cap is cooperative, not preemptive — a member
    that ignores it is still bounded by the host's node timeout. None
    (direct/test calls) publishes no deadline.
    """
    if is_shell_member(impl):
        return run_shell_member(role, name, impl, payload_raw, deadline)
    if deadline is not None:
        set_member_deadline(deadline)
    try:
        try:
            fn = _load_main(role, name, impl)
        except Exception:
            # Import-time failure (missing dep, syntax error): fail OPEN — a
            # broken member must never block the tool — but NOT fail-silent.
            # Forward the traceback to stderr exactly as the per-process
            # `python3 impl.py` wrapper would (Python prints it
            # unconditionally), so a disabled guard stays visible instead of
            # silently allowing.
            return (
                0,
                "",
                f"[dispatch] {role}/{name} import failed:\n{traceback.format_exc()}",
            )
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
    finally:
        if deadline is not None:
            set_member_deadline(None)


def _skip_result(
    role: str, name: str, remaining: float, budget: float, event: str
) -> tuple[int, str, str]:
    """Fail-open skip record for a member the group budget cannot cover.

    The starvation must be VISIBLE (the issue's core complaint is members
    silently never running when an earlier member overruns), on two channels:

    - stdout carries the notice as an `additionalContext` hookSpecificOutput,
      which `run_group`'s issue-#874 merge forwards — the ONE exit-0
      PreToolUse channel that actually reaches the model (stderr does not; see
      `_hook_io.py`). Only surfaced when no deny/ask wins stdout, which is
      fine: a deny/ask already carries its own reason.
    - stderr carries the same note for terminal/log visibility AND as the
      marker `_fire_ledger.classify_decision` reads to record the skip as
      decision "skip" in the fire ledger.
    """
    note = (
        f"{_SKIP_MARKER} {role}/{name}: {max(0.0, remaining):.1f}s left of the "
        f"{budget:.0f}s group budget; member not run (fail-open)"
    )
    stdout = json.dumps({
        "hookSpecificOutput": {"hookEventName": event, "additionalContext": note}
    })
    return 0, stdout, note + "\n"


def _record_fires(members, results, payload_raw: str, event: str) -> None:
    """Log each member's fire decision (issue #710). Fail-open, never raises.

    Called once per member from `run_group`'s loop (incremental — a host kill
    mid-group must not erase the records of members already resolved), with
    single-element lists. Lazily imports `_fire_ledger` so a missing/broken
    module can never break the dispatcher — fire telemetry is observe-only.

    `event` is forwarded so the ledger can gate its Stop-lane classification
    the same way `run_group` gates the lane itself; without it the ledger
    recorded blocks under events where the dispatcher never aggregates.
    """
    try:
        import _fire_ledger  # type: ignore[import-not-found]
        _fire_ledger.record_group_fires(members, results, payload_raw, event)
    except Exception:
        pass


def run_group(
    event: str, matcher: Optional[str], payload_raw: str, host: Optional[str] = None
) -> int:
    """Run the whole (event, matcher) group; emit one decision; return its exit code.

    The transcript turn memo (issue #1281) is enabled for exactly this call:
    members that read the current turn share one parse, and the memo is
    dropped on the way out so nothing outlives the group run. Observe-only and
    fail-open, like the fire telemetry — a missing `_transcript` cannot break
    the dispatcher.
    """
    try:
        import _transcript  # type: ignore[import-not-found]
    except Exception:
        _transcript = None  # type: ignore[assignment]
    if _transcript is not None:
        try:
            _transcript.enable_turn_memo()
        except Exception:
            pass
    try:
        return _run_group(event, matcher, payload_raw, host)
    finally:
        if _transcript is not None:
            try:
                _transcript.disable_turn_memo()
            except Exception:
                pass


def _run_group(
    event: str, matcher: Optional[str], payload_raw: str, host: Optional[str] = None
) -> int:
    # Mark this process as the dispatcher so the fail_open-level coarse recorder
    # (issue #710 coverage expansion) skips the Bash-group members run below —
    # they are recorded richly by _record_fires. Avoids double-counting.
    try:
        import _fire_ledger  # type: ignore[import-not-found]
        _fire_ledger.mark_dispatcher_process()
    except Exception:
        pass

    # A deny is flushed and returned the moment a member computes it, never
    # buffered to the end of the group. What this buys is a shorter exposure
    # window, NOT survival of a host kill: Claude Code "cancels a `command`,
    # `http`, or `mcp_tool` hook that reaches its `timeout`, discarding the
    # hook's output" (https://code.claude.com/docs/en/hooks.md), so output
    # already flushed is discarded too and a deny cut at the node timeout is
    # lost either way. Returning at the first deny ends the group there
    # instead of running the remaining members, so the dispatcher finishes far
    # short of the timeout and the kill has no window to happen in. It cannot
    # change which deny wins (the first one already won) and costs only the
    # advisory stderr of members after it, on a call that is blocked anyway.
    # Per-member deadline (issue #1167): members run sequentially inside ONE
    # host timeout (the max member timeout — see load_group), so without a
    # group deadline one slow member starves every later member and the host
    # kills the dispatcher silently. Track the deadline with a margin under
    # the host's cut. A budget-aware member gets min(remaining, its own
    # manifest timeout) as a cooperative cap; any other member runs only
    # while the FULL manifest timeout still fits (its fixed subprocess
    # timeouts would ignore a shorter cap and overshoot the node budget).
    # Members the budget cannot cover are skipped-with-record, and each
    # member's fire is recorded INCREMENTALLY, right after it resolves —
    # a batch write after the loop would be erased along with the aggregate
    # decision if the host killed the dispatcher mid-group (round-2 review).
    members, budget, member_timeouts = load_group(event, matcher, host)
    # The SUBSTRING marker probe below (member_rc == 2 or _DENY_MARKER in
    # member_so) is scoped to PreToolUse groups (issue #1199 review): on any
    # other event a member's additionalContext that merely QUOTES a marker
    # string would be surfaced as a fake deny and would shadow a real
    # Stop-lane block further down — the exact swallow class that lane exists
    # to prevent. Exit-2 stays event-agnostic: an exit code cannot be faked by
    # quoted text.
    is_pretooluse = event == "PreToolUse"
    is_stop = event in STOP_LANE_EVENTS
    deadline = time.monotonic() + max(
        budget - _GROUP_BUDGET_MARGIN_SEC, _MEMBER_SKIP_FLOOR_SEC
    )
    results = []
    # A PreToolUse deny ends the group at once (below): the tool call is
    # blocked, so the members after it have nothing left to gate. On any
    # other event the tool has already run, and standalone every member ran
    # regardless of its siblings' exit codes — so an exit-2 member must not
    # silence the ones after it (a blocking anchor-comment-gate would drop
    # bypass-telemetry's record). Remember the first blocking output and
    # keep going; the group still returns 2 once every member has spoken.
    blocking_stdout: Optional[str] = None
    for role, name, impl in members:
        member = (role, name)
        remaining = deadline - time.monotonic()
        member_timeout = member_timeouts.get(member)
        if member_timeout is None:
            # No declared timeout (patched roster, malformed manifest entry):
            # nothing to compare the remaining budget against, so run while
            # the floor holds, capped at the group deadline.
            runnable = remaining >= _MEMBER_SKIP_FLOOR_SEC
            member_deadline = deadline
        else:
            # The floor is the ONLY skip condition. Gating on
            # `remaining >= member_timeout` compared the budget against a
            # worst case almost no member reaches — a pure-text gate declaring
            # 5s returns in ~50ms — so a single slow `gh` call early in the
            # group silently skipped every deny-capable gate after it — 7 of
            # them, including block-gh-issue-create-without-dup-search. A gate
            # that does not
            # run cannot block, and the skip is invisible at the call site.
            runnable = remaining >= _MEMBER_SKIP_FLOOR_SEC
            member_deadline = min(time.monotonic() + member_timeout, deadline)
        if runnable:
            result = run_one(role, name, impl, payload_raw, deadline=member_deadline)
        else:
            result = _skip_result(role, name, remaining, budget, event)
        results.append(result)
        # Fire telemetry (issue #710): observe-only and fail-open — the
        # dispatcher's decision is unaffected.
        _record_fires([(role, name, impl)], [result], payload_raw, event)
        # Forward this member's stderr (advisory nudges and deny reasons alike)
        # as it resolves rather than after the loop, for the same reason the
        # deny return above is immediate: it shortens the window, and a kill at
        # the node timeout discards the whole process's output regardless.
        member_rc, member_so, member_se = result
        if member_se:
            sys.stderr.write(member_se)
        if member_rc == 2 or (is_pretooluse and _DENY_MARKER in member_so):
            if is_pretooluse:
                if member_so:
                    sys.stdout.write(member_so)
                return 2
            if blocking_stdout is None:
                blocking_stdout = member_so
    if blocking_stdout is not None:
        if blocking_stdout:
            sys.stdout.write(blocking_stdout)
        return 2

    # Most-restrictive decision wins: deny > ask > allow. Deny already returned
    # from the loop above, so only ask is left to outrank a plain allow. The
    # FIRST ask JSON on stdout is surfaced — concatenating two decision objects
    # would be invalid JSON, and Claude Code surfaces one decision anyway.
    # Detection is role-agnostic within PreToolUse, since some advisory-nudge
    # hooks also emit ask — see ADR-0002 §2.2.
    if is_pretooluse:
        ask = next((so for _rc, so, _se in results if _ASK_MARKER in so), None)
        if ask is not None:
            sys.stdout.write(ask)
            return 0

    # Stop decision lane (issue #1169): a Stop hook blocks via a top-level
    # `{"decision": "block", "reason": ...}` JSON at exit 0 — no exit-2, no
    # permissionDecision marker — so without this branch a member's block would
    # fall through to the context-merge below, `_merge_additional_context`
    # would find no `hookSpecificOutput`, and the block would be silently
    # swallowed (all 13 completion gates disabled errorlessly the day Stop is
    # grouped). Recognition PARSES each member's stdout (`_stop_block_reason`)
    # rather than substring-probing, so prose mentioning "decision" can never
    # fake a block. The host reads ONE JSON object from stdout and feeds its
    # `reason` back to the model, so every blocking member's reason is merged
    # into a single block — blank-line joined, each attributed to its hook the
    # way deny reasons carry their `[praxis:<hook>]` prefix (no attribution is
    # added when the reason already carries it). Exit stays 0: the Stop lane
    # carries blocking in the JSON `decision` field, not the exit code (see
    # _hook_io.emit_stop_block). A blocking group drops non-blocking members'
    # additionalContext for this firing — only one JSON object can be emitted,
    # and the members re-run on the next stop attempt anyway.
    # Scoped to Stop for the same reason the marker lanes are scoped to
    # PreToolUse (issue #1199 review): `{"decision": "block"}` means Stop's
    # block, and re-emitting it as this group's answer on another event
    # answers a question that event never asked.
    #
    # The Stop advisory lane (issue #1281) rides alongside: a member's
    # top-level `systemMessage` (`_hook_io.emit_stop_advisory`) is a field
    # common to every hook event, so it merges into the SAME object as the
    # block when a sibling blocks, and stands alone otherwise. Without this
    # lane a grouped advisory fell through to the context merge below, which
    # forwards only `hookSpecificOutput`, and was dropped — the reason Stop
    # stayed outside the dispatch groups.
    blocks: list[tuple[str, str]] = []
    messages: list[tuple[str, str]] = []
    if is_stop:
        for (_role, name, _impl), (rc, so, _se) in zip(members, results):
            if rc != 0 or not so:
                continue
            reason, message = _stop_output(so)
            if reason is not None:
                blocks.append((name, reason))
            if message is not None:
                messages.append((name, message))
    if blocks or messages:
        stop_out: dict = {}
        if blocks:
            stop_out["decision"] = "block"
            stop_out["reason"] = "\n\n".join(_attributed(blocks))
        if messages:
            stop_out["systemMessage"] = "\n\n".join(_attributed(messages))
        sys.stdout.write(json.dumps(stop_out) + "\n")
        return 0

    # Issue #874: a member may emit a NON-decision `hookSpecificOutput` at exit 0 —
    # today only `additionalContext`, which is the one PreToolUse channel that
    # reaches the model (stderr does not; see _hook_io.py). Without forwarding it
    # here the dispatcher drops that stdout on the floor and the channel is inert
    # for every grouped hook. Only reached once deny and ask have both missed, so
    # this can never put a second decision object on stdout.
    contexts = [
        so
        for rc, so, _se in results
        if so
        and rc != 2
        # Marker exclusion mirrors the substring lanes above: PreToolUse-only,
        # so a non-PreToolUse context that quotes a marker still merges instead
        # of being dropped as a mistaken decision payload.
        and (not is_pretooluse or (_DENY_MARKER not in so and _ASK_MARKER not in so))
    ]
    if contexts:
        merged = _merge_additional_context(contexts, event)
        if merged:
            sys.stdout.write(merged)
    return 0


def _attributed(items: list[tuple[str, str]]) -> list[str]:
    """Prefix each `(hook, text)` with `[praxis:<hook>]` unless it already carries it.

    Shared by the Stop block and advisory lanes so a merged reason and a merged
    systemMessage attribute their parts the same way deny reasons do. A
    malformed (empty) text still yields the bare tag, with no trailing space:
    the entry must not vanish just because its body is missing.
    """
    out: list[str] = []
    for name, text in items:
        tag = f"[praxis:{name}]"
        if not text:
            out.append(tag)
        elif tag in text:
            out.append(text)
        else:
            out.append(f"{tag} {text}")
    return out


def _stop_output(stdout: str) -> tuple[Optional[str], Optional[str]]:
    """Parse a Stop member's stdout into `(block_reason, system_message)`.

    A blocking Stop member writes exactly `{"decision": "block", "reason": ...}`
    (see `_hook_io.format_stop_block` and the shell siblings' `jq -n` form); an
    advisory one writes `{"systemMessage": ...}` (`format_stop_advisory`). Both
    shapes are recognized by PARSING the JSON — never by substring matching —
    so a member whose output merely *mentions* `decision` (prose, a context
    string) cannot fake a block. Both the python `json.dump` single-line form
    and jq's pretty-printed multi-line form parse identically here.

    A parsed block with a missing/non-string `reason` still blocks (empty
    reason): dropping it because its reason field is malformed would be the
    exact silent-swallow this lane exists to prevent. A `systemMessage` is
    forwarded only when it is a non-empty string — there is nothing to show
    otherwise. Fail-open (`(None, None)`) only for outputs that are neither
    (unparseable, or a different shape).
    """
    # No substring pre-filter: `{"\\u0064ecision": "block"}` is valid JSON for
    # the same object, and a literal probe drops it before the parse that is
    # supposed to be the authority here (issue #1199 review).
    if not stdout:
        return None, None
    try:
        obj = json.loads(stdout)
    except ValueError:
        return None, None
    if not isinstance(obj, dict):
        return None, None
    reason: Optional[str] = None
    if obj.get("decision") == "block":
        raw_reason = obj.get("reason")
        reason = raw_reason if isinstance(raw_reason, str) else ""
    raw_message = obj.get("systemMessage")
    message = raw_message if isinstance(raw_message, str) and raw_message else None
    return reason, message


def _stop_block_reason(stdout: str) -> Optional[str]:
    """Return the reason if `stdout` is a Stop-lane block object, else None."""
    return _stop_output(stdout)[0]


def _merge_additional_context(payloads: list[str], event: str) -> str:
    """Fold several members' `additionalContext` into ONE hookSpecificOutput.

    Claude Code reads a single JSON object from a hook's stdout, so concatenating
    N objects is invalid JSON and would lose all of them. Their context strings are
    joined instead. Fail-open: an unparseable or differently-shaped payload is
    dropped rather than corrupting the object the other members produced — the
    advisory's stderr line is emitted separately and is unaffected.

    A member's `hookEventName` is checked against the group's own event instead of
    being adopted from whichever member came first. Adopting it would let one
    member's wrong event name label the merged object, and Claude Code discards an
    object whose event does not match the hook it invoked — losing every member's
    context, which is the same silent drop this forwarding exists to fix.
    """
    chunks: list[str] = []
    for raw in payloads:
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        hso = obj.get("hookSpecificOutput") if isinstance(obj, dict) else None
        if not isinstance(hso, dict):
            continue
        if str(hso.get("hookEventName") or "") != event:
            continue
        text = hso.get("additionalContext")
        if isinstance(text, str) and text:
            chunks.append(text)
    if not chunks:
        return ""
    out: dict = {
        "hookEventName": event,
        "additionalContext": "\n\n".join(chunks),
    }
    return json.dumps({"hookSpecificOutput": out})


def main() -> int:
    """Entrypoint: stdin = hook payload; argv = [event, matcher, host?].

    Defaults to PreToolUse / Bash / no host filter. The build passes the platform
    host as argv[3] so host-restricted hooks are not re-included (see group_members).
    A matcher argv equal to `NO_MATCHER_ARG` maps to None — the group identity of
    matcher-less events (Stop etc.), whose manifest entries carry no matcher key.
    Wrapped in a top-level guard so a dispatcher fault fails open (return 0),
    never blocking the tool call.
    """
    try:
        payload_raw = sys.stdin.read()
        event = sys.argv[1] if len(sys.argv) > 1 else "PreToolUse"
        matcher_arg = sys.argv[2] if len(sys.argv) > 2 else "Bash"
        matcher = None if matcher_arg == NO_MATCHER_ARG else matcher_arg
        host = sys.argv[3] if len(sys.argv) > 3 else None
        return run_group(event, matcher, payload_raw, host)
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
