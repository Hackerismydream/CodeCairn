---
id: v01-000a
scope: early source budget and historical verifier boundary
status: done
depends-on: [v01-000]
---

# Install source and historical-evidence guardrails

## Objective

Make code-size drift and historical verifier coupling fail before the
four-type domain replacement starts. This task changes no product semantics.

## Context

The accepted baseline has 17,250 core lines, 16,841 evaluation lines, and
34,091 total package lines. Domain replacement deletes symbols imported
directly by historical evaluation modules. Waiting until `v01-008` to discover
those dependencies would force `v01-001` to redesign evaluation while changing
the durable schema.

## Paths

- add `scripts/source_budget.py`
- `Makefile`
- CI workflows
- `src/codecairn/evaluation/`
- `src/codecairn/locomo_worker.py`
- focused tests for source counting and historical reader boundaries
- `docs/v0.1/evaluation-and-release.md`

## Required changes

1. Implement one deterministic physical-line counter using the exact
   classification in the evaluation contract.
2. Add `make source-budget` and CI enforcement. It prints full commit, included
   paths, per-area totals, hard ceilings, internal targets, and pass/fail.
3. Record the accepted baseline and stage ceilings:
   `v01-001 <=15,500 core`, `v01-004 <=11,500 core`,
   `v01-007 <=10,000 core`, and `v01-008 <=10,000/15,000`.
4. Move `locomo_worker.py` under `codecairn/evaluation/` or otherwise complete
   the documented single classification without compatibility duplication.
5. Characterize every live Python import needed by
   `evidence/benchmark-v3` verification and current V24 manifest parsing.
6. Put historical bundle parsing behind a small stable evaluation-owned reader
   DTO. The reader must not import the six-type product domain, recall planner,
   or runtime write orchestration.
7. Prove base-plus-exact-repair composition is pure and reports perform no
   provider call or filesystem mutation.
8. Do not slim the full evaluation implementation yet; `v01-008` owns that
   deletion after product behavior is stable.

## Verification

```bash
make source-budget
uv run codecairn evidence verify evidence/benchmark-v3
uv run pytest -k "evidence_bundle or locomo or source_budget"
make format
make check
uv build
```

The historical verifier must report `verified=true` and
`verified_file_count=4411`. The source report must reproduce 17,250 core,
16,841 evaluation, and 34,091 total at the recorded baseline; any task changes
are reported separately from that immutable baseline.

## Exit criteria

- source ceilings fail locally and in CI before `v01-001`;
- historical verification depends only on the characterized reader boundary;
- no product-memory compatibility alias or dual write is added;
- all checks pass and the task is merged to `main`.

## Completion evidence

- `make source-budget`: pass at 16,783 core / 17,502 evaluation / 34,285
  total against the 17,250 / 34,300 transition ceilings;
- accepted `954f728` baseline reproduced as 17,250 core / 16,841 evaluation /
  34,091 total;
- benchmark-v3 verification recomputes 4,411 files through
  `evaluation.historical_reader` without importing product runtime modules;
- `make check`: 668 tests pass with all architecture, lint, format, type, and
  source gates;
- `uv build`: wheel and sdist pass.
