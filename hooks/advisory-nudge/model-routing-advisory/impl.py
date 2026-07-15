#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: nudge when a delegation's `--model` tier does not
match the task signal implied by the command text.

Rule (AGENTS.md `Model Routing Rules`, absorbed from the user-level CLAUDE.md
routing block this hook replaces): cost-aware model selection is not optional —
`find/search/list/... → haiku`, `implement/fix/... → sonnet`,
`architect/design/security/... → opus`. A worker spawned with `--model opus`
for a file-search task wastes budget; one spawned with `--model haiku` for an
architecture task is under-powered. When a delegation command carries an
explicit `--model <tier>`, compare the chosen tier against the tier implied by
the task keywords and nudge on a mismatch.

Scope (v1): Bash delegation commands only (`cmux ... claude -p ... --model`,
`cmux-delegate --model`, any command with `--model <bare-claude-tier>`). The
`Task`/`Agent` tool is deliberately out of scope for now: its tier is implied by
`subagent_type` with only an OPTIONAL `model` override, so there is rarely an
explicit chosen tier to compare — a follow-up may add it (see spec.md).

Precision: only bare Claude tier names (`haiku` / `sonnet` / `opus`, optionally
`claude:<tier>`) are classified. A non-Claude provider (`codex:` / `gemini:`) or
a full model id (`claude-opus-4-8`) yields no comparable tier → SILENT. A command
with no task-signal keyword yields no implied tier → SILENT. Advisory only
(stderr, exit 0 always) — a deliberate tier choice is legitimate, so this nudges
rather than blocks.

Detection is a raw-string scan, NOT structural tokenization: the chosen tier and
the task prompt are typically NESTED inside a quoted `--command "claude -p '...'
--model opus"`, which `safe_tokenize` keeps opaque as one token. Scanning the raw
string with anchored word-boundary patterns handles the nested and flat forms
uniformly; the trade-off (quoting/heredoc boundaries are not respected) is
acceptable for a fail-open nudge and documented in spec.md.

Silent cases (no output):
  - non-Bash tool, empty command
  - no `--model` with a bare Claude tier value
  - no task-signal keyword in the command → no implied tier
  - chosen tier == implied tier
  - opt-out marker `# [model-routing-ack]` in the command
  - PRAXIS_SKIP_MODEL_ROUTING=1 in the environment
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402

OPT_OUT_MARKER = "# [model-routing-ack]"

# Tier ordering: a higher rank means a more capable / costlier model.
_TIER_RANK: dict[str, int] = {"haiku": 1, "sonnet": 2, "opus": 3}

# Task-signal keywords per implied tier (verbatim from the Model Routing Rules
# auto-detect table). Precedence is opus > sonnet > haiku: the most-capable
# signal present wins, matching "when in doubt, the harder sub-signal governs".
_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("opus", ("architect", "design", "security", "incident")),
    ("sonnet", ("implement", "add", "fix", "test", "review", "refactor")),
    ("haiku", ("find", "search", "list", "status", "cleanup", "rename", "move")),
)
# NOTE: the two REGEX opus signals from the Model Routing Rules — `debug.*race`
# and `system.*design` — are intentionally NOT modeled. A bare `debug` is a
# sonnet-level task (only race-condition debugging is opus), so promoting
# standalone `debug` would false-surface ordinary debugging; a literal-keyword
# hook cannot express the `.*race` proximity without an NLP parser. Both are
# documented false-negatives (silent), not covered here.

# `--model <val>` / `--model=<val>` — capture the raw value token; the leading
# tier-shaped run is extracted in `_chosen_tier` so trailing shell punctuation
# (`--model opus; echo x` → `opus`) does not defeat classification.
_MODEL_RE = re.compile(r"--model[=\s]+(\S+)")
_TIER_TOKEN_RE = re.compile(r"[a-z0-9:_-]+")


def _chosen_tier(command: str) -> str | None:
    """Bare Claude tier named by the first `--model`, or None if not comparable.

    `opus` / `sonnet` / `haiku` and `claude:<tier>` classify. A non-Claude
    provider prefix or an unrecognized value (full model id, provider model)
    returns None — the hook cannot compare it, so it stays silent.

    Only the FIRST `--model` is considered — the documented delegation forms carry
    a single flag. A prompt that itself contains a literal `--model <tier>` before
    the real flag (`-p "explain --model opus" --model sonnet`) is a known misread
    (see spec.md "Chosen tier"); structural arg parsing is deliberately avoided.
    """
    m = _MODEL_RE.search(command)
    if not m:
        return None
    raw = m.group(1).strip("\"'").lower()
    # Keep only the leading tier-shaped run so trailing shell punctuation
    # (`opus;`, `opus,sonnet`, `opus)`) does not defeat the `in _TIER_RANK` check.
    tok = _TIER_TOKEN_RE.match(raw)
    if not tok:
        return None
    raw = tok.group(0)
    if ":" in raw:
        provider, _, model = raw.partition(":")
        if provider != "claude":
            return None
        raw = model
    return raw if raw in _TIER_RANK else None


def _implied_tier(command: str) -> tuple[str, str] | None:
    """(tier, matched-keyword) implied by task signals, or None if no signal.

    Keywords match on ASCII-plus-hyphen boundaries — `(?<![a-z0-9_-])kw(?![a-z0-9_-])`
    on the lower-cased command — so a keyword fires only as a standalone word:
    `move` ∉ `remove`, `search` ∉ `research`, `add` ∉ `address` (intra-word), AND
    `add` ∉ `--add-dir`, `test` ∉ `test-utils` (flag names / hyphenated path
    segments) — the hyphen/digit/underscore in the boundary class stops a CLI flag
    or path token from hijacking the implied tier. opus signals are tested first so
    the costliest implied tier wins when multiple signals co-occur.

    Residual boundary (documented in spec.md): a signal word standing alone inside
    another flag's quoted VALUE — e.g. `--append-system-prompt "you review code"` —
    is still scanned and can nudge, because the scan is over the whole command, not
    just the `-p` prompt. Accepted as a rare false-surface on an exit-0 nudge.
    """
    low = command.lower()
    for tier, keywords in _SIGNALS:
        for kw in keywords:
            if re.search(rf"(?<![a-z0-9_-]){re.escape(kw)}(?![a-z0-9_-])", low):
                return tier, kw
    return None


def _emit(chosen: str, implied: str, keyword: str) -> None:
    direction = (
        "over-powered (costlier than the task needs)"
        if _TIER_RANK[chosen] > _TIER_RANK[implied]
        else "under-powered (the task signal needs more)"
    )
    sys.stderr.write(
        "[model-routing] "
        f"--model {chosen} looks {direction}: the task keyword "
        f"'{keyword}' implies {implied} "
        f"(find/search → haiku, implement/fix → sonnet, "
        f"architect/design/security → opus).\n"
        "Confirm the tier is intended, or adjust `--model`. "
        f"Ack with `{OPT_OUT_MARKER}` in the command, or set "
        "PRAXIS_SKIP_MODEL_ROUTING=1 to silence.\n"
    )


@fail_open
def main() -> int:
    if os.environ.get("PRAXIS_SKIP_MODEL_ROUTING") == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "") or ""
    if not command.strip() or OPT_OUT_MARKER in command:
        return 0

    chosen = _chosen_tier(command)
    if chosen is None:
        return 0

    implied = _implied_tier(command)
    if implied is None:
        return 0
    implied_tier, keyword = implied

    if chosen != implied_tier:
        _emit(chosen, implied_tier, keyword)
    return 0


if __name__ == "__main__":
    sys.exit(main())
