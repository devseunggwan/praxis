# Hub-Mediated Orgs — Config Schema

The `block-child-repo-issue-create` hook enforces the hub-mediated issue
workflow at the tool-call layer. It is opt-in: when `PRAXIS_HUB_MEDIATED_ORGS`
is unset or empty the hook is a NO-OP and no `gh issue create` is affected.

## What is a hub-mediated org?

A hub-mediated org uses a single designated repo (the "hub") as the canonical
issue tracker. All new issues should be filed in the hub, which then fans out
labels, assignees, and references to the relevant child repos. Creating issues
directly on child repos bypasses this routing layer.

## Env-var schema

### `PRAXIS_HUB_MEDIATED_ORGS`

Set this in your shell profile (e.g. `~/.zshrc`, `~/.bashrc`) or in the
Claude Code session environment (e.g. via `settings.json`).

**Format:** comma-separated entries, each with the form:

```
<org>:<hub_repo>:<creation_skill>
```

| Field | Description | Example |
| ------- | ------------- | --------- |
| `org` | GitHub org name (case-insensitive) | `example-org` |
| `hub_repo` | Full `org/repo` of the hub (case-insensitive) | `example-org/hub` |
| `creation_skill` | Skill to invoke instead (may contain colons) | `example-org:create-hub-issue` |

**Parsing rule:** each entry is split on the **first two colons only**. The
remainder after the second colon is the `creation_skill` verbatim. This means
`creation_skill` values such as `my-org:create-hub-issue` (containing one
colon) are handled correctly.

Malformed entries (missing colons, empty fields) are silently skipped — the
hook remains a NO-OP for that entry.

### Example

```bash
export PRAXIS_HUB_MEDIATED_ORGS="example-org:example-org/hub:example-org:create-hub-issue,other-org:other-org/tracker:other-org:open-tracker-issue"
```

With this config:
- `gh issue create --repo example-org/any-child-repo` → **BLOCKED** (points to `Skill("example-org:create-hub-issue")`)
- `gh issue create --repo example-org/hub` → **PASS** (hub repo itself)
- `gh issue create --repo other-org/any-child-repo` → **BLOCKED** (points to `Skill("other-org:open-tracker-issue")`)
- `gh issue create --repo unrelated-org/anything` → **PASS** (org not in config)
- `gh issue create` (no `--repo`) → **PASS** (cannot resolve repo)

### `PRAXIS_HOOK_BYPASS_HUB_ENFORCE`

Set to any non-empty value to bypass the hook for the session:

```bash
PRAXIS_HOOK_BYPASS_HUB_ENFORCE=1 gh issue create --repo example-org/child --title "..."
```

Use sparingly — only when a direct child-repo issue is genuinely intentional
(e.g. a repo-local bug that does not need hub routing).

## See also

- Hook spec: [`hooks/preflight-gate/block-child-repo-issue-create/spec.md`](../hooks/preflight-gate/block-child-repo-issue-create/spec.md)
- Hook impl: [`hooks/preflight-gate/block-child-repo-issue-create/impl.py`](../hooks/preflight-gate/block-child-repo-issue-create/impl.py)
- Block message format: [`docs/hook/block-message-format.md`](hook/block-message-format.md)
