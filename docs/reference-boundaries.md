# Reference Boundaries

## Pythia

Pythia remains private and frozen as a prototype. It provides failure cases,
provider-format examples, and candidate tests. The following implementations
are not portable as-is: Evidence Gate, segmentation, import resume, repository
deduplication, pseudo-BM25 ranking, and evaluation reporting.

Any selected code must pass this sequence:

1. State the external behavior in CodeCairn vocabulary.
2. Add an independent failing contract test.
3. Port or rewrite the smallest implementation that satisfies the contract.
4. Remove Pythia-specific names, metrics, paths, and assumptions.

Raw real-session fixtures are not copied into public history. Public fixtures
are synthetic and minimal; private runs may reference an external local corpus
through a manifest containing only hashes and aggregate counts.

## EverOS

EverOS is a mechanism-level reference for Markdown atomicity, locking,
hierarchical search projections, index rebuilds, queue observability, and LoCoMo
orchestration. It is not a source for CodeCairn's Coding Agent Trace, Evidence
Gate, task segmentation, Recall Context contract, or coding-task evaluation
runner.

Public CodeCairn documentation describes CodeCairn directly. When source code is
copied rather than independently implemented, its license and attribution must
be reviewed before the code enters the repository.

Embedding and CrossEncoder execution uses the public FastEmbed Adapter surface
over CodeCairn-resolved local snapshots. Logical aliases, artifact repositories,
immutable commit revisions, dimensions, and licenses are selected independently
for CodeCairn; neither the Adapter design nor the ranking contract is copied from
EverOS.
