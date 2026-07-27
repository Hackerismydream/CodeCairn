# Version 0.1 Scope

Status: accepted design. Implementation is incomplete until the task files under
[`../plan/tasks/`](../plan/tasks/) pass verification and merge.

## Purpose and boundary

Version 0.1 turns the implemented CodeCairn components into a small, auditable
local long-term memory runtime for coding agents. It owns memory capture,
evolution, recall, inspection, and local operations. Internally it has Memory
OS authority. Codex and Claude Code remain clients; they own agent execution
and tool use.

The release has one implicit Coding Profile. It does not introduce a generic
profile/plugin framework, Raven adapter, cloud service, or dashboard.

## System relationship

```text
Codex / Claude Code
   |          |
   | MCP      | Session-end hook
   v          v
entrypoints: CLI / MCP / hooks
              |
              v
         service use cases
              |
       +------+-------+
       |              |
       v              v
   memory model    importer adapters
       |
       v
Markdown Truth <-> SQLite state -> LanceDB projection
       |
       v
evaluation adapters -> immutable artifacts
```

Dependencies still point inward:

```text
entrypoints -> service -> memory
                 ^          ^
                 |          |
             importers   storage adapters
```

## Concepts

| Layer | Durable product concept | Owner |
|---|---|---|
| Source | Agent Trace, Evidence Fact | importers and memory |
| Experience | Task Episode, Task Experience | memory and capture service |
| Knowledge | Repository Knowledge, User Preference, Work State | memory and capture service |
| Evolution | Evolution Record and derived Memory Status | memory and evolution service |
| Recall | Recall Context and sidecar | recall service |

The canonical definitions live in [`../../CONTEXT.md`](../../CONTEXT.md).

## Ownership

| Owner | Creates or calls | Stores or returns |
|---|---|---|
| Provider importer | Agent Trace | source references and normalized events |
| Capture service | Task Experience and optional Knowledge items | Write Intent, Markdown, SQLite completion |
| Evolution service | validated Evolution Record | Multi-file Write Intent plus active-status projection |
| Mini Cascade | search documents from durable artifacts | LanceDB only |
| Recall service | active-memory selection and context compilation | Markdown result plus JSON sidecar |
| CLI/MCP/hooks | service use cases | presentation only |
| Evaluation | frozen runs through public/service contracts | immutable artifacts |

No entrypoint creates an alternative memory model. No storage adapter decides
capture, supersession, or recall policy.

## End-to-end lifecycle

```text
owned trace
  -> normalized Agent Trace
  -> stable Task Episodes
  -> exactly one Task Experience per Episode
  -> optional Repository Knowledge / User Preference / Work State
  -> automatic validated Supersession
  -> Markdown + SQLite commit
  -> Index Queue
  -> LanceDB projection
  -> active-only Recall Context
  -> later trace repeats the cycle
```

Manual `remember` enters at the Coding Memory step and is marked
`agent_asserted`. It does not invent source provenance. Direct User Preference
requires Source Fact Registry IDs that resolve to normalized user-authored
source.

## Product entrypoints

| Surface | Version 0.1 role |
|---|---|
| CLI | Setup, import, remember, recall, inspection, processing, index, doctor, export/reset, evaluation |
| MCP | Live explicit memory access for Codex and Claude Code |
| Hooks | Post-session or post-turn trace import |
| HTTP | Deferred; v0.1 supports local CLI, stdio MCP, and hooks |

## Scope-wide constraints

- One Memory Namespace per repository in the Coding Profile.
- Existing internal `repo_key` names may remain to avoid a broad rename.
- Memory content and Evolution Records are append-only.
- Default recall excludes superseded memory.
- A missing semantic provider creates visible pending work, not lost source.
- Hook capture followed by recall provides deterministic read-your-writes or a
  typed freshness error, never stale success.
- Historical pre-v0.1 runtime roots are not a compatibility target.
- Product core must fit within 10,000 Python lines; total package within 15,000.
- Every public metric is generated from a checked-in run artifact.

## Module index

- [`memory-lifecycle.md`](memory-lifecycle.md) — records, cardinality,
  supersession, storage, and migration boundary.
- [`schema-contract.md`](schema-contract.md) — exact durable/operational
  fields, bounds, canonical identity, Markdown/SQLite mapping, and DTO rules.
- [`agent-integration.md`](agent-integration.md) — CLI, MCP, Claude Code, and
  Codex contracts.
- [`onboarding-and-operations.md`](onboarding-and-operations.md) — init,
  configuration, providers, queues, and diagnostics.
- [`evaluation-and-release.md`](evaluation-and-release.md) — one-command
  evaluation, source budget, and release gates.
- [`walkthrough.md`](walkthrough.md) — one concrete trace-to-recall example.
- [`learning-path.md`](learning-path.md) — required learner-facing reading path.

## Cross-cutting decisions

ADRs 0043–0051 own the accepted version 0.1 product decisions. Earlier ADRs
remain historical context and are amended or superseded where noted.
