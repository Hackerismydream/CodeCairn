# Selected Parents Rerank Authoritative Facts Before Context Compilation

## Status

Accepted for implementation. Benchmark quality remains unverified until the
v14 retrieval-only diagnostic passes its evidence, latency, and resource gates.

## Context

The first v13 hierarchical retrieval-only diagnostic completed 200 questions
without infrastructure failures, but complete gold evidence reached final
context for only 72 of 192 resolvable questions.

The loss occurred at two separate boundaries:

- 37 questions ranked every required parent Episode but did not project every
  required source fact into the parent's candidate snippets.
- 69 questions had every required source fact in candidate snippets, but the
  v4 compiler omitted at least one of them.

The v4 compiler reserved one fact for every selected parent before admitting a
second fact from any parent. With roughly 20 selected parents and a 4,000-token
limit, weak question restatements from many parents displaced stronger second
or third facts from the best parents. Merely changing snippet order could not
fix the 37 questions whose required source fact was absent before compilation.

EverOS demonstrates the useful mechanism: child facts influence parent ranking,
and selected parents can perform query-scoped fact recall. Its unbounded
agentic path and complete-Episode LoCoMo rendering are not compatible with
CodeCairn's 2 GiB worker and 4,000-token context contracts.

## Decision

### Bounded authoritative fact selection

After parent ranking and bounded neighbor expansion, a private
`EvidenceSelector` inspects authoritative `EvidenceFact` records within the
selected parents. Semantic Atomic Facts may enrich the text submitted to the
reranker, but selected evidence always maps back to an exact source fact ID and
exact attributed source text.

Selection is bounded by three frozen limits:

- at most 256 fact candidates globally;
- at most 16 fact candidates from one parent before global allocation;
- at most 8 selected facts from one parent.
- at most 2,048 characters in one local reranker document.

When the global limit is shared across many parents, every parent receives a
deterministic quota. Existing matched facts are preferred during the cheap
prefilter, followed by query-term overlap and source order. One pinned local
CrossEncoder call scores the resulting global batch. Query-time embedding and
LLM calls are not added.

Every selected snippet records the finite relevance score and selector identity
`bounded-authoritative-cross-encoder-v1`. The selector limits and identity are
part of `RecallPlannerConfig.public_config`, the retrieval configuration hash,
and the frozen LoCoMo protocol.

### Scored facts-first compilation

The v5 compiler treats fact relevance as the admission priority. It globally
packs scored authoritative facts under the existing 4,000-token budget and
pays a parent's title and source overhead only when at least one fact from that
parent is admitted. Per-parent fact limits, exact source citations, optional
procedure hydration, deterministic token counting, and omission traces remain
enforced.

Parent diversity is therefore a soft consequence of score and token cost, not a
hard one-fact-per-parent reservation. Deterministic test Adapters without fact
scores retain the legacy breadth-first path so unit fixtures remain independent
from the local model.

### Protocol and compatibility

The v14 question-set assets freeze the selector identity, the 256/16/8 limits,
the enrichment order, and renderer `scored-facts-first-v5`. Existing v13 assets
remain immutable historical evidence.

Report and answer validation continue to accept the token-budgeted v4 renderer
for historical artifacts. v4 does not regain legacy parent-ID citation support:
both v4 and v5 answers may cite only rendered source facts.

A verified v7 corpus and frozen query vectors may be reused by v14 when dataset,
selection, semantic projection, embedding identity, and dimensions match. The
new run manifest still binds the v14 question-set digest, current retrieval
configuration, selector limits, renderer, and repository commit. No document or
query re-embedding is required solely because context selection changed.

## Consequences

- Parent recall, within-parent fact selection, and final context packing are
  independently observable rather than conflated.
- The local reranker performs more work per question, but the candidate count,
  batch size, worker lifetime, RSS ceiling, and context size are all bounded.
- Exact source facts remain the sole answer evidence even when semantic
  projections improve their ranking text.
- A retrieval-only 200-question run must demonstrate at least 85% complete gold
  context coverage, P95 at most 2,500 ms, contexts at or below 4,000 pinned
  tokens, RSS below 2 GiB, and zero infrastructure failures before paid answer
  or judge calls.
- This decision supersedes ADR 0020's v4 one-evidence-per-parent admission floor.
  It does not claim a quality improvement until immutable v14 artifacts pass
  the declared gates.
