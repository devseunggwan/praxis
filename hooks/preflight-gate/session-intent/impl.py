#!/usr/bin/env python3
"""Multi-event hook: detect session-scope read-intent → mutation-pivot drift.

Two event handlers share a session state file:

  UserPromptSubmit
    - First prompt of the session is the **anchor**. Scan it for read-intent
      lexical signals (compare / analyze / review / 비교 / 검토 / ...) and
      record the verdict in the state file. Subsequent prompts do NOT
      overwrite the anchor — only the first one counts.
    - Every prompt is additionally scanned for explicit mutation verbs
      (close / merge / push / comment / 닫 / 머지 / 푸시 / 등록 / ...). When
      seen, the `mutation_verb_seen_at` timestamp is updated.

  PreToolUse
    - When a mutation-capable Bash command (`gh issue (close|comment|create|
      edit|delete)`, `gh pr (create|comment|edit|merge)`,
      `gh api ... --method (POST|PATCH|DELETE)`) is about to execute, read
      the state file:
        * read_intent_anchored AND NOT mutation_verb_seen → emit
          `permissionDecision: "ask"` (default) or `"deny"` if
          `PRAXIS_INTENT_PIVOT_MODE=block`.
        * Otherwise → silent pass.

Session state location (in priority order):
  1. `PRAXIS_SESSION_INTENT_FILE` env var (test override; explicit path)
  2. `<PRAXIS_HOME>/cache/session-intent-${session_id}.json` when the hook
     payload carries a `session_id` field (the canonical praxis hook pattern —
     same key used by `completion-verify.sh`, `retrospect-mix-check.sh`,
     `strike-counter.sh`). Pre-#903 this lived under `${TMPDIR}`;
     `resolve_cache_file` adopts that file if it is still there.
  3. `${PPID}` replaces `${session_id}` in the filename (back-compat fallback
     when the payload does not include `session_id`)

The `$CLAUDE_PROJECT_DIR/.praxis-session-intent.json` branch was removed
intentionally (codex P1 review on PR #190): a project-rooted state file
persists across Claude Code sessions on the same project, which violates
the session-scope contract — a previous session that recorded
`mutation_verb_seen=True` would silently release the gate for a *new*
read-only session in the same project. Path resolution is strictly
session-scoped.

`session_id` is now the primary key (codex R2 P1 review on PR #190): the
prior PPID-only design was incoherent within a single session because
Claude Code spawns hook commands in separate processes, and `os.getppid()`
in one hook invocation can differ from the PPID in a subsequent hook
invocation within the same session. The UserPromptSubmit hook would write
to one PPID-suffixed file and the PreToolUse hook would read a different
one — state never cohered, gate failed open silently. The `session_id`
field from the hook stdin payload is stable across hook invocations within
a session, so it correctly addresses that incoherence while still resetting
across session boundaries.

State file shape:
  {
    "read_intent_anchored": bool,
    "read_intent_marker": "compare|review|...",   # the matched token
    "first_prompt_snippet": "compare pros/cons ...",
    "mutation_verb_seen": bool,
    "mutation_verb_seen_at": "compare|merge|...",
  }

Fail-open everywhere — malformed payloads / unreadable state files / missing
fields all exit 0 silently. The hook may emit warnings but MUST NOT crash
the Claude Code session.

v1 scope: Bash-bound `gh` mutations only. MCP mutations (Slack post,
Notion update, etc.) are documented v2 (see AGENTS.md).
"""
from __future__ import annotations

import json
import os
import re
import sys
import sys as _sys
import tempfile
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    _is_gh_binary,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from _paths import resolve_cache_file  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Lexical signal sets — module-level constants (Korean + English).
# Whole-token / whole-substring matching against the *user prompt text*. The
# prompt is lowercased before substring comparison so authors can keep the
# constants in their natural case.
# ---------------------------------------------------------------------------

# Read-intent: verbs and noun-phrases that indicate analysis / comparison
# without an execution mandate. These are matched as case-insensitive
# substrings against the prompt to tolerate inflection ("comparing",
# "비교해봐", etc.). Korean tokens use char-level substring match by design —
# Korean does not tokenize on whitespace the way English does.
READ_INTENT_MARKERS = (
    # English (matched as whole-word via regex below)
    "compare",
    "analyze",
    "analyse",
    "review",
    "check",
    "investigate",
    "explore",
    "evaluate",
    "assess",
    "examine",
    "diff",
    "pros/cons",
    "pros and cons",
    "trade-off",
    "tradeoff",
    "summary",
    "summarize",
    "summarise",
    "look at",
    "look into",
    # Korean (matched as substring — no word boundary semantics)
    "비교",
    "검토",
    "분석",
    "확인",
    "조사",
    "살펴",
    "장단점",
    "요약",
    "정리해",
    "리뷰",
    "체크",
)

# Mutation verbs from the user. Their appearance in any prompt releases the
# session-scope guard for subsequent mutation tool calls.
MUTATION_VERB_MARKERS = (
    # English
    "close",
    "merge",
    "post",
    "push",
    "comment",
    "create",
    "cancel",
    "delete",
    "remove",
    "publish",
    "send",
    "submit",
    "approve",
    "reject",
    "execute",
    "run it",
    "go ahead",
    "proceed",
    "ship it",
    # Korean
    "닫",
    "머지",
    "게시",
    "푸시",
    "등록",
    "삭제",
    "취소",
    "전송",
    "보내",
    "승인",
    "반려",
    "실행해",
    "올려",
    "진행해",
    "처리해",
)

# English markers split into whole-word vs substring. Whole-word avoids
# matching `comment` inside `commentary` and `review` inside `reviewer`. The
# Korean set is always substring (CJK has no word boundary).
def _is_korean(text: str) -> bool:
    return any(ord(c) >= 0x3000 for c in text)


_ENGLISH_RI = tuple(m for m in READ_INTENT_MARKERS if not _is_korean(m))
_KOREAN_RI = tuple(m for m in READ_INTENT_MARKERS if _is_korean(m))
_ENGLISH_MV = tuple(m for m in MUTATION_VERB_MARKERS if not _is_korean(m))
_KOREAN_MV = tuple(m for m in MUTATION_VERB_MARKERS if _is_korean(m))


def _english_match(prompt_lower: str, markers: tuple[str, ...]) -> str:
    """Return the first whole-word English marker present, or empty string."""
    for marker in markers:
        # Escape for regex; allow multi-word markers (`pros/cons`, `look at`).
        pattern = r"(?<![A-Za-z0-9])" + re.escape(marker) + r"(?![A-Za-z0-9])"
        if re.search(pattern, prompt_lower):
            return marker
    return ""


def _korean_match(prompt: str, markers: tuple[str, ...]) -> str:
    """Return the first Korean substring marker present, or empty string."""
    for marker in markers:
        if marker in prompt:
            return marker
    return ""


def detect_read_intent(prompt: str) -> str:
    """Return the matched read-intent marker, or empty string."""
    prompt_lower = prompt.lower()
    hit = _english_match(prompt_lower, _ENGLISH_RI)
    if hit:
        return hit
    return _korean_match(prompt, _KOREAN_RI)


def detect_mutation_verb(prompt: str) -> str:
    """Return the matched mutation verb, or empty string."""
    prompt_lower = prompt.lower()
    hit = _english_match(prompt_lower, _ENGLISH_MV)
    if hit:
        return hit
    return _korean_match(prompt, _KOREAN_MV)


# ---------------------------------------------------------------------------
# State file IO
# ---------------------------------------------------------------------------

def _extract_session_id(payload: dict) -> str | None:
    """Return the trimmed `session_id` from the hook payload, or None.

    This is the canonical praxis hook session key — same field consumed by
    `completion-verify.sh`, `retrospect-mix-check.sh`, and
    `strike-counter.sh` via `jq -r '.session_id // ...'`.
    """
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None


def resolve_state_path(session_id: str | None = None) -> str:
    """Resolve the session state file path with the documented priority.

    Priority order:
      1. `PRAXIS_SESSION_INTENT_FILE` env var (explicit override for tests).
      2. `session_id` from the hook payload (primary key — stable across
         hook invocations within a single Claude Code session).
      3. PPID fallback (back-compat path when no `session_id` was supplied;
         retained so direct CLI/test usage without a payload still works).

    The `$CLAUDE_PROJECT_DIR/.praxis-session-intent.json` branch was
    intentionally removed (codex P1 on PR #190): project-rooted state
    persists across sessions on the same project and would silently leak
    `mutation_verb_seen=True` from a prior session into a new read-only
    session, breaking the session-scope contract.

    The PPID-only fallback (codex R1 on PR #190) was insufficient (codex
    R2 on PR #190): Claude Code spawns hook commands in separate
    processes, so `os.getppid()` can differ between UserPromptSubmit and
    PreToolUse invocations *within the same session*. `session_id` is
    stable across those invocations and is now the primary key.
    """
    explicit = os.environ.get("PRAXIS_SESSION_INTENT_FILE", "").strip()
    if explicit:
        return explicit

    key = session_id or str(os.getppid())
    return resolve_cache_file(f"session-intent-{key}.json", session_id=key)


def read_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {}


def write_state(path: str, state: dict) -> None:
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        # Atomic write (temp + rename): a crash mid-write must not leave a
        # truncated JSON file — a concurrent PreToolUse read of partial JSON
        # would fail to parse and silently fail-open the gate (issue #647 H7).
        fd, tmp_path = tempfile.mkstemp(
            dir=parent or ".", prefix=".session-intent-"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(tmp_path, path)
        except Exception:
            # Broad on purpose: json.dump can raise TypeError (not OSError);
            # any failure before os.replace must unlink the temp file.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError:
        # State write failure is non-fatal — the gate will simply not fire
        # for this session, equivalent to a missing state file.
        pass


# ---------------------------------------------------------------------------
# Mutation-capable Bash detection (v1 scope: `gh` only)
# ---------------------------------------------------------------------------

# gh global flags that consume one additional argument value. Same set as
# pre-merge-approval-gate.py — must be peeled before checking subcommand.
GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})

# gh subcommand-object → mutating action verbs.
GH_MUTATING_VERBS = {
    "issue": {"close", "comment", "create", "edit", "delete", "reopen", "lock", "unlock", "transfer"},
    "pr": {"create", "comment", "edit", "merge", "close", "reopen", "ready", "review"},
    "release": {"create", "edit", "delete", "upload"},
    "label": {"create", "edit", "delete"},
}

# gh api method flags that signal mutation when present.
GH_API_MUTATING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def _walk_past_gh_globals(argv: list[str], start: int) -> int:
    """Return the index of the first non-global-flag token after `start`."""
    i = start
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok == "--":
            return i + 1
        if not tok.startswith("-"):
            return i
        i += 1
        if "=" not in tok and tok in GH_GLOBAL_FLAGS_WITH_ARG and i < n:
            i += 1
    return i


def is_gh_mutating(argv: list[str]) -> tuple[bool, str]:
    """Detect a mutating gh invocation. Returns (matched, description)."""
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
        return (False, "")

    i = _walk_past_gh_globals(argv, 1)
    if i >= len(argv):
        return (False, "")

    obj = argv[i]

    # gh api ... --method (POST|PATCH|PUT|DELETE)
    if obj == "api":
        method = ""
        j = i + 1
        while j < len(argv):
            tok = argv[j]
            if tok in ("-X", "--method"):
                if j + 1 < len(argv):
                    method = argv[j + 1].upper()
                    break
            elif tok.startswith("--method="):
                method = tok.split("=", 1)[1].upper()
                break
            elif tok.startswith("-X"):
                # `-XPOST` (no space)
                method = tok[2:].upper()
                break
            j += 1
        if method in GH_API_MUTATING_METHODS:
            return (True, f"gh api --method {method}")
        return (False, "")

    # gh <object> <verb> ...
    if obj in GH_MUTATING_VERBS:
        if i + 1 >= len(argv):
            return (False, "")
        verb = argv[i + 1]
        if verb in GH_MUTATING_VERBS[obj]:
            return (True, f"gh {obj} {verb}")

    return (False, "")


def bash_command_is_mutating(command: str) -> tuple[bool, str]:
    """Scan every command segment for a mutating gh invocation."""
    if not command.strip():
        return (False, "")
    tokens = safe_tokenize(command)
    if not tokens:
        return (False, "")
    for argv in iter_command_starts(tokens):
        matched, description = is_gh_mutating(argv)
        if matched:
            return (True, description)
    return (False, "")


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def handle_user_prompt_submit(payload: dict) -> int:
    """Update session state based on the user prompt."""
    prompt = payload.get("prompt") or (payload.get("tool_input") or {}).get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return 0

    path = resolve_state_path(_extract_session_id(payload))
    state = read_state(path)
    changed = False

    # Anchor read-intent on the first prompt only.
    if "read_intent_anchored" not in state:
        marker = detect_read_intent(prompt)
        state["read_intent_anchored"] = bool(marker)
        state["read_intent_marker"] = marker
        state["first_prompt_snippet"] = prompt.strip()[:200]
        changed = True

    # Mutation verb may appear in the same prompt as the read intent (e.g.
    # "review this PR and merge if good"). Recording both flags allows the
    # later mutation tool call to pass silently.
    mverb = detect_mutation_verb(prompt)
    if mverb and not state.get("mutation_verb_seen"):
        state["mutation_verb_seen"] = True
        state["mutation_verb_seen_at"] = mverb
        changed = True

    if changed:
        write_state(path, state)
    return 0


def handle_pre_tool_use(payload: dict) -> int:
    """Inspect mutation-capable tool calls against session intent."""
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command", "") or ""
    if not command.strip():
        return 0

    matched, description = bash_command_is_mutating(command)
    if not matched:
        return 0

    path = resolve_state_path(_extract_session_id(payload))
    state = read_state(path)
    if not state:
        # No anchor yet (first call before any UserPromptSubmit). Silent.
        return 0

    if not state.get("read_intent_anchored"):
        return 0  # session opened with non-read intent — no gate.
    if state.get("mutation_verb_seen"):
        return 0  # user already re-anchored with an explicit mutation verb.

    snippet = state.get("first_prompt_snippet", "")
    marker = state.get("read_intent_marker", "")
    reason = (
        f"Session intent pivot detected. The session opened with read-intent "
        f"(marker: '{marker}') — first utterance: \"{snippet}\". "
        f"No explicit mutation verb (close/merge/push/comment/등록/...) has "
        f"appeared from the user since. The about-to-execute command "
        f"({description}) is a public-surface mutation. "
        f"Re-anchor with the user before proceeding: restate the original "
        f"read-intent and ask explicit confirmation for the mutation pivot."
    )

    mode = os.environ.get("PRAXIS_INTENT_PIVOT_MODE", "").strip().lower()
    decision = "deny" if mode == "block" else "ask"
    emit_decision(decision, reason)
    return 0


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def detect_event(payload: dict) -> str:
    """Resolve the hook event from explicit field or implicit shape."""
    explicit = payload.get("hookEventName") or payload.get("hook_event_name")
    if isinstance(explicit, str) and explicit:
        return explicit
    # Implicit fallback: UserPromptSubmit carries `prompt`, PreToolUse carries
    # `tool_name`. Tests can omit `hookEventName` and still route correctly.
    if "prompt" in payload and "tool_name" not in payload:
        return "UserPromptSubmit"
    if "tool_name" in payload:
        return "PreToolUse"
    return ""


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0  # fail-open on malformed input
    if not isinstance(payload, dict):
        return 0

    event = detect_event(payload)
    if event == "UserPromptSubmit":
        return handle_user_prompt_submit(payload)
    if event == "PreToolUse":
        return handle_pre_tool_use(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
