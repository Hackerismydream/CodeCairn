# Runtime Scope

The CodeCairn runtime converts supported coding-agent session traces into
evidence-backed durable memory and compiles searchable memory into attributed
Recall Context.

This scope owns coding-memory truth and retrieval. It does not own agent
execution, hidden prompt injection, general document ingestion, cloud
multi-tenancy, or memory-editing UI.

## Relationship to the rest of the repository

```text
entrypoints ───────> service ───────> memory
                         ^               ^
                         |               |
                    importers         storage

bootstrap composes concrete adapters
evaluation calls the same service contracts with isolated artifacts
```

Dependencies point inward. `service` defines use cases and ports; it does not
import concrete `importers` or `storage` adapters. `entrypoints` depend on the
shared application facade rather than reimplementing behavior.

## Main concepts

| Concept | One-line definition |
|---|---|
| Agent Trace | Provider-independent normalized events with raw evidence references |
| Task Episode | Stable task-scoped extraction unit |
| Evidence Fact | Deterministic statement derived from normalized events |
| Coding Memory | Evidence-backed durable item in one of six memory types |
| Markdown Truth | Authoritative atomic representation of one Coding Memory |
| Import Ledger | SQLite cursor, audit, memory metadata, and recovery state |
| Index Queue | SQLite transactional outbox for Markdown revisions |
| Recall Episode | Rebuildable parent search projection |
| Atomic Fact Document | Rebuildable evidence-level child search projection |
| Recall Context | Budgeted Markdown output plus an auditable JSON sidecar |

The precise definitions and invariants live in
[`../../CONTEXT.md`](../../CONTEXT.md).

## Module ownership

| Module | Owns | Delegates |
|---|---|---|
| `importers` | JSONL detection, provider parsing, raw record validation, Agent Trace construction | Stable identities and evidence semantics to `memory` |
| `memory` | Domain records, evidence derivation, gates, projection, planner, retrieval provider contracts | Persistence and orchestration |
| `service.runtime` | Import, repair, proposal evaluation, durable memory orchestration | Parsing and persistence through ports |
| `service.cascade` | Reconcile, lease, retry, rebuild, and index parity orchestration | Truth, queue, and index operations through ports |
| `service.recall` | Candidate retrieval, routing, fusion, reranking, expansion, context compilation | Embedding, reranking, index, and state reads |
| `service.application` | Shared CLI/HTTP use-case facade | Evaluation and local operations through `ApplicationOperations` |
| `storage.markdown` | Atomic Markdown creation, parsing, validation, and repair | No operational state |
| `storage.sqlite` | Import ledger, gate audit, recovery audit, memory mirror, fact postings, and index outbox | No search ranking |
| `storage.lance` | Disposable lexical/vector projection and model-identity migration | No durable truth |
| `entrypoints` | Argument/request validation and presentation | All domain behavior |
| `bootstrap` | Concrete provider and adapter composition | No domain ownership |

## Public trace-import lifecycle

```text
supported JSONL source
        |
        v
provider detection or checkpoint provider
        |
        v
Agent Trace + stable raw evidence references
        |
        v
Task Episode segmentation
        |
        v
deterministic Evidence Facts
        |
        v
deterministic Failed Command extraction
        |
        v
atomic Markdown creation
        |
        v
SQLite import transaction
        |
        +--> memory metadata and audit
        +--> Index Queue row
        |
        v
ImportResult
```

`import_session` returns after Markdown and the SQLite transaction are durable.
It does not wait for LanceDB. Repeated import validates the committed prefix
and replays only the active suffix. Before new import work, committed Markdown
is checked and recoverable corruption is repaired through resumable audit rows.

Failed Command extraction does not call the proposal-oriented Evidence Gate.
Its command, outcome, and supporting facts are derived directly from the same
normalized episode. Ordinary trace import is post-hoc and provider-free; remote
embedding begins only when a configured index lifecycle processes the outbox.

## Other durable-write lifecycles

Five additional domain types are implemented behind service seams, not behind
ordinary trace import:

```text
MemoryProposal + supplied deterministic facts
        |
        v
evaluate_proposal
        |
        v
Evidence Gate -> gate audit -> Markdown + SQLite + Index Queue
```

`evaluate_proposal` supports User Preference, Repository Convention, Verified
Fix, and Debug Episode. It is tested as a service contract but has no public
CLI/API producer.

```text
AttributedEpisode
        |
        v
compile exact source-turn facts
        |
        v
write_episode
        |
        +--> Evidence Gate
        +--> grounded semantic projection
        +--> Markdown + SQLite + Index Queue
```

`write_episode` owns Conversation Episode persistence. The LoCoMo adapter calls
it directly; the Codex/Claude import command does not.

## Index lifecycle

`MiniCascade` owns the transition from queued Markdown revisions to disposable
LanceDB projections:

1. `reconcile` scans Markdown truth and repairs queue/state drift.
2. `run_once` atomically leases one job.
3. Upsert jobs re-read Markdown and project one parent plus its child
   documents.
4. Delete jobs remove the memory projection.
5. Completion records the indexed content fingerprint.
6. `rebuild` replaces the entire index and verifies memory and document parity.

The service component is implemented, but the current public CLI and server do
not own its lifecycle. See [`operations.md`](operations.md).

## Recall lifecycle

```text
task + repo_key
      |
      v
deterministic query sketch and soft route
      |
      v
eight Episode/AtomicFact base + entity/temporal candidate lanes
      |
      v
parent lifting, reciprocal-rank fusion and posting expansion
      |
      v
parent CrossEncoder reranking and top-k selection
      |
      v
bounded chronological neighbor expansion
      |
      v
authoritative-fact EvidenceSelector/CrossEncoder
      |
      v
token-budgeted Recall Context + sidecar
```

Recall reads LanceDB candidates and SQLite memory/fact relationships. It never
silently falls back to scanning Markdown. Every selected excerpt retains its
memory and evidence attribution; the sidecar records candidate sources,
configuration identities, stage counts, omissions, and degraded stages.

## State ownership

| State | Authority | Mutation path | Recovery |
|---|---|---|---|
| Coding Memory content and evidence | Markdown | `MemoryRuntime` through `MarkdownMemoryStore` | Parse and hash; audited repair from committed state when deterministic |
| Import cursor and source observation | SQLite | `commit_import` transaction | Prefix validation and resume checkpoint |
| Gate decisions | SQLite | preflight reservation then commit | Idempotent reservation/audit checks |
| Index work | SQLite | import/gate commit and reconcile | Lease expiry, retry, or rebuild |
| Lexical/vector search rows | LanceDB | Mini Cascade only | Delete and rebuild from Markdown |
| Semantic projection cache | JSON cache | configured semantic adapter | Rebuildable from facts and adapter identity |
| Evaluation artifacts | Immutable filesystem directories | Evaluation use cases | Missing-only resume under exact manifest |

SQLite contains committed mirrors needed for transactions and deterministic
repair, but it is not an independently editable content source.

## Public APIs

The runtime-facing facade is `CodeCairnApplication`:

```text
import_session(source_path, repo_key, source_root?) -> ImportResult
list_memories(repo_key) -> tuple[CodingMemory, ...]
recall(query, repo_key, limit) -> RecallResult
doctor() -> diagnostic mapping
```

`MemoryRuntime.evaluate_proposal` and `MemoryRuntime.write_episode` are
service-level seams, not methods on the public application facade.

Evaluation and evidence operations share the same facade but belong to the
evaluation scope. CLI and HTTP surface details live in
[`operations.md`](operations.md).

## Error boundaries

- Malformed or unsupported traces fail before state advances.
- Truncated or mutated committed prefixes fail closed.
- Unsafe Markdown types, paths, sizes, hashes, or schemas fail closed.
- Evidence proposals with missing, foreign, or incompatible facts are rejected
  and audited.
- Provider model identity or vector dimension drift triggers an explicit
  rebuild/migration path; no test adapter is a production fallback.
- Index queue failures remain observable and retryable according to their
  stored state.
- Recall with no candidates returns an attributed partial result rather than
  inventing context.

## Extension points

New provider importers implement the Agent Trace importer contract and remain
outside the service layer. Embedding, reranking, semantic projection, clock,
truth, state, and index behavior are injected through explicit ports.

An extension may add a public cascade lifecycle, but it must preserve the
single-writer index rule, lease semantics, Markdown authority, provider
identity, and diagnostic visibility.

Direct editing of an existing gate-managed Markdown file is not a stable
extension point. Reconcile currently accepts structurally valid offline
changes without re-running the original Evidence Gate; the product must resolve
that authority conflict before advertising editable memory.

## Required tests

Runtime contract tests cover:

- Codex and Claude Code trace normalization and malformed-input failures;
- stable identities, append resume, truncation, mutation, and idempotence;
- adversarial Evidence Gate proposals and Verified Fix chronology;
- atomic Markdown creation and audited corruption repair;
- queue leases, retry, reconcile, rebuild, and parent-child parity;
- repository isolation and unsafe path rejection;
- hierarchical recall attribution, budgets, provider identity, and degraded
  results;
- CLI and HTTP behavior through the shared application facade.
