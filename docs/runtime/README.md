# Runtime Scope

This document separates the implemented baseline from the accepted version 0.1
runtime. For behavior available now, [`operations.md`](operations.md) is
authoritative. For the target lifecycle, use
[`../v0.1/`](../v0.1/README.md).

## Owned boundary

CodeCairn owns local coding-memory truth, lifecycle, search projection, recall,
and diagnostics. It does not own agent execution, hidden prompt injection,
general document ingestion, cloud tenancy, or a memory-editing UI.

Dependencies point inward:

```text
entrypoints -> service -> memory
                 ^          ^
                 |          |
             importers   storage adapters
```

`bootstrap` composes adapters. `evaluation` calls product/service contracts
with isolated roots.

## Implemented baseline: `954f728`

The accepted pre-development planning baseline is `2c79b3f`. Later
contract and guardrail commits do not change the product behavior below. The
guardrail layer now enforces the source budget in `make check`, classifies the
LoCoMo worker under `evaluation`, and verifies benchmark-v3 through a pure
historical reader.

### Implemented

- Codex and Claude Code JSONL detection and normalization.
- Stable Task Episodes, deterministic Evidence Facts, and resumable import.
- Automatic deterministic Failed Command memory.
- Service-only Evidence Gate paths for five other historical types.
- Prepared Markdown plus SQLite Import Ledger and Index Queue. Public import
  does not yet implement the version 0.1 cross-store Write Intent protocol.
- Public CLI/HTTP index sync, rebuild, status, and import-time drain.
- LanceDB parent/child projection and hierarchical Recall Context.
- Lazy retrieval-provider construction with typed configuration errors.
- Four evaluation suites and immutable public evidence.

### Not implemented

- complete four-type capture;
- durable Supersession, status history, or restore;
- MCP server;
- Claude Code or Codex hooks;
- `codecairn init`, config file, or automatic namespace derivation;
- a small release evaluation surface;
- tagged/package-curated open-source release.

## Current write paths

### Public trace import

```text
provider JSONL
  -> Agent Trace
  -> Task Episodes
  -> deterministic Evidence Facts
  -> deterministic Failed Command
  -> Markdown + SQLite + Index Queue
  -> bounded in-process index drain
```

The source cursor advances only after the complete durable write set commits.
Repeated import validates the committed prefix and resumes from the active
suffix. Provider-free import remains possible; retrieval configuration is
resolved only when an index operation needs it.

### Service proposal path

The baseline also implements `MemoryProposal -> EvidenceGate -> gate audit`
for User Preference, Repository Convention, Verified Fix, and Debug Episode.
`write_episode` supplies a similar Conversation Episode path for LoCoMo.
Neither is a complete public automatic-capture product.

These paths are intentionally removed by task `v01-001`, not expanded.

## Current storage

| State | Authority | Recovery |
|---|---|---|
| Coding Memory and evidence | Markdown | validate or audited deterministic repair |
| Import cursor, mirrors, audits, index outbox | SQLite | transaction and reconcile |
| Lexical/vector documents | LanceDB | delete and rebuild from Markdown |
| Evaluation run | immutable artifact directory | missing-only resume under exact manifest |

Import durability and search readiness are separate. A skipped or failed drain
leaves memory durable and index state degraded. Recall never silently scans
Markdown as a fallback.

## Version 0.1 target

The target replaces the historical write model with:

```text
Agent Trace
  -> exactly one Task Experience per Task Episode
  -> optional Repository Knowledge / User Preference / Work State
  -> immutable Evolution Record when a newer memory supersedes
  -> active-only Recall Context
```

It adds:

- one four-type Coding Profile;
- storage without mandatory verification;
- pending/retryable semantic capture;
- Write Intent crash recovery for Markdown/SQLite batches;
- forward-only Supersession and Restore;
- CLI, MCP, and post-session hooks;
- `init`, strict config, processing, and human diagnostics;
- source-code and release-evidence gates.

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
| Transport values/errors | CLI, MCP, hook, or compatibility HTTP |

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

Public behavior is asserted through CLI, MCP, hook, compatibility HTTP, or a
service interface. Concrete adapter tests prove transaction, safety, and
rebuild rules. Evaluation fixtures do not substitute for the installed product
smoke required by the release plan.
