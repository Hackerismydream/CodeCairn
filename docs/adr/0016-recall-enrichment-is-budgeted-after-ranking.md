# Recall Enrichment Is Budgeted After Ranking

## Context

The first 200-question hierarchical diagnostic exposed a multiplicative cost
shape in the retrieval implementation. With `top_k=20`, the soft router asked
the primary hierarchy level for up to `top_k * 8` candidates and the secondary
level for up to `top_k * 4`. Lexical and vector recall ran at both levels, then
same-Episode neighbors were attached to every fused parent before CrossEncoder
reranking. Work that could not affect the final top-k therefore consumed
reranker time, native memory, and Recall Context space.

The diagnostic also showed why a hard fact-or-episode route is unsafe: the
hierarchy helped some single-hop questions while regressing other categories.
The secondary level must remain available, but its work must be bounded.

EverOS was consulted as a mechanism-level reference. Its hierarchical episode
path uses a small recall multiplier, independently recalls parents and atomic
facts, lifts child scores to parents, merges the parent rankings, truncates to
top-k, and only then fetches facts for the retained parents. CodeCairn keeps its
own synchronous interface, RRF contract, deterministic router, evidence model,
and attributed neighbor semantics.

## Decision

`RecallPlanner` owns one manifest-recorded budget policy:

- the primary route receives `max(40, top_k * 2)` candidates;
- the secondary route receives `max(20, top_k)` candidates;
- both lexical and vector routes remain enabled at both hierarchy levels;
- AtomicFact candidates are max-pooled by durable parent before four-way RRF.

Recall enrichment executes in this order:

1. recall Episode and AtomicFact candidates under the route budgets;
2. lift fact matches and fuse parent rankings;
3. attach matched facts and bounded sibling facts to each parent;
4. CrossEncoder-rerank parents without temporal neighbors;
5. retain top-k parents;
6. expand same-Episode chronological neighbors for retained parents only;
7. stop expansion after one global 20-snippet budget and render attributed
   Recall Context.

The budget is global to one recall, not per parent. It is consumed in final
rank order, so lower-ranked parents cannot crowd out context for higher-ranked
parents. Every neighbor still carries its source memory, source URI, fact ID,
and relation. Expansion never crosses repository or source-Episode scope.

LoCoMo evaluation additionally supports `ingest` and `questions` execution
phases. Evidence runs execute those phases in separate processes using the same
immutable manifest and missing-only checkpoints. This releases Arrow, LanceDB,
embedding, and indexing state before the question phase instead of relying on
garbage collection to return native memory. The `all` phase remains a
convenience path for small tests and smoke runs.

## Consequences

- The router remains soft and replayable; a classification error cannot remove
  an entire hierarchy level.
- The 200-question protocol reduces the primary/secondary candidate ceilings
  from 160/80 to 40/20 before store-level filtering.
- Temporal context cannot influence parent ranking. This is intentional:
  neighbors are supporting context for the answer model, not independently
  scored evidence for selecting the parent.
- Neighbor expansion is at most 20 snippets per recall instead of growing with
  every fused candidate.
- Retrieval configuration hashes change because candidate multipliers,
  minimums, enrichment order, and neighbor budget are public contract fields.
- Quality and latency remain empirical. The change must pass the frozen
  three-variant diagnostic before a full LoCoMo run is allowed.
