#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: name the MANDATORY step that has no enforcer.

Issue #1064. In one session every workflow step that carried a hook was
followed and every MANDATORY step that carried none was skipped — the
discriminator of compliance was the presence of an enforcer, not the weight of
the rule. A step with no hook also leaves no `is_error` behind, so a retrospect
that scans for friction signals is structurally blind to it: the 2026-08-20
omission was recovered by an external critic, not by the session.

This hook supplies the missing firing point. It does not block. Its whole
product is that the step becomes observable at the moment it is due.

Four predicates, each attached to the action that immediately precedes its
step, each keyed on an ABSENCE in the session transcript so the advisory
cannot be cleared by adopting whatever vocabulary a marker scan would look for:

  review      a content commit      — no `oh-my-claudecode:code-reviewer`
                                      Agent dispatch this session
  pre-pr      `gh pr create`        — the branch is behind its base
  pre-merge   `gh pr merge`         — the same, re-measured at merge
  in-flight   `git worktree add` /  — no open-PR enumeration this session
              `cmux` dispatch

The rebase predicates deliberately do NOT ask whether a rebase command ran.
"A rebase happened" is a sticky session-wide boolean: rebase once before
opening the PR and the merge-time check goes quiet for the rest of the day,
however far the base moves through the review rounds — the exact shape #1064
records. `git rev-list --count HEAD..<base>` cannot go stale that way, and it
is also silent on a freshly-cut branch where the rebase would be a no-op. All
probes are local reads; nothing here touches the network.

The `review` predicate stands down when `praxis:codex-review-wrap` is missing
too. That case already belongs to `block-commit-without-codex-review`, which
denies the call outright — adding a second message to a turn that is being
blocked anyway buys nothing and spends the advisory's credibility.

Exits 0 always. `PRAXIS_UNENFORCED_STEP_STRICT=1` converts a fire into a hard
block (exit 2) for a session that wants the stronger signal;
`PRAXIS_UNENFORCED_STEP_SKIP=1` silences it.
"""
from __future__ import annotations

import os
import sys
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _git import run_git  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    shared_probe_deadline,
)
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    is_help_invocation,
    iter_command_starts,
    safe_tokenize,
    strip_heredoc_bodies,
    strip_prefix,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    TranscriptReadError,
    scan_cursor_path,
    scan_transcript_resumable,
)

STRICT_ENV = "PRAXIS_UNENFORCED_STEP_STRICT"
SKIP_ENV = "PRAXIS_UNENFORCED_STEP_SKIP"

# Must match this hook's manifest `timeout`; shared_probe_deadline subtracts
# its own margin for interpreter startup and process spawn.
_MANIFEST_TIMEOUT_SEC = 8

# Bound the transcript scan the same way the sibling gates do: a session large
# enough to exceed this is one where the scan cost outweighs the nudge.
_MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024
_HOOK_NAME = "unenforced-step-advisory"

_REVIEW_AGENT = "code-reviewer"
_CODEX_SKILL = "praxis:codex-review-wrap"

# Consulted in this order when `origin/HEAD` is unset, matching the repo's own
# branch-base preference (praxis cuts every branch from main).
_BASE_CANDIDATES = ("origin/main", "origin/master", "origin/prod")

_GROUP_PREFIX_CHARS = "(){}$`"

# Global options accepted BEFORE the subcommand that consume the next token as
# their value. Skipping the flag but not its value leaves the value sitting
# where the subcommand should be, and every match silently fails:
# `git -C /repo commit`, `gh -R owner/repo pr merge`.
_GIT_GLOBAL_VALUE_OPTS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
)
# gh's are inherited cobra flags, so they are accepted in any position — before
# `pr`, and between `pr` and the verb (the same walk gh-merge-worktree-precondition
# performs).
_GH_GLOBAL_VALUE_OPTS = frozenset({"-R", "--repo", "--hostname", "--color"})
# `cmux --config <path> new-workspace` takes a value the same way.
_CMUX_GLOBAL_VALUE_OPTS = frozenset({"--config", "-c"})

# Value-taking flags of the SUBCOMMANDS too, not just the globals. These are
# what `is_help_invocation` needs to tell a help request from a flag whose
# value happens to be `-h`: `git commit -m -h` commits, and reading its `-h` as
# a help flag would silence the predicate on a real commit.
_GIT_VALUE_OPTS = _GIT_GLOBAL_VALUE_OPTS | frozenset(
    {
        "-m", "--message", "-F", "--file", "--author", "--date", "-t",
        "--template", "--cleanup", "--trailer", "-S", "--gpg-sign",
        "--squash", "--fixup", "-u", "--untracked-files", "-b", "-B",
    }
)
_GH_VALUE_OPTS = _GH_GLOBAL_VALUE_OPTS | frozenset(
    {
        "-t", "--title", "-b", "--body", "-F", "--body-file", "-B", "--base",
        "-H", "--head", "-a", "--assignee", "-l", "--label", "-r", "--reviewer",
        "-p", "--project", "-m", "--milestone", "-s", "--state", "-L",
        "--limit", "--search", "--subject", "--match-fields", "--json",
        "--jq", "--template", "--author", "--owner",
    }
)
_CMUX_VALUE_OPTS = _CMUX_GLOBAL_VALUE_OPTS | frozenset(
    {"--name", "--command", "--model", "--cwd"}
)


def _binary_name(token: str) -> str:
    return token.lstrip(_GROUP_PREFIX_CHARS).rsplit("/", 1)[-1]


def _subcommand_words(
    argv: list[str], value_opts: frozenset[str], help_opts: frozenset[str]
) -> list[str] | None:
    """argv[1:] reduced to its positional words, or None if nothing executes.

    `--long=value` is one token and needs no lookahead; a bare `--long value`
    consumes the token after it. Help and version invocations print and exit,
    so they yield None — but only when the flag is the segment's own, which is
    what `is_help_invocation` decides from `help_opts`: a raw scan would read
    the `-h` in `git commit -m -h` as a help request and miss a real commit.
    `value_opts` (globals only) is what decides where the subcommand sits;
    `help_opts` widens that to the subcommand's own value flags.
    """
    if is_help_invocation(argv, help_opts):
        return None
    words: list[str] = []
    skip_next = False
    for tok in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if tok in value_opts:
            skip_next = True
            continue
        if tok.startswith("-"):
            continue
        words.append(tok)
    return words


def _subcommand_of(
    argv: list[str], binary: str, value_opts: frozenset[str], help_opts: frozenset[str]
) -> list[str] | None:
    """Positional words of a `<binary> …` invocation, or None if it is not one."""
    argv = strip_prefix(argv)
    if not argv or _binary_name(argv[0]) != binary:
        return None
    return _subcommand_words(argv, value_opts, help_opts)


def _git_subcommand(argv: list[str], *subcommand: str) -> bool:
    """True iff argv is a `git <subcommand…>` invocation (global flags skipped)."""
    words = _subcommand_of(argv, "git", _GIT_GLOBAL_VALUE_OPTS, _GIT_VALUE_OPTS)
    return words is not None and words[: len(subcommand)] == list(subcommand)


def _gh_subcommand(argv: list[str], group: str, verb: str) -> bool:
    """True iff argv is a `gh <group> <verb>` invocation."""
    words = _subcommand_of(argv, "gh", _GH_GLOBAL_VALUE_OPTS, _GH_VALUE_OPTS)
    return words is not None and words[:2] == [group, verb]


def _is_cmux_dispatch(argv: list[str]) -> bool:
    """True iff argv creates a cmux workspace (this repo's dispatch verb)."""
    words = _subcommand_of(argv, "cmux", _CMUX_GLOBAL_VALUE_OPTS, _CMUX_VALUE_OPTS)
    if words is None:
        return False
    return words[:1] == ["new-workspace"] or words[:2] == ["workspace", "create"]


def _command_segments(command: str):
    """Yield every argv the shell would execute, substitution spans included.

    A trigger wrapped in `$( … )` or backticks runs exactly as if it were
    written plainly, and the repo's own dispatch idiom is one:
    `WS_RAW=$(cmux new-workspace …)` (skills/cmux-delegate/SKILL.md:492).
    Without the span walk `strip_prefix` reads `WS_RAW=$(cmux` as an env
    assignment and drops the binary with it, so the canonical call is invisible
    — the same bypass surface `block-commit-without-codex-review` closes.
    """
    # Spans come from the heredoc-STRIPPED text. A heredoc body is data the
    # shell hands to another program, not commands it runs, and authoring a
    # test fixture or a spec that quotes `gh pr list --state open` would
    # otherwise register as having run it — measured live: three fixture-
    # writing commands in this hook's own implementing session silently
    # cleared the in-flight predicate that way.
    for text in [command, *_substitution_spans(strip_heredoc_bodies(command))]:
        tokens = safe_tokenize(text)
        if not tokens:
            continue
        yield from iter_command_starts(tokens)


def _substitution_spans(command: str) -> list[str]:
    """Inner text of every `$( … )` / backtick substitution bash would execute.

    Quote-aware: bash runs a substitution unquoted and inside double quotes,
    but not inside single quotes. A paren-depth counter that skips
    backslash-escaped characters handles nesting and a literal `\\)`.
    """
    spans: list[str] = []
    i, n = 0, len(command)
    in_squote = in_dquote = False
    while i < n:
        c = command[i]
        if in_squote:
            if c == "'":
                in_squote = False
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if c == "'" and not in_dquote:
            in_squote = True
            i += 1
            continue
        if c == '"':
            in_dquote = not in_dquote
            i += 1
            continue
        if c == "`":
            j = i + 1
            while j < n and command[j] != "`":
                j += 2 if command[j] == "\\" else 1
            if j >= n:
                break
            spans.append(command[i + 1:j])
            i = j + 1
            continue
        if c == "$" and i + 1 < n and command[i + 1] == "(":
            depth, j = 1, i + 2
            start = j
            while j < n and depth:
                if command[j] == "\\":
                    j += 2
                    continue
                if command[j] == "(":
                    depth += 1
                elif command[j] == ")":
                    depth -= 1
                j += 1
            if depth:
                break
            spans.append(command[start:j - 1])
            i = j
            continue
        i += 1
    return spans


def _has_option(argv: list[str], option: str, value_opts: frozenset[str]) -> bool:
    """True iff `option` appears as an OPTION, not as some flag's value.

    `option in argv` cannot tell the two apart, so `git commit -m --amend`
    reads as an amend and a real content commit slips past the predicate — and
    past strict mode's block with it. Everything after a bare `--` is
    positional, never an option.
    """
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            return False
        if tok == option:
            return True
        if tok.startswith("-") and "=" not in tok and tok in value_opts:
            i += 2
            continue
        i += 1
    return False


def _classify(command: str) -> str | None:
    """Return the trigger id for `command`, or None when it is not a trigger."""
    for argv in _command_segments(command):
        if _git_subcommand(argv, "commit"):
            # --amend fixes up a call that already passed this point. Skip this
            # segment only — `git commit --amend && git commit -m x` runs a
            # content commit in the second one, and returning here would miss it.
            if _has_option(argv, "--amend", _GIT_VALUE_OPTS):
                continue
            return "review"
        if _gh_subcommand(argv, "pr", "create"):
            return "pre-pr"
        if _gh_subcommand(argv, "pr", "merge"):
            return "pre-merge"
        if _git_subcommand(argv, "worktree", "add"):
            return "in-flight"
        if _is_cmux_dispatch(argv):
            return "in-flight"
    return None


class _SessionFacts:
    """What the session transcript shows was done, for the predicates in play.

    Only the facts a trigger actually consumes are collected. The `review`
    trigger needs no Bash tokenization at all, and tokenizing every Bash
    command of a large session is the expensive part — 1.8s on a 46MB
    transcript, inside a dispatch group whose members share one deadline, and
    one slow member starves every later one.
    """

    def __init__(self, wanted: frozenset) -> None:
        self._wanted = wanted
        self.review_agent = False
        self.codex_review = False
        self.open_pr_scan = False

    def wants(self, fact: str) -> bool:
        return fact in self._wanted

    def settled(self) -> bool:
        """True once every wanted fact is True — nothing left to learn."""
        return all(getattr(self, name) for name in self._wanted)


# The rebase triggers read nothing from the transcript: whether a rebase ran
# is not the question, whether the branch is current is (see _advise_rebase).
_TRIGGER_FACTS = {
    "review": frozenset({"review_agent", "codex_review"}),
    "pre-pr": frozenset(),
    "pre-merge": frozenset(),
    "in-flight": frozenset({"open_pr_scan"}),
}

# `gh pr list` defaults to open. Both spellings have to be read: skipping the
# short one lets `gh pr list -s merged` clear the in-flight predicate, and a
# `--state merged` enumeration is the exact miss the issue records.
_STATE_FLAGS = ("--state", "-s")
_OPEN_STATES = (None, "open", "all")


def _flag_value(argv: list[str], flags: tuple) -> str | None:
    for i, tok in enumerate(argv):
        for flag in flags:
            if tok == flag and i + 1 < len(argv):
                return argv[i + 1]
            if tok.startswith(flag + "="):
                return tok.split("=", 1)[1]
    return None


def _note_bash(facts: _SessionFacts, command: str) -> None:
    for argv in _command_segments(command):
        if _gh_subcommand(argv, "pr", "list") or _gh_subcommand(argv, "search", "prs"):
            if _flag_value(argv, _STATE_FLAGS) in _OPEN_STATES:
                facts.open_pr_scan = True


def _note_tool_use(facts: _SessionFacts, block: dict) -> None:
    name = block.get("name")
    payload = block.get("input")
    if not isinstance(payload, dict):
        payload = {}
    if name == "Bash":
        if facts.wants("open_pr_scan") and not facts.open_pr_scan:
            command = payload.get("command")
            if isinstance(command, str):
                _note_bash(facts, command)
        return
    if name in ("Agent", "Task"):
        if not facts.wants("review_agent"):
            return
        subagent = payload.get("subagent_type") or payload.get("agent_type") or ""
        if isinstance(subagent, str) and _REVIEW_AGENT in subagent:
            facts.review_agent = True
        return
    if name == "Skill" and facts.wants("codex_review"):
        if payload.get("skill") == _CODEX_SKILL:
            facts.codex_review = True


# Every fact `_note_tool_use` can settle lives in a `tool_use` block whose
# `name` is one of these, so a record without the quoted tool name cannot
# contribute. Rejecting on it before the parse is what keeps a `review` scan
# of a session with no review in it — the case the advisory exists for, where
# nothing ever settles — from parsing every line to EOF on every commit
# (issue #1278). Keyed per fact rather than on a blanket `"tool_use"`: the
# large assistant lines are Bash heredocs and Edit/Write bodies, and a
# `review` walk that parsed every one of those would still pay a parse per
# tool call instead of per Agent/Task/Skill call.
_FACT_NEEDLES = {
    "review_agent": ('"Agent"', '"Task"'),
    "codex_review": ('"Skill"',),
    "open_pr_scan": ('"Bash"',),
}


def _needles_for(facts: _SessionFacts) -> tuple[str, ...]:
    """Quoted tool-name tokens for the facts this trigger still wants.

    Sorted by fact so the tuple is deterministic; a line carrying none of
    them cannot settle any wanted fact and is rejected before `json.loads`.
    """
    return tuple(n for fact in sorted(facts._wanted) for n in _FACT_NEEDLES[fact])


_FACT_NAMES = ("review_agent", "codex_review", "open_pr_scan")


def _encode_facts(facts: _SessionFacts) -> dict:
    """Cursor form of the facts: one bool per fact name."""
    return {name: bool(getattr(facts, name)) for name in _FACT_NAMES}


def _absorb(facts: _SessionFacts, path: str, cursor_path: str | None = None) -> bool:
    """Fold one transcript's tool_use blocks into `facts`, stopping once settled.

    Returns True when the walk finished (EOF, or every wanted fact settled)
    and False when the per-call byte budget cut it short. With `cursor_path`
    the walk resumes where the previous call for this file and trigger
    stopped, so a commit reads only the bytes appended since the last one
    and a settled fact is never re-derived. Raises `TranscriptReadError` for
    a missing or unreadable file.
    """
    wanted = facts._wanted

    def decode(saved: dict) -> _SessionFacts:
        """Rebuild the facts object a previous call saved in the cursor."""
        resumed = _SessionFacts(wanted)
        for name in _FACT_NAMES:
            if saved.get(name) is True:
                setattr(resumed, name, True)
        return resumed

    def fold(state: _SessionFacts, event: dict) -> None:
        """Note every tool_use block of one assistant record."""
        message = event.get("message")
        if not isinstance(message, dict):
            return
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                _note_tool_use(state, block)

    state, complete = scan_transcript_resumable(
        path,
        cursor_path,
        lambda: facts,
        fold,
        needle=tuple(n.encode("utf-8") for n in _needles_for(facts)),
        max_bytes=_MAX_TRANSCRIPT_BYTES,
        stop_when=lambda f: f.settled(),
        encode=_encode_facts,
        decode=decode,
    )
    if state is not facts:  # resumed from the cursor: carry it into the caller's object
        for name in _FACT_NAMES:
            if getattr(state, name):
                setattr(facts, name, True)
    return complete


def _subagents_dir(transcript_path: str):
    """`<session-dir>/subagents/` for a root transcript path, if it exists.

    Claude Code lays subagent transcripts out as a sibling directory named
    after the session: root `<project-dir>/<session_id>.jsonl`, subagents
    `<project-dir>/<session_id>/subagents/agent-*.jsonl`. Mirrors
    `block-commit-without-codex-review._subagents_dir`, its `.jsonl`
    requirement included — two gates reading the same layout differently would
    disagree about what one session did.
    """
    p = _Path(transcript_path)
    if p.suffix != ".jsonl":
        return None
    candidate = p.with_suffix("") / "subagents"
    return candidate if candidate.is_dir() else None


def _scan_session(transcript_path: str, trigger: str, session_id=None) -> _SessionFacts | None:
    """Collect the facts `trigger` needs, or None if the root is unreadable or
    its scan has not caught up yet.

    Subagent transcripts are scanned alongside the root one: an Agent dispatch
    made inside a Task-dispatched subagent is recorded only in that subagent's
    own JSONL, so a root-only scan under-reports work that actually happened
    (the blindness `block-commit-without-codex-review` documents for #730).

    Cursors are keyed per trigger and per file (`<trigger>-root`,
    `<trigger>-<subagent stem>`): the two triggers read different tool
    names, and a `review` walk that skipped Bash lines must never move the
    `in-flight` cursor past them. Without a `session_id` the scan runs
    without persistence, under the same per-call budget.
    """
    facts = _SessionFacts(_TRIGGER_FACTS[trigger])
    if facts.settled():
        return facts  # this trigger reads nothing from the transcript

    try:
        complete = _absorb(
            facts, transcript_path, scan_cursor_path(_HOOK_NAME, session_id, f"{trigger}-root")
        )
    except TranscriptReadError:
        return None
    if not complete and not facts.settled():
        return None  # not caught up this call: nothing to measure against yet

    pending = False
    subagents = _subagents_dir(transcript_path)
    if subagents is not None and not facts.settled():
        for agent_file in sorted(subagents.glob("agent-*.jsonl")):
            try:
                complete = _absorb(
                    facts,
                    str(agent_file),
                    scan_cursor_path(_HOOK_NAME, session_id, f"{trigger}-{agent_file.stem}"),
                )
            except TranscriptReadError:
                continue  # one subagent's history just cannot contribute
            if facts.settled():
                break
            if not complete:
                pending = True  # its unread tail may still settle a fact
    if pending and not facts.settled():
        return None  # a subagent is not caught up: an absence is not yet a fact
    return facts


def _base_ref(cwd: str | None, deadline: float) -> str | None:
    out = run_git(
        ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
        cwd=cwd,
        deadline=deadline,
    )
    if out and out.strip():
        return out.strip().removeprefix("refs/remotes/")
    for candidate in _BASE_CANDIDATES:
        if run_git(
            ["rev-parse", "--verify", "--quiet", candidate], cwd=cwd, deadline=deadline
        ):
            return candidate
    return None


def _commits_behind(cwd: str | None, deadline: float) -> tuple[str, int] | None:
    """`(base, N)` where N is how many base commits HEAD lacks, or None.

    None means the question could not be answered locally (no base ref, a
    detached or unborn HEAD, git unavailable, no probe budget left) — the
    caller then stays silent rather than nudging on an unmeasured premise.
    """
    base = _base_ref(cwd, deadline)
    if base is None:
        return None
    out = run_git(["rev-list", "--count", f"HEAD..{base}"], cwd=cwd, deadline=deadline)
    if out is None:
        return None
    try:
        return base, int(out.strip())
    except ValueError:
        return None


def _worktree_count(cwd: str | None, deadline: float) -> int | None:
    out = run_git(["worktree", "list", "--porcelain"], cwd=cwd, deadline=deadline)
    if out is None:
        return None
    return sum(1 for line in out.splitlines() if line.startswith("worktree "))


def _advise(trigger: str, detail: str) -> int:
    strict = os.environ.get(STRICT_ENV) == "1"
    closing = (
        f"    STRICT mode ({STRICT_ENV}=1) — this call was blocked.\n"
        f"    STRICT 모드({STRICT_ENV}=1) — 이 호출을 차단했습니다.\n"
        if strict
        else "    Skipping on purpose? Carry on — this is not a block.\n"
        "    의도적으로 건너뛰는 것이면 그대로 진행하십시오 — 차단하지 않습니다.\n"
    )
    sys.stderr.write(
        "\n⚠️  UNENFORCED MANDATORY STEP — no gate owns this one\n"
        f"    trigger: {trigger}\n"
        f"    {detail}\n"
        "    No hook owns this step, so skipping it leaves no is_error behind (praxis #1064).\n"
        "    이 단계는 훅이 없어 건너뛰어도 is_error 가 남지 않습니다 (praxis #1064).\n"
        + closing
        + f"    Permanent opt-out / 상시 해제: {SKIP_ENV}=1\n"
    )
    return 2 if strict else 0


def _advise_rebase(trigger: str, cwd: str | None, deadline: float) -> int:
    """Advise when the branch is behind its base, whatever the session did.

    The oracle is the branch's distance from its base, not whether a rebase
    command appeared in the transcript. A session-wide "a rebase happened"
    boolean is sticky: rebase once before opening the PR and the pre-merge
    check goes silent for the rest of the session, however far the base moves
    during the review rounds — which is precisely the shape issue #1064
    records (PR #1058: 04:56 branch cut, review rounds and 4 commits, 22:52
    merge). Distance-from-base cannot go stale that way, and it also stays
    silent on a freshly-cut branch where the rebase would be a no-op.
    """
    behind = _commits_behind(cwd, deadline)
    if behind is None:
        return 0
    base, count = behind
    if count == 0:
        return 0  # already current — a rebase would be a no-op
    label = (
        "gh pr create → pre-PR rebase (MANDATORY)"
        if trigger == "pre-pr"
        else "gh pr merge → pre-merge rebase (MANDATORY)"
    )
    return _advise(
        label,
        f"HEAD is {count} commit(s) behind {base}: "
        f"`git fetch origin && git rebase {base}`\n"
        f"    HEAD 가 {base} 보다 {count} 커밋 뒤에 있습니다.",
    )


@fail_open
def main() -> int:
    """Hook entry point: classify the command, scan the session, advise."""
    if os.environ.get(SKIP_ENV) == "1":
        return 0

    payload = read_payload()
    if payload is None or payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    if not isinstance(command, str) or not command:
        return 0

    trigger = _classify(command)
    if trigger is None:
        return 0

    cwd = payload.get("cwd")
    # One deadline for every probe this invocation spawns, so their SUM stays
    # inside the manifest timeout instead of each reading the budget alone.
    deadline = shared_probe_deadline(_MANIFEST_TIMEOUT_SEC)

    if trigger in ("pre-pr", "pre-merge"):
        return _advise_rebase(trigger, cwd, deadline)

    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return 0  # nothing to measure against → stay silent
    facts = _scan_session(transcript_path, trigger, payload.get("session_id"))
    if facts is None:
        return 0

    if trigger == "review":
        if facts.review_agent or not facts.codex_review:
            return 0
        return _advise(
            "content commit → oh-my-claudecode:code-reviewer (MANDATORY)",
            "no code-reviewer agent call in this session "
            "(model-routing-advisory/spec.md 'Deliver' table).\n"
            "    이 세션에서 code-reviewer 에이전트 호출 0건 "
            "(model-routing-advisory/spec.md 'Deliver' 표).",
        )

    if facts.open_pr_scan:
        return 0
    count = _worktree_count(cwd, deadline)
    worktrees_en = (
        f"{count} active worktree(s)" if count is not None else "active worktree count unknown"
    )
    worktrees_ko = f"{count}개" if count is not None else "수 미확인"
    return _advise(
        "worktree/dispatch → in-flight PR 검사 (MANDATORY)",
        f"no open-PR enumeration in this session, {worktrees_en}: "
        "run `gh pr list --state open` on every related repo first.\n"
        f"    이 세션에서 open PR 열거 0건, 활성 워크트리 {worktrees_ko}: "
        "`gh pr list --state open` 를 관련 repo 전부에 대해 먼저 확인하십시오.",
    )


if __name__ == "__main__":
    sys.exit(main())
