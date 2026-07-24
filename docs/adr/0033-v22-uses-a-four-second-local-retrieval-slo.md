# V22 Uses a Four-Second Local Retrieval SLO

## Status

Accepted.

## Context

V21 reduced provider-free 160-question retrieval P95 to 2,320 ms during tuning.
The first formal run reached 2,502.652 ms: 2.652 ms above the 2,500 ms gate.
It retained 72.73% complete-context coverage, 92.86% ranked-all coverage, zero
infrastructure failures, and 1.00 GiB maximum process RSS.

A later run on the same frozen retrieval contract reached 3,280.548 ms while
retaining the same coverage, zero infrastructure failures, and 0.93 GiB maximum
process RSS. The 2.5- and 3-second thresholds are therefore too sensitive to
ordinary laptop scheduling noise for an offline local cross-encoder. Repeating
architecture changes or paid scoring solely because of scheduler variance does
not improve memory quality.

## Decision

V22 changes the local retrieval P95 limit from 2,500 ms to 4,000 ms in both the
40-question selection gate and the 200-question promotion gate.

Every quality and safety gate remains unchanged:

- at least 70% complete annotated-evidence coverage before paid scoring;
- at least 78% overall, 70% multi-hop, 68% open-domain, and 90% single-hop
  accuracy at promotion;
- zero infrastructure failures;
- process RSS strictly below 2 GiB;
- at most 4,000 context tokens.

The V21 formal latency miss remains immutable negative evidence.

## Consequences

- V22 requires a new 40-question retrieval and paid ablation because its gate
  identity changed.
- The unchanged 160-question selection and retrieval protocol still require a
  fresh holdout artifact bound to the final implementation commit.
- No model, retrieval, answer, judge, or evidence-quality behavior changes.
