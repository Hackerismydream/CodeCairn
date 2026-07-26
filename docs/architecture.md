# Architecture

CodeCairn is a local, evidence-native memory runtime for coding agents. Its
architecture separates durable truth, operational state, disposable search
projection, product entrypoints, and evaluation artifacts.

The runtime is intentionally narrower than a general agent platform. It imports
completed Codex and Claude Code sessions; it does not execute an agent, inject
hidden prompts, ingest a general knowledge base, or provide cloud tenancy.

## System context

```text
Codex / Claude Code JSONL
            |
            v
      CodeCairn runtime
       |      |      |
       |      |      +--> Recall Context + audit sidecar
       |      |
       |      +---------> local CLI / loopback HTTP
       |
       +----------------> immutable evaluation artifacts
```

External model providers may supply embeddings, semantic projections, answers,
or judge votes. Their output is untrusted data. Only normalized events and
deterministic derivations can author provenance, role, command outcome, file
change, quote, or verification state.

## Package boundaries

```text
entrypoints ───────> service ───────> memory
                         ^               ^
                         |               |
                    importers         storage

bootstrap ── composes concrete adapters at the process boundary
evaluation ─ calls service contracts and owns benchmark artifacts
```

The enforced dependency contracts are:

1. `entrypoints -> service -> memory`;
2. importers and storage adapters do not depend on entrypoints;
3. service depends on ports, not concrete importer or storage adapters;
4. entrypoints do not reach through service to concrete adapters.

### Responsibility map

| Package | Responsibility | Must not own |
|---|---|---|
| `memory` | Domain records, stable identities, evidence rules, projection, planner and provider contracts | Filesystem, SQLite, HTTP, CLI |
| `service` | Import, gate, repair, cascade, recall, and application orchestration | Provider JSONL branches or concrete persistence |
| `importers` | Provider detection and JSONL-to-Agent-Trace adaptation | Memory acceptance or persistence |
| `storage` | Markdown, SQLite, LanceDB, and projection-cache adapters | Product workflow decisions |
| `entrypoints` | CLI/HTTP validation and presentation | Alternative domain behavior |
| `bootstrap` | Concrete adapter/provider composition | Durable domain rules |
| `evaluation` | Isolated suite execution, immutable artifacts, reports, and public reducers | Product truth or hidden mutations |

Detailed runtime module ownership lives in
[`runtime/README.md`](runtime/README.md).

## Durable-write paths

CodeCairn currently has three distinct write paths. They share domain and
storage contracts but are not one public import pipeline.

### Public trace import

```text
Provider JSONL
      |
      v
SessionImporter selects Codex or Claude adapter
      |
      v
Agent Trace -> Task Episodes -> deterministic Evidence Facts
      |
      v
deterministic Failed Command extraction
      |
      v
atomic Markdown + SQLite import transaction + Index Queue
      |
      v
ImportResult
```

This is the only durable-write path exposed by `CodeCairnApplication`, CLI, and
HTTP import. It does not call Semantic Compression or Evidence Gate.

### Gate-managed proposal

```text
MemoryProposal + supplied deterministic Evidence Facts
      |
      v
evaluate_proposal -> Evidence Gate
      |
      +--> accepted: Markdown + SQLite gate audit + Index Queue
      |
      +--> rejected: SQLite gate audit only
```

This service seam supports User Preference, Repository Convention, Verified
Fix, and Debug Episode. It is implemented and tested but has no public
producer.

### Attributed Conversation Episode

```text
AttributedEpisode -> exact source-turn facts -> Evidence Gate
      |
      v
grounded EpisodeSemanticizer
      |
      v
Markdown + SQLite gate audit + Index Queue
```

`write_episode` owns this path. LoCoMo evaluation uses it directly; ordinary
Codex/Claude import does not. A grounded semantic clause may improve retrieval
text, but it must cite existing source facts and cannot replace their lossless
child projections.

### Asynchronous index boundary

All accepted write paths may enqueue an index revision:

```text
Markdown + SQLite Index Queue
              |
              | asynchronous outbox boundary
              | no public owner on current main
              v
         Mini Cascade
              |
              v
LanceDB Recall Episode + Atomic Fact Documents
              |
              v
Recall Context Markdown + JSON sidecar
```

## Import and evidence

The public import seam is:

```text
import_session(source_path, repo_key, source_root?) -> ImportResult
```

It hides:

- single-pass provider detection;
- strict JSONL parsing and raw record hashing;
- tool call/result pairing;
- stable Task Episode identity;
- deterministic evidence collection;
- deterministic Failed Command extraction;
- atomic Markdown creation;
- SQLite transaction and index-outbox enqueue;
- resume checkpoint update;
- pre-import repair of committed Markdown.

The Import Ledger advances its committed cursor only after the complete durable
write set succeeds. The resume checkpoint hashes the stable prefix and retains
call IDs and file-change counts needed to validate an appended source without
re-decoding committed records.

### Evidence authority

| Field or claim | Authority |
|---|---|
| Provider, session, source, raw index, call ID | Importer from raw record |
| Message role and exact quote | Normalized source event |
| Command and exit status | Paired call/result event |
| File change | Successful structured tool result or recorded patch call |
| Verification success | Deterministic command classification plus later success |
| Summary and semantic grouping | Optional untrusted model proposal |
| Failed Command durable acceptance | Deterministic extractor over failed command facts |
| Other Coding Memory durable acceptance | Evidence Gate resolving proposal fact IDs |

Accepted and rejected gate-managed proposals both produce auditable SQLite
decisions. Accepted memories persist the complete deterministic fact snapshot
in Markdown and SQLite. Conversation Episodes preserve attributed source turns
as authority and store semantic text only as a marked retrieval projection.

## Storage and consistency

```text
                authoritative       operational        disposable
                    truth              state              search
                 Markdown  <------>  SQLite  --------->  LanceDB
                    ^                  |
                    |                  +--> Index Queue
                    +------------------------ Mini Cascade re-read
```

### Markdown

- One canonical file per Coding Memory.
- Same-directory temporary file, flush, fsync, and atomic create-if-absent.
- Runtime write paths are create-only for an existing memory identity and
  reject conflicting content.
- Safe path, file type, size, schema, and content hash are validated on read.
- Repair is allowed only when committed state renders the exact expected hash;
  repair attempts are audited and resumable.

### SQLite

SQLite owns:

- observed import sources and resume checkpoints;
- memory metadata and deterministic fact mirrors;
- proposal reservations and gate audits;
- Markdown recovery audits;
- fact postings used by bounded expansion;
- the transactional index outbox, leases, retry state, and fingerprints.

SQLite mirrors support transactions and recovery. They are not an independently
editable content source.

The current reconcile path also accepts a structurally valid offline Markdown
modification or deletion, updates SQLite, and requeues the index. For a
gate-managed memory it checks that an accepted audit exists but does not
re-evaluate the edited payload against the original proposal. This conflicts
with the runtime's create-only evidence contract. Direct editing is therefore
not a supported product promise until an ADR chooses either immutable existing
IDs or explicitly editable fields with provenance revalidation.

### LanceDB

LanceDB owns lexical/vector search rows only. Each Coding Memory projects to:

- one parent Recall Episode; and
- zero or more semantic and authoritative Atomic Fact Documents.

Parent/child IDs and content digests are deterministic. Rebuild parity compares
both memory fingerprints and the complete document set, so a missing,
reparented, or changed child fails parity even when memory counts match.

### Consistency contract

Import is strongly durable for Markdown plus the SQLite transaction. Search is
eventually consistent because indexing is a separate outbox consumer. Recall
does not scan Markdown as a degraded fallback.

The current public CLI and HTTP server do not start or expose that consumer.
Consequently, current main can commit truth while remaining index-degraded.
This lifecycle gap is documented in
[`runtime/operations.md`](runtime/operations.md) and must not be hidden by the
architecture diagram.

## Mini Cascade

`MiniCascade` is the single search-projection writer at the service level.

| Operation | Contract |
|---|---|
| `reconcile` | Compare Markdown scan with SQLite and enqueue required changes |
| `run_once` | Lease and apply one upsert/delete job |
| `run_until_idle` | Bound repeated claims to a configured maximum |
| `health` | Report pending, leased, indexed, failed, and stale counts |
| `retry_failed` | Requeue failed jobs through SQLite policy |
| `rebuild` | Replace all index rows from Markdown and verify full parity |

Upserts re-read committed Markdown rather than trusting the SQLite mirror to
author child documents. Jobs are content-addressed, atomically leased, and
idempotent for an unchanged successful revision.

Tests and evaluation compose Mini Cascade explicitly. Product entrypoints
currently do not; the next product slice must give the lifecycle one supported
owner without creating a second index writer.

## Retrieval

Recall receives a task, repository namespace, and result limit.

```text
query validation
      |
      v
deterministic query sketch + soft route
      |
      +--> Episode base vector/lexical candidates
      +--> AtomicFact base vector/lexical candidates
      +--> Episode entity/temporal lexical candidates
      +--> AtomicFact entity/temporal lexical candidates
      |
      v
lift child hits to parents + reciprocal-rank fusion
      |
      v
entity/provenance posting expansion
      |
      v
parent snippets + parent CrossEncoder reranking
      |
      v
core/coverage top-k selection
      |
      v
bounded chronological neighbor expansion
      |
      v
selected-parent authoritative-fact EvidenceSelector/CrossEncoder
      |
      v
budgeted exact-source context compiler
```

The route changes bounded candidate budgets; it never hard-disables the
secondary hierarchy level. Query-time recall makes no LLM call. Candidate
fan-out, reranking work, expansion hops, fact counts, context tokens, and
neighbor snippets are bounded and represented in versioned planner
configuration.

The result contains:

- Markdown intended for a later coding task;
- ranked memories and candidate sources;
- provider and planner identities;
- stage input/output counts;
- selected and omitted evidence IDs;
- context token/character counts;
- degraded stages and coverage requirements;
- latency observations.

Current constraints are frozen by code, manifests, and accepted ADRs 0012–0036.
[`recall-v2-design.md`](recall-v2-design.md) is retained as historical design
and diagnosis, not as the current contract.

## Retrieval provider identity

Production composition defaults to DashScope `text-embedding-v4` at 1,024
dimensions and a pinned local CrossEncoder reranker. The explicit `fastembed`
profile provides offline local embeddings; `hashing-test` is deterministic test
infrastructure only.

Index identity binds endpoint, model alias, declared revision, dimension, and
adapter version. The local reranker also binds artifact source and immutable
revision. Changing an identity component makes existing rows incompatible and
requires the rebuildable projection migration under an inter-process lock.

Provider aliases such as `provider-managed` are declarations, not immutable
model commits. Manifests and sidecars record that limitation without recording
credentials.

## Public entrypoints

`CodeCairnApplication` is the shared facade for CLI and HTTP:

```text
CLI ------------------\
                       > CodeCairnApplication -> MemoryRuntime / operations
HTTP -----------------/
```

HTTP adds:

- loopback-only binding;
- source/artifact root authorization;
- request validation;
- `x-request-id`;
- stable error envelopes.

It does not add a cascade worker or alternative import, recall, evaluation, or
health implementation. The current command and route matrix lives in
[`runtime/operations.md`](runtime/operations.md).

`evaluate_proposal` and `write_episode` remain `MemoryRuntime` service seams and
are not methods on this public facade.

## Evaluation architecture

Evaluation is a separate composition plane around the runtime:

```text
frozen inputs -> isolated execution -> immutable artifacts
                                      |
                                      v
                               pure report reducers
                                      |
                                      v
                            public evidence reducer
                                      |
                                      v
                              offline verification
```

Retrieval, recovery, CodingMemoryBench, and LoCoMo answer different questions.
Manifests bind input digests, repository state, models, seeds, concurrency,
resource limits, and workspace/memory isolation. Resume is missing-only.
Reports cannot mutate runtime state.

Evaluation code sometimes composes `bootstrap` and concrete storage adapters
directly to build isolated corpora and indexes. It is therefore not completely
inside the product `entrypoints -> service -> memory` dependency chain, and the
current import-linter contracts do not claim otherwise. `locomo_worker.py` is a
separate process execution boundary.

The evidence reducer treats saved summaries as assertions and recomputes them
from raw aggregate inputs. Exact repair preserves the negative source run and
proves the repaired ID set. Public artifacts redact licensed/private content
while retaining enough outcomes and receipts for offline recomputation.

Detailed suite ownership and current evidence live in
[`evaluation/README.md`](evaluation/README.md).

## Failure and security boundaries

- Provider traces are untrusted input and are parsed as data.
- Patch contents and paths are never executed by importers.
- Raw path strings are evidence, not filesystem authorities.
- Remote model output cannot author evidence fields.
- Unsafe or corrupt Markdown fails closed.
- Custom provider/model identities cannot silently reuse incompatible vectors.
- Test adapters never become silent production fallbacks.
- HTTP import and evaluation paths remain under configured roots.
- Runtime secrets never enter Markdown, index rows, sidecars, or manifests.
- Benchmark provider/config reachability is not a successful scored result.

## Current architectural gaps

The following are not implemented product capabilities:

1. A public producer for five gate-managed Coding Memory types.
2. A public Mini Cascade lifecycle for import-to-recall completion.
3. A resolved policy for offline edits to gate-managed Markdown.
4. A thin Codex/Claude Code integration that invokes recall and later imports
   sessions during ordinary use.
5. Versioned user-facing configuration files and initialization.
6. Metrics for queue lag, recall latency, and provider failures.
7. Memory supersession, conflict, or repository-revision validity.
8. Tagged release, explicit license, and release-governance files.

Broad knowledge management, multimodal ingestion, cloud multi-tenancy, and a
general OME-style evolution engine remain out of scope unless the product
direction changes.

## Reference policy

Pythia remains a private prototype and regression corpus. EverOS is consulted
for mechanism-level invariants such as atomic Markdown, rebuildable indexes,
queue observability, and evaluation orchestration. CodeCairn independently owns
its Agent Trace, evidence model, gates, coding-memory taxonomy, Recall Context,
and evaluation contracts.

See [`reference-boundaries.md`](reference-boundaries.md) for the clean-room and
attribution rules.
