#!/usr/bin/env python3
"""PreToolUse guard: re-ask before a mutation the user already refused.

Issue #1007. A user rejects an `AskUserQuestion` for an irreversible mutation.
The next user message — a two-word instruction about running two workstreams in
parallel, naming no deletion scope — is read as approval, and the deletion of
~295M objects is launched. A command classifier stopped it; judgement did not.

A rejection is a standing NO for that mutation. Nothing re-reads it before the
next utterance is consumed as consent, so this hook does, at the command
boundary:

  1. the pending tool call names a literal identifier (`s3://…`, `gs://…`, or a
     table named after DROP / TRUNCATE / DELETE FROM) in a destructive position
     — see SURFACES below for what "destructive position" means per surface;
  2. an earlier `AskUserQuestion` in this session was structurally rejected;
  3. the rejected question named the SAME normalized identifier.

All three → `permissionDecision: "ask"`, quoting the rejected question verbatim
and demanding fresh, per-action approval.

DELIBERATE NARROWNESS — three limits, each a decision rather than an oversight:

  REJECTION IS STRUCTURAL ONLY. `_transcript.scan_user_rejections` keys on
  `toolDenialKind == "user-rejected"` plus `is_error: true` plus the runtime's
  fixed refusal sentence. Option-label text is never classified: a gate that
  read "No, stop" as a refusal would be making a natural-language judgement at
  a permission boundary, and DESIGN.md's structural-tokenization rule exists to
  keep exactly that out of hooks. The cost is a real miss — a user who picks an
  "아니오" option without the runtime recording a denial is invisible here.

  OVERLAP IS A LITERAL SHARED IDENTIFIER. No verb-class fallback ("both are
  deletions") and no fuzzy prefix containment: `s3://b/raw/2024` does not match
  `s3://b/raw`. A verb-class rule would fire on every unrelated `rm` after any
  rejection, and an ask that fires on everything is an ask nobody reads.

  THE IDENTIFIER CLASSES ARE A CLOSED LIST. `s3://`, `gs://`, and a table after
  DROP / TRUNCATE / DELETE FROM. A rejected Kubernetes namespace, BigQuery
  dataset, or filesystem path is not matched; each new class needs a
  normalization rule of its own, and guessing normalization is how a
  literal-identifier gate turns into a fuzzy one.

SURFACES (issue #1035). Two, and only condition 1 differs between them:

  BASH — a URI counts only inside a command segment that also carries a
  destructive marker token, so `aws s3 ls s3://b/raw/2024/` stays silent. SQL
  tables carry their verb inside the pattern.

  DISPATCH — an agent/worker dispatch, which is where the originating incident
  actually fired: the deletion scope lived in a WORKER PROMPT, which the Bash
  matcher never sees. Two shapes are read: the `Task` / `Agent` tool's
  `prompt` + `description`, and a Bash segment whose argv[0] is a worker
  launcher (`cmux`, `claude`, `codex`) — there the prompt is nested inside a
  quoted argument, so the segment is scanned as prose.

  On the dispatch surface there is NO destructive-marker requirement, for the
  same reason the rejected-question side has none: a prompt is prose, so argv
  position and marker tokens carry no reliable meaning in it, and the whole
  point of the surface is that intent is hidden in prose. What keeps the ask
  rare is condition 3, which is identical on both surfaces — the identifier has
  to be one the user already refused. The predicate stops at literal substring
  matching of the SAME closed list; no semantic reading of the prompt.

TIER: ask, not deny. The user is the escape hatch — approving the ask IS the
fresh per-action approval the gate demands — so there is no bypass marker and
none is needed.

INDETERMINATE SCAN ASKS TOO (issue #1231). A transcript past the scan's byte
bound answers None, and condition 2 becomes unanswerable rather than false. The
gate asks on that, because the bound is reached only by long sessions and a long
session is where a standing refusal has had time to be forgotten.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    _command_spec_key,
    compound_cascade_hint,
    iter_command_starts,
    iter_command_texts,
    safe_tokenize,
    strip_prefix,
)
from _transcript import scan_cursor_path, scan_user_rejections  # type: ignore[import-not-found]  # noqa: E402

_HOOK_NAME = "rejected-mutation-reconsent-gate"

# ---------------------------------------------------------------------------
# Identifier extraction
# ---------------------------------------------------------------------------

# Object-store URIs. The path part stops at shell/prose punctuation so a URI
# quoted mid-sentence in a question ("delete s3://b/raw/2024/?") normalizes to
# the same value as the same URI on a command line.
_URI_RE = re.compile(
    r"\b(?P<scheme>s3|gs)://(?P<rest>[^\s'\"`,;<>()\[\]{}]+)",
    re.IGNORECASE,
)

# Table names after a destructive SQL verb. The verb is part of the pattern, so
# a table identifier can only be extracted from a destructive context — there is
# no "any word that looks like a table" path.
_SQL_TABLE_RE = re.compile(
    r"\b(?:drop\s+table(?:\s+if\s+exists)?"
    r"|truncate(?:\s+table)?"
    r"|delete\s+from)\s+"
    r"(?P<table>[`\"\[]?[A-Za-z_][A-Za-z0-9_$]*"
    r"(?:[`\"\]]?\.[`\"\[]?[A-Za-z_][A-Za-z0-9_$]*)*[`\"\]]?)",
    re.IGNORECASE,
)

_QUOTE_CHARS = "`\"'[]"
_TRAILING_PUNCT = ".,;:!?)]}>"

# Destructive markers gating URI extraction on the COMMAND side. A URI alone is
# not enough there: `aws s3 ls s3://b/raw/2024/` after a rejected delete of that
# prefix is a read, and asking on it would train the user to click through.
# The rejected QUESTION side has no such requirement — the question is about the
# risky action by construction.
_DESTRUCTIVE_TOKENS = frozenset({
    "rm", "rb", "mv", "delete", "del", "purge", "destroy", "drop", "truncate",
    "--delete", "--purge", "--force-delete", "--recursive-delete",
})


def _normalize_uri(scheme: str, rest: str) -> str:
    """Normalize an object-store URI for literal comparison.

    Scheme and bucket lowercase (both are case-insensitive by rule); the key
    path keeps its case, because object keys are case-sensitive and folding
    them would equate two genuinely different prefixes. A trailing `/` is
    dropped so `s3://b/raw/` and `s3://b/raw` are one identifier.
    """
    rest = rest.strip(_QUOTE_CHARS).rstrip(_TRAILING_PUNCT)
    bucket, sep, key = rest.partition("/")
    key = key.rstrip("/")
    normalized = f"{scheme.lower()}://{bucket.lower()}"
    if sep and key:
        normalized = f"{normalized}/{key}"
    return normalized


def _normalize_table(table: str) -> str:
    """Normalize a SQL table identifier: strip quoting, lowercase.

    Unquoted SQL identifiers are case-insensitive, so folding is correct here
    (unlike an object key). The qualified form is preserved as written —
    `prod.users` and `users` are NOT the same identifier, because treating them
    as one would let a rejection about one schema gate an unrelated one.
    """
    parts = [p.strip(_QUOTE_CHARS) for p in table.split(".")]
    return "table:" + ".".join(p.lower() for p in parts if p)


def _extract_uris(text: str) -> set[str]:
    return {
        _normalize_uri(m.group("scheme"), m.group("rest"))
        for m in _URI_RE.finditer(text)
    }


def _extract_tables(text: str) -> set[str]:
    return {
        _normalize_table(m.group("table"))
        for m in _SQL_TABLE_RE.finditer(text)
    }


def _segment_is_destructive(argv: list[str]) -> bool:
    """True when a command segment carries a destructive marker token."""
    for tok in argv:
        low = tok.lower()
        if low in _DESTRUCTIVE_TOKENS:
            return True
        # `--delete` style flags may arrive glued to a value (`--delete=true`).
        if low.startswith("--") and low.split("=", 1)[0] in _DESTRUCTIVE_TOKENS:
            return True
    return False


def command_identifiers(command: str) -> set[str]:
    """Identifiers the pending command would destroy.

    URIs count only inside a command segment that also carries a destructive
    marker; SQL tables count wherever the destructive-verb pattern matches,
    since the verb is inside the pattern. Segmentation reuses the shared
    `safe_tokenize → iter_command_starts` pipeline so a URI inside a quoted
    string is one token and a compound command is split per segment — an
    `aws s3 ls …` half must not inherit `rm` from the other half of a `&&`.
    """
    identifiers = _extract_tables(command)
    tokens = safe_tokenize(command)
    if not tokens:
        return identifiers
    for raw_argv in iter_command_starts(tokens):
        argv = list(raw_argv)
        if not argv or not _segment_is_destructive(argv):
            continue
        identifiers |= _extract_uris(" ".join(argv))
    return identifiers


def question_identifiers(text: str) -> set[str]:
    """Identifiers named by the rejected question (URIs anywhere + SQL tables)."""
    return _extract_uris(text) | _extract_tables(text)


# ---------------------------------------------------------------------------
# Dispatch surface (issue #1035)
# ---------------------------------------------------------------------------

# Tools whose payload carries a worker prompt. `Task` is Claude Code's agent
# dispatch tool; `Agent` is the same call under the name other hosts use.
DISPATCH_TOOLS = frozenset({"Task", "Agent"})

# Payload keys read from a dispatch tool_input. `prompt` is the worker's
# instructions and is where the incident's deletion scope lived; `description`
# is the short label, included because a scope named only there would otherwise
# be invisible. Nothing else is read — `subagent_type` and `model` are routing
# fields that can never carry a target.
_DISPATCH_TEXT_KEYS = ("prompt", "description")

# Binaries that launch a worker from a Bash command line. The prompt then sits
# inside a quoted argument (`cmux … --command "claude -p '…'"`), so
# `safe_tokenize` makes it one token and the destructive-marker path above never
# sees into it. A closed list, matched on argv[0] only: a URI mentioned in an
# argument of some unrelated command does not become a dispatch.
#
# The four are the providers `cmux-delegate` can actually route to (ARCHITECTURE
# .md → Provider Routing: claude / codex / gemini, dispatched through `cmux`).
# Leaving one out is a silent hole, not a narrower gate — `gemini -p "<prompt>"`
# carries a worker prompt exactly as `claude -p` does.
_DISPATCH_LAUNCHERS = frozenset({"cmux", "claude", "codex", "gemini"})


def dispatch_identifiers(text: str) -> set[str]:
    """Identifiers named anywhere in a worker prompt.

    Same closed list and same normalization as every other surface; what
    differs is that no destructive marker is required. A prompt is prose, so
    argv position and marker tokens carry no reliable meaning in it — and the
    surface exists precisely because the intent is hidden in prose. The
    narrowing is condition 3: the identifier still has to be one the user
    already refused.
    """
    return question_identifiers(text)


def dispatch_text(tool_input: dict) -> str:
    """Flatten a dispatch tool_input's prompt-bearing fields into one string.

    Non-str values are dropped rather than coerced: a list-shaped `prompt` from
    a future payload version must degrade this gate to silence, never crash it
    into a fail-open that looks identical to a clean pass.
    """
    return "\n".join(
        tool_input[key]
        for key in _DISPATCH_TEXT_KEYS
        if isinstance(tool_input.get(key), str)
    )


def _is_launcher(token: str) -> bool:
    """True iff `token` names a worker-launcher binary, however it is written.

    An exact `argv[0] == "cmux"` comparison is bypassed by three shapes that
    all launch the same binary — `/usr/local/bin/cmux`, `(cmux …` inside a
    subshell, and `CMUX_QUIET=1 cmux …` after `strip_prefix` has peeled the
    assignment. `_command_spec_key` is the repo's existing normalization for
    exactly this (see `_is_gh_binary`, issue #1099): strip the shell grouping
    run, then take the path basename.
    """
    return _command_spec_key(token).lower() in _DISPATCH_LAUNCHERS


def bash_dispatch_identifiers(command: str) -> set[str]:
    """Identifiers inside a worker prompt nested in a Bash dispatch command.

    Only segments whose argv[0] is a known launcher are scanned, and they are
    scanned as prose — the segment's own quoting has already been consumed by
    `safe_tokenize`, so what is left is the prompt text as the worker will read
    it.

    Three normalizations stand between a real dispatch and `argv[0]`, and
    skipping any one of them is a silent bypass rather than a narrower gate:

      `iter_command_texts` recurses into ACTIVE `$( … )` / backtick spans,
      because `safe_tokenize` coalesces a substitution run into one token —
      `WS=$(cmux new-workspace …)` otherwise presents `argv[0]` as `WS=$(cmux`
      and the segment is never recognized at all.

      `strip_prefix` peels env assignments and wrapper commands, so
      `env CMUX_QUIET=1 cmux …` and `sudo claude …` do not present `env` /
      `sudo` as the binary.

      `_is_launcher` compares the path basename, so `/usr/local/bin/cmux`
      matches `cmux`.
    """
    identifiers: set[str] = set()
    for text in iter_command_texts(command):
        if not text.strip():
            continue
        tokens = safe_tokenize(text)
        if not tokens:
            continue
        for raw_argv in iter_command_starts(tokens):
            argv = strip_prefix(list(raw_argv))
            if argv and _is_launcher(argv[0]):
                identifiers |= dispatch_identifiers(" ".join(argv))
    return identifiers


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

_QUESTION_EXCERPT_CHARS = 300


def _question_excerpt(rejection: dict) -> str:
    """The rejected question verbatim (first question), bounded.

    Falls back to the flattened input text when the payload does not carry the
    documented `questions[]` shape — a renamed field must degrade the quote,
    never the gate.
    """
    questions = (rejection.get("tool_input") or {}).get("questions")
    if isinstance(questions, list):
        for q in questions:
            if isinstance(q, dict) and isinstance(q.get("question"), str):
                text = q["question"].strip()
                if text:
                    return text[:_QUESTION_EXCERPT_CHARS]
    return " ".join(rejection.get("text", "").split())[:_QUESTION_EXCERPT_CHARS]


# What the ask calls the pending tool call, per surface. The word matters: an
# operator reading "this command" while looking at a `Task` dispatch would go
# hunting for a shell command that does not exist, and the whole point of the
# dispatch surface is that the target is somewhere non-obvious.
SUBJECT_COMMAND = "command"
SUBJECT_DISPATCH = "worker dispatch"


def build_reason(rejection: dict, shared: list[str], subject: str) -> str:
    """Render the ask reason through the shared five-field block format.

    Hand-rolling this text is what `tests/test_block_message.py` forbids for any
    hook not on its legacy allowlist — a new gate has no claim to that carve-out.
    `bypass_env` is None on purpose: the ask itself is the escape hatch, so there
    is no variable to name and `format_block` omits the Bypass line entirely.
    """
    return format_block(
        rule_name="rejected-mutation-reconsent-gate",
        why=(
            f"this {subject} targets something you already refused — shared target(s): "
            + ", ".join(shared)
            + '; you rejected this question earlier in this session: "'
            + _question_excerpt(rejection)
            + '"'
        ),
        correct_path=(
            f"approve here only if you are approving THIS {subject} on THIS target "
            "now. A rejection is a standing NO for that target: a later instruction "
            "that does not name it is not approval — 'proceed', 'continue', or an "
            "unrelated parallel-work instruction all leave the refusal standing."
        ),
        bypass_env=None,
        reference="issue #1007",
    )


def build_unscanned_reason(subject: str) -> str:
    """Render the ask reason for a transcript the scan could not read.

    Condition 2 is unanswerable, not answered `no`. The gate asks anyway
    because the two conditions that DID hold are the expensive ones — a
    destructive verb aimed at a literal identifier — and because the tier is
    ask: the cost of being wrong is one keypress, against launching a mutation
    the user may already have refused.
    """
    return format_block(
        rule_name="rejected-mutation-reconsent-gate",
        why=(
            f"this {subject} names a literal destructive target, and the session "
            "transcript is past the rejection scan's byte bound, so whether you "
            "already refused this target could not be read either way"
        ),
        correct_path=(
            f"approve here only if you are approving THIS {subject} on THIS target "
            "now. The scan is blind, not clear: a refusal made earlier in this "
            "session still stands, and a later 'proceed' or an unrelated "
            "parallel-work instruction is not approval of it."
        ),
        bypass_env=None,
        reference="issue #1231",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def resolve_surface(payload: dict) -> tuple[set[str], str, str]:
    """Return `(pending identifiers, subject label, bash command)` for a payload.

    One entry point for both surfaces, because conditions 2 and 3 are identical
    across them — only the extraction differs. The Bash surface contributes both
    its destructive-segment identifiers AND its nested-worker-prompt ones, so a
    `cmux … --command "claude -p '…'"` is read as the dispatch it is while an
    `aws s3 rm` in the same compound command is still read as a command.

    A non-dispatch, non-Bash tool yields no identifiers, which the caller
    translates to silence.
    """
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    if tool_name in DISPATCH_TOOLS:
        return dispatch_identifiers(dispatch_text(tool_input)), SUBJECT_DISPATCH, ""
    if tool_name != "Bash":
        return set(), SUBJECT_COMMAND, ""
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return set(), SUBJECT_COMMAND, ""
    from_command = command_identifiers(command)
    from_prompt = bash_dispatch_identifiers(command)
    # The subject names what the operator is looking at. When the only
    # identifiers came from a nested worker prompt, calling it "command" would
    # send them hunting through argv for a target that is inside a quoted
    # prompt.
    subject = SUBJECT_COMMAND if from_command else SUBJECT_DISPATCH
    return from_command | from_prompt, subject, command


@fail_open
def main() -> int:
    """Hook entry point: read the payload, match rejections, emit the verdict."""
    payload = read_payload()
    if payload is None:
        return 0  # malformed stdin — fail-open

    # Cheapest discriminator first: no candidate identifier in the tool call
    # means no overlap is possible, and the transcript is never read.
    pending, subject, command = resolve_surface(payload)
    if not pending:
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0

    # Resumable: the cursor (keyed on this hook and the session) lets each
    # call read only the bytes appended since the last one, so the byte
    # bound is a budget per call — a long session is indeterminate for the
    # few calls it takes to catch up, not for the rest of its life.
    rejections = scan_user_rejections(
        transcript_path,
        cursor_path=scan_cursor_path(_HOOK_NAME, payload.get("session_id")),
    )
    if rejections is None:
        # FAIL-CLOSED on an indeterminate scan (issue #1231). This gate's
        # blindness is not evenly distributed: the byte bound is reached by long
        # sessions, and a long session is where a standing refusal has had time
        # to accumulate and be forgotten. Failing open here would leave the gate
        # silent in exactly the sessions it was written for. The reach is narrow
        # — `pending` is already non-empty, so nothing without a literal
        # destructive target ever gets here.
        emit_decision(
            "ask", build_unscanned_reason(subject) + compound_cascade_hint(command)
        )
        return 0

    for rejection in reversed(rejections):
        # Only a rejected APPROVAL QUESTION is a standing NO. A rejected Bash
        # or Edit call is the user declining one attempt, and the retry loop
        # for those is the agent's own problem, not a consent question.
        if rejection.get("tool_name") != "AskUserQuestion":
            continue
        shared = sorted(pending & question_identifiers(rejection.get("text", "")))
        if not shared:
            continue
        emit_decision(
            "ask",
            build_reason(rejection, shared, subject) + compound_cascade_hint(command),
        )
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
