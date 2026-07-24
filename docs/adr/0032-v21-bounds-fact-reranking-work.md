# V21 Bounds Fact Reranking Work

## Status

Accepted.

## Context

Two complete v20 160-question retrieval runs preserved evidence quality and
stayed below the 2 GiB process limit, but retrieval P95 reached 3,172 ms and
2,782 ms. The first run coincided with local Spotlight contention; the second
did not. Both exceeded the frozen 2,500 ms gate, so paid scoring remained
blocked.

The retrieval trace showed that parent candidate counts were already modest.
The expensive stage was the second cross-encoder pass over as many as 256 fact
candidates, with up to 24 candidates from one parent and 2,048 characters per
document.

Provider-free ablations measured:

- batch size 16: 40-question P95 2,495 ms, but RSS rose to 1.46 GiB;
- 192 facts and 20 facts per parent: 40-question P95 2,060 ms and 160-question
  P95 2,612 ms;
- the same bounds with 1,024-character fact documents: 160-question P95
  2,320 ms, maximum RSS 1.05 GiB, 72.73% complete context coverage, and 92.86%
  ranked-all coverage.

All tuning runs made zero answer, judge, or embedding calls.

## Decision

V21 freezes the fact-level reranker at:

- 192 candidates globally;
- 20 candidates per parent;
- 12 selected facts per parent;
- 1,024 characters per rerank document;
- batch size 8 and two inference threads.

These bounds are explicit composition-root settings. The library defaults stay
at 256, 24, and 2,048 so historical protocol fixtures remain reproducible.

The smaller document bound preserves the existing head-and-tail truncation, so
both semantic projection and exact source evidence remain represented. V21
keeps every other representation, retrieval, answer, judge, resource, ablation,
and promotion gate unchanged.

## Consequences

- V21 must produce new 40- and 160-question retrieval artifacts because the
  retrieval protocol changed.
- V20 latency failures remain immutable negative evidence.
- The content-addressed semantic corpus and query-vector artifacts remain
  reusable because their producer contracts are unchanged.
- Paid scoring remains blocked unless both V21 retrieval gates pass.
