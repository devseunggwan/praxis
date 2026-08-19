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
3. No acknowledgement is present — neither the `# approval-premise:ack` comment
   in a Bash command nor the `approval_premise_ack` MCP argument.

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

## Known ceiling

The gate checks that an approval record **exists, names a justification, and
names this target**. It cannot check that the justification is **true**. Routing
an unverified value through a schema check converts it into something that reads
like independent confirmation — the same circle `Own-greencheck and SUT-comment
are not evidence` describes. Reach is partial by construction, and this section
exists so a future reader does not mistake a passing gate for a verified premise.

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

```json
{
  "matcher": "Bash|mcp__.*",
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
lost no true positive. One false positive survives on purpose:
`airflow_import_errors` reads import errors rather than importing, and dropping
"import" to silence it would lose `gitbook_git_import`, a real mutation.

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

`git branch`, `tag`, `remote`, `worktree` and `config` are absent from the
allowlist on purpose: each has a write form one flag away (`git branch -D`,
`git remote add`). `gh api` and `aws` are admitted only in their query shapes —
`gh api` without a method or body flag, `aws` under a `describe-` / `get-` /
`list-` style verb.

## Not yet done

- The blast-radius axis in the message is prose. Making it a required structured
  field is the part of issue #1043 that would actually mechanize; this draft only
  asks the question.
