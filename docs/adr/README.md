# Architecture Decision Records

ADRs preserve why CodeCairn changed. They are append-oriented historical
records, not a flat set of simultaneously current specifications. Read
[`../architecture.md`](../architecture.md) for the current system and use the
ADR chain to understand the decisions behind it.

## Status rules

- `Accepted` means the decision is part of the maintained current or target
  design unless a later ADR supersedes it. It does not by itself prove that an
  implementation task has merged; maintained architecture and operations
  documents state that boundary.
- `Accepted and amended` means the core decision remains, but implementation
  changed a stated count, boundary, or lifecycle detail.
- `Superseded` means later ADRs own current behavior; the older record remains
  evidence of the change.
- A benchmark version in an ADR title is historical protocol identity. It is
  not automatically the current benchmark.

Some early ADR filenames retain old wording for stable links. For example,
`0009-evaluation-has-three-independent-suites.md` now records the implemented
four-suite amendment in its title and status section.

## Reading paths

### Runtime foundation

Read ADRs 0001-0011 for repository boundary, product scope, normalized traces,
stable episodes, derived evidence, storage/outbox behavior, entrypoint parity,
evaluation structure, milestones, and resumable import.

ADR 0040 amends the lifecycle notes in ADR 0006 and ADR 0008: index sync,
rebuild, and status are now public CLI and HTTP operations and import drains
the outbox by default, while neither entrypoint starts a background cascade
worker. See [`../runtime/operations.md`](../runtime/operations.md).

### Retrieval and projection

Read ADRs 0012-0016 for hierarchical projections, provider identity, soft
routing, DashScope production embeddings, and bounded enrichment. Read ADRs
0018-0027 for grounded semantic projection, authoritative source-fact
children, fact reranking, and the facts-first context renderer.

For current behavior, later decisions take precedence:

- ADR 0015 supersedes ADR 0013 as the default production embedding choice;
  ADR 0013 remains the explicit offline profile.
- ADR 0020 and later decisions supersede ADR 0019's original renderer details.
- ADR 0024 supersedes ADR 0023's V16 policy.
- ADR 0025 supersedes ADR 0024's current retrieval representation while
  retaining typed evidence ideas.
- ADR 0026 retains the current v9 flat renderer and v5 fact selector; later
  ADRs refine limits and evaluation protocol.

### Evaluation and public evidence

Read ADR 0017 for immutable shared corpus and query-vector artifacts, then ADRs
0028-0039 in order for transport identity, spend guards, cross-commit corpus
reuse, diagnostic selection, bounded reranking, the four-second local
retrieval SLO, V23 scoring, query-vector deduplication, exact repair, answer
contract exhaustion, and public composite evidence.

The current public result is the V23 exact-repair composite in
`evidence/benchmark-v3`. Its offline verification boundary and known generated
wording errata are documented in
[`../evidence-bundle.md`](../evidence-bundle.md), not retroactively edited into
the immutable evidence bundle.

### Product surface and measurement correctness

Read ADR 0040 for the public index maintenance surface and deferred provider
construction, then ADRs 0041-0042 for the calibrated V24 context budget and the
natural-weighted ablation gate. ADR 0041 amends the 4,000-token ceiling of ADRs
0023 and 0026; ADR 0042 amends how the ADR 0031 gate compares accuracy. The V23
protocol files and every published V23 result keep their frozen contracts.

### Version 0.1 product and simplification

ADRs 0043–0051 define the accepted version 0.1 target:

- ADR 0043 removes mandatory verification from memory storage while retaining
  system-owned provenance;
- ADR 0044 positions CodeCairn as an agent-independent Memory OS with one
  implicit Coding Profile;
- ADR 0045 defines Source, Experience, Knowledge, Evolution, and Recall;
- ADR 0046 makes Supersession and Restore append-only;
- ADR 0047 reduces the product to four Coding Memory types;
- ADR 0048 selects CLI, MCP, and hooks as product surfaces while retaining
  HTTP compatibility;
- ADR 0049 makes 10,000 core / 15,000 total Python lines release gates;
- ADR 0050 amends ADR 0015's target initialization profile.
- ADR 0051 amends ADR 0043 by removing the unowned standalone verification
  operation while retaining system-derived verification facets.

These decisions are specified for implementation under
[`../plan/tasks/`](../plan/tasks/). Current shipped behavior remains
[`../runtime/operations.md`](../runtime/operations.md) until the relevant task
merges.

## Adding a decision

Add the next numeric ADR instead of rewriting the causal history of an accepted
decision. If a new ADR changes current behavior:

1. identify the superseded or amended decision explicitly;
2. update the old ADR's status note without rewriting its historical context;
3. update `CONTEXT.md`, architecture, operations, and evaluation docs when their
   maintained contracts change; and
4. update this reading guide.
