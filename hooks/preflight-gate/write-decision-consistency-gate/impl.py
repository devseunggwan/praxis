#!/usr/bin/env python3
"""PreToolUse guard: block a Write/Edit whose Decisions block also carries a
constraint, unless the author states the two do not contradict.

## Why (issue #905)

Three falsification gates exist, and none of them observes the `Write` surface:

| gate | surfaces scanned |
|---|---|
| `output-block-falsify-advisory`   | AskUserQuestion, Bash |
| `pre-output-falsification-gate`   | AskUserQuestion, Bash |
| `source-citation-probe-gate`      | gh write bodies, Slack/Notion MCP |

So the moment a delegation handoff document is authored — a `Write` of
`/tmp/cmux-delegate-*.md` — is structurally unguarded. On 2026-07-31 a handoff
`### Decisions` block carried both a decision ("add the `use_yn` + `daily_yn`
filters") and, three lines below it, the constraint that invalidates that very
decision ("the docstring states the AUTHORIZED/UNAUTHORIZED check is
deliberately omitted"). The author applied the constraint to a neighbouring
axis only and never cross-checked it against the decision. The wrong decision
shipped to a delegated agent as `Decisions`, and cost two PRs (one closed after
prod measurement showed it would exclude 25+ healthy connections, one failing
CI) before the whole track was cancelled.

CLAUDE.md already carries the prompt-layer rule ("Self-Plan Internal
Consistency Check"). It was loaded and did not fire. This hook moves the check
to the tool-call use-site for the half that a PreToolUse hook can reach.

## Block conditions

Fenced code blocks and inline code spans are removed from the body first, so a
document that merely *shows* a Decisions example — this hook's own spec — is
not read as authoring one.

The gate fires when **any** decision block in the remaining body satisfies all
of:

1. `tool_name` is `Write` or `Edit` (NOT `NotebookEdit`).
2. The block is introduced by a decision header (`## Decisions`,
   `### Decisions`, `### 결정`, ...).
3. Its interior — from the header to the next same-or-higher-level header —
   holds at least one list item AND at least one constraint marker
   (`범위가 아닙니다`, `out of scope`, `의도적 설계`, `by design`, ...) that is
   not negated (`is not out of scope` does not count).
4. Its interior holds no `Consistency:` line (exact prefix at line start).

Every block is checked, not just the first, and the clearing `Consistency:`
line must sit **inside the block it clears** — a line elsewhere in the document
exempts nothing.

## Env vars

- `PRAXIS_HOOK_BYPASS_DECISION_CONSISTENCY_GATE` — any non-empty value → bypass.

## Fail-open

Malformed / missing stdin JSON, unknown tool_name, absent content field, or any
uncaught exception → exit 0.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402

TARGET_TOOLS = frozenset({"Write", "Edit"})

BYPASS_ENV = "PRAXIS_HOOK_BYPASS_DECISION_CONSISTENCY_GATE"

# `## Decisions` / `### Decisions` / `### 결정` / `## 결정 사항`, up to 3 leading spaces.
_DECISION_HEADER_RE = re.compile(
    r"^ {0,3}(?P<hashes>#{2,6})\s*(?:Decisions?|결정)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)

_ANY_HEADER_RE = re.compile(r"^ {0,3}(?P<hashes>#{1,6})\s+\S", re.MULTILINE)

# A markdown list item (`- `, `* `, `1. `) — evidence the block holds discrete decisions.
_LIST_ITEM_RE = re.compile(r"^ {0,3}(?:[-*+]|\d+\.)\s+\S", re.MULTILINE)

# Constraint / out-of-scope / deliberate-design phrasings. Substring match on the
# lowercased block interior; Korean forms are matched as-is.
CONSTRAINT_MARKERS: tuple[str, ...] = (
    "범위가 아닙니다",
    "범위 밖",
    "범위밖",
    "건드리지",
    "의도적 설계",
    "의도된 설계",
    "하지 않는다",
    "하지 않습니다",
    "별도 논의",
    "out of scope",
    "not in scope",
    "by design",
    "deliberate",
    "do not touch",
    "must not",
)

CONSISTENCY_PREFIX = "Consistency:"

# A marker occurrence preceded by one of these is a negation ("Authorization is
# not out of scope") and states no constraint at all.
NEGATION_PREFIXES: tuple[str, ...] = (
    "is not ",
    "are not ",
    "was not ",
    "isn't ",
    "aren't ",
    "가 아닌 ",
    "이 아닌 ",
    "가 아니라 ",
    "이 아니라 ",
)

_FENCE_LINE_RE = re.compile(r"^ {0,3}(?:```|~~~)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def extract_content(tool_name: str, tool_input: dict[str, object]) -> str:
    """Return the body this call would write, or '' when there is nothing to scan."""
    key = "content" if tool_name == "Write" else "new_string"
    value = tool_input.get(key)
    return value if isinstance(value, str) else ""


def strip_code(body: str) -> str:
    """Blank out fenced blocks and inline code spans, preserving line count.

    A document that *shows* a Decisions block inside a fence — this hook's own
    spec.md — is documenting the pattern, not authoring one. Line count is
    preserved so nothing downstream needs to remap positions.
    """
    out: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if _FENCE_LINE_RE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else _INLINE_CODE_RE.sub("", line))
    return "\n".join(out)


def find_decision_blocks(body: str) -> list[str]:
    """Return the interior of EVERY decision block in the body.

    A block runs from just after its header to the next header at the same or
    higher level (fewer-or-equal `#`), or to end-of-body. Checking only the
    first block let a later, contradictory block through (codex review F1).
    """
    blocks: list[str] = []
    for match in _DECISION_HEADER_RE.finditer(body):
        level = len(match.group("hashes"))
        start = match.end()
        end = len(body)
        for nxt in _ANY_HEADER_RE.finditer(body, start):
            if len(nxt.group("hashes")) <= level:
                end = nxt.start()
                break
        blocks.append(body[start:end])
    return blocks


def has_constraint(block: str) -> bool:
    """True when the block states a constraint that is not negated."""
    lowered = block.lower()
    for marker in CONSTRAINT_MARKERS:
        pos = lowered.find(marker)
        while pos != -1:
            preceding = lowered[:pos]
            if not any(preceding.endswith(neg) for neg in NEGATION_PREFIXES):
                return True
            pos = lowered.find(marker, pos + 1)
    return False


def has_consistency_line(block: str) -> bool:
    """True when the block itself carries the clearing line.

    Scoped to the block: a `Consistency:` line elsewhere in the document
    describes a different decision and exempts nothing (codex review F2).
    """
    return any(line.startswith(CONSISTENCY_PREFIX) for line in block.splitlines())


def _emit(file_path: str) -> None:
    emit_block(
        rule_name="DECISION-CONSISTENCY",
        why=(
            "this Write/Edit authors a Decisions block that also states a constraint "
            "(out-of-scope / deliberate-design), with no `Consistency:` line. A "
            "constraint recorded beside a decision is routinely applied to the axis it "
            "names and never cross-checked against the decision itself — that is the "
            "issue #905 failure, which shipped a wrong decision to a delegated agent"
        ),
        correct_path=(
            "add a line starting at column 0: "
            "`Consistency: <decision> vs <constraint> — <why they do not contradict>`. "
            "Check EVERY decision against EVERY constraint in the block, not only the "
            "axis the constraint names. If one does contradict, drop the decision "
            "instead of writing the line"
        ),
        bypass_env=BYPASS_ENV,
        reference=(
            "CLAUDE.md -> Self-Plan Internal Consistency Check; "
            "hooks/preflight-gate/write-decision-consistency-gate/spec.md"
        ),
    )
    if file_path:
        sys.stderr.write(f"\nTarget file: {file_path}\n")


@fail_open
def main() -> int:
    import os

    if os.environ.get(BYPASS_ENV, "").strip():
        return 0

    payload = read_payload()
    if payload is None:
        return 0

    tool_name = payload.get("tool_name", "") or ""
    if tool_name not in TARGET_TOOLS:
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    if not isinstance(tool_input, dict):
        return 0

    body = extract_content(tool_name, tool_input)
    if not body:
        return 0

    scannable = strip_code(body)

    for block in find_decision_blocks(scannable):
        if not _LIST_ITEM_RE.search(block):
            continue
        if not has_constraint(block):
            continue
        if has_consistency_line(block):
            continue
        _emit(str(tool_input.get("file_path") or "").strip())
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
