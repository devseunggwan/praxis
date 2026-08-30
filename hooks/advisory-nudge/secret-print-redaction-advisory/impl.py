#!/usr/bin/env python3
"""PreToolUse(Bash|Write|Edit) advisory: nudge masking when an agent-authored
verification script (or a live command) fetches a secret and routes the
fetched value to stdout unmasked.

Why this exists (issue #827):

An agent-authored verification script fetched real OAuth tokens via a
read-only secret-fetch CLI and printed them in its per-item log lines
(`[SKIP] <id>: <token>`), exposing ~8 real tokens into the session
transcript (non-reversible). This was the 2nd occurrence across 2 sessions —
a memory rule ("redact real secrets at the source of the agent's own
writes") already documented the exact pattern, but the failure happens at
*authoring time*, where behavioral memory is not retrieved. Memory-only
remediation has failed twice, so this promotes it to a structural advisory.

A runtime output scanner is infeasible: PreToolUse cannot see the secret
value (it is fetched at runtime, absent from the command text), and
PostToolUse fires only *after* the value is already in the transcript. The
only feasible gate is a PreToolUse heuristic on the *authoring* command —
which is what this hook is.

Detection design (2-signal AND gate):

  1. a secret/token **fetch invocation** (`aws secretsmanager
     get-secret-value`, `vault kv get`, ... — public CLIs builtin, internal
     ones via PRAXIS_SECRET_FETCH_CLIS, issue #1157), AND
  2. an **unmasked output flow** of the fetched value to stdout
     (`echo`/`printf`/`print`/`logger.*` of the captured variable, or the
     fetch substituted inline into an output command).

Either signal alone is silent — a bare fetch with no output flow, or an
echo of a variable that was never fetch-assigned, never fires.

2-tier scan:

  • **Live Bash command** — var-flow rule only: a capture
    (`VAR=$(fetch)`) followed by an unmasked sink (`echo "$VAR"`) fires;
    an inline no-capture substitution inside an output command
    (`echo "$(vault kv get -field=t s/x)"`) fires. A bare interactive fetch
    alone (`vault kv get secret/p`) is SILENT — it is the
    sanctioned read-only usage (global CLAUDE.md read-only prod calls).
  • **Authored content** (Write `content`, Edit `new_string`, heredoc
    bodies inside a Bash command, echo-appended strings) — var-flow rule
    PLUS bare no-capture fetch lines (incl. `| jq -r .SecretString`
    passthrough) fire, because the script's stdout lands in the transcript
    when it is later executed.

Masking recognized (→ silent): `${VAR:0:6}` / `${VAR: -4}` substring
expansions, python `[:6]` / `[-4:]` slices, `REDACTED`, `mask(`, `***`,
and digest sinks (`| wc`, `| sha*sum`, `| md5sum`, `head -c`).

File-type gate: Write/Edit targets (and heredoc/append redirect targets)
ending `.md`/`.txt`/`.rst` are NOT scanned — documentation legitimately
contains fetch+echo example text (this hook's own spec.md does), and
scanning it would make the hook self-triggering.

Parsing is raw-text, line-oriented regex — deliberately NOT
`safe_tokenize`: heredoc bodies are the *primary detection target* here
(the incident script was authored via a Bash heredoc), the exact opposite
of pipefail-advisory's heredoc-body exclusion.

Fail-open contract:

  • malformed JSON / unknown tool payload → exit 0
  • empty command / content → exit 0
  • any uncaught exception in the inner logic → swallowed, exit 0

Advisory only — writes to stderr, exits 0. Never blocks.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOK_DIR.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Detection enums / regexes
# ---------------------------------------------------------------------------

# Write/Edit targets (and heredoc/append redirect targets) that are
# documentation, not executable scripts — never scanned (self-trigger guard:
# this hook's own spec.md contains fetch+echo examples).
_SKIP_SUFFIXES = (".md", ".txt", ".rst")

# Secret/token fetch invocations. `aws ssm get-parameter` only counts with
# `--with-decryption` (the plaintext-revealing form); a default call returns
# the still-encrypted value. `get_secret_value(` covers python SDK reads
# (boto3 secretsmanager). Matched against the raw line AND a normalized copy
# (quotes/commas/brackets/parens → spaces) so subprocess list-form calls
# (`subprocess.check_output(["vault", "kv", "get", ...])`) are seen. The
# builtin list carries public tools only; internal CLIs extend it via
# PRAXIS_SECRET_FETCH_CLIS (see _extra_fetch_re, issue #1157).
_FETCH_RE = re.compile(
    r"\baws\s+secretsmanager\s+get-secret-value\b"
    r"|\baws\s+ssm\s+get-parameter\b(?=.*--with-decryption)"
    r"|\binfisical\s+(?:secrets\s+get|export)\b"
    r"|\bvault\s+(?:kv\s+get|read)\b"
    r"|\bop\s+(?:read|item\s+get)\b"
    r"|\bgh\s+auth\s+token\b"
    r"|\bkubectl\s+get\s+secrets?\b"
    r"|\bget_secret_value\s*\("
)

# Chars stripped for the normalized fetch-match copy (see _FETCH_RE note).
_NORM_STRIP_RE = re.compile(r"[\"'\[\](),]")


def _extra_fetch_re() -> re.Pattern[str] | None:
    """Compile PRAXIS_SECRET_FETCH_CLIS into extra fetch alternatives.

    Org/author-internal fetch CLIs (e.g. `hubctl token fetch`) stay out of
    the shipped regex — the builtin list carries public tools only. The env
    var takes comma-separated command phrases; each phrase matches its
    whitespace-separated tokens in order, word-bounded (issue #1157).
    """
    raw = os.environ.get("PRAXIS_SECRET_FETCH_CLIS", "")
    alts = []
    for entry in raw.split(","):
        tokens = entry.split()
        if tokens:
            alts.append(r"\b" + r"\s+".join(re.escape(t) for t in tokens) + r"\b")
    return re.compile("|".join(alts)) if alts else None


_EXTRA_FETCH_RE = _extra_fetch_re()

# Output sinks whose stdout lands in the transcript. bash sinks anchor at
# statement start (statements are pre-split on ;/&&/||/newline); python/js
# sinks match anywhere in the statement.
_SINK_RE = re.compile(
    r"^\s*(?:echo|printf)\b"
    r"|\bprint\s*\("
    r"|\blogger\.\w+\s*\("
    r"|\blogging\.\w+\s*\("
    r"|\bconsole\.log\s*\("
)

# Digest sinks — the secret bytes never reach the transcript, only a
# length/hash derived from them.
_DIGEST_RE = re.compile(r"\|\s*(?:wc|sha\w*sum|md5sum|b2sum|cksum)\b|\bhead\s+-c\b")

# Explicit masking markers — presence anywhere in the statement silences it.
_MASK_MARKER_RE = re.compile(r"REDACTED|\bmask\w*\s*\(|\*\*\*")

# Capture assignment: bash `VAR=$(fetch)` / python `var = fetch(...)`.
# `(?!=)` excludes `==` comparisons.
_ASSIGN_RE = re.compile(r"^\s*(?:export\s+|local\s+|readonly\s+)?([A-Za-z_]\w*)\s*=(?!=)")

# Statement splitter for the line-oriented scan. `|` is NOT a separator —
# digest detection needs the pipe kept within the statement.
_STMT_SPLIT_RE = re.compile(r"[;\n]|&&|\|\|")

# Stdout redirected to a file — the value lands in the file, not the
# transcript. `2>` (stderr-only, digit before `>`) and `>&2` (fd-dup, `&`
# after `>`) do NOT divert stdout and are excluded; `&>` diverts both and
# counts. Echo-appended script text is handled by the authored-doc path
# (with its own file-type gate), so suppressing the live fire here does not
# lose that case.
_FILE_REDIRECT_RE = re.compile(r"(?:^|[\s&])>{1,2}(?!&)\s*\S")

# Heredoc opener: `<<EOF`, `<< EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"`.
# `(?!<)` excludes the `<<<` here-string operator (a distinct bash operator,
# never a heredoc — mirrors pipefail-advisory's exclusion).
_HEREDOC_OPEN_RE = re.compile(r"<<(?!<)-?\s*([\"']?)(\w+)\1")

# Redirect target on a heredoc-opener or echo-append line (`> file`,
# `>> file`, `tee file`) — used for the file-type gate only.
_REDIRECT_TARGET_RE = re.compile(r"(?:>>?\s*|\btee\s+(?:-a\s+)?)(\S+)")

_ADVISORY_LIMIT = 3


# ---------------------------------------------------------------------------
# Statement-level scan
# ---------------------------------------------------------------------------


def _is_fetch(stmt: str) -> bool:
    """True if `stmt` invokes a secret fetch — raw or list-form normalized."""
    normalized = _NORM_STRIP_RE.sub(" ", stmt)
    if _FETCH_RE.search(stmt) or _FETCH_RE.search(normalized):
        return True
    if _EXTRA_FETCH_RE is None:
        return False
    return bool(
        _EXTRA_FETCH_RE.search(stmt) or _EXTRA_FETCH_RE.search(normalized)
    )


def _has_unmasked_ref(stmt: str, var: str) -> bool:
    """True if `stmt` references `var` in an unmasked form.

    bash forms: `$VAR` and `${VAR}` are unmasked; `${VAR:...}` / `${VAR#...}`
    / `${VAR%...}` / `${VAR/...}` are substring/trim transforms → masked.
    python form: a bare `var` word not followed by `[` (slice = masked) or
    `=` (reassignment/kwarg label, not a read that reaches output) — checked
    only when the statement carries no `$`-syntax reference at all for this
    var, so a bash label like `TOKEN=${TOKEN:0:6}` is not double-read as a
    python-style bare reference.
    """
    if _MASK_MARKER_RE.search(stmt):
        return False
    dollar_ref = re.compile(r"\$\{" + re.escape(var) + r"([^}]*)\}|\$" + re.escape(var) + r"\b")
    saw_dollar = False
    unmasked = False
    for m in dollar_ref.finditer(stmt):
        saw_dollar = True
        body = m.group(1)
        if body is None or body == "":
            unmasked = True
        elif body[0] not in ":#%/":
            unmasked = True
    if saw_dollar:
        return unmasked
    return re.search(r"\b" + re.escape(var) + r"\b(?!\s*[\[=])", stmt) is not None


def _scan_statements(stmts: list[str], authored: bool) -> list[tuple[str, str]]:
    """Return (reason, statement) findings from an ordered statement list.

    `authored` selects the tier: authored content additionally fires on a
    bare no-capture fetch line (script stdout lands in the transcript on
    execution); a live command's bare fetch is the sanctioned interactive
    read and stays silent.
    """
    findings: list[tuple[str, str]] = []
    captured: list[str] = []
    for raw in stmts:
        stmt = raw.strip()
        if not stmt or stmt.startswith("#"):
            continue
        is_sink = bool(_SINK_RE.search(stmt))
        diverted = bool(_DIGEST_RE.search(stmt)) or bool(_FILE_REDIRECT_RE.search(stmt))
        if _is_fetch(stmt):
            assign = _ASSIGN_RE.match(stmt)
            if assign:
                if assign.group(1) not in captured:
                    captured.append(assign.group(1))
            elif is_sink and not diverted and not _MASK_MARKER_RE.search(stmt):
                findings.append(("fetch substituted inline into an output command", stmt))
            elif authored and not diverted:
                findings.append(("bare fetch — stdout reaches the transcript on execution", stmt))
            continue
        if is_sink and not diverted:
            for var in captured:
                if _has_unmasked_ref(stmt, var):
                    findings.append((f"fetched variable `{var}` printed unmasked", stmt))
                    break
    return findings


# ---------------------------------------------------------------------------
# Bash command decomposition: live lines / heredoc bodies / echo-appends
# ---------------------------------------------------------------------------


def _skip_target(target: str | None) -> bool:
    """True if the redirect target is a documentation file — do not scan."""
    return target is not None and target.rstrip("\"'").lower().endswith(_SKIP_SUFFIXES)


def _redirect_target(line: str) -> str | None:
    m = _REDIRECT_TARGET_RE.search(line)
    return m.group(1) if m else None


def _extract_quoted(text: str) -> list[str]:
    """Return the contents of single- and double-quoted spans in `text`."""
    return [a or b for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", text)]


def _decompose_bash(command: str) -> tuple[list[str], list[list[str]]]:
    """Split a Bash command into (live statements, authored statement docs).

    Authored docs are heredoc bodies and echo/printf-appended quoted strings
    (accumulated per redirect target, so a capture appended by one `echo >>`
    and a sink appended by the next are linked), each already gated by the
    redirect target's file type.
    """
    live_lines: list[str] = []
    authored_docs: list[list[str]] = []
    appends: dict[str, list[str]] = {}

    heredoc_marker: str | None = None
    heredoc_skip = False
    heredoc_body: list[str] = []

    for line in command.split("\n"):
        if heredoc_marker is not None:
            if line.strip() == heredoc_marker:
                if not heredoc_skip and heredoc_body:
                    authored_docs.append(list(heredoc_body))
                heredoc_marker = None
                heredoc_body = []
            else:
                heredoc_body.append(line)
            continue

        live_lines.append(line)

        m = _HEREDOC_OPEN_RE.search(line)
        if m:
            heredoc_marker = m.group(2)
            heredoc_skip = _skip_target(_redirect_target(line))
            continue

        append = re.match(r"\s*(?:echo|printf)\b(.*?)>>?\s*(\S+)\s*$", line)
        if append:
            target = append.group(2)
            if not _skip_target(target):
                appends.setdefault(target, []).extend(_extract_quoted(append.group(1)))

    # Unterminated heredoc (marker never closed within the command) — scan
    # what was collected rather than dropping it.
    if heredoc_marker is not None and not heredoc_skip and heredoc_body:
        authored_docs.append(list(heredoc_body))

    authored_docs.extend(list(strings) for strings in appends.values() if strings)

    live_stmts = _STMT_SPLIT_RE.split("\n".join(live_lines))
    return live_stmts, authored_docs


# ---------------------------------------------------------------------------
# Advisory text
# ---------------------------------------------------------------------------


def _advisory_text(findings: list[tuple[str, str]]) -> str:
    lines = ["[secret-print-redaction-advisory] fetched secret may reach stdout unmasked", ""]
    for reason, stmt in findings[:_ADVISORY_LIMIT]:
        snippet = stmt if len(stmt) <= 100 else stmt[:97] + "..."
        lines.append(f"  Detected: {reason}")
        lines.append(f"    {snippet}")
    lines += [
        "",
        "  A runtime-fetched secret that is echoed/printed lands verbatim in",
        "  the session transcript (non-reversible). Mask at source:",
        "",
        "    bash:   mask() { printf '%s...%s' \"${1:0:6}\" \"${1: -4}\"; }",
        "    python: def mask(s): return f\"{s[:6]}...{s[-4:]}\" if s else s",
        "",
        "  Reference: issue #827 (2nd recurrence — memory-only remediation",
        "  failed; the leak is irreversible once the script runs)",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0

    if not isinstance(payload, dict):
        return 0
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    findings: list[tuple[str, str]] = []

    if tool_name == "Bash":
        command = tool_input.get("command") or ""
        if not isinstance(command, str) or not command.strip():
            return 0
        live_stmts, authored_docs = _decompose_bash(command)
        findings.extend(_scan_statements(live_stmts, authored=False))
        for doc in authored_docs:
            findings.extend(_scan_statements(_STMT_SPLIT_RE.split("\n".join(doc)), authored=True))
    elif tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path") or ""
        if isinstance(file_path, str) and file_path.lower().endswith(_SKIP_SUFFIXES):
            return 0
        text = tool_input.get("content" if tool_name == "Write" else "new_string") or ""
        if not isinstance(text, str) or not text.strip():
            return 0
        findings.extend(_scan_statements(_STMT_SPLIT_RE.split(text), authored=True))
    else:
        return 0

    if findings:
        sys.stderr.write(_advisory_text(findings) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
