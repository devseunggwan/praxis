"""Host approval provider for `gh pr merge` (issue #1639).

A host that collects the human's merge answer in its own channel (a dashboard
button, a chat thread) can hand it to the merge gates through an executable it
names in the hook process environment:

    PRAXIS_MERGE_APPROVAL_PROVIDER=<absolute path>

Unset, nothing here runs and both merge gates behave exactly as before. Set,
the gates call it only for a merge they would otherwise stop:

    <provider> merge-approval --repo <owner/name> --pr <N> --session <session_id>

with stdin closed and a 5 s timeout (less when the dispatch group's deadline
leaves less). Exit 0 with one JSON object

    {"approved": true, "ref": "<unique per approval>", "repo": "owner/name",
     "pr": N, "briefing": "<text the human saw>", "approved_at": <epoch s>}

is a candidate; anything else is a refusal whose reason the gate names.
praxis still judges the candidate: repo and PR must equal the merge target,
the briefing must pass the same item counter the transcript path uses, and the
ref must not have released an earlier merge.

One approval releases one merge. The ref is recorded as spent, under the state
lock, when it releases a call. It is freed again only when that call came back
as a failed tool result (a sibling gate blocked it, or `gh` failed), because
then nothing merged; a call still in flight, or one whose result is not in the
transcript, keeps it spent.

Both gates ask through `decide`, and the first one to ask for a tool call
records the outcome under that call's id, so the provider runs and the ref is
spent once per tool call whatever order the gates run in.

Not a bypass: the agent cannot set the environment of the hook process (an
inline `VAR=… gh pr merge` prefix never reaches it), and the provider replaces
only where the answer comes from, not the briefing check or one-answer-one-merge.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Optional

from _hook_runtime import MIN_SUBPROC_BUDGET_SEC, remaining_budget  # type: ignore[import-not-found]
from _paths import praxis_state_dir  # type: ignore[import-not-found]
from _state_lock import state_lock  # type: ignore[import-not-found]

PROVIDER_ENV = "PRAXIS_MERGE_APPROVAL_PROVIDER"
PROVIDER_TIMEOUT_SEC = 5.0
PROVIDER_OUTPUT_CAP = 64 * 1024
REASON_CAP = 200
STATE_FILE = "merge-approval.json"
# Long enough for every gate of one tool call to read the same outcome.
CALL_RECORD_TTL_SEC = 600
# Spent refs are kept this long; an approval older than that is refused, so a
# pruned ref can never be replayed.
REF_RETENTION_SEC = 30 * 24 * 3600
# Tolerated provider clock lead. A later approved_at would outlive the spent-ref
# record and let the same ref release again once that record is pruned.
CLOCK_SKEW_SEC = 300
# Kept back from the hook budget for recording the outcome after the provider.
STATE_WRITE_MARGIN_SEC = 0.25


@dataclass(frozen=True)
class Decision:
    released: bool
    detail: str

    def note(self) -> str:
        """One line for a gate's message."""
        if self.released:
            return f"host approval provider released this merge ({self.detail})"
        return f"host approval provider did not release this merge: {self.detail}"


def configured() -> bool:
    return bool(os.environ.get(PROVIDER_ENV))


def decide(*, session_id: str, tool_use_id: str, repo: Optional[str], pr: Optional[str],
           refusal: Optional[str], count_items: Callable[[str], int], min_items: int,
           call_failed: Callable[[str], bool]) -> Decision:
    """Ask the provider whether this one merge was approved, once per tool call.

    `refusal` is the gate's reason this merge cannot be bound to one target
    (compound command, no `--repo`); the provider is then not called.
    `call_failed(tool_use_id)` answers whether that earlier call came back as a
    failed tool result in this session's transcript.
    """
    provider = os.environ.get(PROVIDER_ENV, "")
    if not os.path.isabs(provider) or not os.path.isfile(provider) or not os.access(provider, os.X_OK):
        return Decision(False, f"{PROVIDER_ENV} is not an absolute path to an executable file")
    if refusal:
        return Decision(False, refusal)
    if not repo or not pr:
        return Decision(False, "the merge does not name one PR number and its repo")
    if not session_id or not tool_use_id:
        return Decision(False, "the hook payload has no session_id or tool_use_id to bind the approval to")
    path = os.path.join(praxis_state_dir(), STATE_FILE)
    key = _call_key(session_id, tool_use_id, repo, pr)
    with state_lock(path) as locked:
        if not locked:
            return Decision(False, "the approval state lock was not acquired")
        known = _recorded(_load(path), key, time.time())
    if known is not None:
        return known
    candidate = _ask_provider(provider, repo, pr, session_id, count_items, min_items)
    with state_lock(path) as locked:
        if not locked:
            return Decision(False, "the approval state lock was not acquired")
        now = time.time()
        state = _load(path)
        known = _recorded(state, key, now)
        if known is not None:
            return known
        decision = candidate if isinstance(candidate, Decision) else _spend(
            state, candidate, now, session_id, tool_use_id, call_failed)
        state["calls"][key] = {"released": decision.released, "detail": decision.detail, "at": now}
        _save(path, state)
    return decision


def _call_key(session_id: str, tool_use_id: str, repo: str, pr: str) -> str:
    material = json.dumps([session_id, tool_use_id, repo.lower(), str(pr)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _ask_provider(provider: str, repo: str, pr: str, session_id: str,
                  count_items: Callable[[str], int], min_items: int) -> Decision | dict:
    """The validated record, or a refusal."""
    argv = [provider, "merge-approval", "--repo", repo, "--pr", str(pr), "--session", session_id]
    # Under the dispatcher the gate shares the group's deadline; outliving it
    # gets the whole dispatcher killed, so the provider gets what is left.
    timeout = min(PROVIDER_TIMEOUT_SEC,
                  remaining_budget(PROVIDER_TIMEOUT_SEC + STATE_WRITE_MARGIN_SEC) - STATE_WRITE_MARGIN_SEC)
    if timeout < MIN_SUBPROC_BUDGET_SEC:
        return Decision(False, "too little hook time was left to ask the provider")
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return Decision(False, f"the provider did not answer within {timeout:.2g} s")
    except OSError as exc:
        return Decision(False, f"the provider could not be run ({type(exc).__name__})")
    if proc.returncode != 0:
        return Decision(False, f"the provider exited {proc.returncode}{_stated_reason(proc.stdout)}")
    try:
        record = json.loads(proc.stdout[:PROVIDER_OUTPUT_CAP].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return Decision(False, "the provider output is not one JSON object")
    if not isinstance(record, dict):
        return Decision(False, "the provider output is not one JSON object")
    if record.get("approved") is not True:
        return Decision(False, "the provider reports no approval")
    ref = record.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        return Decision(False, "the approval has no ref")
    got_repo = record.get("repo")
    if not isinstance(got_repo, str) or got_repo.lower() != repo.lower():
        return Decision(False, f"the approval names repo {got_repo!r}, not {repo}")
    got_pr = record.get("pr")
    if isinstance(got_pr, bool) or not isinstance(got_pr, int) or str(got_pr) != str(pr):
        return Decision(False, f"the approval names PR {got_pr!r}, not {pr}")
    approved_at = record.get("approved_at")
    age = (time.time() - approved_at
           if isinstance(approved_at, (int, float)) and not isinstance(approved_at, bool)
           and math.isfinite(approved_at) else None)
    if age is None or not -CLOCK_SKEW_SEC <= age <= REF_RETENTION_SEC:
        return Decision(False, "the approval has no approved_at, is in the future, "
                               "or is older than 30 days")
    briefing = record.get("briefing")
    items = count_items(briefing) if isinstance(briefing, str) else 0
    if items < min_items:
        return Decision(False, f"the briefing the human approved has {items} of 6 items, "
                               f"fewer than {min_items}")
    return {"ref": ref}


def _stated_reason(stdout: bytes) -> str:
    """` (reason: …)` when a refusing provider printed a JSON object with a
    string `reason`, so the block message says why; empty otherwise."""
    try:
        record = json.loads(stdout[:PROVIDER_OUTPUT_CAP].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ""
    reason = record.get("reason") if isinstance(record, dict) else None
    if not isinstance(reason, str) or not reason.strip():
        return ""
    clean = "".join(ch if ch.isprintable() else " " for ch in reason).strip()
    return f" (reason: {clean[:REASON_CAP]})"


def _spend(state: dict, candidate: dict, now: float, session_id: str, tool_use_id: str,
           call_failed: Callable[[str], bool]) -> Decision:
    ref = candidate["ref"]
    spent = state["refs"].get(ref)
    if isinstance(spent, dict):
        freed = (spent.get("session") == session_id
                 and isinstance(spent.get("call"), str) and call_failed(spent["call"]))
        if not freed:
            return Decision(False, f"approval {ref} already released another merge")
    state["refs"][ref] = {"session": session_id, "call": tool_use_id, "at": now}
    return Decision(True, ref)


def _recorded(state: dict, key: str, now: float) -> Decision | None:
    rec = state["calls"].get(key)
    if not isinstance(rec, dict) or now - float(rec.get("at", 0)) >= CALL_RECORD_TTL_SEC:
        return None
    return Decision(rec.get("released") is True, str(rec.get("detail", "")))


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    now = time.time()
    calls = raw.get("calls")
    refs = raw.get("refs")
    calls = calls if isinstance(calls, dict) else {}
    refs = refs if isinstance(refs, dict) else {}
    return {
        "calls": {k: v for k, v in calls.items()
                  if isinstance(v, dict) and isinstance(v.get("at"), (int, float))
                  and now - v["at"] < CALL_RECORD_TTL_SEC},
        "refs": {k: v for k, v in refs.items()
                 if isinstance(v, dict) and isinstance(v.get("at"), (int, float))
                 and now - v["at"] < REF_RETENTION_SEC},
    }


def _save(path: str, state: dict) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=parent,
                                     prefix=os.path.basename(path), suffix=".tmp",
                                     delete=False) as tmp:
        tmp.write(json.dumps(state))
    os.replace(tmp.name, path)
