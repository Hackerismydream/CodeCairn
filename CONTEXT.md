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

**Coding Memory**: An evidence-backed reusable item of one of five types: Debug
Episode, Repository Convention, Failed Command, Verified Fix, or User
Preference.

**Evidence Gate**: Type-specific validation that decides whether a Coding
Memory may become durable truth. It validates claims against Evidence Facts,
not against LLM-provided labels.

**Import Ledger**: SQLite state recording source fingerprints, committed raw
event cursors, stable episode identities, memory identities, and failures.

**Markdown Truth**: One atomic, parseable Markdown artifact per Coding Memory.
It contains the complete deterministic Evidence Fact snapshot and is the
authoritative recoverable representation.

**Recall Episode**: The rebuildable parent search document projected from one
Coding Memory. It is not a second durable Task Episode or another source of
truth.

**Atomic Fact Document**: A rebuildable child search document projected from
one Evidence Fact inside Markdown truth. Its parent is the Recall Episode for
that Coding Memory.

**Index Queue**: SQLite-backed outbox of Markdown revisions waiting to be
indexed. Claims use atomic leases and a successful unchanged content hash is a
no-op.

**Recall Context**: A concise task-shaped Markdown artifact plus JSON sidecar,
containing ranked Coding Memories and their provenance.

**Retrieval Providers**: One manifest-recorded embedding and reranker
configuration shared by indexing and recall. Production uses the configured
DashScope embedding endpoint plus a learned local reranker; deterministic
hashing and fusion-score ranking are test Adapters.

**Evaluation Run**: One immutable execution identified by task, arm, repeat,
seed, model configuration, workspace snapshot, memory snapshot, and artifacts.

## Non-negotiable invariants

1. Repository namespace participates in every durable identity and unique key.
2. Committed cursors advance only after their complete durable write set commits.
3. Quotes must be exact source substrings; roles and outcomes come from events.
4. Verified Fix requires both change evidence and successful verification.
5. An index can be deleted and deterministically rebuilt from Markdown truth,
   with both memory-level and parent-child document parity.
6. Evaluation reports are pure readers and never mutate runtime state.
7. Memory-off runs cannot read from or write to memory-on state.
8. An index cannot mix vectors from different embedding model identities or
   dimensions.
