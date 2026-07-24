# V23 Normalizes Safe Insufficient-Answer Shapes

## Status

Accepted.

## Context

The V22 200-question run completed 196 scored questions and reported four
infrastructure failures. Provider transport succeeded in every case. Two
responses returned a non-empty insufficient answer with citations, while two
returned an empty answer list with no citations. The application retried each
response once, received the same payload, and then classified the question as
a contract-exhausted infrastructure failure.

These shapes are locally contradictory but do not make the provider call or
question execution unavailable. Treating them as infrastructure failures drops
scoreable negative answers, spends a second model call, and obscures the
distinction between model quality and infrastructure reliability.

## Decision

V23 keeps the strict grounded-answer parser unchanged and adds two narrowly
bounded normalizations at the application retry boundary:

- `insufficient=true` with a non-empty string answer and populated citations
  discards the citations;
- `insufficient=true` with `answer=[]` and no citations becomes the fixed text
  `The context is insufficient.`

Every normalized attempt is accepted without another provider call and records
its normalization identifier in the version-2 attempt receipt. Unknown
citations on supported answers, omitted evidence, semantic-clause citations,
malformed JSON, and all other schema failures remain rejected.

The answer prompt also states that insufficient answers must use an empty
citation list and that the answer cannot be an empty string or list.

## Consequences

- The answer evidence contract advances to `grounded-cited-answer-v14`.
- The retry and retry-history contracts advance to version 2.
- V22 artifacts remain immutable and are not resumable under V23.
- The V23 40-, 160-, and 200-question assets preserve V22 selection, retrieval,
  resource, quality, and promotion gates while freezing the new answer
  contracts.
- A formal V23 score requires new commit-bound retrieval gates and paid scoring;
  replaying the four V22 payloads proves only the contract repair.
