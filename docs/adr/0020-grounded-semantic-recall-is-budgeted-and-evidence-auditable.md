# Grounded Semantic Recall Is Budgeted and Evidence-Auditable

## Status

Accepted for implementation. Benchmark quality remains unverified until a new
v7 corpus and v13 diagnostic artifact pass the staged evidence and scoring gates.

## Context

The first 200-question hierarchical LoCoMo diagnostic scored 139/200. Failure
analysis separated the 61 errors into 13 questions whose gold Episode never
reached the top 20, 22 whose gold Episode was ranked but omitted from answer
context, 25 whose complete gold evidence was present but still produced a wrong
answer, and one question without usable gold evidence.

ADR 0018 added attributed Episode and Atomic Fact projections, while ADR 0019
changed final context from all-or-nothing parent hydration to facts-first
packing. Those decisions did not establish a real clause-level semantic
projection, did not execute typed query requirements as selection constraints,
and did not bound every expansion lane. They also lacked a provider-free way to
separate retrieval and packing quality from answer and judge quality.

Blindly increasing the context window or adding a query-time model call would
raise cost without identifying which layer improved. Loading every owner
document, expanding entire clusters, or admitting an unbounded neighbor scan is
also incompatible with the 2 GiB worker limit.

## Decision

### Semantic projection

`MemoryRuntime.write_episode()` remains the only public attributed-Episode write
contract. Exact source turns remain authoritative `EvidenceFact` records.

An injected `ClauseProjectionAdapter` may return only clause text and existing
source fact IDs. The host runtime owns Episode and clause identities, canonical
ordering, source digests, cache keys, grounding validation, and persistence. A
projection is accepted only when all clauses are non-empty, every cited source
fact exists, and duplicate clauses or references are absent. Pure filler may
produce no Atomic Fact; meaningful clauses need not cover unrelated source
turns. `SemanticEpisode.source_fact_ids` still inventories the complete source,
and the parent Episode retains every exact turn. Invalid output rejects the
write before durable state changes.

The deterministic lossless adapter remains the offline correctness baseline. A
structured provider adapter is explicitly selected through the semanticizer
profile, uses bounded request and response sizes, and records its effective
provider/model identity and usage. Projection cache identity includes every
output-affecting adapter and limit setting. Corpus resume rejects projection
contract drift instead of mixing old and new semantic records.

### Typed, bounded recall

Recall compiles a deterministic typed `QuerySketch` containing entity, temporal,
relation, list, and provenance requirements. It performs no query-time LLM call.
The requirements are executable selection constraints rather than trace-only
metadata.

Episode and Atomic Fact lanes use bounded candidate sets and fact-level rerank.
Expansion is one hop and may add at most 24 facts in total, partitioned across
entity, time, and provenance lanes. Database reads are bounded as well as final
outputs. Expansion counts, limits, covered requirements, and missing
requirements are included in the recall sidecar.

Attributed sessions that may be expanded as temporal neighbors persist an
explicit adjacency group and non-negative sequence index. Neighbor lookup is
centered on that sequence index and bounded by both the planned window and the
remaining fact budget; it never infers cross-session adjacency from an Episode
identifier.

### Facts-first context compilation

The context compiler first allocates the highest-ranked grounded fact from each
necessary parent, then allocates additional facts in coverage-aware rounds.
Temporal metadata and adjacent source facts are added only inside their explicit
budgets. Complete parent hydration is an optional final allocation for procedure
queries; a parent that does not fit cannot erase already selected facts.

The v4 renderer exposes at most 4,000 deterministic upper-bound tokens. After
the one-evidence-per-parent admission floor, remaining matched facts are packed
globally before sibling or neighbor context, so a long temporal window cannot
evict a second required entity fact. Generated
parent summaries are not answer evidence. The trace records rendered and omitted
fact IDs, parent IDs, token count, tokenizer contract, and exclusion reasons.
Answer citations are valid only when they point to rendered source facts.

### Grounded answers and staged evaluation

Scored answers use a structured contract containing the answer, rendered
supporting evidence IDs, and an insufficiency flag. Report verification rejects
unknown citations and malformed structured output.

Before answer or judge calls, a retrieval-only run produces immutable question
artifacts. A provider-free evidence report maps LoCoMo gold dialog IDs to source
fact IDs and separately measures ranked-parent coverage, selected candidate
snippet coverage, rendered-context coverage, and whether an oracle context fits
the same 4,000-token compiler. The oracle path may read only explicit evidence
dialog IDs and source turns; it may not read the question category, gold answer,
or recall output.

Paid evaluation follows this order:

1. build and verify one content-addressed v7 corpus;
2. freeze query vectors for the diagnostic selection;
3. run retrieval-only and require the evidence-coverage gate;
4. score a small fixed stratified slice whose questions are a subset of the
   frozen diagnostic;
5. score the frozen 200-question diagnostic only after the slice improves;
6. run all 1,540 scored questions only after the diagnostic gate passes.

The 40-question comparison freezes the selected recall mode together with its
commit, corpus, query-vector artifact, answer model, and judge model. The
200-question stage runs that contract once; it does not repeat three paid arms.
Its promotion verifier checks the absolute category, latency, infrastructure,
and RSS gates from the question-set definition. Single-hop regression is
measured against a content-hashed historical run over the same 200-question
selection, not against the smaller 40-question slice.

Every LoCoMo conversation runs in a fresh process. The v13 protocol freezes the
2 GiB RSS limit, one retrieval thread, local reranker batching, provider roles,
context contract, and every expansion bound. Provider failures, incomplete
question inventories, mismatched start/completion receipts, or configuration
drift make an artifact non-publishable.

Answer and judge calls use checkpoint policy
`journal-replay-or-unknown-spend-fail-closed-v3`. Each application attempt has
an fsynced start record and each observed provider attempt has an fsynced
outcome. Only failures known to occur before request acceptance may retry.
Read, write, and remote-protocol failures remain start-only unknown spend and
must not be retried automatically. A successful HTTP response that cannot be
converted into a usage-bearing model response is also unknown spend rather
than a zero-cost provider failure. For priced models, input, cache-hit,
cache-miss, output, and currency-specific cost must be complete and
mathematically consistent in both the Adapter and the final report. One invalid
judge vote ends the question;
later votes cannot change its infrastructure-failed status. Resume rejects a
corrupt attempt journal or a journal that cannot be bound to an immutable
worker receipt and question checkpoint. Reporting independently reconstructs
the expected journal entries from answer and judge receipts and rejects extra,
missing, or unbound unknown-spend entries.

Remote query embeddings are frozen in provider-sized batches. Each batch has
an immutable pre-call attempt receipt and a post-call checkpoint containing the
vectors, HTTP-attempt count, reported input tokens, and configured CNY cost. A
start-only batch is treated as unknown spend and cannot be resumed by silently
calling the provider again. Read, write, and protocol transport failures stop
the batch immediately; only failures known to precede request acceptance may
retry inside the Adapter.

Paid document embedding during corpus ingestion follows the same accounting
boundary. Every conversation attempt records the pre-call embedding counters;
its checkpoint or failure receipt records the delta, including unobserved
provider attempts. Published corpus usage and CNY cost are recomputed only from
those checkpoints. Missing pricing, missing usage, start-only attempts, and
unknown spend fail closed. Corpus and query-vector builders also serialize on
the complete build-contract digest and rescan published artifacts while holding
that lock. A valid exact-contract artifact is returned idempotently; an invalid
or incomplete exact-contract artifact blocks another paid build.

## Consequences

- Semantic model output improves retrieval representation without becoming
  evidence or a second source of truth.
- One paid semantic build is reusable across retrieval experiments; query-time
  recall remains deterministic and provider-free once embeddings are frozen.
- Retrieval, context packing, and answer synthesis can be measured independently,
  so benchmark gains can be attributed to an architectural layer.
- List and multi-hop queries can reserve evidence across parents while remaining
  within a smaller answer context than the previous 23K-character packing policy.
- Strict grounding can lower answer coverage when the renderer omits necessary
  source facts; that is an observable packing failure rather than an uncited model
  guess.
- v5/v6 corpora, v12 query checkpoints, and previous score reports are historical
  evidence only and cannot resume under v7/v13 contracts.
- This decision extends ADR 0018's source-truth boundary and supersedes ADR 0019's
  v12 evaluation protocol and renderer revision. It does not claim a benchmark
  improvement until immutable artifacts pass the declared gates.
