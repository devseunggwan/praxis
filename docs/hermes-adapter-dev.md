# Hermes adapter development

How to write or review a change to the Hermes Agent adapter:
`hooks/_lib/_hermes.py` (payload and transcript translation) and
`plugins/hermes/bridge.py` (the hook Hermes runs). What the adapter does —
event and tool mapping, transcript rebuild, the ask → self-check design,
approval keys, install — is described in
[ARCHITECTURE.md → Hermes Agent adapter](../ARCHITECTURE.md#hermes-agent-adapter)
and not repeated here. For changes to a hook's own decision, follow
[hook-development.md](hook-development.md) first.

An adapter change is correct only if (a) it matches what Hermes actually
writes, and (b) the hooks reading the result behave as they do under Claude
Code, or the difference is intended and documented. A change to how the
adapter translates decisions or transcripts also updates the ARCHITECTURE.md
section, as its own `docs(hermes):` commit.

Paths below use `$HERMES_HOME` (Hermes's state dir, `~/.hermes` by default)
and `$HERMES_SRC` (a checkout of the Hermes Agent source).

## 1. Check the Hermes side against Hermes source

- Compare against `$HERMES_SRC`, and record its version
  (`git -C "$HERMES_SRC" log -1 --format='%h %cs'`) in the report.
- Any marker constant the adapter copies (skill scaffold prefix, instruction
  markers, footer sentence) must be byte-identical to Hermes's definition.
  `grep -n` each constant in `agent/skill_commands.py` and
  `agent/skill_bundles.py`.
- Read every **builder** that emits the shape, not only the extractor.
  Builders have conditional parts the extractor does not mention: the skill
  message writes its skill-directory footer only when there is a skill dir,
  and bundle and stacked-bundle blocks use different `[Loaded as part of the …`
  wording. A parser anchored on an optional part needs a fallback, and you
  must decide which direction that fallback fails (§3).

## 2. Check the Claude side against a real Claude transcript

"Written as Claude Code writes it" needs a real sample. Find a JSONL with both
`"isMeta":true` and `<command-name>/`, and print the user records around the
slash command: Claude Code writes the typed record first
(`<command-message>…</command-message>\n<command-name>/x</command-name>[<command-args>…]`)
and the `isMeta` skill body after it. When the adapter emits a different order
or format, list which readers notice:

- `git grep -n "read_last_user_message\|read_last_user_record" -- 'hooks/**/impl.py'`.
  Default readers (`human_only=False`) see whichever record comes last.
- `git grep -n 'isMeta\|<command-name>' -- 'hooks/**/*.py'`. Some readers skip
  `isMeta`; some treat `<command-name>` as a host opener.
- `hosts` in `hooks/manifest.json`: a `["claude"]`-only hook never sees Hermes
  output.

### Host-written user rows

Hermes stores its own notifications as `role='user'` rows; only
`messages.display_kind` tells them apart. Find the full set with
`SELECT role, display_kind, count(*) FROM messages GROUP BY 1,2`. Copying
those rows as plain user text makes every "last human message" reader treat a
notification as a typed turn — which, for example, closes the merge gate's
window and discards the briefing. Map each host kind to Claude Code's
`origin: {kind: …}`, keep `steer` (the user's out-of-band message) human, keep
unknown kinds and databases without the column human so they fail closed, and
select `display_kind` only when `PRAGMA table_info` lists it.

## 3. Drive Hermes's real builders into the adapter

Import Hermes modules from a scratch dir with the interpreter Hermes itself
runs on, with `$HERMES_SRC` and the worktree's `hooks/_lib` on `sys.path`.
Builders lazily import config code that needs third-party deps; stub only
those side paths (a fake `tools.skills_tool` with `_skills_dir`, no-op
`preprocess_skill_content` / `_load_skills_config` / `_inject_skill_config`)
so the scaffold assembly stays Hermes's own, and name the stubs in the
report.

Build one case per builder branch: with and without a skill dir, with and
without an instruction, with a runtime note, bundle and stacked bundle, a body
that quotes a marker, an instruction that quotes the footer. Print the
adapter's output next to Hermes's own extractor
(`extract_user_instruction_from_skill_message`). A disagreement is a finding
only once you have named its direction:

- **Fail-open:** body text is read as the user's words, so an approval gate
  could pass.
- **Fail-closed:** the user's words are lost, so the gate blocks and asks
  again.

## 4. Replay real rows from `state.db`

Synthetic fixtures alone are not the functional test. Open the database
read-only (`sqlite3.connect("file:$HERMES_HOME/state.db?mode=ro", uri=True)`),
select the affected rows with Hermes's own SQL pattern, run each through the
adapter, and count agreement with Hermes's extractor. Report the counts with
the query as scope.

For "the defect is real" and "HEAD fixes it", run the same rows through both
refs in one script: write `git show <ref>:hooks/_lib/_hermes.py` to a scratch
file per ref, load each with `importlib.util.spec_from_file_location`, set
`db.row_factory = sqlite3.Row`, and call `transcript_events([row], {})`.
Bucket each row (whole scaffold read as typed / instruction / bare command /
other); `main` putting every row in the first bucket is the defect shown on
real data. Grade the evidence:

| Evidence | Grade |
| --- | --- |
| Real `state.db` rows | `PASS(live)` |
| Builder probe with stubbed config paths | `PASS(mock)`, stubs named |
| Unit tests and CI | `PASS(mock)` |
| Gate replay carried from an earlier body, not re-run on HEAD | `Unverified` |

## 5. Fixing an ambiguous marker parse

The user's text can contain anything, so a parse cut from the marker scaffold
is never fully unambiguous. Pick the anchor that keeps the user's words in the
common case and makes the rest fail closed:

- Anchor on a structural line the builder writes **between** body and
  instruction (for a single skill, the line starting `[Skill directory:` after a blank line), not on
  prose a user is likely to quote (the footer sentence). Take the last
  instruction marker, and reject it when that structural line still follows
  it: the body quoted the marker.
- When the builder omits the structural line, fall back to Hermes's own
  extractor behavior, pin that case in a test, and count how many real rows
  lack the line so the report can say how rare it is.

## 6. Decisions: self-check by default, human approval for consent only

The design is in ARCHITECTURE.md → *Asks* and *Approval*. Its rationale, for
reviewers: praxis gates exist to make the agent re-check, not to collect human
approvals. A prompt that shows the user a gate's checklist and leaves nothing
to decide is the bug. Fix the ask → human mapping, never one gate at a time
(grouping more gates, exempting certain repos in one gate); a consent the
human must really give is asked in chat with the content shown.

Constraints on the self-check path, each from a review finding:

- Hermes runs tool calls in parallel. Take and consume the record under
  `_state_lock.state_lock`, and write through a unique
  `tempfile.NamedTemporaryFile(dir=…)` + `os.replace`; a fixed `.tmp` path
  collides, and an unlocked read-modify-write lets two identical re-issues
  pass on one record.
- Test the race as serial equivalence, not "exactly one passes": N identical
  calls on one record alternate pass/record, so 8 processes give 4 passes and
  leave one record. Prove RED by disabling `fcntl` in `_state_lock.py` (it
  fails most runs), then restore it.
- Cap each gate's reason separately, never the joined message: a slice of
  the whole message silently drops a later gate's checklist.
- `split_asks`: when a consent gate and a self-check gate both ask, block for
  the self-check first; the re-issued call then reaches the human with only
  the consent asks.
- Never deliver agent-facing text on the pass path as `context` alone; Hermes
  discards a context-only `pre_tool_call` response.

Constraints on the human-approval path (`approve` + `rule_key`; Hermes stores
an "always" answer profile-wide as `plugin_rule:<rule_key>`):

- Never key by hashing only the reason. Reasons quote the call, so the key
  never re-matches and the user is re-prompted forever — yet a reason that
  happens to be constant makes one "always" cover every later call. A per-call
  key hashes the reasons **and** the tool call's arguments.
- Grouping is opt-in: only gates on `GROUPED_APPROVAL_HOOKS` are keyed by gate
  plus fixed category tags. A denylist of "dangerous" gates fails open — every
  gate added later is grouped by default.
- Only a gate whose ask is a nudge about call shape, or a routine low-stakes
  check, may join the allowlist; never a gate whose ask is consent to an
  action (merge, cross-boundary write, fan-out, session intent, re-consent
  after a rejection, `destructive-bash-guard` in strict mode). Check the
  gate's `hosts` first, and pin each addition with a parametrized test (same
  gate, two calls → equal keys; same gate behind a per-call gate → different
  keys), proven RED by stashing only `_hermes.py`.
- Every asker counts, not the emitted one. The dispatcher emits only a
  group's first ask, so a low-stakes gate early in manifest order hides a
  per-call gate behind it (`git commit -m <long> && rm -rf …`). Collect
  `(member, stdout)` for every asker through `run_group(..., asked_by=list)`;
  if any asker is outside the allowlist, the whole key is per-call. List all
  askers in the message, per-call gates first, so the length cap never cuts
  them.
- A grain tag added to a reason (`[prod]`) is a fixed constant pinned in that
  hook's test. Tags derived from user text only make the key finer, which is
  the safe direction.
- Changing `_run`'s return shape in the bridge breaks tests that monkeypatch
  it with a tuple lambda and the bridge's second caller (`pre_llm_call`);
  grep both before running the suite.

### Bridge probe: main vs branch

Pipe the same payloads into `main`'s and the branch's `plugins/hermes/bridge.py`
with an isolated `HERMES_HOME` and `PRAXIS_HOME`, and print `action` and
`rule_key` side by side:

```json
{"hook_event_name": "pre_tool_call", "tool_name": "terminal",
 "tool_input": {"command": "…"}, "cwd": "<scratch git repo>",
 "session_id": "<sid>", "extra": {"tool_call_id": "t"}}
```

- Include two calls of the same gate with different arguments (equal keys on
  the branch is the fix; different keys on `main` is the defect), and the
  reverse cases — a per-call gate asking behind a groupable one, and gates
  that read the session (send a `pre_llm_call` with the prompt first) — which
  must differ per call. Use a scratch `git init` repo as `cwd` so commit gates
  fire.
- For a self-check change, send the same payload twice with the same
  `PRAXIS_HOME`: a self-check block, then `{}`, proves no human prompt
  remains.
- **Pass both bridge paths as absolute paths.** The bridge runs with `cwd` set
  to the scratch repo, so a relative path misses the script, stdout is empty,
  and the empty result reads exactly like `{}` — a pass on every row. Keep one
  row that must block (a bare `gh pr merge 1 --squash` in a git repo) in
  every run so this failure shows. `hermes hooks test` returns `{}` for the
  same input, so it is not a control.

### Transcript-gate probe through the bridge

The bridge rebuilds the transcript from `state.db` by `session_id`; a
`transcript_path` in the payload is ignored. Build a temp
`$HERMES_HOME/state.db` with
`messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id, role, content, tool_call_id, tool_calls, tool_name, active DEFAULT 1)`:
user rows, assistant rows whose `tool_calls` is
`[{"id","type":"function","function":{"name":"terminal","arguments":"{\"command\":…}"}}]`,
tool rows keyed by `tool_call_id`, and the pending call's assistant row last.
Then run the bridge probe above with that `session_id` and the pending call's
id, and print the action plus the `[praxis:<gate>]` names.

## 7. Replaying Hermes questions through a menu gate

`clarify` maps to `AskUserQuestion`, and a hook with no `hosts` runs on both
hosts, so every AskUserQuestion gate already covers Hermes questions. To
measure a new menu gate's precision on them (the stop rule in
[hook-development.md §1](hook-development.md#1-before-proposing-a-new-gate)):

1. Open `$HERMES_HOME/state.db` and each `$HERMES_HOME/profiles/*/state.db`
   read-only, limited to a window (for example the last 28 days by
   `timestamp`).
2. Collect answers: from `role='tool'` rows with `tool_name='clarify'`,
   parse `content` as JSON and map each `responses[].question` to its
   `user_response`, keyed by `tool_call_id`.
3. Collect questions: from `role='assistant'` rows whose `tool_calls`
   mention `clarify`, parse `tool_calls`, keep calls whose
   `function.name` is `clarify`, then parse `function.arguments` — a JSON
   string nested inside the JSON.
4. For each question, build the payload the bridge builds —
   `{"hook_event_name":"PreToolUse","tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":…,"options":[{"label":c} for c in choices]}]}}` —
   pipe it into the gate's `impl.py`, and count a fire on rc 2 or any
   stdout/stderr.
5. Print `questions=N fired=M (p%)`, then one line per fire with the
   question tail, labels and answer, and hand-label each.

That per-fire output quotes real conversations and can carry private data or
secrets: keep it local (a scratch file outside the repository). A PR or report
gets the aggregate counts and labels, or examples rewritten with synthetic
text — never the raw question tails or answers.

Decode `function.arguments` before searching: non-ASCII text is escaped
inside it, so a SQL `LIKE '%<text>%'` on `tool_calls` silently matches
nothing. Count distinct questions; retries repeat rows.

Positive control that the live bridge covers questions: pipe a
`pre_tool_call` `clarify` payload whose last choice is an end-the-task option
into the configured bridge and expect `block-ask-end-option`'s block.

## 8. Auditing a live install

- **Registration.** `hermes hooks list` must show `plugins/hermes/bridge.py`
  under `pre_tool_call` as allowed.
- **Firing.** When the session has an id and `$HERMES_HOME/state.db` exists
  with a readable `messages` table, the bridge rewrites the session transcript
  on every call, so `ls -lt ~/.praxis/cache/hermes-transcripts | head` shows
  the current session's file with a fresh mtime. `sync_transcript` returns
  `None` without writing when any of those is missing or unreadable, and the
  bridge still runs the gates with an empty `transcript_path`. So no fresh
  file means "not firing" only after those prerequisites are confirmed; when
  one fails, report "transcript unavailable" and lean on Registration plus the
  positive control above (a payload the bridge must block) instead.
- **Which gates can ask.**
  `grep -rln '"ask"' hooks --include=*.py | grep -v test`; say the list is by
  grep (a file may only block in practice).
- **Historical asks have no complete record.** An approved prompt leaves
  nothing in `state.db` or the logs; `[praxis:<gate>]` strings there appear
  only for blocked calls and in messages that quote the format. Report the gap
  instead of a number; `grep -c 'plugin_rule:' "$HERMES_HOME/config.yaml"`
  counts stored "always" answers.
- **"praxis keeps asking": find the gate from the stuck session.** In that
  session's rows, assistant `tool_calls` whose `id` has no `role='tool'` row
  with that `tool_call_id` are the calls waiting right now. Replay each through
  the configured bridge (`hermes config get hooks.pre_tool_call.<i>.command`)
  with a temp `PRAXIS_HOME` and print the action and `[praxis:<gate>]` lines:
  that names the gate and shows the checklist the user is being asked about.
  Then apply §6, not an allowlist addition.
- **"Does it apply to every session?"** Answer per mechanism. The bridge is a
  fresh process per tool call, so updating the checkout its configured path
  lives in reaches every open session at its next call. Settings Hermes
  caches per process apply only to new sessions. Other profiles under
  `$HERMES_HOME/profiles/*/config.yaml` carry their own `hooks:` — grep them
  for `plugins/hermes/bridge.py` before claiming coverage.
- **Moving the bridge to a new path needs first-use consent for that exact
  command string.** Without it, Hermes skips the hook at the next start with
  only a log line, and `fail_closed` does not cover a skipped hook — every
  praxis gate silently goes off. Back up `config.yaml`, change the one line,
  approve it at the consent prompt, and confirm `hermes hooks list` shows it
  allowed. Do not use accept-all flags (`--accept-hooks`,
  `HERMES_ACCEPT_HOOKS=1`), which approve every hook. A running session keeps
  its startup registration (the old path) until restart, so keep that copy at
  the same version until then.
- **`CLAUDE_PLUGIN_ROOT` / `CLAUDE_PLUGIN_DATA` are unset** in the agent's
  shell under Hermes. A skill or script that resolves paths or state through
  them falls back (the codex companion writes state under its `$TMPDIR`
  fallback); probe each such path on a Hermes host before trusting a step
  that reads it.

## 9. Review hygiene for this area

- Verify every symbol the PR body cites exists (`git grep -n <symbol> HEAD`);
  bodies often name a reader function that was since renamed.
- Re-run the test file at HEAD and compare its count with the body; review
  rounds add cases.
- Before keeping a finding that says the adapter diverges from what the user
  typed, check how Hermes itself renders the same thing (e.g.
  `describe_skill_invocation`). If the adapter matches Hermes's rendering,
  drop the finding: changing it would diverge from Hermes.
- When CI's `test` job passed on the same SHA, cite that run instead of
  re-running the full suite locally, and say so.
