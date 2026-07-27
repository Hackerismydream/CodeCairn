# CodeCairn v0.1 Delivery Plan

Status: ready for implementation. Product and architecture decisions are
accepted; each implementation unit is specified under [`tasks/`](tasks/).

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
| [`v01-001`](tasks/v01-001-domain-slimming.md) | Four-type domain and optional verification | v01-000 | ready |
| [`v01-002`](tasks/v01-002-capture-pipeline.md) | Complete episode capture | v01-001 | ready |
| [`v01-003`](tasks/v01-003-evolution-ledger.md) | Supersession and restore | v01-002 | ready |
| [`v01-004`](tasks/v01-004-active-recall.md) | Active-only lifecycle-aware recall | v01-003 | ready |
| [`v01-005`](tasks/v01-005-onboarding.md) | Init, config, process, doctor | v01-004 | ready |
| [`v01-006`](tasks/v01-006-mcp.md) | Explicit MCP access | v01-005 | ready |
| [`v01-007`](tasks/v01-007-hooks.md) | Post-session automatic capture | v01-006 | ready |
| [`v01-008`](tasks/v01-008-evaluation-slimming.md) | Small evaluation surface and source gates | v01-007 | ready |
| [`v01-009`](tasks/v01-009-packaging-learning.md) | Installable learner-facing package | v01-008 | ready |
| [`v01-010`](tasks/v01-010-release-e2e.md) | One verified release candidate | v01-009 | ready |

`ready` means the specification is ready. An agent starts a task only after
every `depends-on` task is completed on its base branch.

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
2. [`../v0.1/memory-lifecycle.md`](../v0.1/memory-lifecycle.md) for durable
   lifecycle;
3. [`../architecture.md`](../architecture.md) for ownership and flows;
4. [`../PRD.md`](../PRD.md) for release requirements;
5. the current task file for implementation boundaries;
6. historical plans and older ADRs for context only.

## Completion

Version 0.1 is not complete when the code merely compiles. It is complete when
`v01-010` binds the installed product, lifecycle smoke, source budget, full
LoCoMo result, coding A/B artifact, package inventory, and documentation to one
clean commit.
