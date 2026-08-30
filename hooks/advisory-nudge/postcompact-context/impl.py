#!/usr/bin/env python3
"""UserPromptSubmit hook: inject post-compaction context into the next prompt.

Reframes the original #466 design ("PreCompact custom_instructions") after
Wave 0 falsified its premise. The Claude Code PreCompact event accepts only
top-level decision/reason/continue/stopReason/suppressOutput/systemMessage —
no `hookSpecificOutput.additionalContext` channel exists at that event.

The transcript JSONL however records compaction as a `user`-type record with
`isCompactSummary: true` and a stable `uuid`. UserPromptSubmit fires before
each new user prompt and DOES support `hookSpecificOutput.additionalContext`,
so the same goal — make the post-compaction prompt aware of carried-over
session state — can be achieved by detecting the compaction marker in the
transcript tail and injecting context on the first prompt after it.

Behaviour
=========

On every `UserPromptSubmit`:

1. Read `transcript_path`, `session_id`, `cwd` from the payload. Any missing
   → silent fail-open.
2. Tail-read the transcript JSONL (last N lines, default 100) looking for the
   most recent `{"type": "user", "isCompactSummary": true, ...}` entry.
3. If none found → silent (no compaction in recent history).
4. Compare the compaction entry's `uuid` against the session state file's
   `last_compact_uuid_emitted`. If they match → already injected for this
   compaction, silent. Unlocked: this only skips work, it does not decide
   the injection (step 6 does).
5. Gather context (read-only, fail-open per source):
     - `session_id`        — from payload
     - `cwd`               — from payload (worktree absolute path)
     - `git branch`        — `git -C <cwd> branch --show-current`
     - `active PR`         — `gh pr list --state open --head <branch>` (JSON)
     - `strike state`      — read `~/.praxis/state/strikes/<sid>.json` first;
                             fallback to `~/.claude/state/praxis/strikes/<sid>.json`
                             when no `PRAXIS_STATE_DIR` override is set and the
                             new location is absent (pre-#527 legacy support)
6. Under `state_lock`, re-read the state and claim the uuid: on a match this
   time a sibling published it while step 5 was shelling out, and this run
   goes silent; otherwise record `last_compact_uuid_emitted = <uuid>`.
7. Emit `hookSpecificOutput.additionalContext` JSON to stdout.

State file
==========

`<PRAXIS_HOME>/cache/postcompact-context-${session_id}.json`

Path resolution: `PRAXIS_POSTCOMPACT_CONTEXT_FILE` env override → session_id
keyed file. The PPID fallback used by sibling `preflight-gate/session-intent`
is dropped here because `main()` rejects payloads with missing `session_id`
before `resolve_state_path` is reached, so the fallback would be unreachable.

Env vars
========

`PRAXIS_POSTCOMPACT_CONTEXT_FILE`   — explicit state file path (test override)
`PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT=1` — full bypass (silent, no scan)
`PRAXIS_POSTCOMPACT_TAIL_LINES`     — tail line count (default 100)

Fail-open
=========

Every external call (file read, subprocess, JSON decode) is wrapped. No
exception propagates. Worst case: a compaction goes uninjected. The hook
NEVER blocks the prompt.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _paths import (  # type: ignore[import-not-found]  # noqa: E402
    legacy_state_dir,
    praxis_state_dir,
    resolve_cache_file,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _state_lock import state_lock  # type: ignore[import-not-found]  # noqa: E402

DEFAULT_TAIL_LINES = 100
BYPASS_ENV = "PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT"
STATE_FILE_ENV = "PRAXIS_POSTCOMPACT_CONTEXT_FILE"
TAIL_LINES_ENV = "PRAXIS_POSTCOMPACT_TAIL_LINES"


# ---------------------------------------------------------------------------
# Payload + state-file helpers (mirrors session-intent canonical pattern)
# ---------------------------------------------------------------------------

def _extract_session_id(payload: dict) -> str | None:
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None


def resolve_state_path(session_id: str) -> str:
    """Resolve the dedup state file path.

    Priority: `PRAXIS_POSTCOMPACT_CONTEXT_FILE` env override → `session_id`
    keyed file under `<PRAXIS_HOME>/cache`. The caller is responsible for
    rejecting missing session_id BEFORE calling this — `main()` already
    does so, so no PPID fallback is needed (the session-intent hook keeps
    one only to support direct CLI / test invocation without a payload).
    """
    explicit = os.environ.get(STATE_FILE_ENV, "").strip()
    if explicit:
        return explicit
    return resolve_cache_file(
        f"postcompact-context-{session_id}.json", session_id=session_id
    )


def read_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, UnicodeDecodeError):
        # ValueError covers JSONDecodeError + embedded-null path raises.
        pass
    return {}


def write_state(path: str, state: dict) -> None:
    """Publish the state atomically, staging through a name of its own.

    The comment above calls this family the session-intent canonical pattern,
    but until #1034 this was the one member that wrote the final name in
    place. That makes the state file its own staging file — the degenerate
    case of DESIGN.md Q0's shared `<path>.tmp` — so two siblings sharing a
    `session_id` truncate and write the same bytes at the same offset, and the
    shorter write leaves the longer one's tail behind. Measured, unforced, on
    the pre-fix code: 5 of 300 concurrent pairs published
    `{..."short-uuid"}uuuu..."}` — bytes `read_state`'s `except ValueError`
    answers with an empty dict.

    `tempfile.mkstemp` is the fix its three unlocked siblings already had: the
    name is unique per call, so no sibling can write into this one's file, and
    `os.replace` publishes it whole. It is also the floor under `main()`'s
    `state_lock`, which is fail-open by contract — an unacquired lock leaves
    the pair racing, and the race has to land on a lost update rather than an
    unreadable file (the same argument #970 recorded for `jq-config`).
    """
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=parent or ".", prefix=".postcompact-context-"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(tmp_path, path)
        except Exception:
            # Broad on purpose, and mirrored from session-intent: json.dump
            # raises TypeError, not OSError, and any failure before the
            # replace must not leak the staging file.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except (OSError, ValueError):
        pass  # non-fatal: next run will simply re-inject


def claim_injection(path: str, compact_uuid: str) -> dict | None:
    """Decide, under the caller's lock, whether THIS process injects.

    Returns the state to publish when the claim is ours, and None when a
    sibling already recorded `compact_uuid` — the caller then returns without
    injecting. It reads and decides but deliberately does not write: the
    publish is the caller's, one statement later, still inside the lock.

    Split out from `main()` for two reasons. It is the whole of the critical
    section's logic, so keeping it in one named place is what keeps the
    section auditable against the budget note in `_active_pr` — the section
    must stay shorter than the lock's own acquisition deadline. And it
    is the decision the double-injection race turns on, so the race test can
    hold both children between the decision and the publish — a barrier on a
    function that had already written would let the first child publish before
    the second decided, which is the interleaving the arm exists to produce.
    """
    state = read_state(path)
    if state.get("last_compact_uuid_emitted") == compact_uuid:
        return None
    state["last_compact_uuid_emitted"] = compact_uuid
    return state


# ---------------------------------------------------------------------------
# Transcript tail scan
# ---------------------------------------------------------------------------

# Cheap lexical marker for a compaction record. A line without it can never
# satisfy `find_latest_compact_summary`, so the tail scan does not retain it.
_COMPACT_MARKER = '"isCompactSummary"'


def _tail_candidate_lines(path: str, n: int, marker: str = _COMPACT_MARKER) -> list[str]:
    """Last `n` lines of a UTF-8 text file, with non-candidates elided to "".

    The window is still the last `n` lines — a line that does not contain
    `marker` is kept as an empty placeholder so its slot, and therefore the
    caller's "within the last n lines" semantics, survives. Only candidate
    lines are held in full.

    That distinction is the point. A plain `deque(fh, maxlen=n)` bounds memory
    by LINE COUNT, not by bytes: a transcript whose records are large — and a
    compaction summary is exactly such a record — costs `n * line_size`.
    Measured at n=100 with 200KB records, the plain form peaked at 20.9 MB of
    retained tail. Compactions are rare, so eliding non-candidates drops that
    to the size of the few real candidates.

    What is NOT bounded, by either form: the scan reads the whole file to
    reach its end (57 ms on a 100 MB transcript, measured), and a single
    genuine candidate line is held at its full size because it still has to be
    parsed. ValueError is caught alongside OSError to cover embedded-null path
    payloads.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return list(deque((line if marker in line else "" for line in fh), maxlen=n))
    except (OSError, ValueError):
        return []


def find_latest_compact_summary(transcript_path: str, n_lines: int) -> dict | None:
    """Scan the tail of the JSONL transcript for the most recent compaction.

    A compaction is encoded as `{"type": "user", "isCompactSummary": true, ...}`.
    The function returns the most-recent matching record (by file order — the
    transcript is append-only and time-ordered) or None.
    """
    lines = _tail_candidate_lines(transcript_path, n_lines)
    if not lines:
        return None
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw or '"isCompactSummary"' not in raw:
            # quick lexical filter — avoids json.loads cost for >99% of records
            continue
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        if record.get("type") != "user":
            continue
        if record.get("isCompactSummary") is not True:
            continue
        return record
    return None


# ---------------------------------------------------------------------------
# Context gathering (read-only, fail-open per source)
# ---------------------------------------------------------------------------

def _run(cmd: list[str], cwd: str | None = None, timeout: float = 2.0) -> str:
    """Run a read-only subprocess and return stdout (stripped) or ''.

    ValueError is caught alongside OSError/SubprocessError so embedded-null
    cwd / argv payloads degrade silently instead of crashing the hook.
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            return (result.stdout or "").strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return ""


def _env_timeout(var: str, default: float) -> float:
    """Subprocess-timeout override from env; falls back to default on a
    missing, non-numeric, or non-positive value.

    The production defaults (1.5s git / 3.0s gh) are tuned against the 8s
    manifest budget for real `git`/`gh`. Under a full local test-suite run,
    however, fork/exec + python-startup contention can push even a trivial
    mock subprocess past those tight bounds, producing a false timeout (empty
    PR section) and a flaky assertion. Tests set these vars high so the
    assertion measures rendering logic, not host load — production behavior is
    unchanged because the defaults are identical to the previous literals.
    """
    raw = os.environ.get(var)
    if raw is None:
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _git_branch(cwd: str) -> str:
    # 1.5s leaves headroom under the 8s manifest budget when paired with
    # _active_pr's 3s. Git branch-show is essentially instant; the timeout
    # exists only to bound pathological FS conditions.
    return _run(
        ["git", "-C", cwd, "branch", "--show-current"],
        cwd=cwd,
        timeout=_env_timeout("PRAXIS_POSTCOMPACT_GIT_TIMEOUT", 1.5),
    )


def _active_pr(cwd: str, branch: str) -> dict | None:
    """Return {"number": int, "url": str, "title": str} or None.

    Uses `gh pr list --state open --head <branch>` so we resolve a PR even
    when cwd is not the repo root the PR was created from. Fail-open on
    missing gh / no PR / JSON parse error.
    """
    if not branch:
        return None
    # 3.0s gh + 1.5s git + ~0.5s python startup is a 5.0s build ceiling. Since
    # #1034 the run can also wait on `state_lock`, whose acquisition deadline
    # is 2.0s, for an absolute ceiling of 7.0s under the manifest's `timeout:
    # 8`. That 2.0s is a bound, not a cost: `main()` builds the context BEFORE
    # taking the lock, so the section a sibling waits on is one `read_state`
    # plus one `write_state` — the deadline is reachable only if a holder is
    # descheduled through it, never because a holder is calling gh. (Build the
    # context inside the section instead and the wait becomes the build, which
    # exceeds the deadline outright.) Authenticated gh on a fast network
    # responds in <1s; the timeout exists to bound auth-prompt / network-hung
    # paths so the hook never trips Claude Code's hard timeout.
    out = _run(
        [
            "gh", "pr", "list",
            "--state", "open",
            "--head", branch,
            "--json", "number,url,title",
            "--limit", "1",
        ],
        cwd=cwd,
        timeout=_env_timeout("PRAXIS_POSTCOMPACT_GH_TIMEOUT", 3.0),
    )
    if not out:
        return None
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        entry = data[0]
        if entry.get("number") and entry.get("url"):
            return {
                "number": entry["number"],
                "url": entry["url"],
                "title": entry.get("title") or "",
            }
    return None


def _strike_state(session_id: str) -> dict | None:
    """Read the praxis strike state for this session. None if absent/empty.

    Reads the host-neutral ~/.praxis/state default (#527), with a read-fallback
    to the pre-#527 ~/.claude/state/praxis location when no PRAXIS_STATE_DIR
    override is set and the new location is absent — so strike state written by
    an older strike-counter still surfaces here.
    """
    bases = [praxis_state_dir()]
    if not os.environ.get("PRAXIS_STATE_DIR"):
        bases.append(legacy_state_dir())
    data = None
    for base in bases:
        path = os.path.join(base, "strikes", f"{session_id}.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            break
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    count = data.get("count")
    if not isinstance(count, int) or count <= 0:
        return None
    reasons = data.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    return {"count": count, "reasons": [str(r) for r in reasons]}


def build_context(
    session_id: str,
    cwd: str,
    compact_uuid: str,
    compact_timestamp: str,
) -> str:
    """Assemble the human-readable additionalContext block."""
    branch = _git_branch(cwd)
    pr = _active_pr(cwd, branch) if branch else None
    strikes = _strike_state(session_id)

    lines = [
        "📎 Praxis post-compaction context",
        "",
        "Session state carried across the compaction boundary:",
        f"  • session_id : {session_id}",
        f"  • cwd        : {cwd}",
        f"  • branch     : {branch or '(detached/unknown)'}",
    ]

    if pr:
        lines.append(f"  • active PR  : #{pr['number']} — {pr['title']}")
        lines.append(f"                 {pr['url']}")
    else:
        lines.append("  • active PR  : (none for current branch)")

    if strikes:
        lines.append(f"  • strikes    : {strikes['count']}/3")
        for idx, reason in enumerate(strikes["reasons"], start=1):
            lines.append(f"      {idx}. {reason}")
    else:
        lines.append("  • strikes    : 0/3")

    lines.append("")
    ts_repr = compact_timestamp or "(unknown)"
    lines.append(
        f"Compaction marker uuid={compact_uuid} timestamp={ts_repr}. "
        "This context is injected once per compaction event; subsequent prompts "
        "will not repeat it."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _tail_lines_setting() -> int:
    raw = os.environ.get(TAIL_LINES_ENV, "").strip()
    if not raw:
        return DEFAULT_TAIL_LINES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TAIL_LINES
    if value < 1:
        return DEFAULT_TAIL_LINES
    return value


@fail_open
def main() -> int:
    if os.environ.get(BYPASS_ENV, "").strip() == "1":
        return 0

    payload = read_payload()
    if payload is None:
        return 0
    if not isinstance(payload, dict):
        return 0

    transcript_path = payload.get("transcript_path")
    session_id = _extract_session_id(payload)
    cwd = payload.get("cwd")

    if not isinstance(transcript_path, str) or not transcript_path:
        return 0
    if not session_id:
        return 0
    if not isinstance(cwd, str) or not cwd:
        return 0
    if not Path(transcript_path).is_file():
        return 0

    compact = find_latest_compact_summary(transcript_path, _tail_lines_setting())
    if not compact:
        return 0

    compact_uuid = compact.get("uuid")
    compact_timestamp = compact.get("timestamp", "")
    if not isinstance(compact_uuid, str) or not compact_uuid:
        return 0

    state_path = resolve_state_path(session_id)

    # Unlocked fast path. The compaction marker stays in the transcript tail
    # for as many prompts as it takes to scroll out, so this hook re-reaches
    # this point on every one of them; without the pre-check each would pay
    # the git+gh build below only to discard it. A stale read here can cost
    # at most one wasted build — it never decides the injection, which is
    # re-taken under the lock.
    if read_state(state_path).get("last_compact_uuid_emitted") == compact_uuid:
        return 0  # already injected for this compaction

    # Built BEFORE the lock, and that ordering is load-bearing (#1034 review).
    # `build_context` shells out to git and gh, whose timeouts bound it at
    # ~4.5s — more than double `state_lock`'s 2s acquisition deadline. Build
    # it inside the section and a holder on the slow `gh` path guarantees the
    # deadline expires for every sibling; each then proceeds unlocked, reads
    # state the holder has not written yet, and injects too — the exact
    # double injection the lock is here to close.
    context = build_context(session_id, cwd, compact_uuid, compact_timestamp)

    # Serialized because the read-modify-write below shares a name with any
    # sibling on this `session_id` — DESIGN.md Q0, re-graded against a live
    # measurement in #1034. The section now holds one `read_state` and one
    # `write_state`, so a sibling waits on two file operations rather than on
    # a network call. `claim_injection` re-reads inside it on purpose: the
    # fast path above ran before the build, and a sibling that published
    # during it must still turn this process back.
    with state_lock(state_path):
        state = claim_injection(state_path, compact_uuid)
        if state is None:
            return 0  # a sibling injected while this process was building

        write_state(state_path, state)

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
