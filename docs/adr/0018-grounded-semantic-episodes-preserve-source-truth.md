# Grounded Semantic Episodes Preserve Source Truth

## Status

Accepted for implementation. Benchmark quality remains unverified until a new
LoCoMo corpus is built under the v5 projection contract.

## Context

The v4 conversation adapter stored every dialog turn as an unattributed
`user_quote`. Speaker and timestamp survived only inside the hash preimage, so
recall could not show who said a fact or when it was observed. The parent
Episode contained only the first and last turn, while answer generation claimed
that its context was attributed and timestamped. Query coverage could also be
satisfied by speaker names repeated in every parent title instead of by a
matched fact.

Generating rewritten facts directly into `EvidenceFact` would improve search
text at the cost of CodeCairn's central audit invariant: model output is not
source evidence. Persisting a second Episode store would also create a second
truth owner beside Markdown.

## Decision

`MemoryRuntime.write_episode()` is the single public write interface for an
attributed source Episode. An adapter supplies exact turns with deterministic
actor, role, observed timestamp, source order, and `EvidenceReference`.
Callers do not choose Evidence Fact kinds, memory types, IDs, proposals, or
gate behavior.

The runtime stores two explicitly different layers in the same immutable
Markdown memory:

1. `EvidenceFact(kind="conversation_turn")` keeps the exact source text and
   deterministic attribution. It remains the only evidence accepted by the
   Evidence Gate.
2. `SemanticEpisode` contains a narrative and grounded `SemanticAtomicFact`
   search annotations. Every annotation cites one or more source fact IDs and
   can never satisfy an evidence gate by itself.

The default `LosslessEpisodeSemanticizer` performs no provider call. It renders
all attributed turns in source order and creates one grounded search fact per
turn. `EpisodeSemanticizer` is an injected seam for a future cached structured
model adapter. A semanticizer may propose derived retrieval text and references
to existing source Evidence Fact IDs, but it may not create or alter those
source IDs, source attribution, timestamps, or evidence locators. Semantic
annotation IDs are deterministic functions of their text and validated source
references; the gate rejects any other ID. Invalid or incomplete source
references reject the whole write before Markdown, SQLite, or index state
changes.

The lossless semanticizer is a correctness baseline, not semantic compression.
It does not split multi-clause turns, resolve pronouns, normalize relative time,
or infer cross-turn relations. It establishes attribution, grounding, rebuild,
and parent-hydration behavior without a provider dependency. Claims about
semantic extraction quality or benchmark gains require a separately identified
structured semanticizer and immutable evaluation artifacts.

Markdown persists both layers. SQLite mirrors them additively, and LanceDB
projects the Semantic Episode as the parent plus Semantic Atomic Facts as
children. Old Markdown without attribution or semantic annotations remains
readable and keeps the ADR 0012 Evidence Fact projection fallback. Rebuild
never invokes a semanticizer.

Recall coverage is computed only from matched or grounded fact snippets, not
from parent titles or summaries. After ranking, selected semantic parents are
hydrated as indivisible complete Episodes. Lower-ranked parents are dropped
when the deterministic context budget is exhausted; an individually oversized
top parent may use an explicitly marked partial evidence closure. Hydrated,
partial, and dropped parent IDs are recorded in `RecallSidecar`.

The LoCoMo corpus projection contract becomes
`locomo-attributed-grounded-episode-v5`. Corpus loading rejects every other
projection revision before question evaluation. Benchmark category labels and
gold answers remain outside memory and query-planning interfaces.

## Consequences

- Exact text, actor, timestamp, order, and provenance round-trip independently
  of retrieval annotations.
- The lossless baseline deliberately retains one search annotation per source
  turn; clause-level Atomic Facts and reference resolution remain future
  semanticizer work rather than properties of this decision.
- A future semantic model can split clauses or resolve references without
  becoming an evidence authority.
- Parent hydration spends context on fewer complete Episodes instead of many
  uniformly truncated summaries.
- Existing corpora cannot recover lost attribution and must be rebuilt from
  the original dataset.
- Adding semantic annotations changes projection fingerprints and therefore
  invalidates old indexes and query-comparison artifacts.
- This decision supersedes ADR 0012 only for memories carrying a
  `SemanticEpisode`; the legacy Evidence Fact projection remains the backward-
  compatible fallback.
