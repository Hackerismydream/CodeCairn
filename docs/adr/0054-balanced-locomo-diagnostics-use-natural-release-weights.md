---
status: accepted
---

# Balanced LoCoMo Diagnostics Use Natural Release Weights

## Context

The version 0.1 diagnostic selects 50 questions from each LoCoMo category so
each failure mode is visible during iteration. The 1,540-question release set
is not balanced: it contains 282 multi-hop, 321 temporal, 96 open-domain, and
841 single-hop questions.

Applying the release threshold directly to the diagnostic's artificial 25%
category mix would measure a different target. ADR 0042 already established
natural category weighting for promotion decisions made from stratified
LoCoMo samples.

## Decision

The version 0.1 protocol records the four natural category counts and computes
`natural_weighted_accuracy` from the diagnostic's four observed category
accuracies. Diagnostic promotion requires:

- natural-weighted accuracy of at least 82.00%;
- zero infrastructure failures; and
- retrieval P95 at most 8.0 seconds under the two-worker LoCoMo pressure run.

This pressure ceiling belongs only to the paid long-conversation diagnostic.
The versioned 100-query product retrieval suite retains its 4.0-second P95
release gate.

The report retains raw balanced accuracy and every category result. The
weighted value is explicitly a promotion estimate; only a complete
1,540-question run can satisfy the release score gate.

## Consequences

- A balanced diagnostic remains sensitive to weak categories without
  pretending its raw average is the release distribution.
- The promotion calculation is frozen in the protocol and recomputed from raw
  question outcomes.
- Passing the diagnostic authorizes the full run but is not a publishable
  LoCoMo release score.
