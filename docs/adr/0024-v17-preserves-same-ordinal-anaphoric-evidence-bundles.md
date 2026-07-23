# V17 Preserves Same-Ordinal Anaphoric Evidence Bundles

## Status

Accepted for implementation. V17 retrieval quality remains unverified until a
new immutable 40-question preflight and the non-overlapping 160-question
holdout pass. The v16 run remains negative historical evidence.

## Context

The formal v16 retrieval-only run,
`locomo-diagnostic-40-v16-hierarchy-retrieval-ed89b6f`, completed all 40
questions with zero infrastructure failures and no paid answer, judge, or
remote embedding calls. It recorded:

| Boundary | Complete questions | Coverage |
|---|---:|---:|
| Ranked parents | 35/38 | 92.11% |
| Candidate snippets | 34/38 | 89.47% |
| Final context | 32/38 | 84.21% |

Retrieval P95 was 1,925.79 ms and maximum accepted worker RSS was
934,985,728 bytes. The run passed completion, infrastructure, latency,
context-size, and memory gates, but failed the 85% complete-context gate by one
resolvable question.

These measurements are backed by the checked-in
[manifest](../../benchmark_results/locomo/locomo-diagnostic-40-v16-hierarchy-retrieval-ed89b6f/manifest.json),
[summary](../../benchmark_results/locomo/locomo-diagnostic-40-v16-hierarchy-retrieval-ed89b6f/summary.json),
[evidence-coverage report](../../benchmark_results/locomo/locomo-diagnostic-40-v16-hierarchy-retrieval-ed89b6f/evidence-coverage.json),
and [resource-usage report](../../benchmark_results/locomo/locomo-diagnostic-40-v16-hierarchy-retrieval-ed89b6f/resource-usage.json).

V16 improved complete final contexts from 29/38 to 32/38 with no complete
question regression. Its alias, semantic-child, and prior-state slots produced
the three expected improvements. The quantity-transition improvement predicted
by ADR 0023 did not survive the final policy hardening.

The remaining quantity failure was
`locomo-question_c3670de7658d24c39bf4`, “How many screenplays has Joanna
written?”. All five gold facts were present in the ranked and candidate
boundaries, but only three reached final context. The missing pair was:

1. an attributed question asking whether the work was Joanna's third one; and
2. Joanna's immediately following confirmation.

The quantity selector grouped facts by ordinal and retained one primary
candidate per group. For `third`, semantic child support selected a later fact
stating that a script had been shown for the third time. That fact was useful
evidence, but it was not the same count state. Because it was not an anaphoric
ordinal, the compiler did not attach a following answer. The actual
`third one` question and answer remained ordinary candidates until only 13
bytes of context budget remained.

The 33/38 counterfactual in ADR 0023 used an earlier chronology-first winner.
The later policy correctly preferred topic and semantic support, but the
counterfactual was not rerun after that change. The formal v16 artifact, not the
older replay, is the source of truth.

## Decision

### Preserve bounded same-ordinal units

The quantity-transition slot keeps its current winner inside each ordinal and
adds one conservative fallback:

1. build one primary unit for each ordinal, attaching the immediate following
   turn when the primary is itself an anaphoric quantity;
2. order numeric primary units from the highest ordinal to the lowest, with
   `another` after explicit ordinals, so a tight slot does not discard the count
   state that determines a quantity answer;
3. when a non-anaphoric primary has no exact query-topic overlap and the
   transition is not `another`, build one optional unit from the best eligible
   anchored anaphoric candidate for that ordinal and its immediate following
   turn;
4. pack complete primary units first and optional units second, without letting
   the 12-fact candidate ceiling split a question/answer pair.

Eligibility, winner priority within an ordinal, source authority,
deduplication, per-parent admission limits, and the 12-fact quantity-slot
ceiling remain unchanged. A fallback may come from another already recalled
parent; its attached following turn must remain inside the fallback candidate's
parent and source-memory. The mechanism does not widen parent retrieval.

This mechanism protects two different but complementary interpretations of the
same ordinal: a semantically supported event and an explicit dialogue count
state. It does not encode screenplay vocabulary, benchmark categories, gold
evidence identifiers, conversation identifiers, or answer labels.

### Audit and protocol identity

The slot-policy identity advances to
`typed-protected-child-support-v2`. The query sketch remains
`codecairn/deterministic-query-sketch-v3`, and the exact-source renderer remains
`exact-source-coverage-aware-facts-first-v8`.

V17 introduces new 40-, 160-, and 200-question protocol files. Their question
selections, provider configuration, resource limits, and gates are identical
to v16. Only the slot-policy identity changes, while the 200-question promotion
contract is rebound to the v17 preflight digest.

The immutable v17 question-set definition SHA-256 values are:

- 40-question preflight:
  `03b7a000d8f263048a118b92d5cc008e6f3b25214acfccc45b07c80e34f1df3b`;
- 160-question holdout:
  `26ae021c9964ffb7df336eaf3ea730aaf05a330fd58f8addf25a5c08eab42e1f`;
- 200-question diagnostic:
  `d7b63a9e05e2619223943ef9d36b18f7dfe3d7d6365a4cb0bbcac385a13109dd`.

Persisted slot attempts remain replay-verified. A v16 artifact therefore cannot
pass as a v17 result: its manifest binds the v1 policy, and its quantity-slot
transcript differs from v2.

### Validation boundary

A deterministic replay over all 40 formal v16 checkpoints rebuilt the persisted
v16 contexts exactly before applying this change. Replaying v2 over the same
frozen admission candidates changed one question:

- complete final contexts moved from 32/38 to 33/38;
- the screenplay quantity question moved from 3/5 to 5/5 gold facts;
- the other 39 contexts were byte-budget and evidence-set stable;
- no previously complete question regressed;
- the changed context used 3,977 of 4,000 pinned tokens.

This is implementation evidence, not a benchmark result. Promotion still
requires:

1. a new v17 immutable 40-question retrieval-only run with at least 85%
   complete-context coverage;
2. a separate non-overlapping 160-question holdout with the same gate;
3. every context at or below 4,000 pinned tokens;
4. retrieval P95 at or below 2,500 ms;
5. process RSS below 2 GiB and zero infrastructure failures.

Paid answer and judge calls remain blocked until both provider-free retrieval
checks pass.

## Consequences

- Same-ordinal semantic and dialogue evidence can coexist within one bounded
  quantity slot.
- The repair adds no query-time model call, embedding request, or wider
  retrieval fan-out.
- Up to two additional exact source facts may displace ordinary high-scoring
  facts for each qualifying ordinal.
- V16 remains a valid negative artifact and cannot be silently reinterpreted
  under the repaired policy.
- The 40-question replay justifies a formal preflight, not a generalization
  claim; the independent 160-question holdout remains mandatory.
