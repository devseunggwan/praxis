#!/usr/bin/env python3
"""PreToolUse memory hint: surface relevant memory file descriptions.

Scans the user-scoped memory directory for `*.md` files whose YAML
frontmatter declares `hookable: true` plus a `hookKeywords: [...]` list,
tokenizes the inbound tool payload, and emits up to 3
`[memory:hookable] {filename} — {description}` lines to stderr per match
(plus an `...and N more` summary line when truncated).

Supported PreToolUse events (matched via `hooks.json` matcher) — Bash,
Edit, Write, NotebookEdit, AskUserQuestion. Each memory opts into specific
events via the optional `hookEvents: [...]` frontmatter field (default
`[Bash]` for backward compatibility). Tokenization differs per event:

  - Bash: shlex via `safe_tokenize` (whole-token, quoted multi-words
          preserved as a single non-matching token — see AC-10)
  - Other events: free-text regex tokenizer that preserves identifier
          tokens (e.g. `cmux-delegate` stays as one token)

Always exits 0. Never blocks. Never asks. The signal is purely
attention-shifting for the LLM's next reasoning step. Co-firing with
`block-gh-state-all` (exit 2) and `side-effect-scan` (`ask`) is intentional
— PreToolUse hooks run in parallel; a memory hint may clarify why a
sibling hook blocked the command.

Memory directory discovery:
  1. `PRAXIS_MEMORY_DIR` env var (when set + exists)
  2. fallback to `${CLAUDE_CONFIG_DIR:-~/.claude}/projects/{slugified-cwd}/memory/`
     (slugify rule: replace every non-alphanumeric character with `-`,
     per-character — see `hooks/_lib/_memory_dir.py`, the shared SoT)
  3. missing dir → exit 0 silently

Frontmatter parser is pure regex (no PyYAML dependency). Supported shapes:
  - `hookable: true|True|TRUE|yes|Yes` (other values silently skip the file)
  - `hookKeywords: [a, b, "c d"]` (flat single-line list only;
                                  multi-line / flow-mapping NOT supported)
  - `hookEvents: [Bash, Edit, ...]` (flat single-line list; default `[Bash]`)
  - `description: anything` (optional; emitted after em-dash when present)
Any parse error within a memory skips that memory only — never the hook.
"""
from __future__ import annotations

import os
import re
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from _memory_dir import resolve_memory_dir  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402


HIT_LIMIT = 3
TRUTHY_VALUES = {"true", "yes"}

SUPPORTED_EVENTS = ("Bash", "Edit", "Write", "NotebookEdit", "AskUserQuestion")
DEFAULT_EVENTS = ("Bash",)

FRONTMATTER_FENCE = re.compile(r"^---\s*$", re.MULTILINE)
HOOKABLE_RE = re.compile(r"^\s*hookable\s*:\s*(.+)$", re.MULTILINE)
KEYWORDS_RE = re.compile(r"^\s*hookKeywords\s*:\s*(.+)$", re.MULTILINE)
EVENTS_RE = re.compile(r"^\s*hookEvents\s*:\s*(.+)$", re.MULTILINE)
DESCRIPTION_RE = re.compile(r"^\s*description\s*:\s*(.+)$", re.MULTILINE)

# Free-text payload tokenizer: ASCII identifier-with-dashes OR any unicode
# word. The first alternative uses an ASCII-only character class so a token
# like `push할까요` splits into `['push', '할까요']` (and not the unicode-`\w`
# greedy match `['push할까요']`). The second alternative (`\w+`) is kept
# unicode-aware so Korean-only text still yields word tokens.
TEXT_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*|\w+", re.UNICODE)

# YAML inline comment: `#` preceded by whitespace (per YAML 1.2 spec). The
# `hookKeywords` list parser additionally tolerates anything after the first
# `]`, which covers comments without leading whitespace.
INLINE_COMMENT_RE = re.compile(r"\s+#.*$")

# Control bytes (incl. ESC for ANSI escape sequences) shouldn't reach stderr —
# memory filenames flow through to log viewers / terminals.
CONTROL_BYTES_RE = re.compile(r"[\x00-\x1f\x7f]")


def _strip_inline_comment(value: str) -> str:
    return INLINE_COMMENT_RE.sub("", value)


def _sanitize(text: str) -> str:
    return CONTROL_BYTES_RE.sub("?", text)


def parse_frontmatter(raw: str) -> dict | None:
    """Extract the first `---`-fenced block. Return None if absent."""
    matches = list(FRONTMATTER_FENCE.finditer(raw))
    if len(matches) < 2:
        return None
    start = matches[0].end()
    end = matches[1].start()
    block = raw[start:end]

    hookable_match = HOOKABLE_RE.search(block)
    if not hookable_match:
        return None
    hookable_value = (
        _strip_inline_comment(hookable_match.group(1))
        .strip()
        .lower()
        .strip('"\'')
    )
    if hookable_value not in TRUTHY_VALUES:
        return None

    keywords_match = KEYWORDS_RE.search(block)
    if not keywords_match:
        return None
    keywords_raw = keywords_match.group(1).strip()
    if not keywords_raw.startswith("["):
        # Scalar form (`hookKeywords: kubectl`) is rejected per AC-22.
        return None
    # Find the first `]` so a trailing inline comment doesn't break parse.
    # `[a, b] # comment` is the natural YAML shape — anything after the
    # closing bracket is treated as comment/garbage.
    close_idx = keywords_raw.find("]")
    if close_idx == -1:
        return None
    inner = keywords_raw[1:close_idx].strip()
    if not inner:
        return None
    keywords = [
        item.strip().strip('"\'')
        for item in inner.split(",")
    ]
    keywords = [k for k in keywords if k]
    if not keywords:
        return None

    description_match = DESCRIPTION_RE.search(block)
    description = ""
    if description_match:
        description = (
            _strip_inline_comment(description_match.group(1))
            .strip()
            .strip('"\'')
        )

    events: tuple[str, ...] = DEFAULT_EVENTS
    events_match = EVENTS_RE.search(block)
    if events_match:
        events_raw = events_match.group(1).strip()
        if events_raw.startswith("["):
            close_idx = events_raw.find("]")
            if close_idx != -1:
                inner = events_raw[1:close_idx].strip()
                if inner:
                    parsed_events = tuple(
                        e.strip().strip('"\'')
                        for e in inner.split(",")
                        if e.strip()
                    )
                    filtered = tuple(
                        e for e in parsed_events if e in SUPPORTED_EVENTS
                    )
                    if filtered:
                        events = filtered

    return {
        "keywords": keywords,
        "description": description,
        "events": events,
    }


def index_memories(directory: str) -> list[tuple[str, dict, float]]:
    """Return [(filename, parsed, mtime), ...] for every hookable memory."""
    out: list[tuple[str, dict, float]] = []
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
            parsed = parse_frontmatter(raw)
        except Exception:
            parsed = None
        if not parsed:
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        out.append((name, parsed, mtime))
    return out


def collect_command_tokens(command: str) -> set[str]:
    """Flatten command tokens after splitting at shell separators + stripping prefixes."""
    tokens = safe_tokenize(command)
    if not tokens:
        return set()
    flat: set[str] = set()
    for argv in iter_command_starts(tokens):
        for tok in strip_prefix(argv):
            flat.add(tok)
    return flat


def tokenize_text_payload(text: str) -> set[str]:
    """Whole-token split for non-Bash payloads (file paths, content, prompts)."""
    if not text:
        return set()
    return set(TEXT_TOKEN_RE.findall(text))


def extract_event_payload(payload: dict) -> tuple[str, str]:
    """Map (tool_name, tool_input) → (event_name, free-text blob for tokenization).

    Returns ('', '') when tool is unsupported or input shape unexpected.
    """
    tool = payload.get("tool_name", "") or ""
    if tool not in SUPPORTED_EVENTS:
        return "", ""
    tool_input = payload.get("tool_input", {}) or {}
    if not isinstance(tool_input, dict):
        return "", ""

    if tool == "Bash":
        return "Bash", str(tool_input.get("command", "") or "")

    if tool == "Edit":
        parts = [
            tool_input.get("file_path", ""),
            tool_input.get("old_string", ""),
            tool_input.get("new_string", ""),
        ]
        return "Edit", "\n".join(str(p) for p in parts if p)

    if tool == "Write":
        parts = [
            tool_input.get("file_path", ""),
            tool_input.get("content", ""),
        ]
        return "Write", "\n".join(str(p) for p in parts if p)

    if tool == "NotebookEdit":
        parts = [
            tool_input.get("notebook_path", ""),
            tool_input.get("new_source", ""),
        ]
        return "NotebookEdit", "\n".join(str(p) for p in parts if p)

    if tool == "AskUserQuestion":
        questions = tool_input.get("questions", []) or []
        chunks: list[str] = []
        if isinstance(questions, list):
            for q in questions:
                if not isinstance(q, dict):
                    continue
                chunks.append(str(q.get("question", "") or ""))
                chunks.append(str(q.get("header", "") or ""))
                options = q.get("options", []) or []
                if isinstance(options, list):
                    for opt in options:
                        if isinstance(opt, dict):
                            chunks.append(str(opt.get("label", "") or ""))
                            chunks.append(str(opt.get("description", "") or ""))
        return "AskUserQuestion", "\n".join(c for c in chunks if c)

    return "", ""


def find_hits(
    indexed: list[tuple[str, dict, float]],
    cmd_tokens: set[str],
    event: str,
) -> list[tuple[str, dict, float]]:
    """Return memories whose hookKeywords match any token AND hookEvents contains `event`.

    Whole-token equality (case-sensitive). Quoted multi-word strings shlex
    parses as a single token (e.g. `echo "use kubectl"` → `use kubectl`),
    so `kubectl` keyword does NOT match — see AC-10. Substring matching
    would create false positives across `kubectl-prod` vs `kubectl`.
    """
    hits: list[tuple[str, dict, float]] = []
    for name, parsed, mtime in indexed:
        if event not in parsed.get("events", DEFAULT_EVENTS):
            continue
        if any(keyword in cmd_tokens for keyword in parsed["keywords"]):
            hits.append((name, parsed, mtime))
    return hits


def emit_hits(hits: list[tuple[str, dict, float]]) -> None:
    """Print up to HIT_LIMIT hit lines + summary, sorted by mtime desc."""
    hits.sort(key=lambda h: h[2], reverse=True)
    for name, parsed, _ in hits[:HIT_LIMIT]:
        safe_name = _sanitize(name)
        description = _sanitize(parsed["description"])
        if description:
            sys.stderr.write(f"[memory:hookable] {safe_name} — {description}\n")
        else:
            sys.stderr.write(f"[memory:hookable] {safe_name}\n")
    extra = len(hits) - HIT_LIMIT
    if extra > 0:
        sys.stderr.write(f"[memory:hookable] ...and {extra} more\n")


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0  # fail-open on malformed stdin

    event, text = extract_event_payload(payload)
    if not event or not text.strip():
        return 0

    if event == "Bash":
        # Backslash line continuation → single space so tokenizer sees one line
        text = text.replace("\\\n", " ")

    directory = resolve_memory_dir()
    if not directory:
        return 0

    indexed = index_memories(directory)
    if not indexed:
        return 0

    if event == "Bash":
        cmd_tokens = collect_command_tokens(text)
    else:
        cmd_tokens = tokenize_text_payload(text)
    if not cmd_tokens:
        return 0

    hits = find_hits(indexed, cmd_tokens, event)
    if hits:
        emit_hits(hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
