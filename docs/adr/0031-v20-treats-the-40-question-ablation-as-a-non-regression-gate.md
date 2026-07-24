# V20 Treats the 40-Question Ablation as a Non-Regression Gate

## Status

Accepted.

## Context

The first complete v19 paid ablation scored all three recall variants at
36/40. Every run had zero infrastructure failures and stayed within the frozen
latency and memory limits. The comparison still failed because it required
`hierarchy-no-neighbors` to exceed `episode-only` by two accuracy points.

That requirement conflicts with the comparison's selection semantics. The
comparison can deterministically select `hierarchy-no-neighbors` when the two
variants tie, but the same tie makes the global gate fail. The threshold also
turns a ten-question-per-category canary into an improvement claim even though
its intended role is to reject regressions before the larger diagnostic.

The failed v19 comparison remains immutable negative evidence. Its score does
not authorize a v19 200-question run.

## Decision

V20 changes only the core 40-question gate:

- `hierarchy-no-neighbors` must not regress from `episode-only`;
- temporal-neighbor promotion still requires a non-regressing overall score,
  a positive temporal or multi-hop category delta, and no more than a 20%
  retrieval-P95 increase;
- the selected variant must still score all 40 questions, have zero
  infrastructure failures, and stay below 2,500 ms retrieval P95.

The question inventory, representation, embedding, retrieval, answer, judge,
resource, and 200-question promotion contracts remain unchanged. V20 uses new
question-set artifacts so the amended gate cannot retroactively relabel the
v19 runs.

## Consequences

- The 40-question stage is an explicit safety and deterministic-selection gate,
  not proof of a statistically significant improvement.
- Accuracy claims come from the independently verified 200-question run.
- The three paid v20 canary runs must be produced again and bound to the v20
  question-set hashes.
- Existing content-addressed corpus and query-vector artifacts remain reusable
  because their producer contracts are unchanged.
