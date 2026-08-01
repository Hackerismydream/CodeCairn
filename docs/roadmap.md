# CodeCairn Roadmap

CodeCairn is an independent, local Agent Memory OS. Coding agents are its first
product profile, and Pico is its first full runtime integration. The roadmap is
ordered by user-visible closure rather than by internal component count.

## Current product line

| Version | User outcome | Core delivery | Completion signal |
|---|---|---|---|
| v0.1 | A coding agent can keep auditable memory across sessions | Five-layer memory model, four coding-memory types, CLI, MCP, hooks, lifecycle-aware recall, immutable evidence | Installed capture, recall, recovery, evaluation, and evidence bundle are reproducible |
| v0.2 | A real agent runtime remembers and may safely abstain | Pico backend, source journal, fresh-process continuity, EverOS product-coupling removal, relevance admission, memory-off/on evidence | Exact Pico/CodeCairn pair passes installed continuity and hard-negative recall; positive task-effect claims require an eligible campaign |

## Product roadmap

| Version | User outcome | Core delivery | Completion signal |
|---|---|---|---|
| v0.3 | People can understand memory | Local read-only Hub for memory list, type, source, evidence, status, recall explanation, evolution history, and health; machine plus blind-human acceptance campaign | The technical gate passes, then at least four of five eligible first-time target learners can answer what was remembered, where it came from, why it was recalled, and which active memory replaced the superseded predecessor |
| v0.4 | People can carry memory | Separate local Onboarding Interface for fixed-source discovery, exact-repository preview, retention disclosure, consent-bound idempotent import, itemized results, and explicit Codex/Claude Hooks; Pico remains continuous-only | An exact installed candidate carries real owned Codex and Claude history from a no-write Preview into the first source-linked Memory and explained Recall, repeats without duplicates, proves selected future capture, and seals independently verifiable evidence |
| v0.5 | A person's agents share governed memory | Myna Person Library, repository/global scope, explicit preference promotion by reference, repository shadowing, and attributable lifecycle operations through the application service | The same local Person receives one promoted preference across repositories, a repository override wins visibly, retries are idempotent, and no client can choose another owner or scope |
| v0.6 | Memory is continuously available | Local daemon, stable local Interface, background queue, automatic recovery, and event stream | Agent and Hub share one state after terminal closure or process restart |
| v0.7 | Memory compounds into capability | Aggregate Task Experiences into Cases and repeated successful patterns into Skills, with quality gates and rollback | A new task demonstrably uses a derived Case or Skill rather than only replaying an old summary |
| v1.0 | The local Memory OS is stable | Stable local semantics, storage migration, backup/restore, long-running reliability, supported client SDK, and release compatibility policy | Local data remains portable and recoverable across upgrades; supported agents share the same memory semantics |
| v2.0 | Teams can share governed memory | Authenticated remote API, encrypted sync, user/agent/team spaces, deployment, and backup services | Local and remote runtimes share one SDK and semantic contract with verified isolation |

## Ordering constraints

Finish the real Agent loop before the Hub. Let a person understand Memory before
asking them to carry existing sources into it. Observe real imported data before
freezing governance operations, and discover governance needs before extracting
a daemon. Stabilize local ownership before adding remote collaboration. Version
0.3 is also the deliberate readability pass: remove obsolete compatibility
paths and recover code headroom without hiding domain behavior behind
abstractions.

The checked-in [`Hub application`](../apps/hub-web/README.md) now uses a
foreground local presentation adapter and real `CodeCairnApplication` reads.
The checked-in
[`acceptance infrastructure`](v0.3/hub-acceptance.md) adds the frozen
retry-policy scenario, public CodeCairn and fresh-process Pico evidence
collectors, Hub adapter, participant/reviewer forms, strict reducer, sealing,
and offline verification.

This closes the implementation seams, not the version 0.3 completion signal.
No formal artifact yet proves an installed Hub distribution and raw collector,
the real Pico scenario against the declared configured LLM, five eligible
first-time target learners, separate human blind reviews, and a sealed
offline-verified bundle.

Version 0.4 is defined by
[`v0.4/onboarding.md`](v0.4/onboarding.md) and ADR 0063. A checked-in Module,
transport Adapter, contract example, or passing test suite establishes only an
implementation candidate. Formal completion additionally requires one sealed,
exact-candidate installed artifact that demonstrates real Codex and Claude
source discovery, no-write Preview, digest-bound consent, store-to-recall,
idempotency, selected Hook capture, honest partial failure, and Pico's
continuous-only boundary without fixture fallback.

ADR 0064 defines the first version 0.5 slice. It introduces Myna's Person
Library without migrating the package or changing existing memory identity.
The implementation candidate must prove stable local ownership, explicit and
idempotent preference promotion, multi-scope recall, visible repository
shadowing, and fail-closed broken references. It does not start Myna Desktop or
change Pico during the active Dogfood campaign.
