# Stop Hook Read-Only Verification Deferral Gate

Supported hosts: all

`hooks/completion-verify/readonly-verify-deferral-gate/impl.py` (run by the `Stop`
dispatch group, `hooks/_dispatch.sh Stop -`, since issue #1281) fires on every `Stop` event and emits
a stdout `{"systemMessage": ...}` JSON advisory when the last assistant turn **offers** to run a
read-only verification ("should I check ...?", "진행할까요?") instead of just
running it and pasting the result.

## Why this exists

A read-only command (a `SELECT`, a `kubectl get`, a `--dry-run`, a `git status`)
carries no mutation risk. CLAUDE.md states that read-only calls auto-proceed and
that user deferral is reserved for mutations (write API, push/merge, delete,
message send, infra changes). The recurring failure mode (Loaded≠Retrieved
family) is the assistant *having the means* to produce evidence and instead
handing it back as a yes/no question — adding a round-trip and training the user
to approve things that need no approval.

This hook is the inverse of `completion-signal-gate`:

| Hook | Failure mode it catches |
| ------ | ------------------------ |
| `completion-signal-gate` | Claims **done** *without* producing evidence |
| `readonly-verify-deferral-gate` | **Offers** to produce read-only evidence instead of producing it |

Both live under the `completion-verify` role and fire independently on the same
`Stop` event.

References: issue [#641](https://github.com/devseunggwan/praxis/issues/641).

## What is detected

A three-signal AND gate over the last assistant turn's text, plus an
already-ran suppressor:

```text
(read-only intent ∧ deferral phrasing ∧ ¬already-ran cue  within ONE sentence)  ∧  ¬mutation carve-out
```

**Same-sentence requirement.** Signal A and Signal B must co-occur within a
single sentence (text is split on `.!?` and newlines). A genuine read-offer
puts the command and the deferral in one sentence ("should I run `SELECT …`?"),
whereas a declarative read *report* closing with a generic courtesy phrase
("Ran `git status` — clean. Let me know if you need more.") or a deferral about
the *next* step ("`SELECT …` returned 1024. Should I proceed?") splits them
across sentences — those do not warn. The mutation carve-out, by contrast, is
turn-global: a mutation anywhere in the turn suppresses the whole turn.

### Signal A — read-only verification intent

Built-in, **project-agnostic** patterns (case-insensitive):

| Pattern | Example |
| --------- | --------- |
| `SELECT … FROM` | "should I run `SELECT count(*) FROM orders`?" |
| `SHOW TABLES/CATALOGS/SCHEMAS/…` | "want me to `SHOW TABLES`?" |
| `DESCRIBE <table>` | "shall I `DESCRIBE users`?" |
| `kubectl get/describe/logs/top` | "should I `kubectl get pods`?" |
| `git status/log/diff/show/branch/remote/rev-parse` | "want me to check `git status`?" |
| `aws <svc> get-/describe-/list-` | "should I `aws s3 list-buckets`?" |
| `gh … view/list/status` | "shall I `gh pr view`?" |
| `--dry-run` | "should I run it with `--dry-run`?" |

**Project extension (normalization):** a project that wants to recognise its own
read-only CLIs sets `PRAXIS_READONLY_VERIFY_SIGNALS` to a single regex
(typically an alternation). It is OR-ed with the built-in patterns. An empty or
invalid value is ignored (fail-open). No project-specific token is hard-coded in
the hook — this keeps the hook reusable across repos.

```sh
# Example: a project recognises two of its own read-only commands
export PRAXIS_READONLY_VERIFY_SIGNALS='\bmycli\s+inspect\b|\bmycli\s+show\b'
```

### Signal B — deferral phrasing

The phrasing that turns "I ran the read" into "do you want me to run it?".

**EN (word-boundary-safe):** `should I check/run/fetch/query/verify/look/…`,
`shall I …`, `(do you )?want me to …`, `would you like me to …`,
`let me know if you …`.

**KO (substring):** `진행할까요`, `진행 여부`, `확인할까요`, `조회할까요`,
`실행할까요`, `검토할까요`, `살펴볼까요`, `원하시면`, `필요하시면`, `알려주세요`,
`말씀해 주세요`, …

### Mutation carve-out — suppress when asking is legitimate

When the deferred action is (or is mixed with) a **mutation**, asking first is
correct and no advisory fires. Over-matching here only costs missed read-only
warns, which this gate prefers over false-warning a real approval gate.

- Command/verb forms (case-insensitive): `git push`, `gh pr merge/create/edit`,
  `kubectl apply/delete/scale/patch/exec/…`, `git commit/merge/rebase/reset`,
  `curl … -X POST/PUT/PATCH/DELETE` or `--data`, `terraform apply/destroy`.
- SQL/HTTP write keywords (**case-sensitive, uppercase only**):
  `POST PUT PATCH DELETE INSERT UPDATE ALTER DROP TRUNCATE MERGE CREATE GRANT REVOKE`
  — uppercase-only so prose ("update the doc", "post a comment") does not trip it.
- KO markers: `머지`, `배포`, `삭제`, `제거`, `전송`, `발송`, `푸시`, `커밋`, …

### Already-ran suppressor — past-tense run cue in the offer sentence

If the offer sentence *also* carries a past-tense run cue (EN: `ran`, `executed`,
`checked`, `fetched`, `queried`, `pulled`, `looked up`; KO: `실행했`, `확인했`,
`조회했`, `돌렸`, `받았`, …), the read was already executed and the deferral is
about *showing / continuing*, not about running it ("I ran `git status` — want me
to show the diff?", "`SELECT …` 를 실행했고 결과를 확인할까요?"). That offer
sentence is skipped.

This is keyed off the **offer sentence text**, not the turn's tool history. A
coarse "any read tool ran this turn" check would wrongly suppress a *mixed* turn —
the assistant runs read A, then in the final message offers a different read B
("ran `git status`, then `Should I run SELECT …?`"). B was never run, so it must
still warn; the offer sentence for B carries no past-run cue, so it does
(issue #641 codex review, P2).

## Response

Advisory arrives as a single stdout `{"systemMessage": ...}` JSON object
(issue #647 H3); stderr stays empty.

```text
[praxis:readonly-verify-deferral-gate] a read-only verification was OFFERED instead of run: the last turn names a read-only command (SELECT…FROM, kubectl get/describe/logs, git status, --dry-run, …) and defers it to the user ('should I check?', '진행할까요?').
[praxis:readonly-verify-deferral-gate] Rule: CLAUDE.md 'Read-only calls auto-proceed' — a read carries no mutation risk, so just run it and paste the result. Do NOT hand it back as an option. Deferral is reserved for mutations (write/push/merge/delete/send/deploy).
[praxis:readonly-verify-deferral-gate] If the deferred action is actually a mutation, name it explicitly so the carve-out recognises it. Bypass: PRAXIS_READONLY_VERIFY_BYPASS=1; strict (decision: block): PRAXIS_READONLY_VERIFY_STRICT=1.
```

## Tiers / env vars

| Env var | Effect |
| --------- | -------- |
| (default) | Advisory: stdout `{"systemMessage": ...}` JSON + exit 0. Never blocks. |
| `PRAXIS_READONLY_VERIFY_STRICT=1` | Escalate to `{"decision": "block", "reason": ...}` — Stop is blocked and the reason is fed to the model so it re-runs the read. |
| `PRAXIS_READONLY_VERIFY_BYPASS=1` | Silence the hook entirely (exit 0, no scan). |
| `PRAXIS_READONLY_VERIFY_SIGNALS=<regex>` | OR-ed into Signal A to recognise project-specific read-only CLIs. |

Exit-2-on-Stop semantics match the sibling `merge-state-claim-gate`: Claude Code
treats a Stop hook `{"decision": "block"}` as "block the stop and feed the
reason to the model" (equivalent to the exit-2 stderr form, but structured).

## KNOWN LIMITATION — output-scan proxy

This hook polices the **articulation** of a deferral, not the behaviour. It is a
nudge, not a guarantee, and it has three structural blind spots:

1. **Vague offer** — an offer that names no command token ("want me to look into
   it?") has no Signal A match and is missed.
2. **Silent-incomplete answer** — answering thinly *without* offering anything is
   strictly worse behaviour, yet produces no Signal B match and is missed.
3. **Gameable** — rewording the same deferral to dodge the patterns (the wrong
   incentive) suppresses the advisory.

It also has a residual **false-positive** direction (the same-sentence rule
removes the common cases but not all):

1. **Same-sentence false pair** — a read command and an unrelated deferral that
   happen to land in the *same* sentence still pair (e.g. "should I, after
   `git status`, redesign this?"). Rare, but possible.
2. **`--dry-run` of a mutation** — `--dry-run` is treated as a read signal, so
   offering a dry-run *preview of a deletion/apply* ("want me to `--dry-run` the
   deletion?") warns even though previewing a mutation is a reasonable thing to
   ask. A dry-run carries no actual mutation risk, so running it is fine — but
   the nudge can misfire here.

Because the strongest variant (a vague or silent deferral) is the one this scan
cannot see, and a residual false-positive surface remains, the hook is
intentionally advisory by default. Promote to STRICT only in a context where the
operator accepts these blind spots.

## Parsing guarantees

| Condition | Behavior |
| ----------- | ---------- |
| Malformed / missing stdin JSON | exit 0 (silent pass) |
| `PRAXIS_READONLY_VERIFY_BYPASS` set | exit 0 (silent pass) |
| `stop_hook_active` is true | exit 0 (re-entry guard) |
| Missing / unreadable `transcript_path` | exit 0 (silent pass) |
| Empty transcript / no assistant text | exit 0 (silent pass) |
| `python3` unavailable / `.py` missing | exit 0 (shell shim guards) |
| Any uncaught exception | exit 0 (`@fail_open`) |

Stdlib only (`json`, `os`, `re`, `sys`, `pathlib`); no external dependencies.

## Tests

```bash
python3 -m pytest tests/hooks/completion-verify/test_readonly_verify_deferral_gate.py -v
```
