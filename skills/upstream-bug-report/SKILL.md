---
name: upstream-bug-report
description: >
  Take a defect in an installed third-party plugin, server, or library from
  local diagnosis to an issue on its public repository — prove the fix in a
  fresh process, reproduce on pristine upstream, dedup, match the repo's
  conventions, and post only after approval. Also vets a contributor's fix PR
  against the original reproduction.
when_to_use: >
  Triggers on "upstream bug report", "file upstream issue", "report upstream",
  "vet fix PR", "업스트림 이슈", and on a feature request to a third-party
  public repo (Steps 3–7 apply; skip Steps 1–2). Do NOT activate on filing an issue in your
  own repository or on a bug whose cause is local setup.
verified-against-runtime: true
runtime-verified-at: 2026-10-16
runtime-verified-note: "gh 2.102.0 — against public repos: `issue list --state all --search`, `search issues` / `search prs` (incl. `--author --merged`), the commits API title query, `issue list --json title`, `label list --json name,description`, `api repos/<r> --jq .permissions` (pull-only on a repo without write access), the issue timeline `cross-referenced` filter and `pr view --json state,isDraft,labels,files,reviewDecision,body` returned live rows; `issue create` / `issue edit` flags checked via `--help` only — nothing was posted. `repo view --json url,defaultBranchRef` and `pr view --json baseRefName` returned the URL, default branch and base. git 2.50.1 — on a scratch clone whose remote is not named `origin`: `fetch <url> <default-branch>` + `worktree add --detach … FETCH_HEAD`, and `fetch <url> pull/<pr>/head:pr-<pr> <base>:pr-<pr>-base` + two `worktree add --detach`, then `worktree remove` and `branch -D` worked. praxis hooks replayed on a `gh issue create --title '[Bug]: …'` payload: `commit-title-format-check` exited 2 (not Conventional Commits) and `block-gh-issue-create-without-dup-search` blocked with no prior `gh search issues` / `gh issue list`. AskUserQuestion not round-tripped"
---

# upstream-bug-report

## Overview

A third-party tool fails, the cause turns out to be in its code, and the next
step is an issue on someone else's public repository. That write crosses a
boundary: it is public, it cannot be unsent, and a report whose reproduction
only works on your patched machine wastes the maintainer's time. Most bad
upstream reports fail on one of three things — they blame upstream for a local
problem, they quote output from a tree that is not upstream, or they duplicate
an existing report.

This skill orders the work so each of those is closed before the body is
drafted, and the post happens only after the user has seen the exact title and
body.

**Core principle:** every command in the issue body is a transcription of a run
on pristine upstream that produced the quoted output.

## When to Use

- An installed plugin, server, or library misbehaves and the defect is in its
  code, not in the local setup.
- The user asks to report a bug upstream or to open an upstream issue.
- A contributor opened a PR for an issue you filed and the user asks how well
  it covers it (Step 8).

## The Iron Law

- **Public writes are approval-gated, one action at a time.** Issue, PR, and
  review comment each need their own explicit approval of the exact text.
- **No internal identifiers in the body.** English, synthetic ids and labels,
  `<checkout>` placeholders instead of home paths, no account names.
- **Always `--body-file`.** Never inline a multi-line body in the command.

## Process

### Step 1: Diagnose locally before blaming upstream

- **Read the error class first, as a clue, not a verdict.** A 401 says
  authentication failed: the credential may be missing from the process, or
  present but invalid or expired. A 400/422 points at the request, but the
  vendor may use it for other rejections too. Read the response body, and
  check the credential's presence (without printing it) and its validity
  before naming a cause. A 401 → 422 transition after adding a key suggests
  auth now works and the next problem is in the payload — confirm it from the
  422's response body.
- **A long-running server process keeps the env and code it started with.** A
  tool served by an already-running MCP server or daemon stays stale until
  that process restarts. Test a fix in a **fresh process** — import the module
  with the plugin's own interpreter (its `.venv/bin/python`), loading the
  credential from your env file inside the same command without printing it.
- **Prove the fix in memory before editing files.** Monkeypatch the call (for
  example wrap the request function to add the missing field) and run one
  positive case and one negative or abstain case. Only then patch the file and
  re-run without the monkeypatch. Tell the user the edit is local-only:
  uncommitted, and overwritten by the next plugin update.
- **Confirm the vendor contract from official docs** (the API reference page
  that says the field is required) and keep the URL for the report.

Done when: the defect is attributed to upstream code with a fresh-process
positive and negative run, and the vendor contract is cited by URL — or it is
attributed to local setup and the skill stops.

### Step 2: Reproduce on pristine upstream

Do not reproduce on the locally patched tree.

- **Single module:** extract the upstream file at the installed commit into a
  scratch directory outside any repository —
  `git -C <plugin> show <sha>:<path> > <scratch>/module.py` (plus its
  `__init__.py`) — and run the exact reproduce command you will put in the
  body from there.
- **Whole package** (when the local install carries patches on top of
  upstream): check out the latest upstream head instead. A local `origin` may
  be a fork and the default branch may not be `main`, so resolve both from
  the canonical repository first —
  `gh repo view <r> --json url,defaultBranchRef --jq '.url, .defaultBranchRef.name'`
  — then fetch by URL:
  `git -C <repo> fetch <upstream-url> <default-branch> && git -C <repo> worktree add --detach <scratch>/<name>-upstream FETCH_HEAD`.
  Point the import path at it, run the repro, then `git worktree remove` it.
  Reproducing on the upstream default branch rather than the installed commit
  also answers "is it already fixed"; cite that SHA in the body.
- **Self-contained:** a throwaway config/home directory, synthetic ids and
  labels, `<checkout>` placeholders — so the repro carries no account names or
  local state.

Done when: the reproduce command, run on pristine upstream at a named SHA,
printed the same error you will quote.

### Step 3: Dedup

```bash
gh issue list --repo <r> --state all --search "<phrase>"
gh search issues --repo <r> "<phrase>"
gh search prs --repo <r> "<phrase>"
```

For a repo of a few hundred issues, or one whose reports are multilingual
(CJK titles are common on AI-tool repos), pull the whole corpus once and
filter locally instead — it covers every language and both states in one
pass:

```bash
gh issue list --repo <r> --state all --limit 1000 \
  --json number,title,state,createdAt,url,body > "$TMPDIR/<r>-issues.json"
# then regex title+body in python, including CJK/Korean synonyms
# (e.g. shortcut|hotkey|快捷键|단축키, sidebar|collapse|折叠|收起|접기)
```

Check the count against `--limit`. Give `gh search issues` a positive
control: if it returns `[]` for a phrase a known issue contains, discard its
empty results and rely on the local filter.

Run several phrasings: the symptom, the function name, and a mechanism word
(for example `tombstone`, `resurrect`). Report the result with its scan scope
(the phrasings used), plus the closest related-but-different reports and why
they differ — those go into the body too. praxis's
`block-gh-issue-create-without-dup-search` hook blocks `gh issue create`
without a prior search whose keywords overlap the title, so this step is also
what lets Step 7 run.

Done when: every phrasing and its hit count is listed, and the nearest
reports are named with one line on how they differ (or "none found").

### Step 4: Read the target repository's conventions before proposing a title

Survey before drafting, not after a hook rejects it:

```bash
gh api "repos/<r>/commits?per_page=50" --jq '.[].commit.message|split("\n")[0]'
gh issue list --repo <r> --state all --limit 50 --json title --jq '.[].title'
```

Also read `.github/ISSUE_TEMPLATE/` and `CONTRIBUTING`. If issues have no
title precedent, mirror the commit convention (for example Conventional
Commits `fix(<scope>): <lowercase>`) and state that as the basis.

praxis's `commit-title-format-check` also checks `gh issue create` titles and
rejects a template's `[Bug]:` / `[Feature]:` title prefix. Propose the
Conventional title in the **first** approval ask and say the template prefix
was dropped for that reason; otherwise the post bounces and costs a second
approval round. Do not offer "advisory mode for this one call" as an option:
an inline `PRAXIS_COMMIT_TITLE_FORMAT_STRICT=0 gh issue create …` prefix
does not reach the hook, which reads the agent process's own environment. The
real choices are the Conventional title, or the user posting the body file
themselves with the repo's own prefix — offer both up front.

Done when: the proposed title cites the convention it follows and the
evidence for it (commit subjects, issue titles, or template).

### Step 5: Draft the body

Write it to a scratch file outside the repository. Include:

- **Suggested severity.** When the repo triages by labels (for example
  `P0`–`P3`, descriptions from `gh label list --json name,description`) but
  the template has no severity field and `gh api repos/<r> --jq .permissions`
  shows no `triage`, open the body with
  `**Suggested severity: P<n>** — <the label's own criterion, e.g. degraded, workaround exists (<workaround>)>`.
  A worse case inferred only from code goes on the same line, marked
  `(inferred from the code path, not reproduced)`.
- The reproduce command and its output from Step 2, with the upstream SHA.
- **Only observed claims about the product.** Every sentence about current
  behavior ("settings lists only X", "no toggle exists") traces to a probe
  you ran; cite competitor behavior by its official doc URL. Cut a plausible
  but unprobed claim (e.g. how full screen or resizing behaves) rather than
  soften it — the "Alternatives considered" field is where these creep in.
- The vendor-contract URL from Step 1 and the related reports from Step 3.
- **Template fields that would upload local state** (a "debug report" field
  that pastes config and logs to a public paste service) are left out. Write
  in that field: "Omitted: it would upload local config and logs. The
  reproduction above is self-contained."

**Feature request (Steps 1–2 skipped):** drop the severity line, the
reproduce command, and the vendor-contract URL. Instead state the use case,
the current behavior as observed by a probe on the latest upstream release
or default branch (name it), and the requested behavior; the observed-claims
and related-reports items still apply.

Done when: the body file exists, opens with the severity line, and every
quoted output traces to a Step 2 run — or, for a feature request, every
claim about current behavior traces to a named probe.

### Step 6: Ask for approval

Show the full title and body, then one `AskUserQuestion` with these options:
"Post issue", "Edit draft", "Keep body file only". A PR is a separate public
write: it is out of this skill's scope and needs its own draft and approval.
The last is the non-posting tier; if you drop it, praxis's
`menu-mutation-tier-advisory` expects a column-0
`Safe-tier-unavailable: <why>` line in the question body instead (posting has
no dry run).

If the approval prompt times out, do not retry on your own. Report the issue
as not posted, keep the body file, and re-ask only when the user explicitly
requests it — re-running Step 3 first.

Done when: the user picked an option for this exact title and body.

### Step 7: Post and verify

```bash
gh issue create --repo <r> --title "<title>" --body-file <body.md>
gh issue view <n> --repo <r> --json title,state,body
```

Confirm the title, the OPEN state, and the body length against the local
file. A body edit after posting is a new public write: show the complete
revised body and get an explicit approval for it before each
`gh issue edit --body-file`, then re-read the first lines of the posted
body. If a hook blocks the title, do not bypass it: return to Step 4,
propose the new title, and re-ask.

Report the URL, that the local fix is still uncommitted and local-only, and
whether a running server process must restart to pick up the local fix.

Done when: the posted issue's title, state, and body length match the
approved draft, and the report names the local-fix status.

### Step 8: Vet a contributor's fix PR

When a PR appears for the issue and the user asks how well it covers it:

1. **Find and read it.**
   `gh api repos/<r>/issues/<n>/timeline --jq '.[]|select(.event=="cross-referenced")|.source.issue.html_url'`,
   then `gh pr view <pr> --json state,isDraft,labels,files,reviewDecision,body`
   and `gh pr checks <pr>`.
2. **Run both sides.** Take the PR's real base branch
   (`gh pr view <pr> --repo <r> --json baseRefName`) — it may be a
   maintenance branch, not `main` — and fetch both sides from the upstream
   URL resolved in Step 2:
   `git fetch <upstream-url> pull/<pr>/head:pr-<pr> <base>:pr-<pr>-base`,
   then `git worktree add --detach <scratch>/pr-<pr> pr-<pr>` and
   `git worktree add --detach <scratch>/pr-<pr>-base pr-<pr>-base`. Run the
   issue's repro on the base worktree and on the PR head, plus the PR's own
   tests and the neighbouring suites it names.
3. **Go one step past the repro.** Exercise the real long-lived object (for
   example a loaded pool that later flushes **and** selects), and grep every
   other writer of the same store for paths the fix does not cover. A fix that
   cleans the disk while the live process keeps using the removed thing is
   partial — report it as such.
4. **Report** a table: scenario × base × PR (✅/❌), the test count, and the
   remaining gaps marked verified or inferred. The author's track record
   (`gh search prs --author <u> --merged`) is a side note; say when a count
   hit the search limit.
5. **A review comment on someone else's PR** is drafted first and approved
   like Step 6. Use Conventional Comments (`issue (blocking|non-blocking): …`)
   with Trigger / Fix / Probe parts, where Probe transcribes the run on the PR
   head SHA. Open with a thank-you and an explicit disclosure that it is an
   AI-prepared review. Post it as a conversation comment when the defect sits
   in a file the PR does not touch (no diff line to anchor on). Confirm the
   head SHA is unchanged right before posting, then re-read the posted body.

Remove the scratch worktrees and the `pr-<pr>` / `pr-<pr>-base` branches
afterwards.

Done when: the scenario table covers every repro scenario on both sides, and
the scratch worktrees and branches are gone.

## Error Handling

| Error | Recovery |
| ------- | ---------- |
| Fix works in a fresh process but the tool still fails in-session | The serving process is stale; say a restart is required — do not re-diagnose |
| Repro does not fail on pristine upstream | Already fixed or local-only cause; cite the upstream SHA and stop |
| `gh issue create` blocked by the dup-search hook | Run Step 3 with keywords from the title, then retry |
| Title rejected by the title-format hook | Back to Step 4; re-ask with the new title, never bypass |
| Approval prompt timed out | Not posted; keep the body file; re-ask only on explicit request |
| PR head SHA moved before a review comment posts | Re-run Step 8.2 on the new head and redraft |

## Limitations

- **The local fix is not a contribution.** It lives uncommitted in an
  installed copy and is overwritten by the next update; only a merged upstream
  fix removes it.
- **Severity is a suggestion.** Without `triage` permission the label is the
  maintainer's call; the severity line only states the criterion matched.
- **Dedup is phrasing-bound.** A search over a few phrasings can miss a report
  worded differently; the scan scope in the report makes that visible.
