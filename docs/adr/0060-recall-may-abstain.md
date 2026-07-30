# ADR 0060: Recall may abstain

Status: Accepted

## Context

Hybrid retrieval previously treated `top_k` as an obligation to return memory.
An unrelated task could therefore receive the least-bad memories in the
namespace. That is unsafe for an automatically injected Memory OS context:
ranking orders candidates, but does not prove that any candidate is relevant.

EverOS exposes optional score thresholds at its knowledge-search and
hierarchical-search boundaries. Its ordinary positive-`top_k` path does not
enable a threshold automatically. CodeCairn adopts the useful mechanism, but
sets the policy at its own product boundary because its compiled context is
consumed directly by coding agents.

## Decision

Recall applies profile-owned relevance admission before type caps, result
limits, and context compilation:

- a lexical candidate is admitted;
- a vector candidate is admitted only when cosine similarity reaches the
  active retrieval profile threshold;
- an explicitly requested, uniquely resolved open Work State remains pinned;
- if nothing is admitted, recall returns an empty ranked result and the
  Markdown message `No relevant memory was admitted.`

The initial thresholds are `0.45` for DashScope
`qwen3.7-text-embedding` and `0.62` for local FastEmbed
`BAAI/bge-small-en-v1.5`. They are part of retrieval-profile behavior, not
LanceDB index identity. Changing one changes the reported retrieval profile
and requires evaluation, but not an index rebuild.

Every result sidecar records the admission policy, outcome, reason, threshold,
and maximum observed vector similarity. Below-threshold candidates appear as
`relevance` omissions. The retrieval gate includes 20 unrelated tasks and
fails when irrelevant memory is injected in more than 5% of them.

## Consequences

Recall is no longer required to choose an answer. Callers, including Pico,
receive no memory when the namespace has no relevant candidate. Explicit
Work State continuation still works without weakening repository isolation or
lifecycle filtering.

The threshold calibration is a local release guard, not a claim of universal
semantic relevance. A new embedding profile must define and test its own
threshold. A future evaluation may replace the lexical admission rule with a
scored lexical policy, but must preserve exact identifiers and auditable
abstention.
