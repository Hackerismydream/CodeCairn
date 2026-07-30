# CodeCairn Documentation

This index distinguishes maintained target design, current implementation
truth, historical decisions, and generated evidence.

## Read first

1. [`../CONTEXT.md`](../CONTEXT.md) — canonical maintained domain language.
2. [`PRD.md`](PRD.md) — accepted product requirements and release outcome.
3. [`architecture.md`](architecture.md) — current ownership and flows.
4. [`v0.1/walkthrough.md`](v0.1/walkthrough.md) — one concrete trace through
   capture, evolution, and recall.
5. [`v0.2/README.md`](v0.2/README.md) — implemented Pico Memory Backend,
   first joint result, and exact-pair rerun boundary.
6. [`roadmap.md`](roadmap.md) — product sequence from v0.1 through v2.0.
7. [`plan/README.md`](plan/README.md) — delivery state and agent-ready work.
8. [`runtime/operations.md`](runtime/operations.md) — public behavior that
   exists on current `main`.
9. [`runtime/installation.md`](runtime/installation.md) — persistent install
   and one-client acceptance path.

## Authority

| Question | Source |
|---|---|
| What does a term mean? | [`../CONTEXT.md`](../CONTEXT.md) |
| What product are we building? | [`PRD.md`](PRD.md) |
| Which component owns a target behavior? | [`architecture.md`](architecture.md) |
| What is the exact durable/operational schema? | [`v0.1/schema-contract.md`](v0.1/schema-contract.md), including its marked post-v0.1 enum amendments |
| What is the lifecycle policy? | [`v0.1/memory-lifecycle.md`](v0.1/memory-lifecycle.md) |
| What commands work on current `main`? | [`runtime/operations.md`](runtime/operations.md) |
| What is the accepted Pico integration target? | [`v0.2/README.md`](v0.2/README.md) and [`adr/0057-pico-uses-codecairn-as-its-long-term-memory-backend.md`](adr/0057-pico-uses-codecairn-as-its-long-term-memory-backend.md) |
| Where is the product going next? | [`roadmap.md`](roadmap.md) |
| What should an implementation agent do next? | [`plan/README.md`](plan/README.md) and [`plan/tasks/`](plan/tasks/) |
| Why did a design change? | [`adr/README.md`](adr/README.md) |
| What does current public evidence prove? | [`evaluation/README.md`](evaluation/README.md) and [`evidence-bundle.md`](evidence-bundle.md) |

When target and current documents differ, that is an implementation delta, not
permission to advertise target behavior as shipped.

## Version 0.1 design

| Document | Purpose |
|---|---|
| [`v0.1/README.md`](v0.1/README.md) | Scope and product boundary |
| [`v0.1/schema-contract.md`](v0.1/schema-contract.md) | Exact records, bounds, IDs, storage and DTO mapping |
| [`v0.1/memory-lifecycle.md`](v0.1/memory-lifecycle.md) | Four records, capture cardinality, evolution, storage, migration |
| [`v0.1/agent-integration.md`](v0.1/agent-integration.md) | CLI, seven MCP tools, resource, Claude/Codex hooks |
| [`v0.1/onboarding-and-operations.md`](v0.1/onboarding-and-operations.md) | Init, config, providers, queues, doctor |
| [`v0.1/evaluation-and-release.md`](v0.1/evaluation-and-release.md) | One-command evaluation, source budget, release gates |
| [`v0.1/candidate-evaluation-f2358a7.md`](v0.1/candidate-evaluation-f2358a7.md) | Current passing candidate metrics, evidence boundaries, resume-safe wording |
| [`v0.1/candidate-evaluation-646449b.md`](v0.1/candidate-evaluation-646449b.md) | Historical failed-candidate boundary |
| [`v0.1/walkthrough.md`](v0.1/walkthrough.md) | Trace-to-recall narrative |
| [`v0.1/learning-path.md`](v0.1/learning-path.md) | Outside-in code-reading path |
| [`v0.1/review-brief.md`](v0.1/review-brief.md) | External architecture review prompt |

## Implementation plan

| Document | Purpose |
|---|---|
| [`plan/README.md`](plan/README.md) | Baseline, dependency graph, task status |
| [`plan/analysis/v0.1-delivery.md`](plan/analysis/v0.1-delivery.md) | Hotspots, deletion strategy, risk and source envelope |
| [`plan/backlog.md`](plan/backlog.md) | Compact task index |
| [`plan/tasks/`](plan/tasks/) | Versioned, independently verifiable task specifications |

These files are the accepted local delivery plan. GitHub Issues remain the
external tracker when tasks are published or assigned.

## Version 0.2 implementation and result

| Document | Purpose |
|---|---|
| [`v0.2/README.md`](v0.2/README.md) | Canonical Pico Source Journal, Memory Adapter, ownership, failure, and evidence contract |
| [`adr/0057-pico-uses-codecairn-as-its-long-term-memory-backend.md`](adr/0057-pico-uses-codecairn-as-its-long-term-memory-backend.md) | Decision to replace Pico's long-term Memory Backend directly |
| [`adr/0059-pico-adapter-targets-one-frozen-host-contract.md`](adr/0059-pico-adapter-targets-one-frozen-host-contract.md) | Frozen Pico plugin and MemoryBackend compatibility identity |
| [`adr/0060-recall-may-abstain.md`](adr/0060-recall-may-abstain.md) | Relevance admission and explicit no-memory result |
| [`plan/tasks/v02-001-pico-trace-import.md`](plan/tasks/v02-001-pico-trace-import.md) | Pico source journal and evidence-preserving importer |
| [`plan/tasks/v02-002-pico-memory-adapter.md`](plan/tasks/v02-002-pico-memory-adapter.md) | Installed Pico plugin and MemoryBackend mapping |
| [`plan/tasks/v02-003-pico-integration-evidence.md`](plan/tasks/v02-003-pico-integration-evidence.md) | Joint installed continuity, isolation, and paired evidence |
| [`plan/pico-memory-adapter-implementation-goal.md`](plan/pico-memory-adapter-implementation-goal.md) | Codex Goal that executes `v02-001` and `v02-002` serially and produces the Pico handoff |

The CodeCairn-owned `v02-001` and `v02-002` deliveries, Pico's default switch,
and EverOS product-coupling removal are implemented. The first joint campaign
completed with a positive-claim-ineligible hard-negative result. ADR 0060 is
implemented; the current exact pair still needs a joint rerun.

## Runtime and operations

| Document | Purpose |
|---|---|
| [`runtime/README.md`](runtime/README.md) | Implemented baseline versus target runtime |
| [`runtime/operations.md`](runtime/operations.md) | Exact current CLI/MCP/hook commands and failure posture |
| [`runtime/installation.md`](runtime/installation.md) | Five-minute manual and ten-minute one-client path |
| [`runtime/agent-instructions.md`](runtime/agent-instructions.md) | Reviewable AGENTS.md and CLAUDE.md snippets |
| [`release-readiness.md`](release-readiness.md) | Current blockers and final release matrix |
| [`reference-boundaries.md`](reference-boundaries.md) | Clean-room and external-reference boundaries |

## Evaluation and evidence

| Document | Purpose |
|---|---|
| [`evaluation/README.md`](evaluation/README.md) | Current suites, artifact truth, known limitations |
| [`v0.1/evaluation-and-release.md`](v0.1/evaluation-and-release.md) | Target release commands and thresholds |
| [`v0.1/candidate-evaluation-f2358a7.md`](v0.1/candidate-evaluation-f2358a7.md) | Current candidate-bound positive and negative results |
| [`v0.1/candidate-evaluation-646449b.md`](v0.1/candidate-evaluation-646449b.md) | Historical failed-candidate results |
| [`evidence-bundle.md`](evidence-bundle.md) | Reducer and offline-verifier contract |
| [`../evidence/v0.1-rc1/metrics.json`](../evidence/v0.1-rc1/metrics.json) | Current generated version 0.1 release evidence |
| [`../evidence/benchmark-v3/README.md`](../evidence/benchmark-v3/README.md) | Retained historical evidence |
| [`../benchmarks/locomo/README.md`](../benchmarks/locomo/README.md) | Current LoCoMo execution protocol |
| [`../benchmarks/coding/README.md`](../benchmarks/coding/README.md) | Coding A/B task and verifier contract |
| [`../benchmarks/retrieval/README.md`](../benchmarks/retrieval/README.md) | Historical retrieval suite |

`recall-v2-design.md` is a historical proposal, not the version 0.1 recall
contract.

## ADR reading ranges

| Range | Area |
|---|---|
| [`0001`](adr/0001-new-public-repository-with-selective-reimplementation.md)–[`0011`](adr/0011-import-resume-replays-only-the-active-suffix.md) | Foundation, evidence, storage, entrypoints, resume |
| [`0012`](adr/0012-hierarchical-recall-is-a-rebuildable-projection.md)–[`0027`](adr/0027-semantic-projection-rejects-foreign-citations-per-clause.md) | Retrieval and grounded projection |
| [`0028`](adr/0028-embedding-transport-policy-is-artifact-identity.md)–[`0042`](adr/0042-ablation-gates-evaluate-natural-weighted-accuracy.md) | Evaluation protocols, exact repair, Fable baseline |
| [`0043`](adr/0043-memory-capture-does-not-require-verification.md)–[`0056`](adr/0056-locomo-pressure-latency-is-not-product-latency.md) | Version 0.1 product, lifecycle, surfaces, source budget, and measurement limits |
| [`0057`](adr/0057-pico-uses-codecairn-as-its-long-term-memory-backend.md)–[`0059`](adr/0059-pico-adapter-targets-one-frozen-host-contract.md) | Post-v0.1 Pico Memory Backend integration, additive source budget, and frozen host contract |
| [`0060`](adr/0060-recall-may-abstain.md) | Recall relevance admission and explicit abstention |

Use [`adr/README.md`](adr/README.md) for status and supersession rules.
