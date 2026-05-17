# Ethos

Why praxis exists. The values and principles that gate every skill, hook, and
manifest in this repository. Implementation choices (`DESIGN.md`) and component
graph (`ARCHITECTURE.md`) descend from these — they do not override them.

## Design Principles

- **CLAUDE.md is the interface**: no config files — project instructions define routing
- **SRP per skill**: each skill has one responsibility
- **Discipline over convenience**: Iron Laws gate each phase, no skipping

## Hook Ethos

Hooks exist because text rules in CLAUDE.md or memory entries alone have
historically failed at the moments they were needed most. These three
principles govern *whether* a hook should exist at all, and *what* it is
allowed to do to the user's session. The mechanisms by which a hook
implements these principles live in [`DESIGN.md`](DESIGN.md).

- **Spec defines, hook enforces.** Each hook is the structural enforcement of
  a rule that already exists in `CLAUDE.md` or a memory entry. Memory-based
  feedback alone has historically failed (≥5 recurrences) — hooks replace the
  memo when the pattern proves recurrent.
- **Fail-open on infrastructure errors.** Missing `jq` / `python3`, malformed
  JSON stdin, unreadable transcript, unknown tool name → exit 0. The hook
  never breaks Claude Code; it only nudges.
- **No agent-attachable bypass for high-stakes gates.** `pre-merge-approval-gate`
  intentionally has no marker; `completion-verify` and `retrospect-mix-check`
  same. Bypass marker (`# side-effect:ack`, `# title-length:ack`) exists only
  where the false-positive cost outweighs the silent-bypass risk.
