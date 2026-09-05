#!/usr/bin/env python3
"""PostToolUse(Bash) advisory: unresolved PR review threads after a `git push`.

Background (issue #1039): `Review Comment Reply & Resolve (MUST)` says a review
comment you acted on gets a reply in its own thread and that thread gets
resolved. Fixing the code is half the action — until the reply lands, the
reviewer's surface still reads "open", and to whoever merges, an unresolved
thread is indistinguishable from an unhandled one.

That rule had no hook behind it, so in practice it fired only when a human typed
"if fixed comments, reply and resolve it" at the agent, every single PR. This
hook moves the reminder to the moment the fix actually lands: right after the
push that carries it.

Inline review threads are a surface of their own. `gh pr view --json
comments,reviews` covers the conversation timeline and review bodies and does
NOT contain them, so `reviewThreads` over GraphQL is the only oracle here.

Threads are reported in two groups, because an open-thread count is not a
blocker count:

  - needs a reply: `(blocking)`, and anything with no recognizable label (a bot
    comment or a pre-vocabulary thread is for a human to grade, not for this
    hook to quietly demote)
  - for reference: `nitpick:` / `note:` / `(non-blocking)` — unresolved by
    design; treating these as blockers is how a merge stalls on a nit

Advisory by default (exit 0 + stderr); PRAXIS_PR_THREAD_ADVISORY_STRICT=1
escalates to exit 2, and only when the needs-a-reply group is non-empty.
Fully fail-open (no gh, no auth, network error, unparseable response -> silent
allow) and bypassable via PRAXIS_PR_THREAD_ADVISORY_BYPASS=1.

Skip conditions (return 0, no advisory):
  - tool_name != Bash, or the command does not parse as a `git push`
  - the push itself failed / was interrupted
  - no open PR for the pushed branch
  - every review thread on that PR is resolved
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _git_push_target import resolve_push_target  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    fail_open,
    remaining_budget,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402

_BYPASS_ENV = "PRAXIS_PR_THREAD_ADVISORY_BYPASS"
_STRICT_ENV = "PRAXIS_PR_THREAD_ADVISORY_STRICT"
_TIMEOUT_ENV = "PRAXIS_PR_THREAD_GH_TIMEOUT"
_DEFAULT_GH_TIMEOUT = 5.0

# Threads fetched in one page. A PR past this is already unreviewable by hand;
# the advisory says so rather than silently reporting a truncated count.
_THREAD_PAGE = 100
_BODY_EXCERPT = 90

_PR_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")

# Review bots lead with decorative markup — Codex opens every finding with
# `**<sub><sub>![P1 Badge](https://img.shields.io/...)</sub></sub>`. Left in,
# that eats the whole excerpt and every thread in the list reads identically.
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]{1,40}>")

# Review bodies and file paths come from whoever opened the PR. Written to a
# terminal unfiltered, an ANSI/OSC sequence in one can rewrite the lines above
# it or hide a link target behind different text — so every GraphQL string is
# stripped of control bytes before it is rendered, not just the body.
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

_THREADS_QUERY = """
query($owner:String!, $name:String!, $number:Int!, $first:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      reviewThreads(first:$first) {
        pageInfo { hasNextPage }
        nodes {
          isResolved
          path
          line
          comments(first:1) { nodes { author { login } body } }
        }
      }
    }
  }
}
"""


def _gh_timeout() -> float:
    raw = os.environ.get(_TIMEOUT_ENV, "").strip()
    if not raw:
        return _DEFAULT_GH_TIMEOUT
    try:
        val = float(raw)
    except ValueError:
        return _DEFAULT_GH_TIMEOUT
    return val if val > 0 else _DEFAULT_GH_TIMEOUT


def _gh(args: list[str], cwd: str) -> str | None:
    """Run `gh <args>` in cwd. Return stdout on success, None on any failure.

    Under the dispatcher the timeout clamps to the member's remaining budget
    so a slow `gh` cannot outlive the PostToolUse group's node timeout.
    """
    timeout = _gh_timeout()
    budget = min(timeout, remaining_budget(timeout))
    if budget < MIN_SUBPROC_BUDGET_SEC:
        return None  # a sub-floor slice cannot finish a gh round trip
    try:
        r = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=budget,
        )
    except (OSError, subprocess.SubprocessError):
        return None  # gh absent, not executable, or hung
    if r.returncode != 0:
        return None  # unauthenticated, no remote, rate limited — all fail-open
    return r.stdout or ""


def _push_output(tool_response: object):
    """Joined command output, or None when the response carries none at all.

    The distinction matters: a response with an empty-but-present output field
    is evidence the push produced nothing (so it did not run), while one with
    no output field is simply unknowable.
    """
    if not isinstance(tool_response, dict):
        return None
    parts = [
        val for key in ("output", "stdout", "stderr")
        if isinstance(val := tool_response.get(key), str)
    ]
    if not parts:
        return None
    return "\n".join(parts)


def _push_landed(output: str, branch: str):
    """Did the push segment run and succeed? True / False / None if unknowable.

    The whole command's exit status answers a different question: `true ||
    git push` exits 0 with no push, and `git push && false` exits non-zero
    with the push already on the remote. The push's own output is what
    distinguishes them.
    """
    if output is None:
        return None  # nothing captured at all — caller falls back to exit status
    if not output.strip():
        return False  # the command produced no output, so no push ran
    if re.search(r"!\s*\[rejected\]|failed to push|error: src refspec", output):
        return False
    if "Everything up-to-date" in output:
        return True
    if re.search(r"->\s*" + re.escape(branch), output):
        return True
    return bool(re.search(r"^To\s", output, re.M))


def _open_pr(branch: str, cwd: str):
    """Return {owner, name, number, url} for the open PR on `branch`, else None."""
    out = _gh(
        [
            "pr", "list",
            "--state", "open",
            "--head", branch,
            "--json", "number,url",
            # Two, not one: `gh pr list --head` cannot filter by owner
            # ("<owner>:<branch>" syntax not supported), so two forks can open
            # PRs on the same base repo under one branch name. Asking for two
            # is what makes that case visible; picking the first would name a
            # PR the push never touched.
            "--limit", "2",
        ],
        cwd,
    )
    if not out:
        return None
    try:
        rows = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(rows, list) or len(rows) != 1:
        return None  # none, or ambiguous — silence beats naming the wrong PR
    row = rows[0]
    if not isinstance(row, dict):
        return None
    url = row.get("url")
    number = row.get("number")
    if not isinstance(url, str) or not isinstance(number, int):
        return None
    m = _PR_URL_RE.search(url)
    if not m:
        return None
    return {"owner": m.group(1), "name": m.group(2), "number": number, "url": url}


def _unresolved_threads(pr: dict, cwd: str):
    """Return (threads, truncated) for the PR's unresolved review threads.

    `None` (not an empty list) means the lookup itself failed — the caller must
    not read that as "no unresolved threads".
    """
    out = _gh(
        [
            "api", "graphql",
            "-f", f"query={_THREADS_QUERY}",
            "-F", f"owner={pr['owner']}",
            "-F", f"name={pr['name']}",
            "-F", f"number={pr['number']}",
            "-F", f"first={_THREAD_PAGE}",
        ],
        cwd,
    )
    if not out:
        return None, False
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return None, False
    try:
        block = data["data"]["repository"]["pullRequest"]["reviewThreads"]
        nodes = block["nodes"]
        truncated = bool(block.get("pageInfo", {}).get("hasNextPage"))
    except (KeyError, TypeError):
        return None, False
    if not isinstance(nodes, list):
        return None, False

    threads = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("isResolved") is True:
            continue
        comments = ((node.get("comments") or {}).get("nodes") or [])
        first = comments[0] if comments and isinstance(comments[0], dict) else {}
        author = ((first.get("author") or {}).get("login")) or "?"
        body = first.get("body") or ""
        threads.append({
            "path": node.get("path") or "?",
            "line": node.get("line"),
            "author": author,
            "body": body,
        })
    return threads, truncated


def _sanitize(text: str) -> str:
    """Drop control bytes. Applied to every string that reaches the terminal."""
    return _CTRL_RE.sub("", text or "")


def _clean_body(body: str) -> str:
    """Strip control bytes, badge images, HTML tags and emphasis; collapse space."""
    text = _sanitize(body)
    text = _IMAGE_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    text = text.replace("**", "").replace("`", "")
    return " ".join(text.split())


def _needs_reply(body: str) -> bool:
    """True when the thread is one a reply is owed on.

    Conventional Comments puts the grade at the very front, so only the head of
    the body is read. An unlabeled thread counts as needing a reply: silence
    about its grade is not the same as a low grade.
    """
    head = _clean_body(body).lstrip("*_> \t").lower()[:80]
    if "(blocking)" in head and "(non-blocking)" not in head:
        return True
    if "(non-blocking)" in head:
        return False
    return not (head.startswith("nitpick:") or head.startswith("note:"))


def _format_thread(t: dict) -> str:
    path = _sanitize(t["path"])
    loc = path if t["line"] is None else f"{path}:{t['line']}"
    excerpt = _clean_body(t["body"])[:_BODY_EXCERPT]
    return f"    - {loc} (@{_sanitize(t['author'])}) {excerpt}"


def _format_truncation_only(pr: dict) -> str:
    return (
        f"[pr-thread-resolve-advisory] {pr['url']} — the first {_THREAD_PAGE} threads are all "
        "resolved, but the next page was not read; \"no unresolved threads\" cannot be claimed.\n"
        f"  첫 {_THREAD_PAGE}건은 모두 resolved 이지만 "
        "그 뒤 페이지를 읽지 않았습니다. 미해결 스레드 없음이라고 말할 수 없습니다.\n"
        "  Full check: paginate reviewThreads to the end with gh api graphql, or read the PR page.\n"
        "  전수 확인: gh api graphql 로 reviewThreads 를 끝까지 페이지네이션하거나 PR 화면에서 직접 확인하세요.\n"
        f"  bypass: {_BYPASS_ENV}=1\n"
    )


def _format_advisory(pr: dict, needs: list, fyi: list, truncated: bool) -> str:
    total = len(needs) + len(fyi)
    lines = [
        f"[pr-thread-resolve-advisory] {pr['url']} has {total} unresolved review "
        f"thread(s) ({len(needs)} need a reply / {len(fyi)} FYI).\n"
        f"  {pr['url']} 에 미해결 리뷰 스레드 "
        f"{total}건 (답변 필요 {len(needs)} / 참고 {len(fyi)}).",
    ]
    if needs:
        lines.append(
            "  Needs a reply — (blocking) or no grade label:\n"
            "  답변 필요 — (blocking) 또는 등급 라벨 없음:"
        )
        lines.extend(_format_thread(t) for t in needs)
    if fyi:
        lines.append(
            "  FYI — nitpick / note / (non-blocking), fine to stay open by design:\n"
            "  참고 — nitpick / note / (non-blocking), 설계상 열려 있어도 정상:"
        )
        lines.extend(_format_thread(t) for t in fyi)
    if truncated:
        lines.append(
            f"  Note: more than {_THREAD_PAGE} threads — only the first page was read; "
            "this list is not complete.\n"
            f"  주의: 스레드가 {_THREAD_PAGE}건을 넘어 첫 페이지만 읽었습니다 — 목록이 전부가 아닙니다."
        )
    lines += [
        "  Reply inside each fixed thread and resolve it "
        "(a top-level comment does not attach to the finding):\n"
        "  고친 스레드는 각자의 스레드 안에 답글을 달고 resolve 하세요 "
        "(top-level 코멘트는 finding 에 붙지 않습니다):",
        "    Fixed — <short-sha> <무엇이 바뀌었는지 한 줄>",
        "    Not fixed — <이유>",
        "    False positive — <반증 프로브와 출력>",
        "  Leave threads not yet fixed, or deferred to a follow-up issue, open — "
        "the open thread carries that carry-over.\n"
        "  아직 안 고쳤거나 후속 이슈로 미룬 스레드는 열어 둡니다 — 열린 스레드가 그 이월을 실어 나릅니다.",
        f"  bypass: {_BYPASS_ENV}=1",
    ]
    return "\n".join(lines) + "\n"


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip():
        return 0

    payload = read_payload(("Bash",))
    if payload is None:
        return 0  # non-Bash tool or malformed stdin — fail-open

    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not command or "push" not in command or "git" not in command:
        return 0

    tool_response = payload.get("tool_response")
    if isinstance(tool_response, dict) and (
        tool_response.get("interrupted") is True or tool_response.get("isError") is True
    ):
        return 0

    targets = resolve_push_target(command, payload.get("cwd") or os.getcwd())
    if targets is None:
        return 0

    # Nothing landed to reply about unless the push segment itself succeeded.
    landed = _push_landed(_push_output(tool_response), targets["remote_branch"])
    if landed is False:
        return 0
    if landed is None:
        # No output captured — fall back to the whole command's exit status.
        # Weaker (a compound command can lie either way), but silence here
        # would disable the hook wherever output is not surfaced. With neither
        # signal there is no evidence the push ran at all, and the default when
        # evidence is missing is silence.
        exit_code = tool_response.get("exit") if isinstance(tool_response, dict) else None
        if exit_code is None:
            return 0
        try:
            if int(exit_code) != 0:
                return 0
        except (TypeError, ValueError):
            return 0

    cwd = targets["cwd"]
    pr = _open_pr(targets["remote_branch"], cwd)
    if pr is None:
        return 0

    threads, truncated = _unresolved_threads(pr, cwd)
    if threads is None:
        return 0  # lookup failed — never read as "nothing unresolved"
    if not threads:
        if truncated:
            # First page all resolved, but there are pages we did not read.
            # Reporting nothing here would state a negative over a surface
            # that was never fully looked at.
            sys.stderr.write(_format_truncation_only(pr))
        return 0

    needs = [t for t in threads if _needs_reply(t["body"])]
    fyi = [t for t in threads if not _needs_reply(t["body"])]

    sys.stderr.write(_format_advisory(pr, needs, fyi, truncated))
    strict = os.environ.get(_STRICT_ENV, "").strip() == "1"
    return 2 if (strict and needs) else 0


if __name__ == "__main__":
    sys.exit(main())
