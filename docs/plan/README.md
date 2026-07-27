# CodeCairn v0.1 Delivery Plan

Status: implementation contracts accepted. The source-budget and historical
verifier guardrail is the only ready implementation task; later tasks become
ready when their dependency merges.

## Baseline

Version 0.1 code work starts from the `main` commit that contains this delivery
plan. Its measured code baseline is Fable's `954f728`, which contains eight
EverOS-alignment commits: public index maintenance, lazy retrieval provider
construction, LoCoMo V24 budget/gate corrections, milestone specifications,
and ADRs 0040–0042.

The baseline was verified with:

```text
make format
make check                         # 661 tests, 82% measured coverage
uv run codecairn evidence verify evidence/benchmark-v3
                                     verified=true, 4,411 files
uv build                            # wheel and sdist
```

These results establish starting reality. They do not prove the version 0.1
target described in the new documents.

## Delivery order

```text
v01-000 Fable baseline (done)
   |
v01-000a source budget + historical verifier guardrails
   |
v01-001 four-type domain + remove write gate
   |
v01-002 capture + pending semantic work
   |
v01-003 evolution + restore
   |
v01-004 active recall + history
   |
v01-005 init + config + operations
   |
v01-006 MCP
   |
v01-007 Claude/Codex hooks
   |
v01-008 evaluation simplification + one-command gates
   |
v01-009 packaging + learner release surface
   |
v01-010 release-candidate E2E and evidence
```

The order is intentionally mostly serial. The same domain, application facade,
bootstrap, CLI, SQLite, and recall files are current hotspots; parallel feature
branches would manufacture conflicts and duplicate compatibility code.

Research, fixture preparation, and independent documentation review may run in
parallel. Implementation merges only after every listed dependency is on
`main`.

## Task state

| ID | Outcome | Depends on | State |
|---|---|---|---|
| [`v01-000`](tasks/v01-000-fable-baseline.md) | Fable baseline on `main` | none | done |
| [`v01-000a`](tasks/v01-000a-guardrails.md) | Early source gate and historical verifier boundary | v01-000 | ready |
| [`v01-001`](tasks/v01-001-domain-slimming.md) | Four-type domain; no standalone verification operation | v01-000a | blocked |
| [`v01-002`](tasks/v01-002-capture-pipeline.md) | Complete episode capture | v01-001 | blocked |
| [`v01-003`](tasks/v01-003-evolution-ledger.md) | Supersession and restore | v01-002 | blocked |
| [`v01-004`](tasks/v01-004-active-recall.md) | Active-only lifecycle-aware recall | v01-003 | planned |
| [`v01-005`](tasks/v01-005-onboarding.md) | Init, config, process, doctor | v01-004 | planned |
| [`v01-006`](tasks/v01-006-mcp.md) | Explicit MCP access | v01-005 | planned |
| [`v01-007`](tasks/v01-007-hooks.md) | Post-session automatic capture | v01-006 | planned |
| [`v01-008`](tasks/v01-008-evaluation-slimming.md) | Small evaluation surface and source gates | v01-007 | planned |
| [`v01-009`](tasks/v01-009-packaging-learning.md) | Installable learner-facing package | v01-008 | planned |
| [`v01-010`](tasks/v01-010-release-e2e.md) | One verified release candidate | v01-009 | planned |

`ready` means an agent may implement the task from current `main`. `blocked`
means a precise contract exists but an implementation dependency has not
merged. `planned` means the task is specified and will be re-read against the
completed dependency before its state changes to ready.

## Agent working contract

For each task:

1. restore current reality from its dependency commit;
2. read `CONTEXT.md`, the task file, and its referenced ADRs;
3. make one coherent implementation; do not add a compatibility framework for
   a pre-release path the task deletes;
4. test public behavior through CLI, MCP, hook, HTTP compatibility, or a
   service interface as directed;
5. update maintained docs when the implementation differs;
6. run the task's verification plus `make format` and `make check`;
7. report source-line deltas and deleted paths;
8. commit only when the task exit criteria are true.

No task may:

- claim a fixture or historical artifact as a new live result;
- publish a benchmark without a clean-commit manifest and raw aggregates;
- add Raven, a watcher, dashboard, cloud tenancy, or dynamic profiles;
- let a model author provenance, roles, command outcomes, file changes, exact
  quotes, or verification state;
- move code merely to evade the source budget.

## Maintained contract order

When documents conflict during implementation:

1. [`../../CONTEXT.md`](../../CONTEXT.md) for domain language;
2. [`../v0.1/schema-contract.md`](../v0.1/schema-contract.md) for exact wire,
   storage, identity, and DTO schema;
3. [`../v0.1/memory-lifecycle.md`](../v0.1/memory-lifecycle.md) for durable
   lifecycle;
4. [`../architecture.md`](../architecture.md) for ownership and flows;
5. [`../PRD.md`](../PRD.md) for release requirements;
6. the current task file for implementation boundaries;
7. historical plans and older ADRs for context only.

## Completion

Version 0.1 is not complete when the code merely compiles. It is complete when
`v01-010` binds the installed product, lifecycle smoke, source budget, full
LoCoMo result, coding A/B artifact, package inventory, and documentation to the
documented clean implementation/evidence SHA pair.
