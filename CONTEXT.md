# CodeCairn

CodeCairn is a local-first Memory OS for agents. It owns durable memory
independently from any agent runtime; version 0.1 delivers one complete Coding
Profile for repository-scoped work.

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
content.

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

## Durable and operational state

**Markdown Truth**:
The authoritative, human-readable representation of Coding Memories and
Evolution Records. “Truth” describes storage authority, not factual
verification.

**Memory Verification**:
An optional operation that checks a Coding Memory against Evidence Facts.
Verification never decides whether the memory may be stored.

**Import Ledger**:
Operational state that records source fingerprints, committed cursors, stable
episode identities, memory identities, and processing failures.

**Index Queue**:
The transactional outbox of durable revisions waiting to enter the disposable
search projection.

**Index Readiness**:
The state in which the search projection matches Markdown Truth and no index
work is pending, leased, failed, or stale.

**Evaluation Run**:
One immutable benchmark or acceptance execution bound to explicit inputs,
configuration, repository state, and generated artifacts.
