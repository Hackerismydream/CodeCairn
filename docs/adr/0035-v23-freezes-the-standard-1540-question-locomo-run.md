# V23 Freezes the Standard 1,540-Question LoCoMo Run

## Status

Accepted.

## Context

The V23 diagnostic protocol freezes 200 questions with balanced categories for
cheap iteration and promotion. The upstream LoCoMo dataset contains 1,540
standard questions across multi-hop, temporal, open-domain, and single-hop
categories, plus 446 adversarial questions that are not part of the standard
aggregate.

A full score must preserve the validated V23 answer, judge, retrieval, worker,
resource, and context contracts without treating an untracked command-line
filter as the benchmark definition.

The diagnostic paid-scoring gate authorizes only its frozen 40- and
200-question selections. It cannot truthfully certify a different 1,540
question selection or a different query-vector artifact.

## Decision

`full-1540-v23.json` freezes every category 1–4 question in the verified
dataset:

- 282 multi-hop questions;
- 321 temporal questions;
- 96 open-domain questions;
- 841 single-hop questions.

The full run inherits the V23 generation, retrieval, context, process-isolation,
and 2 GiB resource contracts. It omits the diagnostic-only paid-scoring gate
instead of weakening that gate or claiming that its 200-question receipt covers
the full vector set.

The run may reuse the immutable verified corpus because corpus integrity,
dataset identity, semantic projection, embedding identity, and retrieval
configuration remain independently checked. It freezes a new query-vector
artifact that exactly covers the 1,540-question selection.

## Consequences

- The full score is reproducible from a checked-in question-set definition.
- Diagnostic promotion and full-dataset reporting remain separate claims.
- The run records the exact corpus, query-vector, model, commit, latency, cost,
  and resource artifacts.
- Category 5 adversarial questions require a separate explicitly named run and
  cannot silently change the standard aggregate.
