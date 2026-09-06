#!/usr/bin/env python3
"""PostToolUse / PostToolUseFailure hook: advisory on repeated identical failures.

Issue #944 — when a tool keeps failing with the same `(tool_name,
error_signature)` pattern, the second retry should surface an advisory instead of
replaying the same failed action in a blind loop.

Scope
=====

Only the repeated *failure* path is handled:

- Malformed stdin -> fail-open (no output, exit 0)
- Missing `session_id` -> fail-open (no output, exit 0)
- Successful tool calls -> no state update, no output
- First failure for a `(tool_name, signature)` pair -> no output
- Second failure for the same pair in one session -> advisory
- Third+ failure for the same pair -> the advisory REPEATS, carrying the running
  occurrence number (issue #1012)

PostToolUseFailure (issue #1337)
================================

The hook is registered on two events. `PostToolUse` delivers `tool_response`,
and the #1096 section below records why that payload cannot say whether a Bash
command failed: a real Bash `tool_response` carries no exit status. The
harness's `PostToolUseFailure` event exists for exactly that case — it fires
"when a tool that started executing fails" and carries a top-level `error`
string whose first line, for Bash, is `Exit code N` (interleaved output
follows). The event is itself the failure evidence, so no allowlist is applied
to the text: every non-interrupted `PostToolUseFailure` counts, MCP tools
included. Three rules, in order:

- `is_interrupt: true` is NOT a failure of the command — the run was aborted
  before it could fail on its own — so the event is dropped without touching
  state.
- `error` that is not a string is an unknown shape -> fail-open, silent.
- Otherwise `error` is the failure text: it seeds the signature exactly as a
  string `tool_response` does, and the same bare-`Exit code N` rule folds the
  command in as a separate digest.

Both events can fire for one tool call, so each counted failure records its
`tool_use_id` in the state file and a second event carrying an already-recorded
id is dropped before the count moves — the pair is counted once whichever
event arrives first. The id list is bounded (`_RECENT_TOOL_USE_IDS_MAX`) and
ordered, so interleaved parallel calls still dedupe. `PostToolUse` stays
registered for one release of parallel running; its removal is a follow-up.

The emitted `hookEventName` mirrors the incoming event: the harness matches it
against the event it is delivering, so a `PostToolUse` name on a
`PostToolUseFailure` reply would be discarded.

Why 3..N also advise (issue #1012)
==================================

The hook used to fire on the exact `prior_count == 1` boundary, so a session that
kept replaying the same failure got exactly one advisory and then silence — and
that silence is indistinguishable, to the model reading the transcript, from the
loop having been noticed and accepted. The measured failure mode this hook exists
for is precisely the long run (the poll-loop family recurred 6x in one session,
5 of them after the first correction signal was already in-transcript), i.e. the
occurrences the boundary suppressed. The advisory now repeats from the 2nd
occurrence onward and names the count, so the signal gets stronger rather than
vanishing exactly where the loop is worst.

Failure detection
================

`tool_response` reaches this hook in two shapes, and the failing one is a
**string** (issue #1265):

- a plain string — the shape a FAILED call actually arrives in. Censused over
  10,467 unique Bash `toolUseResult` entries in 120 live transcripts: 388 are
  strings, every one with `tool_result.is_error == True`, and not one
  successful Bash call is ever a string. Matched by whitelist, `_is_failed` ->
  `_string_failure_text`: text opening with `Error: `, or the literal
  `User rejected tool use` (a denial carries no prefix). The single
  `Error: `-prefixed non-failure — the harness's oversized-output notice for a
  *successful* call whose result was spilled to a file, emitted with
  `is_error == False` — is excluded by name.

  The `Error: ` prefix is NOT read as failure evidence for an MCP tool. Widening
  the census to all 14,652 `toolUseResult` entries shows MCP is the only class
  that ever delivers a *successful* result as a bare string (25 of 1,152; every
  other tool's successes are dicts or content lists), so on that one channel the
  leading text can be the tool's own rather than the harness's envelope — a tool
  answering `Error: no rows found` on success would fire a false advisory on its
  second return. For an MCP tool only harness-authored strings count: the
  `Error: PreToolUse:`/`Error: PostToolUse:` hook-error envelope and the fixed
  rejection sentence. Measured cost: MCP failures carrying the tool's own error
  text (~50 of 573 observed string failures) no longer advise.
- a dict — the shape a SUCCESSFUL call arrives in, plus synthetic/legacy
  payloads. Failure markers, in order:

- `isError`/`is_error is True`
- `status == "error"`
- a non-empty `error` field
- `interrupted is True`
- `exit` is a non-zero integer (synthetic/legacy payloads; real Bash omits it)
- **non-Bash only**: `error`/`stderr` non-empty (legacy/SDK text-only shape),
  read through the harness-noise filter below

Non-empty `stderr` alone is NOT a failure for Bash (issue #1096)
---------------------------------------------------------------

Real Bash `tool_response` payloads carry no exit status and no error field —
verified as `{stdout, stderr, interrupted, isImage, noOutputExpected}` against
live session transcripts — so the only failure signals actually available for a
Bash call are `interrupted` (a killed/timed-out run) and, if one is ever
present, an explicit `isError`/`is_error`/`status == "error"`/`error` marker.
Crucially, a *successful* command that writes to `stderr` — `git
fetch`/`clone`/`checkout` progress, `curl` progress meters, deprecation
warnings, all exit 0 — is byte-for-byte indistinguishable, by its `stderr`
content, from a genuine failure. #1042 stripped only the one harness cwd-reset
line; any *other* `stderr` content still fell through to the back-compat check
and was mislabelled a failure, so the second identical success-with-stderr call
injected a false "N회째 실패" advisory into context. `_is_failed` therefore no
longer treats `stderr` text alone as failure evidence for Bash — the
back-compat `error`/`stderr` fallback runs for non-Bash tools only.

`stderr` is read through the harness-noise filter below before the non-Bash
back-compat check runs — see issue #1042.

That Bash guard does NOT extend to the string path (issue #1265). It exists
because a *dict* payload's `stderr` cannot separate an exit-0 Bash success from
a failure, and both #1042 and #1096 were exactly that: exit-0 successes
arriving as dicts with non-empty `stderr`. A string payload has no `stderr`
field to be ambiguous about — the harness has already made the call and encoded
it in the text. With both roads closed the hook fired 135,030 times and
recorded `decision: pass` on every one.

Harness noise in `stderr` (issue #1042)
=======================================

The `exit` key this hook was written against is not what real Bash
`tool_response` payloads carry: verified against live session transcripts
(`~/.claude/projects/.../*.jsonl`, `toolUseResult` for a `Bash` tool_use),
the actual shape is `{stdout, stderr, interrupted, isImage,
noOutputExpected}` — no `exit`, no `isError`. Every one of those calls
falls straight to the back-compat `error`/`stderr` check, and this harness
appends `"\nShell cwd was reset to <cwd>"` to `stderr` on **every** Bash
call, success or not (this repo's own cwd-reset-between-calls behavior).
That line is not failure evidence, but the back-compat check could not
tell it apart from a real one:

- Every exit-0 command carrying only that line was counted as a failure
  (68 firings in one real session, the last 5 all on exit-0 commands).
- Once normalized, that line is the same string regardless of the
  command that ran, so unrelated calls collapsed onto one
  `(tool_name, signature)` pair and one constant signature
  (`ede370078f51`, confirmed against six independent real session state
  files, all sharing that exact hash).

`_strip_harness_noise` removes that line (and only that line) from
`stderr` before it is used as failure evidence or signature material.
Genuine content on other lines of the same `stderr` blob survives.

The strip is also gated on `tool_name == "Bash"` — the noise line is a
verified property of this harness's own Bash execution path, not of `stderr`
in general. Without the gate, any other tool (or a hypothetical genuine
failure whose only stderr line happens to match that shape) would have its
real failure text silently deleted and be misread as a success or merged
into an unrelated signature.

Signature derivation
===================

`error_signature` is a normalized version of the failure text used to suppress
retries that differ only by volatile values:

- file/path-like tokens -> `<path>`
- UUID-like identifiers -> `<uuid>`
- long hex hashes -> `<hash>`
- timestamps -> `<ts>`
- numeric IDs (`id=...`, `..._id=...`) -> `<id>`
- extra whitespace -> single spaces

This keeps retries that only changed `/tmp/run-<rand>.log`/timestamps/hash IDs from
being treated as distinct failures.

For a string payload the string itself is the signature material — it is the
only failure evidence there is, which is what keeps two unrelated string
failures on distinct signatures rather than collapsing them onto one pair
(the issue #1042 defect-2 shape). One string shape carries nothing to tell two
failures apart — a bare `Error: Exit code N` with no output under it (6 of 388
observed), byte-identical whatever command died. For that shape only
(`_BARE_EXIT_CODE_RE`), the command from `tool_input` joins the key as a
**separate digest** (`_command_discriminator`), so two unrelated commands no
longer share a pair while the same command failing twice still does.

Signature material is the failure text with one leading `Error: ` removed
(`_signature_material`, issue #1337). The `PostToolUse` string carries the
harness envelope (`Error: Exit code 1\\n...`) and the `PostToolUseFailure`
`error` field carries the same lines without it (`Exit code 1\\n...`); the two
events describe one failure and must land on one pair key, or a session whose
failures alternate between the events never reaches the second occurrence.
The prefix is dropped from the *material* only — the failure decision above
still reads it.

The digest hashes the command as written, stripped of leading/trailing
whitespace only. Internal whitespace is shell-significant — `false\nfalse` is
two commands where `false false` is one, and `test 'a  b' = x` compares a
different string than `test 'a b' = x` — so collapsing it digested distinct
commands to one hash and re-created the very collision the discriminator
prevents. A command re-typed with different inner spacing now gets its own key
and stays silent on its second failure: a missed advisory, not a false one.

The digest is separate because normalization would otherwise eat the
discriminator: appending the command to the signature *text* runs it through
`_normalize_signature`, which turns `cat /tmp/a` and `cat /tmp/b` alike into
`cat <path>` — the collision the discriminator exists to prevent. The normalizer
is not weakened to fix this; the command is simply hashed outside it.

For a dict payload the candidate text is drawn from
`error`/`stderr`/`output`/`stdout` in that order (`_derive_failure_text`); `stderr` goes through the same harness-noise
filter as failure detection (issue #1042) before it is used, so a failure
whose `stderr` carries only the harness's cwd-reset notice falls through to
`output`/`stdout` for its distinguishing text instead of normalizing to the
same constant string as every other call in the session.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from typing import Any

from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _paths import resolve_cache_file  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _state_lock import state_lock  # type: ignore[import-not-found]  # noqa: E402


# `{n}` is the running occurrence number (2, 3, 4, …) — issue #1012 replaced the
# fixed "2회째" wording when the advisory stopped firing only on the 2nd failure.
_ADVISORY_PREFIX_TMPL = (
    "[second-failure-advisory] "
    "Failure #{n} of the same error pattern in this session — retrying "
    "without a root-cause read risks a blind retry loop. "
    "(동일한 오류 패턴으로 세션 내 {n}회째 실패가 감지되었습니다. "
    "원인 분석 없이 즉시 재시도하는 루프가 될 수 있습니다.) "
)

# The advisory starts on the 2nd occurrence of a pair and repeats for every
# occurrence after it (issue #1012).
_ADVISORY_FROM_OCCURRENCE = 2

_STATE_SCHEMA_VERSION = 1
_MAX_SIGNATURE_LEN = 4_096

# The two events this hook is registered on (issue #1337). `hook_event_name`
# is read from the payload; anything other than the failure event — including
# an absent field, the shape every pre-#1337 test payload has — takes the
# `tool_response` path.
_EVENT_POSTTOOLUSE = "PostToolUse"
_EVENT_POSTTOOLUSE_FAILURE = "PostToolUseFailure"

# Bound on the per-session list of counted `tool_use_id`s (issue #1337). A
# PostToolUse and a PostToolUseFailure event for the SAME call must count once;
# keeping only the last id would miss the pair when parallel calls interleave
# (A-post, B-post, A-fail), so a short ordered window is kept instead. Sixteen
# covers far more concurrent calls than the harness runs, at 16 ids of state.
_RECENT_TOOL_USE_IDS_MAX = 16


# String-shaped `tool_response` (issue #1265). A failed tool call reaches this
# hook not as a dict but as a plain string; the two shapes below are the whole
# observed failure surface, censused over 10,467 unique Bash `toolUseResult`
# entries in 120 session transcripts: 388 of them are strings, every one
# carrying `tool_result.is_error == True`, and no successful Bash call is ever a
# string. Matching is a whitelist, not "any string is a failure", so a shape
# that has not been observed as a failure cannot start firing the advisory.
_STRING_FAILURE_PREFIX = "Error: "
_STRING_REJECTION_TEXT = "user rejected tool use"

# The one string that opens with `Error: ` and is NOT a failure: the harness's
# oversized-output notice, emitted with `is_error == False` when a *successful*
# call's result is spilled to a file (27 observations, all on MCP tools). It has
# to be excluded by name, or every large successful result reads as a failure.
_STRING_OVERSIZED_OUTPUT_RE = re.compile(
    r"^Error: result \(.*?\) exceeds maximum allowed tokens", re.IGNORECASE
)

# A string failure that is ONLY the exit-code line, with no command output under
# it — 6 of the 388 observed. Every command that dies this way produces the exact
# same bytes, so unrelated failures would share one signature and the second one
# would advise "the same error pattern twice" about two different commands. That
# is the #1042 defect-2 shape, and this hook must not ship it.
#
# Matched against the signature MATERIAL (`_signature_material`), i.e. after the
# `Error: ` envelope is dropped — so it covers both the PostToolUse string
# (`Error: Exit code 1`) and the PostToolUseFailure `error` field (`Exit code
# 1`, the doc's first-line contract for Bash) with one pattern (issue #1337).
_BARE_EXIT_CODE_RE = re.compile(r"^Exit code \d+$")

# MCP tools are the ONE class whose *successful* results are ever delivered as a
# bare string. Censused over 14,652 `toolUseResult` entries in 120 transcripts:
# Bash successes are dicts (11,792/11,792) and every other built-in tool's are
# too (1,135/1,135), while MCP successes are content lists (1,127) plus 25
# strings — all of them the oversized-output notice. So on the MCP string
# channel a leading `Error: ` can be the *tool's own* text rather than the
# harness's failure envelope, and a tool that answers `Error: no rows found` on
# success would accumulate state and fire a false advisory on its second return.
# The classifier therefore stops trusting tool-authored text there: for an MCP
# tool only strings the HARNESS writes count — the hook-error envelope below and
# the fixed rejection sentence. Every other tool keeps the plain prefix, where no
# successful call can reach this branch at all.
_MCP_TOOL_PREFIX = "mcp__"
_STRING_HOOK_ERROR_RE = re.compile(r"^Error: (?:Pre|Post)ToolUse:")


# Reference candidates inside a blocking message, most explicit first.
_REFERENCE_LABEL_RE = re.compile(r"Reference:\s*([^\s\"'`,;]+)")
_HOOK_PATH_RE = re.compile(r"(?<!\S)(hooks/[^\s\"'`,;]+)")
_SPEC_PATH_RE = re.compile(r"(?<!\S)([^\s\"'`,;]*spec\.md)")

_PATH_RE = re.compile(r"(?<!\S)/[^\s\"'`]+")
_WIN_PATH_RE = re.compile(r"(?<!\S)[A-Za-z]:\\[^\s\"'`]+")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HASH_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b|\b\d{4}-\d{2}-\d{2}\b"
)
_ID_RE = re.compile(r"\b([\w.-]*id)[:=]\s*[\"']?[\w-]+[\"']?", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

# Injected by this harness into every Bash `stderr`, success or not, when it
# resets the shell's cwd between calls — not failure evidence (issue #1042).
_HARNESS_CWD_RESET_RE = re.compile(r"(?im)^[ \t]*Shell cwd was reset to .*$")


def _strip_harness_noise(text: str, tool_name: str) -> str:
    """Remove the harness's own cwd-reset notice from a `stderr` blob.

    Verified against real `Bash` `tool_response` payloads (session
    transcripts): this exact line is present on every call regardless of
    success, and nothing else in those payloads carries an `exit` field for
    the failure check below to consult instead.

    Gated on `tool_name == "Bash"` (issue #1071 review) — that verification
    only covers Bash. Applying the same regex to every tool's `stderr`
    regardless of origin would delete a non-Bash tool's genuine error text if
    it happened to match the same line shape, misreading a real failure as a
    success.
    """
    if tool_name != "Bash":
        return text.strip()
    return _HARNESS_CWD_RESET_RE.sub("", text).strip()


def _extract_session_id(payload: dict[str, Any]) -> str | None:
    sid = payload.get("session_id")
    if isinstance(sid, str):
        sid = sid.strip()
        if sid:
            return sid
    return None


def _extract_tool_name(payload: dict[str, Any]) -> str:
    name = payload.get("tool_name")
    if isinstance(name, str):
        return name.strip()
    return ""


def _extract_tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        return tool_input
    return {}


def _extract_event_name(payload: dict[str, Any]) -> str:
    """`hook_event_name`, defaulting to PostToolUse when absent or malformed.

    Every payload this hook received before issue #1337 carried no event field
    and was a PostToolUse delivery, so the default keeps that path unchanged.
    """
    name = payload.get("hook_event_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return _EVENT_POSTTOOLUSE


def _extract_tool_use_id(payload: dict[str, Any]) -> str:
    tool_use_id = payload.get("tool_use_id")
    if isinstance(tool_use_id, str):
        return tool_use_id.strip()
    return ""


def _failure_event_text(payload: dict[str, Any]) -> str | None:
    """Failure text of a PostToolUseFailure payload, or None when not counted.

    The event fires only for a tool that started executing and failed, so
    arrival is the failure evidence and the text needs no allowlist — the
    MCP scoping of `_string_failure_text` exists because a PostToolUse string
    may be a *successful* tool's own text, which cannot happen here.

    None for two shapes: `is_interrupt: true`, where the harness says the run
    reached it as an abort rather than as the command's own failure (counting
    it would advise on the user's interruptions), and a non-string `error`,
    which is a shape this hook has not seen and fails open on. An empty
    string is a failure with no text and normalizes to `<empty>`, exactly as
    the dict path does.
    """
    if payload.get("is_interrupt") is True:
        return None
    error = payload.get("error")
    if not isinstance(error, str):
        return None
    return error.strip()


def _extract_reference(tool_input: dict[str, Any], failure_text: str = "") -> str:
    """Path the agent should read before retrying.

    The failure text wins over `tool_input`: a gate that blocks names the spec
    holding its decision predicate (`Reference: hooks/.../spec.md`), while
    `tool_input` only names whatever the agent was already touching.
    """
    for pattern in (_REFERENCE_LABEL_RE, _HOOK_PATH_RE, _SPEC_PATH_RE):
        match = pattern.search(failure_text)
        if match:
            return match.group(1).strip()

    for key in ("file_path", "path", "target"):
        candidate = tool_input.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _string_failure_text(tool_response: str, tool_name: str) -> str:
    """Failure text carried by a string-shaped `tool_response`, else `""`.

    Returning the text rather than a bool is what keeps unrelated failures on
    distinct signatures (issue #1042 defect 2): the string *is* the only failure
    evidence the payload carries, so it is both the failure marker and the
    signature material.

    Nothing in the payload marks a string as an error — the harness's `is_error`
    flag lives on the transcript's `tool_result` block, not here — so the
    classifier reads the shape instead. A string reaches this hook from a
    *successful* call in exactly one case per the census above (the
    oversized-output notice, MCP only), and that case is what scopes the match:
    an MCP tool's string may be its own text, so only harness-authored strings
    count there.
    """
    text = tool_response.strip()
    if not text:
        return ""
    # Fixed harness sentence, carrying no prefix — a denial, whatever the tool.
    if text.lower() == _STRING_REJECTION_TEXT:
        return text
    if _STRING_OVERSIZED_OUTPUT_RE.match(text):
        return ""
    if tool_name.startswith(_MCP_TOOL_PREFIX):
        return text if _STRING_HOOK_ERROR_RE.match(text) else ""
    if text.startswith(_STRING_FAILURE_PREFIX):
        return text
    return ""


def _derive_failure_text(tool_response: Any, tool_name: str) -> str:
    if isinstance(tool_response, str):
        return _string_failure_text(tool_response, tool_name)
    if not isinstance(tool_response, dict):
        return ""
    if isinstance(tool_response.get("error"), str):
        error_text = tool_response.get("error", "").strip()
        if error_text:
            return error_text
    if isinstance(tool_response.get("stderr"), str):
        err = _strip_harness_noise(tool_response.get("stderr", ""), tool_name)
        if err:
            return err
    if isinstance(tool_response.get("output"), str):
        out = tool_response.get("output", "").strip()
        if out:
            return out
    if isinstance(tool_response.get("stdout"), str):
        out = tool_response.get("stdout", "").strip()
        if out:
            return out
    return ""


def _derive_error_text(tool_response: Any, tool_name: str) -> str:
    """Error-channel text only.

    Distinct from `_derive_failure_text`, which also reads `output`/`stdout` to
    build a signature: those two carry a *successful* tool's normal output, so
    treating them as evidence of failure classifies every repeated success as a
    repeated failure.
    """
    if not isinstance(tool_response, dict):
        return ""
    error_text = tool_response.get("error")
    if isinstance(error_text, str) and error_text.strip():
        return error_text.strip()
    stderr_text = tool_response.get("stderr")
    if isinstance(stderr_text, str):
        stripped = _strip_harness_noise(stderr_text, tool_name)
        if stripped:
            return stripped
    return ""


def _is_failed(tool_response: Any, tool_name: str) -> bool:
    # String-shaped payload (issue #1265) — decided before, and independently
    # of, the `tool_name == "Bash"` guard at the bottom of this function. That
    # guard exists because a *dict* payload's `stderr` cannot tell an exit-0
    # Bash success from a failure (#1042, #1096), and both of those defects were
    # exit-0 successes arriving as dicts with non-empty `stderr`. A string
    # payload has no `stderr` field to be ambiguous about: the harness has
    # already made the success/failure call and encoded it in the text itself.
    # Extending the Bash guard here would re-close the only road left open —
    # which is exactly the 135,030-fire, zero-advisory silence of #1265.
    if isinstance(tool_response, str):
        return bool(_string_failure_text(tool_response, tool_name))

    if not isinstance(tool_response, dict):
        return False

    # Explicit failure markers — trusted for every tool, Bash included. Real
    # Bash payloads do not carry any of these (issue #1096), but if one ever
    # appears we honour it so a genuinely-marked failure still fires.
    if tool_response.get("isError") is True or tool_response.get("is_error") is True:
        return True

    status = tool_response.get("status")
    if isinstance(status, str) and status.strip().lower() == "error":
        return True

    error_field = tool_response.get("error")
    if isinstance(error_field, str) and error_field.strip():
        return True

    interrupted = tool_response.get("interrupted")
    if interrupted is True:
        return True

    exit_code = tool_response.get("exit")
    if exit_code is not None:
        try:
            return int(exit_code) != 0
        except (TypeError, ValueError):
            pass

    # Back-compat path (issue #944): older/SDK payloads that surfaced a failure
    # only through non-empty `error`/`stderr` text.
    #
    # Issue #1096: NOT applied to Bash. A real Bash `tool_response` carries no
    # exit status and no error field — `{stdout, stderr, interrupted, isImage,
    # noOutputExpected}`, verified against live session transcripts — so a
    # success-with-stderr command (git fetch/clone/checkout progress, curl
    # meters, deprecation warnings; all exit 0) is indistinguishable, by its
    # `stderr` content, from a genuine failure. #1042 stripped only the one
    # harness cwd-reset line; any other `stderr` content still fell through here
    # and was mislabelled a failure, letting a repeated success-with-stderr call
    # inject a false "N회째 실패" advisory. For Bash a failure is now signalled
    # only by the real indicators above (interrupted, or an explicit
    # error/isError/status marker when actually present), never by `stderr`
    # content. The text-only fallback stays for non-Bash tools (issue #1071
    # gate; see case 17).
    if tool_name == "Bash":
        return False
    return bool(_derive_error_text(tool_response, tool_name))


def _normalize_signature(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""

    text = _PATH_RE.sub("<path>", text)
    text = _WIN_PATH_RE.sub("<path>", text)
    text = _UUID_RE.sub("<uuid>", text)
    text = _HASH_RE.sub("<hash>", text)
    text = _TIMESTAMP_RE.sub("<ts>", text)
    text = _ID_RE.sub(r"\1=<id>", text)

    text = _WS_RE.sub(" ", text).strip()
    if len(text) > _MAX_SIGNATURE_LEN:
        text = text[:_MAX_SIGNATURE_LEN]
    return text.lower()


def _command_discriminator(tool_input: dict[str, Any] | None) -> str:
    """Digest of the command text, kept OUT of the normalized signature.

    `_normalize_signature` absorbs paths, ids, hashes and timestamps so that two
    genuinely-equivalent errors match — which is exactly what destroys a command
    used as a discriminator: `cat /tmp/a` and `cat /tmp/b` both become
    `cat <path>`. Weakening the normalizer to save the discriminator would break
    the matching the normalizer exists for, so the command is hashed on its own
    and mixed into the pair key beside the normalized signature instead.

    The command text is hashed as written. Internal whitespace is NOT collapsed:
    in shell it is significant, so `false\nfalse` and `false false` are two
    programs, and `test 'a  b' = x` and `test 'a b' = x` compare different
    strings. Collapsing them digested distinct commands to one hash, which is
    the collision this function exists to prevent. Only leading/trailing
    whitespace is stripped — that is insignificant outside quotes, and it is
    what makes an all-whitespace command behave like an absent one.

    The cost is the reverse direction: a command re-typed with different inner
    spacing now gets its own key, so its second failure stays silent. That is a
    missed advisory, not a false one, and matching those retypes was never worth
    a discriminator that cannot discriminate.

    Case is NOT folded, because `cat A` and `cat a` are different files.
    Truncated to the same bound as a signature.
    """
    command = (tool_input or {}).get("command")
    if not isinstance(command, str):
        return ""
    stripped = command.strip()
    if not stripped:
        return ""
    return hashlib.sha1(stripped[:_MAX_SIGNATURE_LEN].encode("utf-8")).hexdigest()


def _signature_material(text: str) -> str:
    """Failure text with one leading `Error: ` envelope removed (issue #1337).

    The PostToolUse string and the PostToolUseFailure `error` field describe
    the same failure with and without the harness prefix; stripping it here
    lands both on one pair key. Applied to signature material only — the
    failure decision keeps reading the prefix.
    """
    text = text.strip()
    if text.startswith(_STRING_FAILURE_PREFIX):
        text = text[len(_STRING_FAILURE_PREFIX):].strip()
    return text


def _signature_from_text(
    tool_name: str,
    text: str,
    tool_input: dict[str, Any] | None,
    discriminate_bare: bool,
) -> str:
    """Pair signature for `text`, the failure evidence already extracted.

    `discriminate_bare` is True when the text is string-shaped evidence (a
    PostToolUse string, or a PostToolUseFailure `error`): only there can a
    bare `Exit code N` line be the whole text, and only there does the command
    join the key. A dict payload's `error`/`stderr` never took the digest and
    still does not.
    """
    material_text = _signature_material(text)
    normalized = _normalize_signature(material_text)
    if not normalized:
        normalized = "<empty>"

    # The bare exit-code line carries nothing to tell two failures apart, so the
    # command that produced it joins the key as a separate digest. It comes from
    # `tool_input` in the same hook payload — the signature still depends on
    # nothing outside the call being judged. Narrow by design: any failure whose
    # text has real content is already distinguishable and is left untouched, and
    # an absent/empty command leaves the key byte-identical to the old one.
    material = f"{tool_name}\0{normalized}"
    if discriminate_bare and _BARE_EXIT_CODE_RE.match(material_text):
        discriminator = _command_discriminator(tool_input)
        if discriminator:
            material = f"{material}\0{discriminator}"

    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def _compute_signature(
    tool_name: str, tool_response: Any, tool_input: dict[str, Any] | None = None
) -> str:
    """Pair signature for a PostToolUse `tool_response` (string or dict)."""
    text = _derive_failure_text(tool_response, tool_name)
    return _signature_from_text(
        tool_name, text, tool_input, discriminate_bare=isinstance(tool_response, str)
    )


def _state_path(session_id: str) -> str:
    override = os.environ.get("PRAXIS_SECOND_FAILURE_ADVISORY_FILE", "").strip()
    if override:
        return override
    return resolve_cache_file(f"second-failure-advisory-{session_id}.json", session_id=session_id)


def _load_state(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {"schema_version": _STATE_SCHEMA_VERSION, "failures": {}}


def _save_state(path: str, state: dict[str, Any]) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _emit_advisory(
    tool_name: str,
    signature: str,
    reference: str,
    occurrence: int,
    event_name: str = _EVENT_POSTTOOLUSE,
) -> None:
    """Emit the advisory for the `occurrence`-th failure of this pair (>= 2).

    Written as `hookSpecificOutput.additionalContext` (DESIGN.md, PostToolUse
    corrective emissions), mirroring `builtin-task-postuse`. A PostToolUse hook
    that exits 0 has its stderr routed to the debug log, never to the model — so
    the stderr form would leave the retry loop uncorrected, which is the one
    thing this hook exists to do.

    `event_name` is echoed as `hookEventName`: the harness accepts the reply
    only under the event it delivered (issue #1337).
    """
    message = _ADVISORY_PREFIX_TMPL.format(n=occurrence)
    message += f"{tool_name} failure pattern recurring, occurrence #{occurrence}. "
    message += f"signature={signature[:12]}"
    if reference:
        message += (
            f" Reference: {reference}"
            f" — before retrying, Read {reference} and restate its blocking"
            f" predicate in one line"
            f" (재시도 전에 {reference}를 read하고 차단 판정 술어를 한 줄로"
            f" 재진술하세요)."
        )
    else:
        message += (
            " Before retrying, restate the blocking predicate in one line"
            " (재시도 전에 차단 판정 술어를 한 줄로 재진술하세요)."
        )
    json.dump(
        {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": message,
            },
        },
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0

    if not isinstance(payload, dict):
        return 0

    session_id = _extract_session_id(payload)
    if not session_id:
        return 0

    tool_name = _extract_tool_name(payload)
    if not tool_name:
        return 0

    tool_input = _extract_tool_input(payload)
    event_name = _extract_event_name(payload)

    if event_name == _EVENT_POSTTOOLUSE_FAILURE:
        # Issue #1337: the event is the failure evidence; `error` is the text.
        failure_text = _failure_event_text(payload)
        if failure_text is None:
            return 0
        signature = _signature_from_text(
            tool_name, failure_text, tool_input, discriminate_bare=True
        )
    else:
        tool_response = payload.get("tool_response")
        if not _is_failed(tool_response, tool_name):
            return 0
        failure_text = _derive_failure_text(tool_response, tool_name)
        signature = _compute_signature(tool_name, tool_response, tool_input)

    ref = _extract_reference(tool_input, failure_text)
    tool_use_id = _extract_tool_use_id(payload)

    path = _state_path(session_id)
    pair_key = f"{tool_name}\0{signature}"

    # The whole read-modify-write is serialized (issue #951). Two processes that
    # read the same count both write count+1, so without the lock the same
    # occurrence number is reported twice and one increment is lost — the
    # duplicate fire recorded as unverified on #950. Persisting inside the lock
    # is what makes each process observe the other's increment. (Issue #1012
    # widened the fire condition from the `prior_count == 1` boundary to every
    # occurrence >= 2; the lost increment still mis-numbers the advisory, so the
    # lock is still load-bearing.)
    with state_lock(path):
        state = _load_state(path)
        failures = state.get("failures")
        if not isinstance(failures, dict):
            failures = {}
            state["failures"] = failures

        # Issue #1337: PostToolUse and PostToolUseFailure can both describe
        # this one call. The first event to arrive counts it and records the
        # id; the second finds the id and leaves the count — and the model's
        # context — untouched. Decided inside the lock so two events for one
        # call cannot both read "unseen".
        recent_ids = state.get("recent_tool_use_ids")
        if not isinstance(recent_ids, list):
            recent_ids = []
        if tool_use_id and tool_use_id in recent_ids:
            return 0

        prior_count = 0
        count = failures.get(pair_key)
        if isinstance(count, int) and count > 0:
            prior_count = count

        failures[pair_key] = prior_count + 1

        if tool_use_id:
            recent_ids.append(tool_use_id)
            state["recent_tool_use_ids"] = recent_ids[-_RECENT_TOOL_USE_IDS_MAX:]

        # Persist before advising: a lost write would let the same advisory
        # fire again on the next failure of this pair.
        saved = _save_state(path, state)

    if not saved:
        return 0

    occurrence = prior_count + 1
    if occurrence >= _ADVISORY_FROM_OCCURRENCE:
        _emit_advisory(tool_name, signature, ref, occurrence, event_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
