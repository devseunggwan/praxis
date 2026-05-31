# PreToolUse block-child-repo-issue-create

Supported hosts: all

`hooks/preflight-gate/block-child-repo-issue-create/impl.py` intercepts every
Bash tool call and hard-blocks `gh issue create` when the target `--repo`
belongs to a hub-mediated org's child repo, redirecting the agent to the
correct hub creation skill instead.

## Why this exists

Some orgs use a dedicated hub repo as the canonical issue tracker for
cross-team routing, label assignment, and multi-repo tracking. Creating issues
directly on child repos within such an org bypasses the hub fan-out and
convention enforcement, fragmenting tracking across repos and causing /cancel
cycles when an issue filed in a child repo must be migrated to the hub.

The hook is opt-in via `PRAXIS_HUB_MEDIATED_ORGS` (see
[docs/hub-mediated-orgs.md](../../../docs/hub-mediated-orgs.md)).
**When the env var is unset / empty the hook is a NO-OP** — praxis itself is
not a hub-mediated org, so out-of-the-box no `gh issue create` is affected.

## What is blocked

A `gh issue create` call is blocked (exit 2) when ALL hold:

1. `PRAXIS_HUB_MEDIATED_ORGS` is set and contains at least one valid entry.
2. The `--repo` / `-R` value's org matches a configured hub-mediated org.
3. The `--repo` / `-R` value is **not** the hub repo itself for that org.
4. `PRAXIS_HOOK_BYPASS_HUB_ENFORCE` is unset / empty.

| Situation | Action |
|-----------|--------|
| `PRAXIS_HUB_MEDIATED_ORGS` unset | **PASS** (NO-OP) |
| `gh issue create --repo example-org/child` (org configured) | **BLOCKED** |
| `gh issue create --title "a; b" --repo example-org/child` (separator inside quoted title) | **BLOCKED** (quote-aware tokenization; issue #514) |
| `grep -rn "gh issue create --repo example-org/child" .` (literal in a quoted string) | **PASS** (not a real invocation; issue #514) |
| `gh issue create --repo example-org/hub` (this IS the hub) | **PASS** |
| `gh issue create --repo other-org/anything` (org not in allowlist) | **PASS** |
| `gh issue create` (no `--repo` flag) | **PASS** |
| `gh issue edit` / `gh issue comment` / `gh issue close` | **PASS** |
| `PRAXIS_HOOK_BYPASS_HUB_ENFORCE` set (non-empty) | **PASS** |
| Malformed `PRAXIS_HUB_MEDIATED_ORGS` entry | **PASS** (fail-safe) |
| Malformed JSON stdin | **PASS** (fail-open) |
| non-Bash tool call | **PASS** |

## Config env var

`PRAXIS_HUB_MEDIATED_ORGS` — comma-separated entries, each:

```
<org>:<hub_repo>:<creation_skill>
```

`creation_skill` may itself contain colons (e.g. `example-org:create-hub-issue`).
Parsing splits each entry on the **first two colons only**; the remainder is
treated as `creation_skill` verbatim. See [docs/hub-mediated-orgs.md](../../../docs/hub-mediated-orgs.md)
for the full schema and examples.

## Escape hatches

- **`PRAXIS_HOOK_BYPASS_HUB_ENFORCE`** — set to any non-empty value to bypass
  for the session. Use when a direct child-repo issue is genuinely intentional.
- **No `--repo` flag** — commands without an explicit repo flag pass silently
  (the hook cannot resolve the repo without shell environment access).
- **Malformed or missing config** — fail-safe pass; no valid entry matched.

## Tests

```bash
bash tests/hooks/preflight-gate/test_block_child_repo_issue_create.sh
```

Covers: no-config NO-OP, child-repo block (space and `=` forms, `-R` short
flag), hub-repo pass, org-not-in-allowlist pass, bypass env, non-create
subcommand pass (`edit`/`comment`/`close`), no-`--repo` pass, creation_skill
colon parsing, malformed config entry fail-safe, malformed JSON fail-open,
non-Bash tool pass.
