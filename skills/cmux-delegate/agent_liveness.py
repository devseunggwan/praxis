"""Liveness classification for a delegated cmux worker (issue #981).

Step 7 of cmux-delegate judges completion from the report file, which is a
two-valued oracle: the file is there or it is not. "Not there" covers a worker
that crashed, one that is thirty seconds into a long tool call, and one sitting
at a prompt waiting for a keypress — three states that call for three different
responses. This module supplies the missing axis.

Four states, and the reason each needs its own name:

  working        mid-turn — a tool is running or the model is thinking. Both
                 look identical from outside and are treated the same by the
                 delegator (wait), so they are not split.
  idle           the turn ended and nothing new arrived. For a delegated worker
                 this is the suspicious one: it was given a task and stopped
                 without leaving a report.
  waiting-input  alive but blocked on a keypress (permission prompt, folder
                 trust dialog)
  crash          the selector resolved and cmux answered with no workspace

`unknown` is the fail-open value: cmux absent, RPC unreachable, malformed
output. praxis targets hosts without cmux (ARCHITECTURE.md, host-neutral), and
a liveness probe that cannot run must never be read as "the worker is dead".
A lookup that fails is `unknown`, never `crash`: only an answered query that
came back empty is evidence of death.

IDENTITY IS THE WORKSPACE, NOT THE DIRECTORY. The first design keyed on the
worktree root, on the premise that a delegated worker has a directory of its
own. It does not — SKILL.md's dispatch defaults `cwd` to the delegator's own
`$(pwd)`, so delegator and delegate normally share one path. Measured on this
host: 18 workspaces at a single repo root, where a path-keyed probe answered
`{ambiguous: 18, idle_seconds: 10264, state: "working"}` — two fields drawn
from two different workspaces and a state taken from the prober's own events.
So a `workspace:N` ref (translated to its id via `cmux workspace list --json`,
the only surface carrying `ref`) or a raw id is the selector; a path is the
weak fallback that answers about one of N and says so in `ambiguous`.

Evidence comes from two cmux surfaces:

  cmux rpc mobile.workspace.list   id, current_directory, last_activity_at,
                                   preview, terminals[].is_ready
  cmux workspace list --json       ref → id translation (this surface alone
                                   carries `ref`; the RPC alone carries the rest)
  cmux events --category agent     turn boundaries

TURN BOUNDARY, NOT TOOL BOUNDARY. The first design read a PreToolUse
`received` with no matching `completed` as "inside a tool call". That is wrong
twice over, and both were caught only by watching a real worker:

  1. `agent.hook.PostToolUse` is NOT among the relayed events. Measured over a
     1500-event sample the relayed set is PreToolUse, Stop, UserPromptSubmit,
     SessionStart, Notification, PermissionRequest, SubagentStop. With no
     PostToolUse there is no "tool finished" signal to pair against.
  2. `received`/`completed` are the start and end of the HOOK DISPATCH, not of
     the tool. They arrive milliseconds apart — observed as seq 75152/75154
     while the tool itself (`sleep 50`) had 40 seconds left to run.

What the stream does give is the turn: `Stop` fires when the agent finishes
its turn. So the newest event decides — `Stop` means idle, anything later than
the last `Stop` means still working. A worker mid-tool and a worker mid-thought
are indistinguishable here, and the name says so.

`preview` is the sidebar's notification text, and a blocked Claude Code puts
its prompt there verbatim ("Claude needs your permission"). That is the only
signal that separates waiting-input from crash without scraping the screen, so
the pattern list below is load-bearing; `read-screen` stays available as a
confirmation path but is not on the hot path.

WINDOW DIRECTION. `--after N --limit M` returns the M events that come *after*
N, oldest-first — so a cold cursor of 0 returns the oldest slice of the
retained log, which on a busy host is entirely historical. The first design
read that empty-of-recent slice as "nothing outstanding" and reported a working
agent as idle. The window is therefore anchored to the ack frame's `latest_seq`
and walked backwards, never forwards from 0.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

# Substrings that mark a worker blocked on a MODAL PROMPT — a dialog that will
# not clear until someone presses a key. Matched case-insensitively against the
# workspace `preview` text.
#
# "waiting for your input" is deliberately absent. cmux emits it when the agent
# simply finishes its turn, so matching it collapses two states the delegator
# treats differently: a modal prompt means go press a key, while a finished
# turn with no report means re-dispatch or take the work over. Turn ends are
# `idle`, and only the turn logic decides them.
# Kept to strings the HOST generates. `preview` carries the agent's own reply
# text verbatim — observed previews include whole paragraphs of Korean prose —
# so a marker that plausibly occurs in an ordinary answer ("(y/n)", "do you
# want to proceed") reports a working agent as blocked. The surviving markers
# are Claude Code's and cmux's own wording, which an agent has no reason to
# emit while answering.
# Each marker is a phrase the HOST emits as a whole, not a fragment that also
# reads as ordinary prose. `do you trust` and a bare `enter to confirm` were
# dropped for failing that test: a delegated review answering "Do you trust
# this refactor?" would be reported as blocked, and a false `waiting-input`
# spends a person's attention walking to a workspace that wants nothing.
# `esc to cancel` is carried along with `enter to confirm` for the same reason
# — the pair is the host's key hint, while the first half alone is a sentence
# anyone can write.
# Each entry is a whole observed preview, matched from the START (see
# `_blocked_on_prompt`). The guessed `trust this folder` was dropped rather
# than anchored: it was never observed, and under a prefix match it could
# never fire, so keeping it would read as coverage of the folder-trust dialog
# that does not exist. The gap is pinned as an xfail test instead.
_PROMPT_MARKERS = (
    "claude needs your permission",
    "enter to confirm · esc to cancel",
)

# The only events that move the turn. Everything else cmux relays under the
# `agent` category — Notification, SessionStart, PermissionRequest — is noise
# for this decision and actively harmful if counted: Notification fires AFTER
# Stop when the agent goes quiet (measured: Stop at seq 77009, Notification at
# 77168 for the same idle worker), so a rule that simply reads the newest agent
# event flips a finished worker back to "working" seconds after it settles.
#
# SubagentStop is excluded on the same principle from the other side: a
# subagent finishing says nothing about whether the main agent's turn is over.
_TURN_END = "agent.hook.Stop"
_TURN_EVENTS = (
    _TURN_END,
    "agent.hook.PreToolUse",
    "agent.hook.UserPromptSubmit",
)

# Selector shapes. `workspace:N` is what `cmux workspace list` prints and what
# Step 5 captures at delegation time; the UUID is the RPC's own `id` and the
# key events carry as `workspace_id`.
_WS_REF_RE = re.compile(r"^workspace:\d+$")
_UUID_RE = re.compile(r"^[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$")

_RPC_TIMEOUT_S = 10
_EVENTS_TIMEOUT_S = 10
# Once per candidate workspace, so it is kept short — a hung git here would
# multiply across the list.
_GIT_TIMEOUT_S = 5

# How long a workspace must be quiet before a turn-ending `Stop` is allowed to
# mean `idle`.
#
# A Stop hook may answer `{"decision": "block"}`, which re-prompts the model —
# praxis ships several such hooks (`completion-verify`, `strike-counter`,
# `negative-existence-verdict-gate`, `merge-state-claim-gate`). The Stop EVENT
# fires either way, and the relayed payload does not say which happened: across
# 204 Stop events in the retained window, `result` was `acknowledged` and
# `is_error` was null without exception. So a bare Stop cannot distinguish a
# finished worker from one that was just sent back to work.
#
# `last_activity_at` can, because a re-prompted worker keeps moving. Below the
# threshold the answer is `working`, which costs a wait; above it, `idle`, which
# costs a re-dispatch on top of a live session. The two errors are not
# symmetric, so the ambiguous side is given to the cheap one.
_IDLE_QUIET_S = 30

# How far back from the stream head to look for this worker's newest event.
#
# Sized to cmux's whole retained log rather than to a time budget, because the
# obvious cheaper signal does not work: `last_activity_at` keeps climbing while
# a tool runs (measured at 49s into a 50s call), so a busy worker and an idle
# one are indistinguishable by idle time. The event window is the only
# discriminator, and a window narrower than retention silently converts "idle"
# into "unknown" on a chatty host.
#
# Past retention cmux no longer holds the answer, and `unknown` is then correct
# rather than conservative.
_EVENT_WINDOW = 4096
_EVENT_LIMIT = 4096


def _run(argv: list[str], timeout: int) -> str | None:
    """stdout of `argv`, or None on any failure. Never raises."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _normalize(path: str) -> str:
    """Compare paths by their resolved form.

    /tmp is a symlink to /private/tmp on macOS, and cmux reports the resolved
    side while a caller standing in /tmp reports the link. Comparing the raw
    strings finds no workspace and reports a live worker as crashed.
    """
    try:
        return os.path.realpath(path).rstrip("/") or "/"
    except OSError:
        return path.rstrip("/") or "/"


_ROOT_CACHE: dict[str, str] = {}


def _worktree_root(path: str) -> str:
    """`path` reduced to its git worktree root, or `_normalize(path)`.

    Both sides of the workspace comparison have to be reduced the same way.
    `agent-liveness.sh` already does this to its argument, so a worker sitting
    in a SUBDIRECTORY of the worktree — which cmux reports verbatim — fails an
    exact comparison against the root and reads as `crash`, whose remedy is
    re-dispatch. Reducing the candidate too is what makes "the same worktree"
    the thing being compared, rather than "the same string".

    Memoized because the event loop asks this of every relayed event's cwd —
    up to `_EVENT_LIMIT` of them, nearly all repeats — and an un-memoized
    subprocess per line would cost more than the whole probe budget.
    """
    cached = _ROOT_CACHE.get(path)
    if cached is not None:
        return cached
    out = _run(["git", "-C", path, "rev-parse", "--show-toplevel"], _GIT_TIMEOUT_S)
    root = out.strip() if out is not None else ""
    resolved = _normalize(root) if root else _normalize(path)
    _ROOT_CACHE[path] = resolved
    return resolved


def _all_workspaces() -> list[dict[str, object]] | None:
    """Every workspace the RPC reports, or None if the lookup itself failed.

    `None` covers an RPC timeout, a non-zero exit, output that is not JSON, and
    a well-formed object carrying no workspace list — an error envelope or a
    renamed field is an ANSWER ABOUT SOMETHING ELSE, and reading it as "zero
    workspaces" reaches `crash`, whose remedy is re-dispatch.
    """
    raw = _run(["cmux", "rpc", "mobile.workspace.list"], _RPC_TIMEOUT_S)
    if raw is None:
        return None
    try:
        payload: object = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    workspaces: object = payload.get("workspaces")
    if not isinstance(workspaces, list):
        return None
    return [ws for ws in workspaces if isinstance(ws, dict)]


def _workspaces_for(cwd: str) -> list[dict[str, object]] | None:
    """Every workspace whose directory (or one of its terminals') is `cwd`.

    Usually one — a delegated worktree is its own path. But nothing stops two
    workspaces from sitting in the same directory, and then the classification
    describes whichever one is listed first, which may not be the delegate.
    The caller is told how many matched rather than being handed a confident
    answer about the wrong session.

    `None` when the lookup itself failed (RPC timeout, non-zero exit, output
    that is not the expected JSON) and `[]` only when cmux answered and no
    workspace matched. Collapsing the two lets a slow or broken RPC read as
    `crash`, whose documented remedy is re-dispatch — so a live worker would
    have its work started a second time underneath it.
    """
    workspaces = _all_workspaces()
    if workspaces is None:
        return None

    target = _worktree_root(cwd)
    matched: list[dict[str, object]] = []
    for ws in workspaces:
        if not isinstance(ws, dict):
            continue
        candidates: list[object] = [ws.get("current_directory")]
        terminals: object = ws.get("terminals")
        for term in terminals if isinstance(terminals, list) else []:
            if isinstance(term, dict):
                candidates.append(term.get("current_directory"))
        if any(isinstance(c, str) and c and _worktree_root(c) == target for c in candidates):
            matched.append(ws)
    return matched


def _id_for_ref(ref: str) -> str | None:
    """The UUID behind a `workspace:N` ref.

    Two surfaces are needed because neither is complete: the RPC carries
    `preview` / `last_activity_at` / `terminals` but no `ref`, while
    `workspace list --json` carries `ref` but none of those. Events key on the
    UUID, so the ref is translated once here.
    """
    raw = _run(["cmux", "workspace", "list", "--json"], _RPC_TIMEOUT_S)
    if raw is None:
        return None
    try:
        payload: object = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    listed: object = payload.get("workspaces")
    for ws in listed if isinstance(listed, list) else []:
        if not isinstance(ws, dict):
            continue
        if ws.get("ref") == ref:
            found = ws.get("id")
            return found if isinstance(found, str) and found else None
    return None


def _workspace_by_id(workspace_id: str) -> dict[str, object] | None:
    """The RPC entry for one workspace, or None if the lookup failed."""
    entries = _all_workspaces()
    if entries is None:
        return None
    for ws in entries:
        if ws.get("id") == workspace_id:
            return ws
    return None


def _blocked_on_prompt(preview: str | None) -> bool:
    """Whether `preview` is a modal prompt rather than text mentioning one.

    Anchored at the start, not searched anywhere in the string. A modal prompt
    takes the preview over; an agent that merely QUOTES the host — "the
    workspace says Claude needs your permission, so I waited" — is answering,
    not blocked, and a false `waiting-input` spends a person's walk to a
    workspace that wants nothing. Round 2 lengthened the markers against this
    and it did not help: the flaw was where the match was allowed to land, not
    how long the needle was.
    """
    if not preview:
        return False
    low = preview.strip().lower().lstrip("*·•- \t")
    return any(low.startswith(marker) for marker in _PROMPT_MARKERS)


def _latest_seq() -> int | None:
    """The stream's newest sequence, read from the ack frame.

    This is the GLOBAL head and there is no way to ask for a per-category one:
    `--category agent` is recorded in the ack's `filters` but leaves
    `latest_seq` untouched (measured — both forms returned 79166 on the same
    host, seconds apart). So the number handed to `_stream_until` as a stop
    target is routinely a sequence the agent-filtered stream will never emit,
    and that reader cannot rely on reaching it. Its wall-clock bound, not this
    target, is what has to terminate it.
    """
    raw = _run(
        ["cmux", "events", "--after", "0", "--limit", "1", "--no-heartbeat"],
        _EVENTS_TIMEOUT_S,
    )
    if raw is None:
        return None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            frame: object = json.loads(line)
        except ValueError:
            continue
        if not isinstance(frame, dict) or frame.get("type") != "ack":
            continue
        resume: object = frame.get("resume")
        if isinstance(resume, dict):
            seq = resume.get("latest_seq")
            if isinstance(seq, int):
                return seq
        return None
    return None


def _kill_group(proc: subprocess.Popen[str]) -> None:
    """SIGKILL the process's whole group, falling back to the process itself."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def _stream_until(start: int, target: int) -> list[str]:
    """Replayed event lines from `start`, stopping once `target` is reached.

    `cmux events` does not exit after replaying: it keeps streaming live, so a
    `--limit` sized for the replay is never reached and the process runs until
    it is killed. `subprocess.run(timeout=...)` then raises and the caller sees
    no events at all — which read as "this worker is not running", the exact
    wrong answer for a worker that is mid-tool.

    So the stream is consumed line by line and stopped at the sequence the ack
    frame reported as the head. Whatever was collected before the bound is
    returned rather than discarded: the replay arrives first and in full, so a
    cut-short read still holds this worker's newest event, and a partial answer
    beats an empty one.

    The bound is a WATCHDOG, not the in-loop deadline check. `for line in
    stream` blocks until the next line arrives, so a deadline tested inside the
    loop is only reached when a line does — and `target` comes from the global
    head while this stream is filtered to `agent`, so the line that would end
    the loop is frequently one the stream will never emit (see `_latest_seq`).
    A timer that kills the process is the only bound that holds when nothing is
    arriving; the in-loop check stays as the cheap path that stops a stream
    still producing output.
    """
    argv = [
        "cmux",
        "events",
        "--after",
        str(start),
        "--category",
        "agent",
        "--limit",
        str(_EVENT_LIMIT),
        "--no-ack",
        "--no-heartbeat",
    ]
    lines: list[str] = []
    deadline = time.time() + _EVENTS_TIMEOUT_S
    proc = None
    watchdog = None
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            # Its own process group, so the watchdog can take the whole tree.
            start_new_session=True,
        )
        # What unblocks the read below is the pipe's write end closing, and
        # `proc.kill()` alone does not guarantee that: a child of the spawned
        # process inherits that fd and holds it open after its parent dies, so
        # the reader waits out the child instead of the deadline. Measured — a
        # `sh -c 'printf …; sleep 30'` stand-in ran the full 30s under a
        # kill-the-parent watchdog with the timeout set to 2s. Signalling the
        # group is what closes every copy. Daemon so a stuck probe cannot hold
        # up interpreter exit.
        watchdog = threading.Timer(_EVENTS_TIMEOUT_S, _kill_group, args=(proc,))
        watchdog.daemon = True
        watchdog.start()
        stream = proc.stdout
        if stream is None:
            return lines
        for line in stream:
            lines.append(line)
            if time.time() > deadline:
                break
            seq = _seq_of(line)
            if seq is not None and seq >= target:
                break
    except (OSError, ValueError):
        return lines
    finally:
        if watchdog is not None:
            watchdog.cancel()
        if proc is not None:
            _kill_group(proc)
            try:
                _ = proc.wait(timeout=2)
            except subprocess.SubprocessError:
                pass
    return lines


def _seq_of(line: str) -> int | None:
    try:
        frame: object = json.loads(line)
    except ValueError:
        return None
    if not isinstance(frame, dict):
        return None
    seq = frame.get("seq")
    return seq if isinstance(seq, int) else None


def _turn_state(workspace_id: str) -> str:
    """`working`, `idle`, or `no-events` for one workspace.

    The newest agent event carrying this `workspace_id` decides:
    `agent.hook.Stop` is the turn boundary, so it means idle, and anything
    newer than it means the agent is still mid-turn. `SubagentStop` is
    deliberately not a boundary — a subagent finishing says nothing about
    whether the main agent is done.

    Keyed on the workspace, NOT on `cwd`. Delegation defaults to the
    delegator's own directory (`cwd = args.cwd || $(pwd)`), so a cwd filter
    admits the delegator's events alongside the delegate's — including the
    events of the probe making this very call, which then become the newest
    and report every delegate as `working`. Measured on this host: 18
    workspaces sharing one directory. The event's top-level `workspace_id` is
    the same UUID the RPC lists as `id`, so the two surfaces join cleanly.
    """
    latest = _latest_seq()
    if latest is None:
        return "no-events"

    # Anchored backwards from the head, every call. A remembered cursor was the
    # first design and it is actively wrong here: this reads the worker's
    # NEWEST event, so a cursor sitting past that event hides it and the answer
    # flips to "no events". There is no incremental version of "what is the
    # latest thing that happened".
    start = max(0, latest - _EVENT_WINDOW)

    newest_seq = -1
    newest_name = ""
    for line in _stream_until(start, latest):
        line = line.strip()
        if not line:
            continue
        try:
            event: object = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("workspace_id") != workspace_id:
            continue
        name = event.get("name")
        if not isinstance(name, str) or name not in _TURN_EVENTS:
            continue
        seq = event.get("seq")
        if not isinstance(seq, int) or seq <= newest_seq:
            continue
        newest_seq = seq
        newest_name = name

    if newest_seq < 0:
        return "no-events"
    return "idle" if newest_name == _TURN_END else "working"


def _select(selector: str) -> tuple[dict[str, object] | None, str, int]:
    """Resolve a selector to one workspace.

    Returns `(workspace, failure_reason, ambiguous_count)`. Three selector
    forms, in descending order of how much they can be trusted:

      `workspace:N`   a cmux ref — exact, and what Step 5 hands out
      a UUID          the RPC's own `id` — exact
      a path          the fallback, and the WEAK one: delegation defaults to
                      the delegator's directory, so a path routinely names
                      several workspaces at once

    The path form still answers, because a delegator that never captured a ref
    has nothing else, but it reports `ambiguous` so the caller knows the answer
    describes one of N rather than the one it asked about.
    """
    if _WS_REF_RE.match(selector):
        workspace_id = _id_for_ref(selector)
        if workspace_id is None:
            return None, "workspace-ref-not-found", 0
        found = _workspace_by_id(workspace_id)
        return (found, "" if found else "workspace-lookup-failed", 0)

    if _UUID_RE.match(selector):
        entries = _all_workspaces()
        if entries is None:
            return None, "workspace-lookup-failed", 0
        for ws in entries:
            if ws.get("id") == selector:
                return ws, "", 0
        return None, "no-workspace", 0

    matched = _workspaces_for(selector)
    if matched is None:
        return None, "workspace-lookup-failed", 0
    if not matched:
        return None, "no-workspace", 0
    return matched[0], "", len(matched)


def classify(selector: str) -> dict[str, object]:
    """Classify one worker, named by `workspace:N` ref, UUID, or path.

    Never raises.
    """
    if _run(["cmux", "capabilities"], _RPC_TIMEOUT_S) is None:
        return {"state": "unknown", "reason": "cmux-unavailable"}

    workspace, failure, ambiguous = _select(selector)
    if workspace is None:
        # `no-workspace` is the only one of these that means the worker is
        # gone. The rest are lookup failures, and `crash` prescribes
        # re-dispatch — which would start the work a second time underneath a
        # worker that is very likely still running.
        if failure == "no-workspace":
            return {"state": "crash", "reason": "no-workspace"}
        return {"state": "unknown", "reason": failure or "workspace-lookup-failed"}

    workspace_id = workspace.get("id")
    if not isinstance(workspace_id, str) or not workspace_id:
        return {"state": "unknown", "reason": "workspace-has-no-id"}

    result: dict[str, object] = {"workspace": workspace_id}
    if ambiguous > 1:
        result["ambiguous"] = ambiguous
    last_activity = workspace.get("last_activity_at")
    if isinstance(last_activity, (int, float)):
        result["idle_seconds"] = int(max(0.0, time.time() - float(last_activity)))

    preview = workspace.get("preview")
    if _blocked_on_prompt(preview if isinstance(preview, str) else None):
        result["state"] = "waiting-input"
        result["preview"] = preview
        return result

    turn = _turn_state(workspace_id)
    if turn == "idle":
        quiet = result.get("idle_seconds")
        if not isinstance(quiet, int) or quiet < _IDLE_QUIET_S:
            # Stop fired, but the workspace is still moving — which is what a
            # blocked Stop looks like from out here. Hold at `working` until it
            # goes quiet; see `_IDLE_QUIET_S`.
            result["state"] = "working"
            result["reason"] = "stop-not-yet-quiet"
            return result
    if turn == "no-events":
        # The workspace exists and is not prompting, but this worker has left
        # no trace in the retained window. That is not a state — it is the
        # absence of evidence, and calling it `idle` would licence a re-dispatch
        # against a worker that may be working.
        result["state"] = "unknown"
        result["reason"] = "no-events-in-window"
        return result

    result["state"] = turn
    return result


def _shell_quote(value: str) -> str:
    """POSIX single-quoting: close, escape, reopen for an embedded quote."""
    return "'" + value.replace("'", "'\\''") + "'"


def _format(result: dict[str, object]) -> str:
    """Render as `key=value` pairs — the shape shell callers can read with
    `case`/`grep` without pulling in a JSON parser.

    Single quotes, always, even where the value looks inert. `preview` carries
    the delegated agent's own text verbatim, so under the documented
    `eval "$(...)"` a double-quoted value runs any `$(...)` or backtick in that
    text inside the DELEGATOR's shell — the delegate would be choosing what the
    delegator executes. Single quotes suppress every expansion, and quoting
    unconditionally keeps that from depending on a per-value judgement about
    which characters are dangerous.
    """
    parts = []
    for key in ("state", "reason", "workspace", "ambiguous", "idle_seconds", "preview"):
        if key not in result:
            continue
        value = str(result[key]).replace("\n", " ").strip()
        parts.append(f"{key}={_shell_quote(value)}")
    return " ".join(parts)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: agent_liveness.py <path-inside-worktree>", file=sys.stderr)
        return 2
    print(_format(classify(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
