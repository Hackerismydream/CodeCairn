# CodeCairn Documentation

This is the entry point for CodeCairn's maintained design and operations
documentation. Generated evidence bundles remain self-describing artifacts and
are not design-document sources of truth.

## Current status

CodeCairn is an evidence-native memory runtime for coding agents. The core
import, evidence, storage, retrieval, and evaluation components are
implemented. The project is still pre-release because the public CLI and HTTP
server do not yet own the lifecycle that drains the SQLite index outbox into
LanceDB.

Read these first:

1. [`../CONTEXT.md`](../CONTEXT.md) — domain language and non-negotiable
   invariants.
2. [`architecture.md`](architecture.md) — system boundaries, state ownership,
   and cross-module flows.
3. [`runtime/README.md`](runtime/README.md) — runtime scope and module
   contracts.
4. [`runtime/operations.md`](runtime/operations.md) — current CLI/API behavior,
   index readiness, diagnostics, and the known product gap.
5. [`PRD.md`](PRD.md) — product problem, delivery state, and acceptance targets.

## Document authority

| Question | Authoritative document |
|---|---|
| What does a domain term mean? | [`../CONTEXT.md`](../CONTEXT.md) |
| Which module owns a state transition? | [`architecture.md`](architecture.md) |
| What can the public CLI or HTTP surface do today? | [`runtime/operations.md`](runtime/operations.md) |
| What is required before the product is release-ready? | [`PRD.md`](PRD.md) |
| Why was an architectural choice made? | [`adr/README.md`](adr/README.md) |
| What is the current recall contract? | [`architecture.md`](architecture.md), [`../CONTEXT.md`](../CONTEXT.md), and accepted ADRs |
| Where is the historical Recall v2 proposal? | [`recall-v2-design.md`](recall-v2-design.md) |
| How are benchmark claims built and verified? | [`evidence-bundle.md`](evidence-bundle.md) |
| What numbers are currently published? | [`../evidence/benchmark-v3/README.md`](../evidence/benchmark-v3/README.md) |

When two documents conflict, prefer the more specific document in this table
and repair the stale document in the same change.

## Runtime

| Document | Purpose |
|---|---|
| [`runtime/README.md`](runtime/README.md) | Runtime purpose, boundaries, module ownership, lifecycle, errors, and extension points |
| [`runtime/operations.md`](runtime/operations.md) | Supported entrypoints, configuration, index readiness, diagnostics, and current limitations |
| [`architecture.md`](architecture.md) | Whole-system topology and cross-scope flows |
| [`reference-boundaries.md`](reference-boundaries.md) | Clean-room and external-reference boundaries |
| [`release-readiness.md`](release-readiness.md) | Packaging, governance, CI, and first-release acceptance gate |

## Evaluation and evidence

| Document | Purpose |
|---|---|
| [`evaluation/README.md`](evaluation/README.md) | Suite ownership, immutable artifacts, verification boundaries, and current evidence |
| [`evidence-bundle.md`](evidence-bundle.md) | Public bundle build and offline verification contract |
| [`recall-v2-design.md`](recall-v2-design.md) | Historical proposal and diagnosis; not the current contract |
| [`../benchmarks/retrieval/README.md`](../benchmarks/retrieval/README.md) | Retrieval benchmark inputs and execution |
| [`../benchmarks/coding/README.md`](../benchmarks/coding/README.md) | CodingMemoryBench tasks and verifier contract |
| [`../benchmarks/locomo/README.md`](../benchmarks/locomo/README.md) | LoCoMo operational protocol |

The generated `evidence/benchmark-v3` bundle is the current published evidence.
Earlier bundles are retained as historical artifacts.

## Architecture decision records

The ADR sequence is append-oriented. Earlier ADRs remain useful history even
when a later ADR supersedes a provider, retrieval, or evaluation contract.

| Range | Area |
|---|---|
| [`ADR guide`](adr/README.md) | Status rules, supersession, and recommended reading paths |
| [`0001`](adr/0001-new-public-repository-with-selective-reimplementation.md)–[`0010`](adr/0010-version-one-ships-in-three-milestones.md) | Repository boundary, V1 scope, trace, evidence, storage, entrypoints, evaluation |
| [`0011`](adr/0011-import-resume-replays-only-the-active-suffix.md)–[`0016`](adr/0016-recall-enrichment-is-budgeted-after-ranking.md) | Import recovery, hierarchical recall, provider identity, routing, enrichment |
| [`0017`](adr/0017-locomo-evaluation-reuses-a-verified-immutable-corpus.md)–[`0027`](adr/0027-semantic-projection-rejects-foreign-citations-per-clause.md) | Grounded semantic projection and recall protocol evolution |
| [`0028`](adr/0028-embedding-transport-policy-is-artifact-identity.md)–[`0039`](adr/0039-public-evidence-publishes-exact-repair-outcomes.md) | Spend safety, frozen evaluation protocols, exact repair, public evidence |

## Product and contributor process

| Document | Purpose |
|---|---|
| [`PRD.md`](PRD.md) | Product requirements and delivery traceability |
| [`release-readiness.md`](release-readiness.md) | Release blockers and package acceptance |
| [`agents/domain.md`](agents/domain.md) | Domain-document discipline for agents |
| [`agents/issue-tracker.md`](agents/issue-tracker.md) | GitHub Issues as the task and PRD tracker |
| [`agents/triage-labels.md`](agents/triage-labels.md) | Canonical triage labels |

Tasks and non-blocking follow-up work belong in GitHub Issues for
`Hackerismydream/CodeCairn`; this repository does not maintain a second
file-based issue backlog.
