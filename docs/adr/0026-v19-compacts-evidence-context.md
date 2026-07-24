# V19 Compacts Evidence Context

## Status

Accepted for implementation. V19 retrieval quality remains unverified until a
new immutable 40-question preflight and the non-overlapping 160-question
holdout pass. Paid answer and judge calls remain blocked.

## Context

The immutable v18 40-question retrieval-only preflight completed all questions
with zero infrastructure failures. It stayed within the frozen resource
limits: retrieval P95 was 2,069.47 ms, maximum process RSS was 969,719,808
bytes, and every context stayed at or below 4,000 pinned tokens.

The retrieval stages nevertheless diverged:

| Evidence boundary | Complete gold coverage |
|---|---:|
| Ranked evidence | 36/38 (94.74%) |
| Candidate snippets | 35/38 (92.11%) |
| Final context | 28/38 (73.68%) |

Two questions have no resolvable gold evidence and are excluded from these
ratios. The final-context result failed the frozen 85% gate, so the v18 holdout
and every paid scoring run remained blocked.

Inspection showed that the v8 renderer spent most of the 4,000-token budget on
repeated parent headings, evidence-section labels, relation labels, and memory
URIs. These fields are useful provenance, but they already exist in the
structured sidecar and do not need to be repeated in model-facing Markdown.
Seven of the ten incomplete contexts already contained every gold source fact
in the bounded candidate set.

Quantity-slot broadening and generic query-term heuristics did not improve a
deterministic replay and sometimes displaced correct evidence. Increasing the
context ceiling would hide the packing failure and violate the lightweight
runtime objective.

## Decision

### Render flat authoritative facts

The context renderer advances from
`exact-source-coverage-aware-facts-first-v8` to
`exact-source-flat-facts-first-v9`.

Model-facing Markdown contains one complete authoritative fact per line:

```text
- [fact_id] complete authoritative fact
```

The fact identifier remains a stable join key. Parent memory identity, source
URI, relation type, ranking scores, and candidate provenance remain in the
structured sidecar and immutable evaluation trace. This removes repeated
presentation chrome without weakening evidence authority or auditability.

### Reserve bounded breadth from a strong parent

The query sketch advances to `codecairn/deterministic-query-sketch-v5` and
always emits one `high_confidence_parent` evidence slot. The slot activates
only when the highest-ranked parent has a final score of at least `5.5`. It
then attempts at most the first four scored direct facts from that parent in
their existing selector order.

The slot does not widen parent recall, add a query-time model call, change fact
scores, or bypass the context budget. It protects bounded within-parent breadth
for list, activity, and preference questions where a strong parent contains
several independently useful facts.

The evidence-slot policy advances to
`typed-protected-child-support-v3`. The new fact limit and score threshold are
part of the frozen public planner configuration and therefore of corpus,
query-vector, run, and promotion validation.

### Keep the exact gate unchanged

The exact annotated evidence gate remains authoritative. Semantically
equivalent but differently annotated evidence does not receive manual credit.
The minimum complete-context coverage stays at 85%, every context stays at or
below 4,000 pinned tokens, retrieval P95 stays at or below 2,500 ms, and every
process remains below 2 GiB.

A deterministic replay over the immutable v18 pre-hydration candidates
projected 33/38 complete contexts (86.84%), with no regressions among the 28
previously complete contexts. This is design evidence only. It is not a v19
benchmark result and cannot authorize paid scoring.

## Validation Boundary

Before any paid answer or judge call:

1. all unit, integration, lint, type, and architecture checks must pass;
2. a fresh v8 corpus and query-vector artifact must bind the v19 protocol and
   implementation commit;
3. the immutable v19 40-question retrieval preflight must pass every frozen
   gate;
4. the disjoint v19 160-question holdout must pass the same gates;
5. the verified retrieval-gate receipt must bind both sources and the exact
   200-question target.

Only then may the 40-question paid ablation select a recall mode. Only the
selected mode may proceed to the full 200-question scored diagnostic.

## Consequences

- More of the fixed context budget carries authoritative evidence rather than
  repeated presentation metadata.
- Structured provenance remains lossless and machine-auditable.
- The new breadth slot has constant, manifest-pinned cost.
- V18 retrieval artifacts remain immutable negative evidence.
- V18 corpus and query-vector artifacts cannot stand in for formal v19
  artifacts because the implementation commit and frozen protocol changed.
- The replay may overestimate holdout performance; a failed canary or holdout
  still blocks paid scoring.
