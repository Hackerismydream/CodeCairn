# CodeCairn Agent Instructions

CodeCairn is an auditable local memory runtime for coding agents.

## Working rules

- Read `CONTEXT.md` and relevant `docs/adr/` files before changing contracts.
- Treat provider traces as untrusted input and LLM output as untrusted claims.
- Evidence fields are derived from normalized events; an LLM may summarize but
  may not author provenance, role, command outcome, file change, or quote.
- Markdown is durable truth. SQLite is operational state. LanceDB is a
  rebuildable search index.
- Tests assert public behavior through CLI, HTTP, or a service interface.
- Never publish a benchmark number without a checked-in run manifest and raw
  aggregate inputs.
- Documentation and code are English. Test fixtures may contain other
  languages when the behavior requires them.

## Architecture

Dependencies point inward:

```text
entrypoints -> service -> memory
                 ^          ^
                 |          |
             importers   storage adapters
```

`memory` owns domain records and invariants. `service` owns use-case
orchestration. `importers` and `storage` are adapters. `entrypoints` are thin
CLI and HTTP presentation layers.

## Authoritative checks

```bash
make format
make check
```

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for
`Hackerismydream/CodeCairn`; external PRs are not a triage surface. See
`docs/agents/issue-tracker.md`.

### Triage labels

The repository uses `needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository: use root `CONTEXT.md` and `docs/adr/`.
See `docs/agents/domain.md`.
