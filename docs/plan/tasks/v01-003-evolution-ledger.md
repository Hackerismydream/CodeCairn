---
id: v01-003
scope: supersession, status projection, and restore
status: blocked
depends-on: [v01-002]
---

# Add the immutable Evolution Layer

## Objective

Implement automatic validated Supersession, derived active status, history,
and forward-only restore without editing a Coding Memory.

## Paths

Primary:

- `src/codecairn/memory/models.py`
- a small `src/codecairn/memory/evolution.py` if it improves ownership
- `src/codecairn/service/runtime.py`
- `src/codecairn/service/application.py`
- `src/codecairn/storage/markdown.py`
- `src/codecairn/storage/sqlite.py`
- `src/codecairn/service/cascade.py`
- focused lifecycle tests under `tests/`

## Required changes

1. Implement `EvolutionRecord`, `EvolutionProposal`, proposal outcome,
   `SourceOrderKey`, `MemoryStatus`, and stable identity exactly as the schema
   and lifecycle contracts specify.
2. Validate existing IDs, same namespace, active predecessor, no self-edge, and
   no cycle before applying an edge.
3. Encode type policy:
   - Task Experience never supersedes;
   - Work State requires the same `workstream_key`, including open-to-closed;
   - User Preference requires same subject plus a newer explicit user source;
   - Repository Knowledge requires same subject and obsolete/contradictory
     proposal;
   - cross-type edges are rejected.
4. Treat repeated identical application as a no-op and conflicting content as
   an error.
5. Apply with `BEGIN IMMEDIATE` CAS and the required one-successor and active
   Work State-head uniqueness constraints.
6. Persist Evolution Markdown, SQLite mirror, derived statuses, and both index
   outbox revisions through one multi-file Write Intent.
7. Rebuild status from Memory and Evolution Markdown and prove parity.
8. Add `restore(memory_id)` that creates a new memory with `origin=restored`,
   `restored_from`, and `restore_predecessor_id`, then follows normal
   supersession.
   Accept only superseded Repository Knowledge, User Preference, or Work State;
   reject Task Experience and already-active memory. Treat the explicit restore
   as a user lifecycle decision while preserving namespace/type/key/cycle
   validation.
9. Restore selects the unique active tip in the restored memory's lineage;
   zero or multiple tips return `ambiguous_lineage`.
10. Automatic proposal outcomes are `applied`, `kept_both`, or `rejected`.
    A policy rejection does not fail completed semantic extraction; explicit
    service calls return typed validation errors.

## Public service contract

Expose:

```text
supersede(predecessor_id, successor_id, reason, proposer)
memory_history(memory_id)
restore(memory_id)
```

History returns a deterministic oldest-to-newest chain plus edge reasons.
Branches are allowed for `keep_both`; a memory may have at most one applied
successor edge in version 0.1.

## Verification

```bash
uv run pytest -k "evolution or supersed or restore or markdown or sqlite or cascade"
make format
make check
```

Required cases:

- each allowed type policy;
- wrong namespace/type/subject/workstream;
- self-edge and multi-node cycle;
- inactive predecessor;
- incomparable cross-session order keeps both;
- idempotent retry and conflicting edge;
- concurrent same-predecessor and same-Workstream-head races;
- transaction rollback;
- policy rejection preserves completed semantic job;
- Markdown-to-SQLite status rebuild;
- predecessor and successor index requeue;
- restore creates a new ID and preserves all prior files.
- restore rejects append-only Task Experience and already-active memory.
- restore returns `ambiguous_lineage` for zero or multiple active tips.

## Exit criteria

- status is derived exclusively from Evolution Records;
- no mutation/deletion path is introduced;
- automatic and explicit failure semantics differ only in presentation;
- rebuild and transaction tests pass;
- all checks pass and line deltas are reported.
