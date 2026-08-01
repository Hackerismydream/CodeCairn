# CodeCairn

CodeCairn is an auditable local long-term memory runtime for coding agents. It
owns durable memory independently from any agent runtime; internally this is
its Memory OS authority. Version 0.1 delivers one complete Coding Profile for
repository-scoped work.

## Product language

**Memory OS**:
The independent local system that captures, stores, evolves, and recalls memory
for agent clients.
_Avoid_: Agent runner, coding-agent wrapper

**Coding Profile**:
The version 0.1 interpretation of coding sessions and repository-scoped work.
It is implicit product behavior, not a user-selected mode.
_Avoid_: Coding mode, plugin profile

**Memory Namespace**:
The durable isolation boundary for memory identity and recall. The Coding
Profile uses one repository identity as its Memory Namespace.
_Avoid_: Tenant, account

**Coding Memory**:
One durable Task Experience, Repository Knowledge, User Preference, or Work
State produced by the Coding Profile.
_Avoid_: Summary blob, context cache

## Source Layer

**Source Layer**:
Imported material and system-derived observations that preserve what happened
and where it came from without model-authored interpretation.

**Agent Trace**:
A provider-independent sequence of normalized coding-session events with stable
source locations.

**Evidence Reference**:
An immutable pointer from a derived record to its supporting source event.

**Evidence Fact**:
A statement derived deterministically from normalized events, such as a message
role, exact quote, command result, or changed file. Model output is never an
Evidence Fact.

## Experience Layer

**Experience Layer**:
Bounded, source-linked accounts of tasks or interactions.

**Task Episode**:
A stable extraction boundary formed by one user task and its related actions and
outcome. It closes at the next user task or an explicit Stop, SessionEnd, or
manual-import boundary. Appending later source events does not rename a
committed Episode; a continuation becomes a new linked Episode.

**Task Experience**:
The durable account of one Task Episode, including its goal, relevant actions,
outcome, and source references. Debugging, failed commands, and verified results
are facets of the experience, not separate memory types.
_Avoid_: Debug Episode, Failed Command memory, Verified Fix memory

**Experience Outcome**:
The observed result of a Task Experience: `success`, `failure`, `partial`, or
`unknown`.

## Knowledge Layer

**Knowledge Layer**:
Reusable, source-linked knowledge derived from one or more experiences.

**Repository Knowledge**:
A reusable repository-specific statement about architecture, conventions,
commands, constraints, or solutions.
_Avoid_: Repository Convention as a top-level memory type

**User Preference**:
A reusable working or output preference derived from user-authored source
content. Version 0.1 presents this repository-scoped type as **Repository
Working Preference**. Myna may explicitly promote an active repository
preference into Person-global scope without copying the Coding Memory.

**Workstream**:
One independently progressing unit of work inside a Memory Namespace, identified
by an issue, branch, task, or session-derived key.

**Work State**:
The latest known goal, progress, blockers, and next step or terminal outcome
for one Workstream. Its workstream state is `open` or `closed`; this is
separate from the Coding Memory's active/superseded lifecycle status.

## Evolution Layer

**Evolution Layer**:
Cross-memory decisions that change which durable memories are active without
rewriting their content or deleting their history.

**Supersession**:
A directed relation in which a newer memory replaces an older active memory for
default recall.

**Evolution Record**:
The immutable record of one Supersession, including predecessor, successor,
reason, proposer, and source lineage.

**Memory Status**:
The recall lifecycle state derived from Evolution Records. Version 0.1 uses
`active` and `superseded`.

**Restore**:
The act of creating a new memory from a historical memory and making that new
revision active. Restore never mutates the historical memory or reverses an
existing Evolution Record.

## Recall Layer

**Recall Layer**:
Task-shaped selection and compilation of memory for an agent client. It is a
derived view, not another durable source of truth.

**Recall Context**:
The bounded Markdown context returned to an agent together with a structured
sidecar describing selected memories, provenance, status, ranking, and omitted
candidates.

**Recall Admission**:
The auditable decision that at least one retrieved memory is relevant enough
to enter Recall Context. Ranking orders candidates after retrieval; it does not
force CodeCairn to choose a memory. If no candidate is admitted, recall returns
an empty ranked result and an explicit abstention.
_Avoid_: Always return top-k, best available answer

## Durable and operational state

**Markdown Truth**:
The authoritative, human-readable representation of Coding Memories and
Evolution Records. “Truth” describes storage authority, not factual
verification.

**Verification Facet**:
A system-derived Task Experience facet that records an observed verification
fact. Version 0.1 has no standalone Memory Verification record or operation,
and verification never decides whether memory may be stored.

**Import Ledger**:
Operational state that records source fingerprints, committed cursors, stable
episode identities, memory identities, and processing failures.

**Index Queue**:
The transactional outbox of durable revisions waiting to enter the disposable
search projection.

**Write Intent**:
The SQLite recovery record that reserves one deterministic multi-file durable
write before Markdown is created and is completed only after Markdown,
mirrors, projections, queues, and any source cursor agree.

**Index Readiness**:
The state in which the search projection matches Markdown Truth and no index
work is pending, leased, failed, or stale.

**Evaluation Run**:
One immutable benchmark or acceptance execution bound to explicit inputs,
configuration, repository state, and generated artifacts.

## Version 0.3 local Hub

**Hub Read Interface**:
The three-operation, view-oriented local Interface used by the read-only Hub to
inspect Memories, explain Recall, and project a point-in-time Doctor snapshot.
It is bound to one resolved Memory Namespace and is not a general network API.

**Foreground Hub Host**:
The loopback-only process pair that serves the Hub web application and Hub Read
Interface for the lifetime of one terminal command. It is not a daemon and
does not imply remote availability.

**Hub Comprehension Acceptance**:
The version 0.3 product gate that joins one exact candidate's machine-derived
Pico continuity and Hub-read evidence with separately collected, blindly
reviewed answers from eligible first-time target learners. A source-checkout
machine pilot, screenshot, fixture, or unreviewed answer is not formal
acceptance.

**Hub Presentation Snapshot**:
The machine-frozen Memories, Recall, System, and lifecycle projection that the
live Hub must match when a participant session opens and submits. Participant
responses bind its digest and the campaign-manifest digest so answers cannot
be reused for another candidate.

**Lifecycle Comprehension**:
Understanding that an older Coding Memory is either `active` or `superseded`,
that Supersession excludes the predecessor from default recall, and that the
immutable relation identifies its active successor. Version 0.3 does not use
time expiry as a Memory Status.

## Version 0.4 local onboarding

**Hub Onboarding Interface**:
The separate two-operation local Interface used to preview supported owned
coding-session sources and apply one consent-bound import and continuous-capture
plan. It is bound to one server-selected repository and does not add writes to
the Hub Read Interface.
_Avoid_: Writable Hub Read Interface, filesystem browser

**Onboarding Preview**:
A no-write, bounded observation of fixed Codex and Claude Code history roots,
their exact repository match, current import state, supported Hook plans, and
the versioned Retention Disclosure. It may produce a Consent Token for one
valid selected plan. It is not evidence that an import occurred.

**Opaque Source ID**:
A path-free selection handle for one source discovered in an Onboarding
Preview. It is valid only inside that bound preview and is neither a source
locator nor repository authority.

**Retention Disclosure**:
The versioned explanation of which normalized source identity, bounded Evidence
Facts, Coding Memories, operational receipts, and configured data egress an
Onboarding plan will create, and which provider-native content CodeCairn will
not silently copy or preserve.

**Consent Token**:
A short-lived opaque capability binding one repository, preview revision,
selected source digests, planned Hook writes, settings digests, retention
revision, egress posture, and expiry. Apply accepts the token instead of paths
or a second mutable selection.

**Onboarding Apply Report**:
The itemized result of applying a Consent Token after a complete stale
preflight. It distinguishes created, skipped, failed, partial, and index
readiness outcomes; it never rolls back a prior durable import or converts a
partial result into success.

**Live Onboarding**:
The real-source journey from Preview through explicit consent and Apply into
the existing Memories and Recall views. A Guided Demo is isolated and cannot
substitute for Live Onboarding or formal version 0.4 acceptance.

## Myna Person Library

**Myna**:
The person-first local memory runtime built on the CodeCairn compatibility
package. Myna owns memory; Pico owns agent execution.
_Avoid_: Agent harness, task workbench

**Person**:
The one local owner of a runtime root, identified by a stable random opaque
`person_id`. It is not derived from a client, repository, provider account,
email address, or operating-system username.

**Memory Scope**:
The effective applicability of a memory. Phase one uses `repository` for an
existing Coding Memory and `global` only for an explicitly promoted User
Preference.

**Source Context**:
The immutable repository and memory identity from which a scoped library item
originates. Scope changes applicability, not provenance or memory identity.

**Global Preference Promotion**:
An immutable Person-owned reference that makes one active repository User
Preference available in every repository. Repeating the same promotion is
idempotent; promotion never copies or rewrites the source memory.

**Preference Shadowing**:
The rule that an active current-repository User Preference suppresses a global
preference with the same subject during recall. The suppressed promotion stays
durable and the recall sidecar records the local memory IDs that shadowed it.

## Version 0.2 Pico integration

The following terms define the implemented CodeCairn side of the version 0.2
Pico integration. Pico selects this backend on its current product line; exact
cross-repository evidence remains bound to the Pico and CodeCairn commits that
produced it.

**Pico Source Journal**:
The CodeCairn-owned append-only source that records persisted Pico after-Turn
batches under schema `codecairn.pico.source.v1`. It is separate from Pico
Session storage and has its own staged append and replay protocol. Boundary
`pico_turn_end` closes one imported batch without asserting task success.
_Avoid_: Pico Session mirror, shared Session database

**Pico Agent Trace**:
The provider-independent Agent Trace produced by normalizing a Pico Source
Journal with provider `pico`. Its Evidence Facts follow the same structured
source rules as Codex and Claude traces.
_Avoid_: Pico transcript summary

**Pico Memory Adapter**:
The installed CodeCairn Integration Module loaded from Pico entry
`codecairn`, identified by plugin manifest `codecairn-memory`, and contributing
Memory Backend key `codecairn`. It translates Pico's MemoryBackend Interface
to `CodeCairnApplication` without owning memory policy.
_Avoid_: MCP proxy, second memory runtime
