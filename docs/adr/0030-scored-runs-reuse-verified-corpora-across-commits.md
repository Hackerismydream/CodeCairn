# Scored Runs Reuse Verified Corpora Across Commits

## Status

Accepted.

## Context

LoCoMo corpus artifacts already record their producer commit, immutable content
digest, dataset, semantic projection identity, embedding identity, protocol,
and per-conversation receipts. Retrieval runs also record the corpus producer
commit separately from the run commit.

The paid-scoring preflight nevertheless required both commits to be identical.
That prevented a parser-only evaluation fix from consuming an unchanged,
fully verified corpus and forced the same semantic and embedding work to be
paid again.

The ordinary retrieval runner already supports this producer/consumer split and
keeps both commits in worker specifications. The paid preflight was stricter
without adding a new integrity check.

## Decision

The paid preflight no longer requires the corpus producer commit to equal the
scored-run commit. It continues to bind and verify:

- corpus content and build-contract digests;
- dataset and question-set identities;
- semantic projection and embedding contracts;
- retrieval, reranker, planner, renderer, and worker contracts;
- the scored run commit and both retrieval-source run commits.

A contract change still invalidates the corpus. A consumer commit may reuse it
only when every artifact-facing contract remains identical.

Grounded answer parsing also accepts a non-empty JSON array of non-empty strings
for list questions and normalizes it to a semicolon-separated answer. Citation,
insufficient-state, exact-field, and source-evidence validation remain
unchanged.

## Consequences

- Evaluation-only fixes do not repeat semantic or embedding spend.
- Producer and consumer commits remain independently auditable.
- List-shaped model responses no longer become infrastructure failures merely
  because the internal answer representation is a string.
