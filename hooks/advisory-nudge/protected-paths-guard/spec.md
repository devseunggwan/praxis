# PreToolUse Protected-Paths Write Guard

Supported hosts: all

`hooks/advisory-nudge/protected-paths-guard/impl.py` warns (or, in strict
mode, blocks) Edit / Write / NotebookEdit calls that target sensitive
credential files — `.env`, private keys, SSH config, `credentials`,
`.netrc`, `.npmrc`.

### Why this exists (issue #464)

`Yeachan-Heo/gajae-code` ships `examples/hooks/protected-paths.ts` (MIT)
which guards `.env` / `.git/` / `node_modules/` writes. praxis already has
branch and worktree guards but no `file_path`-based credential guard. The
agent has no structural backstop preventing it from writing tokens to
`.env`, dumping a private key for inspection, or rewriting `.npmrc` while
debugging a publish flow.

This hook adapts the gajae pattern with **path-component-exact matching**
(not substring), explicit allowlists for templates and test fixtures, and
the praxis fail-open / advisory-first contract.

### Trigger criteria

The hook fires when **all** are true:

1. `tool_name` is `Edit`, `Write`, or `NotebookEdit`.
2. `tool_input.file_path` (or `notebook_path` for `NotebookEdit`) is non-empty.
3. The path matches one of the detection rules below.
4. None of the skip rules below apply.
5. `PRAXIS_HOOK_BYPASS_PROTECTED_PATHS` is unset / empty.

### Detection rules

| Rule | Matches | Examples |
| ------ | --------- | ---------- |
| Basename-exact | `.env`, `.netrc`, `.npmrc`, `.git-credentials`, `.pgpass`, `credentials`, `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519` | `~/.netrc`, `aws/credentials`, `~/.git-credentials`, `~/.pgpass` |
| `.env.<env>` prefix | basename starts with `.env.` AND is not in the env allowlist | `.env.production`, `.env.local` |
| Extension | `*.pem`, `*.key`, `*.p12`, `*.keystore` | `keys/server.pem`, `tls/cert.p12` |
| Directory-component | any path component equals `.ssh`, `.aws`, `.kube`, or `.gnupg` | `~/.ssh/config`, `~/.aws/credentials`, `~/.kube/config`, `~/.gnupg/secring.gpg` |

#### Env allowlist (exact basename match)

`.env.example`, `.env.sample`, `.env.template`, `.env.defaults` — these are
canonical template names that the .env-prefix rule explicitly exempts.

#### Public-key allow (NOT under `.ssh/`)

`id_rsa.pub`, `id_dsa.pub`, `id_ecdsa.pub`, `id_ed25519.pub` — these are
public keys, safe to share. When the file is **not** under a `.ssh/`
directory component (e.g. `pubkeys/id_rsa.pub` in a config repo), the
public-key allow fires. When **under** `.ssh/`, the directory-component
rule still fires because the directory itself is the trust anchor.

### Skip rules (path-component / fragment based)

| Skip rule | Trigger |
| ----------- | --------- |
| Test fixtures | path contains `/fixtures/`, `/__fixtures__/`, `/test-data/`, `/testdata/`, or `/test_data/` directory component |
| Planning artifacts | an **absolute** path under `/tmp/` or `/private/tmp/` (macOS realpath form) after lexical normalization, or a path containing `/.omc/plans/` or `/.claude/projects/` after the same normalization. A relative `tmp/.env` is a project path, not scratch, and `/tmp/../proj/.env` resolves outside `/tmp/` — both get the advisory (#1362). The two tests live in [`hooks/_lib/_path_scope.py`](../../_lib/_path_scope.py), shared with `settings-path-advisory` |
| Self-edit | path is inside `CLAUDE_PLUGIN_ROOT` (so this very hook can be edited) |

### Examples

| Path | Action | Why |
| ------ | -------- | ----- |
| `.env` | **ADVISORY** | basename-exact |
| `path/to/.env` | **ADVISORY** | basename-exact |
| `.env.production` | **ADVISORY** | `.env.` prefix, not allow-listed |
| `.env.example` | **SILENT** | env allowlist |
| `.env.defaults` | **SILENT** | env allowlist |
| `.env.example.local` | **ADVISORY** | exact-match allowlist — `.example.local` is a project-specific override, not a canonical template |
| `.environment` | **SILENT** | basename differs, not `.env.<env>` |
| `node_modules_backup/.env_old` | **SILENT** | basename `.env_old` differs |
| `~/.ssh/id_rsa` | **ADVISORY** | directory-component (`.ssh/`) |
| `~/.ssh/id_rsa.pub` | **ADVISORY** | directory-component fires first |
| `~/.ssh/known_hosts` | **ADVISORY** | directory-component |
| `pubkeys/id_rsa.pub` | **SILENT** | public-key allow (not under `.ssh/`) |
| `~/.aws/credentials` | **ADVISORY** | directory-component (`.aws/`) |
| `~/.kube/config` | **ADVISORY** | directory-component (`.kube/`) |
| `~/.gnupg/secring.gpg` | **ADVISORY** | directory-component (`.gnupg/`) |
| `~/.git-credentials` | **ADVISORY** | basename-exact |
| `~/.pgpass` | **ADVISORY** | basename-exact |
| `keys/server.pem` | **ADVISORY** | extension `.pem` |
| `tls/cert.p12` | **ADVISORY** | extension `.p12` |
| `aws/credentials` | **ADVISORY** | basename-exact |
| `~/.netrc` | **ADVISORY** | basename-exact |
| `tests/fixtures/.env.local` | **SILENT** | test fixture skip |
| `src/__fixtures__/sample.pem` | **SILENT** | test fixture skip |
| `/tmp/scratch.env.production` | **SILENT** | planning artifact skip |
| `.omc/plans/sketch.env` | **SILENT** | planning artifact skip — a fragment matches a relative path too |
| `tmp/.env` | **ADVISORY** | relative, so not scratch (#1362) |
| `/tmp/../proj/.env` | **ADVISORY** | resolves outside `/tmp/` (#1362) |

### Modes

| Env var | Effect |
| --------- | -------- |
| (unset) | Advisory — stderr text, exit 0. Edit proceeds. |
| `PRAXIS_PROTECTED_PATHS_STRICT=1` | Block — stderr text + exit 2 (Claude Code blocks the call). |
| `PRAXIS_HOOK_BYPASS_PROTECTED_PATHS=1` | Full bypass — exit 0 silently. |

### Response format

```
stderr: "[protected-paths-guard] sensitive file write detected — ADVISORY
        Path: <path>
        Reason: <reason>
        Sensitive files leak credentials when committed. ...
        Bypass options: ..."
exit 0 (advisory) or 2 (strict)
```

### Parsing guarantees (fail-open)

- malformed JSON stdin → exit 0
- non-Edit/Write/NotebookEdit tool → exit 0
- empty / missing `file_path` → exit 0
- uncaught exception in inner logic → swallowed, exit 0

### Relationship to sibling hooks

| Hook | Scope | Overlap |
| ------ | ------- | --------- |
| `pre-edit-protected-branch-guard` | edits on protected git branches | None — this hook is file-pattern based, not branch-state based |
| `worktree-edit-gate` | edits to source files when HEAD is on a base branch | None — different defect class (workflow vs credential leak) |
| `external-write-falsify-check` | external-surface write commands (opt-in, off by default) | None — different tool surfaces (Bash vs Edit/Write) |
| `side-effect-scan` | gate on mutation CLI commands | None — Bash matcher, not Edit/Write |

### Known limitations

- **Symlink target**: the hook reads the path string from `tool_input.file_path`
  verbatim. If the path is a symlink to a protected file (e.g. `config.json` →
  `.env`), the hook does NOT follow symlinks; the unsuspecting basename
  (`config.json`) passes silently.
- **Custom credential filenames**: `.docker/config.json` is NOT guarded —
  it is a config file that may contain non-credential settings and the
  false-positive rate would be high. Authors that want to guard it can add
  a wrapper hook or extend the directory-component set. (`.aws/` and `.kube/`
  are now covered by the directory-component rule.)
- **Allow-list extension form**: `.pem.example` does NOT match the env
  allowlist (which is `.env.<allowed>` only). A test fixture named `.pem` is
  protected unless placed under a fixture directory.
- **Cross-repo plugin path**: when the file is inside a different praxis-style
  plugin (not the current `CLAUDE_PLUGIN_ROOT`), the self-edit skip does NOT
  fire, and writes to a plugin's own `.env.example` etc. are subject to the
  env allowlist as usual.

### Tests

```bash
bash tests/hooks/advisory-nudge/test_protected_paths_guard.sh
```

Cases cover: every detection rule (basename, .env prefix, extension,
directory), every allowlist (env templates, public keys, test fixtures,
planning artifacts, self-edit), false-positive guards (basename component
boundary, `.environment` vs `.env`, `_backup/.env_old`), strict-mode exit
code escalation, bypass env var, infrastructure fail-open.
