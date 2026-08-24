#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `git commit` after sciomc/reviewer finding
without user-design consensus re-fetch.

Backs the rule: user-stated design (PR body / issue body / direct utterance)
is RATIFIED — AI analysis findings (sciomc structured output, reviewer
analysis) are DRAFTS. Surface findings to the user, never auto-flip the
design via direct commit.

Background:
  Four converging memory rules encode the same root cause family
  (falsify-before-lock, self-authored option scope, recommendation lock,
  consensus re-fetch before lock). Two CLAUDE.md promotions already done
  (Output-Block-Level Falsification Gate / Self-Falsify Before Recommendation
  Lock). Per prompt-layer retrieval failure threshold, 3+ generations require
  hook escalation, not another memo. This hook enforces the gate at the
  commit checkpoint.

Concrete retrospect pattern (praxis issue #374):
  1. User's PR body specifies a literal value or sibling-convention design
     choice — the ratified design, written on a shared surface
  2. A subsequent sciomc Stage N or reviewer analysis surfaces a finding
     that the user's literal/design is "sibling-deviant" or sub-optimal
  3. AI auto-commits a flip of the user's literal without re-reading the
     PR body or asking for approval — treating its own analysis output
     as a ratified directive
  4. User redirects back to the originally-stated design → revert
  5. Cost: extra commits, PR body rewrite, reviewer-timeline noise,
     occasionally a duplicate follow-up issue spawned from the same
     analysis-as-directive pipeline

Block conditions (ALL must hold):
  (a) Tool is Bash with a content `git commit` (not amend/merge/rebase/
      cherry-pick/revert)
  (b) Recent ASSISTANT-AUTHORED transcript content (last ~200 entries:
      assistant message text + Agent/Task subagent tool-results — NOT user
      turns, system-reminder blocks, or Read/Skill tool-results that merely
      *load* documentation such as a SKILL.md) contains a sciomc structured
      output marker: "[FINDING:", "[STAGE_COMPLETE:", "[CONFLICTS:",
      "sibling-deviant", "의미 mismatch", "의미 충돌". Scanning is role-aware
      (JSONL parsed per-entry) so a SKILL.md that *documents* these tokens as
      literal example text does not self-trip the gate (praxis issue #573).
  (c) No `gh pr view ... --json body` OR `gh issue view ... --json body` OR
      explicit ratification token was emitted AFTER the most recent finding
      marker in the transcript tail

Allow conditions (escape hatches):
  - Commit message (-m / --message value) contains [user-approved] or
    [ratified-by-user] token (scoped to message value only)
  - CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 env var
  - git commit --amend / git merge / git rebase / git revert / git cherry-pick

NOT exempt: --allow-empty / --allow-empty-message. These flags do NOT prevent
staged content from being committed — `git commit --allow-empty -m x` with a
staged file commits that file. Exempting them would let staged changes bypass
the gate. An intentional empty CI-trigger commit uses the [user-approved] token
or the env bypass instead.

Classification is token-level (shlex), not raw-string, so:
  - `git commit-tree` plumbing does not match (tokenizes as one token)
  - `echo "git commit"` does not trip the gate (no `git`+`commit` adjacency)
  - `--amend` inside a `-m` message is a value token, not a flag
  - `[user-approved]` outside the message value (e.g. after &&) does not bypass
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _transcript import load_transcript_objs  # type: ignore[import-not-found]  # noqa: E402


@fail_open
def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0  # fail-open on malformed payload

    if os.environ.get("CLAUDE_HOOK_BYPASS_SCIOMC_GATE") == "1":
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    commit_args = _commit_invocation_args(command)
    if commit_args is None:
        return 0  # no real `git commit` invocation (or unparseable) → fail-open

    if _is_exempt(commit_args):
        return 0  # --amend (fix-up of an already-gated commit)

    if _has_ratification_token(commit_args):
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0  # no transcript → cannot enforce

    scan = _build_finding_scan(transcript_path, max_entries=200, max_bytes=50 * 1024 * 1024)
    if scan is None:
        return 0
    stream, finding_offsets, matched_markers = scan

    if not finding_offsets:
        return 0  # no finding context in assistant-authored content → allow

    last_finding_idx = max(finding_offsets)
    after_finding = stream[last_finding_idx:]

    if _has_consensus_refetch(after_finding):
        return 0

    matched = sorted(set(matched_markers))[:3]
    _emit_block_message(matched)
    return 2




# ---------------------------------------------------------------------------
# Command classification
#
# Classification operates on shlex tokens, not the raw command string.
# Matching a raw string is unsound for a commit gate:
#   - `--amend` inside a -m message would falsely exempt a content commit
#   - `git commit-tree` tokenizes as one token "commit-tree" (≠ "commit")
#     so plumbing subcommands do not match
#   - `echo "git commit"` has no `git`+`commit` token adjacency → no match
#   - `[user-approved]` outside the -m value (e.g. after &&) does not bypass
#
# Only `--amend` is exempt: its intent is to fix up a prior (already-gated)
# commit. `--allow-empty` / `--allow-empty-message` are deliberately NOT exempt
# — they only *permit* an empty commit / message and do not *prevent* staged
# content from riding along, so exempting them would let
# `git commit --allow-empty -m x` (with staged changes) bypass the gate.
# Verified functionally: both flags commit staged files.
# ---------------------------------------------------------------------------

_SHELL_SEPARATORS = {";", "&", "&&", "|", "||", "\n"}
_GLOBAL_OPTS_WITH_VALUE = {"-c", "-C", "--git-dir", "--work-tree", "--namespace"}
# Terminal global options consume the rest as non-execution: `git --help commit`
# and `git --version commit` print help/version, they do NOT run commit.
_TERMINAL_GLOBAL_OPTS = {"--help", "-h", "--version"}
_EXEMPT_FLAGS = {"--amend"}
_MESSAGE_FLAGS = {"-m", "--message"}

_RATIFICATION_TOKEN_RE = re.compile(
    r"\[(?:user-approved|ratified-by-user|user-ratified)\]",
    re.IGNORECASE,
)

_FINDING_MARKERS = (
    # Structured strong tokens — sciomc emits these in its output schema.
    # `[FINDING:` / `[STAGE_COMPLETE:` / `[CONFLICTS:` are bracket-delimited
    # tokens with a colon; only sciomc output contains them, so false-positive
    # risk is minimal and no word-boundary guard is needed.
    re.compile(r"\[FINDING:"),
    re.compile(r"\[STAGE_COMPLETE:\d"),
    re.compile(r"\[CONFLICTS:"),
    # Praxis-unique prose markers — these phrases appear only in sciomc
    # sibling-convention analysis or its Korean equivalents; low FP risk.
    re.compile(r"\bsibling[- ]deviant\b", re.IGNORECASE),
    # Trailing \b omitted for Korean-particle attachment: Python's Unicode-aware \b
    # does not place a boundary between a Hangul word char and a following Hangul
    # particle (e.g. "의미 충돌이" has no \b after 충돌).  The leading \b still
    # guards against substring matches like "무의미 충돌".
    re.compile(r"\b의미\s*mismatch", re.IGNORECASE),
    re.compile(r"\b의미\s*충돌"),
)

_CONSENSUS_REFETCH_MARKERS = (
    re.compile(r"\bgh\s+pr\s+view\s+[^\n]*--json\s+[^\n]*body", re.IGNORECASE),
    re.compile(r"\bgh\s+issue\s+view\s+[^\n]*--json\s+[^\n]*body", re.IGNORECASE),
    re.compile(r"\bconsensus\s+re[- ]?fetch", re.IGNORECASE),
    re.compile(r"\bre[- ]?read\s+(?:PR|issue)\s+body", re.IGNORECASE),
    # "user-stated design" removed: it appears verbatim inside [CONFLICTS:] marker
    # payloads (e.g. "[CONFLICTS: user-stated design vs sibling convention]"), which
    # would self-satisfy the re-fetch check and silently pass the gate.  The
    # remaining gh-view and consensus-refetch patterns cover all real re-fetch paths.
    re.compile(r"\[ratified-by-user\]", re.IGNORECASE),
    re.compile(r"\[user-approved\]", re.IGNORECASE),
)


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


def _has_ratification_token(commit_args: list[str]) -> bool:
    # Scope the check to -m/--message values so a token elsewhere in a compound
    # command (`git commit -m x; echo '[user-approved]'`) does not bypass the gate.
    return any(_RATIFICATION_TOKEN_RE.search(value) for value in _message_values(commit_args))


# ---------------------------------------------------------------------------
# Role-aware transcript scan (praxis issue #573)
#
# The marker corpus is restricted to ASSISTANT-AUTHORED content so a loaded
# SKILL.md — which *documents* the `[FINDING:` / `[CONFLICTS:` /
# `[STAGE_COMPLETE:` output schema as literal example text — cannot self-trip
# the gate. A raw-text tail scan conflated two oracles: genuine agent finding
# output vs. documentation that merely names the tokens.
#
# Included in the finding corpus:
#   - assistant message TEXT blocks (the agent emitting a finding directly)
#   - tool_result blocks whose originating tool_use is Agent/Task — a sciomc
#     subagent's own output. Results are recorded in `user`-type entries, so
#     each tool_result's tool_use_id is resolved back to its tool name.
# Excluded from the finding corpus (the #573 false-positive sources):
#   - user-turn text (real user messages, skill-load doc payloads)
#   - system-reminder blocks / non-user/assistant entry types
#   - Read / Skill / Bash / other tool-results (loaded files, command output)
#
# The consensus-refetch check still scans the full ordered stream AFTER the
# last finding (assistant text + assistant tool_use commands + Agent/Task
# results), so a `gh pr view … --json body` recorded as an assistant Bash
# tool_use still satisfies the re-fetch gate.
# ---------------------------------------------------------------------------

_ASSISTANT_TEXT = "assistant_text"
_ASSISTANT_TOOLUSE = "assistant_tooluse"
_AGENT_RESULT = "agent_result"
# Kinds whose text counts as genuine, agent-authored finding output.
_FINDING_KINDS = frozenset({_ASSISTANT_TEXT, _AGENT_RESULT})
# tool_use names whose tool_result is a subagent's OWN output (sciomc runs as a
# subagent). Read/Skill/Bash/etc. results are loaded docs or command output and
# are NOT part of the finding corpus.
_SUBAGENT_TOOLS = frozenset({"Agent", "Task"})


def _map_tool_use_names(objs: list) -> dict:
    """Map tool_use_id → tool name across the WHOLE transcript so a recent
    tool_result can be resolved even when its originating tool_use predates the
    recency window."""
    names: dict = {}
    for obj in objs:
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        message = obj.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                tuid, name = blk.get("id"), blk.get("name")
                if isinstance(tuid, str) and isinstance(name, str):
                    names[tuid] = name
    return names


def _tool_use_text(blk: dict) -> str:
    inp = blk.get("input")
    if not isinstance(inp, dict):
        return ""
    cmd = inp.get("command")
    if isinstance(cmd, str):
        return cmd  # Bash command — where a `gh pr view … --json body` lives
    try:
        return json.dumps(inp, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""


def _tool_result_text(blk: dict) -> str:
    content = blk.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                out.append(item["text"])
            elif isinstance(item, str):
                out.append(item)
        return "\n".join(out)
    return ""


def _entry_segments(obj, tool_names: dict) -> list:
    """Return (kind, text) segments contributed by one transcript entry."""
    if not isinstance(obj, dict):
        return []
    typ = obj.get("type")
    message = obj.get("message")
    # Simplified string message form: only an assistant string is authored text.
    if isinstance(message, str):
        return [(_ASSISTANT_TEXT, message)] if typ == "assistant" else []
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        blocks: list = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        blocks = content
    else:
        return []
    segs: list = []
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        btype = blk.get("type")
        if typ == "assistant":
            if btype == "text" and isinstance(blk.get("text"), str):
                segs.append((_ASSISTANT_TEXT, blk["text"]))
            elif btype == "tool_use":
                segs.append((_ASSISTANT_TOOLUSE, _tool_use_text(blk)))
        elif typ == "user" and btype == "tool_result":
            # tool_results live in user-type entries; only a subagent's own
            # output counts as finding content. Read/Skill/etc. are excluded.
            if tool_names.get(blk.get("tool_use_id")) in _SUBAGENT_TOOLS:
                segs.append((_AGENT_RESULT, _tool_result_text(blk)))
        # user text blocks (real user msg / skill-load / system-reminder) and
        # non-user/assistant entry types contribute nothing to either corpus.
    return segs


def _build_finding_scan(path: str, max_entries: int, max_bytes: int):
    """Build the role-aware scan over recent transcript entries.

    Returns (stream, finding_offsets, matched_markers), or None when the
    transcript cannot be read (caller fail-opens):
      stream          — ordered text of assistant + Agent/Task segments, used
                        for the positional consensus-refetch check.
      finding_offsets — offsets in `stream` of finding markers that land in
                        agent-authored (assistant text / Agent result) spans.
      matched_markers — the matched marker strings (for the block message).
    """
    objs = load_transcript_objs(path, max_bytes)
    if objs is None:
        return None
    tool_names = _map_tool_use_names(objs)
    parts: list = []
    finding_offsets: list = []
    matched_markers: list = []
    pos = 0
    for obj in objs[-max_entries:]:
        for kind, text in _entry_segments(obj, tool_names):
            start = pos
            parts.append(text)
            parts.append("\n")
            pos += len(text) + 1
            if kind in _FINDING_KINDS:
                for m in _iter_marker_matches(text, _FINDING_MARKERS):
                    finding_offsets.append(start + m.start())
                    matched_markers.append(m.group(0).strip())
    return "".join(parts), finding_offsets, matched_markers


def _iter_marker_matches(text: str, patterns: tuple[re.Pattern, ...]):
    for pat in patterns:
        yield from pat.finditer(text)


def _has_consensus_refetch(text: str) -> bool:
    return any(pat.search(text) for pat in _CONSENSUS_REFETCH_MARKERS)


def _emit_block_message(matched_markers: list[str]) -> None:
    sys.stderr.write(
        "\n".join(
            [
                "BLOCKED: `git commit` after sciomc finding without user-design consensus re-fetch.",
                "",
                f"Detected finding markers in recent transcript: {', '.join(matched_markers) or '(none)'}",
                "",
                "Rule: User-stated design (PR body / issue body) is RATIFIED.",
                "AI analysis findings ([FINDING:...], [STAGE_COMPLETE:N], [CONFLICTS:...],",
                "sibling-deviant, 의미 mismatch/충돌) are DRAFTS — surface to user, never",
                "auto-flip the design via direct commit.",
                "",
                "Pattern (praxis issue #374):",
                "  AI flips a user-stated literal/design based on a sciomc/reviewer finding,",
                "  without re-reading the PR body first. User redirects back → revert + extra",
                "  commits + PR body rewrite + reviewer-timeline noise.",
                "",
                "Resolve by one of:",
                "  1. Re-fetch the user's stated design with:",
                "       gh pr view <N> --json body --jq .body",
                "       gh issue view <N> --json body --jq .body",
                "     Compare against the user's stated design. If conflicting, surface via",
                "     AskUserQuestion BEFORE committing.",
                "  2. If user explicitly approved the change in this session, add token",
                "     [user-approved] or [ratified-by-user] to the commit message.",
                "  3. One-off bypass: set CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 in the session",
                "     environment, since an inline `VAR=1 git commit ...` prefix never",
                "     reaches this hook.",
                "",
                "CLAUDE.md: Output-Block-Level Falsification Gate / Self-Falsify Before Recommendation Lock",
            ]
        )
        + "\n"
    )


if __name__ == "__main__":
    sys.exit(main())
