# V15 Rebalances Fact Selection and Context Budgeting

## Status

Accepted for implementation. V15 benchmark quality remains unverified until a
new immutable retrieval-only diagnostic passes its evidence, latency, and
resource gates. The v14 question sets and run artifacts remain unchanged
historical evidence.

## Context

The scored 200-question diagnostic that motivated the recent retrieval work
completed without infrastructure failures at 139/200 (69.5%). Its category
accuracy was 52% multi-hop, 80% temporal, 54% open-domain, and 92% single-hop.
That scored run predates the v14 retrieval protocol; it must not be presented as
a v14 score.

V14 deliberately stopped before answer and judge calls. The immutable
`locomo-diagnostic-200-v14-hierarchy-retrieval-efe76a7` run completed all 200
questions with zero infrastructure failures and no provider tokens or cost.
Among 192 questions with resolvable gold evidence, complete evidence reached:

| Boundary | Complete questions | Coverage |
|---|---:|---:|
| Ranked parents | 178/192 | 92.71% |
| Candidate snippets | 157/192 | 81.77% |
| Final context | 136/192 | 70.83% |

Every oracle context fit within the 4,000-token contract, but the produced
contexts averaged 3,981.46 pinned upper-bound tokens. Retrieval P95 was
2,868.51 ms, above the 2,500 ms gate. Maximum observed RSS was 1,040,449,536
bytes, below 2 GiB. V14 therefore passed run completion, infrastructure,
context-size, and memory checks, but failed both the 85% complete-context
coverage gate and the latency gate.

The 56 resolvable questions without complete final evidence separated into
three first-failure boundaries:

| First failure | Questions | Diagnosis |
|---|---:|---|
| Parent selection | 14 | Required parents were usually retrieved but pruned before the selected top 20. |
| Within-parent fact selection | 21 | Equal parent quotas and a fixed top-eight cutoff discarded relevant facts, including nearby dialogue turns. |
| Context packing | 21 | Gold facts reached the candidate set, but per-line rounding, repeated parent overhead, and global score ordering consumed the available budget before every required fact was admitted. |

The local fact CrossEncoder also scored about 241 documents in 31 batches per
question. Batches mixed short and long documents, so tokenizer padding made the
v14 selection improvement more expensive than its candidate count alone
suggested.

Increasing every candidate bound or the context window would spend more CPU and
answer tokens without addressing these boundaries. Query-time LLM refinement
would also add a paid, non-deterministic dependency before the provider-free
retrieval gate.

## Decision

### Rank-weighted parent allocation

The global fact-rerank budget remains bounded at 256 candidates, but it is no
longer divided equally across every selected parent. Parent ranks 1-4 receive
weight 3, ranks 5-8 receive weight 2, and later parents receive weight 1.
Deterministic allocation observes a hard cap of 24 candidates per parent.
After reranking, at most 12 facts from one parent may proceed to context
compilation.

The prefilter keeps already matched facts first, then facts within two
attributed turns of a match, then query overlap and source chronology. This
preserves bounded recall while directing capacity toward the parents most
likely to contain a second or third required fact.

### Previous-turn-aware fact reranking

Each CrossEncoder document may combine three bounded components:

1. an evidence-linked semantic projection;
2. the immediately preceding attributed source turn; and
3. the candidate's exact attributed source turn.

The preceding turn supplies dialogue context for short answers such as “yes,”
“there,” or a bare date. The following turn is intentionally excluded because
it can repeat the next question and outrank the turn that contains the answer.
The final selection still maps to the candidate's authoritative source fact ID;
dialogue context and semantic text cannot author provenance.

### Semantic ranking, exact-source rendering

Semantic Atomic Facts may improve prefilter and CrossEncoder inputs, but they
never replace authoritative evidence in the Markdown. Source linkage proves
provenance, not semantic entailment. Every source fact counted in
`rendered_fact_ids` is therefore rendered as its complete exact attributed
text, including the source timestamp when present.

`RecallSnippet.text`, the source fact ID, and the exact attributed source text
remain in the JSON sidecar for audit and citation validation. The semantic
projection remains derived ranking metadata, not durable truth or presentation
evidence. Answer citations continue to resolve to rendered authoritative source
fact IDs.

### Exact upper-bound byte budgeting

The compiler reserves and admits UTF-8 bytes directly under the pinned
`codecairn/utf8-two-byte-upper-bound-v1` contract, then computes the final token
count once. It no longer rounds every heading and fact independently before
summing them. Parent heading and source overhead are charged only when the
first fact from that parent is admitted.

This removes deterministic budget fragmentation without increasing the
4,000-token ceiling or changing the historical tokenizer contract.

### Length-sorted CrossEncoder batches

Before local inference, candidate documents are sorted by text length with
memory ID as the deterministic tie-breaker. Scores are restored to the original
document identities after inference. Grouping similar lengths reduces padding
work without changing the candidate set, model, or ranking semantics.

### Protocol and compatibility

V15 freezes the new selector identity, the 256 global candidate limit, the
rank-weighted per-parent allocation, the 24-candidate and 12-selected-fact
per-parent limits, previous-turn-aware CrossEncoder input, exact-source
renderer revision, and exact upper-bound byte accounting.

The v15 question sets retain the frozen v14 question selection and promotion
gates. A verified v7 corpus and compatible frozen query vectors may be reused
when dataset, selection, semantic projection, embedding identity, revision, and
dimension match. No document or query re-embedding is justified solely by
selection, rendering, or local batching changes.

The v14 question-set JSON, manifests, summaries, question artifacts, and
evidence report are immutable historical evidence. V15 uses new question-set
and run IDs; it does not overwrite or resume a v14 run under a changed
retrieval contract.

## Consequences

- High-ranked parents can retain several related facts without making recall or
  CrossEncoder work unbounded.
- Short dialogue answers gain the preceding turn needed to interpret them while
  preserving exact source provenance.
- Semantic projection can improve fact ranking, but complete exact source text
  remains the only evidence eligible for Markdown coverage.
- Exact byte accounting uses the existing context ceiling more fully; it does
  not increase the answer model's token budget.
- Length-sorted batches target the measured padding cost without changing
  retrieval quality by construction.
- Retrieval-only v15 must still reach at least 85% complete gold context
  coverage, P95 at most 2,500 ms, contexts at or below 4,000 pinned tokens, RSS
  below 2 GiB, and zero infrastructure failures before any paid answer or judge
  run.
- No v15 accuracy, coverage, latency, or cost improvement is claimed until a
  checked-in immutable run manifest and raw aggregate inputs pass verification.

This decision supersedes ADR 0021's equal parent allocation, fixed
16-candidate/8-selected-fact per-parent limits, per-line token summation, and
unsorted local inference batches. It preserves ADR 0021's bounded selector,
exact-only Markdown evidence, exact source authority, provider-free retrieval
gate, and immutable-artifact boundary.
