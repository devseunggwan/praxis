#!/usr/bin/env python3
"""PreToolUse(Bash) guard: surface collateral side effects before execution.

Reads Claude Code hook JSON on stdin; if tool_input.command matches a known
side-effect category (git mutation, remote trigger, kubernetes write, known
wrapper CLIs that commit internally), emits permissionDecision "ask" with a
reason. Otherwise exits 0 (transparent pass-through).

Uses shlex for tokenization so quoted/subshelled fragments can't hide a
matching command from the detector.

Opt-out: embed the marker `# side-effect:ack` anywhere in the command to
signal an intentional invocation — the hook then exits 0 without prompting.
"""
from __future__ import annotations

import json
import re
import shlex
import sys


CATEGORIES = {
    "git-commit": {
        "patterns": [
            ("git", (1,), {"commit", "merge", "rebase", "cherry-pick", "revert"}),
            ("iceberg-schema", (1,), {"migrate", "promote"}),
            ("omc", (1,), {"ralph"}),
        ],
        "reason": "local git state mutation — 현재 브랜치/HEAD 확인 필요",
    },
    "git-push": {
        "patterns": [
            ("git", (1,), {"push"}),
        ],
        "reason": "remote trigger (git push) — 타겟 브랜치와 upstream 재확인",
    },
    "gh-merge": {
        "patterns": [
            ("gh", (1, 2), ("pr", {"merge", "create"})),
            ("gh", (1, 2), ("workflow", {"run"})),
        ],
        "reason": "remote trigger via gh — PR/워크플로 타겟과 의도 재확인",
    },
    "kubectl-apply": {
        "patterns": [
            ("kubectl", (1,), {"apply", "delete", "replace", "patch"}),
        ],
        "reason": "shared resource write (kubectl) — cluster/namespace 재확인",
    },
}

PROD_LITERAL_TOKENS = {"prod", "production"}
SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}
OPT_OUT_MARKER = "# side-effect:ack"

ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Shell keywords that appear at the start of a command segment but are purely
# syntactic. `if true; then git push; fi` segments as `['if','true']`,
# `['then','git','push']`, `['fi']` — we peel the keyword so argv[0] becomes
# the real executable.
SHELL_KEYWORDS = {
    "if", "then", "elif", "else", "fi",
    "while", "until", "do", "done",
    "case", "esac", "in", "for",
    "{", "}", "!", "function",
}

# Prefix wrappers that execute the following command as a new process. The
# scanner looks past them to find the real argv[0]. Per-wrapper option
# dictionaries list *only* flags that take a separate-token argument so that
# `sudo --user admin kubectl ...` peels both `--user` and `admin`. Bare flags
# (with no arg) and `--long=value` forms are handled generically below.
PREFIX_WRAPPERS = {"env", "sudo", "nice", "time", "stdbuf", "ionice"}
WRAPPER_OPTS_WITH_ARG = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "sudo": {
        "-u", "-g", "-p", "-C", "-D", "-r", "-t", "-T", "-U", "-h",
        "--user", "--group", "--prompt", "--close-from", "--chdir",
        "--role", "--type", "--host", "--other-user",
    },
    "nice": {"-n", "--adjustment"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "time": {"-f", "--format", "-o", "--output"},
    "ionice": {
        "-c", "--class", "-n", "--classdata",
        "-p", "--pid", "-P", "--pgid", "-u", "--uid",
    },
}


def has_opt_out(raw: str) -> bool:
    return OPT_OUT_MARKER in raw.lower()


def safe_tokenize(command: str) -> list[str]:
    """Tokenize with shell operators and line breaks split into tokens.

    Uses shlex.shlex(punctuation_chars=';|&') so that `git push&&echo` and
    `git push;echo` split into `['git', 'push', '&&', 'echo']` etc. Plain
    shlex.split keeps operators glued to adjacent words, which would let a
    whitespace-free one-liner bypass detection entirely.

    Newlines are a command separator in Bash but shlex's whitespace_split
    consumes them as generic whitespace, flattening multi-line scripts into
    one token stream. We pre-split the raw command on `\\n` and insert a
    synthetic `;` between line tokens so iter_command_starts sees the break.
    Lines that fail to parse (unmatched quote, runaway heredoc, etc.) are
    skipped — better a silent pass than a crashed hook.
    """
    lines = [ln for ln in command.split("\n") if ln.strip()]
    if not lines:
        return []
    tokens: list[str] = []
    for idx, line in enumerate(lines):
        if idx > 0:
            tokens.append(";")
        try:
            lex = shlex.shlex(line, posix=True, punctuation_chars=";|&")
            lex.whitespace_split = True
            lex.commenters = ""  # raw `#` is not a comment here; opt-out marker
            tokens.extend(list(lex))
        except ValueError:
            continue
    return tokens


def strip_prefix(argv: list[str]) -> list[str]:
    """Peel shell keywords, `KEY=VAL` assignments, and wrapper commands off
    the front so argv[0] becomes the real executable.

    Handles (in any order, iteratively):
    - shell keywords (`if`, `then`, `do`, `while`, etc.) — pure syntax, drop
    - env assignments (`FOO=1`) — drop
    - wrapper commands (`env`, `sudo`, `nice`, `time`, `stdbuf`, `ionice`) —
      drop the wrapper plus its option flags. Option flags are peeled
      generically: any `-*` token is consumed, and if it's a known arg-taking
      flag for this wrapper the following value token is peeled too. The
      `--long=value` form counts as a single token and is handled naturally.
    """
    i = 0
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok in SHELL_KEYWORDS:
            i += 1
            continue
        if ENV_ASSIGN_RE.match(tok):
            i += 1
            continue
        if tok in PREFIX_WRAPPERS:
            wrapper = tok
            i += 1
            opts_with_arg = WRAPPER_OPTS_WITH_ARG.get(wrapper, set())
            while i < n:
                nxt = argv[i]
                if ENV_ASSIGN_RE.match(nxt):
                    i += 1
                    continue
                if not nxt.startswith("-"):
                    break
                if "=" in nxt:
                    # --long=value — value embedded; peel this token only
                    i += 1
                    continue
                if nxt in opts_with_arg and i + 1 < n:
                    # --user admin / -u admin — peel pair
                    i += 2
                    continue
                # bare flag (-E, -i, -oL, etc.) — peel single token
                i += 1
            continue
        break
    return argv[i:]


def iter_command_starts(tokens: list[str]):
    """Yield argv slices at each command start across shell separators."""
    start = 0
    for i, tok in enumerate(tokens):
        if tok in SHELL_SEPARATORS:
            if start < i:
                yield tokens[start:i]
            start = i + 1
    if start < len(tokens):
        yield tokens[start:]


def argv_matches(argv: list[str], positions, expected) -> bool:
    """Check argv tokens at `positions` against `expected`.

    `expected` is either a single set (single position) or a tuple aligned
    with multiple positions.
    """
    if not argv:
        return False
    if isinstance(expected, (set, frozenset)):
        pos = positions[0]
        return len(argv) > pos and argv[pos] in expected
    for pos, want in zip(positions, expected):
        if len(argv) <= pos:
            return False
        val = argv[pos]
        if isinstance(want, (set, frozenset)):
            if val not in want:
                return False
        else:
            if val != want:
                return False
    return True


def detect(argv: list[str]) -> list[str]:
    argv = strip_prefix(argv)
    if not argv:
        return []
    cmd = argv[0].rsplit("/", 1)[-1]
    matched = []
    for category, spec in CATEGORIES.items():
        for cmd_name, positions, expected in spec["patterns"]:
            if cmd != cmd_name:
                continue
            if argv_matches(argv, positions, expected):
                matched.append(category)
                break
    return matched


def has_prod_scope(tokens: list[str]) -> bool:
    for raw in tokens:
        t = raw.lower()
        if t in PROD_LITERAL_TOKENS:
            return True
        if t.startswith("--env=") and t.split("=", 1)[1] in PROD_LITERAL_TOKENS:
            return True
        if t.startswith("--environment=") and t.split("=", 1)[1] in PROD_LITERAL_TOKENS:
            return True
    # also: `--env prod` as two tokens
    for i, raw in enumerate(tokens[:-1]):
        if raw.lower() in {"--env", "--environment", "-e"}:
            if tokens[i + 1].lower() in PROD_LITERAL_TOKENS:
                return True
    return False


def build_reason(categories: list[str], prod: bool) -> str:
    parts = [f"[{c}] {CATEGORIES[c]['reason']}." for c in categories]
    msg = " ".join(parts)
    if prod:
        msg = "⚠️  PROD scope 감지 — " + msg + " 배포/운영 영향 재확인 필수."
    msg += (
        " 의도한 실행이면 command 에 '# side-effect:ack' 주석을 포함해 재호출하세요."
    )
    return msg


def emit_ask(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # never break the session on malformed input

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0
    if has_opt_out(command):
        return 0

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    matched: list[str] = []
    for argv in iter_command_starts(tokens):
        for cat in detect(argv):
            if cat not in matched:
                matched.append(cat)

    if not matched:
        return 0

    reason = build_reason(matched, has_prod_scope(tokens))
    emit_ask(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
