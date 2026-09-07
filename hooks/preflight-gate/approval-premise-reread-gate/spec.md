# approval-premise-reread-gate

Supported hosts: all

`PreToolUse` gate on irreversible production calls. Tracked by issue #1043.

## Decision predicate

The gate emits `ask` when **all** of the following hold:

1. The call is a mutation — a `Bash` command that is **not** provably read-only,
   or an MCP tool one of whose leaf-name tokens is a mutating verb
   (`MUTATING_MCP_VERBS`). Read-only calls are out of scope; a gate that fires
   on queries becomes the noise it exists to replace. The two branches ask the
   question from opposite ends, and the section below says why.
2. The call's arguments carry a production phase marker (`PROD_MARKER_RE`).
3. No acknowledgement is present. Each surface is read only where it is
   declared: the `# approval-premise:ack <premise>` **unquoted comment** in the
   Bash command, and for MCP a **hook-owned file** at
   `~/.praxis/cache/approval-premise-ack-<session_id>.json` (`PRAXIS_HOME`- and
   `PRAXIS_STATE_DIR`-aware via `resolve_cache_file`) holding
   `{"premise": "<one line>"}`. Both must carry a non-empty premise string — a
   bare marker, an empty string, a boolean `true` and a malformed file are all
   rejected.

Anything else passes silently. A malformed payload fails open.

## Why `ask` and not a block

The gate cannot evaluate whether the approval premise still holds — that fact
lives in observations made between the approval and this call, which no hook can
see. What it can do is force the question to be answered out loud at the call
site. Both originating failures had the disqualifying observation already
recorded, in the agent's own words, in the same turn as the execution.

## The acknowledgement is an attestation, not a bypass

`# approval-premise:ack <one-line premise re-read>` asserts that the premise was
re-read and states what it now says. Attaching it without having done so is a
false attestation, and the rules treat that as the documented path by which a
gate becomes decorative.

**The statement is the acknowledgement, so the gate requires one.** A bare
marker, a blank premise, and a `{"premise": true}` ack file all say the field
was set and nothing about what the premise now says; each is rejected.

**The MCP acknowledgement is a file, not a tool argument, and it is consumed on
read.** An MCP call has no comment surface, and its arguments belong to the
server's schema: a synthetic `approval_premise_ack` field is rejected outright
by any server that validates its input, so the quiet path it described was
unreachable at runtime for exactly the tools that validate most strictly. The
file is hook-owned, session-keyed like every other praxis cache entry, and
claimed with an atomic `os.rename` before it is read — one acknowledgement
covers one call, so no shape of it disables the gate. The claim is what makes
that true under concurrency: reading and then unlinking lets two hook processes
for the same session both open the file before either removes it, and one
acknowledgement then passes two calls. The gate's own message prints the resolved path, because an
opt-out nobody can find is an opt-out that does not exist.
What the gate cannot check is whether the sentence is *true* — see
[Known ceiling](#known-ceiling).

**Each surface is parsed where it is declared**, never by searching the
serialized input. A substring scan accepts `# approval-premise:ack` sitting
inside an unrelated MCP field — a runbook quote, a description, a log line —
which no one wrote as an attestation, and which the caller cannot even see.

On the Bash surface "where it is declared" means the command's **first unquoted
comment**, not a substring of the command. The opener is located in the raw text with quote
state intact rather than in the token stream: `safe_tokenize` strips quote
delimiters, so after it `'#'` and a real comment opener are the same token and
`bash -c '<mutation>' '#' approval-premise:ack …` attests for a command it only
quotes. Without that,
`gh api -X POST repos/o/prod-svc/issues -f body='# approval-premise:ack ...'`
attests with its own request body — the mutation carries the words that excuse
it. Two further positions are refused for the same reason: a marker inside an
*earlier* comment (`# note # approval-premise:ack …`) is prose about the
marker, and a marker on an *earlier line* attests for that line rather than for
a mutation below it — the tokenizer ends a line with `;`, so a `;` after the
marker means a command follows.

## Known ceiling

The gate checks that an approval record **exists, names a justification, and
names this target**. It cannot check that the justification is **true**. Routing
an unverified value through a schema check converts it into something that reads
like independent confirmation — the same circle the *Own green check and SUT comment are not evidence* rule
([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)) describes. Reach is partial by construction, and this section
exists so a future reader does not mistake a passing gate for a verified premise.

## The message is the shared five-field block

The reason text is rendered by `hooks/_lib/block_message.py`, like every other
preflight gate, so the two questions live in the `Correct path` field rather
than in hand-rolled prose. `bypass_env` is `None` on purpose: the only way past
this gate is the acknowledgement, which is an attestation the agent writes after
re-reading, and an environment variable would let a session switch the gate off
for itself.

## Originating failures

- **PREMISE_DISSOLVED** — approval granted on "we have to run it to see whether
  the failing step passes". Before execution, a direct query showed the failing
  run had already recovered; that observation was written up in the same turn.
  The call fired anyway, on live customer data.
- **COHORT_INHERITED** — a three-axis blast radius measured on the first target
  of a cohort, inherited by two more without re-measurement. The third was a
  different failure mode whose deletion steps had never run, leaving two axes
  unmeasured for it. Observed, written down, and triggered three seconds later
  with no new approval surface in between.

## Registration

Two manifest entries (#1239): the `Bash` leg runs inside the `PreToolUse(Bash)`
dispatch group, the `mcp__.*` leg stays a standalone node.

```json
{
  "matcher": "mcp__.*",
  "hooks": [
    {
      "type": "command",
      "command": "$CLAUDE_PLUGIN_ROOT/hooks/preflight-gate/approval-premise-reread-gate/impl.py"
    }
  ]
}
```

## Input surface

Both classifiers were measured rather than guessed, against the 374 MCP tools
exposed in one session and an enumerated list of marker spellings.

**MCP leaf names are matched by token, not by substring.** Splitting the leaf on
`_` and `-` and intersecting with the verb set is a strict subset of substring
matching on that surface: it dropped eight read-only tools — `list_labels` via
"label", `s3_count_records` via "record", `figma_get_component_sets` and
`signoz_query_alert_preset` via "set", `shared_memory_read` via "share" — and
lost no true positive. Three false positives survive on purpose, each because
silencing it costs a real mutation: `airflow_import_errors` reads import errors
rather than importing, but dropping "import" loses `gitbook_git_import`;
`mysql_resolve_user_connectors` is a query, but dropping "resolve" loses a
review-thread resolve; the `merge_readiness_*` tools are local session state,
but dropping "merge" loses `merge_pull_request`.

**The verb set is this repository's write vocabulary, not a fresh reading.**
`pr-claim-mutation-gate/spec.md` already classifies `submit` / `resolve` /
`dismiss` / `merge` as GitHub MCP writes and `pipefail-advisory` adds `close` /
`reopen`. A verb this repository elsewhere calls a mutation must not read as
read-only here, so those — plus the irreversible operations verbs adjacent to
them (`approve`, `revoke`, `restore`, `truncate`, `purge`, `rollback`,
`deploy`, `publish`, `execute`, and their neighbours) — are in the set.

**A global flag before the verb is skipped with its value.** `kubectl --context
prod-apne2 get pods` and `kubectl --context get delete pod` are both misread
when the flag's value is taken for the subcommand — the first asks for nothing,
the second passes a delete as read-only. The value-taking and boolean global
flags are transcribed per binary from `kubectl options`, `git --help`,
`aws help`, `docker --help` and `gh --help`. A flag in neither set is
unrecognised: the verb's position is then unknowable, so the classifier gives
up and the gate asks. A future release adding a global flag lands there rather
than through the gate.

**The marker absorbs spacing and casing, but stops at named flags.** Accepted:
`--phase prod`, `--phase=prod`, `--phase  prod`, `--phase PROD`, `-p prod`,
`--profile prod`, `--env prod-apne2`, `--phase production-mirror`, and the
quoted forms a serialized MCP argument produces (`{"phase": "prod"}`).

Bare `production` is still deliberately **not** a marker. The read-only filter
below narrows the Bash branch but does not make it exhaustive, and a marker that
common would lean on the filter for every namespace query.

## The Bash branch recognises read-only shapes, not mutating ones

Both branches now require a mutation. The MCP side asks whether the leaf name
names one; the Bash side asks the inverse — is this invocation *provably*
read-only? — and stays quiet only then.

The direction is the whole design. A mutation allowlist goes **silent** on the
call it exists to catch whenever the list is incomplete, and no list of shell
mutations is complete. Recognising read-only shapes instead makes an unknown
command fall through to `ask`: a gap costs one question, never a missed prod
mutation. That also keeps this out of the shell-parser corner-case spiral, since
the answer to "did I miss a form?" is a question rather than a hole.

Three properties carry it, each pinned by a test:

- **Every segment must be read-only, not just the first.** Segments come from
  `safe_tokenize` + `iter_command_starts`, so `kubectl get … | xargs kubectl
  delete` is two segments and the second one decides. A first-segment-only read
  is how a gate goes quiet on a deletion.
- **The subcommand is read by position, never by membership.** `gh` is
  noun-verb (`gh pr view`) while the rest are verb-first, so the position is
  per-binary. Scanning every token for a read-only word would pass `gh pr create
  --title view` and `git commit -m log`.
- **A state-changing redirect disqualifies the command** regardless of its
  verbs, via the existing `has_state_changing_redirect`.
- **a substitution anywhere in the command** — `$(…)`, a backtick, `<(…)` or
  `>(…)`. The outer binary's name says nothing about what the shell runs
  inside: `echo "$(kubectl delete pod x -n prod-apne2)"` is an `echo` by every
  test the allowlist applies, and the delete still executes. Recognising the
  inner command means parsing it, so the allowlist declines the whole command
  instead. `$((…))` is arithmetic, not a substitution, and is exempt.

A binary is on the bare-name allowlist only when it is read-only under *every*
argument. `find`, `yq`, `sort`, `uniq` and `date` therefore are not: each writes
under a flag or a second positional (`find -delete`, `yq -i`, `sort -o`,
`uniq in out`, `date -s`), which makes its read-onlyness a property of the
arguments rather than of the command. Admitting one buys a quiet path in
exchange for the guarantee the allowlist exists to give.

`git branch`, `tag`, `remote`, `worktree` and `config` are absent from the
allowlist on purpose: each has a write form one flag away (`git branch -D`,
`git remote add`). `gh api` and `aws` are admitted only in their query shapes. `aws` needs a
`describe-` / `get-` / `list-` style verb. `gh api` is classified token by
token against the complete flag list from `gh api --help`, because it reaches
every verb the service has and a write is one flag away:

- the **effective method** decides, and it is computed before the body flags
  are judged: an explicit `-X` / `--method` wins, otherwise a body flag
  (`-f` / `--raw-field`, `-F` / `--field`, `--input`) implies POST and a call
  with neither is a GET;
- a body flag is therefore not decisive on its own — `gh api --method GET
  search/issues -f q=...` sends those fields as query parameters, exactly as
  `gh api --help` describes, and asking for approval on it is a false positive;
- long options accept `--name=value` and short ones an attached value
  (`-ftitle=x`, `-XPOST`), so every token is split before it is classified;
- **an unrecognised flag returns not-read-only.** The list is closed on purpose:
  a flag added by a future gh release must fall through to `ask`, not through
  the gate.

## Not yet done

- The blast-radius axis in the message is prose. Making it a required structured
  field is the part of issue #1043 that would actually mechanize; this draft only
  asks the question.
