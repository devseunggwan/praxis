#!/bin/sh
# Workspace-trust lookup for cmux-delegate (issue #981, Work B).
#
# Claude Code shows a workspace trust dialog the first time it starts in a
# directory, and that dialog CONSUMES PIPED STDIN. A delegation whose prompt
# arrives on stdin therefore loses it silently: the worker lands in an empty
# REPL and does nothing, while the delegator sees only an absent report. Step 4
# now hands the prompt over argv so nothing can be eaten, and this script
# answers the other half — will the worker sit at that dialog waiting for a
# keypress? Knowing before launch turns a mystery into an expected wait.
#
#   eval "$(sh path/to/cwd-trust.sh "$PWD")"
#   [ "$trusted" = no ] && echo "이 워커는 신뢰 다이얼로그에서 대기합니다"
#
# Output is `key=value` pairs on one line, values single-quoted. Exits 0 for
# well-formed arguments; an unreadable config yields trusted=unknown, never a
# failure. praxis is host-neutral (ARCHITECTURE.md) and a probe that cannot run
# must not manufacture an answer.
#
# WHICH CONFIG FILE. `CLAUDE_CONFIG_DIR` set → `$CLAUDE_CONFIG_DIR/.claude.json`,
# otherwise `$HOME/.claude.json`. Measured rather than assumed: on this host
# `$HOME/.claude.json` carried 43 projects with a same-day mtime while
# `$HOME/.claude/.claude.json` held 2 and had not moved in eight days, and a
# per-account dir (`~/.claude-2/.claude.json`) carried its own live 17. The
# delegator must read the SAME file the worker will, because `--account` sends
# the worker to a different one — asking the wrong file is worse than not
# asking, since it answers confidently about someone else's trust state.
#
# UNKNOWN IS NOT NO. `no` means "a human will have to press a key", so treating
# an unreadable config as `no` would attach that warning to every healthy
# delegation until nobody reads it.
#
# CWD OR ANCESTOR — the exact key is UNVERIFIED. The lookup is keyed on the
# directory as given, and a trusted ancestor is reported separately in
# `ancestor` rather than folded into the verdict. Whether Claude Code inherits
# trust from a parent directory was not established: this host has 43 project
# entries and none is a subdirectory of a repository, which is equally
# consistent with "trust is inherited" and with "claude was always started at a
# repository root". Reporting both facts is what keeps an unresolved question
# from being silently answered by this script's return value.

set -eu

if [ $# -ne 1 ] || [ -z "$1" ]; then
    echo "usage: cwd-trust.sh <directory>" >&2
    exit 2
fi

if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    _CT_CONFIG="$CLAUDE_CONFIG_DIR/.claude.json"
else
    _CT_CONFIG="$HOME/.claude.json"
fi

python3 - "$1" "$_CT_CONFIG" <<'PY'
import json
import os
import sys


def quote(value):
    """POSIX single-quoting: close, escape, reopen for an embedded quote."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def emit(**fields):
    print(" ".join(f"{k}={quote(v)}" for k, v in fields.items() if v is not None))
    raise SystemExit(0)


target = os.path.realpath(sys.argv[1]).rstrip("/") or "/"
config = sys.argv[2]

try:
    with open(config, encoding="utf-8") as handle:
        projects = json.load(handle).get("projects", {})
except (OSError, ValueError):
    # Absent, unreadable, or malformed — all the same answer, and it is not `no`.
    emit(trusted="unknown", reason="config-unreadable", config=config)

if not isinstance(projects, dict):
    emit(trusted="unknown", reason="config-shape-unexpected", config=config)


def accepted(path):
    entry = projects.get(path)
    return isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True


if accepted(target):
    emit(trusted="yes", reason="entry-accepted", config=config, matched=target)

# Nearest accepted ancestor, reported but never substituted for the verdict.
ancestor = None
walk = os.path.dirname(target)
while walk and walk != "/":
    if accepted(walk):
        ancestor = walk
        break
    walk = os.path.dirname(walk)

reason = "entry-declined" if target in projects else "no-entry"
emit(trusted="no", reason=reason, config=config, ancestor=ancestor)
PY
