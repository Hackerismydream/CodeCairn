---
id: v01-008
scope: evaluation surface, historical verifier, and source budget
status: done
depends-on: [v01-007]
---

# Reduce evaluation to reproducible product and release gates

## Objective

Provide one-command evaluation for users while reducing installable source
below the accepted 10,000-core/15,000-total ceilings and preserving historical
evidence verification.

## Context

The baseline evaluation package is 16,841 lines; `locomo.py` alone is 7,724.
The package has accumulated protocol-specific orchestration. Version 0.1 keeps
current runners and immutable readers, not every experiment generation.

## Paths

Primary:

- `Makefile`
- `scripts/source_budget.py` from `v01-000a`
- `src/codecairn/evaluation/`
- `src/codecairn/evaluation/locomo_worker.py`
- `src/codecairn/entrypoints/cli.py`
- `benchmarks/locomo/`
- `benchmarks/coding/`
- `tests/test_locomo*.py`
- `tests/test_coding_evaluation.py`
- `tests/test_evidence_bundle.py`
- `docs/evaluation/README.md`
- `docs/evidence-bundle.md`

## Guardrails already required

Do not begin unless `v01-000a` already proves:

1. `evidence/benchmark-v3` verifies all 4,411 inventory files;
2. current V24 diagnostic/full manifests parse;
3. base plus exact repair composition remains pure;
4. coding A/B workspaces are isolated;
5. reports perform no provider call or mutation.

Only then remove superseded execution paths.

## Required changes

1. Complete Make targets:
   `eval-smoke`, `eval-scale`, `eval-retrieval`, `eval-locomo-200`,
   `eval-locomo-full`, `eval-coding-ab`, `evidence-verify`, and
   `source-budget`.
2. Make the smoke fully offline and cover the complete lifecycle, MCP, and hook
   fixtures.
3. Collapse LoCoMo execution into:
   - one manifest parser;
   - one generic immutable attempt journal;
   - one runner with diagnostic/full selection;
   - one exact-failure repair/composite path;
   - one pure report reducer.
4. Keep answer and judge providers separate and record their identities.
5. Preserve a small compatibility reader for historical bundle verification;
   do not keep historical execution commands merely because old filenames
   exist.
6. Retain coding A/B isolation and hidden verifier behavior; remove duplicate
   CLI/orchestration.
7. Move obsolete benchmark specifications to documentation/history if useful,
   or delete them if Git history is sufficient. Do not ship them as executable
   product code.
8. Retain and strengthen the early deterministic source gate; do not re-create
   it in evaluation code.
9. Add package-area line totals and the 9,700/14,100 internal targets to the
   task report.
10. Retain CI verification of the current selected `benchmark-v3` evidence
    bundle through the historical-reader boundary.
11. Emit coding-native hook freshness, stale leakage, provenance coverage,
    continuation, selected-memory precision/latency, A/B outcome, and
    memory-induced regression metrics.

## Paid-run safety

LoCoMo targets require explicit:

- immutable run ID;
- clean repository commit;
- protocol path;
- provider configuration;
- spend acknowledgement and ceiling.

Missing credentials or provider failure is recorded as infrastructure failure.
The task does not need to spend money; it must produce the exact command the
maintainer runs. The release task owns the actual full run.

## Verification

```bash
make eval-smoke
make eval-scale
make eval-retrieval
make evidence-verify
make source-budget
make format
make check
uv build
```

Also dry-run or validate without provider calls:

```bash
make eval-locomo-200 HELP=1
make eval-locomo-full HELP=1
make eval-coding-ab HELP=1
```

The implementation may choose an equivalent documented “plan” flag, but every
target must expose resolved inputs, expected outputs, and spend boundary before
execution.

## Exit criteria

- the eight Make targets are the authoritative surface;
- historical v3 verification still passes;
- product lifecycle smoke passes offline;
- core is at most 10,000 and total source at most 15,000 physical Python lines;
- internal release target is at most 9,700 core and 14,100 total;
- no code was moved to evade counting;
- all checks and package build pass.

## Completion evidence

Completed on the v0.1 mainline after `v01-007`:

- all eight Make targets are authoritative;
- `eval-smoke` passed the offline lifecycle/MCP/hook test set;
- `eval-scale` imported 1,000 sessions and 100,000 events twice with 0 duplicate
  Episode or Memory;
- `eval-retrieval` ran 100 queries at 97% Recall@5, 100% provenance coverage,
  zero stale leakage, and approximately 27 ms local P95 under the named
  deterministic evaluation protocol;
- LoCoMo diagnostic/full selection parsing, separate answer/judge providers,
  immutable attempt journals, pure reduction, and exact-failure repair/
  composition are implemented; HELP plans make no provider call;
- historical `benchmark-v3` still verifies all 4,411 files;
- source count is 9,681 core / 3,633 evaluation / 13,314 total, below the
  enforced 9,700 / 14,100 internal targets;
- the speculative HTTP compatibility adapter was retired under ADR 0052;
- `make format`, `make check`, every offline evaluation target, evidence
  verification, and `uv build` passed.

These fixture/deterministic results are v0.1 gates, not the candidate-bound
LoCoMo or Coding A/B release results owned by `v01-010`.
