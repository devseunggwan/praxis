# Step 4 runbook: resolve and run codex-companion (codex-review-wrap)

Detailed procedure for [`../SKILL.md`](../SKILL.md) Step 4 sub-steps 4a
and 4b. The spine keeps the Step 4 preconditions (PR-state check, the
`Skill("codex:review")` prohibition); this file is the runbook the agent
follows once those pass. Numbering (4a/4b) is shared with the spine and
with cross-references from Step 5.

## 4a. Resolve the codex-companion.mjs path

Read the install path from the canonical Claude Code plugin manifest:

```bash
manifest="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json"
install_path=$(jq -r '.plugins["codex@openai-codex"][0].installPath // empty' "$manifest")
companion="$install_path/scripts/codex-companion.mjs"
```

If `$companion` is empty or the file does not exist:

1. Output: `"⚠ codex-companion.mjs not found — openai-codex plugin may not be installed."`
2. Offer alternatives via `AskUserQuestion`:
   - **`oh-my-claudecode:code-reviewer`** — Claude-based code review (equivalent quality)
   - **`Manual`** — output the diff for direct inspection; skip automated review
   - **`Cancel`** — abort the review
3. Act on the selection:
   - `oh-my-claudecode:code-reviewer` → `Skill("oh-my-claudecode:code-reviewer")` with cwd set to `{selected_path}`
   - `Manual` → run `git diff origin/<base-branch>..HEAD` in `{selected_path}` and exit
   - `Cancel` → abort silently with one-line message

**Record the resolved path in the ledger, on every first round of a
target** — including the ordinary case where `codex-companion.mjs`
resolved and no question was asked:

```text
review-path: target={worktree-path}#{branch} | round={N} | path={codex-companion | code-reviewer | manual}
```

A round re-entered by 5j reads this row instead of re-resolving or
re-asking (5j → *Re-entry*, item 4), so without the row the re-entry
instruction has nothing to read. Match on `target=`, same as `sibling-id:`.

The script derives its own ROOT_DIR via `import.meta.url`, so passing the
absolute script path to `node` is sufficient — `CLAUDE_PLUGIN_ROOT` does
not need to be set.

## 4b. Run the review

Change working directory to the selected worktree, then invoke the
companion. `{{ARGUMENTS}}` passes any flags through unchanged. The review
path reads `--model`, `--base`, `--scope`, and `--json`. It also *accepts*
`--wait` and `--background` — both sit in its `booleanOptions` and are then
never read, so neither changes anything; `options.wait` is consumed only by
`status`, `options.background` only by `task`.

**Always run this via `Bash(..., run_in_background: true)`.** A review
routinely runs past a foreground tool timeout, and a timed-out call kills
it mid-run: the round then ends with no findings, which is
indistinguishable from a clean review. There is no flag that avoids this —
`review` has no background path of its own. `handleReview` →
`handleReviewCommand` → `runForegroundCommand` is unconditional, and the
`detached: true` worker (`spawnDetachedTaskWorker`) is reachable only from
`task --background`.

Measured against `codex@openai-codex 1.0.6` (`scripts/codex-companion.mjs`):
`options.background` is read at exactly one line, inside `handleTask`.
Re-measure if the plugin version rises — a `review` background path would
end the no-op and make `run_in_background: true` optional. The
`adversarial-review` subcommand dispatches straight into that same
`handleReviewCommand`, so every claim in this step covers it unchanged.

```bash
cd -- "{selected_path}"
node "{resolved_companion_path}" review "{{ARGUMENTS}}"
```

A backgrounded call hands back a task id, not the review, so there is
nothing to return yet — say so in one line: "Codex review started in the
background. Check `/codex:status` for progress." Once the run completes,
return the script's stdout **verbatim** — do not paraphrase, summarize, or
add commentary. This matches `/codex:review`'s contract.

### Liveness — `ps` and log mtime, never `status` or `elapsed`

A killed review leaves its job file reading `status: "running"` with a dead
pid: the terminal write in `runTrackedJob` never executes, and `elapsed` is
computed from `startedAt` against the wall clock, so it keeps climbing
after the process is gone. Both fields report a dead job as a healthy one.
Judge liveness on two independent signals instead.

Measured against `codex@openai-codex 1.0.6` (`scripts/lib/tracked-jobs.mjs`):
`runTrackedJob` writes the terminal record in-process, after its runner
resolves. Re-measure if the plugin version rises — a terminal write moved to
a signal handler or an external supervisor would make `status` trustworthy
and retire this whole section.

The two signals need a pid and a log path, and **neither is in the
human-readable status output** — `pid` is never rendered there, and the log
line only appears for some job states. Take both from `--json`, which
carries the whole job record:

```bash
node "{resolved_companion_path}" status --cwd "{selected_path}" --json \
  | jq -r '.running[] | "\(.id) \(.pid) \(.logFile)"'
```

`--cwd` is not optional here. The companion keys its state directory on the
workspace root it derives from `process.cwd()`, and each Bash call starts
back at the session cwd rather than the worktree Step 2 selected — so
without it this command reads a different state directory and reports no
running job at all.

Empty output from that pipeline is **not** the same as "no job is running".
A failed `status` — wrong directory, unreadable state file, a companion
version that does not know `--json` — also prints nothing, and `jq` exits 0
on the empty input, so the pipeline's status says nothing either. Read the
companion's own exit code before drawing any conclusion from silence, and
treat a non-zero one as *unknown*, never as *stale*. This matters because
the next paragraph turns "no matching process" into a cancel.

`.logFile` is an absolute path under the companion's own state directory
(`$CLAUDE_PLUGIN_DATA/state/<workspace-slug>-<hash>/jobs/`, falling back to
`$TMPDIR/codex-companion/…`) — it is not relative to the worktree, so use
the value the command prints rather than constructing one. Then:

```bash
ps -p {pid} -o pid=,etime=,stat=     # empty output → process is gone
find "{logFile}" -mmin +5            # prints the path → no write in 5 min
```

**Only the first is decisive.** A dead pid settles it: the job is stale,
whatever the log says. A quiet log does not — the companion appends a line
per item lifecycle event and runs no periodic heartbeat, so one long
reasoning or command item produces no write at all while the review is
perfectly healthy. Read a quiet log as *tell the user this is taking a
while*, never as grounds to cancel. Cancel on the pid.

`find -mmin` is used rather than `stat`, whose mtime syntax splits BSD from
GNU (`stat -f` on macOS, `stat -c` on Linux); the reaper script next door
resolves that split at run time with a BSD-then-GNU-then-python3 `mtime()`
(issue #1302). Quote the path — the state directory is relocatable and may
sit under a name containing spaces.

`status: "running"` with no matching process is a **stale** job, not a
progressing one. Cancel it (`node "{resolved_companion_path}" cancel --cwd
"{selected_path}" {job-id}`) and re-launch; polling it longer never
resolves. `cancel` resolves its state directory the same way `status` does,
so it needs the same `--cwd`.

### When the review completes

Backgrounding defers Step 5, it does not skip it. Which path the findings
take depends on where the session is when the run completes:

- **User is back in an interactive foreground turn** — collect the findings
  and enter Step 5 from the top (interactivity check → 5f → …); the
  interactivity check passes and 5i can ask.
- **No user is reachable** (unattended worker, `-p` run, session already
  ended) — the interactivity check fails and the findings take the
  non-interactive path: verified, applied to nothing, deferred.

Either way, never apply a review's findings from the completion notification
alone.
