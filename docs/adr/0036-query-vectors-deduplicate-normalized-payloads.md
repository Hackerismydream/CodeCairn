# Query Vectors Deduplicate Normalized Payloads

## Status

Accepted.

## Context

The full LoCoMo selection contains different question records with identical
normalized question text. The query-vector builder previously embedded every
question record independently. When identical payloads landed in separate
provider batches, small provider-side floating-point differences produced two
vectors for one payload digest.

The immutable artifact validator correctly rejected that ambiguity, but only
after the paid build had completed.

## Decision

Query-vector construction now freezes the first vector returned for each
normalized payload SHA-256 and reuses its exact encoded bytes for every later
question with that digest.

The builder:

- deduplicates both within and across provider batches;
- reconstructs the payload cache from verified checkpoints when resuming;
- records zero provider usage for a fully cached batch;
- keeps one record per question so selection and checkpoint cardinality remain
  auditable.

New artifacts declare
`deduplication_contract=payload-digest-first-vector-v1`. The field participates
in the build contract, so an older ambiguous artifact cannot be silently
reused. Legacy artifacts without duplicate payloads remain readable.

## Consequences

- Identical normalized queries always resolve to identical frozen vectors.
- Full-dataset builds avoid duplicate embedding spend.
- Strict duplicate-payload validation remains fail-closed.
- Existing diagnostic artifacts remain immutable and compatible.
