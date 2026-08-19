# approval-premise-reread-gate

`PreToolUse` gate on irreversible production calls. Tracked by issue #1043.

## Decision predicate

The gate emits `ask` when **all** of the following hold:

1. The call is a mutation — a `Bash` command, or an MCP tool whose leaf name
   contains a mutating verb (`MUTATING_MCP_VERBS`). Read-only calls are out of
   scope; a gate that fires on queries becomes the noise it exists to replace.
2. The call's arguments carry a production phase marker (`PROD_MARKERS`).
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

## Not yet done

- No unit tests. The sibling gates carry `tests/` coverage; this draft does not.
- `MUTATING_MCP_VERBS` and `PROD_MARKERS` were derived from one session's call
  set, not from a survey of the MCP surface. Both need widening before this is
  anything but a draft.
- The blast-radius axis in the message is prose. Making it a required structured
  field is the part of issue #1043 that would actually mechanize; this draft only
  asks the question.
