# CodeCairn

CodeCairn is an auditable local memory runtime that helps coding agents reuse
repository knowledge without trusting opaque summaries.

## Domain language

**Agent Trace**: A provider-independent sequence of normalized coding-session
events. It contains user and assistant messages, tool calls and results, file
changes, command outcomes, and final answers.

**Evidence Reference**: An immutable pointer to the provider, session, source
record, call identifier, and raw event index that supports a fact.

**Evidence Fact**: A statement derived deterministically from normalized events,
such as a user-authored quote, command exit status, or changed file. LLM output
is never an Evidence Fact.

**Task Episode**: A stable extraction unit bounded by a user task and its related
actions and outcome. Appending a later episode must not change earlier episode
identities.

**Coding Memory**: An evidence-backed reusable item of one of six types: Debug
Episode, Conversation Episode, Repository Convention, Failed Command, Verified
Fix, or User Preference. A Conversation Episode preserves attributed source
turns plus a grounded, derived retrieval projection; the source turns remain
the evidence authority.

**Evidence Gate**: Type-specific validation for gate-managed proposals and
Conversation Episodes. It validates claims against Evidence Facts, not against
LLM-provided labels. Deterministic Failed Command extraction is a separate
durable-write path and does not call this gate.

**Import Ledger**: SQLite state recording source fingerprints, committed raw
event cursors, stable episode identities, and memory identities. Gate failures,
recovery failures, and index failures belong to their dedicated SQLite audit
or queue state.

**Markdown Truth**: One atomic, parseable Markdown artifact per Coding Memory.
It contains the complete deterministic Evidence Fact snapshot and is the
authoritative recoverable representation.

**Recall Episode**: The rebuildable parent search document projected from one
Coding Memory. It is not a second durable Task Episode or another source of
truth.

**Atomic Fact Document**: A rebuildable child search document projected from a
grounded Semantic Atomic Fact inside Markdown truth or from an authoritative
Evidence Fact. Every source fact keeps its own raw child even when a semantic
child cites it, because citation does not prove complete semantic coverage. A
child may cite one or more authoritative Evidence Facts, and its parent is the
Recall Episode for that Coding Memory. These lossless source-fact documents
remain disposable index data; Markdown is still the evidence authority.

**Index Queue**: SQLite-backed outbox of Markdown revisions waiting to be
indexed. Claims use atomic leases and a successful unchanged content hash is a
no-op.

**Index Readiness**: The operational state in which the LanceDB memory and
document fingerprints match Markdown truth and the Index Queue has no pending,
leased, failed, or stale jobs. Durable import and index readiness are separate
states.

**Recall Context**: A budgeted task-shaped Markdown artifact plus JSON sidecar,
containing ranked Coding Memories, complete source-fact excerpts, provenance,
and an auditable record of evidence omitted by the compiler.

**Retrieval Providers**: One manifest-recorded embedding and reranker
configuration shared by indexing and recall. Production uses the configured
DashScope embedding endpoint plus a learned local reranker; deterministic
hashing and fusion-score ranking are test Adapters.

**Evaluation Run**: One immutable suite execution with an explicit run
identifier, inputs, output directory, and suite-appropriate manifest. Applicable
identity may include commit, selection, provider, seed, repeat, workspace,
memory, corpus, and resource fields; not every suite has every field.

## Non-negotiable invariants

1. Repository namespace participates in every durable identity and unique key.
2. Committed cursors advance only after their complete durable write set commits.
3. Quotes must be exact source substrings; roles and outcomes come from events.
4. Verified Fix requires both change evidence and successful verification.
5. An index can be deleted and structurally rebuilt from Markdown truth, with
   both memory-level and parent-child document parity. A provider-managed
   embedding alias does not promise bit-for-bit identical vectors across
   provider-side model changes.
6. Evaluation reports are pure readers and never mutate runtime state.
7. Memory-off runs cannot read from or write to memory-on state.
8. An index cannot mix vectors from different embedding model identities or
   dimensions.
9. Recall never scans Markdown as a silent fallback. If the index is absent or
   behind truth, diagnostics expose that state and recall may report no
   candidates.
