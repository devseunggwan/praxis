# PreToolUse Secret-Print Redaction Advisory

Supported hosts: all

`hooks/advisory-nudge/secret-print-redaction-advisory/impl.py` nudges the
agent to mask at source when a Bash command or an agent-authored
verification script both (a) fetches a secret/token via a known fetch CLI
and (b) routes the fetched value to stdout unmasked — the value would land
verbatim in the session transcript, which is non-reversible.

## Why this exists (issue #827)

An agent-authored verification script fetched real OAuth tokens via a
read-only secret-fetch CLI and printed them in its per-item log lines
(`[SKIP] <id>: <token>`), exposing ~8 real tokens into the session
transcript. This was the **2nd recurrence across 2 sessions** — a memory
rule ("redact real secrets at the source of the agent's own writes")
already documented the exact pattern, but the failure happens at
*authoring time*, where behavioral memory is not retrieved. Memory-only
remediation has failed twice.

A runtime output scanner is infeasible:

- **PreToolUse cannot see the value** — it is fetched at runtime and absent
  from the command text.
- **PostToolUse fires only after** the value is already in the transcript.

So the only feasible gate is a PreToolUse heuristic on the *authoring*
command — this hook.

## Trigger criteria (2-signal AND gate)

The advisory fires only when **both** signals are present; either alone is
silent:

1. **Fetch invocation** — the scanned text contains a secret/token fetch:
   `aws secretsmanager get-secret-value`,
   `aws ssm get-parameter ... --with-decryption` (decryption flag required —
   the default call returns the still-encrypted value), `infisical secrets
   get` / `infisical export`, `vault kv get` / `vault read`, `op read` /
   `op item get`, `gh auth token`, `kubectl get secret`, python
   `get_secret_value(`. Subprocess list-form calls
   (`subprocess.check_output(["vault", "kv", "get", ...])`) are
   matched via a quote/comma/bracket-normalized copy of the line.

   The builtin list carries **public tools only**. Org/author-internal fetch
   CLIs extend it via `PRAXIS_SECRET_FETCH_CLIS` — comma-separated command
   phrases (e.g. `hubctl token fetch`), each matched as its
   whitespace-separated tokens in order, word-bounded, on both the raw and
   normalized copies (issue #1157; `hubctl` used to be a shipped literal).
   The `hubctl` examples through the rest of this spec assume that env is
   set, as the test suite sets it for the incident-replica fixtures.
2. **Unmasked output flow** — one of:
   - a variable captured from a fetch (`VAR=$(fetch)` / backtick / python
     assignment on a fetch line) later appears unmasked in an output sink
     (`echo`, `printf`, `print(`, `logger.*(`, `logging.*(`,
     `console.log(`);
   - the fetch is substituted inline into an output command with no capture
     (`echo "$(hubctl token fetch x)"`);
   - **authored tier only**: a bare no-capture fetch line (incl.
     `fetch | jq -r .SecretString` passthrough) — the script's stdout lands
     in the transcript when executed.

## 2-tier scan

| Tier | Scope | Rules applied |
| ------ | ------- | --------------- |
| **Live Bash command** | the command text outside heredoc bodies | var-flow + inline no-capture substitution only. A bare interactive fetch (`hubctl token fetch p --phase dev`) is **SILENT** — it is the sanctioned read-only usage (the *Read-only prod calls auto-proceed* rule, [`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)) |
| **Authored content** | Write `content`, Edit `new_string`, heredoc bodies inside a Bash command, echo/printf-appended quoted strings (accumulated per redirect target, so a capture appended by one `echo >>` and a sink appended by the next are linked) | var-flow + inline substitution **plus** bare no-capture fetch lines |

## Exclusions (silent — no advisory)

- **Bare interactive fetch (live tier)**: `hubctl token fetch p --phase
  dev` alone — sanctioned read-only use.
- **File-type gate**: Write/Edit targets — and heredoc/append redirect
  targets — ending `.md` / `.txt` / `.rst` are not scanned. Documentation
  legitimately contains fetch+echo example text; without this gate the hook
  would self-trigger on its own spec.md (this file).
- **Masked output**: `${VAR:0:6}` / `${VAR: -4}` substring expansions
  (any `${VAR:...}` / `#` / `%` / `/` transform), python `[:6]` / `[-4:]`
  slices, or a `REDACTED` / `mask(` / `***` marker on the statement.
- **Digest sinks**: `| wc`, `| sha*sum`, `| md5sum`, `| b2sum`, `| cksum`,
  `head -c` — only a length/hash derived from the secret reaches the
  transcript.
- **Stdout diverted to a file**: `echo "$TOKEN" > out.log` — the value
  lands in the file, not the transcript (`2>` stderr-only and `>&2` fd-dup
  do not divert stdout and are not exclusions; `&>` diverts both and is).
- **Usage without printing**: `curl -H "Authorization: Bearer $TOKEN"` —
  `curl` is not an output sink; passing the token to an API is the intended
  use.
- **No fetch anchor**: `TOKEN="literal"; echo "$TOKEN"` — a variable never
  assigned from a fetch is not tracked.

## False-positive / false-negative table

| Surface | Behaviour |
| --------- | ----------- |
| Heredoc-authored script with capture+echo (incident replica) | **ADVISORY** — heredoc bodies are the primary detection target |
| Live `VAR=$(fetch); echo "$VAR"` | **ADVISORY** — var-flow in the live command |
| `echo "$(hubctl token fetch x)"` (inline, no capture) | **ADVISORY** — fetch substituted into an output command |
| Authored bare `aws secretsmanager get-secret-value ... \| jq -r .SecretString` | **ADVISORY** — passthrough line, stdout reaches transcript on execution |
| Live bare `hubctl token fetch p --phase dev` | Silent — sanctioned interactive read |
| `curl -H "Authorization: Bearer $TOKEN"` after a fetch | Silent — not an output sink |
| Masked echo / python slice / digest sink | Silent — masking recognized |
| Write to `.md` / `.txt` / `.rst` | Silent — file-type gate |
| Comment lines (`# fetch example`) | Silent — line-level comment skip |
| Label FP guard: `echo "TOKEN=${TOKEN:0:6}"` | Silent — the bare word `TOKEN` in the label is not double-read as a python-style reference when `$`-syntax references exist for that var |

## Detection design notes

Parsing is **raw-text, line-oriented regex** — deliberately NOT
`safe_tokenize`: heredoc bodies are the *primary detection target* here
(the incident script was authored via a Bash heredoc), the exact opposite
of `pipefail-advisory`'s heredoc-body exclusion. Statements are split on
newlines / `;` / `&&` / `||`; `|` is kept within the statement so digest
sinks stay visible. The heredoc opener regex excludes the `<<<` here-string
operator (mirrors `pipefail-advisory`).

## Examples

| Input | Action |
| ------- | -------- |
| Bash heredoc: `TOKEN=$(hubctl token fetch p)` + `echo "[SKIP] $id: $TOKEN"` | **ADVISORY** |
| Write `verify.py`: `token = subprocess.check_output(["hubctl", "token", "fetch", "p"])` + `print(f"{i}: {token}")` | **ADVISORY** |
| `SECRET=$(vault kv get -field=token secret/x); echo "$SECRET"` | **ADVISORY** |
| `token = client.get_secret_value(SecretId="x")` + `logger.info("token=%s", token)` | **ADVISORY** |
| `echo 'TOKEN=$(hubctl token fetch p)' >> verify.sh` + `echo 'echo "$TOKEN"' >> verify.sh` | **ADVISORY** — appended strings accumulated per target |
| `TOKEN=$(hubctl token fetch p); echo "${TOKEN:0:6}****${TOKEN: -4}"` | **SILENT** — masked |
| `TOKEN=$(hubctl token fetch p); echo "$TOKEN" \| wc -c` | **SILENT** — digest |
| `hubctl token fetch p --phase dev` | **SILENT** — live bare interactive fetch |
| Write `spec.md` containing fetch+echo example text | **SILENT** — file-type gate |
| `P=$(aws ssm get-parameter --name x); echo "$P"` (no `--with-decryption`) | **SILENT** — encrypted value, not a plaintext fetch |

## Response format

```text
stderr: "[secret-print-redaction-advisory] fetched secret may reach stdout unmasked
          Detected: <reason>
            <statement snippet>
          ...mask helper snippets (bash mask() / python mask())..."
exit 0
```

Advisory-only: the hook **never blocks** and never emits JSON. The mask
helpers suggested:

```bash
mask() { printf '%s...%s' "${1:0:6}" "${1: -4}"; }
```

```python
def mask(s): return f"{s[:6]}...{s[-4:]}" if s else s
```

## Parsing guarantees (fail-open)

- malformed JSON stdin → exit 0
- tool other than Bash / Write / Edit → exit 0
- empty / whitespace command or content → exit 0
- uncaught exception in inner logic → swallowed, exit 0 (shared
  `@fail_open` guard)

## Known limitations

Heuristic only — advisory-scoped, false-positive cost dominates; the
residual gap is documented rather than chased:

| Case | Behaviour |
| ------ | ----------- |
| Env-var-only secret reads (`echo "$AWS_SECRET_ACCESS_KEY"`, `os.environ["TOKEN"]`) | Silent (false negative) — no fetch invocation anchor exists in the text; tracking every env var named `*TOKEN*`/`*SECRET*` would flood normal scripts with false positives |
| Fetch CLIs outside the enum (`doppler secrets get`, `berglas access`, custom wrappers) | Silent — the enum mirrors the CLIs observed in this environment; extend the enum on recurrence |
| Multi-hop variable passing (`A=$TOKEN; echo "$A"`) | Silent (false negative) — only the directly captured variable is tracked, one hop; alias-graph tracking is out of scope for a line-oriented advisory |
| Pre-existing script execution (`bash old-verify.sh` where the leak lives in the file, not the command) | Silent — PreToolUse sees only the invocation text; the authoring-time gate must have fired when the script was written |
| Quoted example text in a live Bash command (`gh issue create --body "TOKEN=$(fetch); echo $TOKEN"`) | May fire (false positive) — raw-text scanning cannot distinguish quoted example text from live statements without full shell parsing; acceptable for an advisory that never blocks |
| Sink vocabulary outside the enum (`click.echo`, `sys.stdout.write`, `fmt.Println`) | Silent — extend on recurrence |

## Tests

```bash
bash tests/hooks/advisory-nudge/test_secret_print_redaction_advisory.sh
```

Cases cover: the incident-replica heredoc authoring (P1), Write .sh/.py
authoring (P2/P3), live var-flow (P4), inline no-capture substitution (P5),
echo-append accumulation (P6), infisical/vault/op variants (P7), python
logger sink (P8), authored jq passthrough (P9), Edit new_string, gh auth
token, kubectl get secret; silent on curl-usage-only, masked echo, python
slice masking, placeholder assignment without fetch, no-fetch scripts,
`.md` file-type gate (Write and heredoc-redirect), digest sinks, live bare
fetch, ssm-without-decryption, and infrastructure fail-open (malformed
JSON, non-matching tool, empty/whitespace command). All fixture token
values are obvious fakes (`FAKE_TOKEN_xxxx`) — verified by
`tests/test_no_live_keys_in_fixtures.sh`.

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| ------ | ------- | --------- |
| `protected-paths-guard` | Edit/Write targeting sensitive files (`.env`, keys) | None — that hook guards the *storage* surface; this one guards the *transcript* surface |
| `external-api-literal-trigger` | unverified enum/identifier literals in Write/Edit/Bash | Shares the Write/Edit content-scan shape (`content` / `new_string` field map); different signal entirely |
| `pipefail-advisory` | mutating git/gh piped to truncating sinks | Opposite heredoc stance — that hook *excludes* heredoc bodies (example-text false positives); this hook *targets* them (authored scripts are the incident surface) |
