# Semantic Projection Rejects Foreign Citations Per Clause

## Status

Accepted.

## Context

The first formal v19 corpus build stopped during `conv-42` after three
conversation checkpoints had been published. DeepSeek returned a structurally
valid JSON response containing at least one clause whose `source_fact_ids`
included an identifier outside the current request window.

The adapter recorded the failed conversation receipt before terminating:
23 semantic projection calls, 44,401 input tokens, 11,237 output tokens, and
CNY 0.0248526. No embedding call occurred for that failed conversation. The
failure was therefore accounted rather than treated as unknown spend.

Rejecting the entire response preserved the Evidence Gate, but made one
untrusted clause discard every valid sibling clause and abort the whole corpus.
Retrying the same response would spend money without changing the deterministic
grounding rule. Accepting the foreign citation would allow untrusted model
output to author provenance.

## Decision

The structured projection request contract advances from
`codecairn/grounded-clause-drafts-v2` to
`codecairn/grounded-clause-drafts-v3`.

JSON and clause schemas remain strict. For each schema-valid clause:

1. if every cited fact belongs to the current request window, emit the
   untrusted `ClauseDraft`;
2. if any cited fact is foreign, reject that clause only;
3. continue validating independent sibling clauses.

The downstream `GroundedClauseSemanticizer` still canonicalizes and validates
every emitted draft against the complete authoritative Episode. Empty accepted
output falls back to the lossless source-fact narrative. The v8 corpus
projection also emits a raw retrieval child for every authoritative source
fact, so clause-local rejection cannot remove source truth from the index.

The request contract remains part of the adapter configuration digest and the
immutable corpus build contract. A v2 cache or partial corpus therefore cannot
be reused by a v3 build.

## Consequences

- A foreign model citation never enters Markdown truth or the retrieval index.
- Valid sibling clauses survive one malformed citation.
- Provider usage remains charged and recorded once; no automatic model retry is
  added.
- The failed v2 build remains local negative evidence and is not a formal v19
  corpus.
- Formal retrieval and scoring still require a fresh clean commit and a fully
  published v3 corpus.
