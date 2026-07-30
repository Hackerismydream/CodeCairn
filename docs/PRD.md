# CodeCairn v0.1 Product Requirements

Status: version 0.1 historical product contract, accepted on 2026-07-27 and
completed by `v0.1.0-rc1`. Current Pico behavior is in
[`v0.2/README.md`](v0.2/README.md); future product scope is in
[`roadmap.md`](roadmap.md).

## Product statement

CodeCairn is an auditable local long-term memory runtime for coding agents.
Agents use but do not own it. Version 0.1 ships one complete Coding Profile: it
converts Codex and Claude Code work into inspectable repository memory, evolves
active revisions through supersession, and returns bounded context to the next
coding task.

The release is both a usable product and a readable learning project. A feature
that works only through an internal Python seam, a benchmark-only adapter, or a
future roadmap does not count as shipped.

## Current baseline

The implementation baseline is Fable's `954f728` code plus this accepted
pre-development documentation on its `main` descendant:

| Area | Baseline reality |
|---|---|
| Import | Codex and Claude JSONL normalize into Agent Trace and Task Episode |
| Durable state | Markdown, SQLite Import Ledger, transactional Index Queue |
| Search | LanceDB parent/child projection and hierarchical recall |
| Index lifecycle | Import drains by default; CLI/HTTP sync, rebuild, and status exist |
| Provider startup | Retrieval providers are lazy and configuration failures are typed |
| Memory production | Ordinary import still produces deterministic Failed Command only |
| Taxonomy | Six historical types and Evidence Gate paths remain in code |
| Agent integration | No MCP server or installed session-end hooks |
| Onboarding | No `init`, config file, automatic repository identity, or PyPI release |
| Published benchmark | Historical `benchmark-v3` records 82.60% on 1,540 LoCoMo questions |
| Source size | 34,091 physical Python lines: 16,841 evaluation and 17,250 other source |

The historical benchmark remains valid for its frozen commit and protocol. It
is not evidence for the version 0.1 architecture until a new run is bound to
the release candidate.

## Release outcome

A new user can install CodeCairn, initialize it in a repository, connect Codex
or Claude Code, finish one task, and then ask a later session what happened.
The agent receives active memory with source links. If later work makes prior
knowledge stale, the new memory takes over normal recall while history remains
inspectable and restorable.

The release demo is an explicit sequence, not one implied `init` side effect:

```text
uv tool install codecairn==0.1.0
  -> codecairn init
  -> register one client MCP server
  -> dry-run and install one client hook
  -> review/trust the Codex hook when Codex is selected
  -> codecairn doctor --strict
  -> finish a coding task
  -> hook imports deterministic Task Experience and queues projection
  -> next session calls recall
  -> recall performs bounded deterministic index preflight
  -> agent receives active Work State, Knowledge, Preference, and Experience
  -> memory history shows any superseded predecessor
```

The measured onboarding claims are five minutes for offline manual
import-to-recall and ten minutes for one client's MCP-plus-hook integration.

## Users

### Coding-agent user

Wants memory to work across sessions and across Codex or Claude Code without
manually maintaining a knowledge file.

### Learner

Wants to understand an end-to-end Memory OS by following a small number of
modules, one walkthrough, and reproducible evaluation commands.

### Maintainer

Wants every release claim bound to a clean commit and a checked-in artifact,
without preserving historical framework code that no longer serves the
product.

## Product requirements

### Memory capture

- **FR-01**: Codex and Claude Code sources normalize into the existing Agent
  Trace and stable Task Episode contracts.
- **FR-02**: Every Task Episode creates exactly one Task Experience, including
  failed, partial, interrupted, and unknown outcomes.
- **FR-03**: One Task Episode may create zero or more Repository Knowledge and
  User Preference items and at most one Work State when it leaves unresolved
  work or closes a previously open Workstream.
- **FR-04**: Coding Memory has exactly four public types: Task Experience,
  Repository Knowledge, User Preference, and Work State.
- **FR-05**: Storage does not require evidence verification. The system, not a
  model, authors namespace, source references, roles, command outcomes, file
  changes, exact quotes, and verification state.
- **FR-06**: User Preference candidates come only from user-authored source
  content. Model paraphrase is allowed and retains Source Fact Registry IDs.
- **FR-07**: A missing semantic model never discards source import or the
  deterministic Task Experience. Optional semantic work remains pending and
  visible through diagnostics.

### Memory evolution

- **FR-08**: Task Experience is append-only.
- **FR-09**: An open or terminal Work State supersedes the prior active state
  for the same Workstream.
- **FR-10**: A provably newer explicit User Preference may supersede the
  previous preference on the same subject. Cross-session order that cannot be
  proven defaults to keep-both.
- **FR-11**: Repository Knowledge supersedes only a same-subject item proposed
  as obsolete or contradictory; otherwise both remain active.
- **FR-12**: Supersession is automatic after structural validation and does not
  require evidence verification or human approval.
- **FR-13**: Every supersession is an immutable Evolution Record. It never
  deletes or rewrites a memory.
- **FR-14**: Restore creates a new memory revision from historical content and
  supersedes the unique active tip in that historical memory's lineage.

### Recall

- **FR-15**: Default recall searches only active memory.
- **FR-16**: A matching open Work State is pinned first. Closed Work State and
  the remaining Repository Knowledge, User Preference, and Task Experience are
  ranked under per-type caps. Exact Evidence Fact or source-line children are
  globally packed under one strict total context budget.
- **FR-17**: Recall returns Markdown for the agent and a JSON sidecar containing
  memory identity, type, status, ranking, provenance, provider identity, and
  the IDs actually rendered, public type caps, and omissions.
- **FR-18**: Historical recall is explicit through `include_superseded` and
  `memory history`.
- **FR-18a**: Recall provides read-your-writes for deterministic memory through
  a bounded namespace index preflight. Failure to reach the required cursor is
  `index_not_ready`, never stale success.

### Product surfaces

- **FR-19**: CLI, MCP, and session-end hooks call the same service use cases.
- **FR-20**: MCP exposes `recall`, `remember`, `list_memories`, `get_memory`,
  `memory_history`, `import_session`, and `doctor`, plus
  `codecairn://memory/{id}`.
- **FR-21**: Claude Code uses `SessionEnd`. Codex uses its `Stop` hook and relies
  on import idempotency because Stop may fire per turn.
- **FR-22**: Hook execution durably imports source, queues remaining processing,
  never blocks agent shutdown, emits no protocol-breaking stdout, and exposes
  failures through `doctor`.
- **FR-23**: `codecairn init` derives repository identity, writes
  `codecairn.toml`, records an explicit retrieval profile, and prints working
  import, recall, MCP, and hook commands.
- **FR-24**: CLI and MCP are the required version 0.1 interfaces. The legacy
  unpublished HTTP compatibility adapter is retired under ADR 0052.
- **FR-24a**: CLI supplies safe repository export and namespace reset with
  dry-run, explicit confirmation, and recoverable backup-first behavior.
- **FR-24b**: Onboarding supplies visible, reviewable `AGENTS.md` and
  `CLAUDE.md` instructions that ask the agent to recall before work and
  remember durable repository knowledge. CodeCairn does not inject them.
- **FR-24c**: Configuration and `doctor` disclose whether embedding and
  semantic extraction are local, networked, or disabled, and which source
  content class may leave the machine.

### Evaluation and release

- **FR-25**: The repository exposes one-command lifecycle smoke, scale,
  retrieval, LoCoMo-200, LoCoMo-1540, coding A/B, evidence verification, and
  source-budget targets.
- **FR-26**: The offline smoke covers import, four-type capture, supersession,
  active recall, history, restore, MCP, and hook ingestion.
- **FR-27**: A release score comes only from a frozen manifest and raw
  aggregates generated at the release commit.
- **FR-28**: The shipped product core is at most 10,000 physical Python lines
  and all `src/codecairn` source is at most 15,000.
- **FR-29**: The package ships under MIT with curated wheel/sdist contents and
  supports persistent `uv tool install codecairn==0.1.0`.
- **FR-30**: The release includes a five-minute quickstart, five-layer
  architecture map, end-to-end trace walkthrough, code-reading path, ADR index,
  and evaluation guide.

## Operational requirements

- Markdown is authoritative for Coding Memories and Evolution Records.
- SQLite owns transactional cursors, work queues, active projections, and
  diagnostics.
- LanceDB is disposable and rebuildable from Markdown plus Evolution Records.
- A committed source cursor advances only in the completion transaction after
  its complete Write Intent file set has been fsynced and verified.
- A supersession never crosses a Memory Namespace, references itself, or forms
  a cycle.
- Index identity cannot mix embedding provider, model, revision, adapter, or
  dimension.
- Evaluation reports are pure readers and memory-off runs remain physically
  isolated.
- No provider error is converted into a successful empty result unless the
  relevant contract explicitly defines an optional pending state.

## Release acceptance

Version 0.1 uses an immutable implementation/evidence pair. Provider runs bind
to a clean `implementation_sha`. A direct descendant `evidence_sha` may add
only generated artifacts and generated documentation; the release tag points
to `evidence_sha`. Any code change creates a new implementation candidate and
invalidates every prior gate.

Version 0.1 is releasable only when all of these pass for that pair:

1. `make format` and `make check`.
2. Installed-wheel CLI import-to-recall smoke.
3. In-process MCP tool/resource smoke.
4. Real Claude Code SessionEnd and Codex Stop hook smoke, with manual import as
   the documented fallback for client hook defects.
5. Supersession, history, and restore end-to-end tests.
6. `make eval-smoke`.
7. A frozen full 1,540-question LoCoMo run scoring at least 82.00%, with a target
   no lower than the historical 82.60%.
8. Local recall P95 at or below four seconds under the release protocol.
9. `make evidence-verify` against the new release bundle.
10. Source-budget verification at or below 10,000 core and 15,000 total lines.
11. Clean wheel and sdist installation through a persistent tool environment.
12. Documentation link, command, and terminology checks.

## Version 0.1 out of scope

- Pico/Raven integration, delivered later in version 0.2.
- Dynamic or user-selected profiles.
- Agent Skill synthesis, clustering, or general background Reflection.
- Hidden prompt injection or a resident watcher daemon.
- Dashboard or memory-editing UI.
- Cloud hosting, authentication, organizations, billing, or multi-user tenancy.
- Global cross-repository memory.
- Standalone Memory Verification operation; system-derived Task Experience
  verification facets remain.
- General document or multimodal ingestion.
- A formal same-harness EverOS comparison.
- Compatibility migration for pre-release runtime roots. Historical evidence
  bundles remain independently verifiable; users re-import owned traces into a
  version 0.1 root.

## Design and delivery references

- [`v0.1/README.md`](v0.1/README.md) — accepted product and scope design.
- [`architecture.md`](architecture.md) — target architecture and current delta.
- [`plan/README.md`](plan/README.md) — delivery order and task state.
- [`release-readiness.md`](release-readiness.md) — release evidence checklist.
