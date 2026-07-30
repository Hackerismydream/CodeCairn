# Architecture

Status: maintained implementation architecture. Version 0.1 is the local
coding-first Memory OS foundation. Version 0.2 adds the installed Pico
Integration Module and recall relevance admission. Version 0.3 adds a
foreground read-only Hub presentation.

## Product boundary

CodeCairn is an auditable local long-term memory runtime for coding agents. It
owns durable memory independently from Codex, Claude Code, Raven, or another
agent runtime. Internally it has Memory OS authority; version 0.1 ships one
implicit Coding Profile for repository-scoped work.

CodeCairn does not execute an agent, inject hidden prompts, ingest arbitrary
documents, run a cloud service, or provide a memory-editing UI. Its Hub is a
local inspection surface, not a remote API or daemon. Pico remains a
general agent harness and consumes CodeCairn through the installed integration
specified in [`v0.2/README.md`](v0.2/README.md).

## System context

```text
Codex ----------- MCP / CLI -----------+
  | Stop hook                          |
  +------------------------------------+
                                       v
Claude Code ------ MCP / CLI ---> service use cases
  | SessionEnd hook                    ^
  +------------------------------------|
                                       |
Pico ------ installed MemoryBackend ---+
                                       |
       +-------------------------------+----------------------------+
       |                               |                            |
       v                               v                            v
Source + Experience              Knowledge + Evolution            Recall
       |                               |                            |
       +-------------------------------+----------------------------+
                                       |
                     Markdown <----> SQLite ----> LanceDB
                     durable truth     operations   rebuildable index
                                       |
                                       v
                              evaluation adapters
                                       |
                                       v
                               immutable artifacts

Browser -> same-origin Hub route -> foreground Hub Read Interface
                                      |
                                      +-> service use cases
```

Provider traces are untrusted input. Model output is an untrusted
interpretation. The system derives namespace, source locations, roles, exact
quotes, command outcomes, file changes, and verification status from normalized
events. A model may propose memory text and supersession but cannot author those
fields.

## Five-layer memory model

| Layer | Owns | Does not own |
|---|---|---|
| Source | Agent Trace, source locations, Evidence Facts | Model-authored meaning |
| Experience | One Task Experience per Task Episode | Cross-task consolidation |
| Knowledge | Repository Knowledge, User Preference, Work State | Source provenance |
| Evolution | Immutable Supersession decisions and derived status | In-place edits |
| Recall | Relevance admission, active-memory selection, and bounded context | Durable truth |

The layer names describe authority and lifecycle, not Python packages. The
canonical terms and invariants live in [`../CONTEXT.md`](../CONTEXT.md).

## Dependency direction

```text
entrypoints -> service -> memory
                 ^          ^
                 |          |
             importers   storage adapters
```

`bootstrap` composes concrete adapters at the process boundary. `evaluation`
calls the same service or public contracts with isolated roots.

The import-linter contracts remain:

1. entrypoints depend on service, and service depends on memory;
2. importers and storage adapters do not depend on entrypoints;
3. service depends on ports, not concrete importers or storage;
4. entrypoints do not reach through service to concrete adapters.

### Responsibility map

| Package | Current responsibility | Excluded responsibility |
|---|---|---|
| `memory` | Four memory records, Source records, lifecycle invariants, provider ports, recall value objects | Filesystem, SQL, CLI |
| `service` | Capture, evolution, import, process, index, recall, inspection, diagnostics | Provider JSONL branches or presentation |
| `importers` | Codex, Claude, and Pico JSONL normalization | Capture policy or persistence |
| `integrations/pico` | Installed Pico adapter and append-only source journal | Memory policy or Pico agent execution |
| `storage` | Markdown, SQLite, and LanceDB adapters | Product workflow decisions |
| `entrypoints` | CLI, MCP, and hook presentation | Alternative domain behavior |
| `bootstrap` | Config loading and concrete composition | Durable rules |
| `evaluation` | Thin suite adapters, immutable runs, reducers, verifier | Product-only duplicate runtime |
| `apps/hub-api` | Three local view reads, token check, error envelope, Doctor projection | Storage access, remote API, mutation |
| `apps/hub-web` | Chinese Memories, Recall, and System views | Durable policy, fixture fallback, provider configuration |

The complete repository workspace map is [`workspace.md`](workspace.md). ADR
0061 defines why the foreground Hub does not restore the generic HTTP
compatibility surface removed by ADR 0052.

## Pico Integration Module

```text
Pico Runtime
  -> pico.plugins entry "codecairn"
     -> manifest "codecairn-memory"
        -> Memory Backend contribution "codecairn"
           -> CodeCairn Pico Memory Adapter
              -> CodeCairnApplication
                 +-> Pico Source Journal
                 |   -> Pico importer
                 |      -> provider "pico" Agent Trace
                 +-> repository Recall Context
```

The Integration Module implements the backend structurally and is loaded only
through Pico discovery. It lazily imports Pico's public `Memory` result carrier
to satisfy Pico's concrete contract. Importing CodeCairn core or the cheap
entry-point resource package does not import Pico. Pico may declare and pin the
CodeCairn distribution so a fresh default installation is resolvable, but it
calls the Integration Module instead of storage or memory internals.

| Owner | Responsibility |
|---|---|
| CodeCairn | Installed entry point, plugin manifest, Pico Memory Adapter, source journal, importer, repository identity, replay, and Recall mapping |
| Pico | Backend selection, CodeCairn dependency pin, Local Skills, onboarding, runtime failure visibility, continuity, and PicoBench |

Pico Session storage remains Pico conversation truth. CodeCairn stores one
append-only persisted after-Turn batch in its own source journal and normalizes
it under the existing Agent Trace and evidence invariants. Boundary
`pico_turn_end` closes the batch without asserting task success. User-track
recall returns one compiled Recall Context as a concrete Pico `Memory`.

An unrelated query is allowed to return no Pico memory. CodeCairn performs
relevance admission before context compilation, records the decision in the
sidecar, and preserves only an explicitly requested open Work State as a
pinned exception.
Agent-track recall is empty, feedback is a compatibility no-op, and media
understanding is not part of the adapter.

The complete Interface, source, failure, and joint evidence contract is
[`v0.2/README.md`](v0.2/README.md). ADR 0057 records why the integration is a
direct Memory Backend rather than MCP or a Pico-owned duplicate.

## Durable records

The complete version 0.1 durable model is:

```text
Agent Trace
  -> Task Episode
       -> exactly 1 Task Experience
       -> 0..N Repository Knowledge
       -> 0..N User Preference
       -> 0..1 Work State when work remains or a prior Workstream closes

new Coding Memory
  + active same-subject memory
  -> keep both OR immutable Evolution Record
  -> derived active/superseded status
```

There are four public Coding Memory types:

- Task Experience;
- Repository Knowledge;
- User Preference;
- Work State.

Debugging, failed commands, and verified results are facets of Task Experience.
Repository Convention is a Repository Knowledge category. Conversation Episode
is retained only as a source/experience adapter where an evaluation needs it.

The complete field and transition contract is
[`v0.1/memory-lifecycle.md`](v0.1/memory-lifecycle.md). Exact field, bound,
canonicalization, identity, and storage mappings are in
[`v0.1/schema-contract.md`](v0.1/schema-contract.md).

## Capture flow

```text
owned provider transcript
  -> provider detection
  -> strict normalized events
  -> stable Task Episodes closed by next-task, Stop, SessionEnd,
     or explicit manual finalize
  -> deterministic Task Experience
  -> optional semantic proposals
  -> system validates source roles and cardinality
  -> automatic supersession proposal validation
  -> write intent + deterministic Markdown batch + SQLite completion
  -> semantic/index jobs
```

Source import and deterministic Task Experience do not require a semantic
model. If no semantic model is configured, the transaction still commits and
records optional Knowledge/evolution work as pending. A later
`codecairn process` completes that work without rewriting Task Experience.

An unclosed source suffix may advance source observation state but does not
produce memory. Once a boundary commits an Episode, later appended source
records form a linked continuation Episode rather than editing its immutable
Task Experience.

Manual `remember` enters at the Coding Memory boundary. Its origin is
`agent_asserted`; Repository Knowledge and Work State may have no Evidence
References and cannot claim source-derived facts they did not observe. A
direct User Preference requires Source Fact Registry IDs that resolve to
user-authored facts.

## Evolution flow

```text
successor memory + proposed predecessor
  -> same namespace?
  -> eligible types and same subject/workstream?
  -> predecessor active?
  -> no self-edge or cycle?
  -> append Evolution Record
  -> derive statuses
  -> enqueue predecessor and successor projections
```

The model selects only `keep_both` or `supersede` and supplies a closed
`relation_kind`. CodeCairn owns validation. Source recency comes from a
`source_order_key`, never import time; incomparable cross-session sources keep
both. Semantic-job completion and proposal outcome are separate. Task
Experience never supersedes. Restore copies historical content into a new
memory revision and supersedes the unique active tip in that memory's lineage;
an ambiguous lineage is an error.

## Recall flow

```text
task query
  -> resolve Memory Namespace
  -> bounded deterministic index preflight
  -> active-only parent and exact-child candidate search
  -> pin matching open Work State
  -> fuse parent ranks and locally rerank searched exact children
  -> apply per-type caps
  -> supplement exact lines inside admitted parents
  -> globally pack exact excerpts under one total token budget
  -> render attributed Markdown from admitted excerpts
  -> emit structured JSON sidecar
```

Normal recall never silently includes superseded memory. Every recall first
drains a bounded number of deterministic index jobs for the selected namespace.
If the required cursor cannot become ready inside the bound, recall returns
`index_not_ready`; it never returns a stale success. Semantic extraction may
remain pending while its deterministic Task Experience is recallable, and the
sidecar reports source/index cursors, semantic state, and freshness.
`include_superseded` and `memory history` are explicit historical operations.
Recall Context is a derived view and is not written back as memory.

Each projected memory has one searchable parent. Capture-derived memory also
has one child per exact Evidence Fact. A memory without Evidence Facts projects
up to 128 non-empty content lines as exact search children; these are
rebuildable excerpts, not new durable facts. Searched children are reranked;
after parent admission, Recall supplements missing exact lines from admitted
memories while memories with Evidence Facts remain fact-only. Child ranking is
bounded to 12 excerpts per memory. The Repository Knowledge cap is 40 parents,
and the renderer considers at most 192 excerpts before applying the encoded
context budget. The sidecar reports the IDs actually rendered rather than
treating every ranked parent as present in context.

## Storage authority

```text
                       authoritative     operational       disposable
                          truth             state             search
Coding Memories ------> Markdown <------> SQLite ---------> LanceDB
Evolution Records ---->    ^                |
                            |                +--> source/semantic/index queues
                            +--------------------- rebuild reads
```

### Markdown

- one immutable file per Coding Memory;
- one immutable file per Evolution Record;
- bounded system-derived Evidence Fact snapshots inside capture-derived
  memories;
- closed, versioned frontmatter and bounded bodies;
- safe-path, type, size, schema, and digest validation;
- atomic create and exact-content idempotency.

Markdown authority means it is the durable representation. It does not mean
model-authored content is factually verified.

### SQLite

SQLite owns transactions and operational projections:

- source fingerprints, committed cursors, and episode identities;
- memory and Evolution Record mirrors;
- derived active status;
- pending, leased, failed, and retryable processing jobs;
- index outbox and readiness diagnostics.
- prepared/completed/conflicted multi-file Write Intents;
- Source Fact Registry rows and bounded Hook Receipts.

SQLite content mirrors are rebuildable from Markdown except for operational
source cursors and queues. They are not an independent editing surface.

### LanceDB

LanceDB contains searchable parents plus exact Evidence Fact or source-line
children. Index rows include memory status and retrieval-profile identity.
The projection revision is part of that identity, so a child-schema change
requires rebuild. Rebuild replays Markdown and Evolution Records and verifies
the complete projected document set.

Recall does not scan Markdown as a hidden fallback. A stale or missing index is
an explicit degraded state with a remediation command.

## Consistency boundaries

SQLite and the filesystem cannot share one ACID transaction. Version 0.1 uses
the following write-intent recovery protocol instead of claiming atomicity:

1. transaction A inserts a `prepared` Write Intent containing the operation
   identity, deterministic payloads, expected safe paths and digests, record
   IDs, and prior/target source cursor; it does not advance the cursor;
2. each file is written to a same-directory temporary file, file-fsynced,
   atomically created, and followed by a directory fsync;
3. one intent covers the complete Memory/Evolution batch, so recovery knows the
   expected set even though filesystem renames are individually atomic;
4. transaction B verifies the complete file set, writes memory/evolution
   mirrors and derived status, creates semantic/index jobs, advances the source
   cursor, and marks the intent `completed`;
5. startup and every mutating application composition recover unresolved
   intents before accepting new work: matching files complete transaction B,
   missing files are deterministically recreated, and conflicting bytes mark
   the intent `conflicted` with a typed recovery error.

The fault-injection suite covers intent commit, temporary write, file fsync,
atomic create, directory fsync, transaction-B start, transaction-B rollback,
and post-commit acknowledgement. A committed cursor therefore always has its
complete durable write set. Search and semantic enrichment remain queued, but
recall owns the bounded freshness preflight described above.

| Failure | Durable result | User-visible result |
|---|---|---|
| Semantic provider absent | Source and Task Experience committed | Pending semantic job |
| Semantic provider fails | Source and Task Experience committed | Failed retryable job |
| Index drain fails | Markdown and SQLite committed | Degraded index with remediation |
| Hook input invalid | No partial memory | Hook failure visible in `doctor` |
| Supersession invalid | Successor may remain active; no edge applied | Rejected proposal outcome or typed command error |
| LanceDB lost | No truth lost | Rebuild required |

## Product surfaces

CLI, MCP, and hooks are the version 0.1 product interfaces. Each calls the same
service use cases:

| Use case | CLI | MCP | Hook |
|---|---:|---:|---:|
| Initialize/configure | yes | no | installer only |
| Import session | yes | yes | yes |
| Capture direct memory | yes | yes | no |
| Process pending work | yes | no | queues only |
| Recall | yes | yes | no |
| List/show/history | yes | yes | no |
| Supersede/restore | yes | history/remember workflow | no |
| Doctor | yes | yes | failure writer only |
| Export/reset namespace | yes | no | no |
| Evaluate/verify | yes/Make | no | no |

Version 0.1 retires the unpublished HTTP compatibility adapter under ADR 0052.
Exact CLI, MCP, and hook contracts are in
[`v0.1/agent-integration.md`](v0.1/agent-integration.md).

## Configuration and provider boundary

`codecairn init` writes non-secret configuration to `codecairn.toml`. Runtime
precedence is:

```text
explicit CLI option > environment secret/override > codecairn.toml > default
```

Retrieval and semantic extraction are independent capabilities. Retrieval uses
an explicitly selected profile:

- recommended DashScope `qwen3.7-text-embedding`, 1,024 dimensions; or
- explicit pinned local FastEmbed.

There is no silent network-to-local fallback. Hashing is test-only. A retrieval
profile change invalidates the disposable index; a missing semantic model only
defers semantic capture.

## Evaluation boundary

Evaluation is a consumer of product contracts, not a second product runtime.
Version 0.1 retains four user-facing evaluation families:

1. offline lifecycle, scale, recovery, and retrieval;
2. LoCoMo diagnostic and full runs;
3. coding memory-off/on A/B;
4. immutable evidence verification.

Historical evidence remains independently verifiable. A historical result is
never relabeled as a result from the version 0.1 architecture. The exact command
and release contract is
[`v0.1/evaluation-and-release.md`](v0.1/evaluation-and-release.md).

## Readability budget

At implementation baseline `954f728`, `src/codecairn` has 34,091 physical
Python lines: 17,250
outside `evaluation` and 16,841 inside it. Version 0.1 accepts at most:

- 10,000 physical Python lines in product core excluding `evaluation`;
- 15,000 physical Python lines in all of `src/codecairn`.

The budget is checked by a deterministic repository script. Generated code,
vendored code, or moving implementation into another installable package does
not evade it.

## Current-to-target delta

| Implementation baseline `954f728` | Version 0.1 target | Task |
|---|---|---|
| No early source gate; historical verifier owns worker code | CI source budget plus a read-only historical verifier adapter | `v01-000a` |
| Six types plus Evidence Gate paths | Four types; no standalone verification operation | `v01-001` |
| Import emits Failed Command only | Complete cardinality and pending semantic jobs | `v01-002` |
| No durable evolution | Evolution Records, status, restore | `v01-003` |
| Recall has no lifecycle filter | Active-only recall and history | `v01-004` |
| Manual environment/repo key setup | `init`, config, derived namespace | `v01-005` |
| CLI/HTTP only | MCP tool/resource server | `v01-006` |
| No automatic client ingestion | Claude/Codex session-end hooks | `v01-007` |
| Large historical evaluation framework | Four thin commands and source gates | `v01-008` |
| Checkout-only, no license | MIT, curated package, learner docs | `v01-009` |
| No release-candidate proof | Installed E2E and new evidence bundle | `v01-010` |

The task files under [`plan/tasks/`](plan/tasks/) are the agent-executable
delivery contract.

## Version 0.2 implementation status

| Delivery | Current status | Evidence boundary |
|---|---|---|
| Provider `pico` and append-only Pico Source Journal | Implemented | `v02-001` |
| Entry `codecairn`, manifest `codecairn-memory`, and installed adapter | Implemented | `v02-002` |
| Pico default switch and EverOS product-coupling removal | Implemented in Pico | Exact Pico commit belongs in each handoff or campaign manifest |
| Store, fresh-process recall, isolation, and paired tasks | Executed | The first campaign is measurement-valid but positive-claim-ineligible |
| Unrelated-query abstention | Implemented | ADR 0060 and the 120-case local retrieval gate |
