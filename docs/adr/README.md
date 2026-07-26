# Architecture Decision Records

ADRs preserve why CodeCairn changed. They are append-oriented historical
records, not a flat set of simultaneously current specifications. Read
[`../architecture.md`](../architecture.md) for the current system and use the
ADR chain to understand the decisions behind it.

## Status rules

- `Accepted` means the decision is part of the implemented design unless a
  later ADR supersedes it.
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

The current lifecycle caveat is important: ADR 0006's asynchronous outbox is
implemented, but the public CLI and server do not yet own a Mini Cascade worker
or index-sync operation. See
[`../runtime/operations.md`](../runtime/operations.md).

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

## Adding a decision

Add the next numeric ADR instead of rewriting the causal history of an accepted
decision. If a new ADR changes current behavior:

1. identify the superseded or amended decision explicitly;
2. update the old ADR's status note without rewriting its historical context;
3. update `CONTEXT.md`, architecture, operations, and evaluation docs when their
   maintained contracts change; and
4. update this reading guide.
