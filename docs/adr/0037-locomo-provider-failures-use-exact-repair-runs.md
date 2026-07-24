# LoCoMo Provider Failures Use Exact Repair Runs

## Status

Accepted.

## Context

A full LoCoMo run can finish retrieval for every question while an answer or
judge provider temporarily rejects a subset of calls. Re-running all 1,540
questions would pay again for already verified answers and votes. Mutating the
original run or copying checkpoints into a synthetic run would destroy the
artifact boundary and make the reported score difficult to audit.

The V23 full run is already an immutable, checked-in benchmark protocol. A
provider-only repair must not change its corpus, query vectors, retrieval
planner, answer model, judge model, or scoring contracts.

## Decision

CodeCairn supports an `explicit-question-ids-v1` question-set algorithm for
failed-only repair runs. Its loader validates that every ID exists, is unique,
matches the declared category counts, and matches the frozen selection digest.

`compose-locomo-repair` produces a formal score only when:

- both source runs independently pass the ordinary immutable LoCoMo verifier;
- the base run is bound to the target question set;
- the repair selection is exactly the base run's unscored question set;
- the repair scores every selected question without infrastructure failures;
- base successes and repair successes form a disjoint complete partition of
  the target selection;
- dataset, corpus, query vectors, retrieval, generation, judging, and
  checkpoint contracts remain identical.

The source repository commits may differ. Both commits and both manifest
digests are recorded, while the artifact-facing contract must remain equal.
The composite report sums only verified source metrics and is written
exclusively without mutating either source run.

## Consequences

- A transient provider outage can be repaired without repeating successful
  paid calls.
- The original negative artifact remains intact.
- Missing, extra, overlapping, failed, or contract-changing repairs fail
  closed.
- The final score remains attributable to one target protocol and two
  independently verifiable source runs.
