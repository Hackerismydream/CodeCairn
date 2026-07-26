# Ablation Gates Evaluate Natural-Weighted Accuracy

## Status

Accepted. It amends how the ADR 0031 non-regression gate compares variant
accuracy. The gate structure, its thresholds, and the staged promotion chain
are unchanged.

## Context

The 40- and 200-question diagnostics are stratified: equal counts of multi-hop,
temporal, open-domain, and single-hop questions, so each category carries 25%
of a variant's score. The scored LoCoMo standard set frozen by ADR 0035 is not
stratified — 282 multi-hop, 321 temporal, 96 open-domain, and 841 single-hop
questions — so the published aggregate weighs those categories at 18.3%, 20.8%,
6.2%, and 54.6%.

The ablation gate compared variants on stratified accuracy. A variant that
trades open-domain answers for single-hop answers therefore looks worse to the
gate and better on the benchmark the gate exists to protect.

That is not hypothetical. The v5 200-question ablation scored the two arms at
these category accuracies, pinned in
`tests/test_locomo.py::test_natural_weighting_flips_the_frozen_v5_hierarchy_comparison`:

| Variant | Multi-hop | Temporal | Open-domain | Single-hop |
|---|---:|---:|---:|---:|
| `episode-only` | 42% | 72% | 56% | 82% |
| `hierarchy` | 42% | 70% | 46% | 88% |

Stratified, `hierarchy` scores 1.5 points below `episode-only`. Natural
weighted, it scores about 2.2 points above it. The gate rejected that arm on a
number the benchmark does not use.

Weighing the comparison set by its own `category_targets` does not fix this. A
stratified set's own targets *are* the stratified weights, so the report would
carry a natural-weighting label over the same distorted comparison.

## Decision

`compare-locomo` reports both accuracies for every variant and evaluates every
accuracy-delta gate — `hierarchy-no-neighbors.accuracy_delta_vs_episode_points`
and `hierarchy.accuracy_delta_vs_no_neighbors_points` — on the natural-weighted
numbers. Both views stay in the report JSON, so a stratified regression remains
readable.

The natural weights are the module constant
`LOCOMO_NATURAL_CATEGORY_WEIGHTS` in `evaluation/locomo_ablation.py`: 282, 321,
96, and 841. They are the default and need no flag. The report records
`weighting.id` as `natural-v1` and `weighting.source` as
`frozen-locomo10-category-counts-v1` with no question-set identity, and
promotion validation re-checks a frozen-source report against the constant.

`--natural-weight-question-set <path>` replaces the constant with that question
set's `category_targets` and records `weighting.source` as
`question-set-category-targets` together with the set's selection id and
content digest. It exists for a dataset revision whose scored category
distribution differs from this one. It is not a way to restore self-weighting.

A comparison run that does not score every weighted category fails instead of
being silently renormalized over the categories it did measure: accuracy for an
unmeasured category cannot be estimated from the ones that were.

## Consequences

- The ablation gate and the published aggregate measure the same quantity, so a
  promotion decision can no longer be inverted by the diagnostic's own
  stratification.
- Comparison reports written before this record are stratified comparisons
  whatever their weighting label says, and are not comparable with later
  selections.
- Open-domain regressions carry 6.2% of the overall verdict instead of 25%. The
  per-category deltas stay in the report, and the temporal-neighbor promotion
  check still gates on raw temporal and multi-hop category accuracy.
- Changing the assumed distribution now requires either editing a frozen module
  constant or naming a question set in the run command, and either choice is
  recorded in the report.
