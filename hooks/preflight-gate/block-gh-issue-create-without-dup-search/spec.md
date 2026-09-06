# PreToolUse gh issue create Without Dup Search Block

Supported hosts: all

`hooks/preflight-gate/block-gh-issue-create-without-dup-search/impl.py` intercepts every Bash
tool call and hard-blocks `gh issue create` when no prior duplicate search
happened in the same session.

### Why this exists

CLAUDE.md "GitHub Issue Hygiene" requires `gh search issues '<keywords>'
--repo <repo>` (open AND closed) before creating any issue, so duplicates are
not filed. Memory-only enforcement of this rule recurred — the hook
intercepts at the create checkpoint instead.

Retrospect pattern (praxis issue #374): the agent spawned a new follow-up
issue from a fresh analysis finding without running a duplicate search;
an existing open issue already covered the same root-cause scope (often
surfaced earlier in the same session by a sibling sciomc Stage or a PR
body "follow-up review" ("후속 검토") item). User redirect → `/cancel` cycle.

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
| ----------- | -------- |
| `gh issue create --title "feat: add brands lookup"`, no prior search | **BLOCKED** (no search) |
| prior `gh search issues "auth token"`, then create titled "chart filter" | **BLOCKED** (no overlap) |
| prior `gh search issues "brands lookup"`, then create titled "feat: brands lookup CTE" | **PASS** (overlap) |
| `gh issue create --repo <owner>/scratchs ...` with `<owner>` ∈ `PRAXIS_PERSONAL_REPO_OWNERS` | **PASS** (personal-repo carve-out) |
| same command with `PRAXIS_PERSONAL_REPO_OWNERS` unset | **BLOCKED** — no exemption on shipped defaults (issue #1156) |
| `gh issue create --title "feat: foo bar [dup-checked]"` | **PASS** (dup token) |
| `gh issue create --title "fix: ci"` (no keyword ≥4 chars) | **PASS** (cannot enforce) |
| `gh issue create --title="…"` / `-t "…"` / `-t="…"` / `gh -R o/r issue create --title …` | **BLOCKED** (title parsed from all flag forms; issue #514) |
| `grep -rn "gh issue create" hooks/` / `echo gh issue create --title x` | **PASS** (literal inside a quoted string is not a real invocation; issue #514) |

### Escape hatches

- Add `[dup-checked]` or `[no-search-needed]` to the `--title` when the
  duplicate check was verified outside the session.
- Personal-repo carve-out: a `--repo` write whose target owner is listed in
  `PRAXIS_PERSONAL_REPO_OWNERS` (comma-separated handles — the same env var
  `block-personal-asset-leak` reads for the same "owners that are mine"
  concept) is low blast-radius and passes without a search. Unset = no
  exemption for anyone; the hardcoded author-namespace regex this replaces
  changed enforcement per-installer (issue #1156).
- Set `CLAUDE_HOOK_BYPASS_DUP_GATE=1` for a deliberate one-off bypass.
- Title with no extractable keyword ≥4 chars → silent pass (cannot enforce).
- Missing / unreadable transcript → silent pass. There is no size bound any
  more (issue #1279): the tail is read from the end, so an oversized
  transcript is scanned like any other and can block.
  Malformed stdin → silent fail-open.

### Tests

```bash
bash tests/hooks/preflight-gate/test_block_gh_issue_create_without_dup_search.sh
```

Covers both block paths (no search, no overlap), silent paths (each escape
hatch, personal-repo carve-out, keyword overlap, unkeyworded title), the
title-flag forms and quote-aware over-block guards (issue #514),
non-Bash tool passthrough, missing `transcript_path`, and malformed
JSON fail-open.
