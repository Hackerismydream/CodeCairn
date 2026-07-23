# V16 Protects Typed Evidence Before Global Context Packing

## Status

Accepted for implementation. V16 retrieval quality remains unverified until new
immutable runs pass the frozen 40-question preflight and the non-overlapping
160-question holdout. The v15 question sets and run artifacts remain unchanged
historical evidence.

## Context

The final v15 retrieval-only preflight,
`locomo-diagnostic-40-v15-hierarchy-retrieval-3f728fa`, completed all 40
questions with zero infrastructure failures and no answer or judge calls.
Among 38 questions with resolvable gold evidence, complete evidence reached:

| Boundary | Complete questions | Coverage |
|---|---:|---:|
| Ranked parents | 35/38 | 92.11% |
| Candidate snippets | 34/38 | 89.47% |
| Final context | 29/38 | 76.32% |

Final-context coverage was 5/10 for multi-hop, 10/10 for temporal, 4/8 for
open-domain, and 10/10 for single-hop. Retrieval P95 was 1,752.75 ms and the
maximum accepted worker RSS was 945,913,856 bytes. The run therefore passed
completion, infrastructure, latency, context-size, and memory checks, but
failed the 85% complete-context coverage gate. It is negative retrieval
evidence, not a scored accuracy result.

The v15 compiler globally ordered exact source facts by effective relevance.
That policy was correct for ordinary single-fact questions, but it allowed a
large set of high-scoring, partially redundant facts to exhaust the fixed
4,000-token context before lower-scoring evidence needed by a typed question.
A deterministic counterfactual inspected five representative budget misses
from the nine incomplete contexts:

| Budget-miss class | Representative question | Failure mechanism |
|---|---|---|
| Activity-list facet diversity | `locomo-question_a861ec724a42433f126a` | Several distinct list items lost to higher-scoring facts from already represented activity facets. |
| Quantity state transition | `locomo-question_c3670de7658d24c39bf4` | Ordinal transitions and a short following answer were separated and admitted too late. |
| Vocative alias | `locomo-question_b80d07ada1c4a005d8e8` | A shortened name occurred in a low-scoring greeting rather than an answer-shaped sentence. |
| Semantic child support | `locomo-question_077230ed9581db2d2211` | Relevant semantic child hits lifted the parent, but their exact source facts were not protected during final packing. |
| Prior-state inference | `locomo-question_065f3eb69389d2934860` | Earlier exclusivity and affect evidence was displaced by later, more directly named events. |

These classes describe admission failures in one frozen negative artifact.
They are not a taxonomy learned from the full LoCoMo dataset.

### Why naive breadth is rejected

Increasing selected-parent fan-out does not solve the final-context boundary.
Under the fixed 256-fact rerank budget, a wider parent set removes within-parent
breadth and can reduce complete candidate coverage. Increasing every
per-parent quota likewise spends more CrossEncoder work and presents more
redundant facts to the same fixed context.

An analysis-only counterfactual that protected benchmark-specific activity,
potential, count, alias, and affect keyword lists reached 34/38 with no observed
regressions on the frozen 40-question artifact. That result is not accepted:
the facet dictionaries encode the inspected questions, have no stable domain
contract, and were not tested on unseen questions. Raising the 4,000-token
ceiling would also change answer cost and the benchmark contract rather than
improve evidence selection.

## Decision

### Typed, provider-free evidence slots

The deterministic query sketch advances to
`codecairn/deterministic-query-sketch-v3`. It may emit bounded context-evidence
slots from query shape, named anchors, and normalized topic terms. Slot
construction performs no query-time LLM calls, reads no benchmark category or
gold evidence, and does not alter parent retrieval.

V16 implements four general mechanisms:

1. `semantic_child_support` protects up to 16 exact source facts linked to
   retrieved semantic Atomic Facts.
2. `quantity_transition` protects up to 12 ordinal or count-state facts and a
   bounded adjacent answer needed to resolve an anaphoric transition.
3. `vocative_alias` protects up to two exact source facts where one named
   participant addresses another with a shortened form.
4. `prior_state` protects up to four earlier exact source facts carrying both
   exclusivity and affect evidence for a before-state query.

The policy identity is `typed-protected-child-support-v1`. Each slot has a hard
ceiling in the planner configuration. Slot-selected facts are attempted before
the ordinary global relevance order, but they still pay their complete UTF-8
byte cost, obey the existing per-parent fact limit, deduplicate by authoritative
source fact ID, and stop at the same 4,000-token upper bound.

Each requested slot records its ordered fact attempts and one of four admission
outcomes: `admitted`, `duplicate`, `parent_limit`, or `budget`. Report
verification does not trust that persisted transcript. It rebuilds the typed
query sketch from the checkpoint question, replays the deterministic compiler
over the frozen ranked evidence and planner limits, and requires the complete
slot transcript to match. The trace freezes the pre-hydration admission fact
IDs, so later complete-Episode hydration cannot alter replay input. Removing a
slot, changing that boundary, or changing a rejection reason makes the run
ineligible for reporting.

The renderer advances to
`exact-source-coverage-aware-facts-first-v8`. Semantic text may select an
authoritative child, but only the complete exact attributed source fact is
rendered and counted as evidence. The Markdown truth, citation, sidecar,
omission trace, and source-authority contracts from ADR 0022 remain unchanged.

The activity-family facet route from the analysis-only counterfactual is
deliberately omitted. On the frozen v15 artifact, replaying the four accepted
mechanisms changes complete-context coverage from 29/38 to 33/38, not 34/38.
This is an offline, targeted context replay; it is implementation evidence only
and does not replace a new retrieval run.

### Protocol and evaluation boundary

V16 introduces new 40- and 200-question protocol files and run IDs. Their
question selection and gates are identical to v15, while their protocol digests
bind the v3 query sketch, v8 renderer, slot-policy identity, and all four slot
limits. The v15 JSON files, manifests, summaries, and evidence reports are
byte-identical historical evidence.

No generalization claim may be made from the targeted replay. The implementation
must first pass a new immutable retrieval-only run over the frozen 40-question
slice. It must then be evaluated with
`diagnostic-160-holdout-v16.json`. The holdout uses
`stratified-sha256-window-v1` with an offset of 10 and a count of 40 in each
scored category. It therefore selects zero-based ranks 10 through 49 from the
same seeded ordering used by the 40- and 200-question assets. Its 160 question
identities have selection SHA-256
`5aa5f9518508417fc0905b928ea8774e6a56e149d7afdbd816979da6ce766ad9`;
they have zero overlap with the 40-question preflight and are the exact set
difference between the 200- and 40-question selections.

The immutable v16 question-set definition SHA-256 values are:

- 40-question preflight:
  `85ea8afa0936519762f8ca57aa9edfde9aa7748644b3c638372c48d2e7756a99`;
- 160-question holdout:
  `02a28013feb64ad034f736ebab1a86e665ebc05ccde0f0410a1dc14acef38e2c`;
- 200-question diagnostic:
  `04517fed9274f85e03e46fc9c07b79ce61cd1e6ba9f61174a66ae99a83eae2f4`.

A 200-question aggregate is insufficient for the retrieval gate unless the
non-overlapping 160-question holdout is reported separately. Paid answer and
judge calls remain blocked until both provider-free retrieval checks pass.

The promotion thresholds remain unchanged: complete gold evidence must reach
context for at least 85% of resolvable questions, every context must remain at
or below 4,000 pinned tokens, retrieval P95 must be at most 2,500 ms, process
RSS must remain below 2 GiB, and infrastructure failures must be zero.

## Consequences

- Typed questions can reserve a small amount of the existing context for
  structurally necessary evidence without widening parent retrieval or the
  context window.
- The change adds no embedding, query-time LLM, answer, or judge cost to a
  retrieval-only preflight.
- Slot selection is auditable through a versioned planner and renderer contract;
  exact source facts remain the only evidence authority.
- Protected facts can displace higher-scoring ordinary facts. The frozen
  40-question run and non-overlapping 160-question holdout are therefore
  required to measure regressions.
- The 33/38 replay is not a benchmark result, accuracy score, or
  generalization result. V16 has no publishable improvement until immutable
  run artifacts pass verification.

This decision extends ADR 0022's exact-source rendering, fixed context ceiling,
bounded local reranking, and provider-free retrieval gate. It changes only
query typing and final evidence admission; it does not change durable memory,
embedding identity, parent ranking, or source authority.
