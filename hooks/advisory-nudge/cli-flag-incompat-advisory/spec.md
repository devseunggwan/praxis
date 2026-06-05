# PreToolUse CLI Flag Incompatibility Advisory

Supported hosts: all

`hooks/advisory-nudge/cli-flag-incompat-advisory/impl.py` is the **advisory** counterpart to the
deny-mode `gh-flag-verify`. It nudges `<cli> <subcmd> --help` before known
mode-incompatible flag combinations land on external CLIs whose surface area
is too large or too version-fluid to safely hard-block.

### Why this exists

`gh-flag-verify` blocks unknown flags on a maintained `gh` COMPAT table.
The same friction recurs on other CLIs (`git`, `kubectl`), but:

- their subcommand surface is larger
- their flag tables shift across versions
- a hard block would have unacceptable false-positive cost

Memory entry `feedback_external_cli_verify_first` covers the "always run
`--help` before guessing flag combinations" rule and has been refreshed
for ≥1 prior incident, but the retrieval moment (command composition)
keeps failing. This hook moves a fragment of the enforcement to the Bash
boundary as a low-cost reminder (issue #248).

### What is detected

#### `git merge-tree` mode conflict

`git merge-tree` has a modern `--write-tree` mode and a deprecated
`--trivial-merge` mode. Modern flags (`--name-only`, `--write-tree`) only
work in modern mode, which is selected by **1 or 2** positional branch
arguments. Three positionals drops merge-tree into legacy mode and the
modern flag is rejected with the confusing message
`fatal: --trivial-merge is incompatible with all other options` — even
though the operator never wrote `--trivial-merge`.

| Command | Action |
|---------|--------|
| `git merge-tree --name-only abc HEAD origin/main` | **ADVISORY** — 3 positionals + modern flag |
| `git merge-tree --write-tree A B C` | **ADVISORY** |
| `git merge-tree --name-only $(git merge-base HEAD origin/main) HEAD origin/main` | **ADVISORY** (3 positionals expanded) |
| `git -C /tmp merge-tree --name-only A B C` | **ADVISORY** — `-C` is git global |
| `git merge-tree --name-only HEAD origin/main` | **SILENT** — 2 positionals, modern OK |
| `git merge-tree --merge-base=A --name-only HEAD origin/main` | **SILENT** — explicit merge-base (equals form) |
| `git merge-tree --merge-base A --name-only HEAD origin/main` | **SILENT** — explicit merge-base (space form) |
| `git merge-tree -X ours --name-only HEAD origin/main` | **SILENT** — `-X` consumes one value token |
| `git merge-tree --merge-base $(git merge-base HEAD origin/main) --name-only HEAD origin/main` | **SILENT** — unquoted `$(...)` run coalesced as one value |
| `git merge-tree --merge-base "$(...)" --name-only HEAD origin/main` | **SILENT** — quoted form already one token |
| `git merge-tree abc HEAD origin/main` | **SILENT** — no modern flag (legacy form) |

#### `kubectl` deprecated flag

Known deprecations:

| Flag | Reason |
|------|--------|
| `--use-protocol-buffers` | Removed in kubectl ≥1.27 — protobuf is auto-negotiated |

| Command | Action |
|---------|--------|
| `kubectl --use-protocol-buffers get pods` | **ADVISORY** |
| `kubectl --use-protocol-buffers=true get pods` | **ADVISORY** (`=value` form) |
| `kubectl --use-protocol-buffers exec pod -- mytool` | **ADVISORY** — flag is kubectl-side, before `--` |
| `kubectl exec pod -- mytool --use-protocol-buffers` | **SILENT** — flag is the in-container command's arg, after `--` |
| `kubectl get pods -n default` | **SILENT** |

### Response format

```
stderr: "[cli-flag-incompat-advisory] <cli> <category>\n<body>"
exit 0
```

Advisory-only: the hook **never blocks** and never emits JSON. The user
sees the stderr text and decides whether to re-run with the suggested
fix or proceed.

### Adding a new CLI

The hook uses a plugin-style registry:

```python
def _check_<cli>(argv: list[str]) -> Optional[str]:
    argv = strip_prefix(argv)
    if not argv or argv[0] != "<cli>":
        return None
    # Return advisory text or None
    ...

CHECKS = (_check_git, _check_kubectl, _check_<new_cli>)
```

Each check function returns `Optional[str]` (the advisory text). The hook
walks every command segment, runs every check, and emits the first hit.

### Parsing guarantees (fail-open)

- malformed JSON stdin → exit 0
- non-Bash tool → exit 0
- empty / whitespace command → exit 0
- uncaught exception in inner logic → swallowed, exit 0
- `python3` unavailable → shell wrapper exits 0

### Relationship to sibling hooks

| Hook | Scope | Overlap |
|------|-------|---------|
| `gh-flag-verify` | `gh <subcmd>` — deny on unknown flag from COMPAT table | None — gh-only, this hook covers other CLIs |
| `block-gh-state-all` | `gh search --state all` | None — gh-only |
| `verify-commit-flag-override` | `git commit --no-verify` etc. | Complementary — covers commit safety, this hook covers shape errors |

### Tests

```bash
bash hooks/test-cli-flag-incompat-advisory.sh
```

26 cases: 7 advisory (4 git merge-tree variants, 3 kubectl including
flag-before-`--`), 17 silent (correct merge-tree usage including
space-form `--merge-base`, `-X`, `--strategy-option`, unquoted `$(...)`
command substitution, equals-form `--merge-base=$(...)`, quoted
`"$(...)"`, `kubectl exec -- mytool --flag` remote-arg shadowing,
unrelated commands, comment), 2 infrastructure (non-Bash passthrough,
malformed JSON fail-open).
