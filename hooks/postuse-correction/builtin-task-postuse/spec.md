# PostToolUse Built-in Task Classification

Supported hosts: claude (the omc `pre-tool-enforcer` false positive this hook corrects exists only in the Claude Code ecosystem — issue #1158)

`hooks/builtin-task-postuse.py` fires after any built-in task **management**
tool executes and emits a corrective context note so Claude is not misled by
upstream hook false positives.

### Why this exists

Claude Code ships two distinct sets of `Task*` tools with completely different
semantics:

| Tool | Role | Spawns subagent? |
| ------ | ------ | ----------------- |
| `Task` | Agent spawner | **Yes** |
| `TaskCreate` | Create task list entry | No |
| `TaskUpdate` | Update task list entry | No |
| `TaskGet` | Read task list entry | No |
| `TaskList` | List task list entries | No |
| `TaskStop` | Cancel task list entry | No |
| `TaskOutput` | Read task output | No |

Some upstream hooks (e.g. OMC `pre-tool-enforcer`) conflate the management
tools with `Task` and emit misleading "agent spawn" signals for them. This
PostToolUse hook fires immediately after those tools execute and injects a
correction note — "no subagent was spawned, prior signals were false positives"
— so Claude's subsequent reasoning reflects the actual operation.

### Covered tools

`TaskCreate`, `TaskUpdate`, `TaskGet`, `TaskList`, `TaskStop`, `TaskOutput`

### Tests

`tests/test_builtin_task_postuse.sh` covers 18 cases: corrective output for
all 6 management tools, silent pass-through for `Task` / `Agent` / `Bash` /
`Edit` / `Write` / `Read` / `Skill`, and edge cases (empty stdin, malformed
JSON, missing tool field). Run before editing the hook:

```bash
./tests/test_builtin_task_postuse.sh
```
