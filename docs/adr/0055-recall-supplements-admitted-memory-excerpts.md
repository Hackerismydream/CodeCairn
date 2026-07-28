---
status: accepted
---

# Recall Supplements Excerpts Inside Admitted Memory

## Context

The shared lexical/vector search returns at most 100 parent and child
documents. A relevant parent can survive that global bound while its exact
evidence line does not, especially in long conversational memory. Parent
selection alone therefore does not prove that Recall Context contains the
evidence which made the parent useful.

## Decision

After active parent selection and type caps, Recall rebuilds the same bounded
line excerpts directly from each admitted memory:

- memories with Evidence Facts retain fact children only;
- memories without facts expose at most 128 non-empty content lines;
- original lexical/vector child candidates are reranked and form the first
  priority layer;
- deterministic lexical scores select additional lines inside admitted
  parents;
- each parent contributes at most 12 excerpts; and
- context compilation considers at most 192 excerpts before applying the
  existing total token budget.

The supplemental lines use the same stable document IDs and exact durable text
as the rebuildable search projection. They do not create facts or provenance.

## Consequences

- Global search no longer silently discards every useful line inside an
  admitted long memory.
- Semantic search quality is preserved because original child candidates keep
  their reranked order; supplemental lines only fill remaining capacity.
- Work remains bounded by admitted parents, 128 source lines, 12 excerpts per
  parent, 192 compilation candidates, and the total token budget.
- Retrieval and LoCoMo evidence must be regenerated for this packing contract.

This refines ADR 0053 without changing the durable memory schema or retrieval
profile identity.
