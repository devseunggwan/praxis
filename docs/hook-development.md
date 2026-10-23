# Hook development: precision, corpus replay, transcript gates

The checklist for changing how a hook decides its verdict
(`hooks/<role>/<name>/impl.py` + `spec.md` +
`tests/hooks/<role>/test_<name>.{sh,py}`). The mechanics of adding a hook —
layout, manifest, wrapper build, INDEX row, canary — are in
[CONTRIBUTING.md → Adding or modifying a hook](../CONTRIBUTING.md#adding-or-modifying-a-hook);
this file covers how to show the new decision is right before a reviewer asks.

An issue that proposes a hook change is usually the approved plan already:
problem, proposal, expected effect, verification plan, one PR of scope.
Carry out that plan; do not plan it again.

## 1. Before proposing a new gate

Prove the gate is missing and that it can be precise, before writing a spec.

- **Check what already runs.** Read `hooks/manifest.json`, not a copy of it
  elsewhere: a hook with no `hosts` runs on every host, so an existing gate on
  the same tool may already cover the case. Send that gate one payload it must
  block as a positive control before claiming a gap.
- **Replay the real calls before writing anything.** Feed every recorded call
  of the gated tool to the draft impl and hand-label each fire against what
  the call actually was. For a question/menu gate, that means every
  `AskUserQuestion` `tool_use` in `~/.claude*/projects/*/*.jsonl`: build the
  PreToolUse payload from its `tool_input`, pipe it into `impl.py`, count a
  fire on rc 2 or any stdout/stderr, and print the question tail, the option
  labels and the user's answer (the matching `tool_result`) for each fire.
  Count distinct questions: retried calls repeat rows.
- **Label against context the text does not carry.** A text-only detector
  cannot see repo visibility, branch ownership, or whether the rules require
  the ask. A gate meant to flag "unnecessary" asks therefore tends to fire
  mostly on asks the rules demand (a write to a public repo, a change to
  someone else's PR).
- **Stop rule.** If most fires are asks the rules require, drop the gate and
  report the replay numbers in the issue instead of shipping a noisy
  advisory. The numbers follow the
  [recurrence-evidence format](../CONTRIBUTING.md#evidence-and-sunset-review).

## 2. Orient on the existing hook

- Read `impl.py`, `spec.md` and the test file in full before editing. The
  spec's behavior table and its "why" paragraphs are the contract: every
  behavior change edits a table row and adds a paragraph citing the corpus
  numbers (§6).
- Check the issue's prerequisites merged and that no open PR already covers
  it (`gh pr list --state all --search "<hook-name> <keyword>"`).
- An issue's simulation numbers were measured against an older baseline.
  Re-measure on current `origin/main` before reporting the PR's effect.
- **A change that relaxes what a rule demands** (for example, one answer
  covering several merges) also touches every surface that states the old
  rule: `ETHOS.md`, the checklist text in `hooks/_lib/block_message.py`,
  sibling specs, and the skill that teaches the procedure. `git grep` the old
  phrasing — in this repo and in any rule files you keep downstream — before
  calling the change complete.

## 3. Enumerate the input surface

Run the [`surface-enumeration`](../skills/surface-enumeration/SKILL.md) skill
before writing code. For a gate that parses shell text, the surface includes
at least:

- quoting: single, double, `$'…'`
- escapes
- nested expansions, and separators inside them
- command position vs argument position
- unterminated constructs
- newlines and heredocs
- arithmetic `(( ))`

## 4. Precision rules

- **A false block is the serious defect** for a gate that blocks: exit 2 on a
  command the shell would run fine. A miss is acceptable.
- **Inverted for a new release path on a consent gate** (merge, external
  write): a false release — an action the user never approved — is the
  serious defect, and a missed release (one more ask) is acceptable. See §7.
- When the parser cannot find where a construct ends, pass the whole command
  through instead of guessing.
- If the change uncovers a path the old blanket pass-through used to hide,
  close that path in the same PR and say so in the report.
- Segment-splitting gates already accept one false positive: an earlier
  segment's side effect, as in `touch a.x; ls *.x`. Use it as the precedent
  for a similar case, and record the decision in `spec.md`.

### Security invariant for gates that probe the shell

A span replayed to `zsh -c ': <span>'` must never contain an executable
`$(…)`, backtick, or `(e:…:)` glob qualifier outside single quotes. Pin it
with a side-effect test: run the hook on a span whose substitution would
create a file, then assert the file does not exist.

## 5. Tests and the real-shell check

- Write each new case as a single-quoted bash string, so the hook receives
  the command exactly as written.
- Keep the old expectations. Change one only when the new rule deliberately
  flips it, and explain the flip in a comment.
- **Check every new case against the real shell.** Write the exact string to
  a script outside the repository from bash, so no second quoting layer can
  break a case that contains `'` —
  `{ echo 'setopt nomatch'; printf '%s\n' <literal>; } > <scratch>/case.zsh` —
  run `zsh -f <scratch>/case.zsh` from a fixture with the same file layout,
  and label it: agree, deliberate MISS (zsh aborts, gate passes), or
  deliberate FP (gate blocks, zsh runs). Document every MISS and FP in
  `spec.md` as a trade-off.
- To probe `main` without checking it out, pipe a
  `{"tool_name":"Bash","tool_input":{"command":…},"cwd":…}` payload into the
  `impl.py` of an extracted main copy (§6) with `SHELL=$(command -v zsh)`.

## 6. Corpus replay: main vs branch

The effect section of the PR body is these numbers, not adjectives.

1. **Main copy.** `D=$(mktemp -d) && git archive origin/main hooks | tar -x -C "$D"`.
2. **Load both.** Import `impl.py` from `$D` and from the worktree with
   `importlib.util.spec_from_file_location`; tests and replays call the
   internals directly.
3. **Corpus.** Stream `~/.claude*/projects/*/*.jsonl` and pair each tool call
   with its `tool_result`. For a glob gate, keep the Bash calls whose result
   matches `\(eval\):\d+: no matches found: (\S+)` — those are the real
   failures the gate exists to catch. If several config dirs link to the same
   `projects/` dir, glob only one, or every row counts N times.
4. **Static coverage.** For each row, run each side's pre-probe pipeline
   (pass-through checks → segments → candidate spans) and count how often
   the failed pattern is a candidate.
5. **Regressions.** List the rows `main` covers that the branch drops. The
   list must be empty, or each row explained.
6. **Live run.** Feed the targeted rows to both hooks, only where the row's
   `cwd` still exists, and count rc 0 vs rc 2. Report rows whose `cwd` is gone
   as `cwd_gone` rather than dropping them silently.

**For a decision change, replay every recorded call of the gated command**,
not only the targeted rows. Write the transcript lines preceding each call
(tail-capped at the gate's scan limit) to a temp file, call the gate's
decision function on both sides with the gate's advisory env var unset, and
report `{transcripts, calls, same, deny_to_allow, allow_to_deny, errors}`
with every changed row listed. Zero changes plus green release fixtures means
the new path opens only for the new shape; say exactly that in the spec and
PR instead of implying real-world coverage.

## 7. Transcript-reading gates

Gates such as `momentum-rule-retrieval-gate` and `pre-merge-approval-gate`
decide from the session transcript: what the assistant wrote, what the user
answered, which calls ran. Parse conservatively: when the answer or the
briefing shape is ambiguous, do not release.

**One decision, one parser.** When a sibling gate must agree (a prompt gate
skipping its ask exactly when the briefing gate releases), expose a public
function from the owning `impl.py` and load it in the sibling with
`spec_from_file_location` (the pattern `early-stop-advisory` uses). On a
consent gate, wrap the call so any exception or unreadable transcript returns
"not approved": the gate keeps asking, whereas the owner's `@fail_open`
contract would release on a crash.

### Adversarial checklist for approval and briefing parsers

Hand this to the reviewer and pin each item as a denying test. Each one was a
false release in a first implementation.

- **Section headings.** Count only markdown headings, or lines bold from end
  to end; a bold lead inside a bullet (`- **Risk**: #999 …`) is an item that
  mentions another PR. Skip lines inside ``` / ~~~ fences — a
  `# then rebase #999` shell comment reads as a heading — and close a fence
  the CommonMark way (a bare run of the opener's character, at least as
  long). A heading names a PR only when it names exactly one PR: test all of
  the heading's refs, not their intersection with the approved set, or
  `### PR #833 / PR #1001` is cut down to #833's section. The first per-PR
  heading fixes the section level; a deeper sub-heading is content of its
  section, and a shared trailing block (`### Common`) is credited to no PR.
  Score each PR on its first section only, so fragments cannot add up to a
  briefing.
- **The ask.** A list of PRs before "merge" is a merge ask only when merge is
  the predicate and the ask ending follows it directly. Prep or status
  questions ("re-run CI before merging #A and #B?", "queue it for merge?")
  do not count.
- **The last question wins.** A reply naming no PR (`ok`, `yes`) answers the
  turn's last question. Credit it to the merge ask only when that ask is the
  last ask sentence of the turn; a trailing "rebase first?" or "or just #833
  for now?" is what it answers.
- **Only AskUserQuestion results are answers.** Match a `tool_result` to an
  `AskUserQuestion` `tool_use` by `tool_use_id` before reading `"q"="a"`
  pairs from it; a Bash or Read output that prints such pairs otherwise
  stands in for the user.
- **Results carry several pairs.** Read every pair: a refusal on any of them
  voids the result, and only a pair whose question is the merge ask (or whose
  label names the PRs) decides. An approval on an unrelated question
  ("clean up the worktree too?") must not approve a merge.
- **Free-text answers.** Parse a free-text ("Other") label whole, the way a
  typed reply is parsed, not only the lead before the first `—` or `,`.
  Require every sentence of the reply to be PR references plus approval
  words: a deferral or precondition need not cite a number ("look at the
  second one tomorrow. merge all", "run CI once more. ok"). Cap the reply at
  a few hundred characters before parsing; a long reply is not a bare
  approval.
- **Bare numbers.** Users approve as `98, 1118 merge` with no `#`, so a
  reply-only regex accepts bare numbers, while briefing text keeps requiring
  `#`/`PR` so `CI 10/10` is not a PR. Read bare numbers as PRs only in a reply
  with no `#N`/`PR N`: beside one, a bare number is an ordinal
  (`1 — #833, #999 merge`) and must void the reply, not name PR #1.
- **The command.** Release only a command that is the merge alone, with an
  explicit number (one command start in total). `cd <other> && gh pr merge N`
  and a `GH_REPO=`/`GH_HOST=` prefix retarget the repo invisibly to `-R`
  parsing; loops, chains of merges and numberless merges stay out. A `cd`,
  `pushd`, `popd` or `export GH_REPO=` issued as an earlier call persists into
  the merge call, so after one since the briefing window opened, release only
  a merge whose `--repo` matches the repo the window's probes named.
- **Regex cost.** Two adjacent optional whitespace runs (`pr\s*#?\s*`)
  backtrack quadratically, and an exception or timeout inside a `@fail_open`
  gate is a release. Write `pr\s*(?:#\s*)?` and time each new pattern on a
  50k-character adversarial input.
- **Consumption.** One answer releases each listed PR once, in order; a merge
  outside the set, or a repeat, ends it. Read executed merges from clean
  `tool_result`s only and count every clean call containing a merge, chained
  or conditional ones included (`gh pr merge 1001 && echo done`). Do not
  reuse a blocking gate's "executed merges" helper as-is: it drops chained
  merges to fail open, which on a release path is a hole — add a flag
  (`include_skippable=True` in `momentum-rule-retrieval-gate`) instead.

Before choosing vocabulary, mine real replies: scan short user messages near
merges in the corpus and sort them into approvals ("merge both", "merge all")
and look-alikes that are not ("merged both", "brief 41 and 47 separately",
"merge from 1316 on"). Keep the non-English phrasings the corpus actually
contains.

### Fixtures and RED proof

- Generate fixtures with a throwaway generator (helpers for user text,
  assistant text, Bash `tool_use`, `tool_result`, `AskUserQuestion` with a
  `"q"="a"` result), write them to `tests/fixtures/<gate>-<case>.jsonl`, and
  keep the generator in scratch so review rounds add cases by editing it.
- Prove RED by reverting only the impl files to the old ref, with the copy
  procedure in [Applying review findings](#applying-review-findings) (save a
  copy of `<impl>` and `<sibling impl>`, write the old versions with
  `git show`, run both test files, copy the saved files back). A
  `git stash pop` afterwards would pop whatever sits at `stash@{0}`, and a
  stash of committed impl changes saves nothing. Each new release case must
  fail on `main`, and each new
  deny case must fail on the previous commit's impl.

## 8. Before opening the PR

Run what CI runs (see [CONTRIBUTING.md → Testing](../CONTRIBUTING.md#testing)).
Notes that the entry point does not tell you:

- `shellcheck` with the CI flags, `--severity=warning
  --exclude=SC2154,SC2034`. At default severity it reports SC2016 on
  single-quoted commands meant to stay literal; that is not a finding.
- `markdownlint-cli2` at the version pinned in `.github/workflows/ci.yml`,
  run from inside the checkout so the repo's rule config applies. CI lints
  changed lines only, so a hit is yours only if its line appears in
  `git diff -U0 origin/main...HEAD`.
- `scripts/run-tests.sh` takes several minutes: run it in the background and
  write `git rev-parse --short HEAD` as the log's first line. A result counts
  for that SHA only; any later commit or rebase needs a fresh run before the
  PR body says the suite passed. Until it lands, leave a placeholder.
- `git fetch origin` before drafting the PR and rebase if `origin/main`
  moved; re-run the touched test files afterwards.
- Mirror the body of recent merged PRs on the same hook
  (`gh pr view <N> --json body`), including the `Caller chain verified:` line
  backed by a grep. Use `Closes #N` for the full scope, `Refs #N` for part.
- Before publishing a `$` block, read its output: a probe can exit 0 while
  doing nothing (a lock held by a concurrent run, an empty payload). If you
  trimmed the output, say so above the block and name the refs each side ran
  on; re-run probes on the final HEAD rather than pasting a pre-rebase run.

## 9. Review

- **Independent review while the suite runs.** Give the reviewer the diff, the
  precision rule from §4, the security invariant, and the accepted trade-offs
  so it does not re-report them. Require it to reproduce every finding as
  hook rc vs the real shell's outcome — or, for a transcript gate, the hook's
  decision on branch vs `main` for a written fixture. For a release path,
  hand it the §7 checklist and ask for false releases only.
- Stop when a round finds nothing new and valid; at most two rounds. When the
  last fix came from round two, re-run that reviewer's probe on the fixed HEAD
  yourself and say the last fix had no independent round.
- A [`codex-review-wrap`](../skills/codex-review-wrap/SKILL.md) pass after the
  subagent rounds still finds release holes. Reproduce each finding as a
  fixture RED on the current impl before editing.
- Bot reviewers often post after CI turns green, with real findings on
  release paths. Before asking for a merge, read the inline threads on the
  final HEAD, fix with a RED-proven fixture, reply with the fixing SHA, and
  re-run the suite on the new SHA.

### Applying review findings

1. Prove each new test RED on the old code by reverting **only the
   implementation file** to the old ref and then restoring your exact copy:
   `cp <impl> <scratch>/impl.keep`, `git show <ref>:<impl> > <impl>`, run the
   tests, `cp <scratch>/impl.keep <impl>`. Do not restore with
   `git checkout -- <impl>` (it brings back the index version and drops
   unstaged edits), and do not rely on `git stash push -- <impl>` (it saves
   nothing when the implementation change is already committed). Reverting
   everything reverts the new tests too, so the run passes and proves nothing.
2. A behavior you decide to keep (an accepted trade-off) gets a test that pins
   it, with the reason in a comment.
3. Re-run the review's probes on the new code, then `ruff check` and `mypy`
   on the touched files.
4. Atomic commits: fix + tests in one, docs wording in another
   (`git add -p` to split hunks). Behavior commits carry `Confidence:` /
   `Not-tested:` trailers.
5. Rewrite the PR body (`gh pr edit --body-file`) and fix every sentence the
   review falsified: test counts, renamed symbols, new behavior, pointers to
   a verification comment that does not exist. Re-measure every count you
   state; say which claims were re-measured and which carried over.
6. Read all three comment surfaces (conversation, inline threads, review
   bodies), say whether each bot finding is fixed on HEAD, and check whether
   base commits touch the PR's files (`git diff --name-only HEAD...origin/main`).

## Pitfalls

- **Sibling imports in `hooks/_lib`.** A module that imports a sibling
  (`_state_lock`, …) does it at module scope inside `try/except ImportError`
  with a no-op fallback (the `_fire_ledger` pattern). Tests load modules via
  `spec_from_file_location` without `hooks/_lib` on `sys.path`, so a bare or
  function-local import raises `ModuleNotFoundError` there.
- **Removing a helper.** Grep the whole repo for the symbol
  (`--include=*.py --include=*.sh --include=*.md`) first; tests call `impl.py`
  internals directly.
- **Scratch dirs.** Use a fresh `D=$(mktemp -d)` per run instead of
  `rm -rf`-ing an old one; destructive-command guards block the latter.
- **Rewriting unpushed commits.** Use
  `git reset --soft $(git merge-base HEAD origin/main)`, never
  `--soft origin/main`: if a fetch moved `origin/main`, upstream's new files
  get staged as reverted. Check `git diff --stat origin/main...HEAD` lists
  only your files before committing.
- **Commit titles ≤ 50 characters**, docs commits included:
  `commit-title-length-check` blocks a longer one, and a chained
  `git add && git commit` then aborts as a unit.
- **Chained cleanup traps.** Suites like `tests/test_codex_broker_reaper.sh`
  chain `EXIT` traps: each gate's cleanup calls the previous one by name. A
  gate inserted mid-file must also be named by the next gate's cleanup, or its
  fixture processes and temp dirs leak; check `pgrep -f <fixture>` and the
  temp-dir glob are empty after the run.
- **Prerequisite rows.** Leave the `README.md` / `AGENTS.md` prerequisite
  rows alone unless the change needs them: the two tier tables are kept
  identical by a drift check, and `AGENTS.md` has a
  word budget (see
  [Keeping `AGENTS.md` small](../CONTRIBUTING.md#keeping-agentsmd-small)).
  Put a skill's dependency note in the skill itself.
- **Transient CI failures.** A job that dies before linting with a GitHub API
  5xx is transient: find the run with
  `gh pr checks <N> --json name,link,workflow`, confirm `main`'s recent runs
  are green, then `gh run rerun <run-id> --failed`.
