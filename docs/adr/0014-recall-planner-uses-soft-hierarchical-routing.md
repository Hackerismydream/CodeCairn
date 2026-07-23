# RecallPlanner Uses Soft Hierarchical Routing

## Context

The rebuildable index contains broad Episode documents and smaller AtomicFact
children. Episode-only search misses an exact detail when the generated memory
summary omits it. Fact-only search loses the narrative and is brittle when a
query is phrased as a problem or solution. A hard classifier would turn every
routing error into a recall failure.

EverOS was consulted for the mechanism-level pattern of independently recalling
parents and children, lifting each child score to its parent, and expanding
facts only after a parent is selected. CodeCairn keeps its own contracts,
storage projection, fusion, attribution, and benchmark implementation.

## Decision

`RecallPlanner` classifies each query as `episode_first` or `fact_first` with a
versioned deterministic cue router. The route changes bounded candidate-pool
sizes; it never disables the secondary hierarchy level. This soft route makes
the decision inspectable without making it a single point of failure.

Vector and lexical search run independently at both levels. AtomicFact results
are max-pooled by parent memory, and the four parent rankings are fused with
reciprocal rank fusion. The CrossEncoder receives the parent narrative plus
matched fact excerpts and bounded siblings. Recall Context preserves the source
memory URI for every excerpt.

Full hierarchy mode also expands one chronological memory on either side of a
matched parent when they share the same source Episode. Chronology is derived
from immutable session and raw-event indices. Expansion never crosses a
repository or Episode boundary, and every neighbor remains separately
attributed.

The public retrieval configuration freezes three ablation modes:

- `episode-only`: parent lexical/vector recall only; fact postings and
  provenance expansion cannot introduce or enrich a parent;
- `hierarchy-no-neighbors`: parent plus AtomicFact recall;
- `hierarchy`: parent plus AtomicFact recall and bounded neighbors.

The mode, router version, limits, per-level candidate counts, matches, excerpts,
and expansion count are retained in manifests or recall sidecars.

## Consequences

- Exact fact text can surface its durable parent even when the parent summary
  omits the query term.
- Query routing remains deterministic and replayable.
- Temporal neighbors improve local continuity without becoming unattributed
  free text.
- A diagnostic run can compare each layer without changing code or model
  identities.
- More candidates reach the reranker, so latency and quality must be measured
  together before the full LoCoMo run.
