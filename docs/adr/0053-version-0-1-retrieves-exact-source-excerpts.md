---
status: accepted
---

# Version 0.1 Retrieves Exact Source Excerpts

## Context

Version 0.1 initially ranked one parent document per Coding Memory and compiled
the parent body as an indivisible context section. Long repository memories
therefore had two failure modes: the parent could rank while the relevant line
remained diluted, or the whole section could be omitted when it did not fit the
remaining token budget. Raising the memory count did not solve either problem.

Evidence Facts already provide exact child documents for capture-derived
memory. Manually remembered Repository Knowledge may legitimately have no
Evidence Facts, so it needs an equally deterministic, non-semantic retrieval
unit without inventing provenance.

## Decision

The rebuildable LanceDB projection contains:

- one parent document for every Coding Memory;
- one exact child per Evidence Fact when facts exist;
- otherwise, one exact child per non-empty content line, capped at 128 lines.

Line children are search documents, not durable facts. Their IDs contain the
memory ID and zero-based line ordinal. Their text is copied exactly from
Markdown Truth, and their status follows the parent.

Lexical and vector search operate over the same parent/child projection. Parent
scores use reciprocal-rank fusion. Eligible child documents are reranked
locally, capped at 12 per memory, and attached to their parent result. Recall
admits up to 20 Repository Knowledge parents, then globally packs the highest
scoring exact excerpts while repeatedly checking the rendered UTF-8 token
upper bound. The returned sidecar records actual rendered memory and Evidence
Fact IDs, the public type caps, and omitted excerpt count.

The retrieval profile identity includes
`codecairn-memory-line-snippets-v1`. Existing projections must rebuild rather
than silently mixing document schemas. The context renderer identity becomes
`codecairn/typed-excerpt-context-v2`.

## Consequences

- A large memory can contribute only the lines relevant to the current task.
- Context never claims a generated summary is an exact source excerpt.
- Line children remain rebuildable and do not add a fifth Coding Memory type.
- More index rows are traded for smaller, more useful context sections.
- Changing excerpt projection or packing rules invalidates candidate-bound
  retrieval and LoCoMo evidence.

This amends ADRs 0012, 0021, and 0026 for the version 0.1 runtime. Their
historical benchmark contracts remain immutable.
