---
id: v01-000
scope: repository baseline
status: done
depends-on: []
---

# Land Fable's EverOS-alignment baseline

## Objective

Make Fable's completed P0/B0 work the single implementation base for version
0.1 without preserving a competing branch history.

## Included commits

```text
bf73872 docs(plans): add EverOS alignment roadmap and milestone specs
b7d1bd4 feat(bootstrap): resolve retrieval providers lazily with typed errors
0eddfee feat(index): surface index maintenance on CLI/HTTP and drain on import
1a04a2d feat(locomo): freeze v24 budgets and gate ablation on natural weights
475917c test(locomo): pin the retrieval gate ceiling
27ae061 docs(plans): add onboarding P1 and agent-integration P2 specs
4ddea8c fix(locomo): weigh ablation gates by frozen category counts
954f728 docs(adr): record index surface/v24/natural weighting
```

## Result

The feature branch was a direct descendant of `main`; `main` fast-forwarded to
`954f728` with no conflict or merge-only commit.

## Verification

```bash
make format
make check
uv run codecairn evidence verify evidence/benchmark-v3
uv build
git rev-parse main
git rev-parse feat/everos-alignment-m4
```

Observed:

- 661 tests passed;
- combined measured coverage rounded to 82%;
- four import-linter contracts passed;
- evidence bundle verified 4,411 files;
- wheel and sdist built;
- both branch tips were `954f728`.

## Exit criteria

Complete. Later tasks use this commit or a descendant as their base.
