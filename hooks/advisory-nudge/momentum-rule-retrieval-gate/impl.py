#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: surface relevant CLAUDE.md rules and memory
entries at high-momentum action points to prevent "Loaded ≠ Retrieved" failures.

Background (issue #326):
  2026-05-18 retrospect identified 3 friction events converging on the root
  cause "Loaded ≠ Retrieved at execution time" — rules and memory entries were
  loaded in context but failed retrieval at high-momentum moments such as
  multi-PR merge, dispatch, and force-push. This hook fires at exactly those
  moments and emits a structured reminder to stderr.

Trigger commands (Phase 1 — ANY single matching call triggers the surface;
  multi-mutation detection is deferred to Phase 2):
  • gh pr merge (any flags)
  • cmux new-workspace --command "claude -p ..." (dispatch via cmux)
  • git push --force-with-lease / git push -f / git push --force

Behavior:
  • Emits structured reminder to stderr ([praxis:momentum-gate] prefix per line)
  • Content is scoped per trigger type:
      gh pr merge        → Pre-Merge Reporting, No Approval Transfer Across
                           Companion PRs, memory feedback_pre_merge_briefing_compound_imperative
      cmux new-workspace → Pre-Implementation Surface Enumeration → Multi-PR /
                           multi-worktree shared state, Self-Authored Labels Are
                           Drafts, Not Ratified Scope
      force-push         → memory feedback_force_history_rewrite_mutation
  • Default mode: advisory (exit 0). Strict mode (PRAXIS_MOMENTUM_STRICT=1)
    exits 2 to block unless PRAXIS_MOMENTUM_ACK=1 is also set.
  • Bypass: PRAXIS_MOMENTUM_BYPASS=1 silences all output and exits 0.

Fail-open contract:
  • Malformed JSON / non-Bash payload → exit 0
  • Empty command → exit 0
  • Any uncaught exception in inner logic → swallowed, exit 0
  • python3 unavailable → shell wrapper exits 0
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from _transcript import TRANSCRIPT_SCAN_LINES  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Prefix used on every stderr line.
# ---------------------------------------------------------------------------

PREFIX = "[praxis:momentum-gate]"

# Trigger identifiers — keys for both static rule surfaces and dynamic
# memory filtering via the `momentum:` frontmatter field.
TRIGGER_MERGE = "merge"
TRIGGER_DISPATCH = "dispatch"
TRIGGER_FORCE_PUSH = "force-push"

# AI-provider name match for cmux dispatch detection. ASCII word boundaries
# (case-insensitive) so an actual provider invocation matches but an
# identifier substring like "CLAUDE_API_KEY=abc" does not (issue #513, 결함2).
_AI_PROVIDER_RE = re.compile(r"\b(claude|codex|gemini)\b", re.IGNORECASE)

CLOSING_LINE = f"{PREFIX} ─────────────────────────────────────────────────────────────"

# ---------------------------------------------------------------------------
# Static rule surfaces per trigger — CLAUDE.md rule cites only. Memory cites
# are loaded dynamically from the memory directory's frontmatter `momentum`
# field (replaces the prior hardcoded `Memory: <name>` blocks).
# ---------------------------------------------------------------------------

_MERGE_STATIC = """\
{p} ── TRIGGER: gh pr merge ──────────────────────────────────
{p}
{p} Rule: Pre-Merge Reporting (CLAUDE.md)
{p}   Before asking for merge approval, report: What changed / What was
{p}   verified / What was NOT verified / Risk / Open items / Explicit ask.
{p}   "완료했습니다, 머지할까요?" without evidence is an anti-pattern.
{p}
{p} Rule: No Approval Transfer Across Companion PRs (CLAUDE.md)
{p}   Approving "merge PR X" approves ONLY X. Companion, chore, regen, or
{p}   hotfix-blocker PRs each need their own explicit approval.""".format(p=PREFIX)

_DISPATCH_STATIC = """\
{p} ── TRIGGER: cmux new-workspace (dispatch) ───────────────────
{p}
{p} Rule: Pre-Implementation Surface Enumeration → Multi-PR / multi-worktree
{p}   shared state (CLAUDE.md)
{p}   When dispatching independent PRs in parallel, enumerate every file each
{p}   PR touches that a sibling also touches. hooks.json, AGENTS.md/CLAUDE.md,
{p}   marketplace.json, plugin.json, hook-index tables — all shared. First PR
{p}   to merge wins; subsequent PRs need rebase.
{p}
{p} Rule: Self-Authored Labels Are Drafts, Not Ratified Scope (CLAUDE.md)
{p}   AI-written task labels during planning = drafts. Ambiguous verbs
{p}   (define / review / verify / clean up / 정의 / 검토 / 확인) in self-authored
{p}   labels → surface one-time disambiguation BEFORE execution.""".format(p=PREFIX)

_FORCE_PUSH_STATIC = """\
{p} ── TRIGGER: git push --force / --force-with-lease / -f ─────
{p}
{p} Rule: History rewrite is a mutation — prior approval is NOT carried over
{p}   force-with-lease / reset --hard / rebase --skip (worker commit drop) all
{p}   require fresh per-action consent. Prefer a fixup commit when possible.""".format(p=PREFIX)

_STATIC_BY_TRIGGER = {
    TRIGGER_MERGE: _MERGE_STATIC,
    TRIGGER_DISPATCH: _DISPATCH_STATIC,
    TRIGGER_FORCE_PUSH: _FORCE_PUSH_STATIC,
}

# ---------------------------------------------------------------------------
# Frontmatter parser — mirrors hooks/memory-hint.py shape; no PyYAML.
# ---------------------------------------------------------------------------

FRONTMATTER_FENCE = re.compile(r"^---\s*$", re.MULTILINE)
MOMENTUM_RE = re.compile(r"^\s*momentum\s*:\s*(.+)$", re.MULTILINE)
NAME_RE = re.compile(r"^\s*name\s*:\s*(.+)$", re.MULTILINE)
DESCRIPTION_RE = re.compile(r"^\s*description\s*:\s*(.+)$", re.MULTILINE)
INLINE_COMMENT_RE = re.compile(r"\s+#.*$")
CONTROL_BYTES_RE = re.compile(r"[\x00-\x1f\x7f]")


def _strip_inline_comment(value: str) -> str:
    return INLINE_COMMENT_RE.sub("", value)


def _sanitize(text: str) -> str:
    return CONTROL_BYTES_RE.sub("?", text)


def _parse_momentum_frontmatter(raw: str) -> dict | None:
    """Extract the `---`-fenced frontmatter. Return None if absent or no momentum list."""
    matches = list(FRONTMATTER_FENCE.finditer(raw))
    if len(matches) < 2:
        return None
    block = raw[matches[0].end():matches[1].start()]

    momentum_match = MOMENTUM_RE.search(block)
    if not momentum_match:
        return None
    momentum_raw = momentum_match.group(1).strip()
    if not momentum_raw.startswith("["):
        return None
    close_idx = momentum_raw.find("]")
    if close_idx == -1:
        return None
    inner = momentum_raw[1:close_idx].strip()
    if not inner:
        return None
    triggers = [item.strip().strip('"\'') for item in inner.split(",")]
    triggers = [t for t in triggers if t]
    if not triggers:
        return None

    description = ""
    description_match = DESCRIPTION_RE.search(block)
    if description_match:
        description = (
            _strip_inline_comment(description_match.group(1))
            .strip()
            .strip('"\'')
        )

    return {"triggers": triggers, "description": description}


def _resolve_memory_dir() -> str | None:
    env_dir = os.environ.get("PRAXIS_MEMORY_DIR", "").strip()
    if env_dir:
        return env_dir if os.path.isdir(env_dir) else None
    home = os.path.expanduser("~")
    cwd = os.getcwd()
    slug = cwd.replace("/", "-")
    fallback = os.path.join(home, ".claude", "projects", slug, "memory")
    return fallback if os.path.isdir(fallback) else None


def _load_momentum_memories(directory: str, trigger: str) -> list[tuple[str, str, float]]:
    """Return [(filename, description, mtime), ...] for memories matching trigger."""
    out: list[tuple[str, str, float]] = []
    try:
        entries = os.listdir(directory)
    except OSError:
        return out
    for name in entries:
        if not name.endswith(".md"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        try:
            parsed = _parse_momentum_frontmatter(raw)
        except Exception:
            parsed = None
        if not parsed or trigger not in parsed["triggers"]:
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        out.append((name, parsed["description"], mtime))
    out.sort(key=lambda h: h[2], reverse=True)
    return out


def _format_memory_lines(memories: list[tuple[str, str, float]]) -> list[str]:
    """Render memory entries as PREFIX-prefixed lines mirroring the prior hardcoded shape."""
    lines: list[str] = []
    for name, description, _ in memories:
        safe_name = _sanitize(name)
        # Strip ".md" suffix for parity with the prior hardcoded `Memory: feedback_*`
        # text (which referenced the slug without the file extension).
        display = safe_name[:-3] if safe_name.endswith(".md") else safe_name
        lines.append(f"{PREFIX} Memory: {display}")
        if description:
            lines.append(f"{PREFIX}   {_sanitize(description)}")
        lines.append(PREFIX)
    return lines


def _emit_for_trigger(trigger: str, directory: str | None) -> str:
    """Compose the full surface for a single trigger (static rules + dynamic memories)."""
    parts = [_STATIC_BY_TRIGGER[trigger], PREFIX]
    if directory:
        memories = _load_momentum_memories(directory, trigger)
        if memories:
            parts.extend(_format_memory_lines(memories))
    parts.append(CLOSING_LINE)
    return "\n".join(parts)

# ---------------------------------------------------------------------------
# Merge-briefing escalation (issue #797).
#
# The `merge` trigger alone is advisory (stderr) — but stderr cannot stop a
# `gh pr merge` that skips the Pre-Merge Reporting briefing, which recurred
# despite the advisory firing (memory feedback_pre_merge_briefing_compound_
# imperative, 2 recurrences). For the `merge` trigger ONLY, this hook inspects
# the assistant text preceding the merge call and, when the 6-item briefing is
# absent, escalates to a `permissionDecision: deny` so the merge is blocked and
# the agent must produce the briefing before retrying.
#
# Why deny (not ask): the sibling `pre-merge-approval-gate` already emits an
# unconditional `ask` on every `gh pr merge`. A second `ask` is redundant with
# the exact gate that failed to prevent the #795 recurrence (the user approved
# blindly while the agent never produced the briefing). `deny` adds the missing
# teeth — it blocks and feeds the reason back so the agent self-corrects.
#
# Fail-open / false-positive containment:
#   • no readable transcript                 → no escalation (fail open)
#   • CMUX_DELEGATE=1 (background agent)      → no escalation (mirror sibling)
#   • PRAXIS_MOMENTUM_MERGE_ADVISORY=1        → demote back to advisory only
#   • trivial-PR markers in the briefing text → no escalation (CLAUDE.md carve-out)
# ---------------------------------------------------------------------------

MERGE_ADVISORY_ENV = "PRAXIS_MOMENTUM_MERGE_ADVISORY"

# Distinct Pre-Merge Reporting items must be present in the pre-merge assistant
# text. The documented failure surfaced only 3 of 6 (What changed / verified /
# risk — missing NOT-verified, Open-items, and the explicit approve-ask), so a
# floor of 4 separates that case from a complete (or near-complete) briefing
# while keeping the false-positive rate low.
MERGE_BRIEFING_MIN_ITEMS = 4

# Each tuple is one Pre-Merge Reporting item; a single keyword hit marks the
# item present. Bilingual (EN/KO) since briefings are authored in Korean.
_BRIEFING_ITEM_GROUPS: tuple[tuple[str, ...], ...] = (
    # 1. What changed
    ("what changed", "무엇이 변경", "변경 사항", "변경사항", "변경 내용",
     "changed:", "changes:", "scope summary", "what was changed"),
    # 2. What was verified
    ("verified", "검증", "verification", "tested", "test pass", "테스트 통과",
     "lint clean", "확인 완료", "확인함"),
    # 3. What was NOT verified
    ("not verified", "not tested", "미검증", "미확인", "unverified",
     "not exercised", "검증하지", "확인하지 못", "deferred", "ci pending", "skipped"),
    # 4. Risk / blast radius
    ("risk", "blast radius", "리스크", "위험", "영향 범위", "영향범위",
     "blast", "downstream", "affects"),
    # 5. Open items
    ("open item", "미해결", "남은 항목", "남은 작업", "follow-up", "follow up",
     "후속", "caveat", "unresolved"),
    # 6. Explicit approve-ask
    ("approve", "승인", "머지할까요", "머지 할까요", "approve merge", "merge?",
     "진행할까요", "머지해도", "머지 진행", "proceed with the merge"),
)

# The 'verified' group shares substrings with the 'not verified' group —
# "not verified" ⊃ "verified", "not tested" ⊃ "tested", "unverified" ⊃
# "verified", "미검증" ⊃ "검증". A negative-phrasing briefing would otherwise
# satisfy BOTH groups from one phrase and inflate the count, letting a
# changed / not-verified / risk briefing reach the 4-item pass threshold with
# only 3 real items. The verified check therefore runs against a haystack with
# the not-verified phrases stripped out first (see _briefing_item_count).
# Named indices keep the strip correct if the group order is ever changed.
_VERIFIED_GROUP_IDX = 1
_NOT_VERIFIED_GROUP_IDX = 2

# Trivial-PR carve-out — CLAUDE.md permits a 2-line report for typo / comment /
# single-line config merges. When the agent has flagged the PR as trivial, the
# full 6-item briefing is not required, so do not escalate.
_TRIVIAL_MERGE_MARKERS: tuple[str, ...] = (
    "trivial pr", "trivial change", "typo", "comment-only", "comment only",
    "single-line", "single line", "오타", "주석만", "주석 수정", "2-line report",
)


def _briefing_item_count(text: str) -> int:
    """Count distinct Pre-Merge Reporting items present in the assistant text.

    The 'verified' group is checked against a haystack with the 'not verified'
    phrases stripped out, so a negative-phrasing briefing cannot satisfy both
    the verified and not-verified items from a single phrase (substring overlap
    documented at _VERIFIED_GROUP_IDX / _NOT_VERIFIED_GROUP_IDX).
    """
    low = text.lower()
    verified_haystack = low
    for kw in _BRIEFING_ITEM_GROUPS[_NOT_VERIFIED_GROUP_IDX]:
        verified_haystack = verified_haystack.replace(kw, " ")
    count = 0
    for idx, group in enumerate(_BRIEFING_ITEM_GROUPS):
        haystack = verified_haystack if idx == _VERIFIED_GROUP_IDX else low
        if any(kw in haystack for kw in group):
            count += 1
    return count


def _is_trivial_merge(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _TRIVIAL_MERGE_MARKERS)


def _current_turn_assistant_text(transcript_path: str) -> str | None:
    """Concatenate assistant text emitted since the last human user message.

    Scans a bounded tail window (TRANSCRIPT_SCAN_LINES) so a long transcript
    cannot blow the hook timeout. Returns:
      • None  → transcript missing / unreadable (caller fails open, no block)
      • ""    → transcript readable but the agent emitted no briefing text this
                turn (the exact compound-imperative failure — caller escalates)
      • text  → the briefing text to score
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            # Bounded tail read — hold at most TRANSCRIPT_SCAN_LINES lines in
            # memory instead of loading the whole (potentially huge) transcript.
            lines = deque(fh, maxlen=TRANSCRIPT_SCAN_LINES)
    except OSError:
        return None

    entries: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            entries.append(obj)

    # Locate the most recent human-authored user message (skip tool_result-only
    # user entries — those are the runtime's tool-output bridge, not input).
    last_user_idx = -1
    for i, ev in enumerate(entries):
        msg = ev.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "user" or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if isinstance(content, str):
            if content.strip():
                last_user_idx = i
        elif isinstance(content, list):
            if any(isinstance(b, dict) and b.get("type") != "tool_result" for b in content):
                last_user_idx = i

    turn = entries[last_user_idx + 1:] if last_user_idx >= 0 else entries
    texts: list[str] = []
    for ev in turn:
        msg = ev.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant" or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    t = b.get("text", "")
                    if isinstance(t, str):
                        texts.append(t)
    return "\n".join(texts)


def _merge_escalation_reason(payload: dict) -> str | None:
    """Return a deny reason when the pre-merge briefing is incomplete, else None."""
    # Background cmux-delegate agents merge autonomously — the delegation intent
    # is the approval (mirror pre-merge-approval-gate).
    if os.environ.get("CMUX_DELEGATE") == "1":
        return None
    # Demote-to-advisory escape hatch (false-positive relief without full bypass).
    if os.environ.get(MERGE_ADVISORY_ENV) == "1":
        return None

    text = _current_turn_assistant_text(payload.get("transcript_path", "") or "")
    if text is None:
        return None  # transcript unreadable → fail open, do not block
    if _is_trivial_merge(text):
        return None
    if _briefing_item_count(text) >= MERGE_BRIEFING_MIN_ITEMS:
        return None

    return format_block(
        rule_name="Pre-Merge Reporting briefing",
        why="this gh pr merge is not preceded by the Pre-Merge Reporting "
            "briefing — fewer than "
            f"{MERGE_BRIEFING_MIN_ITEMS} of 6 items present (What changed / "
            "verified / NOT verified / Risk / Open items / explicit approve-ask)",
        correct_path="surface the 6-item briefing and an explicit 'Approve "
            "merge?' question, then re-run the merge",
        bypass_env=MERGE_ADVISORY_ENV,
        reference="CLAUDE.md → Pre-Merge Reporting; "
            "hooks/advisory-nudge/momentum-rule-retrieval-gate/spec.md",
    )


# ---------------------------------------------------------------------------
# GH global flags that consume one additional argument.
# ---------------------------------------------------------------------------

GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})

# git global flags that consume one additional SEPARATE-TOKEN argument
# (value). Verified against `man git` / `git -h`. When these appear between
# `git` and the subcommand AND are NOT in fused `--flag=value` form, both the
# flag AND its value token must be skipped so the subcommand check lands at
# the correct argv position.
#
# IMPORTANT — boolean flags MUST NOT be in this set. Round-3 fix: prior
# inclusion of `--literal-pathspecs` and `--super-prefix` (both boolean per
# `man git` / `git -h`) caused the walker to consume the NEXT token as a
# value, masking `push` and bypassing the force-push gate entirely. Example:
# `git --literal-pathspecs push --force` previously slipped through.
#
# `--exec-path` retained: bare `--exec-path` is a QUERY form that exits
# immediately (never reaches `push`), so treating it as value-consuming does
# not introduce a real bypass — only over-skips in a code path that does not
# reach runtime.
GIT_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-c", "-C",
    "--git-dir", "--work-tree", "--namespace",
    "--exec-path", "--config-env",
})


# ---------------------------------------------------------------------------
# Trigger detection helpers.
# ---------------------------------------------------------------------------

def _is_gh_pr_merge(argv: list[str]) -> bool:
    """Return True iff the argv segment is `gh pr merge`."""
    argv = strip_prefix(argv)
    if not argv or argv[0] != "gh":
        return False
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in GH_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            i += 1
    if i + 1 >= len(argv):
        return False
    return argv[i] == "pr" and argv[i + 1] == "merge"


def _is_cmux_dispatch(argv: list[str]) -> bool:
    """Return True iff the argv is `cmux new-workspace ... --command "<ai> ..."`.

    Round-2 fix (MAJOR 1): require `--command` value to contain an AI provider
    token (`claude` / `codex` / `gemini`) to avoid false positives on:
      • `cmux new-workspace test-foo` (plain workspace, no command)
      • `cmux new-workspace --command "echo hello"` (non-AI command)
    False positives train users to ignore the surface, defeating the gate.

    Detection contract:
      1. argv[0] == "cmux"
      2. argv contains "new-workspace" as a token
      3. argv contains `--command <value>` OR `--command=<value>` where the
         value contains a known AI provider name token.
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "cmux":
        return False
    if "new-workspace" not in argv[1:]:
        return False

    # Scan for --command and inspect its value (matched via _AI_PROVIDER_RE).
    n = len(argv)
    for i, tok in enumerate(argv):
        value: str | None = None
        if tok == "--command" and i + 1 < n:
            value = argv[i + 1]
        elif tok.startswith("--command="):
            value = tok.split("=", 1)[1]
        if value is None:
            continue
        if _AI_PROVIDER_RE.search(value):
            return True
    return False


def _is_force_push(argv: list[str]) -> bool:
    """Return True iff the argv is `git push` with a force flag.

    Round-2 fix (MAJOR 2): the prior bare-flag walker treated `-c key=val`
    as `(flag)(positional)` and landed the subcommand check on `key=val`,
    silently bypassing the gate for `git -c user.name=x push --force ...`.
    Mirror the gh global-flags-with-arg pattern: skip both the flag and its
    value token. `--flag=value` fused form needs only single-token skip.
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "git":
        return False
    # Walk past git global flags + their value tokens to find the subcommand.
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        # If the flag is a known value-taker AND in bare form (not --flag=value),
        # the next token is the value — skip it too.
        if "=" not in tok and tok in GIT_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            i += 1
    if i >= len(argv) or argv[i] != "push":
        return False
    # Scan remaining tokens for force flags.
    rest = argv[i + 1:]
    force_flags = {"--force", "-f", "--force-with-lease"}
    for tok in rest:
        bare = tok.split("=", 1)[0]
        if bare in force_flags or tok in force_flags:
            return True
        # Bundled POSIX short cluster carrying `f` (=`--force`), e.g. `-fu`, `-vf`.
        # All `git push` short opts are value-less, so a bare `^-[a-zA-Z]+$` token
        # is a plain flag cluster (#512).
        if (
            len(tok) > 2
            and tok.startswith("-")
            and not tok.startswith("--")
            and "=" not in tok
            and tok[1:].isalpha()
            and "f" in tok[1:]
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _detect_triggers(command: str) -> list[str]:
    """Return list of trigger ids (one of TRIGGER_*) for the given Bash command."""
    tokens = safe_tokenize(command)
    if not tokens:
        return []

    triggered: list[str] = []
    for argv in iter_command_starts(tokens):
        if _is_gh_pr_merge(argv) and TRIGGER_MERGE not in triggered:
            triggered.append(TRIGGER_MERGE)
        if _is_cmux_dispatch(argv) and TRIGGER_DISPATCH not in triggered:
            triggered.append(TRIGGER_DISPATCH)
        if _is_force_push(argv) and TRIGGER_FORCE_PUSH not in triggered:
            triggered.append(TRIGGER_FORCE_PUSH)
    return triggered


@fail_open
def main() -> int:
    # Bypass: scripted batch operations may set this to silence the gate.
    if os.environ.get("PRAXIS_MOMENTUM_BYPASS") == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed input

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    triggers = _detect_triggers(command)
    if not triggers:
        return 0

    directory = _resolve_memory_dir()

    # Emit each surface to stderr — static rule text + dynamic memory cites.
    for trigger in triggers:
        sys.stderr.write(_emit_for_trigger(trigger, directory) + "\n")

    # Merge-briefing escalation (issue #797): for the `merge` trigger only,
    # block the merge when the preceding assistant text lacks the Pre-Merge
    # Reporting briefing. Runs in default mode — this is the whole point of the
    # gate (advisory stderr alone did not stop the recurrence). The stderr
    # advisories above are still emitted so the agent sees the rule text too.
    if TRIGGER_MERGE in triggers:
        reason = _merge_escalation_reason(payload)
        if reason is not None:
            emit_decision("deny", reason)
            return 0

    # Strict mode: block unless PRAXIS_MOMENTUM_ACK=1 is also present.
    if os.environ.get("PRAXIS_MOMENTUM_STRICT") == "1":
        if os.environ.get("PRAXIS_MOMENTUM_ACK") != "1":
            sys.stderr.write(
                f"{PREFIX} STRICT MODE — set PRAXIS_MOMENTUM_ACK=1 to proceed\n"
            )
            return 2

    return 0




if __name__ == "__main__":
    sys.exit(main())
