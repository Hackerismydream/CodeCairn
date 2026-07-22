# Recall Context Packs Grounded Facts Before Parent Episodes

## Status

Accepted.

## Context

ADR 0018 made attributed source turns authoritative and initially rendered selected
semantic parents as indivisible complete Episodes. The frozen 200-question LoCoMo
diagnostic showed that this rendering policy was incompatible with fact-level
ranking: recall selected about 20 parents per question, but the character budget
could hydrate only about 5.5 complete parents and silently omitted about 14.3.
Necessary lower-ranked evidence therefore disappeared after retrieval had already
found it.

Increasing the context window would retain the same all-or-nothing failure mode and
raise answer cost. Rendering every parent as an equal fixed truncation would waste
budget on weak snippets and would not preserve the strongest fact from each selected
parent.

## Decision

Recall Context uses a facts-first, round-robin compiler:

1. Each selected parent must first fit one compact grounded evidence excerpt plus
   its source URI as one atomic allocation.
   When only the Episode lane matched, the engine chooses query-overlapping source
   Evidence Facts and then deterministic beginning/middle/end coverage facts; it
   never treats a generated summary or the first 200 characters of an Episode as
   matched evidence.
2. Additional matched, sibling, and temporal excerpts are allocated in rank-aware
   rounds under the existing deterministic character budget.
3. A procedure query may hydrate at most the first two complete parent Episodes,
   and only from budget left after compact evidence has been allocated.
4. A selected parent that cannot fit even one compact grounded excerpt is explicitly
   recorded as omitted. Compact representation is not itself a degraded result.
5. The sidecar records the rendered parent IDs, rendered fact IDs, omitted parent
   IDs, the count of unique ranked snippet facts not rendered, renderer revision,
   and final character count. Rendered and omitted parent IDs must partition the
   selected ranking exactly; facts-first reports reject a missing trace.
   Complete hydration records every authoritative parent fact ID, not only the
   compact excerpts.
6. Answer helpers and citation validation may consume only facts recorded as rendered
   by the context trace. Ranked but unrendered snippets are not answer evidence, and
   report verification rejects trace IDs that are absent from the ranked source
   facts.

The complete attributed Episode remains authoritative source evidence and remains
available for audit. This decision supersedes only ADR 0018's indivisible complete-
Episode rendering policy; it does not change source truth, semantic grounding,
projection, or rebuild contracts.

The soft route candidate policy remains the policy accepted in ADR 0016: the primary
hierarchy receives `max(40, top_k * 2)` candidates and the secondary hierarchy
receives `max(20, top_k)` candidates. Restoring those asymmetric defaults is a
separate retrieval-budget correction and must be measured independently from the
context compiler in ablation reports.

The corresponding 200-question protocol is frozen in
`benchmarks/locomo/diagnostic-200-v12.json`. A run validates that protocol before
creating an artifact directory or invoking answer and judge providers. The ablation
verifier compares every planner field except the deliberately varied mode and
neighbor window. The protocol separately freezes both ordinary and temporal
neighbor windows for each recall mode, so the ablation cannot silently change
the strength of one variant.

## Consequences

- Multi-hop and list questions can retain evidence from more selected sessions
  without increasing the answer context budget.
- Complete parent transcripts no longer crowd out compact evidence from lower-ranked
  parents.
- `partial_episode_ids` identifies parents represented by compact evidence rather
  than complete hydration. Recall completion becomes partial only when required
  coverage or a selected parent's compact evidence is missing.
- Retrieval configuration hashes change because the renderer revision and route
  budgets are manifest-recorded. Old question checkpoints cannot resume under the
  new contract.
- Clause-level semantic extraction is still a separate ingestion concern. This
  compiler improves evidence allocation but does not turn one-turn lossless facts
  into semantic clauses.
- Query-aware Episode fallback is cached by memory within one recall, so the core,
  full, and final enrichment stages do not repeatedly scan the same long source
  Episode.
