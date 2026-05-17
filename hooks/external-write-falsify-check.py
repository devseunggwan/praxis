#!/usr/bin/env python3
"""PreToolUse advisory: warn before posting hypothesis-form claims to external surfaces.

Public/shared-state writes — PR comments, issue bodies, Slack messages, Notion
pages — train downstream readers (review bots, teammates) on the published
facts. Posting hypothesis-stage thinking to these surfaces creates retraction
and noise cost when the hypothesis turns out to be false.

This hook detects:
  - Bash calls invoking `gh issue/pr comment`, `gh issue/pr create` with a body
    flag (`--body`, `-b`, `--body-file`, `-F`)
  - MCP tool calls writing to chat / docs surfaces (slack send/post,
    notion create_page / update_page)
  - Write tool calls to staging paths (/tmp/*-issue-*.md, /tmp/*-pr-*.md,
    .omc/plans/*.md) when cluster-approval language is found in recent user
    messages (issue #276)

When the body contains hypothesis markers (might / could / potentially / appears
to / is failing / 가설 / 추정), it emits a stderr advisory reminding the user
to verify each factual claim with executed evidence before posting.

Additionally, when the body contains author-exempt claim shapes (mapping table
rows or bash code blocks with unverified identifiers) and no verification call
(gh label list / DESCRIBE / <binary> --help) is found in the recent transcript,
it emits a separate advisory (issue #183).

Exits 0 by default — this is an advisory, not a block. Set
`PRAXIS_EXTERNAL_WRITE_STRICT=1` to convert hypothesis-marker detection into a
hard block (exit 2). Set `PRAXIS_AUTHOR_EXEMPT_STRICT=1` to convert author-exempt
detection into a hard block (exit 2). Set `PRAXIS_CLUSTER_APPROVAL_STRICT=1` to
convert cluster-approval staging detection into a hard block (exit 2).

Uses shlex tokenization (same approach as block-gh-state-all.py / side-effect-scan.py)
so that pattern references inside quoted strings, echo arguments, or comments
are not mistakenly flagged.
"""
from __future__ import annotations

import json
import os
import re
import sys

# Resolve sibling `_hook_utils.py` regardless of cwd at invocation time.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)


# ---------------------------------------------------------------------------
# Detection — heuristic markers
# ---------------------------------------------------------------------------

# English hypothesis markers — conservative list to reduce false positives.
HYPOTHESIS_MARKERS_EN = (
    "might ", "could be", "could fail", "could break",
    "potentially", "potential ",
    "appears to", "seems to",
    "likely ", "suspected", "hypothesis",
    "is failing", "is broken",
    "may have", "may be ",
)
HYPOTHESIS_MARKERS_KO = (
    "가설", "추정", "추측", "가능성", "의심됨", "의심된다",
)


# ---------------------------------------------------------------------------
# Bash gh detection
# ---------------------------------------------------------------------------

GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})

# `gh <obj> <sub>` pairs that write to external surfaces.
GH_WRITE_SUBCOMMANDS = frozenset({
    ("issue", "comment"),
    ("pr", "comment"),
    ("issue", "create"),
    ("pr", "create"),
    ("issue", "edit"),
    ("pr", "edit"),
    ("pr", "review"),  # accepts --body / -b / --body-file / -F; posts public review comment
})

GH_BODY_FLAGS_WITH_ARG = frozenset({"-b", "--body", "-F", "--body-file"})


def _resolve_body(flag: str, value: str) -> str:
    """Read body content. For --body-file, read file contents (best effort)."""
    if flag in {"-F", "--body-file"}:
        try:
            with open(value, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""  # treat unreadable file as empty body — advisory-only hook
    return value


def _extract_gh_body(argv: list[str]) -> str | None:
    """Pull body text from --body / --body-file in a gh argv. None if absent.

    `gh issue/pr comment` and friends only accept body via flags (--body / -b
    / --body-file / -F per `gh <subcmd> --help`); positional form
    `gh issue comment <num> "body"` is rejected by gh itself with
    `accepts 1 arg(s)`. Detecting positional shape would only add noise on
    already-invalid invocations, so this extractor is flag-only.
    """
    for i, tok in enumerate(argv):
        if "=" in tok:
            key, _, val = tok.partition("=")
            if key in GH_BODY_FLAGS_WITH_ARG:
                return _resolve_body(key, val)
            continue
        if tok in GH_BODY_FLAGS_WITH_ARG and i + 1 < len(argv):
            return _resolve_body(tok, argv[i + 1])
    return None


def _is_gh_external_write(argv: list[str]) -> bool:
    """Return True iff argv invokes a gh subcommand that writes to a public surface."""
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
    obj, sub = argv[i], argv[i + 1]
    return (obj, sub) in GH_WRITE_SUBCOMMANDS


# ---------------------------------------------------------------------------
# MCP detection
# ---------------------------------------------------------------------------

MCP_EXTERNAL_WRITE_PATTERNS = (
    re.compile(r".*slack.*send.*", re.IGNORECASE),
    re.compile(r".*slack.*post.*", re.IGNORECASE),
    re.compile(r".*slack.*update.*", re.IGNORECASE),
    re.compile(r".*notion.*create.*page.*", re.IGNORECASE),
    re.compile(r".*notion.*update.*page.*", re.IGNORECASE),
    re.compile(r".*notion.*append.*block.*", re.IGNORECASE),
)


def _is_mcp_external_write(tool_name: str) -> bool:
    return any(p.match(tool_name) for p in MCP_EXTERNAL_WRITE_PATTERNS)


# Leaf keys whose value is body content (collect descendant strings).
BODY_LEAF_KEYS = frozenset({
    "text", "content", "body", "message", "page_content",
})

# Container keys that wrap block/rich-text lists. Inside a container we
# traverse wrapper dicts (paragraph, heading_1, section, ...) until we hit
# a body leaf or another container.
BODY_CONTAINER_KEYS = frozenset({
    "children", "blocks", "rich_text",
})


# [PR #179] P2: MCP payloads nest body text under shapes like Notion's
# `children[].paragraph.rich_text[].text.content` (3 levels deep) and Slack's
# `blocks[].text.text`. A flat top-level scan missed both. An unconstrained
# recursive walk (earlier revision) over-collected siblings — page property
# titles like `properties.Name.title[].text.content` would surface and trip
# markers like "potential" / "likely" inside legitimate titles. So entry into
# body subtrees is gated: top level accepts BODY_LEAF_KEYS or BODY_CONTAINER_KEYS;
# inside containers, wrapper dicts (paragraph / section / heading_X) are
# transparent — recursion continues until a leaf is reached.
def _collect_under_leaf(node, parts: list[str]) -> None:
    """Collect every string descendant. Called once a leaf key is entered."""
    if isinstance(node, str):
        parts.append(node)
    elif isinstance(node, list):
        for item in node:
            _collect_under_leaf(item, parts)
    elif isinstance(node, dict):
        for val in node.values():
            _collect_under_leaf(val, parts)


def _walk_in_container(node, parts: list[str]) -> None:
    """Inside `children` / `blocks` / `rich_text`: traverse wrapper dicts
    (paragraph / section / heading_X) and nested containers transparently,
    switching to leaf-collection only at body keys."""
    if isinstance(node, list):
        for item in node:
            _walk_in_container(item, parts)
    elif isinstance(node, dict):
        for key, val in node.items():
            if isinstance(key, str) and key.lower() in BODY_LEAF_KEYS:
                _collect_under_leaf(val, parts)
            else:
                _walk_in_container(val, parts)


def _extract_mcp_body(tool_input: dict) -> str:
    """Body extraction from MCP tool_input gated by recognized entry points.

    At the top level only BODY_LEAF_KEYS and BODY_CONTAINER_KEYS are
    entered — sibling keys like `properties`, `parent`, `channel`, `title`
    are ignored so that property metadata (e.g. Notion page property
    titles under `properties.Name.title`) does not surface as body.
    """
    parts: list[str] = []
    for key, val in tool_input.items():
        if not isinstance(key, str):
            continue
        kl = key.lower()
        if kl in BODY_LEAF_KEYS:
            _collect_under_leaf(val, parts)
        elif kl in BODY_CONTAINER_KEYS:
            _walk_in_container(val, parts)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Hypothesis marker scan
# ---------------------------------------------------------------------------

def _has_hypothesis_marker(body: str) -> bool:
    if not body:
        return False
    lower = body.lower()
    if any(marker in lower for marker in HYPOTHESIS_MARKERS_EN):
        return True
    return any(marker in body for marker in HYPOTHESIS_MARKERS_KO)


# ---------------------------------------------------------------------------
# Author-exempt claim-shape detection (issue #183)
# ---------------------------------------------------------------------------
# Detects unverified identifiers the agent authored itself: mapping table rows
# with CLI flags or label names, and bash code blocks with column/table names.
# No hypothesis hedging language is required — the pattern is structural.
#
# Verification is category-specific: a gh label list only clears label claims;
# a DESCRIBE only clears schema/column claims; --help only clears flag claims.
# Mixed bodies require matching evidence per category (Codex P2 fix).

# Identifier categories — used to match identifiers against verification commands.
_CAT_FLAG   = "flag"    # e.g. --cli-flag
_CAT_LABEL  = "label"   # e.g. type:docs
_CAT_SCHEMA = "schema"  # e.g. snake_col, schema.table, `backtick-id`

# Markdown table data row: at least two pipe-delimited cells.
_MD_TABLE_ROW_RE = re.compile(r"^\|(.+\|)+", re.MULTILINE)

# Identifier patterns checked inside table cells (high specificity).
_CELL_FLAG_RE    = re.compile(r"--[a-z][a-z0-9-]{1,30}")
_CELL_LABEL_RE   = re.compile(r"\b[a-z][a-z0-9-]+:[a-z][a-z0-9][a-z0-9-]*\b")
_CELL_BACKTICK_RE = re.compile(r"`[a-z][a-z0-9_-]{2,}`")

# Bash / SQL / any language code block.
_CODE_BLOCK_RE = re.compile(r"```\w*\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# Inside code blocks: add snake_case column names and schema-qualified tables.
_CODE_SNAKE_RE    = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*){1,}\b")
_CODE_QUALIFIED_RE = re.compile(r"\b[a-z][a-z0-9_]+\.[a-z][a-z0-9_]+\b")

# Verification regex patterns per category.
_VERIF_BY_CAT: dict[str, tuple[re.Pattern, ...]] = {
    _CAT_FLAG: (
        re.compile(r"\bgh\s+\w[\w-]*\s+(?:--help|-h)\b", re.IGNORECASE),
        re.compile(r"\b\w[\w.-]{1,}\s+--help\b", re.IGNORECASE),
    ),
    _CAT_LABEL: (
        re.compile(r"\bgh\s+label\s+list\b", re.IGNORECASE),
    ),
    _CAT_SCHEMA: (
        re.compile(r"\bDESCRIBE\s+\w", re.IGNORECASE),
        re.compile(r"\bSHOW\s+COLUMNS\b", re.IGNORECASE),
    ),
}

_TRANSCRIPT_SCAN_LINES = 400


def _is_separator_row(row: str) -> bool:
    """True for pure separator rows like |---|:---:|---| (no data)."""
    return bool(re.fullmatch(r"[\|\s\-:=+]+", row.strip()))


def _extract_categorized_identifiers(body: str) -> dict[str, list[str]]:
    """Return {category: [identifiers]} from mapping table cells and code blocks."""
    result: dict[str, list[str]] = {_CAT_FLAG: [], _CAT_LABEL: [], _CAT_SCHEMA: []}

    # Markdown table data rows
    for m in _MD_TABLE_ROW_RE.finditer(body):
        row = m.group(0)
        if _is_separator_row(row):
            continue
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        for cell in cells:
            if not cell:
                continue
            result[_CAT_FLAG].extend(_CELL_FLAG_RE.findall(cell))
            result[_CAT_LABEL].extend(_CELL_LABEL_RE.findall(cell))
            result[_CAT_SCHEMA].extend(_CELL_BACKTICK_RE.findall(cell))

    # Code blocks (any language tag)
    for m in _CODE_BLOCK_RE.finditer(body):
        block = m.group(1)
        result[_CAT_FLAG].extend(_CELL_FLAG_RE.findall(block))
        result[_CAT_LABEL].extend(_CELL_LABEL_RE.findall(block))
        result[_CAT_SCHEMA].extend(_CELL_BACKTICK_RE.findall(block))
        result[_CAT_SCHEMA].extend(_CODE_SNAKE_RE.findall(block))
        result[_CAT_SCHEMA].extend(_CODE_QUALIFIED_RE.findall(block))

    return {k: v for k, v in result.items() if v}


def _recent_bash_commands(transcript_path: str) -> list[str]:
    """Return recent Bash command strings from the last N transcript JSONL lines."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    cmds: list[str] = []
    for line in lines[-_TRANSCRIPT_SCAN_LINES:]:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message") or {}
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for block in (msg.get("content") or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                inp = block.get("input") or {}
                cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                if isinstance(cmd, str) and cmd.strip():
                    cmds.append(cmd)
    return cmds


def _unverified_identifiers(
    categorized: dict[str, list[str]], commands: list[str]
) -> list[str]:
    """Return a sample of identifiers whose category has no matching verification.

    Each category is checked independently — a gh label list only clears label
    claims; a DESCRIBE only clears schema/column claims; --help only clears flag
    claims. Returns up to 2 identifiers per unverified category.
    """
    unverified: list[str] = []
    for cat, ids in categorized.items():
        patterns = _VERIF_BY_CAT.get(cat, ())
        cat_verified = any(
            pat.search(cmd)
            for cmd in commands
            for pat in patterns
        )
        if not cat_verified:
            unverified.extend(list(dict.fromkeys(ids))[:2])
    return unverified


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

ADVISORY_MESSAGE = (
    "REMINDER (External-Surface Write Falsification): hypothesis markers "
    "detected in body.\n"
    "Before posting, verify:\n"
    "  • Has each factual claim been verified by executed evidence "
    "(query output, test pass, log inspection)?\n"
    "  • Is your verification's own premise (key, filter, schema, "
    "dimensional layout) falsified?\n"
    "  • If the verification loop has not closed, write to /tmp/ or "
    ".omc/plans/ instead.\n"
    "Set PRAXIS_EXTERNAL_WRITE_STRICT=1 to convert this advisory into a "
    "hard block (exit 2).\n"
)

AUTHOR_EXEMPT_ADVISORY = (
    "REMINDER (External-Surface Write / Author-Exempt): body contains "
    "mapping table or code-block identifiers ({identifiers}) with no "
    "verification call found in recent transcript.\n"
    "Own-authored labels, columns, and flags are in scope — run "
    "gh label list / DESCRIBE / <binary> --help before publishing.\n"
    "Set PRAXIS_AUTHOR_EXEMPT_STRICT=1 to convert this advisory into a "
    "hard block (exit 2).\n"
)


# ---------------------------------------------------------------------------
# Cluster-approval language detection (issue #276)
# ---------------------------------------------------------------------------
# Detects the pattern: user approves a cluster of tasks in bulk ("all 4 together",
# "1+3 같이"), then the agent writes directly to a staging path without
# per-action surfacing. CLAUDE.md rule: "No Approval Transfer Across Companion PRs".

_USER_MSG_SCAN_COUNT = 5

# Patterns that indicate a cluster/bulk approval by the user.
CLUSTER_APPROVAL_PATTERNS = (
    # English — exact phrases from the issue spec
    re.compile(r"\b\d+\s+buckets?\s+together\b", re.IGNORECASE),
    re.compile(r"\ball\s+\d+\s+as\s+separate\b", re.IGNORECASE),
    re.compile(r"\bas\s+approved\s+above\b", re.IGNORECASE),
    re.compile(r"\bcluster\s+(?:i|we)\s+approved\b", re.IGNORECASE),
    # "all N ... approv*" within 50 chars (e.g. "all 4 approved", "all 4 go ahead")
    re.compile(r"\ball\s+\d+\b.{0,50}\bapprov\w*", re.IGNORECASE),
    # Korean — from issue spec
    re.compile(r"\d+개\s*모두"),
    re.compile(r"\d+\s*\+\s*\d+\s*같이"),
    re.compile(r"모두\s*승인"),
)

# Staging paths: intermediate draft files, not final external-surface targets.
STAGING_PATH_PATTERNS = (
    re.compile(r"/tmp/[^/\s]*-issue-[^/\s]*\.md$"),
    re.compile(r"/tmp/[^/\s]*-pr-[^/\s]*\.md$"),
    re.compile(r"\.omc/plans/[^/\s]*\.md$"),
)

CLUSTER_APPROVAL_ADVISORY = (
    "REMINDER (External-Surface Write / Cluster-Approval): cluster-approval "
    "language detected in a recent user message.\n"
    "Cluster approvals (\"all N together\", \"1+3 같이\", \"as approved above\") "
    "do NOT auto-transfer to per-action staging writes.\n"
    "Each child mutation (issue draft, PR body) requires its own explicit "
    "per-action surfacing via AskUserQuestion.\n"
    "Surface a dedicated AskUserQuestion for this specific action before "
    "writing to a staging path.\n"
    "Set PRAXIS_CLUSTER_APPROVAL_STRICT=1 to convert this advisory into a "
    "hard block (exit 2).\n"
)


def _recent_user_messages(transcript_path: str, count: int) -> list[str]:
    """Return text from the last `count` user messages in the transcript.

    Scans the last _TRANSCRIPT_SCAN_LINES JSONL entries in reverse so that
    the most-recent user messages are returned first (index 0 = most recent).
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    msgs: list[str] = []
    for line in reversed(lines[-_TRANSCRIPT_SCAN_LINES:]):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message") or {}
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            text = "\n".join(parts)
        else:
            continue
        if text.strip():
            msgs.append(text)
        if len(msgs) >= count:
            break
    return msgs


def _has_cluster_approval(user_messages: list[str]) -> bool:
    """Return True if any recent user message contains cluster-approval language."""
    for msg in user_messages:
        for pat in CLUSTER_APPROVAL_PATTERNS:
            if pat.search(msg):
                return True
    return False


def _is_staging_path(file_path: str) -> bool:
    """Return True if file_path matches a recognized staging path pattern."""
    if not file_path:
        return False
    return any(pat.search(file_path) for pat in STAGING_PATH_PATTERNS)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    tool_name = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input", {}) or {}
    transcript_path = payload.get("transcript_path", "") or ""

    # --- Collect all bodies from external write surfaces ---
    all_bodies: list[str] = []

    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
        if not command.strip():
            return 0
        command = command.replace("\\\n", " ")
        tokens = safe_tokenize(command)
        if not tokens:
            return 0
        for argv in iter_command_starts(tokens):
            if _is_gh_external_write(argv):
                candidate = _extract_gh_body(argv)
                if candidate is not None:
                    all_bodies.append(candidate)
    elif _is_mcp_external_write(tool_name):
        mcp_body = _extract_mcp_body(tool_input)
        if mcp_body:
            all_bodies.append(mcp_body)
    elif tool_name == "Write":
        # --- Check 3: cluster-approval + staging path (issue #276) ---
        file_path = tool_input.get("file_path", "") or ""
        if _is_staging_path(file_path):
            user_msgs = _recent_user_messages(transcript_path, _USER_MSG_SCAN_COUNT)
            if _has_cluster_approval(user_msgs):
                sys.stderr.write(CLUSTER_APPROVAL_ADVISORY)
                if os.environ.get("PRAXIS_CLUSTER_APPROVAL_STRICT") == "1":
                    return 2
        return 0
    else:
        return 0

    if not all_bodies:
        return 0

    exit_code = 0

    # --- Check 1: hypothesis markers (existing behavior) ---
    for b in all_bodies:
        if _has_hypothesis_marker(b):
            sys.stderr.write(ADVISORY_MESSAGE)
            if os.environ.get("PRAXIS_EXTERNAL_WRITE_STRICT") == "1":
                exit_code = 2
            break

    # --- Check 2: author-exempt claim-shape (issue #183) ---
    combined = "\n".join(all_bodies)
    categorized = _extract_categorized_identifiers(combined)
    if categorized:
        commands = _recent_bash_commands(transcript_path)
        unverified = _unverified_identifiers(categorized, commands)
        if unverified:
            sample = ", ".join(list(dict.fromkeys(unverified))[:3])
            sys.stderr.write(AUTHOR_EXEMPT_ADVISORY.format(identifiers=sample))
            if os.environ.get("PRAXIS_AUTHOR_EXEMPT_STRICT") == "1":
                exit_code = 2

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
