# PreToolUse gh issue create Without Dup Search Block

Supported hosts: all

`hooks/block-gh-issue-create-without-dup-search.sh` intercepts every Bash
tool call and hard-blocks `gh issue create` when no prior duplicate search
happened in the same session.

### Why this exists

CLAUDE.md "GitHub Issue Hygiene" requires `gh search issues '<keywords>'
--repo <repo>` (open AND closed) before creating any issue, so duplicates are
not filed. Memory-only enforcement of this rule recurred — the hook
intercepts at the create checkpoint instead.

Retrospect source (Hub #2242 retrospect, 2026-05-21): the agent created Hub
#2245 ("shopby_v2 brands_lookup CTE pattern") while Hub #2243 ("products_src
catalog gap") already covered the same root-cause scope. User redirect:
"기존에 PR이 존재했는데 왜 또 만드나요" → `/cancel`.

### What is blocked

Either condition triggers a block (exit 2):

1. **No search at all** — `gh issue create` is invoked with no prior `gh
   search issues` / `gh issue list` / `gh issue view` anywhere in the
   transcript tail (last ~400 lines).
2. **No keyword overlap** — prior searches exist, but none of their args
   contain any keyword extracted from the new issue's `--title`.

Keyword extraction strips the Conventional Commits prefix (`feat(scope):`),
lowercases, splits on word boundaries, and drops stop words and tokens
shorter than 4 chars. Overlap is satisfied if ANY remaining keyword appears
literally in a prior search command.

| Situation | Action |
|-----------|--------|
| `gh issue create --title "feat: add brands lookup"`, no prior search | **BLOCKED** (no search) |
| prior `gh search issues "auth token"`, then create titled "chart filter" | **BLOCKED** (no overlap) |
| prior `gh search issues "brands lookup"`, then create titled "feat: brands lookup CTE" | **PASS** (overlap) |
| `gh issue create --repo devseunggwan/scratchs ...` | **PASS** (personal-repo carve-out) |
| `gh issue create --title "feat: foo bar [dup-checked]"` | **PASS** (dup token) |
| `gh issue create --title "fix: ci"` (no keyword ≥4 chars) | **PASS** (cannot enforce) |

### Escape hatches

- Add `[dup-checked]` or `[no-search-needed]` to the `--title` when the
  duplicate check was verified outside the session.
- Personal-repo carve-out: `--repo devseunggwan/...` writes are low
  blast-radius and pass without a search.
- Set `CLAUDE_HOOK_BYPASS_DUP_GATE=1` for a deliberate one-off bypass.
- Title with no extractable keyword ≥4 chars → silent pass (cannot enforce).
- Missing / unreadable / oversized (`>50MB`) transcript → silent pass.
  Malformed stdin → silent fail-open.

### Tests

```bash
bash tests/test_block_gh_issue_create_without_dup_search.sh
```

Covers 14 cases: both block paths (no search, no overlap), silent paths
(each escape hatch, personal-repo carve-out, keyword overlap, unkeyworded
title), non-Bash tool passthrough, missing `transcript_path`, and malformed
JSON fail-open.
