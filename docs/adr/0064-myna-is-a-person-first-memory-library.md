---
status: accepted
---

# ADR 0064: Myna Is a Person-First Memory Library

## Context

The Coding Profile currently derives one Memory Namespace from one repository.
That boundary is correct for repository knowledge, task experience, and work
state, but it is incomplete for preferences that belong to the person using
many agents across many repositories. Treating a Pico user ID, an operating
system username, or a repository as the person would make ownership depend on
one client or checkout. Copying a preference into every namespace would split
its history and provenance.

CodeCairn is also intended to move into a Pico monorepo after the active Pico
Dogfood campaign. The durable contract must remain usable both as a separate
package and as `packages/myna`; a repository move cannot be a data migration.

## Decision

**Myna** is the product name for the person-first memory runtime built on the
existing CodeCairn compatibility package. Phase one adds a **Person Library**
over, not instead of, repository Memory Namespaces.

One runtime root owns one local **Person**. Its `person_id` is a random opaque
identifier created locally and persisted in Markdown Truth. It is never
derived from an email address, operating-system account, repository path,
Pico `user_id`, Agent ID, or provider account. Phase one has no account merge,
login, team, or remote identity.

Every effective memory has one **Memory Scope**:

- `repository` means the existing Coding Memory belongs to its unchanged
  repository namespace;
- `global` means one eligible repository User Preference is referenced by the
  Person Library for use in every repository.

Existing Coding Memories, IDs, Markdown paths, Evolution Records, and
`codecairn://memory/<id>` URIs remain byte- and identity-compatible. A global
preference is not a copied Coding Memory and receives no second memory ID.
Instead, an immutable **Global Preference Promotion** records the Person,
normalized preference subject, optional predecessor Promotion, and a **Source
Context** containing the source repository key and source memory ID.

Promotion is an explicit governance operation. Its source must be an active
`user_preference` in the foreground repository. At most one promotion is
effective for one `(person_id, subject_key)`. Repeating the same source is
idempotent; attempting to promote an unrelated different source for the same
subject fails with `global_preference_conflict`. If the effective source was
superseded, explicitly promoting its active successor appends an immutable
replacement Promotion; it does not mutate the predecessor. A missing,
superseded, wrong-type, or foreign effective source fails closed until that
valid replacement. Phase one does not automatically infer global scope.

Repository scope is more specific. During recall, any active preference in the
current repository shadows a global promotion with the same `subject_key`.
The global source remains durable and inspectable; the recall sidecar reports
which local memory IDs shadowed it. A broken or no-longer-active promoted source
causes `global_preference_invalid` instead of silently omitting global policy.

`MemoryLibraryApplication` is separate from the legacy-compatible
`CodeCairnApplication`. It owns three person-first use cases:

1. `browse_library` projects repository memories and promoted global
   preferences with effective scope and Source Context;
2. `recall_for` derives the Person, current repository, and active scopes from
   the server-bound application, then performs one candidate-union, admission,
   type-cap, and context-pack pass across both scopes;
3. `promote_preference` creates or replays one immutable promotion.

Callers cannot submit a Person ID, repository key, source repository, or scope
set to these operations. Pico will eventually call `recall_for` as a client;
its own user identifier does not select ownership. The existing Pico adapter
is not changed during the active Dogfood campaign.

Markdown remains durable authority for the Person and promotions. SQLite may
mirror and validate those records as operational state, and LanceDB remains a
rebuildable search projection. Namespace reset is rejected with
`memory_referenced_by_global_scope` while an immutable promotion references a
memory in that namespace. Doctor reports invalid promotion references.

The existing three Hub Read operations may add person, scope, Source Context,
and shadowing projections while keeping their paths and legacy fields. The
only new Hub write is:

```text
POST /hub-governance/v1/preferences/promote
```

Its closed request contains only `memory_id`; the foreground Host binds every
other authority. The Hub remains foreground, loopback-only, token-protected,
same-origin, and `no-store`.

This phase does not add a Desktop process, daemon, task runner, terminal,
Diff/Tools/Subagent workbench, account system, remote sync, arbitrary memory
editing, automatic promotion, or monorepo migration. Pico owns agent execution
and workbench behavior. A future `Myna Desktop` may host Myna Hub after the
resident-runtime decision, but it is not part of this slice.

Add a `v05-person-first` source-budget stage over the same maintained roots as
`v04-onboarding`. Its ceilings are 20,700 product-core and 29,900 total
maintained physical source lines. The additional core allowance covers
fail-closed truth validation and crash-safe promotion/evolution coordination;
the total product budget is unchanged. This is a new additive implementation
budget; it does not change any frozen v0.4 or earlier ceiling.

## Consequences

- All supported agents can converge on one local Person without sharing an
  agent runtime or trusting client identity fields.
- Repository knowledge, experiences, and work state remain isolated; phase one
  global scope is deliberately limited to explicit User Preference promotion.
- Provenance stays singular because global scope references the original
  memory instead of copying content.
- Local repository policy deterministically overrides a global default and the
  decision is visible in the recall sidecar.
- The package can move into `packages/myna` later without changing durable IDs.
- Passing tests proves an implementation candidate, not installed Pico task
  effect, formal user acceptance, or completion of a future Desktop product.
