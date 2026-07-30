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
| v0.3 | People can understand memory | Local read-only Hub for memory list, type, source, evidence, status, recall explanation, evolution history, and health; simplify internal compatibility and evaluation code after the UI fixes the concepts people actually need | A non-developer can answer what was remembered, where it came from, why it was recalled, and whether it is stale |
| v0.4 | People can govern memory | Remember, supersede, restore, archive, conflict handling, and retry through the same application service used by CLI, MCP, and Hub | Every mutation is attributable, auditable, reversible, and does not edit SQLite or Markdown behind the service |
| v0.5 | Memory is continuously available | Local daemon, stable local API, background queue, automatic recovery, and event stream | Agent and Hub share one state after terminal closure or process restart |
| v0.6 | Memory compounds into capability | Aggregate Task Experiences into Cases and repeated successful patterns into Skills, with quality gates and rollback | A new task demonstrably uses a derived Case or Skill rather than only replaying an old summary |
| v1.0 | The local Memory OS is stable | Stable local semantics, storage migration, backup/restore, long-running reliability, supported client SDK, and release compatibility policy | Local data remains portable and recoverable across upgrades; supported agents share the same memory semantics |
| v2.0 | Teams can share governed memory | Authenticated remote API, encrypted sync, user/agent/team spaces, deployment, and backup services | Local and remote runtimes share one SDK and semantic contract with verified isolation |

## Ordering constraints

Finish the real Agent loop before the Hub. Use the Hub to discover governance
needs before extracting a daemon. Stabilize local ownership before adding
remote collaboration. Version 0.3 is also the deliberate readability pass:
remove obsolete compatibility paths and recover code headroom without hiding
domain behavior behind abstractions.

The checked-in [`Hub application`](../apps/hub-web/README.md) now uses a
foreground local presentation adapter and real `CodeCairnApplication` reads.
This closes the technical browser-to-runtime seam; version 0.3 still requires
the non-developer comprehension acceptance signal in the table above.
