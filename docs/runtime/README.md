# Runtime Scope

This document summarizes the implemented runtime. For exact commands and
failure behavior, [`operations.md`](operations.md) is authoritative. Version
0.1 lifecycle documents remain the historical foundation; version 0.2 adds
Pico and recall admission; version 0.5 adds the Myna Person Library without
changing existing repository Memory identities.

## Owned boundary

CodeCairn owns local coding-memory truth, lifecycle, search projection, recall,
and diagnostics. It does not own agent execution, hidden prompt injection,
general document ingestion, cloud tenancy, or a general memory-editing UI.
Myna owns the local Person identity, explicit global User Preference
references, multi-scope Recall policy, and their narrow Hub presentation.

Dependencies point inward:

```text
entrypoints -> service -> memory
                 ^          ^
                 |          |
             importers   storage adapters
```

`bootstrap` composes adapters. `evaluation` calls product/service contracts
with isolated roots.

## Implemented runtime

Current `main` implements the local memory lifecycle:

- Codex and Claude Code JSONL normalization, stable Task Episodes,
  deterministic Source Facts, incremental checkpoints, and source-rewrite
  detection;
- one deterministic Task Experience per closed Episode plus bounded,
  retryable semantic proposals for the other three memory types;
- cross-store Write Intents, immutable Markdown truth, SQLite operational
  state, and recoverable crash boundaries;
- Supersession, active/superseded projection, history, and forward-only
  restore;
- active-only hybrid recall over LanceDB with bounded preflight, relevance
  admission and explicit abstention, pinned open Work State, provenance,
  ranking, omission, and token-budget sidecars;
- repository initialization, explicit provider profiles, doctor,
  export/reset, CLI, seven MCP tools, and one MCP resource;
- Claude Code `SessionEnd` and Codex `Stop` hooks with atomic/idempotent
  settings installation and bounded operational receipts;
- eight authoritative evaluation commands, deterministic lifecycle/scale/
  retrieval gates, lean paid-run contracts, a deterministic source gate, and
  pure verification of historical evidence;
- MIT-licensed curated wheel/sdist packaging, persistent-tool installation,
  installed CLI/MCP/lifecycle/evidence smoke, artifact inventory checks, and a
  maintained learner path.

- installed Pico Source Journal, importer, and repository-bound MemoryBackend.
- one stable local Myna Person per runtime root, immutable global User
  Preference promotion references, repository-over-global subject shadowing,
  one multi-scope Recall pass, and one closed Hub Governance write.

Version 0.1 release evidence and the first Pico campaign retain their original
commit bindings. The next product stages are maintained in
[`../roadmap.md`](../roadmap.md).

## Current write paths

### Trace import and hooks

```text
provider JSONL
  -> Agent Trace
  -> Task Episodes
  -> deterministic Source Facts and Task Experience
  -> one cross-store Write Intent
  -> Markdown + SQLite mirrors and semantic/index queues
  -> optional bounded index drain or recall preflight
```

The source cursor advances only after the complete durable write set commits.
Repeated import validates the committed prefix and resumes from the active
suffix. Hooks use explicit Stop/SessionEnd boundaries, call no model provider,
leave full processing queued, and record outcomes without blocking the client.

## Current storage

| State | Authority | Recovery |
|---|---|---|
| Coding Memory and evidence | Markdown | validate or audited deterministic repair |
| Myna Person and global promotion references | Markdown | validate references and fail closed |
| Import cursor, mirrors, audits, index outbox | SQLite | transaction and reconcile |
| Lexical/vector documents | LanceDB | delete and rebuild from Markdown |
| Evaluation run | immutable artifact directory | missing-only resume under exact manifest |

Import durability and search readiness are separate. A skipped or failed drain
leaves memory durable and index state degraded. Recall never silently scans
Markdown as a fallback.

The Person record is `library/person.md`; promotions are immutable Markdown
under `library/global-preferences/`. A promotion references an unchanged active
User Preference by repository key, Memory ID, and revision digest. It is never
a copied fifth Memory type. An active local preference with the same subject
shadows the global reference for that repository.

## Version 0.1 release remainder

The implemented memory flow is:

```text
Agent Trace
  -> exactly one Task Experience per Task Episode
  -> optional Repository Knowledge / User Preference / Work State
  -> immutable Evolution Record when a newer memory supersedes
  -> active-only Recall Context
```

The remaining release work is real installed-client lifecycle smoke through
the client UI/trust boundary and a clean implementation/evidence SHA pair.

The exact record contract is
[`../v0.1/schema-contract.md`](../v0.1/schema-contract.md); lifecycle policy is
[`../v0.1/memory-lifecycle.md`](../v0.1/memory-lifecycle.md). Target module
ownership and current-to-target mapping are in
[`../architecture.md`](../architecture.md).

## State transition ownership in version 0.1

| Transition | Owner |
|---|---|
| Provider JSONL to Agent Trace | importer adapter |
| Agent Trace to Task Episodes | memory domain called by import service |
| Episode to deterministic Task Experience | capture service using domain constructors |
| Optional Knowledge proposal | semantic provider port plus capture validation |
| Memory create | service Write Intent over Markdown/SQLite ports |
| Supersession/restore | evolution service and domain validation |
| Memory revision to search rows | Mini Cascade |
| Candidate selection to Recall Context | recall service |
| Transport values/errors | CLI, MCP, or hook |

Storage adapters never choose type cardinality, supersession, or recall policy.
Entrypoints never implement an alternative memory lifecycle.

## Error boundary

- malformed or mutated traces fail before their cursor advances;
- unsafe Markdown and conflicting durable identities fail closed;
- semantic absence preserves deterministic experience and records pending work;
- invalid automatic supersession records a rejected proposal outcome without
  failing completed semantic extraction;
- failed index work preserves durable truth and reports degradation;
- missing candidates return an attributed partial result, not invented memory;
- provider reachability alone never counts as successful inference.

## Test boundary

Public behavior is asserted through CLI, MCP, hook, or a
service interface. Concrete adapter tests prove transaction, safety, and
rebuild rules. Evaluation fixtures do not substitute for the installed product
smoke required by the release plan.

The version 0.5 contract and evidence boundary are maintained in
[`../v0.5/myna-person-library.md`](../v0.5/myna-person-library.md). This runtime
does not include a Myna Desktop process, Agent workbench, task runner, terminal,
or remote account/sync layer.
