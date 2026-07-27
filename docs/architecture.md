# Architecture

Status: accepted version 0.1 target. The implementation delta is tracked under
[`plan/`](plan/); this document must not be read as a claim that every target
surface already exists on `main`.

## Product boundary

CodeCairn is a local-first Memory OS for agents. It owns durable memory
independently from Codex, Claude Code, Raven, or another agent runtime. Version
0.1 ships one implicit Coding Profile for repository-scoped work.

CodeCairn does not execute an agent, inject hidden prompts, ingest arbitrary
documents, run a cloud service, or provide a memory-editing UI. Raven
integration is deliberately deferred until after version 0.1.

## System context

```text
                         explicit recall / remember
Codex -------------------------------+
  |                                  |
  | Stop hook                        v
  +---------------------------> MCP / CLI
                                      |
Claude Code --------------------------+
  |                                  |
  | SessionEnd hook                  v
  +--------------------------> service use cases
                                      |
       +------------------------------+-----------------------------+
       |                              |                             |
       v                              v                             v
 Source + Experience            Knowledge + Evolution              Recall
       |                              |                             |
       +------------------------------+-----------------------------+
                                      |
                    Markdown <----> SQLite ----> LanceDB
                    durable truth     operations   rebuildable index
                                      |
                                      v
                              evaluation adapters
                                      |
                                      v
                              immutable artifacts
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
| Recall | Active-memory selection and bounded context | Durable truth |

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

### Target responsibility map

| Package | Version 0.1 responsibility | Excluded responsibility |
|---|---|---|
| `memory` | Four memory records, Source records, lifecycle invariants, provider ports, recall value objects | Filesystem, SQL, HTTP, CLI |
| `service` | Capture, evolution, import, process, index, recall, inspection, diagnostics | Provider JSONL branches or presentation |
| `importers` | Codex/Claude source discovery and JSONL normalization | Capture policy or persistence |
| `storage` | Markdown, SQLite, and LanceDB adapters | Product workflow decisions |
| `entrypoints` | CLI, MCP, hook, and compatibility HTTP presentation | Alternative domain behavior |
| `bootstrap` | Config loading and concrete composition | Durable rules |
| `evaluation` | Thin suite adapters, immutable runs, reducers, verifier | Product-only duplicate runtime |

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
[`v0.1/memory-lifecycle.md`](v0.1/memory-lifecycle.md).

## Capture flow

```text
owned provider transcript
  -> provider detection
  -> strict normalized events
  -> stable Task Episodes closed by next-task, Stop, SessionEnd, or manual EOF
  -> deterministic Task Experience
  -> optional semantic proposals
  -> system validates source roles and cardinality
  -> automatic supersession proposal validation
  -> atomic Markdown + SQLite transaction
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
direct User Preference still requires references to user-authored source.

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

The model selects only `keep_both` or `supersede`. CodeCairn owns validation.
Task Experience never supersedes. Restore copies historical content into a new
memory revision and applies the normal forward-only evolution rules.

## Recall flow

```text
task query
  -> resolve Memory Namespace
  -> active-only candidate search
  -> pin matching open Work State
  -> rank Knowledge, Preference, Experience
  -> apply total token budget and per-type caps
  -> render attributed Markdown
  -> emit structured JSON sidecar
```

Normal recall never silently includes superseded memory. `include_superseded`
and `memory history` are explicit historical operations. Recall Context is a
derived view and is not written back as memory.

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

SQLite content mirrors are rebuildable from Markdown except for operational
source cursors and queues. They are not an independent editing surface.

### LanceDB

LanceDB contains searchable parent and atomic child documents only. Index rows
include memory status and retrieval-profile identity. Rebuild replays Markdown
and Evolution Records and verifies the complete projected document set.

Recall does not scan Markdown as a hidden fallback. A stale or missing index is
an explicit degraded state with a remediation command.

## Consistency boundaries

The source-import transaction commits normalized identity, Markdown memory,
SQLite mirrors, and queue records before advancing the source cursor. Search and
semantic enrichment are eventually consistent.

| Failure | Durable result | User-visible result |
|---|---|---|
| Semantic provider absent | Source and Task Experience committed | Pending semantic job |
| Semantic provider fails | Source and Task Experience committed | Failed retryable job |
| Index drain fails | Markdown and SQLite committed | Degraded index with remediation |
| Hook input invalid | No partial memory | Hook failure visible in `doctor` |
| Supersession invalid | Successor may remain active; no edge applied | Failed processing job or typed command error |
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
| Evaluate/verify | yes/Make | no | no |

The existing HTTP adapter remains compatible for existing routes. New v0.1
lifecycle operations do not require HTTP parity. Exact integration contracts
are in [`v0.1/agent-integration.md`](v0.1/agent-integration.md).

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
Version 0.1 retains four user-facing evaluation paths:

1. offline lifecycle smoke;
2. LoCoMo diagnostic and full runs;
3. coding memory-off/on A/B;
4. immutable evidence verification.

Historical evidence remains independently verifiable. A historical result is
never relabeled as a result from the version 0.1 architecture. The exact command
and release contract is
[`v0.1/evaluation-and-release.md`](v0.1/evaluation-and-release.md).

## Readability budget

At `main@954f728`, `src/codecairn` has 34,091 physical Python lines: 17,250
outside `evaluation` and 16,841 inside it. Version 0.1 accepts at most:

- 10,000 physical Python lines in product core excluding `evaluation`;
- 15,000 physical Python lines in all of `src/codecairn`.

The budget is checked by a deterministic repository script. Generated code,
vendored code, or moving implementation into another installable package does
not evade it.

## Current-to-target delta

| Current `main@954f728` | Version 0.1 target | Task |
|---|---|---|
| Six types plus Evidence Gate paths | Four types; optional verification | `v01-001` |
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
