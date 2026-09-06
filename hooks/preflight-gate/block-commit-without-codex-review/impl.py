#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `git commit` when `praxis:codex-review-wrap`
has not been invoked in the current session.

Backs the rule (devseunggwan/ai-dotfiles#93, AGENTS.md `Deliver` table):
`praxis:codex-review-wrap` is a second mandatory independent review pass before
commit. Prose alone is unreliable (prompt-layer retrieval failure); per the
established escalation pattern, this hook enforces the gate at the commit
checkpoint.

The gate keys on an ABSENCE — the required skill invocation is missing from
the transcript — rather than on the presence of a marker, so a commit cannot
pass by avoiding whatever vocabulary a marker scan would look for.

Block conditions (ALL must hold):
  (a) Tool is Bash with a content `git commit` (not amend/merge/rebase/
      cherry-pick/revert). Commits wrapped in a subshell/group (`(git …)`),
      command substitution (`$(git …)` / backtick), or chained behind a
      space-less separator (`true;git commit …`) are detected too.
  (b) NEITHER the session's own transcript NOR any of its subagent
      transcripts (`<session-dir>/subagents/agent-*.jsonl`) contains a
      `Skill` tool_use with input.skill == "praxis:codex-review-wrap", and
      the root transcript has no `/praxis:codex-review-wrap` slash-command
      invocation either.

      Subagent transcripts are scanned because a
      `Skill(praxis:codex-review-wrap)` call made *inside* a Task/Agent-
      dispatched subagent is recorded only in that subagent's own JSONL
      file — the
      root transcript sees just the subagent's final tool_result text, with
      no `isSidechain` entries at all (verified against live
      ~/.claude/projects/<project>/<session_id>*.jsonl files: 0 isSidechain
      lines in the root transcript, full subagent turns only under
      <session_id>/subagents/). A root-only scan is structurally blind to
      review work a subagent actually performed (praxis issue #730,
      surfaced during issue #720). Each Claude Code session (and therefore
      each worktree/ultrawork branch, which runs as its own session) has
      its own transcript file and its own sibling subagents/ directory, so
      this scan never crosses session boundaries.

Capability tiering (issue #1187) — effectively block condition (c): the
deny applies only when the codex
capability is attested — `codex` on PATH, or PRAXIS_CODEX_REVIEW_STRICT=1
pinning it. Otherwise the guidance ships as a stderr advisory and the
commit proceeds (attested-convention principle, #1159).

Allow conditions (escape hatches):
  - The commit -m/--message message contains a [skip-codex-review] token
    (a -F file / heredoc body is not argv-visible — use the persistent env
    bypass there instead, see below)
  - CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1 set as a PERSISTENT environment
    variable (exported before Claude Code starts, or configured via the
    host's session/settings env) — NOT as an inline command prefix. The
    PreToolUse hook runs as a separate process that only inherits the
    Claude Code host's own environment; `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1
    git commit …` places the assignment inside `tool_input.command`, which is
    data the hook inspects, not env applied to the hook process itself — it
    never reaches this process's `os.environ`. Verified empirically during
    issue #720 work.
  - git commit --amend / git merge / git rebase / git cherry-pick / git revert
  - Missing / unreadable / oversized transcript → fail-open (cannot enforce)
  - Malformed / unparseable command (unbalanced quotes) → fail-open

NOT exempt: --allow-empty / --allow-empty-message. They permit an empty commit
or empty message but do NOT prevent staged content from being committed, so
exempting them would let `git commit --allow-empty -m x` (with staged changes)
bypass the gate. An intentional empty CI-trigger commit uses the skip token or
the persistent env bypass instead.

Semantics: a whole-transcript scan means one codex-review-wrap invocation in
the session satisfies all subsequent commits — the same coarse session-level
granularity as the other commit/PR review gates.

Classification is token-level (shlex punctuation_chars), not raw-string, so:
  - `git commit-tree` plumbing does not match (single token `commit-tree` ≠ `commit`)
  - `echo "git commit"` does not trip the gate (no `git`+`commit` adjacency)
  - `(git commit -m x)` grouped form is detected (`(` stripped from `(git` → `git`)
  - `true;git commit -m x` separator-chained form is detected (`;` is a token)
  - `echo $(git commit -m x)` substitution form is detected
  - `echo "$(git commit -m x)"` quoted substitution is detected via span scan
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    TranscriptReadError,
    scan_cursor_path,
    scan_transcript_resumable,
)
from pathlib import Path

_TARGET_SKILL = "praxis:codex-review-wrap"
_MAX_BYTES = 50 * 1024 * 1024

# Fire-ledger identity — must match this hook's manifest `name`/`role`, since the
# dispatcher keys its RICH fire records off the manifest entry (see
# hooks/manifest.json). count_session_fires filters on these exact strings.
_HOOK_NAME = "block-commit-without-codex-review"
_HOOK_ROLE = "preflight-gate"

# Escalate the block message once this gate has ALREADY blocked the same session
# at least this many times before the current block (issue #805). 1 → the 2nd
# block onward carries the escalated wording; the 1st keeps the plain message.
# The block/allow verdict is unchanged either way — escalation only strengthens
# the message (form (a)), never relaxes or tightens the gate, so it adds no
# bypass incentive.
_ESCALATE_AFTER_PRIOR_BLOCKS = 1


@fail_open
def main() -> int:
    """Hook entry point: read the payload, run the gate, emit the verdict."""
    payload = read_payload()
    if payload is None:
        return 0  # fail-open on malformed payload

    if os.environ.get("CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE") == "1":
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    commit_args = _commit_invocation_args(command)
    if commit_args is None:
        return 0  # no real `git commit` invocation (or unparseable) → fail-open

    if _is_exempt(commit_args):
        return 0  # --amend (fix-up of an already-gated commit)

    if _has_skip_token(commit_args):
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0  # no transcript → cannot enforce

    invoked = _scan_transcript(transcript_path, payload.get("session_id"))
    if invoked is None:
        return 0  # unreadable/oversized → fail-open
    if invoked:
        return 0  # codex-review-wrap ran this session → allow

    strict, codex_detected = _capability_tier()
    if not strict:
        _emit_advisory_message(codex_detected)
        return 0

    _emit_block_message(_prior_block_count(payload.get("session_id")))
    return 2


def _prior_block_count(session_id) -> int:
    """Blocks this gate already emitted for `session_id` this session, read from
    the fire ledger (issue #805). 0 when session_id is absent or the ledger is
    unreadable/opted-out — fail-open to the non-escalated message.

    Counts decision=="block" only: a prior pass/advise fire of this hook is not
    a repeated block and must not inflate the escalation counter. The dispatcher
    records the current block AFTER this hook returns, so the in-flight block is
    excluded — this is strictly the count of blocks BEFORE now."""
    if not isinstance(session_id, str) or not session_id:
        return 0
    return _fire_ledger.count_session_fires(_HOOK_NAME, session_id, decision="block")




# ---------------------------------------------------------------------------
# Command classification
#
# Classification operates on shlex tokens, not the raw command string. Matching
# a raw string is unsound for a commit gate: `--amend` inside a -m message would
# falsely exempt a content commit, `git commit-tree` would falsely match `commit`
# via the `\b` boundary, and `echo "git commit"` / `git log --grep="git commit"`
# would falsely trip the gate on a non-commit command.
#
# Ported from block-sciomc-finding-commit (PR #445) to close the same bypass
# surface: grouped (`(git commit …)`), command-substitution (`$(git commit …)`),
# and separator-chained (`true;git commit …`) forms that the prior shlex.split
# tokenizer missed. Only the "is this a real git commit" detection is shared;
# the block/pass decision logic and bypass tokens remain TARGET-specific.
# ---------------------------------------------------------------------------

_SKIP_TOKEN_RE = re.compile(r"\[skip-codex-review\]", re.IGNORECASE)
_SHELL_SEPARATORS = {";", "&", "&&", "|", "||", "\n"}
_GLOBAL_OPTS_WITH_VALUE = {"-c", "-C", "--git-dir", "--work-tree", "--namespace"}
# Terminal global options consume the rest as non-execution: `git --help commit`
# and `git --version commit` print help/version, they do NOT run commit.
_TERMINAL_GLOBAL_OPTS = {"--help", "-h", "--version"}
# Only `--amend` is exempt: its intent is to fix up a prior (already-gated)
# commit. `--allow-empty` / `--allow-empty-message` are deliberately NOT exempt
# — they only *permit* an empty commit / message and do not *prevent* staged
# content from riding along, so exempting them would let
# `git commit --allow-empty -m x` (with staged changes) bypass the gate.
# Verified functionally: both flags commit staged files.
_EXEMPT_FLAGS = {"--amend"}
_MESSAGE_FLAGS = {"-m", "--message"}


def _tokenize(command: str) -> list[str] | None:
    # punctuation_chars=True splits shell operators (`;`, `&&`, `|`, `(`, `)`,
    # redirects) into their own tokens even when not space-delimited, while
    # quotes still protect their contents. Plain shlex.split folds
    # `true;git commit` into a single `true;git` token, letting a content commit
    # ride behind a space-less separator — a bypass the prior raw regex caught.
    # whitespace_split=True keeps normal word splitting; commenters='' preserves
    # the old comments=False behaviour (a bare `#` is not a comment delimiter).
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        return list(lex)
    except ValueError:
        return None  # unbalanced quotes → unparseable → caller fail-opens


# shlex (posix) does not treat shell grouping / command-substitution syntax as
# operators, so `(git commit …)` and `echo $(git commit …)` tokenize the binary
# as `(git` / `$(git` / `` `git ``. Without normalizing these, the parser would
# miss a real content commit wrapped in a subshell/group — a bypass the prior
# raw-regex (`\bgit\s+commit\b`) happened to catch. Strip a leading run of
# grouping/substitution chars before the binary-name check.
_GROUP_PREFIX_CHARS = "(){}$`"


def _is_git_binary(token: str) -> bool:
    stripped = token.lstrip(_GROUP_PREFIX_CHARS)
    return stripped == "git" or stripped.endswith("/git")


# Command-substitution spans. Bash executes the inner command even when the
# substitution sits inside double quotes — where shlex keeps the whole quoted
# string as a single token — so a content commit can hide in `echo "$(git
# commit …)"` or `x="$(git …)"`. A regex cannot delimit `$(…)` reliably: nested
# `$(a $(b))` and escaped `\)` (a literal paren passed to the inner command, not
# a closer) break naive `\$\(.*?\)` matching. We scan with a paren-depth counter
# that skips backslash-escaped chars, then re-tokenize each extracted span.


def _extract_substitutions(command: str) -> list[str]:
    # Quote-aware scan. Bash runs `$(…)` / backtick substitution when unquoted
    # or inside DOUBLE quotes, but NOT inside SINGLE quotes (`echo '$(git …)'`
    # is a literal string, not an executed commit). We track quote state so the
    # scanner mirrors what bash actually executes — extracting from single-quoted
    # text would false-block, scanning raw across quotes would mis-handle an
    # apostrophe inside double quotes (`"it's $(git …)"`). A paren-depth counter
    # that skips backslash-escaped chars handles nested `$( $( ) )` and `\)`.
    spans: list[str] = []
    i = 0
    n = len(command)
    in_squote = False
    in_dquote = False
    while i < n:
        c = command[i]
        if in_squote:
            if c == "'":
                in_squote = False
            i += 1
            continue
        if c == "\\":
            i += 2  # escaped char is literal, never opens a substitution
            continue
        if c == "'" and not in_dquote:
            in_squote = True
            i += 1
            continue
        if c == '"':
            in_dquote = not in_dquote
            i += 1
            continue
        if c == "`":  # backtick substitution (active unquoted and in double quotes)
            j = i + 1
            while j < n and command[j] != "`":
                j += 2 if command[j] == "\\" else 1
            if j >= n:
                break
            spans.append(command[i + 1:j])
            i = j + 1
            continue
        if c == "$" and i + 1 < n and command[i + 1] == "(":
            depth = 1
            j = i + 2
            start = j
            while j < n and depth:
                ch = command[j]
                if ch == "\\":  # escaped char (e.g. `\)`) does not close the span
                    j += 2
                    continue
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            spans.append(command[start:j])
            i = j + 1
            continue
        i += 1
    return spans


def _scan_tokens_for_commit(tokens: list[str] | None) -> list[str] | None:
    """Walk shlex tokens and return the arg tokens of the first real
    `git commit` invocation (from the `commit` token up to the next shell
    separator or next `git`), or None. `git commit-tree` (single token
    `commit-tree` ≠ `commit`) and non-content subcommands return None."""
    if tokens is None:
        return None
    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]
        if _is_git_binary(tok):
            j = i + 1
            while j < n:
                t = tokens[j]
                if t in _TERMINAL_GLOBAL_OPTS:
                    break  # `git --help commit` / `git --version commit` run no commit
                if t in _GLOBAL_OPTS_WITH_VALUE:
                    j += 2  # skip option + its value
                    continue
                if t.startswith("-"):
                    j += 1
                    continue
                break
            if j < n and tokens[j] == "commit":
                args: list[str] = []
                k = j
                while k < n:
                    t = tokens[k]
                    if t in _SHELL_SEPARATORS:
                        break
                    if k > j and _is_git_binary(t):
                        break
                    args.append(t)
                    k += 1
                return args
            i = j
            continue
        i += 1
    return None


def _commit_invocation_args(command: str) -> list[str] | None:
    """Return the argument tokens of the first real `git commit` invocation, or
    None if the command runs no `git commit`.

    Hybrid detection: (1) direct shlex tokenization handles plain, grouped
    (`(git …)`), unquoted-substitution (`$(git …)`), and separator-chained
    forms; (2) a command-substitution span scan handles QUOTED substitution
    (`echo "$(git commit …)"`, `x="$(git …)"`) that shlex folds into one token.
    A plain quoted literal (`echo "git commit"`, `--grep="git commit"`) has no
    `$(…)`/backtick span and no `git`+`commit` token adjacency, so it is
    correctly ignored — the precision the token approach was introduced for.
    """
    args = _scan_tokens_for_commit(_tokenize(command))
    if args is not None:
        return args
    for inner in _extract_substitutions(command):
        args = _scan_tokens_for_commit(_tokenize(inner))
        if args is not None:
            return args
    return None


def _is_separate_message_flag(arg: str) -> bool:
    """True if `arg` is a message flag whose value is the NEXT token: `-m`,
    `--message`, or a clustered short option ending in -m with no attached
    value (`-am`). Inline forms (`-mMSG`, `--message=MSG`, `-ammsg`) carry the
    value in the token itself and return False (no separate value token)."""
    if arg in _MESSAGE_FLAGS:
        return True
    return (
        arg.startswith("-")
        and not arg.startswith("--")
        and len(arg) > 2
        and arg[1] != "m"                     # `-m`/`-mMSG` handled separately
        and "m" in arg
        and arg[1:arg.index("m")].isalpha()
        and not arg[arg.index("m") + 1:]      # no attached value → next token is value
    )


def _is_exempt(commit_args: list[str]) -> bool:
    # Exempt flags must be standalone flag tokens. A `-m`/`--message`/`-am`
    # VALUE that happens to equal "--amend" (`git commit -m --amend`) is the
    # message text, not the amend flag — skip the consumed value token before
    # matching, or it would false-exempt a content commit.
    i = 0
    n = len(commit_args)
    while i < n:
        arg = commit_args[i]
        if _is_separate_message_flag(arg):
            i += 2  # skip the flag and its separate value token
            continue
        if arg in _EXEMPT_FLAGS:
            return True
        i += 1
    return False


def _message_values(commit_args: list[str]) -> list[str]:
    """Extract -m / --message values (separate, joined `-mMSG`, clustered
    `-am`/`-ammsg`, and `--message=MSG` forms). -F/--file values are file
    paths, not the message text, so they are not inspected."""
    values: list[str] = []
    i = 0
    n = len(commit_args)
    while i < n:
        arg = commit_args[i]
        if _is_separate_message_flag(arg):
            if i + 1 < n:
                values.append(commit_args[i + 1])
            i += 2
            continue
        if arg.startswith("--message="):
            values.append(arg[len("--message="):])
        elif arg.startswith("-m") and len(arg) > 2:
            values.append(arg[2:])
        elif (
            arg.startswith("-")
            and not arg.startswith("--")
            and len(arg) > 2
            and arg[1] != "m"
            and "m" in arg
            and arg[1:arg.index("m")].isalpha()
        ):
            # inline clustered value: `-ammsg` → "msg"
            values.append(arg[arg.index("m") + 1:])
        i += 1
    return values


def _has_skip_token(commit_args: list[str]) -> bool:
    # The documented escape hatch is a token in the commit message — scope the
    # check to -m/--message values so a token elsewhere in a compound command
    # (`git commit -m x; echo '[skip-codex-review]'`) does not bypass the gate.
    return any(_SKIP_TOKEN_RE.search(value) for value in _message_values(commit_args))


# ---------------------------------------------------------------------------
# Transcript scan
# ---------------------------------------------------------------------------

# The namespace prefix is optional: a slash command may be typed as either
# `/praxis:codex-review-wrap` or `/codex-review-wrap`. Accepting both is the
# permissive (fewer false blocks) choice for this secondary detection channel —
# the primary `_has_skill_tool_use` path still requires the exact qualified
# skill name. Line-anchored so a prose mention ("run /praxis:codex-review-wrap?")
# does not match.
_SLASH_RE = re.compile(r"^\s*/(?:praxis:)?codex-review-wrap(?:\s.*)?$")
_CMDNAME_RE = re.compile(r"^\s*<command-name>/?(?:praxis:)?codex-review-wrap(?:\s|</|$)")


# Every record that can satisfy the gate carries this literal: the Skill
# tool_use's `skill` value (`praxis:codex-review-wrap`) and both slash-command
# spellings the regexes above accept. A bare literal rather than
# `json_needle` because the slash forms are text inside a message, not a JSON
# string value on their own. JSON escaping cannot hide it — plain ASCII with
# no character `json.dumps` rewrites — so a line without it is rejected before
# any parse, in C, at memchr speed.
_SKILL_NEEDLE = b"codex-review-wrap"


def _transcript_invokes_skill(
    path: str, *, check_slash: bool, cursor_path: str | None = None
) -> bool | None:
    """True if `path` carries a genuine codex-review-wrap invocation, False if
    it was read to the end and does not, None when the file is missing or
    unreadable or the scan has not caught up with it yet (the caller decides
    how to treat None per call site).

    Folds through `_transcript.scan_transcript_resumable`, parsing only the
    lines that contain `_SKILL_NEEDLE` (issue #1277). The previous shape —
    `read_text().splitlines()` into a list, then `json.loads` on every line —
    cost 490 ms and ~70 MB of RSS per `git commit` on a 36 MB session, inside
    the Bash dispatch group's shared deadline (#1167); the same scan with the
    prefilter is 41 ms at constant memory. With `cursor_path` (one per
    transcript file and session, see `_scan_transcript`) each call reads only
    the bytes appended since the last one, and `_MAX_BYTES` is a budget per
    call rather than a ceiling on the session: a transcript past it answers
    None for the few commits it takes to catch up, then costs its delta, and
    once the invocation is on record the file body is not read at all. The
    reader owns the budget (counted on bytes actually read, capped per read),
    the cursor and the fail-open classes; this function owns only what a
    satisfying record looks like.

    `check_slash` scopes the `/praxis:codex-review-wrap` slash-command check
    to the root transcript only (subagent scans pass `check_slash=False`): a
    slash command is a literal *human* keystroke, and a subagent's "user"
    turns are Task-dispatch prompts / tool_results, never human input — the
    slash channel cannot fire there in genuine operation, so checking it
    would only be a phantom code path a fabricated transcript could abuse.
    """

    try:
        found, complete = _scan_for_invocation(
            path, check_slash=check_slash, cursor_path=cursor_path
        )
    except TranscriptReadError:  # missing, not a file, or unreadable
        return None
    if found:
        return True
    return False if complete else None  # None: budget spent before EOF


def _scan_for_invocation(
    path: str, *, check_slash: bool, cursor_path: str | None = None
) -> tuple[bool, bool]:
    """`(found, complete)` for one transcript file: whether an invocation is
    on record (from this call or the cursor) and whether the file was read
    to EOF within this call's budget. Raises `TranscriptReadError` for a
    missing or unreadable file so the caller can tell that apart from a scan
    that merely has not caught up yet."""

    def fold(state: dict, obj: dict) -> None:
        """Mark the state found when `obj` is a genuine invocation record."""
        # A `Skill` tool_use is a genuine invocation wherever it appears —
        # only the assistant can emit one, and emitting it *is* running
        # the skill. A slash command, by contrast, is user-initiated: an
        # assistant message that merely prints "/praxis:codex-review-wrap"
        # on a line is a suggestion, not an invocation. Scope slash
        # detection to user entries (the real invocation is recorded as a
        # user message with a <command-name> marker) so an assistant
        # suggestion cannot satisfy it.
        if _has_skill_tool_use(obj):
            state["found"] = True
        elif check_slash and obj.get("type") == "user" and _has_slash_command(obj):
            state["found"] = True

    state, complete = scan_transcript_resumable(
        path,
        cursor_path,
        lambda: {"found": False},
        fold,
        needle=_SKILL_NEEDLE,
        max_bytes=_MAX_BYTES,
        stop_when=lambda s: s.get("found") is True,
    )
    return state.get("found") is True, complete


def _subagents_dir(transcript_path: str) -> Path | None:
    """Return `<session-dir>/subagents/` for a root transcript path, if it
    exists on disk.

    Claude Code lays subagent transcripts out as a sibling directory named
    after the session: root transcript `<project-dir>/<session_id>.jsonl`,
    subagent transcripts `<project-dir>/<session_id>/subagents/agent-*.jsonl`
    (one file per Task/Agent-dispatched subagent, live-verified against
    `~/.claude/projects/<project>/<session_id>/subagents/`). Each session
    (and therefore each worktree/ultrawork branch, which runs as its own
    Claude Code session) has its own transcript and its own sibling
    subagents/ directory, so this never reaches across sessions.

    Returns None (not an error — just "nothing to scan") when the transcript
    path has no `.jsonl` suffix or the sibling directory does not exist,
    e.g. when the hook fires for a session that spawned no subagents yet.
    """
    p = Path(transcript_path)
    if p.suffix != ".jsonl":
        return None
    candidate = p.with_suffix("") / "subagents"
    return candidate if candidate.is_dir() else None


def _scan_transcript(path: str, session_id=None) -> bool | None:
    """Return True if codex-review-wrap was invoked in the session's own
    transcript OR any of its subagent transcripts, False if not invoked
    anywhere, None if the root transcript cannot be read or its scan has
    not caught up yet (caller treats None as fail-open).

    Each file gets its own cursor, keyed on `session_id` and the file
    (`root`, or the subagent file's stem) through `scan_cursor_path`; with no
    session id the scan runs without persistence, under the same per-call
    budget. A subagent transcript that is individually unreadable is skipped
    (that one subagent's history just cannot contribute a PASS) — it does
    not turn the overall result into a fail-open None, since the root
    transcript already answered the question for everything outside that
    one subagent run. A subagent transcript the scan has *not caught up
    with* is different: its unread tail may hold the invocation, so unless
    a later subagent confirms one the answer is None, not False — the same
    treatment the root gets, for the few commits the catch-up takes.
    """
    root = _transcript_invokes_skill(
        path, check_slash=True, cursor_path=scan_cursor_path(_HOOK_NAME, session_id, "root")
    )
    if root is None:
        return None  # missing/unreadable/not-caught-up root → cannot enforce
    if root:
        return True

    pending = False
    subagents_dir = _subagents_dir(path)
    if subagents_dir is not None:
        # check_slash=False: a subagent transcript's "user" turns are
        # Task-dispatch prompts / tool_results, never human keystrokes, so
        # slash-command detection is root-only (see _transcript_invokes_skill).
        for agent_file in sorted(subagents_dir.glob("agent-*.jsonl")):
            try:
                found, complete = _scan_for_invocation(
                    str(agent_file),
                    check_slash=False,
                    cursor_path=scan_cursor_path(_HOOK_NAME, session_id, agent_file.stem),
                )
            except TranscriptReadError:
                continue  # unreadable: this one subagent cannot contribute
            if found:
                return True
            if not complete:
                pending = True  # its unread tail may still hold the invocation
    return None if pending else False


def _has_skill_tool_use(obj: dict) -> bool:
    message = obj.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    for item in content:
        if (
            isinstance(item, dict)
            and item.get("type") == "tool_use"
            and item.get("name") == "Skill"
        ):
            tool_input = item.get("input")
            if isinstance(tool_input, dict) and tool_input.get("skill") == _TARGET_SKILL:
                return True
    return False


def _has_slash_command(obj: dict) -> bool:
    message = obj.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
    for text in texts:
        for ln in text.splitlines():
            if _SLASH_RE.match(ln) or _CMDNAME_RE.match(ln):
                return True
    return False


def _escalation_banner(prior_blocks: int) -> list[str]:
    """Form-(a) escalation preface (issue #805): shown from the 2nd same-session
    block onward. It does NOT repeat the base message louder — it names the exact
    anti-pattern the repeat signals (changing HOW the commit is invoked instead
    of running the review), which is the observed failure mode a plain re-block
    does not address. The gate verdict is unchanged; only the wording escalates.
    """
    times = "time" if prior_blocks == 1 else "times"
    return [
        f"ESCALATION: this gate has already blocked `git commit` {prior_blocks} {times} "
        "in this session.",
        "Changing HOW you invoke the commit (heredoc, -F file, subshell, a",
        "different separator) does NOT change the gate condition — it re-checks the",
        "same session for a codex-review-wrap pass and blocks again. The only",
        "resolutions are the three below. Run option 1 now (or option 2/3 only if",
        "this commit genuinely qualifies for a skip).",
        "",
    ]


def _capability_tier() -> tuple[bool, bool]:
    """Attested-convention capability tier (issue #1187, principle #1159).

    Returns (strict, codex_detected) — codex_detected feeds the advisory
    wording so an explicit STRICT=0 demote with codex present does not
    falsely claim the CLI is missing.

    The review this gate demands runs through the openai-codex CLI, so the
    codex binary on PATH is the detectable local attestation of the
    convention. `PRAXIS_CODEX_REVIEW_STRICT=1` pins the deny regardless of
    detection (for setups that route the review without a PATH-visible
    binary), and "0" forces advisory even when codex is detected — the
    explicit-override-wins contract shared by the sibling tiering slices.
    Detection is deterministic (`shutil.which`) in its return values, so
    there is no ambiguous-failure state to mis-read as demotion: absent
    means absent. (A pathological PATH entry — dead NFS mount — can hang
    the probe; the hook's 5s manifest timeout bounds that, failing open,
    the same envelope the transcript read already had.)
    A PATH stripped of codex demotes the gate — accepted, because the same
    actor already holds the CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE escape,
    and the strict env exists precisely to pin the deny.
    """
    raw = os.environ.get("PRAXIS_CODEX_REVIEW_STRICT", "").strip()
    if raw and raw != "0":
        # Explicit pin: skip the PATH probe entirely — the pinned setup's
        # whole point is "deny regardless of detection", so it must not
        # inherit the (documented) pathological-PATH hang exposure.
        return True, True
    detected = shutil.which("codex") is not None
    if raw:
        # raw == "0": explicit demote wins even with codex detected — the
        # sibling explicit-override-wins contract; detection still feeds
        # the advisory wording.
        return False, detected
    return detected, detected


def _emit_advisory_message(codex_detected: bool) -> None:
    """Demoted emission when the capability tier is advisory (issue #1187):
    a short advisory summarizing the gate and naming both escalation
    routes (the block message's resolution options assume a runnable
    review, so they are not repeated), exit 0. Two variants: undetected
    (install/pin guidance) and detected-but-demoted via STRICT=0 (unset
    guidance) — the latter must not falsely claim the CLI is missing. No escalation banner:
    the escalation counter tracks repeated *denies*, and an advisory is not
    one.
    """
    if codex_detected:
        lines = [
            "[advisory] codex review gate demoted by PRAXIS_CODEX_REVIEW_STRICT=0",
            "— `git commit` will proceed. codex IS on PATH; unset",
            "PRAXIS_CODEX_REVIEW_STRICT (or set it to 1) to restore the deny",
            "(issue #1187).",
        ]
    else:
        lines = [
            "[advisory] codex capability not detected — `git commit` will proceed.",
            "This gate denies commits made without a `praxis:codex-review-wrap`",
            "pass, but the codex CLI is not on PATH, so the review cannot run",
            "here. Install the openai-codex CLI to restore the deny, or set",
            "PRAXIS_CODEX_REVIEW_STRICT=1 to pin the deny regardless of",
            "detection (issue #1187).",
        ]
    sys.stderr.write("\n".join(lines) + "\n")


def _emit_block_message(prior_blocks: int = 0) -> None:
    lines: list[str] = []
    if prior_blocks >= _ESCALATE_AFTER_PRIOR_BLOCKS:
        lines.extend(_escalation_banner(prior_blocks))
    lines.extend(
        [
            "BLOCKED: `git commit` without a `praxis:codex-review-wrap` review pass this session.",
            "",
            "Rule (AGENTS.md Deliver table): codex-review-wrap is a second mandatory",
            "independent review pass before commit — an independent Codex pass after",
            "omc:code-reviewer that catches defects a single reviewer misses.",
            "",
            "Resolve by one of:",
            "  1. Run the review (it is a model-invocable skill, not an agent):",
            "       Skill(skill=\"praxis:codex-review-wrap\")",
            "     then re-run the commit (one run satisfies all commits this session,",
            "     including one run inside a subagent this session dispatched).",
            "  2. Skip for this commit: add a [skip-codex-review] token to the",
            "     commit message (e.g. trivial docs/typo change).",
            "  3. Persistent bypass: set CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1 in your",
            "     environment BEFORE starting Claude Code (settings env / shell export).",
            "     An inline prefix on this command does NOT work — the hook process",
            "     never sees it.",
            "",
            "Fail-open: missing/unreadable transcript and non-content commits",
            "(--amend / merge / rebase / cherry-pick / revert) pass.",
        ]
    )
    sys.stderr.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
