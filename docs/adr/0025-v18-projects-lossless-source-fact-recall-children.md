# V18 Projects Lossless Source-Fact Recall Children

## Status

Accepted for implementation. V18 retrieval quality remains unverified until a
new immutable 40-question preflight and the non-overlapping 160-question
holdout pass. Paid answer and judge calls remain blocked.

## Context

The retained local v17 retrieval artifacts established that the repeated
canary completed without infrastructure or paid-model calls, while the
non-overlapping holdout failed both the frozen context-coverage and latency
gates. Exact v17 measurements are intentionally not published here because the
corresponding raw artifacts are not checked into this branch.

Failure attribution at the earliest missed boundary found all three retrieval
stages represented:

| Earliest failed boundary | Primary cause |
|---|---|
| Ranked parent | complementary parents fell below the bounded parent set |
| Selected fact candidate | authoritative facts fell outside the per-parent set |
| Final context | low-scoring sibling facts lost the saturated context budget |

Packing-only replays and a larger-context counterfactual still left incomplete
contexts. Increasing top-k, context size, or neighbor fan-out would therefore
add latency without fixing the representation failure.

The structured corpus contained authoritative source facts that were not
represented completely by semantic Atomic Facts. The previous recall
projection emitted semantic children whenever a Semantic Episode existed and
could omit source terms from the child index even when a semantic child cited
the source. The durable Markdown still contained those facts, but citation
alone did not guarantee a lossless retrieval representation. This copied the
parent-child shape of hierarchical memory without preserving its evidence.

## Decision

### Project semantic and authoritative source children

`project_recall_documents` emits:

1. every grounded semantic Atomic Fact in canonical semantic order; and
2. one deterministic raw child for every authoritative source fact, in source
   order.

The two sets intentionally overlap. A grounded semantic child is a derived
retrieval annotation; its `source_fact_ids` establish provenance, not complete
lexical or relational coverage of every cited source.

Raw children retain the authoritative target source `fact_id`. A preceding
other-speaker question from the same conversation Episode may appear only as
retrieval context. That context is bounded to 1,024 characters and explicitly
labelled retrieval-only; the target evidence remains complete. Markdown stays
the single source of truth, and no durable schema changes.

The parent Episode records the actual emitted child count. Existing semantic
and raw child stable-ID namespaces remain unchanged, so their document
identities cannot collide. Downstream selection continues to deduplicate
evidence by authoritative source identity while the child index remains
lossless.

The LoCoMo corpus projection contract advances from
`locomo-grounded-clause-projection-v7` to
`locomo-grounded-clause-projection-v8`. Existing v7 corpora fail closed and
require an explicit full rebuild because memory digests do not reveal changed
disposable child documents.

### Bind dialogue questions during fact reranking

The bounded fact selector includes the immediately preceding other-speaker
question in the reranker document even when the answer is long and
self-contained. A non-question preceding turn still follows the existing
short/anaphoric rule. The selector identity advances to
`bounded-dialogue-aware-cross-encoder-v5`; the 256 global, 24 per-parent, and
12 selected-per-parent ceilings do not change.

### Parse complete calendar dates

The deterministic query sketch accepts both day-month-year and
month-day-year forms, validates calendar dates, and emits an ISO day prefix.
Month-year behavior remains unchanged. The sketch identity advances to
`codecairn/deterministic-query-sketch-v4`, and the temporal lane advances to
`explicit-calendar-prefix-v2`. Query-time LLM calls remain zero.

### Preserve the v8 context renderer

V18 keeps `exact-source-coverage-aware-facts-first-v8`. A frozen replay showed
that flat packing can provide only a small improvement, while replacing the
renderer without versioned replay would weaken historical v8 artifact
validation. Renderer compaction is deferred until v8 and its successor can
both be replayed under their original byte-cost contracts.

### Freeze a new evaluation protocol

The v18 40-, 160-, and 200-question definitions retain the exact v17 question
selections, provider identities, resource limits, and context budget. Only the
query sketch, temporal lane, and fact selector identities change. The
200-question promotion contract is rebound to the v18 preflight and protocol
digests. It pins both the exact 40-question canary definition and the exact
160-question holdout definition.

Every non-retrieval v18 run carries a self-hashed preflight receipt. The receipt
binds the dataset, repository commit, frozen 200-question gate target, scored
question-set subset, protocol, corpus, and query-vector artifacts. The parent
process validates it before constructing answer or judge providers, and every
isolated worker validates the same receipt before constructing any provider.
The receipt has an exact schema and carries both verified source summaries;
omitting thresholds or either source invalidates it even after rehashing. Final
promotion revalidates the receipt against the scored run and the two frozen
source definitions, then records its digest in the promotion report. The
canary and holdout must be disjoint and their exact union must equal the frozen
200-question target.

## Validation boundary

Before any paid answer or judge call:

1. all unit, integration, lint, type, and architecture checks must pass;
2. a v8 structured corpus must rebuild with 100% memory and document parity;
3. the v18 40-question retrieval-only preflight must reach at least 85%
   complete-context coverage, remain at or below 4,000 tokens, complete with
   zero infrastructure failures, keep P95 at or below 2,500 ms, and keep every
   process below 2 GiB;
4. the non-overlapping v18 160-question holdout must pass the same gates.

The 40-question result is a canary, not a promotion result. A failed holdout is
retained as negative evidence and blocks paid scoring.

## Consequences

- Semantic compression can no longer make authoritative source evidence
  unreachable at the child index.
- New raw source documents increase one-time embedding work, but do not add
  query-time model calls or widen the fixed retrieval fan-out.
- Exact question context improves answer-fact ranking without changing source
  authority.
- Existing v7 corpora cannot be silently reused.
- V17 artifacts remain immutable historical evidence.
- The change may still fail the 85% holdout gate; only a new immutable run may
  establish improvement.
