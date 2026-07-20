# Hierarchical Recall Is a Rebuildable Projection

## Context

Task Episodes and Evidence Facts already form the extraction domain, while one
Coding Memory Markdown file is durable truth. Hierarchical recall needs a
parent document with smaller fact-level children, but persisting a second
Episode or AtomicFact truth store would create competing ownership and make
recovery ambiguous.

## Decision

CodeCairn keeps `TaskEpisode`, `EvidenceFact`, and `CodingMemory` as the only
domain contracts. A Coding Memory now carries the complete deterministic fact
snapshot selected by its Evidence Gate or failed-command extractor. Markdown
stores that snapshot in the same atomic artifact; SQLite mirrors it in a
`facts_json` column for transactional reads.

LanceDB derives one Recall Episode document from each Coding Memory and one
AtomicFact child document from every stored Evidence Fact. The Recall Episode
identifier is derived from repository and memory identity. Each AtomicFact
identifier is derived from repository, memory, and fact identity, and its
`parent_document_id` points to that Recall Episode. A Recall Episode is a search
projection, not a new durable `TaskEpisode`.

Rebuild compares both memory fingerprints and the complete set of document
fingerprints, including parent identifiers and content digests. A missing,
reparented, or modified AtomicFact therefore makes rebuild parity false.
Incremental workers also reparse the committed Markdown instead of trusting a
possibly stale SQLite fact mirror.

Legacy Markdown without fact snapshots remains valid and projects to a Recall
Episode with no children. SQLite adds `facts_json` with an empty default.
Existing flat LanceDB rows migrate without losing the memory-level record; a
full rebuild from Markdown creates the canonical hierarchy.

## Consequences

- Markdown remains the only recoverable content truth.
- Storage migration is additive and does not require rewriting old memories.
- Index deletion and rebuild preserve parent-child structure, not only memory
  counts.
- PR1 does not alter ranking: vector and lexical search still filter to Recall
  Episode documents, whose search content omits the serialized fact snapshot.
- AtomicFact query routing, neighbor expansion, and hierarchical planning can
  be added without another storage migration. ADR 0014 records that later
  ranking decision.
