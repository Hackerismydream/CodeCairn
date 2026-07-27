# Version 0.1 Memory Lifecycle

## Purpose

This document defines the records and state transitions that turn source traces
into active, historical, and recalled memory. It is the implementation contract
for `memory`, capture/evolution service code, Markdown, SQLite, and index
projection.

## Boundary

The memory domain owns record shape, identity, cardinality, and lifecycle
invariants. It does not parse provider JSONL, write files or SQL, call an MCP
client, or choose a concrete model provider.

## Coding Memory contract

Every Coding Memory has these common fields:

| Field | Type | Contract |
|---|---|---|
| `schema_version` | positive integer | Versioned durable schema |
| `memory_id` | non-empty string | Stable within `repo_key`; content-addressed where possible |
| `repo_key` | non-empty string | Coding Profile implementation of Memory Namespace |
| `memory_type` | closed enum | `task_experience`, `repository_knowledge`, `user_preference`, `work_state` |
| `title` | bounded string | Human-readable, model-authored or deterministic |
| `content` | bounded string | Durable interpretation; may be unverified |
| `category` | optional bounded string | Type-local category, never another top-level type |
| `tags` | tuple of bounded strings | Search facets with deterministic ordering |
| `created_at_ms` | non-negative integer | System clock at durable creation |
| `episode_id` | optional string | Required for capture-derived memory |
| `evidence` | tuple of Evidence Reference | System-authored lineage; may be empty only for explicit direct memory |
| `facts` | tuple of Evidence Fact | Bounded system-derived snapshots required for capture-derived memory |
| `origin` | closed enum | `capture`, `agent_asserted`, `restored` |

`status` is not authored inside the immutable memory. It is derived by replaying
Evolution Records: a memory with no applied outgoing Supersession is `active`;
an applied predecessor is `superseded`.

### Type-specific payloads

| Type | Required payload | Rules |
|---|---|---|
| Task Experience | `goal`, `outcome`, relevant actions, result | Exactly one per Task Episode |
| Repository Knowledge | `subject_key` and one reusable claim | Category may be architecture, convention, command, constraint, or solution |
| User Preference | `subject_key` and preference | Source-derived items reference user-authored content |
| Work State | `workstream_key`, `workstream_state`, goal, progress, blockers, next step or terminal outcome | `workstream_state` is `open` or `closed`; at most one active revision per Workstream |

Failed commands and verified results are structured Task Experience facets.
They remain available to retrieval and evaluation without being public memory
types.

### Stable identity

`created_at_ms`, Markdown path, provider attempt ID, and ranking score never
enter a memory identity.

| Origin/type | Stable identity inputs |
|---|---|
| Task Experience | schema, `repo_key`, `episode_id`, `task_experience` |
| Capture-derived Knowledge | schema, `repo_key`, `episode_id`, type, normalized `subject_key` or `workstream_key`, sorted source fact IDs, canonical payload digest |
| Direct Knowledge/Work State | schema, `repo_key`, type, normalized key, canonical payload digest |
| Restore | schema, restored memory ID, active predecessor ID |

Canonical payloads use UTF-8, Unicode NFC, normalized newlines, sorted map keys,
and deterministic list ordering. An exact direct `remember` retry therefore
returns the existing memory. A completed semantic job never calls the provider
again; it reuses its stored output fingerprint.

Restoring an item is idempotent while its restored revision is already the
active successor. A later restore after another revision becomes active creates
a new revision.

`subject_key` is a bounded machine key separate from display title. It uses
Unicode NFC, collapsed whitespace, and lowercase for text subjects; repository
paths retain case. `workstream_key` is selected deterministically:

1. an exact issue reference in the user task (`issue:<repo-key>#<number>`);
2. normalized source Git branch (`branch:<name>`);
3. the Episode (`task:<episode-id>`);
4. the source session only when no Episode exists (`session:<session-id>`).

A semantic proposal may select only one of the system-derived candidates.

## Capture cardinality

A Task Episode becomes capturable only after a boundary: the next normalized
user task, a client Stop/SessionEnd event, or explicit manual-import EOF. The
committed identity includes its stable source span and boundary kind. If the
same source later appends records beyond that boundary, CodeCairn creates a new
Episode with `continues_episode_id`; it never rewrites the committed Episode or
its Task Experience. Repeating a boundary with no new normalized event is a
no-op.

For each committed Task Episode:

```text
1 Task Experience
0..N Repository Knowledge
0..N User Preference
0..1 Work State when unresolved work remains or this Episode closes an
     existing open Workstream
```

Task Experience creation is deterministic: it uses the user task, normalized
actions, observed outcome, and a bounded template. A semantic extractor
proposes Knowledge items and evolution relations; it does not rewrite or
“improve” the immutable Task Experience. Provider absence or failure therefore
does not remove or weaken the deterministic experience.

Capture is idempotent. Reprocessing the same closed Episode under the same
capture schema either produces identical IDs and content or fails with an
explicit contract mismatch; it never creates silent duplicates. An unclosed
source suffix may advance the Import Ledger but produces no Task Experience
until a boundary arrives.

## Supersession proposal

The evolution input is:

| Field | Type | Contract |
|---|---|---|
| `decision` | closed enum | `keep_both` or `supersede` |
| `predecessor_id` | optional string | Required for `supersede` |
| `successor_id` | string | Newly durable memory |
| `reason` | bounded string | Human-readable model or user explanation |
| `proposer` | closed enum | `capture_model`, `agent`, `user`, `system` |

Model output is an untrusted proposal. The system applies `supersede` only
when:

1. both IDs exist;
2. both IDs belong to the same `repo_key`;
3. neither ID references itself;
4. the predecessor is active;
5. both types are eligible under the policy below;
6. adding the edge cannot create a cycle.

Failure raises a typed validation error for explicit commands and records a
failed processing job for automatic capture. It never degrades into an applied
partial relation.

## Type policy

| Successor | Predecessor | Policy |
|---|---|---|
| Task Experience | any | Never allowed; experience is append-only |
| Work State | Work State | Apply automatically when `workstream_key` matches, including an `open` to `closed` transition |
| User Preference | User Preference | Apply only for a newer explicit user statement with the same `subject_key` |
| Repository Knowledge | Repository Knowledge | Apply only when `subject_key` matches and the proposal marks older content obsolete or contradictory |
| Different types | any | Keep both |

Repository Knowledge consolidation and multi-memory skill synthesis are outside
version 0.1.

## Evolution Record

An applied relation creates one immutable record:

| Field | Type |
|---|---|
| `schema_version` | positive integer |
| `evolution_id` | stable string |
| `repo_key` | string |
| `predecessor_id` | string |
| `successor_id` | string |
| `reason` | bounded string |
| `proposer` | closed enum |
| `evidence` | tuple of source references |
| `created_at_ms` | non-negative integer |

The stable identity includes namespace, predecessor, successor, and evolution
schema. Applying the same relation is a no-op; applying incompatible content to
the same identity fails.

## Restore

`restore(memory_id)`:

1. requires a superseded Repository Knowledge, User Preference, or Work State;
2. creates a new memory ID with `origin=restored`;
3. copies content and lineage and adds `restored_from=memory_id`;
4. records the explicit restore as `proposer=user`, validates the same
   `subject_key` or `workstream_key`, and supersedes the current active item;
5. enqueues all changed active projections.

The restored memory is a new revision. No prior Memory or Evolution Record is
edited or deleted. Task Experience is append-only and never restorable; an
active memory returns a typed `already_active` error. Explicit restore replaces
the semantic “newer/obsolete” test for that one edge, but never bypasses
namespace, type, key, active-predecessor, self-edge, or cycle validation.

## Durable layout

Version 0.1 uses two authoritative Markdown collections:

```text
<root>/memory/<repo-slug>/<memory-type>/<memory-id>.md
<root>/evolution/<repo-slug>/<evolution-id>.md
```

The exact safe slugging function is shared with the existing Markdown store.
Both formats have closed, versioned frontmatter and bounded bodies.
Capture-derived Memory Markdown embeds its selected Evidence Fact snapshots as
well as raw-source references, so later audit does not depend on SQLite or a
model response. The original owned transcript remains external source material;
CodeCairn does not silently copy the full transcript.

SQLite owns:

- memory metadata mirrors;
- Evolution Record mirrors;
- the derived active/superseded projection;
- source import checkpoints;
- semantic-processing jobs;
- index outbox jobs and leases.

LanceDB stores all memory documents with projected status. Normal recall filters
`active`; historical recall explicitly permits `superseded`. Rebuild reads
Markdown Memories and Evolution Records, derives status, then proves memory and
document parity.

## Pre-release migration boundary

Version 0.1 does not carry the six-type runtime schema indefinitely:

| Historical type | Version 0.1 interpretation |
|---|---|
| `debug_episode` | Task Experience with debugging tag |
| `conversation_episode` | Task Experience/source adapter for evaluation |
| `failed_command` | Task Experience failed-command facet |
| `verified_fix` | Task Experience verified-result facet and optional Repository Knowledge |
| `repository_convention` | Repository Knowledge with convention category |
| `user_preference` | User Preference |

Pre-v0.1 runtime roots are detected and rejected with a re-import instruction.
The release does not ship a permanent compatibility layer. Frozen evidence
bundles stay verifiable through their artifact schemas and do not require a
live runtime migration.

## Required tests

- Four-type schema accepts valid records and rejects unknown variants.
- One Episode creates exactly one Task Experience across retries and appends.
- A repeated Stop creates no Episode; appended continuation events create a
  linked Episode without rewriting the first.
- Missing semantic provider preserves Task Experience and a pending job.
- Type-specific cardinality and source-role rules hold.
- Supersession rejects foreign namespace, wrong type, inactive predecessor,
  self-reference, and cycles.
- Restore creates a new ID and a forward-only history chain.
- Rebuild derives the same active projection and parent/child document set.
- Default recall excludes superseded memory; historical recall includes it.
- A pre-v0.1 root fails with one actionable typed error.
